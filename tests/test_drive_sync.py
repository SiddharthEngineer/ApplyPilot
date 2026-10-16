"""Tests for moving job PDFs to Drive (google-drive-file-library Tasks 4, 5, 7)."""

import pytest
from fake_drive import FakeDrive, fake_media

from strelitzia import users
from strelitzia.database import init_db
from strelitzia.storage.drive import DriveClient
from strelitzia.storage.sync import ensure_local_pdf, local_pdf, run_drive_sync


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
    assert drive.path_of(row["resume_drive_id"])[:4] == ["Strelitzia", "Acme", "Data Engineer", "2026-09-30"]


def test_sync_writes_only_the_jobs_users_row(conn, client, tmp_path):
    _add_job(conn, tmp_path)
    other = users.invite(conn, "b@example.com").id
    conn.execute("INSERT INTO jobs (user_id, url, title) VALUES (?, ?, 'Theirs')", (other, "https://example.com/jobs/1"))
    conn.commit()
    run_drive_sync(conn, client)
    rows = {r["user_id"]: dict(r) for r in conn.execute("SELECT * FROM jobs").fetchall()}
    assert rows[users.owner(conn).id]["resume_drive_id"] and rows[users.owner(conn).id]["drive_folder_id"]
    assert rows[other]["resume_drive_id"] is None and rows[other]["drive_folder_id"] is None


def test_sync_saves_the_job_folder(conn, client, drive, tmp_path):
    _add_job(conn, tmp_path)
    run_drive_sync(conn, client)
    row = _row(conn)
    resume = drive.store[row["resume_drive_id"]]
    folder = resume["parents"][0]
    assert drive.store[folder]["name"] == "2026-09-30"  # the leaf: company/role/date
    assert row["drive_folder_id"] == folder
    assert row["drive_folder_url"] == f"https://drive.google.com/drive/folders/{folder}"


def test_cover_only_job_gets_the_cover_folder(conn, client, drive, tmp_path):
    stem = _add_job(conn, tmp_path)
    stem.with_suffix(".pdf").unlink()
    run_drive_sync(conn, client)
    row = _row(conn)
    assert row["resume_drive_id"] is None
    assert row["drive_folder_id"] == drive.store[row["cover_letter_drive_id"]]["parents"][0]


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


def test_sync_leaves_row_factory_alone(conn, client, tmp_path):
    import sqlite3
    conn.row_factory = sqlite3.Row
    _add_job(conn, tmp_path)
    run_drive_sync(conn, client)
    assert conn.row_factory is sqlite3.Row


@pytest.fixture
def token(tmp_path, monkeypatch):
    from strelitzia import config
    path = tmp_path / "google_token.json"
    monkeypatch.setattr(config, "GOOGLE_TOKEN_PATH", path)
    return path


def test_drive_sync_enabled(token, monkeypatch):
    from strelitzia.config import drive_sync_enabled
    monkeypatch.delenv("DRIVE_SYNC", raising=False)
    token.write_text("{}")
    assert not drive_sync_enabled()
    monkeypatch.setenv("DRIVE_SYNC", "1")
    assert drive_sync_enabled()
    token.unlink()
    assert not drive_sync_enabled()


# DRIVE_SYNC after a draft is saved: tests/test_cli_workbench.py.


def test_pdf_stage_does_not_rebuild_moved_pdfs(conn, client, tmp_path, monkeypatch):
    import strelitzia.scoring.pdf as pdf_mod
    from strelitzia.storage import sync
    stem = _add_job(conn, tmp_path, cover=False)
    stem.with_suffix(".txt").write_text("resume text")
    (tmp_path / "other.txt").write_text("unsynced resume")
    run_drive_sync(conn, client)
    converted = []
    monkeypatch.setattr(pdf_mod, "TAILORED_DIR", tmp_path)
    monkeypatch.setattr(pdf_mod, "convert_to_pdf", lambda f: converted.append(f.name))
    monkeypatch.setattr(sync, "get_connection", lambda: conn)
    pdf_mod.batch_convert()
    assert "1.txt" not in converted and "other.txt" in converted


def test_sync_only_moves_the_owners_pdfs(conn, client, drive, tmp_path):
    """The CLI's token is the owner's Drive: another user's PDFs stay put (user-auth-6-drive)."""
    friend = users.invite(conn, "friend@example.com")
    stem = _add_job(conn, tmp_path, url="https://example.com/jobs/9")
    conn.execute("UPDATE jobs SET user_id = ? WHERE url = ?", (friend.id, "https://example.com/jobs/9"))
    conn.commit()
    assert run_drive_sync(conn, client)["uploaded"] == 0
    assert stem.with_suffix(".pdf").exists() and not drive.pdfs()
    assert run_drive_sync(conn, client, user_id=friend.id)["uploaded"] == 2
