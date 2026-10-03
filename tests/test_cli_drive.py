"""Tests for `applypilot drive` (google-drive-file-library Task 6)."""

import csv
import re

import pytest
from fake_drive import FakeDrive, fake_media
from typer.testing import CliRunner

from applypilot import config, database
from applypilot.cli import app
from applypilot.storage.drive import DriveClient

runner = CliRunner()


def _plain(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


@pytest.fixture
def env(tmp_path, monkeypatch):
    db = tmp_path / "jobs.db"
    monkeypatch.setattr(database, "DB_PATH", db)
    monkeypatch.setattr(config, "GOOGLE_TOKEN_PATH", tmp_path / "google_token.json")
    client = DriveClient(FakeDrive(), media_factory=fake_media)
    monkeypatch.setattr(DriveClient, "from_credentials", classmethod(lambda cls: client))
    conn = database.init_db(db)
    for i, company in enumerate(["Acme", "Globex"], start=1):
        pdf = tmp_path / f"job{i}.pdf"
        pdf.write_bytes(b"%PDF " + company.encode())
        conn.execute(
            "INSERT INTO jobs (url, title, site, company, tailored_resume_path, tailored_at) "
            "VALUES (?, 'Data Engineer', 'linkedin', ?, ?, '2026-09-30T12:00:00')",
            (f"https://example.com/jobs/{i}", company, str(pdf)),
        )
    conn.commit()
    yield tmp_path
    database.close_connection(db)


def test_help_lists_commands():
    out = _plain(runner.invoke(app, ["drive", "--help"]).output)
    for cmd in ("auth", "sync", "links"):
        assert cmd in out


def test_sync_then_links_csv(env):
    result = runner.invoke(app, ["drive", "sync"])
    assert result.exit_code == 0, result.output
    out = _plain(result.output)
    assert "New files" in out and "2" in out
    assert not (env / "job1.pdf").exists()

    csv_path = env / "links.csv"
    result = runner.invoke(app, ["drive", "links", "--company", "acme", "--csv", str(csv_path)])
    assert result.exit_code == 0, result.output
    rows = list(csv.DictReader(csv_path.open()))
    assert [r["company"] for r in rows] == ["Acme"]
    assert set(rows[0]) == {"company", "role", "date", "site", "resume_url", "cover_letter_url", "job_url"}
    assert rows[0]["resume_url"].startswith("https://drive.google.com/")
    assert rows[0]["site"] == "linkedin" and rows[0]["date"] == "2026-09-30"


def test_links_empty(env):
    result = runner.invoke(app, ["drive", "links"])
    assert result.exit_code == 0 and "No Drive links yet" in result.output


def test_sync_without_token(env, monkeypatch):
    from applypilot.storage.drive import load_credentials
    monkeypatch.setattr(DriveClient, "from_credentials", classmethod(lambda cls: cls(load_credentials())))
    result = runner.invoke(app, ["drive", "sync"])
    assert result.exit_code == 1
    assert "drive" in _plain(result.output).lower()


def test_drive_status(env, monkeypatch):
    from applypilot.storage import drive
    monkeypatch.setattr(drive, "drive_libraries_installed", lambda: True)
    assert drive.drive_status()[0] == "not_authorized"
    (env / "google_token.json").write_text("{}")
    assert drive.drive_status()[0] == "authorized"
    monkeypatch.setattr(drive, "drive_libraries_installed", lambda: False)
    assert drive.drive_status()[0] == "not_installed"
