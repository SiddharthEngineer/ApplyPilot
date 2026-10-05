"""ApplyPilot database layer: schema, migrations, stats, and connection helpers.

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

from applypilot import config
from applypilot.config import DB_PATH
from applypilot.db_pg import PgConnection
from applypilot.enrichment.posting_model import EXTRACT_VERSION

# Either backend: sqlite3 (default) or Postgres (APPLYPILOT_DATABASE_URL), which exposes the same API.
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
    """The database to open: an explicit path or URL, else APPLYPILOT_DATABASE_URL, else DB_PATH.

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

    Postgres when the target is a `postgresql://` URL (by default APPLYPILOT_DATABASE_URL), otherwise a SQLite
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
    conn.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            -- Discovery stage (smart_extract / job_search)
            url                   TEXT PRIMARY KEY,
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
            verification_confidence TEXT
        )
    """)
    conn.commit()

    # Run migrations for any columns added after initial schema
    ensure_columns(conn)
    _ensure_tables(conn)
    backfill_job_keys(conn)

    return conn


def _ensure_tables(conn: Connection) -> None:
    """Side tables and indexes (idempotent). Ids are uuid4 hex, which needs no SERIAL vs AUTOINCREMENT."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS status_events ("
        "id TEXT PRIMARY KEY, url TEXT, from_status TEXT, to_status TEXT, at TEXT, source TEXT)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_status_events_url ON status_events (url)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_job_key ON jobs (job_key)")
    conn.commit()


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
# Jobs a scoring run should pick up: described, unscored, and not given up on after repeated LLM errors.
MAX_SCORE_ATTEMPTS = 3
PENDING_SCORE_WHERE = (
    f"full_description IS NOT NULL AND fit_score IS NULL AND COALESCE(score_attempts, 0) < {MAX_SCORE_ATTEMPTS}"
)

# Jobs an extract run should pick up: described, not yet extracted at the current EXTRACT_VERSION,
# and not given up on after repeated failures.
MAX_EXTRACT_ATTEMPTS = 3
PENDING_EXTRACT_WHERE = (
    f"full_description IS NOT NULL AND (extracted_at IS NULL OR extract_version < {EXTRACT_VERSION}) "
    f"AND COALESCE(extract_attempts, 0) < {MAX_EXTRACT_ATTEMPTS}"
)

_ALL_COLUMNS: dict[str, str] = {
    # Discovery
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
    # Scoring
    "fit_score": "INTEGER",
    "score_reasoning": "TEXT",
    "scored_at": "TEXT",
    "score_attempts": "INTEGER DEFAULT 0",
    # Tailoring
    "tailored_resume_path": "TEXT",
    "tailored_at": "TEXT",
    "tailor_attempts": "INTEGER DEFAULT 0",
    # Cover letter
    "cover_letter_path": "TEXT",
    "cover_letter_at": "TEXT",
    "cover_attempts": "INTEGER DEFAULT 0",
    # Google Drive (the PDFs are moved there; see applypilot.storage)
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
    # Dashboard status tracking (applypilot.tracking)
    "user_status": "TEXT",  # NULL | in_progress | submitted | rejected | heard_back
    "status_updated_at": "TEXT",
    "submitted_at": "TEXT",
    "responded_at": "TEXT",
    "notes": "TEXT",
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
    if site and site.lower() not in JOB_BOARD_SITES:
        return site
    return "Unknown company"


def get_stats(conn: Connection | None = None) -> dict:
    """Return job counts by pipeline stage.

    Provides a snapshot of how many jobs are at each stage, useful for
    dashboard display and pipeline progress tracking.

    Args:
        conn: Database connection. Uses get_connection() if None.

    Returns:
        Dictionary with keys:
            total, by_site, pending_detail, with_description,
            scored, unscored, tailored, untailored_eligible,
            with_cover_letter, applied, score_distribution
    """
    if conn is None:
        conn = get_connection()

    stats: dict = {}

    # Total jobs
    stats["total"] = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]

    # By site breakdown
    rows = conn.execute(
        "SELECT site, COUNT(*) as cnt FROM jobs GROUP BY site ORDER BY cnt DESC"
    ).fetchall()
    stats["by_site"] = [(row[0], row[1]) for row in rows]

    # Enrichment stage
    stats["pending_detail"] = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE detail_scraped_at IS NULL"
    ).fetchone()[0]

    stats["with_description"] = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE full_description IS NOT NULL"
    ).fetchone()[0]

    stats["detail_errors"] = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE detail_error IS NOT NULL"
    ).fetchone()[0]

    # Scoring stage
    stats["scored"] = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE fit_score IS NOT NULL"
    ).fetchone()[0]

    stats["unscored"] = conn.execute(
        "SELECT COUNT(*) FROM jobs "
        f"WHERE {PENDING_SCORE_WHERE}"
    ).fetchone()[0]

    # Score distribution
    dist_rows = conn.execute(
        "SELECT fit_score, COUNT(*) as cnt FROM jobs "
        "WHERE fit_score IS NOT NULL "
        "GROUP BY fit_score ORDER BY fit_score DESC"
    ).fetchall()
    stats["score_distribution"] = [(row[0], row[1]) for row in dist_rows]

    # Tailoring stage
    stats["tailored"] = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE tailored_resume_path IS NOT NULL"
    ).fetchone()[0]

    stats["untailored_eligible"] = conn.execute(
        "SELECT COUNT(*) FROM jobs "
        "WHERE fit_score >= 7 AND full_description IS NOT NULL "
        "AND tailored_resume_path IS NULL"
    ).fetchone()[0]

    stats["tailor_exhausted"] = conn.execute(
        "SELECT COUNT(*) FROM jobs "
        "WHERE COALESCE(tailor_attempts, 0) >= 5 "
        "AND tailored_resume_path IS NULL"
    ).fetchone()[0]

    # Cover letter stage
    stats["with_cover_letter"] = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE cover_letter_path IS NOT NULL"
    ).fetchone()[0]

    stats["cover_exhausted"] = conn.execute(
        "SELECT COUNT(*) FROM jobs "
        "WHERE COALESCE(cover_attempts, 0) >= 5 "
        "AND (cover_letter_path IS NULL OR cover_letter_path = '')"
    ).fetchone()[0]

    # Application stage
    stats["applied"] = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE applied_at IS NOT NULL"
    ).fetchone()[0]

    stats["apply_errors"] = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE apply_error IS NOT NULL"
    ).fetchone()[0]

    stats["ready_to_apply"] = conn.execute(
        "SELECT COUNT(*) FROM jobs "
        "WHERE tailored_resume_path IS NOT NULL "
        "AND applied_at IS NULL "
        "AND application_url IS NOT NULL"
    ).fetchone()[0]

    return stats


def store_jobs(conn: Connection, jobs: list[dict],
               site: str, strategy: str) -> tuple[int, int]:
    """Store discovered jobs, skipping duplicates by URL.

    Args:
        conn: Database connection.
        jobs: List of job dicts with keys: url, title, salary, description, location.
        site: Source site name (e.g. "RemoteOK", "Dice").
        strategy: Extraction strategy used (e.g. "json_ld", "api_response", "css_selectors").

    Returns:
        Tuple of (new_count, duplicate_count).
    """
    now = datetime.now(timezone.utc).isoformat()
    new = 0
    existing = 0

    for job in jobs:
        url = job.get("url")
        if not url:
            continue
        try:
            conn.execute(
                "INSERT INTO jobs (url, title, salary, description, location, site, strategy, discovered_at, job_key) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (url, job.get("title"), job.get("salary"), job.get("description"),
                 job.get("location"), site, strategy, now, job_key(url)),
            )
            new += 1
        except IntegrityError:
            existing += 1

    conn.commit()
    return new, existing


def get_jobs_by_stage(conn: Connection | None = None,
                      stage: str = "discovered",
                      min_score: int | None = None,
                      limit: int = 100) -> list[dict]:
    """Fetch jobs filtered by pipeline stage.

    Args:
        conn: Database connection. Uses get_connection() if None.
        stage: One of "discovered", "enriched", "scored", "tailored", "applied".
        min_score: Minimum fit_score filter (only relevant for scored+ stages).
        limit: Maximum number of rows to return.

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
            "fit_score >= ? AND full_description IS NOT NULL "
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
        params.append(7)  # default min_score

    if min_score is not None and "fit_score" not in where and stage in ("scored", "tailored", "applied"):
        where += " AND fit_score >= ?"
        params.append(min_score)

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


def get_jobs_by_urls(conn: Connection | None, urls: list[str]) -> list[dict]:
    """These jobs (those with a description), in the order given: a single-job tailor or cover run from the dashboard."""
    if conn is None:
        conn = get_connection()
    if not urls:
        return []
    rows = conn.execute(
        f"SELECT * FROM jobs WHERE url IN ({', '.join('?' * len(urls))}) AND full_description IS NOT NULL",
        list(urls),
    ).fetchall()
    by_url = {row["url"]: dict(row) for row in rows}
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
