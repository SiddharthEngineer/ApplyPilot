"""Tests for the Drive client against an in-memory fake (google-drive-file-library Task 3)."""

import subprocess
import sys

import pytest
from fake_drive import FakeDrive, fake_media

from strelitzia.storage.drive import DriveClient, DriveNotConfigured, file_md5, load_credentials
from strelitzia.storage.drive_layout import drive_target

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


def test_existing_legacy_root_folder_is_reused(drive, client, monkeypatch):
    """Files synced before the rename sit under ApplyPilot/ (the old default root): new files join that tree."""
    monkeypatch.delenv("DRIVE_ROOT_FOLDER", raising=False)
    legacy = DriveClient(drive).ensure_folder_path(("ApplyPilot", "Acme"))
    assert client.ensure_folder_path(("Strelitzia", "Acme")) == legacy
    assert len(drive.folders()) == 2


def test_new_root_folder_without_a_legacy_one(drive, client, monkeypatch):
    monkeypatch.delenv("DRIVE_ROOT_FOLDER", raising=False)
    client.ensure_folder_path(("Strelitzia", "Acme"))
    assert sorted(f["name"] for f in drive.folders()) == ["Acme", "Strelitzia"]


def test_folders_created_once(drive, client):
    a = client.ensure_folder_path(("Strelitzia", "Acme", "Data Engineer", "2026-09-30"))
    b = DriveClient(drive).ensure_folder_path(("Strelitzia", "Acme", "Data Engineer", "2026-09-30"))
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
    assert drive.path_of(first.id) == ["Strelitzia", "Acme", "Data Engineer", "2026-09-30",
                                       "Acme - Data Engineer - Resume.pdf"]
    assert drive.store[first.id]["appProperties"] == {
        "applypilot_job": target.job_key, "applypilot_kind": "resume", "applypilot_site": "indeed"}  # legacy keys, kept for existing files


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
    from strelitzia import config
    monkeypatch.setattr(config, "GOOGLE_TOKEN_PATH", tmp_path / "none.json")
    with pytest.raises(DriveNotConfigured, match="drive auth"):
        load_credentials()


def test_import_without_google_libraries():
    code = (
        "import sys; sys.modules['googleapiclient'] = None; sys.modules['google_auth_oauthlib'] = None; "
        "import strelitzia.storage.drive as d; assert not d.drive_libraries_installed()"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


# -- Per-user token store (user-auth-6-drive Task 1) ---------------------------

@pytest.fixture
def token_key(monkeypatch):
    """A Fernet key, and the Drive library check passing (google-auth, all these tests need, is in the web extra)."""
    from cryptography.fernet import Fernet

    from strelitzia.storage import drive as drive_mod
    monkeypatch.setattr(drive_mod, "drive_libraries_installed", lambda: True)
    monkeypatch.setenv("STRELITZIA_TOKEN_KEY", Fernet.generate_key().decode())


@pytest.fixture
def conn(tmp_path):
    from strelitzia.database import init_db
    c = init_db(tmp_path / "t.db")
    yield c
    c.close()


def _creds(expired: bool = False):
    from datetime import datetime, timedelta, timezone

    from google.oauth2.credentials import Credentials
    expiry = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=-1 if expired else 1)
    return Credentials(token="access-secret", refresh_token="refresh-secret", token_uri="https://oauth2.googleapis.com/token",
                       client_id="cid", client_secret="csecret", scopes=["https://www.googleapis.com/auth/drive.file"],
                       expiry=expiry)


def _stored(conn, user_id):
    row = conn.execute("SELECT token_enc FROM user_drive WHERE user_id = ?", (user_id,)).fetchone()
    return row[0] if row else None


def test_user_token_is_encrypted_at_rest(conn, token_key):
    from strelitzia.storage.drive import load_user_credentials, save_user_token, user_drive_status
    save_user_token(conn, "u1", _creds())
    stored = _stored(conn, "u1")
    assert stored and "refresh_token" not in stored and "refresh-secret" not in stored and "access-secret" not in stored
    creds = load_user_credentials(conn, "u1")
    assert creds.refresh_token == "refresh-secret" and creds.token == "access-secret"
    assert user_drive_status(conn, "u1")[0] == "authorized"
    assert user_drive_status(conn, "u2") == ("not_authorized", "Connect Google Drive on the Claude setup page")


def test_save_refuses_without_token_key(conn, monkeypatch):
    from strelitzia.storage.drive import save_user_token, user_drive_status
    monkeypatch.delenv("STRELITZIA_TOKEN_KEY", raising=False)
    monkeypatch.delenv("APPLYPILOT_TOKEN_KEY", raising=False)
    with pytest.raises(DriveNotConfigured, match="TOKEN_KEY"):
        save_user_token(conn, "u1", _creds())
    assert _stored(conn, "u1") is None
    assert user_drive_status(conn, "u1")[0] in ("not_configured", "not_installed")


def test_missing_user_token(conn, token_key):
    from strelitzia.storage.drive import DriveClient, load_user_credentials
    with pytest.raises(DriveNotConfigured, match="Claude setup page"):
        load_user_credentials(conn, "nobody")
    with pytest.raises(DriveNotConfigured):
        DriveClient.for_user(conn, "nobody")


def test_expired_user_token_is_refreshed_and_resaved(conn, token_key, monkeypatch):
    from datetime import datetime, timedelta, timezone

    from google.oauth2.credentials import Credentials

    from strelitzia.storage.drive import load_user_credentials, save_user_token

    def fake_refresh(self, request):
        self.token = "new-access"
        self.expiry = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)

    monkeypatch.setattr(Credentials, "refresh", fake_refresh)
    save_user_token(conn, "u1", _creds(expired=True))
    before = _stored(conn, "u1")
    assert load_user_credentials(conn, "u1").token == "new-access"
    assert _stored(conn, "u1") != before
    assert load_user_credentials(conn, "u1").token == "new-access"  # the refreshed token was saved


def test_revoked_user_token(conn, token_key, monkeypatch):
    from google.auth.exceptions import RefreshError
    from google.oauth2.credentials import Credentials

    from strelitzia.storage.drive import load_user_credentials, save_user_token

    def revoked(self, request):
        raise RefreshError("invalid_grant")

    monkeypatch.setattr(Credentials, "refresh", revoked)
    save_user_token(conn, "u1", _creds(expired=True))
    with pytest.raises(DriveNotConfigured, match="Connect Google Drive"):
        load_user_credentials(conn, "u1")


def test_wrong_token_key_asks_to_reconnect(conn, token_key, monkeypatch):
    from cryptography.fernet import Fernet

    from strelitzia.storage.drive import load_user_credentials, save_user_token
    save_user_token(conn, "u1", _creds())
    monkeypatch.setenv("STRELITZIA_TOKEN_KEY", Fernet.generate_key().decode())
    with pytest.raises(DriveNotConfigured, match="TOKEN_KEY"):
        load_user_credentials(conn, "u1")


def test_disconnect(conn, token_key):
    from strelitzia.storage.drive import disconnect, save_user_token, user_drive_status
    save_user_token(conn, "u1", _creds())
    save_user_token(conn, "u2", _creds())
    disconnect(conn, "u1")
    assert user_drive_status(conn, "u1")[0] == "not_authorized"
    assert user_drive_status(conn, "u2")[0] == "authorized"


def test_owner_imports_legacy_token_file_once(conn, token_key, monkeypatch, tmp_path):
    from strelitzia import config, users
    from strelitzia.storage.drive import disconnect, import_legacy_token, load_user_credentials
    token_file = tmp_path / "google_token.json"
    token_file.write_text(_creds().to_json(), encoding="utf-8")
    monkeypatch.setattr(config, "GOOGLE_TOKEN_PATH", token_file)
    owner = users.owner(conn).id
    assert import_legacy_token(conn) is True
    assert load_user_credentials(conn, owner).refresh_token == "refresh-secret"
    assert "refresh-secret" not in _stored(conn, owner)
    assert token_file.exists()  # the CLI keeps its file
    disconnect(conn, owner)
    assert import_legacy_token(conn) is False  # once: a later disconnect sticks
    assert _stored(conn, owner) is None


def test_legacy_import_waits_for_token_key(conn, monkeypatch, tmp_path):
    from strelitzia import config
    from strelitzia.storage.drive import import_legacy_token
    token_file = tmp_path / "google_token.json"
    token_file.write_text(_creds().to_json(), encoding="utf-8")
    monkeypatch.setattr(config, "GOOGLE_TOKEN_PATH", token_file)
    monkeypatch.delenv("STRELITZIA_TOKEN_KEY", raising=False)
    monkeypatch.delenv("APPLYPILOT_TOKEN_KEY", raising=False)
    assert import_legacy_token(conn) is False
    from cryptography.fernet import Fernet
    monkeypatch.setenv("STRELITZIA_TOKEN_KEY", Fernet.generate_key().decode())
    assert import_legacy_token(conn) is True
