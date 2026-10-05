"""Tests for `applypilot db migrate` / `db verify` (SQLite → SQLite always; SQLite → Postgres with the pg tier)."""

import sqlite3
from datetime import date

import pytest
from typer.testing import CliRunner

from applypilot.cli import app
from applypilot.database import get_connection, init_db, store_jobs
from applypilot.migrate import backup_source, migrate, verify

BACKENDS = pytest.mark.parametrize(
    "db_target", ["sqlite", pytest.param("pg", marks=pytest.mark.pg)], indirect=True
)


def _source(tmp_path, n=7):
    src = tmp_path / "src.db"
    conn = init_db(src)
    store_jobs(conn, [{"url": f"https://x/{i}", "title": f"T{i}", "salary": "50%"} for i in range(n)],
               site="RemoteOK", strategy="json_ld")
    conn.execute("UPDATE jobs SET fit_score = 8, full_description = 'jd' WHERE url IN (?, ?)", ("https://x/0", "https://x/1"))
    conn.execute("UPDATE jobs SET tailored_resume_path = '/r.pdf' WHERE url = ?", ("https://x/0",))
    conn.commit()
    return src


def _dst(db_target):
    return db_target if str(db_target).startswith("postgresql") else f"sqlite:///{db_target}"


@BACKENDS
def test_migrate_copies_every_row_and_is_rerunnable(tmp_path, db_target):
    src = _source(tmp_path)
    dst = _dst(db_target)

    first = migrate(src, dst, batch=3)
    assert first["source"] == 7 and first["inserted"] == 7 and first["skipped"] == 0
    assert first["destination"] == 7
    assert all(a == b for _, a, b in verify(src, dst))

    # New rows in the source since the first run are picked up; existing ones are skipped.
    conn = get_connection(src)
    store_jobs(conn, [{"url": "https://x/new", "title": "New"}], site="Dice", strategy="api")
    second = migrate(src, dst, batch=3, backup=False)
    assert second["inserted"] == 1 and second["skipped"] == 7 and second["destination"] == 8

    row = get_connection(dst).execute("SELECT * FROM jobs WHERE url = ?", ("https://x/0",)).fetchone()
    assert row["fit_score"] == 8 and row["tailored_resume_path"] == "/r.pdf" and row["salary"] == "50%"


def test_migrate_copies_only_shared_columns(tmp_path, caplog):
    src = tmp_path / "old.db"
    conn = sqlite3.connect(src)
    conn.execute("CREATE TABLE jobs (url TEXT PRIMARY KEY, title TEXT, legacy_col TEXT)")
    conn.execute("INSERT INTO jobs VALUES ('u1', 'Engineer', 'x')")
    conn.commit()
    conn.close()

    result = migrate(src, f"sqlite:///{tmp_path / 'new.db'}", backup=False)

    assert result["inserted"] == 1
    assert result["missing_columns"] == ["legacy_col"]
    assert "legacy_col" in caplog.text


def test_verify_reports_mismatches(tmp_path):
    src = _source(tmp_path)
    dst = f"sqlite:///{tmp_path / 'dst.db'}"
    migrate(src, dst, backup=False)
    get_connection(dst).execute("UPDATE jobs SET fit_score = NULL WHERE url = ?", ("https://x/1",))
    get_connection(dst).commit()

    checks = {name: (a, b) for name, a, b in verify(src, dst)}
    assert checks["jobs"] == (7, 7)
    assert checks["jobs.fit_score"] == (2, 1)
    assert checks["jobs.tailored_resume_path"] == (1, 1)


def test_backup_source(tmp_path):
    src = _source(tmp_path)
    bak = backup_source(src, today=date(2026, 10, 5))
    assert bak.name == "src.db.bak-2026-10-05"
    assert sqlite3.connect(bak).execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 7


def test_source_is_not_modified(tmp_path):
    src = _source(tmp_path)

    def snapshot():
        conn = sqlite3.connect(src)
        try:
            return conn.execute("SELECT * FROM jobs ORDER BY url").fetchall()
        finally:
            conn.close()

    before = snapshot()
    migrate(src, f"sqlite:///{tmp_path / 'dst.db'}", backup=False)
    assert snapshot() == before


class TestCli:
    def test_migrate_then_verify(self, tmp_path):
        src = _source(tmp_path)
        dst = f"sqlite:///{tmp_path / 'cli.db'}"
        runner = CliRunner()

        out = runner.invoke(app, ["db", "migrate", "--from", str(src), "--to", dst])
        assert out.exit_code == 0, out.output
        assert "Copied 7 new jobs" in out.output

        out = runner.invoke(app, ["db", "verify", "--from", str(src), "--to", dst])
        assert out.exit_code == 0, out.output
        assert "All counts match" in out.output

    def test_verify_exits_nonzero_on_mismatch(self, tmp_path):
        src = _source(tmp_path)
        dst = f"sqlite:///{tmp_path / 'cli2.db'}"
        init_db(dst)
        out = CliRunner().invoke(app, ["db", "verify", "--from", str(src), "--to", dst])
        assert out.exit_code == 1
        assert "MISMATCH" in out.output

    def test_no_destination(self, tmp_path):
        src = _source(tmp_path)
        out = CliRunner().invoke(app, ["db", "migrate", "--from", str(src)])
        assert out.exit_code == 1
        assert "No destination" in out.output
