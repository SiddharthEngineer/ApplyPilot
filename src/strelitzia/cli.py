"""Strelitzia CLI — the main entry point."""

from __future__ import annotations

import logging
import os
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
    help="Strelitzia server admin: run the dashboard, check the setup, manage the database and user accounts.",
    no_args_is_help=True,
)
console = Console()
log = logging.getLogger(__name__)

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
    """Strelitzia server admin. Every per-user action (adding, scoring, tailoring, tracking jobs) is in the dashboard."""


@app.command()
def init() -> None:
    """Run the first-time setup wizard (profile, resume, search config)."""
    from strelitzia.wizard.init import run_wizard

    run_wizard()


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
    _bootstrap()  # init_db creates the owner row, so the list always shows it
    from strelitzia.database import get_connection

    return get_connection()


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

    if not users.looks_like_email(email):
        console.print(f"[red]{email!r} isn't an email address.[/red]")
        raise typer.Exit(code=1)
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


@users_app.command("enable")
def users_enable(email: str = typer.Argument(..., help="The account's email.")) -> None:
    """Let a disabled account sign in again."""
    from strelitzia import users

    conn = _users_conn()
    user = _account(conn, email)
    if user.status != "disabled":
        console.print(f"{user.email} isn't disabled ({user.status}).")
        return
    # Back to invited if it never signed in, so the first sign-in still activates it.
    status = "active" if user.last_login_at else "invited"
    console.print(_users_table(conn, [users.set_status(conn, user.id, status)]))


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
        return ("Database", "[yellow]WARN[/yellow]", f"{where}: not created yet (run 'strelitzia serve')")
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


def _doctor_library_row(ok_mark: str, warn_mark: str) -> tuple[str, str, str]:
    """The owner's content library, which the dashboard's Claude workbench reads from the data dir."""
    from strelitzia.config import CONTENT_LIBRARY_PATH

    if not CONTENT_LIBRARY_PATH.exists():
        return ("content_library.md", warn_mark, "Run 'strelitzia init' and choose content library workflow")
    try:
        from strelitzia.scoring.content_library import parse_content_library

        lib = parse_content_library(CONTENT_LIBRARY_PATH)
    except Exception as exc:
        return ("content_library.md", "[red]ERROR[/red]", f"File exists but cannot be parsed: {exc}")
    n_projects = sum(len(r.projects) for r in lib.roles)
    return ("content_library.md", ok_mark, f"{CONTENT_LIBRARY_PATH} — parsed: {len(lib.roles)} roles, "
            f"{n_projects} projects, {len(lib.all_angles)} angle tags")


def _doctor_auth_rows(ok_mark: str, fail_mark: str, warn_mark: str) -> list[tuple[str, str, str]]:
    """Google sign-in and Drive connect keys: presence only, never the values."""
    from strelitzia.config import env

    if not env("GOOGLE_CLIENT_ID"):
        return [("Google sign-in", warn_mark, "Off: set STRELITZIA_GOOGLE_CLIENT_ID to turn login on "
                 "(see agents/DEPLOY.md, Google sign-in)"),
                ("Drive connect", warn_mark, "Needs Google sign-in")]
    missing = [f"STRELITZIA_{name}" for name in ("GOOGLE_CLIENT_SECRET", "SESSION_SECRET", "OWNER_EMAIL")
               if not env(name)]
    if missing:
        rows = [("Google sign-in", fail_mark, f"STRELITZIA_GOOGLE_CLIENT_ID is set but {', '.join(missing)} is not")]
    else:
        rows = [("Google sign-in", ok_mark, f"On, owner {env('OWNER_EMAIL').strip().lower()}")]
    if not env("PUBLIC_URL"):
        rows.append(("Public URL", warn_mark, "STRELITZIA_PUBLIC_URL unset: OAuth redirects use the request's host"))
    from strelitzia.storage.drive import drive_libraries_installed, INSTALL_HINT
    if not drive_libraries_installed():
        rows.append(("Drive connect", warn_mark, f"Optional: {INSTALL_HINT}"))
    elif env("TOKEN_KEY"):
        rows.append(("Drive connect", ok_mark, "Users can connect their Drive (STRELITZIA_TOKEN_KEY is set)"))
    else:
        rows.append(("Drive connect", warn_mark, "Set STRELITZIA_TOKEN_KEY to let users connect their Drive"))
    return rows


@app.command()
def doctor() -> None:
    """Check the server setup: database, sign-in and Drive keys, Claude key, models and spend."""
    from strelitzia.config import load_env

    load_env()

    ok_mark = "[green]OK[/green]"
    fail_mark = "[red]MISSING[/red]"
    warn_mark = "[yellow]WARN[/yellow]"

    results: list[tuple[str, str, str]] = []  # (check, status, note)

    # Job store: SQLite file, or Postgres when STRELITZIA_DATABASE_URL is set
    results.append(_doctor_database_row(ok_mark))
    results.extend(_doctor_auth_rows(ok_mark, fail_mark, warn_mark))
    results.append(_doctor_library_row(ok_mark, warn_mark))

    # Discovery: off by default; jobs come from the dashboard's Add job
    results.append(("Discovery", ok_mark, "off; add jobs with the dashboard's Add job"))

    # jobspy (discovery dep installed separately)
    try:
        import jobspy  # noqa: F401
        results.append(("python-jobspy", ok_mark, "Job board scraping available"))
    except ImportError:
        results.append(("python-jobspy", warn_mark,
                        'pip install "python-jobspy>=1.2.0"'))

    # Claude
    from strelitzia.llm import PURPOSES, resolve_model
    if os.environ.get("ANTHROPIC_API_KEY", "").strip():
        results.append(("Claude API key", ok_mark, "ANTHROPIC_API_KEY is set"))
    else:
        results.append(("Claude API key", fail_mark,
                        "Set ANTHROPIC_API_KEY in ~/.strelitzia/.env (run 'strelitzia init')"))
    for purpose in PURPOSES:
        model, effort = resolve_model(purpose)
        results.append((f"{purpose.replace('_', ' ').title()} model", ok_mark, f"{model} (effort {effort})"))
    results.extend(_doctor_spend_rows(ok_mark, warn_mark))

    # --- Render results ---
    console.print()
    console.print("[bold]Strelitzia Doctor[/bold]\n")

    col_w = max(len(r[0]) for r in results) + 2
    for check, status, note in results:
        pad = " " * (col_w - len(check))
        console.print(f"  {check}{pad}{status}  [dim]{note}[/dim]")

    console.print()


def legacy_main() -> None:
    """The deprecated `applypilot` console script: the same app, with a notice on stderr."""
    print("applypilot: this command is deprecated, use `strelitzia` (same commands and options).", file=sys.stderr)
    app()


if __name__ == "__main__":
    app()
