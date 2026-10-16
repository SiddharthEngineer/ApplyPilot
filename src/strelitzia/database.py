"""Strelitzia database layer: schema, migrations, stats, and connection helpers.

Single source of truth for the jobs table schema. All columns from every
pipeline stage are created up front so any stage can run independently
without migration ordering issues.
"""

import hashlib
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import TypeAlias

from strelitzia import config, users
from strelitzia.config import DB_PATH
from strelitzia.db_pg import PgConnection
from strelitzia.enrichment.posting_model import EXTRACT_VERSION

# Either backend: sqlite3 (default) or Postgres (STRELITZIA_DATABASE_URL), which exposes the same API.
Connection: TypeAlias = sqlite3.Connection | PgConnection

try:  # optional: only the Postgres backend needs psycopg
    import psycopg as _psycopg
except ImportError:
    _psycopg = None

# Duplicate-key errors on either backend: `except IntegrityError:` around an INSERT/UPDATE.
IntegrityError: tuple[type[Exception], ...] = (sqlite3.IntegrityError,) + (
    (_psycopg.IntegrityError,) if _psycopg else ()
)
# A cached connection that can't be used any more (closed, or the server dropped it).
_STALE_CONNECTION_ERRORS: tuple[type[Exception], ...] = (sqlite3.ProgrammingError,) + (
    (_psycopg.OperationalError, _psycopg.InterfaceError) if _psycopg else ()
)

# Thread-local connection storage — each thread gets its own connection
# (required for SQLite thread safety with parallel workers; psycopg connections aren't shared either)
_local = threading.local()


def _is_pg_url(target: str) -> bool:
    return target.startswith(("postgresql://", "postgres://"))


def _resolve_target(db_path: Path | str | None) -> str:
    """The database to open: an explicit path or URL, else STRELITZIA_DATABASE_URL, else DB_PATH.

    A `sqlite:///path` URL is accepted too and means that SQLite file.
    """
    if db_path is None:
        return config.database_url() or str(DB_PATH)
    target = str(db_path)
    if target.startswith("sqlite:///"):
        return target[len("sqlite:///"):]  # sqlite:////abs/path → /abs/path, sqlite:///rel → rel
    return target


def get_connection(db_path: Path | str | None = None) -> Connection:
    """Get a thread-local cached connection.

    Postgres when the target is a `postgresql://` URL (by default STRELITZIA_DATABASE_URL), otherwise a SQLite
    connection with WAL mode enabled. Connections are cached and reused within the same thread.

    Args:
        db_path: Override the default database (a SQLite path or a database URL). Useful for testing.

    Returns:
        A sqlite3.Connection (row factory sqlite3.Row) or a PgConnection with the same API.
    """
    path = _resolve_target(db_path)

    if not hasattr(_local, 'connections'):
        _local.connections = {}

    conn = _local.connections.get(path)
    if conn is not None:
        try:
            conn.execute("SELECT 1")
            return conn
        except _STALE_CONNECTION_ERRORS:
            _local.connections.pop(path, None)

    if _is_pg_url(path):
        conn = PgConnection(path)
        _local.connections[path] = conn
        return conn

    conn = sqlite3.connect(path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.row_factory = sqlite3.Row
    _local.connections[path] = conn
    return conn


def close_connection(db_path: Path | str | None = None) -> None:
    """Close the cached connection for the current thread."""
    path = _resolve_target(db_path)
    if hasattr(_local, 'connections'):
        conn = _local.connections.pop(path, None)
        if conn is not None:
            conn.close()


def backend_name(conn: Connection) -> str:
    """'postgresql' or 'sqlite' (for `doctor`)."""
    return "postgresql" if isinstance(conn, PgConnection) else "sqlite"


def init_db(db_path: Path | str | None = None) -> Connection:
    """Create the full jobs table with all columns from every pipeline stage.

    This is idempotent -- safe to call on every startup. Uses CREATE TABLE IF NOT EXISTS
    so it won't destroy existing data.

    Schema columns by stage:
      - Discovery:  url, title, salary, description, location, site, strategy, discovered_at
      - Enrichment: full_description, application_url, detail_scraped_at, detail_error
      - Scoring:    fit_score, score_reasoning, scored_at
      - Tailoring:  tailored_resume_path, tailored_at, tailor_attempts
      - Cover:      cover_letter_path, cover_letter_at, cover_attempts
      - Apply:      applied_at, apply_status, apply_error, apply_attempts,
                   agent_id, last_attempted_at, apply_duration_ms, apply_task_id,
                   verification_confidence

    Args:
        db_path: Override the default DB_PATH.

    Returns:
        Connection with the schema initialized.
    """
    path = _resolve_target(db_path)

    # Ensure parent directory exists
    if not _is_pg_url(path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)

    conn = get_connection(path)
    _ensure_tables(conn)
    # Rows inserted without a user belong to the owner (callers not yet threading a user through).
    owner_id = _owner_id(conn)
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS jobs (
            user_id               TEXT NOT NULL DEFAULT '{owner_id}',
            -- Discovery stage (smart_extract / job_search)
            url                   TEXT,
            title                 TEXT,
            salary                TEXT,
            description           TEXT,
            location              TEXT,
            site                  TEXT,
            strategy              TEXT,
            discovered_at         TEXT,

            -- Enrichment stage (detail_scraper)
            full_description      TEXT,
            application_url       TEXT,
            detail_scraped_at     TEXT,
            detail_error          TEXT,

            -- Scoring stage (job_scorer)
            fit_score             INTEGER,
            score_reasoning       TEXT,
            scored_at             TEXT,
            score_attempts        INTEGER DEFAULT 0,

            -- Tailoring stage (resume tailor)
            tailored_resume_path  TEXT,
            tailored_at           TEXT,
            tailor_attempts       INTEGER DEFAULT 0,

            -- Cover letter stage
            cover_letter_path     TEXT,
            cover_letter_at       TEXT,
            cover_attempts        INTEGER DEFAULT 0,

            -- Application stage
            applied_at            TEXT,
            apply_status          TEXT,
            apply_error           TEXT,
            apply_attempts        INTEGER DEFAULT 0,
            agent_id              TEXT,
            last_attempted_at     TEXT,
            apply_duration_ms     INTEGER,
            apply_task_id         TEXT,
            verification_confidence TEXT,
            PRIMARY KEY (user_id, url)
        )
    """)
    conn.commit()

    # Run migrations for any columns added after initial schema
    ensure_columns(conn)
    migrate_users(conn)
    backfill_job_keys(conn)

    return conn


def _ensure_tables(conn: Connection) -> None:
    """Side tables and indexes (idempotent). Ids are uuid4 hex, which needs no SERIAL vs AUTOINCREMENT."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS status_events ("
        "id TEXT PRIMARY KEY, url TEXT, from_status TEXT, to_status TEXT, at TEXT, source TEXT)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_status_events_url ON status_events (url)")
    # Dashboard generation queue (strelitzia.web.tasks): kind resume|cover|both, state queued|running|done|error.
    conn.execute(
        "CREATE TABLE IF NOT EXISTS dashboard_tasks ("
        "id TEXT PRIMARY KEY, url TEXT, kind TEXT, state TEXT, created_at TEXT, started_at TEXT, "
        "finished_at TEXT, error TEXT)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_dashboard_tasks_url ON dashboard_tasks (url)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_dashboard_tasks_state ON dashboard_tasks (state)")
    # One row per Claude response (strelitzia.llm.record_usage); batch 0|1, ts ISO UTC. A NULL user_id is set to
    # the owner's on the next start (migrate_users).
    conn.execute(
        "CREATE TABLE IF NOT EXISTS llm_usage ("
        "id TEXT PRIMARY KEY, ts TEXT, purpose TEXT, model TEXT, batch INTEGER, input_tokens INTEGER, "
        "cache_read_tokens INTEGER, cache_write_tokens INTEGER, output_tokens INTEGER, cost_usd DOUBLE PRECISION, "
        "job_url TEXT NULL, user_id TEXT NULL)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_llm_usage_ts ON llm_usage (ts)")
    # Message Batches (strelitzia.llm_batch): status submitted -> collected (or lost, if the API no longer has it).
    # estimate_usd counts against the monthly budget until the batch is collected.
    conn.execute(
        "CREATE TABLE IF NOT EXISTS llm_batches ("
        "id TEXT PRIMARY KEY, purpose TEXT, submitted_at TEXT, status TEXT, request_count INTEGER, "
        "estimate_usd DOUBLE PRECISION)"
    )
    # Job preferences (strelitzia.scoring.preferences), one JSON document per user.
    conn.execute(
        "CREATE TABLE IF NOT EXISTS user_preferences (user_id TEXT NULL UNIQUE, prefs_json TEXT, updated_at TEXT)"
    )
    # Workspace (strelitzia.workspace): a user's contacts (shared across their jobs), and interviews per job.
    conn.execute(
        "CREATE TABLE IF NOT EXISTS contacts ("
        "id TEXT PRIMARY KEY, name TEXT NOT NULL, company TEXT, role TEXT, email TEXT, phone TEXT, "
        "linkedin_url TEXT, notes TEXT, created_at TEXT, updated_at TEXT)"
    )
    # relation: recruiter | hiring_manager | referral | interviewer | other
    conn.execute(
        "CREATE TABLE IF NOT EXISTS job_contacts ("
        "job_url TEXT, contact_id TEXT, relation TEXT, PRIMARY KEY (job_url, contact_id))"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_job_contacts_contact ON job_contacts (contact_id)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS interviews ("
        "id TEXT PRIMARY KEY, job_url TEXT, scheduled_at TEXT, stage TEXT, format TEXT, interviewers TEXT, "
        "prep_notes TEXT, debrief_notes TEXT, outcome TEXT, created_at TEXT, updated_at TEXT)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_interviews_job ON interviews (job_url, scheduled_at)")
    # Every accepted Claude workbench paste (strelitzia.scoring.workbench.import_result), addressable by
    # (job_url, version); the jobs row only keeps the latest. JSON columns: the resume dict, the cover letter, coverage.
    conn.execute(
        "CREATE TABLE IF NOT EXISTS drafts ("
        "id TEXT PRIMARY KEY, job_url TEXT, version INTEGER, rules_version TEXT, resume_json TEXT, "
        "resume_text TEXT, cover_json TEXT, coverage_json TEXT, created_at TEXT)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_drafts_job ON drafts (job_url, version)")
    # Resume panel (strelitzia.scoring.panel): blind A/B grading of two drafts per job. Draft "a" is the older
    # (or rules_version_a) one, "b" the newer; shown_first is which of them packet 1 shows first (packet 2 swaps).
    # status open | complete. winner (first | second | tie) is in packet 1's order for every grader.
    conn.execute(
        "CREATE TABLE IF NOT EXISTS panel_runs ("
        "id TEXT PRIMARY KEY, created_at TEXT, mode TEXT, rules_version_a TEXT, rules_version_b TEXT, "
        "status TEXT, packet_text TEXT, packet_text_swapped TEXT)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS panel_items ("
        "run_id TEXT, item_no INTEGER, job_url TEXT, draft_a_id TEXT, draft_b_id TEXT, shown_first TEXT, "
        "PRIMARY KEY (run_id, item_no))"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS panel_grades ("
        "run_id TEXT, item_no INTEGER, grader TEXT, winner TEXT, detail_json TEXT, created_at TEXT, "
        "PRIMARY KEY (run_id, item_no, grader))"
    )
    # Accounts (strelitzia.users): role owner | user, status invited | active | disabled, emails lowercased.
    conn.execute(
        "CREATE TABLE IF NOT EXISTS users ("
        "id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, name TEXT, role TEXT NOT NULL DEFAULT 'user', "
        "status TEXT NOT NULL DEFAULT 'invited', monthly_cap_usd DOUBLE PRECISION NULL, created_at TEXT, "
        "last_login_at TEXT)"
    )
    # Small app-wide key/value state, e.g. the hashes of the Claude Project Docs (strelitzia.scoring.workbench).
    conn.execute("CREATE TABLE IF NOT EXISTS app_state (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)")
    conn.commit()



# Tables migrate_users gives a user_id (with the owner as the default); user_preferences and llm_usage already have one.
USER_TABLES = ("status_events", "dashboard_tasks", "contacts", "job_contacts", "interviews", "drafts", "panel_runs")


def _owner_id(conn: Connection) -> str:
    owner_id = users.owner(conn).id
    if not owner_id.isalnum():  # it's inlined into DEFAULT clauses
        raise ValueError(f"unexpected owner id {owner_id!r}")
    return owner_id


def _jobs_pk(conn: Connection) -> list[str]:
    """The columns of the jobs primary key, in key order."""
    if isinstance(conn, PgConnection):
        rows = conn.execute(
            "SELECT k.column_name FROM information_schema.table_constraints c "
            "JOIN information_schema.key_column_usage k ON k.constraint_name = c.constraint_name "
            "AND k.table_schema = c.table_schema AND k.table_name = c.table_name "
            "WHERE c.table_schema = current_schema() AND c.table_name = 'jobs' AND c.constraint_type = 'PRIMARY KEY' "
            "ORDER BY k.ordinal_position"
        ).fetchall()
        return [r[0] for r in rows]
    cols = [r for r in conn.execute("PRAGMA table_info(jobs)").fetchall() if r[5]]
    return [r[1] for r in sorted(cols, key=lambda r: r[5])]


def _rebuild_sqlite_jobs(conn: sqlite3.Connection, owner_id: str) -> None:
    """Copy jobs into a table keyed on (user_id, url), keeping every column and row (SQLite can't alter a key)."""
    info = [r for r in conn.execute("PRAGMA table_info(jobs)").fetchall() if r[1] != "user_id"]
    defs = [f"user_id TEXT NOT NULL DEFAULT '{owner_id}'"]
    for _, name, ctype, notnull, default, _pk in info:
        col = f"{name} {ctype}".strip()
        if notnull:
            col += " NOT NULL"
        if default is not None:
            col += f" DEFAULT {default}"
        defs.append(col)
    names = ", ".join(r[1] for r in info)
    has_user = "user_id" in table_columns(conn)
    user_expr = f"COALESCE(user_id, '{owner_id}')" if has_user else f"'{owner_id}'"
    conn.commit()
    conn.execute("BEGIN")
    try:
        conn.execute(f"CREATE TABLE jobs_new ({', '.join(defs)}, PRIMARY KEY (user_id, url))")
        conn.execute(f"INSERT INTO jobs_new (user_id, {names}) SELECT {user_expr}, {names} FROM jobs")
        conn.execute("DROP TABLE jobs")
        conn.execute("ALTER TABLE jobs_new RENAME TO jobs")
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _rekey_pg_jobs(conn: PgConnection, owner_id: str) -> None:
    conn.execute("BEGIN")
    try:
        if "user_id" not in table_columns(conn):
            conn.execute("ALTER TABLE jobs ADD COLUMN user_id TEXT")
        conn.execute("UPDATE jobs SET user_id = ? WHERE user_id IS NULL", (owner_id,))
        conn.execute(f"ALTER TABLE jobs ALTER COLUMN user_id SET DEFAULT '{owner_id}', ALTER COLUMN user_id SET NOT NULL")
        pkey = conn.execute(
            "SELECT constraint_name FROM information_schema.table_constraints "
            "WHERE table_schema = current_schema() AND table_name = 'jobs' AND constraint_type = 'PRIMARY KEY'"
        ).fetchone()
        if pkey:
            conn.execute(f'ALTER TABLE jobs DROP CONSTRAINT "{pkey[0]}"')
        conn.execute("ALTER TABLE jobs ADD PRIMARY KEY (user_id, url)")
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def migrate_users(conn: Connection) -> None:
    """Make every per-user table carry user_id, owned by the owner where unset, and key jobs on (user_id, url).

    Idempotent: once jobs has the composite key it only fills NULL user_ids (rows inserted without one) and makes
    sure the indexes exist. Never drops data.
    """
    owner_id = _owner_id(conn)
    for table in USER_TABLES:
        if "user_id" not in table_columns(conn, table):
            # The default fills existing rows and any insert that doesn't name a user yet.
            conn.execute(f"ALTER TABLE {table} ADD COLUMN user_id TEXT DEFAULT '{owner_id}'")
    conn.commit()

    if _jobs_pk(conn) != ["user_id", "url"]:
        if isinstance(conn, PgConnection):
            _rekey_pg_jobs(conn, owner_id)
        else:
            _rebuild_sqlite_jobs(conn, owner_id)

    # Preferences were one row with a NULL user_id (racing first saves could leave several; the newest won).
    if not conn.execute("SELECT 1 FROM user_preferences WHERE user_id = ?", (owner_id,)).fetchone():
        newest = conn.execute("SELECT prefs_json, updated_at FROM user_preferences WHERE user_id IS NULL "
                              "ORDER BY updated_at DESC LIMIT 1").fetchone()
        if newest:
            conn.execute("INSERT INTO user_preferences (user_id, prefs_json, updated_at) VALUES (?, ?, ?)",
                         (owner_id, newest[0], newest[1]))
    for table in ("jobs", "llm_usage", *USER_TABLES):
        conn.execute(f"UPDATE {table} SET user_id = ? WHERE user_id IS NULL", (owner_id,))

    conn.execute("DROP INDEX IF EXISTS idx_jobs_job_key")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_user_job_key ON jobs (user_id, job_key)")
    for table in ("llm_usage", *USER_TABLES):
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_user ON {table} (user_id)")
    conn.commit()

def get_state(conn: Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM app_state WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def set_state(conn: Connection, key: str, value: str) -> None:
    """Upsert app_state[key] (the caller commits)."""
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO app_state (key, value, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        (key, value, now),
    )


def job_key(url: str) -> str:
    """Stable per-job ID (12 hex chars of sha1(url)): dashboard URLs and the Drive appProperties key."""
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]


def backfill_job_keys(conn: Connection | None = None) -> int:
    """Set job_key on rows that lack it (rows inserted by discovery paths that don't set it). Returns the count."""
    if conn is None:
        conn = get_connection()
    urls = [r[0] for r in conn.execute("SELECT url FROM jobs WHERE job_key IS NULL").fetchall()]
    for url in urls:
        conn.execute("UPDATE jobs SET job_key = ? WHERE url = ?", (job_key(url), url))
    if urls:
        conn.commit()
    return len(urls)


# Complete column registry: column_name -> SQL type with optional default.
# This is the single source of truth. Adding a column here is all that's needed
# for it to appear in both new databases and migrated ones.
# Jobs a scoring run should pick up: extracted, never assessed, and not given up on after repeated LLM errors.
# Jobs whose assessment is stale (the resume or the assessment inputs changed) are only re-assessed on request
# (`run_scoring(reassess=True)`), since that costs money.
MAX_SCORE_ATTEMPTS = 3
PENDING_SCORE_WHERE = (
    "full_description IS NOT NULL AND details_json IS NOT NULL AND assessment_hash IS NULL "
    f"AND COALESCE(score_attempts, 0) < {MAX_SCORE_ATTEMPTS}"
)
# assessment_hash of jobs scored by the old 1-10 scorer (never equal to a current key, so they show as stale).
LEGACY_ASSESSMENT_HASH = "0:legacy"
# Jobs flagged by a dealbreaker or the title prefilter (fit_flags is NULL when there are none).
UNFLAGGED_WHERE = "fit_flags IS NULL"
# Default fit_score (0-100) a job needs before resumes and cover letters are generated for it.
DEFAULT_MIN_SCORE = 70

# Jobs an extract run should pick up: described, not yet extracted at the current EXTRACT_VERSION,
# and not given up on after repeated failures.
MAX_EXTRACT_ATTEMPTS = 3
PENDING_EXTRACT_WHERE = (
    f"full_description IS NOT NULL AND (extracted_at IS NULL OR extract_version < {EXTRACT_VERSION}) "
    f"AND COALESCE(extract_attempts, 0) < {MAX_EXTRACT_ATTEMPTS}"
)

_ALL_COLUMNS: dict[str, str] = {
    # Discovery (the key is (user_id, url); migrate_users adds user_id to old tables)
    "url": "TEXT PRIMARY KEY",
    "job_key": "TEXT",  # job_key(url): short stable id for dashboard URLs and Drive appProperties
    "title": "TEXT",
    "salary": "TEXT",
    "description": "TEXT",
    "location": "TEXT",
    "site": "TEXT",
    "strategy": "TEXT",
    "discovered_at": "TEXT",
    "company": "TEXT",
    # Enrichment
    "full_description": "TEXT",
    "application_url": "TEXT",
    "detail_scraped_at": "TEXT",
    "detail_error": "TEXT",
    # Extraction (enrichment/extract.py; heuristic fallback in enrichment/classify.py)
    "details_json": "TEXT",
    "role_category": "TEXT",
    "seniority": "TEXT",
    "employment_type": "TEXT",
    "work_mode": "TEXT",
    "location_city": "TEXT",
    "location_state": "TEXT",
    "location_country": "TEXT",
    "salary_min": "REAL",
    "salary_max": "REAL",
    "salary_currency": "TEXT",
    "salary_period": "TEXT",
    "posted_date": "TEXT",
    "deadline": "TEXT",
    "extracted_at": "TEXT",
    "extract_attempts": "INTEGER DEFAULT 0",
    "extract_error": "TEXT",
    "extract_version": "INTEGER",
    "category_source": "TEXT",  # 'llm' or 'heuristic'
    # Scoring (scoring/assessment.py): fit_score 0-100 from the preferences and the Claude assessment
    "fit_score": "INTEGER",
    "score_reasoning": "TEXT",
    "scored_at": "TEXT",
    "score_attempts": "INTEGER DEFAULT 0",
    "fit_assessment": "TEXT",  # JSON: the rubric's ratings, red flags and summary
    "assessment_hash": "TEXT",  # "<ASSESS_VERSION>:<sha256 of the assessment inputs>" it was made with
    "assessed_at": "TEXT",
    "fit_breakdown": "TEXT",  # JSON list of the scored components
    "fit_flags": "TEXT",  # JSON list of failed dealbreakers (and "prefilter"); NULL when none
    # Tailoring
    "tailored_resume_path": "TEXT",
    "tailored_at": "TEXT",
    "tailor_attempts": "INTEGER DEFAULT 0",
    # Cover letter
    "cover_letter_path": "TEXT",
    "cover_letter_at": "TEXT",
    "cover_attempts": "INTEGER DEFAULT 0",
    # Claude workbench (strelitzia.scoring.workbench): one accepted paste = one draft version
    "draft_version": "INTEGER",
    "draft_rules_version": "TEXT",
    "tailor_report": "TEXT",  # JSON: coverage, unmet, warnings
    "resume_docx_path": "TEXT",
    "submitted_draft_version": "INTEGER",  # copied from draft_version when the job is marked Submitted
    "submitted_rules_version": "TEXT",
    "packet_doc_id": "TEXT",
    "packet_doc_url": "TEXT",
    # Google Drive (the PDFs are moved there; see strelitzia.storage)
    "resume_drive_id": "TEXT",
    "resume_drive_url": "TEXT",
    "cover_letter_drive_id": "TEXT",
    "cover_letter_drive_url": "TEXT",
    "drive_synced_at": "TEXT",
    "drive_folder_id": "TEXT",  # the job's leaf folder (company/role/date), from sync_job
    "drive_folder_url": "TEXT",
    # Application
    "applied_at": "TEXT",
    "apply_status": "TEXT",
    "apply_error": "TEXT",
    "apply_attempts": "INTEGER DEFAULT 0",
    "agent_id": "TEXT",
    "last_attempted_at": "TEXT",
    "apply_duration_ms": "INTEGER",
    "apply_task_id": "TEXT",
    "verification_confidence": "TEXT",
    # Dashboard status tracking (strelitzia.tracking)
    "user_status": "TEXT",  # NULL | in_progress | submitted | rejected | heard_back
    "status_updated_at": "TEXT",
    "submitted_at": "TEXT",
    "responded_at": "TEXT",
    "notes": "TEXT",
    # Workspace intake (strelitzia.intake): jobs the user added by link or paste
    "source": "TEXT",  # 'manual' | 'discovered'; NULL = discovered (rows from before the column)
    "added_at": "TEXT",
    "intake_status": "TEXT",  # queued | scraping | ok | needs_paste | error
    "intake_error": "TEXT",
}


def table_columns(conn: Connection, table: str = "jobs") -> set[str]:
    """Column names of a table: information_schema on Postgres, PRAGMA table_info on SQLite."""
    if isinstance(conn, PgConnection):
        rows = conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = ?",
            (table,),
        ).fetchall()
        return {row[0] for row in rows}
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def ensure_columns(conn: Connection | None = None) -> list[str]:
    """Add any missing columns to the jobs table (forward migration).

    Reads the current table schema via PRAGMA table_info and compares against
    the full column registry. Any missing columns are added with ALTER TABLE.

    This makes it safe to upgrade the database from any previous version --
    columns are only added, never removed or renamed.

    Args:
        conn: Database connection. Uses get_connection() if None.

    Returns:
        List of column names that were added (empty if schema was already current).
    """
    if conn is None:
        conn = get_connection()

    existing = table_columns(conn)
    added = []

    for col, dtype in _ALL_COLUMNS.items():
        if col not in existing:
            # PRIMARY KEY columns can't be added via ALTER TABLE, but url
            # is always created with the table itself so this is safe
            if "PRIMARY KEY" in dtype:
                continue
            conn.execute(f"ALTER TABLE jobs ADD COLUMN {col} {dtype}")
            added.append(col)

    if "company" in added:
        # Workday and ATS-board rows already carry the employer in `site`.
        conn.execute(
            "UPDATE jobs SET company = site WHERE company IS NULL "
            "AND (strategy = 'workday_api' OR strategy LIKE 'ats_%')"
        )

    if "fit_assessment" in added:
        # fit_score went from 1-10 to 0-100: keep the old order until the jobs are re-assessed.
        conn.execute("UPDATE jobs SET fit_score = fit_score * 10 WHERE fit_score IS NOT NULL")
        # They count as stale, not pending: re-assessing them costs money, so it waits for the user's confirmation
        # on the Preferences page.
        conn.execute(f"UPDATE jobs SET assessment_hash = '{LEGACY_ASSESSMENT_HASH}' WHERE fit_score IS NOT NULL")
        conn.execute("UPDATE jobs SET fit_score = 0, fit_flags = '[\"prefilter\"]' "
                     "WHERE score_reasoning = 'prefilter: title not relevant'")

    if added:
        conn.commit()

    return added


# Values of `site` that name a job board rather than an employer.
JOB_BOARD_SITES = {"indeed", "linkedin", "glassdoor", "zip_recruiter", "ziprecruiter", "google"}


def job_company(job: dict) -> str:
    """The employer for a job row: `company`, else `site` unless it is a job-board name."""
    company = (job.get("company") or "").strip()
    if company:
        return company
    site = (job.get("site") or "").strip()
    # A job added by link has the posting's hostname as `site`, which isn't an employer.
    if site and site.lower() not in JOB_BOARD_SITES and job.get("strategy") != "manual":
        return site
    return "Unknown company"


def get_stats(conn: Connection | None = None, user_id: str | None = None) -> dict:
    """Return job counts by pipeline stage.

    Provides a snapshot of how many jobs are at each stage, useful for
    dashboard display and pipeline progress tracking.

    Args:
        conn: Database connection. Uses get_connection() if None.
        user_id: Count only this user's jobs; None = every user.

    Returns:
        Dictionary with keys:
            total, by_site, pending_detail, with_description,
            scored, unscored, tailored, untailored_eligible,
            with_cover_letter, applied, score_distribution
    """
    if conn is None:
        conn = get_connection()

    stats: dict = {}
    mine, params = ("user_id = ?", (user_id,)) if user_id else ("1=1", ())

    def count(where: str = "1=1") -> int:
        return conn.execute(f"SELECT COUNT(*) FROM jobs WHERE ({where}) AND {mine}", params).fetchone()[0]

    stats["total"] = count()

    # By site breakdown
    rows = conn.execute(
        f"SELECT site, COUNT(*) as cnt FROM jobs WHERE {mine} GROUP BY site ORDER BY cnt DESC", params
    ).fetchall()
    stats["by_site"] = [(row[0], row[1]) for row in rows]

    # Enrichment stage
    stats["pending_detail"] = count("detail_scraped_at IS NULL")
    stats["with_description"] = count("full_description IS NOT NULL")
    stats["detail_errors"] = count("detail_error IS NOT NULL")

    # Scoring stage
    stats["scored"] = count("fit_score IS NOT NULL")
    stats["unscored"] = count(PENDING_SCORE_WHERE)

    # Score distribution
    dist_rows = conn.execute(  # fit_score is 0-100: bands of ten (70 = 70-79; 100 is its own band)
        "SELECT (fit_score / 10) * 10 AS band, COUNT(*) as cnt FROM jobs "
        f"WHERE fit_score IS NOT NULL AND {mine} "
        "GROUP BY band ORDER BY band DESC",
        params,
    ).fetchall()
    stats["score_distribution"] = [(row[0], row[1]) for row in dist_rows]

    # Tailoring stage
    stats["tailored"] = count("tailored_resume_path IS NOT NULL")
    stats["untailored_eligible"] = count(
        f"fit_score >= {DEFAULT_MIN_SCORE} AND {UNFLAGGED_WHERE} AND full_description IS NOT NULL "
        "AND tailored_resume_path IS NULL"
    )
    stats["tailor_exhausted"] = count("COALESCE(tailor_attempts, 0) >= 5 AND tailored_resume_path IS NULL")

    # Cover letter stage
    stats["with_cover_letter"] = count("cover_letter_path IS NOT NULL")
    stats["cover_exhausted"] = count(
        "COALESCE(cover_attempts, 0) >= 5 AND (cover_letter_path IS NULL OR cover_letter_path = '')"
    )

    # Applications: tracked in the dashboard (user_status). applied_at is only set on jobs the removed auto-apply stage sent.
    applied = "(COALESCE(user_status, '') IN ('submitted', 'rejected', 'heard_back') OR applied_at IS NOT NULL)"
    stats["applied"] = count(applied)
    stats["apply_errors"] = count("apply_error IS NOT NULL")
    stats["ready_to_apply"] = count(
        f"tailored_resume_path IS NOT NULL AND NOT {applied} AND application_url IS NOT NULL"
    )

    return stats


def store_jobs(conn: Connection, jobs: list[dict],
               site: str, strategy: str, user_id: str | None = None) -> tuple[int, int]:
    """Store discovered jobs for a user, skipping urls the user already has.

    Args:
        conn: Database connection.
        jobs: List of job dicts with keys: url, title, salary, description, location.
        site: Source site name (e.g. "RemoteOK", "Dice").
        strategy: Extraction strategy used (e.g. "json_ld", "api_response", "css_selectors").
        user_id: Whose jobs these are; None = the owner's (until every caller passes a user).

    Returns:
        Tuple of (new_count, duplicate_count).
    """
    user_id = user_id or users.owner(conn).id
    now = datetime.now(timezone.utc).isoformat()
    new = 0
    existing = 0

    for job in jobs:
        url = job.get("url")
        if not url:
            continue
        cur = conn.execute(
            "INSERT INTO jobs (user_id, url, title, salary, description, location, site, strategy, discovered_at, "
            "job_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (user_id, url) DO NOTHING",
            (user_id, url, job.get("title"), job.get("salary"), job.get("description"),
             job.get("location"), site, strategy, now, job_key(url)),
        )
        if cur.rowcount == 1:
            new += 1
        else:
            existing += 1

    conn.commit()
    return new, existing


def get_jobs_by_stage(conn: Connection | None = None,
                      stage: str = "discovered",
                      min_score: int | None = None,
                      limit: int = 100,
                      user_id: str | None = None) -> list[dict]:
    """Fetch jobs filtered by pipeline stage.

    Args:
        conn: Database connection. Uses get_connection() if None.
        stage: One of "discovered", "enriched", "scored", "tailored", "applied".
        min_score: Minimum fit_score filter (only relevant for scored+ stages).
        limit: Maximum number of rows to return.
        user_id: Only this user's jobs; None = every user's.

    Returns:
        List of job dicts.
    """
    if conn is None:
        conn = get_connection()

    conditions = {
        "discovered": "1=1",
        "pending_detail": "detail_scraped_at IS NULL",
        "enriched": "full_description IS NOT NULL",
        "pending_extract": PENDING_EXTRACT_WHERE,
        "pending_score": PENDING_SCORE_WHERE,
        "scored": "fit_score IS NOT NULL",
        "pending_tailor": (
            f"fit_score >= ? AND {UNFLAGGED_WHERE} AND full_description IS NOT NULL "
            "AND tailored_resume_path IS NULL AND COALESCE(tailor_attempts, 0) < 5"
        ),
        "tailored": "tailored_resume_path IS NOT NULL",
        "pending_apply": (
            "tailored_resume_path IS NOT NULL AND applied_at IS NULL "
            "AND application_url IS NOT NULL"
        ),
        "applied": "applied_at IS NOT NULL",
    }

    where = conditions.get(stage, "1=1")
    params: list = []

    if "?" in where and min_score is not None:
        params.append(min_score)
    elif "?" in where:
        params.append(DEFAULT_MIN_SCORE)

    if min_score is not None and "fit_score" not in where and stage in ("scored", "tailored", "applied"):
        where += " AND fit_score >= ?"
        params.append(min_score)

    if user_id:
        where = f"({where}) AND user_id = ?"
        params.append(user_id)

    query = f"SELECT * FROM jobs WHERE {where} ORDER BY fit_score DESC NULLS LAST, discovered_at DESC"
    if limit > 0:
        query += " LIMIT ?"
        params.append(limit)

    rows = conn.execute(query, params).fetchall()

    # Convert sqlite3.Row objects to dicts
    if rows:
        columns = rows[0].keys()
        return [dict(zip(columns, row)) for row in rows]
    return []


def get_jobs_by_urls(conn: Connection | None, urls: list[str], user_id: str | None = None) -> list[dict]:
    """These jobs (those with a description), in the order given: a single-job tailor or cover run from the dashboard.

    user_id: only this user's rows; None = any user's (one row per url, whichever comes first).
    """
    if conn is None:
        conn = get_connection()
    if not urls:
        return []
    mine, params = (" AND user_id = ?", [user_id]) if user_id else ("", [])
    rows = conn.execute(
        f"SELECT * FROM jobs WHERE url IN ({', '.join('?' * len(urls))}) AND full_description IS NOT NULL{mine}",
        [*urls, *params],
    ).fetchall()
    by_url: dict[str, dict] = {}
    for row in rows:
        by_url.setdefault(row["url"], dict(row))
    return [by_url[u] for u in dict.fromkeys(urls) if u in by_url]


def reset_score_errors(conn: Connection | None = None) -> int:
    """Make jobs whose scoring failed with an LLM error pending again. Returns the number of rows reset.

    Covers the old behavior (errors saved as fit_score = 0) and the new one (fit_score NULL + score_attempts).
    """
    if conn is None:
        conn = get_connection()
    cur = conn.execute(
        "UPDATE jobs SET fit_score = NULL, score_reasoning = NULL, scored_at = NULL, score_attempts = 0 "
        # Errors saved before 2026-10-02 are stored as "<empty keywords>\nLLM error: ...".
        "WHERE LTRIM(score_reasoning, ?) LIKE 'LLM error%'",
        ("\n",),
    )
    conn.commit()
    return cur.rowcount
