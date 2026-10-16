"""Claude workbench routes: the packet (inline vs Drive), pasting a result back, and the setup page."""

import json

import pytest
from fake_drive import FakeDrive, fake_media
from web_helpers import add_job

from strelitzia import config
from strelitzia.database import job_key
from strelitzia.scoring import pdf as pdf_mod
from strelitzia.scoring import workbench as wb
from strelitzia.storage.drive import DOC_MIME, DriveClient
from strelitzia.web import app as app_mod
from workbench_helpers import POSTING, fake_render_pdf, good_result, inputs, job, reply

REAL_DRIVE_CLIENT = app_mod._drive_client
URL = job()["url"]
KEY = job_key(URL)
_REAL_FOR_USER = wb.Inputs.__dict__["for_user"]


@pytest.fixture(autouse=True)
def _workbench(monkeypatch, tmp_path):
    monkeypatch.setattr(pdf_mod, "render_pdf", fake_render_pdf)
    monkeypatch.setattr(wb.Inputs, "for_user", classmethod(lambda cls, conn, user_id: inputs()))
    monkeypatch.setattr(config, "TAILORED_DIR", tmp_path / "tailored")
    monkeypatch.setattr(config, "COVER_LETTER_DIR", tmp_path / "cover")
    monkeypatch.setattr(app_mod, "_drive_client", lambda request: None)
    monkeypatch.setenv("DRIVE_ROOT_FOLDER", "Strelitzia")


@pytest.fixture
def the_job(conn):
    j = job()
    add_job(conn, URL, title=j["title"], company=j["company"], site=j["site"], job_key=KEY,
            full_description=j["full_description"], details_json=json.dumps(POSTING))
    return KEY


@pytest.fixture
def drive(monkeypatch):
    fake = FakeDrive()
    client = DriveClient(fake, media_factory=fake_media, html_media_factory=lambda html: html.encode("utf-8"))
    monkeypatch.setattr(app_mod, "_drive_client", lambda request: client)
    return fake


def test_packet_without_drive_is_inline(client, the_job):
    r = client.post(f"/app/api/jobs/{the_job}/packet")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["inline"] is True and body["doc_url"] is None and body["drive_error"] is None
    assert "Required qualifications" in body["prompt"] and "notes_for_user" in body["prompt"]
    assert body["rules_version"] == wb.RULES_VERSION


def test_packet_with_drive_writes_docs_and_a_short_prompt(client, conn, the_job, drive):
    body = client.post(f"/app/api/jobs/{the_job}/packet").json()
    assert body["inline"] is False and body["doc_url"]
    assert len(body["prompt"]) <= wb.PROMPT_MAX and "Claude packet" in body["prompt"]
    names = sorted(f["name"] for f in drive.store.values() if f["mimeType"] == DOC_MIME)
    assert names == sorted(["Strelitzia rules", "Strelitzia content library", "Strelitzia panel rubric",
                            f"Initech – {job()['title']} – Claude packet"])
    detail = client.get(f"/app/api/jobs/{the_job}").json()
    assert detail["packet_doc_url"] == body["doc_url"] == detail["links"]["packet"]

    setup = client.get("/app/api/claude/setup").json()
    assert {d["kind"]: bool(d["url"]) for d in setup["docs"]} == {"rules": True, "library": True}


def test_packet_falls_back_to_inline_when_drive_fails(client, the_job, monkeypatch):
    def broken(request):
        raise RuntimeError("token expired")

    monkeypatch.setattr(app_mod, "_drive_client", broken)
    body = client.post(f"/app/api/jobs/{the_job}/packet").json()
    assert body["inline"] is True and "token expired" in body["drive_error"]


def test_bad_result_is_422_with_a_fix_prompt(client, conn, the_job):
    data = good_result()
    data["resume"]["skills"].append({"category": "Cloud", "items": "Kubernetes"})
    r = client.post(f"/app/api/jobs/{the_job}/result", json={"text": reply(data)})
    assert r.status_code == 422
    body = r.json()
    assert any("Kubernetes" in e for e in body["errors"])
    assert body["fix_prompt"].startswith("These problems were found:") and "Kubernetes" in body["fix_prompt"]
    assert conn.execute("SELECT draft_version FROM jobs").fetchone()[0] is None


def test_result_without_json_is_422(client, the_job):
    r = client.post(f"/app/api/jobs/{the_job}/result", json={"text": "Sure, here you go!"})
    assert r.status_code == 422 and r.json()["fix_prompt"]


def test_good_result_saves_and_bumps_the_version(client, the_job):
    r = client.post(f"/app/api/jobs/{the_job}/result", json={"text": reply(good_result())})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["outcome"]["draft_version"] == 1
    d = body["job"]
    assert d["draft_version"] == 1 and d["draft_rules_version"] == wb.RULES_VERSION
    assert d["coverage"]["pct_required"] == 80.0 and d["unmet"] == ["Kubernetes", "Terraform"]
    assert [h["version"] for h in d["draft_history"]] == [1]
    for kind in ("resume_pdf", "resume_docx", "cover_letter_pdf"):
        link = d["links"][kind]
        f = client.get(link)
        assert f.status_code == 200 and f.content, kind

    d2 = client.post(f"/app/api/jobs/{the_job}/result",
                     json={"text": reply(good_result(headline="Data engineer for Airflow pipelines"))}).json()["job"]
    assert d2["draft_version"] == 2
    assert [h["version"] for h in d2["draft_history"]] == [1, 2]


def test_good_result_uploads_to_drive(client, the_job, drive):
    d = client.post(f"/app/api/jobs/{the_job}/result", json={"text": reply(good_result())}).json()["job"]
    assert d["links"]["resume"] and d["links"]["cover_letter"]
    assert "resume_pdf" not in d["links"]  # moved to Drive
    assert d["links"]["resume_docx"]  # the DOCX stays local


def test_submitting_records_the_draft_and_rules_version(client, the_job):
    client.post(f"/app/api/jobs/{the_job}/result", json={"text": reply(good_result())})
    d = client.post(f"/app/api/jobs/{the_job}/status", json={"status": "submitted"}).json()
    assert d["submitted_draft_version"] == 1 and d["submitted_rules_version"] == wb.RULES_VERSION


def test_missing_file_is_404(client, the_job):
    assert client.get(f"/app/api/jobs/{the_job}/files/resume_pdf").status_code == 404
    assert client.get(f"/app/api/jobs/{the_job}/files/other").status_code == 422


def test_setup_without_drive(client):
    s = client.get("/app/api/claude/setup").json()
    assert s["drive"] is False and s["rules_version"] == wb.RULES_VERSION
    assert [d["name"] for d in s["docs"]] == ["Strelitzia rules", "Strelitzia content library"]
    assert all(d["url"] is None for d in s["docs"])
    assert "Strelitzia rules" in s["project_instructions"]
    assert client.post("/app/api/claude/docs").status_code == 409


def test_publish_docs_from_setup(client, drive):
    s = client.post("/app/api/claude/docs").json()
    assert all(d["url"] and d["changed_at"] for d in s["docs"])


def test_a_saved_draft_marks_the_job_in_progress(client, the_job):
    client.post(f"/app/api/jobs/{the_job}/result", json={"text": reply(good_result())})
    d = client.get(f"/app/api/jobs/{the_job}").json()
    assert d["status"] == "in_progress" and d["status_color"] == "yellow"
    assert d["events"][0]["source"] == "workbench"


def test_a_saved_draft_keeps_a_status_the_user_set(client, the_job):
    client.post(f"/app/api/jobs/{the_job}/status", json={"status": "submitted"})
    client.post(f"/app/api/jobs/{the_job}/result", json={"text": reply(good_result())})
    d = client.get(f"/app/api/jobs/{the_job}").json()
    assert d["status"] == "submitted" and len(d["events"]) == 1


def test_a_rejected_paste_leaves_the_status_alone(client, the_job):
    client.post(f"/app/api/jobs/{the_job}/result", json={"text": "no json here"})
    assert client.get(f"/app/api/jobs/{the_job}").json()["status"] == "active"


# --- Per-user Drive (user-auth-6-drive) ---

@pytest.fixture
def per_user_drive(monkeypatch):
    """The real _drive_client, with DriveClient.for_user on a fake Drive; returns the user ids it was asked for."""
    from cryptography.fernet import Fernet

    from strelitzia.storage import drive as drive_mod
    monkeypatch.setattr(app_mod, "_drive_client", REAL_DRIVE_CLIENT)
    monkeypatch.setattr(drive_mod, "drive_libraries_installed", lambda: True)
    monkeypatch.setenv("STRELITZIA_TOKEN_KEY", Fernet.generate_key().decode())
    fake, asked = FakeDrive(), []

    def for_user(cls, conn, user_id):
        asked.append(user_id)
        return cls(fake, media_factory=fake_media, html_media_factory=lambda html: html.encode("utf-8"))

    monkeypatch.setattr(DriveClient, "for_user", classmethod(for_user))
    return asked


def _connect(conn, user_id):
    from datetime import UTC, datetime, timedelta

    from google.oauth2.credentials import Credentials

    from strelitzia.storage.drive import save_user_token
    save_user_token(conn, user_id, Credentials(token="t", refresh_token="r", token_uri="https://oauth2.googleapis.com/token",
                                               client_id="c", client_secret="s",
                                               expiry=datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1)))


def test_packet_uses_the_users_own_drive(client, conn, the_job, per_user_drive):
    from strelitzia import users
    owner = users.owner(conn).id
    assert client.post(f"/app/api/jobs/{the_job}/packet").json()["inline"] is True  # not connected: clipboard
    assert per_user_drive == []
    _connect(conn, owner)
    body = client.post(f"/app/api/jobs/{the_job}/packet").json()
    assert body["inline"] is False and body["doc_url"]
    assert per_user_drive == [owner]


def test_setup_reports_the_users_drive_and_the_connect_url(client, conn, per_user_drive, monkeypatch):
    from strelitzia import users
    s = client.get("/app/api/claude/setup").json()
    assert s["drive"] is False and s["drive_source"] is None and s["connect_url"] is None  # login off: no web client
    assert "Claude setup page" in s["drive_detail"]
    monkeypatch.setattr(app_mod.auth, "drive_connect_available", lambda: True)
    assert client.get("/app/api/claude/setup").json()["connect_url"] == "/app/api/drive/connect"
    _connect(conn, users.owner(conn).id)
    s = client.get("/app/api/claude/setup").json()
    assert s["drive"] is True and s["drive_source"] == "user"


def test_no_content_library_says_where_to_add_it(client, the_job, monkeypatch):
    monkeypatch.setattr(wb.Inputs, "for_user", _REAL_FOR_USER)  # the owner's profile has no content library here
    r = client.post(f"/app/api/jobs/{the_job}/packet")
    assert r.status_code == 409 and r.json()["detail"] == (
        "No content library yet. Add your content library on the Profile page.")


def test_docs_without_drive_say_where_to_connect(client):
    r = client.post("/app/api/claude/docs")
    assert r.status_code == 409 and r.json()["detail"] == (
        "Google Drive isn't set up. Connect Google Drive on the Claude setup page.")


def test_owner_without_a_token_key_keeps_the_cli_token(client, conn, tmp_path, monkeypatch):
    """A local install with only `strelitzia drive auth` (no TOKEN_KEY) still gets Drive in the dashboard."""
    from strelitzia.storage import drive as drive_mod
    monkeypatch.setattr(app_mod, "_drive_client", REAL_DRIVE_CLIENT)
    monkeypatch.setattr(drive_mod, "drive_libraries_installed", lambda: True)
    token_file = tmp_path / "google_token.json"
    token_file.write_text("{}")
    monkeypatch.setattr(config, "GOOGLE_TOKEN_PATH", token_file)
    fake = FakeDrive()
    monkeypatch.setattr(DriveClient, "from_credentials",
                        classmethod(lambda cls: cls(fake, html_media_factory=lambda html: html.encode("utf-8"))))
    s = client.get("/app/api/claude/setup").json()
    assert s["drive"] is True and s["drive_source"] == "legacy"
    assert client.post("/app/api/claude/docs").status_code == 200


def test_owner_keeps_the_cli_token_while_the_dashboard_cant_connect(client, conn, tmp_path, monkeypatch):
    """Login off (no web OAuth client) with TOKEN_KEY set and nothing stored: the CLI's token still works."""
    from cryptography.fernet import Fernet

    from strelitzia.storage import drive as drive_mod
    monkeypatch.setattr(drive_mod, "drive_libraries_installed", lambda: True)
    monkeypatch.setenv("STRELITZIA_TOKEN_KEY", Fernet.generate_key().decode())
    token_file = tmp_path / "google_token.json"
    token_file.write_text("{}")
    monkeypatch.setattr(config, "GOOGLE_TOKEN_PATH", token_file)
    s = client.get("/app/api/claude/setup").json()
    assert s["drive"] is True and s["drive_source"] == "legacy" and s["connect_url"] is None
