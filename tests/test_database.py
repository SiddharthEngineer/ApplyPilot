"""Tests for the jobs schema: company column, Drive link columns, job_company()."""

import sqlite3

import pandas as pd

from applypilot.database import ensure_columns, init_db, job_company
from applypilot.discovery.jobspy import store_jobspy_results

DRIVE_COLUMNS = {
    "company", "resume_drive_id", "resume_drive_url",
    "cover_letter_drive_id", "cover_letter_drive_url", "drive_synced_at",
}


def _columns(conn) -> set[str]:
    return {r[1] for r in conn.execute("PRAGMA table_info(jobs)").fetchall()}


def test_fresh_db_has_company_column(tmp_path):
    conn = init_db(tmp_path / "fresh.db")
    assert "company" in _columns(conn)


def test_migration_adds_company_and_backfills_employer_rows(tmp_path):
    conn = sqlite3.connect(tmp_path / "old.db")
    conn.execute("CREATE TABLE jobs (url TEXT PRIMARY KEY, title TEXT, site TEXT, strategy TEXT)")
    conn.executemany("INSERT INTO jobs VALUES (?, ?, ?, ?)", [
        ("u1", "Engineer", "NVIDIA", "workday_api"),
        ("u2", "Engineer", "Acme", "ats_greenhouse"),
        ("u3", "Engineer", "indeed", "jobspy"),
    ])
    conn.commit()

    added = ensure_columns(conn)

    assert "company" in added
    rows = dict(conn.execute("SELECT url, company FROM jobs").fetchall())
    assert rows == {"u1": "NVIDIA", "u2": "Acme", "u3": None}


def test_jobspy_stores_company(tmp_path):
    conn = init_db(tmp_path / "jobs.db")
    df = pd.DataFrame([{
        "job_url": "https://example.com/job/1", "title": "Data Engineer", "company": "Acme",
        "location": "Austin, TX", "site": "indeed", "description": "x",
    }])
    store_jobspy_results(conn, df, "test")
    site, company = conn.execute("SELECT site, company FROM jobs").fetchone()
    assert (site, company) == ("indeed", "Acme")


def test_job_company():
    assert job_company({"site": "indeed", "company": None}) == "Unknown company"
    assert job_company({"site": "LinkedIn", "company": ""}) == "Unknown company"
    assert job_company({"site": "NVIDIA"}) == "NVIDIA"
    assert job_company({"site": "indeed", "company": "Acme"}) == "Acme"
    assert job_company({}) == "Unknown company"


def test_drive_columns_on_fresh_and_migrated_db(tmp_path):
    assert DRIVE_COLUMNS <= _columns(init_db(tmp_path / "fresh.db"))
    conn = sqlite3.connect(tmp_path / "old.db")
    conn.execute("CREATE TABLE jobs (url TEXT PRIMARY KEY, title TEXT, site TEXT, strategy TEXT)")
    ensure_columns(conn)
    assert DRIVE_COLUMNS <= _columns(conn)


class TestBackendDispatch:
    """get_connection() picks Postgres for postgresql:// URLs and SQLite otherwise."""

    class _FakePg:
        def __init__(self, url):
            self.url = url

        def execute(self, sql, params=()):
            return self

    def _patch_pg(self, monkeypatch):
        from applypilot import database

        monkeypatch.setattr(database, "PgConnection", self._FakePg)
        return database

    def test_env_url_selects_postgres(self, monkeypatch):
        database = self._patch_pg(monkeypatch)
        url = "postgresql://applypilot:pw@127.0.0.1:5432/applypilot"
        monkeypatch.setenv("APPLYPILOT_DATABASE_URL", url)
        conn = database.get_connection()
        try:
            assert isinstance(conn, self._FakePg) and conn.url == url
            assert database.get_connection() is conn  # cached per thread
        finally:
            database._local.connections.pop(url, None)

    def test_explicit_path_wins_over_env(self, monkeypatch, tmp_path):
        database = self._patch_pg(monkeypatch)
        monkeypatch.setenv("APPLYPILOT_DATABASE_URL", "postgresql://u:p@127.0.0.1/db")
        conn = database.get_connection(tmp_path / "x.db")
        assert isinstance(conn, sqlite3.Connection)
        assert database.backend_name(conn) == "sqlite"

    def test_unset_env_uses_sqlite_db_path(self, monkeypatch):
        from applypilot import database

        monkeypatch.delenv("APPLYPILOT_DATABASE_URL", raising=False)
        assert database._resolve_target(None) == str(database.DB_PATH)

    def test_sqlite_url(self, tmp_path):
        from applypilot import database

        assert database._resolve_target(f"sqlite:///{tmp_path}/a.db") == f"{tmp_path}/a.db"
        conn = init_db(f"sqlite:///{tmp_path}/a.db")
        assert "company" in _columns(conn)
        assert (tmp_path / "a.db").exists()

    def test_backend_name_postgres(self):
        from applypilot.database import backend_name
        from applypilot.db_pg import PgConnection

        pg = PgConnection.__new__(PgConnection)
        assert backend_name(pg) == "postgresql"

    def test_table_columns_sqlite(self, tmp_path):
        from applypilot.database import table_columns

        conn = init_db(tmp_path / "t.db")
        assert {"url", "fit_score", "company"} <= table_columns(conn)


EXTRACT_COLUMNS = {
    "details_json", "role_category", "seniority", "employment_type", "work_mode",
    "location_city", "location_state", "location_country", "salary_min", "salary_max",
    "salary_currency", "salary_period", "posted_date", "deadline", "extracted_at",
    "extract_attempts", "extract_error", "extract_version", "category_source",
}


def test_extract_columns_on_fresh_and_migrated_db(tmp_path):
    assert EXTRACT_COLUMNS <= _columns(init_db(tmp_path / "fresh.db"))
    conn = sqlite3.connect(tmp_path / "old.db")
    conn.execute("CREATE TABLE jobs (url TEXT PRIMARY KEY, title TEXT, site TEXT, strategy TEXT)")
    added = ensure_columns(conn)
    assert len(EXTRACT_COLUMNS) == 19
    assert EXTRACT_COLUMNS <= set(added)


def test_pending_extract_selection(tmp_path):
    from applypilot.database import get_jobs_by_stage
    from applypilot.enrichment.posting_model import EXTRACT_VERSION

    conn = init_db(tmp_path / "jobs.db")
    rows = [
        ("no-desc", None, None, None, 0),
        ("pending", "d", None, None, 0),
        ("done", "d", "2026-10-05", EXTRACT_VERSION, 0),
        ("stale", "d", "2026-10-05", EXTRACT_VERSION - 1, 0),
        ("gave-up", "d", None, None, 3),
        ("retry", "d", None, None, 2),
    ]
    conn.executemany(
        "INSERT INTO jobs (url, full_description, extracted_at, extract_version, extract_attempts) "
        "VALUES (?, ?, ?, ?, ?)", rows,
    )
    conn.commit()
    urls = {j["url"] for j in get_jobs_by_stage(conn=conn, stage="pending_extract", limit=0)}
    assert urls == {"pending", "stale", "retry"}
