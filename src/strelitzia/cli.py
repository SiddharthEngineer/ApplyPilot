"""Strelitzia CLI — the main entry point."""

from __future__ import annotations

import logging
import os
import shutil
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from strelitzia import __version__

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%H:%M:%S",
)

app = typer.Typer(
    name="strelitzia",
    help="Job discovery, scoring and tailoring, with a dashboard to track your applications.",
    no_args_is_help=True,
)
console = Console()
log = logging.getLogger(__name__)

# Valid pipeline stages (in execution order). tailor and cover moved to the Claude workbench (`strelitzia workbench`).
VALID_STAGES = ("discover", "enrich", "score", "extract", "pdf")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bootstrap() -> None:
    """Common setup: load env, create dirs, init DB."""
    from strelitzia.config import load_env, ensure_dirs
    from strelitzia.database import init_db

    load_env()
    ensure_dirs()
    init_db()


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"[bold]strelitzia[/bold] {__version__}")
        raise typer.Exit()


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

@app.callback()
def main(
    version: bool = typer.Option(
        False, "--version", "-V",
        help="Show version and exit.",
        callback=_version_callback,
        is_eager=True,
    ),
) -> None:
    """Strelitzia — job discovery, scoring and tailoring, with a dashboard to track your applications."""


@app.command()
def init() -> None:
    """Run the first-time setup wizard (profile, resume, search config)."""
    from strelitzia.wizard.init import run_wizard

    run_wizard()


@app.command()
def run(
    stages: Optional[list[str]] = typer.Argument(
        None,
        help=(
            "Pipeline stages to run. "
            f"Valid: {', '.join(VALID_STAGES)}, all. "
            "Defaults to 'all' if omitted, which leaves out discover (add it with --with-discover)."
        ),
    ),
    workers: int = typer.Option(1, "--workers", "-w", help="Parallel threads for discovery/enrichment stages."),
    stream: bool = typer.Option(False, "--stream", help="Run stages concurrently (streaming mode)."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview stages without executing."),
    no_cache: bool = typer.Option(
        False,
        "--no-cache",
        help="Bypass per-domain strategy cache in smart-extract (forces fresh LLM strategy for every site).",
    ),
    no_prefilter: bool = typer.Option(
        False,
        "--no-prefilter",
        help="Assess every job with the LLM. By default, jobs whose title shares no word with your "
             "search queries or target roles are flagged 'prefilter' (fit_score 0) without an LLM call.",
    ),
    limit: int | None = typer.Option(
        None, "--limit", "-l", min=1,
        help="Max jobs sent to the LLM by the score and extract stages (default: all).",
    ),
    sync: bool = typer.Option(
        False,
        "--sync",
        help="Score and extract with one request per job (full price, results right away) instead of a "
             "Message Batch (half price, usually done within an hour). Runs of 5 jobs or fewer are always sync.",
    ),
    with_discover: bool = typer.Option(
        False,
        "--with-discover",
        help="Also run the discover stage (job-board crawling). Off by default: add jobs from the dashboard.",
    ),
    reset_errors: bool = typer.Option(
        False,
        "--reset-errors",
        help="Mark jobs whose scoring failed with an LLM error as unscored again (resets their retry count), then exit.",
    ),
) -> None:
    """Run pipeline stages: discover, enrich, score, extract, pdf. Resumes are written in Claude (see `workbench`)."""
    _bootstrap()

    from strelitzia.pipeline import REMOVED_STAGES, run_pipeline

    stage_list = stages if stages else ["all"]

    # Validate stage names
    for s in stage_list:
        if s != "all" and s not in VALID_STAGES and s not in REMOVED_STAGES:
            console.print(
                f"[red]Unknown stage:[/red] '{s}'. "
                f"Valid stages: {', '.join(VALID_STAGES)}, all"
            )
            raise typer.Exit(code=1)

    if reset_errors:
        from strelitzia.database import reset_score_errors
        count = reset_score_errors()
        console.print(f"[green]Reset {count} job(s) with LLM scoring errors; they will be scored on the next run.[/green]")
        return

    # Gate AI stages behind Tier 2
    llm_stages = {"score", "extract"}
    if any(s in stage_list for s in llm_stages) or "all" in stage_list:
        from strelitzia.config import check_tier
        check_tier(2, "AI scoring")

    result = run_pipeline(
        stages=stage_list,
        dry_run=dry_run,
        stream=stream,
        workers=workers,
        no_cache=no_cache,
        prefilter=not no_prefilter,
        limit=limit,
        sync=sync,
        with_discover=with_discover,
    )

    if result.get("errors"):
        raise typer.Exit(code=1)


@app.command()
def discover(
    probe: bool = typer.Option(False, "--probe", help="Check each job board with a 3-result search and exit (no DB writes)."),
    sites: str | None = typer.Option(None, "--sites", help="Comma-separated JobSpy boards to probe (default: 'sites' in searches.yaml, plus one row per ATS kind)."),
    query: str = typer.Option("Software Engineer", "--query", "-q", help="Search term for --probe."),
    location: str = typer.Option("Remote", "--location", help="Location for --probe."),
) -> None:
    """Job-board tools. Use `strelitzia run discover` to crawl."""
    if not probe:
        console.print("Nothing to do. Use [bold]--probe[/bold] to check board health, "
                      "or [bold]strelitzia run discover[/bold] to crawl.")
        raise typer.Exit(code=1)

    from strelitzia.config import load_env, load_search_config
    from strelitzia.discovery.jobspy import DEFAULT_SITES, probe_boards

    load_env()
    cfg = load_search_config() or {}
    site_list = [s.strip() for s in sites.split(",") if s.strip()] if sites else list(cfg.get("sites") or DEFAULT_SITES)

    console.print(f"Probing {len(site_list)} board(s): \"{query}\" in {location}\n")
    results = probe_boards(
        site_list, query=query, location=location, proxy=cfg.get("proxy") or os.environ.get("PROXY"),
        country_indeed=cfg.get("defaults", {}).get("country_indeed", "usa"),
        include_ats=not sites,
    )

    colors = {"ok": "green", "empty": "yellow", "blocked": "red", "error": "red"}
    table = Table(title="Board health", show_header=True, header_style="bold cyan")
    table.add_column("Source", style="bold")
    table.add_column("Status")
    table.add_column("Rows", justify="right")
    table.add_column("Latency", justify="right")
    table.add_column("Detail", overflow="fold")
    for r in results:
        c = colors.get(r.status, "white")
        table.add_row(r.site, f"[{c}]{r.status}[/{c}]", str(r.rows), f"{r.latency_s:.1f}s", r.detail)
    console.print(table)


@app.command()
def status() -> None:
    """Show pipeline statistics from the database."""
    _bootstrap()

    from strelitzia.database import get_stats

    stats = get_stats()

    console.print("\n[bold]Strelitzia Pipeline Status[/bold]\n")

    # Summary table
    summary = Table(title="Pipeline Overview", show_header=True, header_style="bold cyan")
    summary.add_column("Metric", style="bold")
    summary.add_column("Count", justify="right")

    summary.add_row("Total jobs discovered", str(stats["total"]))
    summary.add_row("With full description", str(stats["with_description"]))
    summary.add_row("Pending enrichment", str(stats["pending_detail"]))
    summary.add_row("Enrichment errors", str(stats["detail_errors"]))
    summary.add_row("Scored by LLM", str(stats["scored"]))
    summary.add_row("Pending scoring", str(stats["unscored"]))
    summary.add_row("Tailored resumes", str(stats["tailored"]))
    summary.add_row("Pending tailoring (7+)", str(stats["untailored_eligible"]))
    summary.add_row("Cover letters", str(stats["with_cover_letter"]))
    summary.add_row("Ready to apply", str(stats["ready_to_apply"]))
    summary.add_row("Applied", str(stats["applied"]))

    console.print(summary)

    # Score distribution
    if stats["score_distribution"]:
        dist_table = Table(title="\nScore Distribution", show_header=True, header_style="bold yellow")
        dist_table.add_column("Score", justify="center")
        dist_table.add_column("Count", justify="right")
        dist_table.add_column("Bar")

        max_count = max(count for _, count in stats["score_distribution"]) or 1
        for score, count in stats["score_distribution"]:
            bar_len = int(count / max_count * 30)
            if score >= 70:
                color = "green"
            elif score >= 50:
                color = "yellow"
            else:
                color = "red"
            bar = f"[{color}]{'=' * bar_len}[/{color}]"
            dist_table.add_row(str(score) if score >= 100 else f"{score}-{score + 9}", str(count), bar)

        console.print(dist_table)

    # By site
    if stats["by_site"]:
        site_table = Table(title="\nJobs by Source", show_header=True, header_style="bold magenta")
        site_table.add_column("Site")
        site_table.add_column("Count", justify="right")

        for site, count in stats["by_site"]:
            site_table.add_row(site or "Unknown", str(count))

        console.print(site_table)

    console.print()


@app.command()
def classify() -> None:
    """Fill role category, work mode and location for jobs not yet LLM-extracted (keyword rules, no LLM)."""
    _bootstrap()

    from strelitzia.enrichment.classify import run_classify

    result = run_classify()
    console.print(f"[green]Classified {result['classified']} job(s) by heuristics.[/green]")
    table = Table(title="Role categories (heuristic)", show_header=True, header_style="bold cyan")
    table.add_column("Category")
    table.add_column("Jobs", justify="right")
    for category, count in result["by_category"].items():
        table.add_row(category, str(count))
    console.print(table)


workbench_app = typer.Typer(
    help="Claude workbench: print a job's packet for a Claude chat, and import the reply.", no_args_is_help=True,
)
app.add_typer(workbench_app, name="workbench")


def _workbench_job(key: str) -> tuple:
    from strelitzia import users
    from strelitzia.database import backfill_job_keys, get_connection

    _bootstrap()
    conn = get_connection()
    backfill_job_keys(conn)
    # The CLI acts as the owner: every user's row for a posting shares its key.
    row = conn.execute("SELECT * FROM jobs WHERE user_id = ? AND job_key = ?", (users.owner(conn).id, key)).fetchone()
    if row is None:
        console.print(f"[red]No job with key {key}.[/red] The key is the last part of the job's dashboard URL.")
        raise typer.Exit(code=1)
    return conn, dict(row)


def _workbench_inputs():
    from strelitzia.scoring.workbench import Inputs

    try:
        return Inputs.load()
    except FileNotFoundError as e:
        console.print(f"[red]{e}.[/red] Run `strelitzia init` first.")
        raise typer.Exit(code=1) from e


@workbench_app.command("packet")
def workbench_packet(key: str = typer.Argument(..., help="The job's key (from its dashboard URL).")) -> None:
    """Print the job's full packet (posting, qualifications, rules, content library) to paste into Claude."""
    from strelitzia.scoring.workbench import packet_for

    _, job = _workbench_job(key)
    typer.echo(packet_for(job, _workbench_inputs(), inline=True).prompt)


@workbench_app.command("import")
def workbench_import(
    key: str = typer.Argument(..., help="The job's key (from its dashboard URL)."),
    file: Path = typer.Argument(..., exists=True, dir_okay=False, help="A file holding Claude's reply."),
) -> None:
    """Check a Claude reply and, if it passes, save the resume PDF/DOCX and cover letter as a new draft."""
    from strelitzia.config import drive_sync_enabled
    from strelitzia.scoring.workbench import import_result

    conn, job = _workbench_job(key)
    outcome = import_result(conn, job, file.read_text(encoding="utf-8"), _workbench_inputs())
    for w in outcome.warnings:
        console.print(f"[yellow]Warning:[/yellow] {w}", highlight=False)
    if not outcome.ok:
        console.print("[red]Not saved.[/red] Paste this into the same chat:\n", highlight=False)
        typer.echo(outcome.fix_prompt)
        raise typer.Exit(code=1)
    console.print(f"[green]Saved draft v{outcome.draft_version}[/green]")
    for kind, path in outcome.files.items():
        typer.echo(f"  {kind}: {path}")
    if drive_sync_enabled():
        try:
            from strelitzia.storage.drive import DriveClient
            from strelitzia.storage.sync import sync_job

            row = conn.execute("SELECT * FROM jobs WHERE user_id = ? AND url = ?",
                               (job["user_id"], job["url"])).fetchone()
            sync_job(conn, DriveClient.from_credentials(), dict(row))
            console.print("  Moved the PDFs to Google Drive.")
        except Exception as e:  # noqa: BLE001 -- the files are saved locally; `strelitzia drive sync` retries
            console.print(f"[yellow]Drive upload failed (run `strelitzia drive sync` later):[/yellow] {e}")


template_app = typer.Typer(help="Your resume template: create the fixed blocks and preview the layout.", no_args_is_help=True)
app.add_typer(template_app, name="template")


def _template_inputs():
    from strelitzia.config import CONTENT_LIBRARY_PATH, RESUME_PATH, load_env, load_profile
    from strelitzia.scoring.content_library import parse_content_library

    load_env()
    if not RESUME_PATH.exists():
        console.print(f"[red]No base resume at {RESUME_PATH}.[/red] Run `strelitzia init` first.")
        raise typer.Exit(code=1)
    library = parse_content_library(CONTENT_LIBRARY_PATH) if CONTENT_LIBRARY_PATH.exists() else None
    return load_profile(), RESUME_PATH.read_text(encoding="utf-8"), library


@template_app.command("init")
def template_init(
    force: bool = typer.Option(False, "--force", help="Overwrite existing resume_fixed.yaml / resume_template.html."),
) -> None:
    """Write resume_fixed.yaml (education + role facts from resume.txt) and a starter resume_template.html."""
    from strelitzia.config import RESUME_FIXED_PATH, RESUME_TEMPLATE_PATH
    from strelitzia.scoring.template import DEFAULT_TEMPLATE_PATH, write_fixed_yaml

    profile, resume_text, library = _template_inputs()
    if RESUME_FIXED_PATH.exists() and not force:
        console.print(f"[yellow]Kept existing {RESUME_FIXED_PATH}[/yellow] (use --force to regenerate)")
    else:
        console.print(f"[green]Wrote {write_fixed_yaml(resume_text, library, profile)}[/green]")
    if RESUME_TEMPLATE_PATH.exists() and not force:
        console.print(f"[yellow]Kept existing {RESUME_TEMPLATE_PATH}[/yellow]")
    else:
        shutil.copyfile(DEFAULT_TEMPLATE_PATH, RESUME_TEMPLATE_PATH)
        console.print(f"[green]Wrote {RESUME_TEMPLATE_PATH}[/green] (the default layout; edit it to match your resume)")


@template_app.command("preview")
def template_preview() -> None:
    """Render your base resume through the template to template_preview.pdf for a side-by-side check."""
    from strelitzia.scoring.template import preview_template

    profile, resume_text, library = _template_inputs()
    info = preview_template(profile, resume_text, library)
    console.print(f"[green]Preview:[/green] {info['path']}  (HTML: {info['html_path']})")
    if info["overflow"]:
        console.print(
            f"[yellow]Overflows one page[/yellow] ({info['content_height_pt']}pt of {info['usable_height_pt']:.0f}pt)."
        )
    else:
        console.print(f"One page ({info['content_height_pt']}pt of {info['usable_height_pt']:.0f}pt).")


db_app = typer.Typer(help="Job store: copy the SQLite jobs into Postgres and check the copy.", no_args_is_help=True)
app.add_typer(db_app, name="db")


def _db_endpoints(src: Optional[str], dst: Optional[str]):

    from strelitzia.config import DB_PATH, database_url, load_env

    load_env()
    source = Path(src).expanduser() if src else DB_PATH
    target = dst or database_url()
    if not target:
        console.print("[red]No destination.[/red] Pass --to postgresql://… or set STRELITZIA_DATABASE_URL.")
        raise typer.Exit(code=1)
    if not source.exists():
        console.print(f"[red]No SQLite database at {source}.[/red]")
        raise typer.Exit(code=1)
    return source, target


def _safe_target(target: str) -> str:
    from strelitzia.db_pg import describe_url

    return describe_url(target) if "://" in target else target


@db_app.command("migrate")
def db_migrate(
    src: Optional[str] = typer.Option(None, "--from", help="SQLite file to copy (default: ~/.strelitzia/strelitzia.db)."),
    dst: Optional[str] = typer.Option(None, "--to", help="Destination URL (default: STRELITZIA_DATABASE_URL)."),
    batch: int = typer.Option(500, "--batch", help="Rows per insert batch."),
) -> None:
    """Copy every job into the destination. Safe to rerun: urls already there are skipped."""
    from strelitzia.migrate import migrate

    source, target = _db_endpoints(src, dst)
    result = migrate(source, target, batch=batch)
    if result.get("backup"):
        console.print(f"Backed up {source} to {result['backup']}")
    if result["missing_columns"]:
        console.print(f"[yellow]Not copied (no such column in the destination):[/yellow] {', '.join(result['missing_columns'])}")
    console.print(
        f"[green]Copied {result['inserted']} new jobs[/green] to {_safe_target(target)} "
        f"({result['skipped']} already there; destination now has {result['destination']})."
    )


@db_app.command("verify")
def db_verify(
    src: Optional[str] = typer.Option(None, "--from", help="SQLite file (default: ~/.strelitzia/strelitzia.db)."),
    dst: Optional[str] = typer.Option(None, "--to", help="Destination URL (default: STRELITZIA_DATABASE_URL)."),
) -> None:
    """Compare row counts (and fit_score/tailored_resume_path/... counts) between the SQLite file and the destination."""
    from rich.table import Table

    from strelitzia.migrate import verify

    source, target = _db_endpoints(src, dst)
    checks = verify(source, target)
    table = Table(title=f"{source} vs {_safe_target(target)}")
    for name in ("Check", "SQLite", "Destination", ""):
        table.add_column(name)
    bad = 0
    for check, a, b in checks:
        ok = a == b
        bad += not ok
        table.add_row(check, str(a), str(b), "[green]OK[/green]" if ok else "[red]MISMATCH[/red]")
    console.print(table)
    if bad:
        console.print(f"[red]{bad} mismatch(es).[/red] Rerun `strelitzia db migrate` to copy missing rows.")
        raise typer.Exit(code=1)
    console.print("[green]All counts match.[/green]")


users_app = typer.Typer(help="User accounts: invite emails, set monthly Claude caps, disable access.", no_args_is_help=True)
app.add_typer(users_app, name="users")


def _users_conn():
    _bootstrap()
    from strelitzia import users
    from strelitzia.database import get_connection

    conn = get_connection()
    users.owner(conn)  # created on first use, so the list always shows it
    return conn


def _users_table(conn, accounts) -> Table:
    from strelitzia.llm import user_month_spend

    table = Table(title="Users")
    for name in ("Email", "Name", "Role", "Status", "Cap (USD/month)", "Spent this month", "Last sign-in"):
        table.add_column(name)
    for u in accounts:
        cap = "none" if u.monthly_cap_usd is None else f"${u.monthly_cap_usd:.2f}"
        table.add_row(u.email, u.name or "", u.role, u.status, cap, f"${user_month_spend(conn, u.id):.2f}",
                      (u.last_login_at or "")[:16].replace("T", " "))
    return table


def _account(conn, email: str):
    from strelitzia import users

    user = users.by_email(conn, email)
    if user is None:
        console.print(f"[red]No account for {email}.[/red] Invite it with `strelitzia users invite {email}`.")
        raise typer.Exit(code=1)
    return user


@users_app.command("list")
def users_list() -> None:
    """Every account: the owner first, then invited users by email."""
    from strelitzia import users

    conn = _users_conn()
    console.print(_users_table(conn, users.list_users(conn)))


@users_app.command("invite")
def users_invite(
    email: str = typer.Argument(..., help="Google account email to let in."),
    cap: float = typer.Option(5.0, "--cap", min=0, help="Monthly Claude spend cap in USD."),
) -> None:
    """Let an email sign in to the dashboard (the account turns active on its first sign-in)."""
    from strelitzia import users

    conn = _users_conn()
    if users.by_email(conn, email) is not None:
        console.print(f"[red]{email} already has an account.[/red]")
        raise typer.Exit(code=1)
    console.print(_users_table(conn, [users.invite(conn, email, cap)]))


@users_app.command("disable")
def users_disable(email: str = typer.Argument(..., help="The account's email.")) -> None:
    """Stop an account from signing in (its data stays)."""
    from strelitzia import users

    conn = _users_conn()
    user = _account(conn, email)
    if user.role == "owner":
        console.print("[red]You can't disable the owner account.[/red]")
        raise typer.Exit(code=1)
    console.print(_users_table(conn, [users.set_status(conn, user.id, "disabled")]))


@users_app.command("cap")
def users_cap(
    email: str = typer.Argument(..., help="The account's email."),
    usd: float = typer.Argument(..., min=0, help="Monthly Claude spend cap in USD."),
) -> None:
    """Change an account's monthly Claude spend cap."""
    from strelitzia import users

    conn = _users_conn()
    user = _account(conn, email)
    if user.role == "owner":
        console.print("[red]The owner has no cap[/red] (LLM_MONTHLY_BUDGET_USD limits the whole server).")
        raise typer.Exit(code=1)
    console.print(_users_table(conn, [users.set_cap(conn, user.id, usd)]))


drive_app = typer.Typer(help="Google Drive: move tailored resumes and cover letters there and list their links.",
                        no_args_is_help=True)
app.add_typer(drive_app, name="drive")


@drive_app.command("auth")
def drive_auth(
    client_secret: Optional[str] = typer.Option(
        None, "--client-secret", help="OAuth client JSON (default: ~/.strelitzia/google_client_secret.json)."),
    port: int = typer.Option(8765, "--port", help="Local port Google redirects back to."),
    no_browser: bool = typer.Option(False, "--no-browser", help="Print the sign-in URL instead (headless VPS)."),
) -> None:
    """Sign in to Google and allow Strelitzia to manage the files it creates in your Drive."""

    from strelitzia.config import GOOGLE_CLIENT_SECRET_PATH, ensure_dirs, load_env
    from strelitzia.storage.drive import DriveNotConfigured, authorize

    load_env()
    ensure_dirs()
    secret = Path(client_secret).expanduser() if client_secret else GOOGLE_CLIENT_SECRET_PATH
    if no_browser:
        console.print(
            f"On a headless machine, first open a tunnel from your laptop:\n"
            f"  [bold]ssh -N -L {port}:localhost:{port} <user>@<this-server>[/bold]\n"
            f"then open the URL below in your laptop's browser.\n"
        )
    try:
        token = authorize(secret, port=port, open_browser=not no_browser)
    except DriveNotConfigured as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1)
    console.print(f"[green]Google Drive authorized.[/green] Token saved to {token}")


@drive_app.command("sync")
def drive_sync(
    limit: Optional[int] = typer.Option(None, "--limit", help="Only move files for this many jobs."),
    keep_local: bool = typer.Option(False, "--keep-local", help="Upload but keep the local PDFs."),
) -> None:
    """Move tailored resume and cover letter PDFs to Drive and save their links."""
    _bootstrap()
    from strelitzia.storage.drive import DriveNotConfigured
    from strelitzia.storage.sync import run_drive_sync

    try:
        result = run_drive_sync(limit=limit, keep_local=keep_local)
    except DriveNotConfigured as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1)
    table = Table(title="Drive sync")
    for col in ("New files", "Updated", "Local copies removed", "Missing locally", "Errors"):
        table.add_column(col, justify="right")
    table.add_row(*(str(result[k]) for k in ("uploaded", "updated", "moved", "missing", "errors")))
    console.print(table)
    if result["errors"]:
        raise typer.Exit(code=1)


@drive_app.command("links")
def drive_links(
    company: Optional[str] = typer.Option(None, "--company", help="Only jobs whose company contains this text."),
    csv_path: Optional[str] = typer.Option(None, "--csv", help="Also write the links to this CSV file."),
) -> None:
    """List the Drive links saved for each job."""
    _bootstrap()
    import csv

    from strelitzia.database import get_connection, job_company
    from strelitzia.storage.drive_layout import job_date

    conn = get_connection()
    cur = conn.execute(
        "SELECT url, title, site, company, tailored_at, cover_letter_at, resume_drive_url, cover_letter_drive_url "
        "FROM jobs WHERE resume_drive_url IS NOT NULL OR cover_letter_drive_url IS NOT NULL ORDER BY tailored_at"
    )
    names = [d[0] for d in cur.description]
    rows = []
    for values in cur.fetchall():
        job = dict(zip(names, values))
        name = job_company(job)
        if company and company.lower() not in name.lower():
            continue
        rows.append({
            "company": name, "role": job["title"] or "", "date": job_date(job, "resume"), "site": job["site"] or "",
            "resume_url": job["resume_drive_url"] or "", "cover_letter_url": job["cover_letter_drive_url"] or "",
            "job_url": job["url"],
        })
    if not rows:
        console.print("No Drive links yet. Run `strelitzia drive sync`.")
        return
    table = Table(title=f"Drive links ({len(rows)} jobs)")
    for col in ("Company", "Role", "Date", "Site", "Resume", "Cover letter"):
        table.add_column(col, overflow="fold")
    for r in rows:
        table.add_row(r["company"], r["role"], r["date"], r["site"], r["resume_url"], r["cover_letter_url"])
    console.print(table)
    if csv_path:
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        console.print(f"[green]Wrote {csv_path}[/green]")


@app.command()
def dashboard() -> None:
    """Generate and open the HTML dashboard in your browser."""
    _bootstrap()

    from strelitzia.view import open_dashboard

    open_dashboard()


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host", help="Interface to listen on."),
    port: int = typer.Option(8765, "--port", help="Port to listen on."),
    web_dir: Optional[str] = typer.Option(
        None, "--web-dir", help="Built dashboard UI (default: $STRELITZIA_WEB_DIR, else the bundle shipped in the package).",
    ),
) -> None:
    """Run the dashboard API (and UI) at http://HOST:PORT/app/."""
    _bootstrap()
    try:
        import uvicorn

        from strelitzia.web.app import create_app
    except ImportError:
        console.print('[red]The dashboard needs the web extra:[/red] pip install -e ".[web]"')
        raise typer.Exit(code=1)

    # One worker: generation tasks run on a single background thread inside this process.
    uvicorn.run(create_app(static_dir=web_dir), host=host, port=port, workers=1, log_level="info")


def _doctor_database_row(ok_mark: str) -> tuple[str, str, str]:
    from strelitzia.config import DB_PATH, database_url
    from strelitzia.db_pg import describe_url

    url = database_url()
    where = f"postgresql ({describe_url(url)})" if url else f"sqlite ({DB_PATH})"
    if not url and not DB_PATH.exists():
        return ("Database", "[yellow]WARN[/yellow]", f"{where}: not created yet (run 'strelitzia run discover')")
    try:
        from strelitzia.database import get_connection

        conn = get_connection()
        jobs = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] if _has_jobs_table(conn) else 0
    except Exception as exc:  # unreachable server, wrong password, psycopg not installed...
        hint = ' (pip install -e ".[postgres]")' if isinstance(exc, ImportError) else ""
        return ("Database", "[red]ERROR[/red]", f"{where}: {exc}{hint}")
    return ("Database", ok_mark, f"{where}, {jobs} jobs")


def _doctor_spend_rows(ok_mark: str, warn_mark: str) -> list[tuple[str, str, str]]:
    """Month-to-date LLM spend against LLM_MONTHLY_BUDGET_USD, and Message Batches not yet collected."""
    from strelitzia.config import DB_PATH, database_url
    from strelitzia.llm import month_spend, monthly_budget, reserved_spend

    budget = monthly_budget()
    spent, reserved, batches = 0.0, 0.0, []
    if database_url() or DB_PATH.exists():
        try:
            from strelitzia.database import get_connection, table_columns
            from strelitzia.llm_batch import open_batches

            conn = get_connection()
            if table_columns(conn, "llm_usage"):
                spent = month_spend(conn)
            if table_columns(conn, "llm_batches"):
                batches = open_batches(conn=conn)
                reserved = reserved_spend(conn)
        except Exception as exc:  # noqa: BLE001 - the Database row above reports connection problems
            return [("LLM spend", warn_mark, f"unavailable: {exc}")]
    open_note = f" + ${reserved:.2f} estimated in open batches" if reserved else ""
    if budget is None:
        rows = [("LLM spend", warn_mark,
                 f"${spent:.2f} this month{open_note}, no budget (set LLM_MONTHLY_BUDGET_USD)")]
    else:
        over = spent + reserved >= budget
        rows = [("LLM spend", warn_mark if over else ok_mark, f"${spent:.2f}{open_note} of ${budget:.2f} this month"
                 + (" (budget reached: LLM stages stop)" if over else ""))]
    if batches:
        detail = ", ".join(f"{b['purpose']} {b['id']} ({b['request_count']} requests, {b['submitted_at']})"
                           for b in batches)
        rows.append(("Open batches", warn_mark, f"{detail}; the next batch run of that stage collects them"))
    else:
        rows.append(("Open batches", ok_mark, "none"))
    return rows


def _has_jobs_table(conn) -> bool:
    from strelitzia.database import table_columns

    return bool(table_columns(conn))


@app.command()
def doctor() -> None:
    """Check your setup and diagnose missing requirements."""
    from strelitzia.config import (
        load_env, PROFILE_PATH, RESUME_PATH, RESUME_PDF_PATH,
        SEARCH_CONFIG_PATH, CONTENT_LIBRARY_PATH,
    )

    load_env()

    ok_mark = "[green]OK[/green]"
    fail_mark = "[red]MISSING[/red]"
    warn_mark = "[yellow]WARN[/yellow]"

    results: list[tuple[str, str, str]] = []  # (check, status, note)

    # --- Tier 1 checks ---
    # Profile
    if PROFILE_PATH.exists():
        results.append(("profile.json", ok_mark, str(PROFILE_PATH)))
    else:
        results.append(("profile.json", fail_mark, "Run 'strelitzia init' to create"))

    # Resume
    if RESUME_PATH.exists():
        results.append(("resume.txt", ok_mark, str(RESUME_PATH)))
    elif RESUME_PDF_PATH.exists():
        results.append(("resume.txt", warn_mark, "Only PDF found — plain-text needed for AI stages"))
    else:
        results.append(("resume.txt", fail_mark, "Run 'strelitzia init' to add your resume"))

    # Content library
    if CONTENT_LIBRARY_PATH.exists():
        try:
            from strelitzia.scoring.content_library import parse_content_library
            lib = parse_content_library(CONTENT_LIBRARY_PATH)
            n_roles = len(lib.roles)
            n_projects = sum(len(r.projects) for r in lib.roles)
            n_angles = len(lib.all_angles)
            results.append((
                "content_library.md", ok_mark,
                f"{CONTENT_LIBRARY_PATH} — parsed: {n_roles} roles, {n_projects} projects, {n_angles} angle tags",
            ))
        except Exception as exc:
            results.append((
                "content_library.md", "[red]ERROR[/red]",
                f"File exists but cannot be parsed: {exc}",
            ))
    else:
        results.append((
            "content_library.md", warn_mark,
            "Run 'strelitzia init' and choose content library workflow",
        ))

    # Search config
    if SEARCH_CONFIG_PATH.exists():
        results.append(("searches.yaml", ok_mark, str(SEARCH_CONFIG_PATH)))
    else:
        results.append(("searches.yaml", warn_mark, "Will use example config — run 'strelitzia init'"))

    # Job store: SQLite file, or Postgres when STRELITZIA_DATABASE_URL is set
    results.append(_doctor_database_row(ok_mark))

    # Discovery: off unless asked for (strelitzia run --with-discover, or run discover)
    from strelitzia.pipeline import DISCOVERY_OFF_NOTE
    results.append(("Discovery", ok_mark, f"{DISCOVERY_OFF_NOTE}; add jobs with the dashboard's Add job"))

    # jobspy (discovery dep installed separately)
    try:
        import jobspy  # noqa: F401
        results.append(("python-jobspy", ok_mark, "Job board scraping available"))
    except ImportError:
        results.append(("python-jobspy", warn_mark,
                        'pip install "python-jobspy>=1.2.0"'))

    # Google Drive (optional)
    from strelitzia.storage.drive import drive_status
    drive_state, drive_detail = drive_status()
    results.append(("Google Drive", ok_mark if drive_state == "authorized" else warn_mark,
                    {"authorized": f"Authorized ({drive_detail})",
                     "not_authorized": f"Not authorized: {drive_detail}",
                     "not_installed": f"Optional: {drive_detail}"}[drive_state]))

    # --- Tier 2 checks ---
    from strelitzia.llm import PURPOSES, resolve_model
    if os.environ.get("ANTHROPIC_API_KEY", "").strip():
        results.append(("Claude API key", ok_mark, "ANTHROPIC_API_KEY is set"))
    else:
        results.append(("Claude API key", fail_mark,
                        "Set ANTHROPIC_API_KEY in ~/.strelitzia/.env (run 'strelitzia init')"))
    for purpose in PURPOSES:
        model, effort = resolve_model(purpose)
        results.append((f"{purpose.title()} model", ok_mark, f"{model} (effort {effort})"))
    results.extend(_doctor_spend_rows(ok_mark, warn_mark))

    # --- Render results ---
    console.print()
    console.print("[bold]Strelitzia Doctor[/bold]\n")

    col_w = max(len(r[0]) for r in results) + 2
    for check, status, note in results:
        pad = " " * (col_w - len(check))
        console.print(f"  {check}{pad}{status}  [dim]{note}[/dim]")

    console.print()

    # Tier summary
    from strelitzia.config import get_tier, TIER_LABELS
    tier = get_tier()
    console.print(f"[bold]Current tier: Tier {tier} — {TIER_LABELS[tier]}[/bold]")

    if tier == 1:
        console.print("[dim]  → Tier 2 unlocks: scoring and extraction (needs LLM API key)[/dim]")

    if CONTENT_LIBRARY_PATH.exists():
        from strelitzia.config import RESUME_FIXED_PATH, RESUME_TEMPLATE_PATH
        console.print("[dim]  → Resumes are tailored from it in Claude (dashboard → Open in Claude; see /app/claude-setup)[/dim]")
        if not (RESUME_TEMPLATE_PATH.exists() and RESUME_FIXED_PATH.exists()):
            console.print("[dim]  → Run `strelitzia template init` to set up your resume template[/dim]")

    console.print()


def legacy_main() -> None:
    """The deprecated `applypilot` console script: the same app, with a notice on stderr."""
    print("applypilot: this command is deprecated, use `strelitzia` (same commands and options).", file=sys.stderr)
    app()


if __name__ == "__main__":
    app()
