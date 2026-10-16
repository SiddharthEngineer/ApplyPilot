"""Strelitzia Pipeline Orchestrator.

Runs pipeline stages in sequence or concurrently (streaming mode).

No CLI command runs it any more (the CLI is admin-only, user-auth-8-cli): per-user work happens in the dashboard,
which calls the stage modules directly. run_pipeline(stages, with_discover=..., stream=..., dry_run=...) stays for
scripts and tests.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from strelitzia import users
from strelitzia.config import load_env, ensure_dirs
from strelitzia.database import (
    PENDING_EXTRACT_WHERE,
    PENDING_SCORE_WHERE,
    get_connection,
    get_stats,
    init_db,
)

log = logging.getLogger(__name__)
console = Console()


# ---------------------------------------------------------------------------
# Stage definitions
# ---------------------------------------------------------------------------

# `extract` only needs `enrich`, but runs after the stages the user waits on, so a run that hits the monthly
# LLM budget has spent it on scoring first.
STAGE_ORDER = ("discover", "enrich", "score", "extract", "pdf")
# Discovery is off by default: jobs come from the dashboard's Add job. `all` leaves it out unless --with-discover.
OPTIONAL_STAGES = ("discover",)
DISCOVERY_OFF_NOTE = "off (use --with-discover)"
# Stages that moved to the Claude workbench: naming one prints this instead of running it.
REMOVED_STAGES = ("tailor", "cover")
WORKBENCH_NOTE = "Resumes are written in Claude now: open a job in the dashboard → Open in Claude"

STAGE_META: dict[str, dict] = {
    "discover": {"desc": "Job discovery (JobSpy + Workday + smart extract)"},
    "enrich":   {"desc": "Detail enrichment (full descriptions + apply URLs)"},
    "score":    {"desc": "LLM scoring (fit 1-10)"},
    "extract":  {"desc": "Structured posting extraction (LLM, batched) + heuristic categories"},
    "pdf":      {"desc": "PDF conversion (tailored resumes + cover letters)"},
}

# Upstream dependency: a stage only finishes when its upstream is done AND
# it has no remaining pending work.
_UPSTREAM: dict[str, str | None] = {
    "discover": None,
    "enrich":   "discover",
    "score":    "enrich",
    "extract":  "enrich",
    "pdf":      None,
}


# ---------------------------------------------------------------------------
# Individual stage runners
# ---------------------------------------------------------------------------

def _run_discover(workers: int = 1, no_cache: bool = False) -> dict:
    """Stage: Job discovery — JobSpy, ATS job boards, Workday, and smart-extract scrapers."""
    stats: dict = {"jobspy": None, "ats": None, "workday": None, "smartextract": None}

    # JobSpy
    console.print("  [cyan]JobSpy full crawl...[/cyan]")
    try:
        from strelitzia.discovery.jobspy import run_discovery
        result = run_discovery()
        disabled = result.get("disabled_sites", [])
        if disabled:
            sites_str = ", ".join(disabled)
            reasons = (result.get("site_stats") or {}).get("reasons", {})
            detail = ", ".join(f"{s}: {reasons.get(s, '0 results across multiple searches')}" for s in disabled)
            console.print(
                f"  [yellow]JobSpy skipped site(s) — {detail}. "
                f"Remove from 'sites' in searches.yaml to permanently disable.[/yellow]"
            )
            stats["jobspy"] = f"ok (disabled: {sites_str})"
        else:
            stats["jobspy"] = "ok"
    except Exception as e:
        log.error("JobSpy crawl failed: %s", e)
        console.print(f"  [red]JobSpy error:[/red] {e}")
        stats["jobspy"] = f"error: {e}"

    # Greenhouse / Lever / Ashby public job boards
    console.print("  [cyan]ATS job boards (Greenhouse/Lever/Ashby)...[/cyan]")
    try:
        from strelitzia.discovery.ats_boards import run_ats_discovery
        ats = run_ats_discovery()
        stats["ats"] = f"ok ({ats['new']} new from {ats['boards']} boards" + (
            f", {ats['errors']} failed)" if ats["errors"] else ")")
    except Exception as e:
        log.error("ATS discovery failed: %s", e)
        console.print(f"  [red]ATS error:[/red] {e}")
        stats["ats"] = f"error: {e}"

    # Workday corporate scraper
    console.print("  [cyan]Workday corporate scraper...[/cyan]")
    try:
        from strelitzia.discovery.workday import run_workday_discovery
        run_workday_discovery(workers=workers)
        stats["workday"] = "ok"
    except Exception as e:
        log.error("Workday scraper failed: %s", e)
        console.print(f"  [red]Workday error:[/red] {e}")
        stats["workday"] = f"error: {e}"

    # Smart extract
    console.print("  [cyan]Smart extract (AI-powered scraping)...[/cyan]")
    try:
        from strelitzia.discovery.smartextract import run_smart_extract
        se = run_smart_extract(workers=workers, no_cache=no_cache)
        stats["smartextract"] = _stage_status(se)["status"]
    except Exception as e:
        log.error("Smart extract failed: %s", e)
        console.print(f"  [red]Smart extract error:[/red] {e}")
        stats["smartextract"] = f"error: {e}"

    return stats


def _run_enrich(workers: int = 1) -> dict:
    """Stage: Detail enrichment — scrape full descriptions and apply URLs."""
    try:
        from strelitzia.enrichment.detail import run_enrichment
        run_enrichment(workers=workers)
        return {"status": "ok"}
    except Exception as e:
        log.error("Enrichment failed: %s", e)
        return {"status": f"error: {e}"}


BUDGET_STOPPED = "stopped: monthly LLM budget"
AUTH_STOPPED = "stopped: Claude API key missing or rejected"
OVERLOAD_STOPPED = "stopped: Claude API overloaded"
# A stage that stopped early on purpose (LLMStopRun.reason -> status); the rest of its jobs wait for the next run.
_STOP_STATUS = {"budget": BUDGET_STOPPED, "auth": AUTH_STOPPED, "overloaded": OVERLOAD_STOPPED}
STOPPED_STATUSES = tuple(_STOP_STATUS.values())


def _stage_status(stats: dict | None) -> dict:
    """Map an LLM stage's stats to a pipeline status ("stopped" = budget, key or overload, rest left for later)."""
    if isinstance(stats, dict) and stats.get("stopped") in _STOP_STATUS:
        return {"status": _STOP_STATUS[stats["stopped"]]}
    return {"status": "ok"}


def _run_score(prefilter: bool = True, limit: int | None = None, sync: bool = False) -> dict:
    """Stage: LLM scoring, each user's jobs with their preferences (a Message Batch per user unless `sync` or a
    small run). Stopped only when every user's run stopped: one user over their cap doesn't stop the others."""
    try:
        from strelitzia.scoring.scorer import run_scoring
        extra = ({"limit": limit} if limit else {}) | ({"mode": "sync"} if sync else {})
        conn = get_connection()
        owner_id = users.owner(conn).id
        user_ids = sorted({r[0] for r in conn.execute("SELECT DISTINCT user_id FROM jobs").fetchall()} | {owner_id},
                          key=lambda u: (u != owner_id, u))
        statuses = [_stage_status(run_scoring(prefilter=prefilter, user_id=u, **extra)) for u in user_ids]
        return next((s for s in statuses if s["status"] == "ok"), statuses[0])
    except Exception as e:
        log.error("Scoring failed: %s", e)
        return {"status": f"error: {e}"}


def _run_extract(limit: int | None = None, sync: bool = False) -> dict:
    """Stage: heuristic categories for new jobs (no LLM), then LLM extraction (a Message Batch unless `sync`)."""
    try:
        from strelitzia.enrichment.classify import run_classify
        from strelitzia.enrichment.extract import run_extraction
        run_classify(only_missing=True)
        stats = run_extraction(limit=limit, **({"mode": "sync"} if sync else {}))
        if stats.get("stopped") == "errors":
            return {"status": "error: extraction requests keep failing (see log)"}
        return _stage_status(stats)
    except Exception as e:  # noqa: BLE001 -- reported as the stage status, like the other stages
        log.error("Extraction failed: %s", e)
        return {"status": f"error: {e}"}


def _run_pdf() -> dict:
    """Stage: PDF conversion — convert tailored resumes and cover letters to PDF."""
    try:
        from strelitzia.scoring.pdf import batch_convert
        batch_convert()
        return {"status": "ok"}
    except Exception as e:
        log.error("PDF conversion failed: %s", e)
        return {"status": f"error: {e}"}


# Map stage names to their runner functions
_STAGE_RUNNERS: dict[str, callable] = {
    "discover": _run_discover,
    "enrich":   _run_enrich,
    "score":    _run_score,
    "extract":  _run_extract,
    "pdf":      _run_pdf,
}


# ---------------------------------------------------------------------------
# Stage resolution
# ---------------------------------------------------------------------------

def _resolve_stages(stage_names: list[str], with_discover: bool = False) -> list[str]:
    """Resolve 'all' and validate/order stage names.

    'all' is every stage but discover; `with_discover` adds discover to any selection. Naming discover runs it.
    """
    if "all" in stage_names:
        return [s for s in STAGE_ORDER if s not in OPTIONAL_STAGES or with_discover]

    resolved = ["discover"] if with_discover else []
    for name in stage_names:
        if name in REMOVED_STAGES:
            console.print(f"[yellow]{name}:[/yellow] {WORKBENCH_NOTE}")
            continue
        if name not in STAGE_META:
            console.print(
                f"[red]Unknown stage:[/red] '{name}'. "
                f"Available: {', '.join(STAGE_ORDER)}, all"
            )
            raise SystemExit(1)
        if name not in resolved:
            resolved.append(name)

    # Maintain canonical order
    return [s for s in STAGE_ORDER if s in resolved]


# ---------------------------------------------------------------------------
# Streaming pipeline helpers
# ---------------------------------------------------------------------------

class _StageTracker:
    """Thread-safe tracker for which stages have finished producing work."""

    def __init__(self):
        self._events: dict[str, threading.Event] = {
            stage: threading.Event() for stage in STAGE_ORDER
        }
        self._results: dict[str, dict] = {}
        self._lock = threading.Lock()

    def mark_done(self, stage: str, result: dict | None = None) -> None:
        with self._lock:
            self._results[stage] = result or {"status": "ok"}
        self._events[stage].set()

    def is_done(self, stage: str) -> bool:
        return self._events[stage].is_set()

    def wait(self, stage: str, timeout: float | None = None) -> bool:
        return self._events[stage].wait(timeout=timeout)

    def get_results(self) -> dict[str, dict]:
        with self._lock:
            return dict(self._results)


# SQL to count pending work for each stage
_PENDING_SQL: dict[str, str] = {
    "enrich": "SELECT COUNT(*) FROM jobs WHERE detail_scraped_at IS NULL",
    "score":  f"SELECT COUNT(*) FROM jobs WHERE {PENDING_SCORE_WHERE}",
    "extract": f"SELECT COUNT(*) FROM jobs WHERE {PENDING_EXTRACT_WHERE}",
    "pdf": (
        "SELECT COUNT(*) FROM jobs WHERE tailored_resume_path IS NOT NULL "
        "AND tailored_resume_path LIKE '%.txt'"
    ),
}

# How long to sleep between polling loops in streaming mode (seconds)
_STREAM_POLL_INTERVAL = 10


def _count_pending(stage: str) -> int:
    """Count pending work items for a stage."""
    sql = _PENDING_SQL.get(stage)
    if sql is None:
        return 0
    return get_connection().execute(sql).fetchone()[0]


def _run_stage_streaming(
    stage: str,
    tracker: _StageTracker,
    stop_event: threading.Event,
    workers: int = 1,
    no_cache: bool = False,
    prefilter: bool = True,
    limit: int | None = None,
    sync: bool = False,
) -> None:
    """Run a single stage in streaming mode: loop until upstream done + no work.

    For discover: runs once, then marks done.
    For all others: polls DB for pending work, runs the batch processor,
    and repeats until upstream is done and no pending work remains.
    """
    runner = _STAGE_RUNNERS[stage]
    kwargs: dict = {}
    if stage in ("discover", "enrich"):
        kwargs["workers"] = workers
    if stage == "discover":
        kwargs["no_cache"] = no_cache
    if stage == "score":
        kwargs["prefilter"] = prefilter
        kwargs["limit"] = limit
    if stage == "extract":
        kwargs["limit"] = limit
    if stage in ("score", "extract"):
        kwargs["sync"] = sync

    upstream = _UPSTREAM[stage]

    if stage == "discover":
        # Discover runs once (its sub-scrapers already do their full crawl)
        try:
            result = runner(**kwargs)
            tracker.mark_done(stage, result)
        except Exception as e:
            log.exception("Stage '%s' crashed", stage)
            tracker.mark_done(stage, {"status": f"error: {e}"})
        return

    # For downstream stages: loop until upstream done + no pending work
    passes = 0
    while not stop_event.is_set():
        # Wait for upstream to start producing work (first pass only)
        if passes == 0 and upstream and not tracker.is_done(upstream):
            # Wait a bit for upstream to produce some work before first run
            tracker.wait(upstream, timeout=_STREAM_POLL_INTERVAL)

        pending = _count_pending(stage)

        if pending > 0:
            try:
                result = runner(**kwargs)
                passes += 1
            except Exception as e:
                log.error("Stage '%s' error (pass %d): %s", stage, passes, e)
                passes += 1
            else:
                if isinstance(result, dict) and result.get("status") in STOPPED_STATUSES:
                    # Re-running would hit the same stop (budget, key or overload) again.
                    tracker.mark_done(stage, {"status": result["status"], "passes": passes})
                    return
                if stage in ("score", "extract") and limit:
                    # --limit caps LLM calls for the whole run, so one pass is all it gets.
                    break
        else:
            # No work right now
            upstream_done = upstream is None or tracker.is_done(upstream)
            if upstream_done:
                # No work and upstream is done — this stage is finished
                break
            # Upstream still running, wait and retry
            if stop_event.wait(timeout=_STREAM_POLL_INTERVAL):
                break  # Stop requested

    tracker.mark_done(stage, {"status": "ok", "passes": passes})


# ---------------------------------------------------------------------------
# Pipeline orchestrators
# ---------------------------------------------------------------------------

def _run_sequential(ordered: list[str], workers: int = 1,
                    no_cache: bool = False,
                    prefilter: bool = True,
                    limit: int | None = None,
                    sync: bool = False) -> dict:
    """Execute stages one at a time (original behavior)."""
    results: list[dict] = []
    errors: dict[str, str] = {}
    pipeline_start = time.time()

    for name in ordered:
        meta = STAGE_META[name]
        console.print(f"\n{'=' * 70}")
        console.print(f"  [bold]STAGE: {name}[/bold] — {meta['desc']}")
        console.print(f"  Started: {datetime.now().strftime('%H:%M:%S')}")
        console.print(f"{'=' * 70}")

        t0 = time.time()
        runner = _STAGE_RUNNERS[name]

        try:
            kwargs: dict = {}
            if name in ("discover", "enrich"):
                kwargs["workers"] = workers
            if name == "discover":
                kwargs["no_cache"] = no_cache
            if name == "score":
                kwargs["prefilter"] = prefilter
                kwargs["limit"] = limit
            if name == "extract":
                kwargs["limit"] = limit
            if name in ("score", "extract"):
                kwargs["sync"] = sync
            result = runner(**kwargs)
            elapsed = time.time() - t0

            status = "ok"
            if isinstance(result, dict):
                status = result.get("status", "ok")
                if name == "discover":
                    sub_errors = [
                        f"{k}: {v}" for k, v in result.items()
                        if isinstance(v, str) and v.startswith("error")
                    ]
                    if sub_errors:
                        status = "partial"

        except Exception as e:
            elapsed = time.time() - t0
            status = f"error: {e}"
            log.exception("Stage '%s' crashed", name)
            console.print(f"\n  [red]STAGE FAILED:[/red] {e}")

        results.append({"stage": name, "status": status, "elapsed": elapsed})
        if status not in ("ok", "partial", *STOPPED_STATUSES):
            errors[name] = status

        console.print(f"\n  Stage '{name}' completed in {elapsed:.1f}s — {status}")

    total_elapsed = time.time() - pipeline_start
    return {"stages": results, "errors": errors, "elapsed": total_elapsed}


def _run_streaming(ordered: list[str], workers: int = 1,
                   no_cache: bool = False,
                   prefilter: bool = True,
                   limit: int | None = None,
                   sync: bool = False) -> dict:
    """Execute stages concurrently with DB as conveyor belt."""
    tracker = _StageTracker()
    stop_event = threading.Event()
    pipeline_start = time.time()

    console.print("\n  [bold cyan]STREAMING MODE[/bold cyan] — stages run concurrently")
    console.print(f"  Poll interval: {_STREAM_POLL_INTERVAL}s\n")

    # Mark stages NOT in `ordered` as done so downstream doesn't wait for them
    for stage in STAGE_ORDER:
        if stage not in ordered:
            tracker.mark_done(stage, {"status": "skipped"})

    # Launch each stage in its own thread
    threads: dict[str, threading.Thread] = {}
    start_times: dict[str, float] = {}

    for name in ordered:
        start_times[name] = time.time()
        t = threading.Thread(
            target=_run_stage_streaming,
            args=(name, tracker, stop_event, workers, no_cache, prefilter, limit, sync),
            name=f"stage-{name}",
            daemon=True,
        )
        threads[name] = t
        t.start()
        console.print(f"  [dim]Started thread:[/dim] {name}")

    # Wait for all threads to finish
    try:
        for name in ordered:
            threads[name].join()
            elapsed = time.time() - start_times[name]
            console.print(
                f"  [green]Completed:[/green] {name} ({elapsed:.1f}s)"
            )
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted — stopping stages...[/yellow]")
        stop_event.set()
        for t in threads.values():
            t.join(timeout=10)

    total_elapsed = time.time() - pipeline_start

    # Build results from tracker
    all_results = tracker.get_results()
    results: list[dict] = []
    errors: dict[str, str] = {}

    for name in ordered:
        r = all_results.get(name, {"status": "unknown"})
        elapsed = time.time() - start_times.get(name, pipeline_start)
        status = r.get("status", "ok")

        results.append({"stage": name, "status": status, "elapsed": elapsed})
        if status not in ("ok", "partial", "skipped", *STOPPED_STATUSES):
            errors[name] = status

    return {"stages": results, "errors": errors, "elapsed": total_elapsed}


def run_pipeline(
    stages: list[str] | None = None,
    dry_run: bool = False,
    stream: bool = False,
    workers: int = 1,
    no_cache: bool = False,
    prefilter: bool = True,
    limit: int | None = None,
    sync: bool = False,
    with_discover: bool = False,
) -> dict:
    """Run pipeline stages.

    Args:
        stages: List of stage names, or None / ["all"] for full pipeline.
        dry_run: If True, preview stages without executing.
        stream: If True, run stages concurrently (streaming mode).
        workers: Number of parallel threads for discovery/enrichment stages.
        no_cache: If True, bypass per-domain strategy cache in smart-extract.
        prefilter: If False, score every job with the LLM (skip the title pre-filter).
        limit: Max jobs sent to the LLM by the score and extract stages (default: all).
        sync: Score and extract with one request per job instead of a Message Batch (full price, no wait).
        with_discover: Also run discover (left out of "all" by default).

    Returns:
        Dict with keys: stages (list of result dicts), errors (dict), elapsed (float).
    """
    # Bootstrap
    load_env()
    ensure_dirs()
    init_db()

    # Resolve stages
    if stages is None:
        stages = ["all"]
    ordered = _resolve_stages(stages, with_discover=with_discover)
    if not ordered:
        return {"stages": [], "errors": {}, "elapsed": 0.0}

    # Banner
    mode = "streaming" if stream else "sequential"
    console.print()
    console.print(Panel.fit(
        f"[bold]Strelitzia Pipeline[/bold] ({mode})",
        border_style="blue",
    ))
    console.print(f"  Workers:    {workers}")
    console.print(f"  Stages:     {' -> '.join(ordered)}")
    console.print(f"  Discovery:  {'on' if 'discover' in ordered else DISCOVERY_OFF_NOTE}")

    # Pre-run stats
    pre_stats = get_stats()
    console.print(f"  DB:        {pre_stats['total']} jobs, {pre_stats['pending_detail']} pending enrichment")

    if dry_run:
        console.print(f"\n  [yellow]DRY RUN[/yellow] — would execute ({mode}):")
        for name in ordered:
            meta = STAGE_META[name]
            console.print(f"    {name:<12s}  {meta['desc']}")
        console.print("\n  No changes made.")
        return {"stages": [], "errors": {}, "elapsed": 0.0}

    # Execute
    if stream:
        result = _run_streaming(ordered, workers=workers, no_cache=no_cache, prefilter=prefilter, limit=limit, sync=sync)
    else:
        result = _run_sequential(ordered, workers=workers, no_cache=no_cache, prefilter=prefilter, limit=limit, sync=sync)

    # Summary table
    console.print(f"\n{'=' * 70}")
    summary = Table(title="Pipeline Summary", show_header=True, header_style="bold")
    summary.add_column("Stage", style="bold")
    summary.add_column("Status")
    summary.add_column("Time", justify="right")

    for r in result["stages"]:
        elapsed_str = f"{r['elapsed']:.1f}s"
        status_display = r["status"][:30]
        if r["status"] == "ok":
            style = "green"
        elif r["status"] in ("partial", "skipped", *STOPPED_STATUSES):
            style = "yellow"
        else:
            style = "red"
        summary.add_row(r["stage"], f"[{style}]{status_display}[/{style}]", elapsed_str)

    summary.add_row("", "", "")
    summary.add_row("[bold]Total[/bold]", "", f"[bold]{result['elapsed']:.1f}s[/bold]")
    console.print(summary)

    # Final DB stats
    final = get_stats()
    console.print("\n  [bold]DB Final State:[/bold]")
    console.print(f"    Total jobs:     {final['total']}")
    console.print(f"    With desc:      {final['with_description']}")
    console.print(f"    Scored:         {final['scored']}")
    console.print(f"    Tailored:       {final['tailored']}")
    console.print(f"    Cover letters:  {final['with_cover_letter']}")
    console.print(f"    Ready to apply: {final['ready_to_apply']}")
    console.print(f"    Applied:        {final['applied']}")
    console.print(f"{'=' * 70}\n")

    return result
