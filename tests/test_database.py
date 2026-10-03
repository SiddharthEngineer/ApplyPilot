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
