"""Job fit scoring: the Claude assessment of each job, then the 0-100 fit score.

The assessment (`scoring/assessment.py`) rates skills, experience and domain fit on a fixed rubric and lists red
flags. `recompute_fit` then combines it with the code-scored preferences into `jobs.fit_score`. The rubric and the
candidate (resume plus assessment inputs) are two cached system blocks and the job is the user turn, so every
request after the first reuses them. Bulk runs go through the Message Batches API (half price); small ones are sync.
"""

import json
import logging
import re
import time
from datetime import UTC, datetime
from typing import Literal

import anthropic

from strelitzia.config import RESUME_PATH, load_profile, load_search_config
from strelitzia.database import MAX_SCORE_ATTEMPTS, get_connection, get_jobs_by_stage, job_key
from strelitzia.llm import LLMStopRun, check_budget, estimate_cost, get_client
from strelitzia.llm_batch import resume_open_batches, run_batch
from strelitzia.scoring.assessment import (
    ASSESS_SCHEMA,
    assess_messages,
    assessment_key,
    load_resume_text,
    parse_assessment,
    recompute_fit,
)
from strelitzia.scoring.preferences import Preferences, load_preferences

log = logging.getLogger(__name__)

# Output budget per job, thinking included.
_MAX_OUTPUT_TOKENS = 2048
# Runs of at most this many jobs go sync: a batch's latency isn't worth it for a handful of requests.
SYNC_MAX_JOBS = 5


def _result_from_reply(response: str) -> dict:
    assessment = parse_assessment(response.strip())
    if assessment is None:
        result = _error_result(f"unparseable response: {response[:200]!r}")
        result["parse_error"] = True
        return result
    return {"assessment": assessment}


def assess_job(prefs: Preferences, resume_text: str, job: dict) -> dict:
    """Assess one job against the resume and the assessment inputs.

    Args:
        prefs: The preferences (only the assessment inputs reach the prompt).
        resume_text: The candidate's full resume text.
        job: Job row: title, company/site, details_json (the extracted posting), full_description, url.

    Returns:
        {"assessment": dict} on success. On an LLM error or an unparseable reply, "assessment" is None and "error"
        holds the message, so the job stays pending and retryable.
    """
    try:
        client = get_client("scoring")
        response = client.chat(assess_messages(prefs, resume_text, job), max_tokens=_MAX_OUTPUT_TOKENS,
                               schema=ASSESS_SCHEMA, job_url=job.get("url"))
        return _result_from_reply(response)
    except LLMStopRun:
        raise  # run_scoring stops the stage; this job stays untouched
    except anthropic.APIStatusError as e:
        log.error("LLM error assessing job '%s': HTTP %d: %s", job.get("title", "?"), e.status_code, e.message)
        return _error_result(f"HTTP {e.status_code}")
    except Exception as e:
        log.error("LLM error assessing job '%s': %s", job.get("title", "?"), e)
        return _error_result(str(e))


def _error_result(msg: str) -> dict:
    """Result for a job that couldn't be assessed. `reasoning` keeps the 'LLM error' prefix --reset-errors matches."""
    return {"assessment": None, "reasoning": f"LLM error: {msg}", "error": msg}


# ── Title pre-filter (no LLM call) ───────────────────────────────────────

PREFILTER_REASONING = "prefilter: title not relevant"

# Words that say nothing about the kind of role: articles, seniority, levels, work mode.
_TITLE_STOPWORDS = frozenset({
    "a", "an", "and", "the", "of", "for", "to", "in", "at", "on", "with", "or", "by", "from",
    "senior", "sr", "junior", "jr", "lead", "staff", "principal", "mid", "entry", "level", "head", "chief",
    "i", "ii", "iii", "iv", "v", "remote", "hybrid", "onsite", "contract", "temporary", "full", "part", "time",
})


def _title_tokens(text: str) -> set[str]:
    """Significant lower-cased words of a title (keeps tech tokens like c++, c#, .net, ai)."""
    words = re.findall(r"[a-z0-9][a-z0-9+#.]*", (text or "").lower())
    return {w.rstrip(".") for w in words if w.rstrip(".") and w.rstrip(".") not in _TITLE_STOPWORDS}


def load_target_tokens(prefs: Preferences | None = None) -> set[str]:
    """Words from searches.yaml queries, the profile's target role(s) and the Preferences page's target roles."""
    titles: list[str] = list(prefs.target_roles) if prefs else []
    try:
        titles += [q.get("query", "") for q in load_search_config().get("queries", []) if isinstance(q, dict)]
    except Exception as e:  # noqa: BLE001 - a missing/broken config just disables the filter
        log.debug("prefilter: no search queries (%s)", e)
    try:
        exp = load_profile().get("experience", {})
        titles += re.split(r"[,;/|]", exp.get("target_role", "") or "")
        titles += exp.get("target_titles", []) or []
    except Exception as e:  # noqa: BLE001
        log.debug("prefilter: no profile target role (%s)", e)
    tokens: set[str] = set()
    for t in titles:
        tokens |= _title_tokens(t)
    return tokens


def prefilter_jobs(jobs: list[dict], target_tokens: set[str]) -> tuple[list[dict], list[dict]]:
    """Split jobs into (to_score, skipped). A title passes if it shares a significant word with any target.

    With no targets configured, nothing is skipped.
    """
    if not target_tokens:
        return jobs, []
    keep, skipped = [], []
    for job in jobs:
        (keep if _title_tokens(job.get("title", "")) & target_tokens else skipped).append(job)
    return keep, skipped


def _save_result(conn, r: dict, key: str, prefs: Preferences) -> None:
    """Write one assessment (and the job's new fit score) and commit it right away."""
    if r["assessment"] is None:
        # The job keeps any earlier assessment and score; a never-assessed one is retried, up to MAX_SCORE_ATTEMPTS.
        conn.execute(
            "UPDATE jobs SET score_reasoning = ?, score_attempts = COALESCE(score_attempts, 0) + 1 WHERE url = ?",
            (r["reasoning"], r["url"]),
        )
        conn.commit()
        return
    a = r["assessment"]
    now = datetime.now(UTC).isoformat()
    conn.execute(
        "UPDATE jobs SET fit_assessment = ?, assessment_hash = ?, assessed_at = ?, scored_at = ?, score_reasoning = ? "
        "WHERE url = ?",
        (json.dumps(a), key, now, now, f"{', '.join(a['matched_keywords'])}\n{a['summary']}", r["url"]),
    )
    conn.commit()
    recompute_fit(conn, [r["url"]], prefs)


class _Tally:
    """Saves each result as it arrives and keeps the counts."""

    def __init__(self, conn, total: int, key: str, prefs: Preferences) -> None:
        self.conn = conn
        self.total = total
        self.key = key
        self.prefs = prefs
        self.results: list[dict] = []
        self.errors = self.parse_errors = 0
        self.first_error = ""

    def add(self, url: str, result: dict, title: str = "?") -> None:
        result["url"] = url
        if result.get("parse_error"):
            self.parse_errors += 1
        if result["assessment"] is None:
            self.errors += 1
            self.first_error = self.first_error or result.get("reasoning", "")
        self.results.append(result)
        _save_result(self.conn, result, self.key, self.prefs)
        if result["assessment"] is None:
            shown = "error"
        else:
            shown = "skills %d/5" % result["assessment"]["skills_match"]["score"]
        log.info("[%d/%d] %s  %s", len(self.results), self.total, shown, title[:60])


def _score_sync(tally: _Tally, resume_text: str, jobs: list[dict]) -> str:
    """One request per job. Returns the stop reason ("" if it ran to the end)."""
    for job in jobs:
        try:
            result = assess_job(tally.prefs, resume_text, job)
        except LLMStopRun as e:
            log.warning("Scoring stopped: %s. %d jobs left unassessed for the next run.", e,
                        len(jobs) - len(tally.results))
            return e.reason
        tally.add(job["url"], result, job.get("title") or "?")
    return ""


def assessment_requests(prefs: Preferences, resume_text: str, jobs: list[dict]) -> list[tuple[str, dict]]:
    """(job_key, Messages API params) for each job: the Message Batches items, also used for cost estimates."""
    client = get_client("scoring")
    return [(job_key(j["url"]), client.request_params(assess_messages(prefs, resume_text, j),
                                                      max_tokens=_MAX_OUTPUT_TOKENS, schema=ASSESS_SCHEMA))
            for j in jobs]


def _score_batch(tally: _Tally, resume_text: str, jobs: list[dict], poll_seconds: float, extra: dict) -> str:
    """Every job in Message Batches, after a cost estimate and budget check. Returns the stop reason."""
    client = get_client("scoring")
    by_key = {job_key(j["url"]): j for j in jobs}
    items = assessment_requests(tally.prefs, resume_text, list(by_key.values()))

    def on_result(custom_id: str, text: str | None, error: str | None) -> None:
        job = by_key.get(custom_id)
        if job is None:
            log.warning("Batch result %s matches no job in this run; skipped.", custom_id)
            return
        tally.add(job["url"], _result_from_reply(text) if text is not None else _error_result(error or "?"),
                  job.get("title") or "?")

    try:
        estimate = estimate_cost("scoring", [params for _, params in items], batch=True)
        extra["estimate_usd"] = estimate
        log.info("Assessing %d job(s) in a batch: estimated cost $%.2f (%s).", len(items), estimate, client.model)
        check_budget(estimate, tally.conn)
        run_batch("scoring", items, on_result, poll_seconds=poll_seconds, estimate_usd=estimate, conn=tally.conn)
    except LLMStopRun as e:
        log.warning("Scoring stopped: %s. Unassessed jobs stay pending for the next run.", e)
        return e.reason
    return ""


def _collect_open_batches(conn, poll_seconds: float, wait: bool, key: str, prefs: Preferences) -> int:
    """Save the results of scoring batches a killed run left open (matched by job_key). Returns how many.

    A job assessed since with the current inputs keeps that assessment. Raises LLMStopRun if the batches can't be
    collected.
    """
    tally = _Tally(conn, 0, key, prefs)

    def on_result(custom_id: str, text: str | None, error: str | None) -> None:
        row = conn.execute("SELECT url, title, assessment_hash FROM jobs WHERE job_key = ?", (custom_id,)).fetchone()
        if row is None:
            log.warning("Batch result %s matches no job; skipped.", custom_id)
            return
        if row[2] == key:
            log.info("Batch result for %s skipped: assessed since.", row[0])
            return
        tally.add(row[0], _result_from_reply(text) if text is not None else _error_result(error or "?"),
                  row[1] or "?")

    open_left = resume_open_batches("scoring", on_result, poll_seconds=poll_seconds, wait=wait, conn=conn)["still_open"]
    if open_left:
        log.warning("%d scoring batch(es) from an earlier run are still processing; a later batch run collects them.",
                    open_left)
    return len(tally.results)


_EXTRACTED = "full_description IS NOT NULL AND details_json IS NOT NULL"


def _select_jobs(conn, key: str, limit: int, rescore: bool, reassess: bool, urls: list[str] | None) -> list[dict]:
    if urls is not None:
        if not urls:
            return []
        rows = conn.execute(f"SELECT * FROM jobs WHERE {_EXTRACTED} AND url IN ({', '.join('?' * len(urls))})",
                            list(urls)).fetchall()
    elif rescore:
        query = f"SELECT * FROM jobs WHERE {_EXTRACTED}"
        if limit > 0:
            query += f" LIMIT {int(limit)}"
        rows = conn.execute(query).fetchall()
    elif reassess:
        rows = conn.execute(
            f"SELECT * FROM jobs WHERE {_EXTRACTED} AND (assessment_hash IS NULL OR assessment_hash != ?) "
            f"AND COALESCE(score_attempts, 0) < {MAX_SCORE_ATTEMPTS} ORDER BY fit_score DESC NULLS LAST, url",
            (key,),
        ).fetchall()
    else:
        # No SQL limit: the prefilter must see every pending job, and `limit` caps LLM calls below.
        return get_jobs_by_stage(conn=conn, stage="pending_score", limit=0)
    return [dict(r) for r in rows]


def _prefilter(conn, jobs: list[dict], key: str, prefs: Preferences) -> tuple[list[dict], int]:
    """Flag jobs whose title matches no target ("prefilter", fit_score 0) without an LLM call."""
    total = len(jobs)
    jobs, skipped = prefilter_jobs(jobs, load_target_tokens(prefs))
    now = datetime.now(UTC).isoformat()
    conn.executemany(
        "UPDATE jobs SET fit_assessment = ?, assessment_hash = ?, assessed_at = ?, scored_at = ?, "
        "score_reasoning = ? WHERE url = ?",
        [(json.dumps({"prefiltered": True}), key, now, now, PREFILTER_REASONING, j["url"]) for j in skipped],
    )
    conn.commit()
    recompute_fit(conn, [j["url"] for j in skipped], prefs)
    log.info("prefilter skipped %d/%d jobs (title not relevant; --no-prefilter to assess them)",
             len(skipped), total)
    return jobs, len(skipped)


# Requests built to estimate a re-assessment's cost; the estimate scales them up to the job count.
_PREVIEW_SAMPLE = 5


def reassess_preview(conn, new_prefs: Preferences) -> dict:
    """What saving `new_prefs` would cost in re-assessment, before anything is saved.

    Returns:
        {"reassess_needed", "jobs_to_reassess", "estimated_usd"}. reassess_needed is False when the assessment
        inputs are unchanged (preference-only edits are free); then nothing needs re-assessing.
    """
    resume_text = load_resume_text()
    old_key = assessment_key(load_preferences(conn), resume_text)
    new_key = assessment_key(new_prefs, resume_text)
    if old_key == new_key:
        return {"reassess_needed": False, "jobs_to_reassess": 0, "estimated_usd": 0.0}
    jobs = _select_jobs(conn, new_key, 0, rescore=False, reassess=True, urls=None)
    estimate = 0.0
    if jobs:
        sample = jobs[::max(1, len(jobs) // _PREVIEW_SAMPLE)][:_PREVIEW_SAMPLE]
        requests = [params for _, params in assessment_requests(new_prefs, resume_text, sample)]
        estimate = estimate_cost("scoring", requests, batch=True) * len(jobs) / len(sample)
    return {"reassess_needed": True, "jobs_to_reassess": len(jobs), "estimated_usd": round(estimate, 4)}


def run_scoring(limit: int = 0, rescore: bool = False, prefilter: bool = True,
                mode: Literal["batch", "sync"] = "batch", poll_seconds: float = 30,
                urls: list[str] | None = None, reassess: bool = False) -> dict:
    """Assess pending jobs with Claude, then recompute every job's fit score from the assessments and preferences.

    Pending means extracted and never assessed. Each result is written to the DB as soon as it arrives, so a run
    that is stopped (Ctrl-C, budget, crash) keeps every assessment it already paid for; a batch left open is
    collected by the next batch run.

    Args:
        limit: Maximum number of jobs sent to the LLM in this run (0 = all). Applied after
            the prefilter, which costs no LLM calls and always covers every pending job.
        rescore: If True, re-assess every extracted job.
        prefilter: If True, jobs whose title shares no word with the search queries / target roles are flagged
            "prefilter" (fit_score 0) without an LLM call. Never applied to `urls`.
        mode: "batch" (Message Batches, half price, default) or "sync" (one request at a time). Runs of at most
            SYNC_MAX_JOBS jobs are always sync.
        poll_seconds: How often to check a submitted batch.
        urls: Assess exactly these jobs (the dashboard's Rescore), whatever their state.
        reassess: Also re-assess jobs whose assessment is stale (the resume or assessment inputs changed since).
            Only after the user confirmed the cost.

    Returns:
        {"scored", "errors", "parse_errors", "prefiltered", "elapsed", "distribution", "mode"}, plus
        "estimate_usd" for a batch and "stopped" when the budget, key or API stopped the run.
    """
    resume_text = RESUME_PATH.read_text(encoding="utf-8")
    conn = get_connection()
    prefs = load_preferences(conn)
    key = assessment_key(prefs, resume_text)
    extra: dict = {}
    # Results of batches a killed run left open come first, so those jobs aren't paid for twice. A batch run waits
    # for them; a sync or small run only takes the ones that have already ended.
    try:
        extra["resumed"] = _collect_open_batches(
            conn, poll_seconds, wait=mode == "batch" and not 0 < limit <= SYNC_MAX_JOBS and urls is None,
            key=key, prefs=prefs)
    except LLMStopRun as e:  # scoring now could pay again for jobs in the uncollected batch
        log.warning("Scoring stopped: could not collect open batches (%s).", e)
        return {"scored": 0, "errors": 0, "elapsed": 0.0, "distribution": [], "stopped": e.reason}

    jobs = _select_jobs(conn, key, limit, rescore, reassess, urls)
    if not jobs:
        log.info("No extracted jobs waiting for an assessment.")
        recompute_fit(conn, prefs=prefs)
        return {"scored": 0, "errors": 0, "elapsed": 0.0, "distribution": []}

    prefiltered = 0
    if prefilter and urls is None:
        jobs, prefiltered = _prefilter(conn, jobs, key, prefs)
        if not jobs:
            recompute_fit(conn, prefs=prefs)
            return {"scored": 0, "errors": 0, "parse_errors": 0, "prefiltered": prefiltered,
                    "elapsed": 0.0, "distribution": []}

    if limit > 0 and len(jobs) > limit:
        log.info("--limit %d: assessing %d of %d jobs; the rest stay pending.", limit, limit, len(jobs))
        jobs = jobs[:limit]

    if mode == "batch" and len(jobs) <= SYNC_MAX_JOBS:
        mode = "sync"
    log.info("Assessing %d jobs (%s)...", len(jobs), mode)
    t0 = time.time()
    tally = _Tally(conn, len(jobs), key, prefs)
    if mode == "sync":
        stopped = _score_sync(tally, resume_text, jobs)
    else:
        stopped = _score_batch(tally, resume_text, jobs, poll_seconds, extra)
    results, errors, parse_errors = tally.results, tally.errors, tally.parse_errors

    if not stopped and errors == len(jobs) and errors > 0:
        log.error("ALL %d jobs failed to score; check the Claude setup with `strelitzia doctor`. First error: %s",
                  errors, tally.first_error)

    elapsed = time.time() - t0
    log.info("Done: %d assessed, %d errors in %.1fs (%.1f jobs/sec)", len(results) - errors, errors, elapsed,
             len(results) / elapsed if elapsed > 0 else 0)
    # Each unparseable reply costs a retry on a later run (another paid request).
    log.info("JSON parse retries (scoring): %d", parse_errors)

    recompute_fit(conn, prefs=prefs)
    dist = conn.execute("""
        SELECT (fit_score / 10) * 10 AS band, COUNT(*) FROM jobs
        WHERE fit_score IS NOT NULL
        GROUP BY band ORDER BY band DESC
    """).fetchall()
    distribution = [(row[0], row[1]) for row in dist]

    stats = {
        "scored": len(results) - errors,
        "errors": errors,
        "parse_errors": parse_errors,
        "prefiltered": prefiltered,
        "elapsed": elapsed,
        "distribution": distribution,
    }
    stats.update(extra, mode=mode)
    if stopped:
        stats["stopped"] = stopped
    return stats
