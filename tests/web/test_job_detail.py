"""Job detail, status buttons and PATCH edits (dashboard-api Task 4)."""

import json
from datetime import UTC, date, datetime

import pytest
from web_helpers import add_job

from applypilot.database import job_key
from applypilot.web import app as app_mod

URL = "https://boards.example.com/acme/jobs/42"
KEY = job_key(URL)
TODAY = date(2026, 10, 5)


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setattr(app_mod, "today", lambda: TODAY)


@pytest.fixture
def job(conn):
    add_job(conn, URL, title="Data Scientist", company="Acme", fit_score=8, deadline="2026-10-20",
            application_url="https://apply.example.com/42", full_description="Long description",
            description="short", details_json=json.dumps({"summary": "Models", "skills": ["python"]}),
            resume_drive_url="https://drive.google.com/file/d/r/view", job_key=KEY)
    return URL


def _detail(client, key=KEY):
    r = client.get(f"/app/api/jobs/{key}")
    assert r.status_code == 200, r.text
    return r.json()


def _status(client, status, **extra):
    return client.post(f"/app/api/jobs/{KEY}/status", json={"status": status, **extra})


def test_detail_shape(client, job):
    d = _detail(client)
    assert d["key"] == KEY and d["url"] == URL and d["title"] == "Data Scientist" and d["company"] == "Acme"
    assert d["fit_score"] == 8
    assert d["description_text"] == "Long description"
    for raw in ("full_description", "description", "details_json"):
        assert raw not in d
    assert d["details"] == {"summary": "Models", "skills": ["python"]}
    assert d["status"] == "active" and d["status_color"] == "grey"
    assert d["days_since_submitted"] is None
    assert d["events"] == []
    assert d["links"] == {
        "posting": URL, "apply": "https://apply.example.com/42", "drive_folder": None,
        "resume": "https://drive.google.com/file/d/r/view", "cover_letter": None,
    }


def test_detail_without_extraction(client, conn):
    add_job(conn, "https://x/plain", full_description=None, description="snippet")
    d = _detail(client, job_key("https://x/plain"))  # job_key backfilled on lookup
    assert d["details"] is None and d["description_text"] == "snippet"


def test_unknown_key_is_404(client, job):
    assert client.get("/app/api/jobs/doesnotexist").status_code == 404
    assert client.post("/app/api/jobs/doesnotexist/status", json={"status": "submitted"}).status_code == 404
    assert client.patch("/app/api/jobs/doesnotexist", json={"notes": "x"}).status_code == 404


def test_submit_defaults_to_now(client, job):
    r = _status(client, "submitted")
    assert r.status_code == 200
    d = r.json()
    assert d["status"] == "submitted" and d["status_color"] == "blue"
    assert d["submitted_at"][:10] == datetime.now(UTC).date().isoformat()
    assert d["responded_at"] is None


def test_submit_with_a_date_and_days_since(client, job):
    d = _status(client, "submitted", submitted_at="2026-09-30").json()
    assert d["submitted_at"] == "2026-09-30"
    assert d["days_since_submitted"] == 5


def test_manual_date_edit(client, job):
    _status(client, "submitted")
    r = client.patch(f"/app/api/jobs/{KEY}", json={"submitted_at": "2026-09-25"})
    assert r.status_code == 200
    d = r.json()
    assert d["submitted_at"] == "2026-09-25" and d["days_since_submitted"] == 10
    assert len(d["events"]) == 1  # a date edit isn't a status change
    assert client.patch(f"/app/api/jobs/{KEY}", json={"submitted_at": "yesterday"}).status_code == 422
    assert client.patch(f"/app/api/jobs/{KEY}", json={"submitted_at": None}).status_code == 422


def test_notes(client, job):
    d = client.patch(f"/app/api/jobs/{KEY}", json={"notes": "Referral from Sam"}).json()
    assert d["notes"] == "Referral from Sam"
    d = client.patch(f"/app/api/jobs/{KEY}", json={}).json()
    assert d["notes"] == "Referral from Sam"  # absent fields don't change
    assert client.patch(f"/app/api/jobs/{KEY}", json={"notes": None}).json()["notes"] is None


@pytest.mark.parametrize("status,color", [("rejected", "red"), ("heard_back", "green")])
def test_response_transitions(client, job, status, color):
    _status(client, "submitted", submitted_at="2026-09-30")
    d = _status(client, status).json()
    assert d["status"] == status and d["status_color"] == color
    assert d["responded_at"]
    assert d["submitted_at"] == "2026-09-30"
    assert d["days_since_submitted"] is None  # only shown while Submitted


def test_in_progress_and_reset(client, job, conn):
    assert _status(client, "in_progress").json()["status_color"] == "yellow"
    conn.execute("UPDATE jobs SET deadline = '2026-10-01' WHERE url = ?", (URL,))
    conn.commit()
    d = _status(client, None).json()
    assert d["user_status"] is None and d["status"] == "inactive" and d["status_color"] == "orange"


def test_bad_status_is_422(client, job):
    assert _status(client, "active").status_code == 422
    assert _status(client, "submitted", submitted_at="not a date").status_code == 422
    assert client.post(f"/app/api/jobs/{KEY}/status", json={}).status_code == 422


def test_status_needs_the_csrf_header(app, job):
    from fastapi.testclient import TestClient

    with TestClient(app) as bare:
        assert bare.post(f"/app/api/jobs/{KEY}/status", json={"status": "submitted"}).status_code == 403
        assert bare.patch(f"/app/api/jobs/{KEY}", json={"notes": "x"}).status_code == 403


def test_event_log_newest_first(client, job):
    _status(client, "in_progress")
    _status(client, "submitted")
    d = _status(client, "heard_back").json()
    assert [(e["from_status"], e["to_status"]) for e in d["events"]] == [
        ("submitted", "heard_back"), ("in_progress", "submitted"), ("active", "in_progress"),
    ]
    assert all(e["source"] == "dashboard" for e in d["events"])
