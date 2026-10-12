"""Tests for scripts/seed_preprod.py: the preprod seed data and the guard that keeps it off every other database."""

import importlib.util
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from strelitzia import tracking
from strelitzia.database import close_connection

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "seed_preprod.py"
NOW = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def seed_mod():
    spec = importlib.util.spec_from_file_location("seed_preprod", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "seed.db"
    yield path
    close_connection(str(path))


def _rows(path: Path) -> list[dict]:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM jobs")]
    finally:
        conn.close()


def test_build_jobs_is_deterministic(seed_mod):
    assert seed_mod.build_jobs(seed_mod.DEFAULT_POSTINGS, NOW) == seed_mod.build_jobs(seed_mod.DEFAULT_POSTINGS, NOW)


def test_seed_covers_every_stage_and_status(seed_mod, db, capsys):
    assert seed_mod.main(["--sqlite", str(db)]) == 0
    assert capsys.readouterr().out.strip() == f"seeded 18 jobs into {db.name}"
    rows = _rows(db)
    assert len(rows) == 18
    assert all(r["job_key"] for r in rows)
    assert sum("example.com" in r["url"] for r in rows) == 12

    assert any(r["full_description"] is None for r in rows)  # discovered only
    assert any(r["full_description"] and r["detail_scraped_at"] and r["fit_score"] is None for r in rows)
    scores = [r["fit_score"] for r in rows if r["fit_score"] is not None]
    assert scores and all(3 <= s <= 10 for s in scores)
    assert all(r["score_reasoning"] for r in rows if r["fit_score"] is not None)
    assert any(r["tailored_at"] and not r["cover_letter_at"] for r in rows)
    assert any(r["cover_letter_at"] for r in rows)
    assert all(r["tailored_resume_path"] is None and r["cover_letter_path"] is None for r in rows)

    statuses = {r["user_status"] for r in rows}
    assert set(tracking.USER_STATUSES) <= statuses
    assert all(r["submitted_at"] for r in rows if r["user_status"] in ("submitted", "rejected", "heard_back"))


def test_seed_twice_wipes_first(seed_mod, db):
    assert seed_mod.main(["--sqlite", str(db)]) == 0
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO jobs (url, title) VALUES ('https://stray.example.com/1', 'Stray')")
    conn.execute("INSERT INTO dashboard_tasks (id, url, kind, state) VALUES ('t1', 'x', 'resume', 'queued')")
    conn.commit()
    conn.close()

    assert seed_mod.main(["--sqlite", str(db)]) == 0
    assert len(_rows(db)) == 18
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM dashboard_tasks").fetchone()[0] == 0
        events = conn.execute("SELECT COUNT(*) FROM status_events").fetchone()[0]
    finally:
        conn.close()
    assert events == 8  # 2 in_progress + 2 submitted + 2 × (submitted, then rejected / heard_back)


@pytest.mark.parametrize("url", [
    "postgresql://u:p@h/applypilot",
    "postgresql://u:p@h:5432/applypilot_test",
    "postgresql://u:p@h/applypilot_preprod_old",
    "mysql://u:p@h/applypilot_preprod",
    "sqlite:////tmp/applypilot_preprod",
    None,
])
def test_guard_refuses_anything_but_preprod(seed_mod, monkeypatch, capsys, tmp_path, url):
    if url is None:
        monkeypatch.delenv("STRELITZIA_DATABASE_URL", raising=False)
    else:
        monkeypatch.setenv("STRELITZIA_DATABASE_URL", url)
    monkeypatch.setattr(seed_mod, "init_db", lambda *_: pytest.fail("must not open a database"))
    assert seed_mod.main([]) == 2
    err = capsys.readouterr().err
    assert "refusing to seed" in err
    assert ":p@" not in err  # never echoes the password


def test_guard_accepts_preprod_name(seed_mod):
    assert seed_mod._preprod_db_name("postgresql://u:p@analytics-db:5432/applypilot_preprod") == "applypilot_preprod"
    assert seed_mod._preprod_db_name("postgres://u:p@h/applypilot_preprod?sslmode=disable") == "applypilot_preprod"
