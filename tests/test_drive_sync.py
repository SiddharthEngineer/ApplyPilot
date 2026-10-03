"""Tests for moving job PDFs to Drive (google-drive-file-library Tasks 4, 5, 7)."""

import pytest
from fake_drive import FakeDrive, fake_media

from applypilot.database import init_db
from applypilot.storage.drive import DriveClient
from applypilot.storage.sync import ensure_local_pdf, local_pdf, run_drive_sync


@pytest.fixture
def drive():
    return FakeDrive()


@pytest.fixture
def client(drive):
    return DriveClient(drive, media_factory=fake_media)


@pytest.fixture
def conn(tmp_path):
    return init_db(tmp_path / "jobs.db")


def _add_job(conn, tmp_path, url="https://example.com/jobs/1", title="Data Engineer", company="Acme",
             cover=True, tailored_at="2026-09-30T12:00:00"):
    stem = tmp_path / url.rsplit("/", 1)[-1]
    (stem.with_suffix(".pdf")).write_bytes(b"%PDF resume " + url.encode())
    (stem.with_suffix(".json")).write_text("{}")
    cl = None
    if cover:
        cl = tmp_path / f"{stem.name}_CL.txt"
        cl.write_text("Dear team")
        cl.with_suffix(".pdf").write_bytes(b"%PDF letter " + url.encode())
    conn.execute(
        "INSERT INTO jobs (url, title, site, company, tailored_resume_path, tailored_at, cover_letter_path, "
        "cover_letter_at) VALUES (?, ?, 'indeed', ?, ?, ?, ?, ?)",
        (url, title, company, str(stem.with_suffix(".pdf")), tailored_at, str(cl) if cl else None,
         tailored_at if cl else None),
    )
    conn.commit()
    return stem


def _row(conn, url="https://example.com/jobs/1"):
    cur = conn.execute("SELECT * FROM jobs WHERE url = ?", (url,))
    return dict(zip([d[0] for d in cur.description], cur.fetchone()))


def test_sync_moves_pdfs_and_saves_links(conn, client, drive, tmp_path):
    stem = _add_job(conn, tmp_path)
    result = run_drive_sync(conn, client)
    assert result == {"uploaded": 2, "updated": 0, "moved": 2, "missing": 0, "errors": 0}
    row = _row(conn)
    assert row["resume_drive_id"] and row["resume_drive_url"].startswith("https://drive.google.com/")
    assert row["cover_letter_drive_id"] and row["cover_letter_drive_url"] and row["drive_synced_at"]
    assert not stem.with_suffix(".pdf").exists()
    assert stem.with_suffix(".json").exists()  # working files stay
    assert (tmp_path / "1_CL.txt").exists()
    assert drive.path_of(row["resume_drive_id"])[:4] == ["ApplyPilot", "Acme", "Data Engineer", "2026-09-30"]


def test_second_sync_creates_nothing(conn, client, drive, tmp_path):
    _add_job(conn, tmp_path)
    run_drive_sync(conn, client)
    creates = drive.calls["create"]
    again = run_drive_sync(conn, client)
    assert drive.calls["create"] == creates
    assert again["uploaded"] == 0 and again["missing"] == 0


def test_retailored_pdf_updates_same_file(conn, client, drive, tmp_path):
    stem = _add_job(conn, tmp_path)
    run_drive_sync(conn, client)
    file_id = _row(conn)["resume_drive_id"]
    stem.with_suffix(".pdf").write_bytes(b"%PDF new version")
    result = run_drive_sync(conn, client)
    assert result["updated"] == 1 and result["uploaded"] == 0
    assert _row(conn)["resume_drive_id"] == file_id
    assert drive.store[file_id]["content"] == b"%PDF new version"


def test_same_company_role_date_different_jobs(conn, client, drive, tmp_path):
    _add_job(conn, tmp_path, url="https://example.com/jobs/1", cover=False)
    _add_job(conn, tmp_path, url="https://example.com/jobs/2", cover=False)
    run_drive_sync(conn, client)
    a = _row(conn, "https://example.com/jobs/1")["resume_drive_id"]
    b = _row(conn, "https://example.com/jobs/2")["resume_drive_id"]
    assert a != b and len(drive.pdfs()) == 2
    assert drive.store[a]["parents"] == drive.store[b]["parents"]


def test_checksum_mismatch_keeps_local(conn, tmp_path):
    stem = _add_job(conn, tmp_path, cover=False)
    bad = DriveClient(FakeDrive(corrupt_md5=True), media_factory=fake_media)
    result = run_drive_sync(conn, bad)
    assert result["errors"] == 1 and result["moved"] == 0
    assert stem.with_suffix(".pdf").exists()


def test_keep_local(conn, client, tmp_path):
    stem = _add_job(conn, tmp_path)
    result = run_drive_sync(conn, client, keep_local=True)
    assert result["uploaded"] == 2 and result["moved"] == 0
    assert stem.with_suffix(".pdf").exists()


def test_limit_and_missing(conn, client, tmp_path):
    _add_job(conn, tmp_path, url="https://example.com/jobs/1", cover=False)
    _add_job(conn, tmp_path, url="https://example.com/jobs/2", cover=False)
    gone = _add_job(conn, tmp_path, url="https://example.com/jobs/3", cover=False)
    gone.with_suffix(".pdf").unlink()
    result = run_drive_sync(conn, client, limit=1)
    assert result["uploaded"] == 1 and result["missing"] == 1


def test_no_local_files_needs_no_client(conn, tmp_path):
    stem = _add_job(conn, tmp_path, cover=False)
    stem.with_suffix(".pdf").unlink()
    assert run_drive_sync(conn, client=None)["uploaded"] == 0  # would raise DriveNotConfigured if it built a client


def test_one_job_error_does_not_stop_others(conn, drive, tmp_path):
    _add_job(conn, tmp_path, url="https://example.com/jobs/1", cover=False)
    _add_job(conn, tmp_path, url="https://example.com/jobs/2", cover=False)

    calls = []

    def flaky_media(path):
        calls.append(path)
        if len(calls) == 1:
            raise OSError("network blip")
        return fake_media(path)

    result = run_drive_sync(conn, DriveClient(drive, media_factory=flaky_media))
    assert result["errors"] == 1 and result["uploaded"] == 1


def test_ensure_local_pdf_downloads_moved_file(conn, client, tmp_path):
    stem = _add_job(conn, tmp_path, cover=False)
    original = stem.with_suffix(".pdf").read_bytes()
    run_drive_sync(conn, client)
    job = _row(conn)
    assert not local_pdf(job, "resume").exists()
    restored = ensure_local_pdf(job, "resume", client=client)
    assert restored == local_pdf(job, "resume") and restored.read_bytes() == original


def test_ensure_local_pdf_without_drive(tmp_path):
    job = {"tailored_resume_path": str(tmp_path / "x.pdf")}
    assert ensure_local_pdf(job, "resume") is None
    assert ensure_local_pdf({"tailored_resume_path": None}, "resume") is None


def test_build_prompt_downloads_moved_pdfs(conn, client, tmp_path, monkeypatch):
    from unittest.mock import patch

    from test_prompt import _minimal_profile

    from applypilot.apply.prompt import build_prompt
    from applypilot.storage.drive import DriveClient as RealClient

    stem = _add_job(conn, tmp_path)
    resume_bytes = stem.with_suffix(".pdf").read_bytes()
    run_drive_sync(conn, client)
    job = dict(_row(conn), application_url="https://example.com/apply", fit_score=8)
    assert not stem.with_suffix(".pdf").exists()

    monkeypatch.setattr(RealClient, "from_credentials", classmethod(lambda cls: client))
    workers = tmp_path / "workers"
    with (
        patch("applypilot.apply.prompt.config.load_profile", return_value=_minimal_profile()),
        patch("applypilot.apply.prompt.config.load_search_config", return_value={"location": {}}),
        patch("applypilot.apply.prompt.config.APPLY_WORKER_DIR", workers),
        patch("applypilot.config.load_blocked_sso", return_value=[]),
    ):
        build_prompt(job, "resume text", cdp_port=9222)

    uploads = sorted(p.name for p in (workers / "current").iterdir())
    assert any(n.endswith("_Resume.pdf") for n in uploads)
    assert any(n.endswith("_Cover_Letter.pdf") for n in uploads)
    resume_upload = next(p for p in (workers / "current").iterdir() if p.name.endswith("_Resume.pdf"))
    assert resume_upload.read_bytes() == resume_bytes
