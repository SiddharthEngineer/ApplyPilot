"""Tests for the Drive client against an in-memory fake (google-drive-file-library Task 3)."""

import subprocess
import sys

import pytest
from fake_drive import FakeDrive, fake_media

from applypilot.storage.drive import DriveClient, DriveNotConfigured, file_md5, load_credentials
from applypilot.storage.drive_layout import drive_target

JOB = {"url": "https://example.com/jobs/1", "title": "Data Engineer", "site": "indeed", "company": "Acme",
       "tailored_at": "2026-09-30T12:00:00"}


@pytest.fixture
def drive():
    return FakeDrive()


@pytest.fixture
def client(drive):
    return DriveClient(drive, media_factory=fake_media)


@pytest.fixture
def pdf(tmp_path):
    p = tmp_path / "resume.pdf"
    p.write_bytes(b"%PDF-1.4 first")
    return p


def test_folders_created_once(drive, client):
    a = client.ensure_folder_path(("ApplyPilot", "Acme", "Data Engineer", "2026-09-30"))
    b = DriveClient(drive).ensure_folder_path(("ApplyPilot", "Acme", "Data Engineer", "2026-09-30"))
    assert a == b
    assert len(drive.folders()) == 4
    assert drive.calls["create"] == 4


def test_upsert_creates_then_updates(drive, client, pdf):
    target = drive_target(JOB, "resume")
    first = client.upsert_file(target, pdf)
    assert first.created and first.md5 == file_md5(pdf) and first.url
    pdf.write_bytes(b"%PDF-1.4 second")
    second = DriveClient(drive, media_factory=fake_media).upsert_file(target, pdf)  # new run, no known id
    assert not second.created and second.id == first.id
    assert drive.store[first.id]["content"] == b"%PDF-1.4 second"
    assert len(drive.pdfs()) == 1
    assert drive.path_of(first.id) == ["ApplyPilot", "Acme", "Data Engineer", "2026-09-30",
                                       "Acme - Data Engineer - Resume.pdf"]
    assert drive.store[first.id]["appProperties"] == {
        "applypilot_job": target.job_key, "applypilot_kind": "resume", "applypilot_site": "indeed"}


def test_known_id_used(drive, client, pdf):
    target = drive_target(JOB, "resume")
    first = client.upsert_file(target, pdf)
    again = client.upsert_file(target, pdf, known_id=first.id)
    assert again.id == first.id and not again.created


def test_trashed_or_stale_known_id_falls_back(drive, client, pdf):
    target = drive_target(JOB, "resume")
    first = client.upsert_file(target, pdf)
    assert client.upsert_file(target, pdf, known_id="gone").id == first.id
    drive.store[first.id]["trashed"] = True
    fresh = client.upsert_file(target, pdf, known_id=first.id)
    assert fresh.created and fresh.id != first.id


def test_known_id_in_other_folder_not_reused(drive, client, pdf):
    old = client.upsert_file(drive_target(JOB, "resume"), pdf)
    later = dict(JOB, tailored_at="2026-10-05T12:00:00")
    new = client.upsert_file(drive_target(later, "resume"), pdf, known_id=old.id)
    assert new.created and new.id != old.id


def test_resume_and_cover_letter_are_separate(drive, client, pdf):
    r = client.upsert_file(drive_target(JOB, "resume"), pdf)
    c = client.upsert_file(drive_target(dict(JOB, cover_letter_at=JOB["tailored_at"]), "cover_letter"), pdf)
    assert r.id != c.id
    assert drive.store[c.id]["name"] == "Acme - Data Engineer - Cover Letter.pdf"


def test_name_collision_gets_suffix(drive, client, pdf):
    other = dict(JOB, url="https://example.com/jobs/2")
    a = client.upsert_file(drive_target(JOB, "resume"), pdf)
    b = client.upsert_file(drive_target(other, "resume"), pdf)
    assert a.id != b.id
    assert drive.store[b.id]["name"] == drive_target(other, "resume").collision_name()
    assert drive.store[a.id]["parents"] == drive.store[b.id]["parents"]


def test_quotes_in_names(drive, client, pdf):
    job = dict(JOB, company="O'Reilly", title="Engineer, Data's Team")
    a = client.upsert_file(drive_target(job, "resume"), pdf)
    b = client.upsert_file(drive_target(job, "resume"), pdf)
    assert a.id == b.id and len(drive.folders()) == 4


def test_download_round_trips(drive, client, pdf, tmp_path):
    f = client.upsert_file(drive_target(JOB, "resume"), pdf)
    out = client.download(f.id, tmp_path / "sub" / "back.pdf")
    assert out.read_bytes() == pdf.read_bytes()


def test_missing_token(monkeypatch, tmp_path):
    pytest.importorskip("googleapiclient")
    from applypilot import config
    monkeypatch.setattr(config, "GOOGLE_TOKEN_PATH", tmp_path / "none.json")
    with pytest.raises(DriveNotConfigured, match="drive auth"):
        load_credentials()


def test_import_without_google_libraries():
    code = (
        "import sys; sys.modules['googleapiclient'] = None; sys.modules['google_auth_oauthlib'] = None; "
        "import applypilot.storage.drive as d; assert not d.drive_libraries_installed()"
    )
    subprocess.run([sys.executable, "-c", code], check=True)
