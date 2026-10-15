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

URL = job()["url"]
KEY = job_key(URL)


@pytest.fixture(autouse=True)
def _workbench(monkeypatch, tmp_path):
    monkeypatch.setattr(pdf_mod, "render_pdf", fake_render_pdf)
    monkeypatch.setattr(wb.Inputs, "load", classmethod(lambda cls: inputs()))
    monkeypatch.setattr(config, "TAILORED_DIR", tmp_path / "tailored")
    monkeypatch.setattr(config, "COVER_LETTER_DIR", tmp_path / "cover")
    monkeypatch.setattr(app_mod, "_drive_client", lambda: None)
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
    monkeypatch.setattr(app_mod, "_drive_client", lambda: client)
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
    def broken():
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
