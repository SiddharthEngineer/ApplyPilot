"""`strelitzia workbench packet|import` and `strelitzia run tailor` after the API writer was removed."""

import re
from pathlib import Path

import pytest
from fake_drive import FakeDrive, fake_media
from typer.testing import CliRunner

from strelitzia import config, database
from strelitzia.cli import app
from strelitzia.scoring import pdf as pdf_mod
from strelitzia.scoring import workbench as wb
from strelitzia.storage.drive import DriveClient
from workbench_helpers import add_job_row, fake_render_pdf, inputs

runner = CliRunner()
FIXTURES = Path(__file__).parent / "fixtures"
OK = FIXTURES / "workbench_result_ok.json"
BAD = FIXTURES / "workbench_result_bad.json"


def _plain(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


@pytest.fixture
def env(tmp_path, monkeypatch):
    db = tmp_path / "jobs.db"
    monkeypatch.setattr(database, "DB_PATH", db)
    monkeypatch.setattr(config, "TAILORED_DIR", tmp_path / "tailored")
    monkeypatch.setattr(config, "COVER_LETTER_DIR", tmp_path / "cover")
    monkeypatch.setattr(config, "GOOGLE_TOKEN_PATH", tmp_path / "google_token.json")
    monkeypatch.setattr(wb.Inputs, "load", classmethod(lambda cls: inputs()))
    monkeypatch.setattr(pdf_mod, "render_pdf", fake_render_pdf)
    monkeypatch.delenv("DRIVE_SYNC", raising=False)
    conn = database.init_db(db)
    job = add_job_row(conn)
    yield conn, job
    database.close_connection(db)


def test_packet_prints_the_inline_packet(env):
    _, job = env
    result = runner.invoke(app, ["workbench", "packet", job["job_key"]])
    assert result.exit_code == 0, result.output
    assert "Required qualifications" in result.output
    assert '"strelitzia"' in result.output and "notes_for_user" in result.output  # the JSON schema


def test_unknown_key(env):
    result = runner.invoke(app, ["workbench", "packet", "nope"])
    assert result.exit_code == 1 and "No job with key nope" in _plain(result.output)


def test_import_ok_saves_draft_v1(env):
    conn, job = env
    result = runner.invoke(app, ["workbench", "import", job["job_key"], str(OK)])
    out = _plain(result.output)
    assert result.exit_code == 0, out
    assert "Saved draft v1" in out
    row = dict(conn.execute("SELECT * FROM jobs").fetchone())
    assert row["tailored_resume_path"] in out and row["resume_docx_path"] in out
    assert runner.invoke(app, ["workbench", "import", job["job_key"], str(OK)]).exit_code == 0
    assert conn.execute("SELECT draft_version FROM jobs").fetchone()[0] == 2


def test_import_bad_exits_1_with_the_fix_message(env):
    conn, job = env
    result = runner.invoke(app, ["workbench", "import", job["job_key"], str(BAD)])
    out = _plain(result.output)
    assert result.exit_code == 1
    assert "These problems were found:" in out and "Kubernetes" in out
    assert "reply with the corrected JSON" in out
    assert conn.execute("SELECT draft_version FROM jobs").fetchone()[0] is None


@pytest.fixture
def drive(monkeypatch):
    fake = FakeDrive()
    client = DriveClient(fake, media_factory=fake_media)
    monkeypatch.setattr(DriveClient, "from_credentials", classmethod(lambda cls: client))
    return fake


def test_import_moves_pdfs_to_drive_when_drive_sync_is_on(env, drive, monkeypatch):
    conn, job = env
    config.GOOGLE_TOKEN_PATH.write_text("{}")
    monkeypatch.setenv("DRIVE_SYNC", "1")
    result = runner.invoke(app, ["workbench", "import", job["job_key"], str(OK)])
    assert result.exit_code == 0, result.output
    assert "Moved the PDFs to Google Drive" in result.output
    row = dict(conn.execute("SELECT * FROM jobs").fetchone())
    assert row["resume_drive_url"] and row["cover_letter_drive_url"]
    assert len(drive.pdfs()) == 2


def test_import_skips_drive_when_drive_sync_is_off(env, drive):
    _, job = env
    config.GOOGLE_TOKEN_PATH.write_text("{}")
    assert runner.invoke(app, ["workbench", "import", job["job_key"], str(OK)]).exit_code == 0
    assert drive.pdfs() == []


def test_drive_errors_do_not_fail_the_import(env, monkeypatch):
    _, job = env
    config.GOOGLE_TOKEN_PATH.write_text("{}")
    monkeypatch.setenv("DRIVE_SYNC", "1")

    def boom(cls):
        raise RuntimeError("Drive is down")

    monkeypatch.setattr(DriveClient, "from_credentials", classmethod(boom))
    result = runner.invoke(app, ["workbench", "import", job["job_key"], str(OK)])
    assert result.exit_code == 0
    assert "Saved draft v1" in result.output and "Drive is down" in _plain(result.output)


def test_run_tailor_points_to_the_workbench(env, monkeypatch):
    monkeypatch.setattr("strelitzia.config.check_tier", lambda *a, **k: None)
    for stage in ("tailor", "cover"):
        result = runner.invoke(app, ["run", stage])
        out = " ".join(_plain(result.output).split())
        assert result.exit_code == 0, out
        assert "Resumes are written in Claude now: open a job in the dashboard → Open in Claude" in out


def test_dry_run_has_no_tailor_or_cover_stage(env, monkeypatch):
    monkeypatch.setattr("strelitzia.config.check_tier", lambda *a, **k: None)
    result = runner.invoke(app, ["run", "--dry-run"], env={"COLUMNS": "200"})
    out = _plain(result.output)
    assert result.exit_code == 0, out
    assert "DRY RUN" in out and "score" in out and "extract" in out
    stages = [line.split()[0] for line in out.splitlines() if line.startswith("    ") and line.split()]
    assert "score" in stages and "tailor" not in stages and "cover" not in stages
