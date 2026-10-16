"""Add job, pasted descriptions, contacts and interviews over the API (workspace-intake Task 3)."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from web_helpers import act_as, add_job, second_user

from strelitzia import users
from strelitzia.database import job_key
from strelitzia.web.app import create_app

HEADERS = {"X-Strelitzia": "1"}

DESC = "We are hiring a data scientist to build forecasting models for our retail business. " * 4


@pytest.fixture(autouse=True)
def no_budget(monkeypatch):
    monkeypatch.delenv("LLM_MONTHLY_BUDGET_USD", raising=False)


@pytest.fixture
def client(db_path):
    """Tasks are queued but never run (no scraping or LLM work in these tests)."""
    with TestClient(create_app(db=db_path, run_tasks=False), headers=HEADERS) as c:
        yield c


def _tasks(conn):
    return [tuple(r) for r in conn.execute("SELECT url, kind, state FROM dashboard_tasks").fetchall()]


def _iso(days: float) -> str:
    return (datetime.now(UTC) + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


# --- Add job ---

def test_add_job_by_link_queues_intake_and_dedupes(client, conn):
    r = client.post("/app/api/jobs", json={"url": "https://boards.greenhouse.io/acme/jobs/1?utm_source=li"})
    assert r.status_code == 201, r.text
    body = r.json()
    url = "https://boards.greenhouse.io/acme/jobs/1"
    assert body["created"] is True and body["key"] == job_key(url)
    assert body["task"]["kind"] == "intake" and body["task"]["state"] == "queued"
    assert _tasks(conn) == [(url, "intake", "queued")]

    job = client.get(f"/app/api/jobs/{body['key']}").json()
    assert (job["source"], job["intake_status"], job["intake_error"]) == ("manual", "queued", None)
    assert job["added_at"] and job["contacts"] == [] and job["interviews"] == []

    again = client.post("/app/api/jobs", json={"url": url + "#apply"})
    assert again.status_code == 200 and again.json()["created"] is False and again.json()["key"] == body["key"]
    assert len(_tasks(conn)) == 1  # the queued intake task is reused

    conn.execute("UPDATE dashboard_tasks SET state = 'running'")
    conn.execute("UPDATE jobs SET intake_status = 'scraping'")
    conn.commit()
    running = client.post("/app/api/jobs", json={"url": url}).json()
    assert running["task"]["id"] == body["task"]["id"] and len(_tasks(conn)) == 1  # nor is a running one duplicated
    assert client.get(f"/app/api/jobs/{body['key']}").json()["intake_status"] == "scraping"


def test_readding_a_scored_job_queues_nothing(client, conn):
    add_job(conn, "https://x/scored", fit_score=80, details_json="{}", fit_flags='["prefilter"]')
    r = client.post("/app/api/jobs", json={"url": "https://x/scored"})
    assert r.status_code == 200 and r.json() == {"key": job_key("https://x/scored"), "created": False, "task": None}
    assert _tasks(conn) == []
    assert client.get(f"/app/api/jobs/{job_key('https://x/scored')}").json()["source"] == "manual"


def test_add_job_by_paste(client, conn):
    r = client.post("/app/api/jobs", json={"title": "T", "company": "C", "description": DESC})
    assert r.status_code == 201
    key = r.json()["key"]
    url = _tasks(conn)[0][0]
    assert url.startswith("manual://") and job_key(url) == key
    job = client.get(f"/app/api/jobs/{key}").json()
    assert (job["title"], job["company"], job["description_text"]) == ("T", "C", DESC.strip())


@pytest.mark.parametrize("body", [{}, {"title": "T"}, {"url": "  ", "description": " "}, {"url": "mailto:x@y.z"},
                                  {"description": "hi"}, {"url": "manual://" + "0" * 32}])
def test_add_job_needs_a_link_or_description(client, body):
    assert client.post("/app/api/jobs", json=body).status_code == 422


def test_add_job_over_budget_is_402(client, conn, monkeypatch):
    add_job(conn, "https://x/scored", fit_score=80, details_json="{}")
    monkeypatch.setenv("LLM_MONTHLY_BUDGET_USD", "1")
    conn.execute("INSERT INTO llm_usage (id, ts, purpose, cost_usd) VALUES ('u1', ?, 'extract', 2.0)",
                 (datetime.now(UTC).isoformat(),))
    conn.commit()
    assert client.post("/app/api/jobs", json={"url": "https://x/new"}).status_code == 402
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
    assert client.post("/app/api/jobs", json={"url": "https://x/scored"}).status_code == 200  # no LLM step


def test_put_description(client, conn):
    key = client.post("/app/api/jobs", json={"url": "https://www.linkedin.com/jobs/view/42/"}).json()["key"]
    conn.execute("UPDATE jobs SET intake_status = 'needs_paste', intake_error = 'sign-in page'")
    conn.execute("UPDATE dashboard_tasks SET state = 'done'")
    conn.commit()

    assert client.put(f"/app/api/jobs/{key}/description", json={"description": "too short"}).status_code == 422
    r = client.put(f"/app/api/jobs/{key}/description", json={"title": "ML Engineer", "description": DESC})
    assert r.status_code == 200 and r.json()["task"]["kind"] == "intake" and r.json()["task"]["state"] == "queued"
    job = client.get(f"/app/api/jobs/{key}").json()
    assert (job["title"], job["intake_status"], job["intake_error"]) == ("ML Engineer", "queued", None)
    assert job["description_text"] == DESC.strip()
    assert client.put("/app/api/jobs/nope/description", json={"description": DESC}).status_code == 404


# --- Contacts ---

def test_contacts_crud_and_links(client, conn):
    add_job(conn, "https://x/1", title="Data Scientist")
    add_job(conn, "https://x/2", title="ML Engineer")
    k1, k2 = job_key("https://x/1"), job_key("https://x/2")

    r = client.post(f"/app/api/jobs/{k1}/contacts",
                    json={"contact": {"name": "Ana Ruiz", "company": "Acme", "email": "ana@acme.com"},
                          "relation": "recruiter"})
    assert r.status_code == 200
    [ana] = r.json()["contacts"]
    assert (ana["name"], ana["relation"]) == ("Ana Ruiz", "recruiter")

    found = client.get("/app/api/contacts", params={"q": "an"}).json()
    assert [(c["name"], c["job_count"]) for c in found] == [("Ana Ruiz", 1)]
    r = client.post(f"/app/api/jobs/{k2}/contacts", json={"contact_id": ana["id"], "relation": "referral"})
    assert r.json()["contacts"][0]["relation"] == "referral"

    r = client.patch(f"/app/api/contacts/{ana['id']}", json={"phone": "+1 555 0100"})
    assert r.status_code == 200 and r.json()["phone"] == "+1 555 0100"
    assert sorted(j["key"] for j in r.json()["jobs"]) == sorted([k1, k2])
    assert client.get(f"/app/api/jobs/{k2}").json()["contacts"][0]["phone"] == "+1 555 0100"

    other = client.post("/app/api/contacts", json={"name": "Bo"})
    assert other.status_code == 201 and other.json()["name"] == "Bo"
    assert client.get(f"/app/api/contacts/{other.json()['id']}").json()["jobs"] == []

    assert client.delete(f"/app/api/jobs/{k1}/contacts/{ana['id']}").json()["contacts"] == []
    assert client.delete(f"/app/api/contacts/{ana['id']}").json() == {"ok": True}
    assert client.get(f"/app/api/jobs/{k2}").json()["contacts"] == []


def test_contact_errors(client, conn):
    add_job(conn, "https://x/1")
    k1 = job_key("https://x/1")
    assert client.post("/app/api/contacts", json={"name": ""}).status_code == 422
    assert client.get("/app/api/contacts/missing").status_code == 404
    assert client.patch("/app/api/contacts/missing", json={"name": "X"}).status_code == 404
    assert client.delete("/app/api/contacts/missing").status_code == 404
    assert client.post(f"/app/api/jobs/{k1}/contacts", json={"contact_id": "missing"}).status_code == 404
    assert client.post(f"/app/api/jobs/{k1}/contacts", json={"relation": "other"}).status_code == 422
    r = client.post(f"/app/api/jobs/{k1}/contacts", json={"contact": {"name": "A"}, "relation": "boss"})
    assert r.status_code == 422
    assert client.post("/app/api/jobs/nope/contacts", json={"contact": {"name": "A"}}).status_code == 404


# --- Interviews ---

def test_interviews_crud(client, conn):
    add_job(conn, "https://x/1")
    k1 = job_key("https://x/1")
    r = client.post(f"/app/api/jobs/{k1}/interviews",
                    json={"scheduled_at": "2026-11-02T15:00:00.000Z", "stage": "onsite", "interviewers": "Ana, Bo"})
    assert r.status_code == 201
    iv = r.json()
    assert (iv["scheduled_at"], iv["outcome"]) == ("2026-11-02T15:00:00.000Z", "pending")

    r = client.patch(f"/app/api/interviews/{iv['id']}", json={"prep_notes": "Line 1\nLine 2", "outcome": "passed"})
    assert r.status_code == 200 and r.json()["prep_notes"] == "Line 1\nLine 2" and r.json()["stage"] == "onsite"
    assert client.get(f"/app/api/jobs/{k1}").json()["interviews"][0]["outcome"] == "passed"

    assert client.patch(f"/app/api/interviews/{iv['id']}", json={"outcome": "maybe"}).status_code == 422
    assert client.patch(f"/app/api/interviews/{iv['id']}", json={"scheduled_at": "next tuesday"}).status_code == 422
    assert client.patch("/app/api/interviews/missing", json={"stage": "x"}).status_code == 404
    assert client.post("/app/api/jobs/nope/interviews", json={}).status_code == 404

    assert client.delete(f"/app/api/interviews/{iv['id']}").json() == {"ok": True}
    assert client.delete(f"/app/api/interviews/{iv['id']}").status_code == 404
    assert client.get(f"/app/api/jobs/{k1}").json()["interviews"] == []


def test_another_users_job_contacts_and_interviews_are_404(client, conn, monkeypatch):
    url = "https://x/1"
    add_job(conn, url, title="Mine", location="Lisbon")
    key = job_key(url)
    ana = client.post(f"/app/api/jobs/{key}/contacts", json={"contact": {"name": "Ana"}, "relation": "recruiter"}
                      ).json()["contacts"][0]
    iv = client.post(f"/app/api/jobs/{key}/interviews", json={"stage": "phone"}).json()
    client.post(f"/app/api/jobs/{key}/status", json={"status": "in_progress"})

    bo = second_user(conn)
    act_as(monkeypatch, bo)
    assert client.get(f"/app/api/jobs/{key}").status_code == 404
    assert client.post(f"/app/api/jobs/{key}/status", json={"status": "submitted"}).status_code == 404
    assert client.patch(f"/app/api/jobs/{key}", json={"notes": "x"}).status_code == 404
    assert client.post(f"/app/api/jobs/{key}/interviews", json={}).status_code == 404
    assert client.get(f"/app/api/contacts/{ana['id']}").status_code == 404
    assert client.patch(f"/app/api/contacts/{ana['id']}", json={"name": "Mine"}).status_code == 404
    assert client.delete(f"/app/api/contacts/{ana['id']}").status_code == 404
    assert client.patch(f"/app/api/interviews/{iv['id']}", json={"outcome": "rejected"}).status_code == 404
    assert client.delete(f"/app/api/interviews/{iv['id']}").status_code == 404
    assert client.get("/app/api/contacts").json() == []
    assert _list(client) == []
    facets = client.get("/app/api/facets").json()
    assert facets["total"] == 0 and facets["location"] == []

    # Bo's own row for the same posting shares the key but none of the workspace, and can't take Ana.
    add_job(conn, url, title="Theirs", user_id=bo.id)
    job = client.get(f"/app/api/jobs/{key}").json()
    assert (job["title"], job["contacts"], job["interviews"], job["events"]) == ("Theirs", [], [], [])
    assert client.post(f"/app/api/jobs/{key}/contacts",
                       json={"contact_id": ana["id"], "relation": "referral"}).status_code == 404
    assert client.delete(f"/app/api/jobs/{key}/contacts/{ana['id']}").status_code == 200
    assert [j["title"] for j in _list(client)] == ["Theirs"]

    act_as(monkeypatch, users.owner(conn))
    job = client.get(f"/app/api/jobs/{key}").json()
    assert job["title"] == "Mine" and job["status"] == "in_progress"
    assert [c["name"] for c in job["contacts"]] == ["Ana"] and [i["id"] for i in job["interviews"]] == [iv["id"]]
    assert client.get("/app/api/facets").json()["total"] == 1


def test_writes_need_the_csrf_header(db_path, conn):
    add_job(conn, "https://x/1")
    k1 = job_key("https://x/1")
    with TestClient(create_app(db=db_path, run_tasks=False)) as bare:
        for method, path in [("post", "/app/api/jobs"), ("put", f"/app/api/jobs/{k1}/description"),
                             ("post", "/app/api/contacts"), ("patch", "/app/api/contacts/x"),
                             ("delete", "/app/api/contacts/x"), ("post", f"/app/api/jobs/{k1}/contacts"),
                             ("delete", f"/app/api/jobs/{k1}/contacts/x"), ("post", f"/app/api/jobs/{k1}/interviews"),
                             ("patch", "/app/api/interviews/x"), ("delete", "/app/api/interviews/x")]:
            kwargs = {} if method == "delete" else {"json": {}}
            assert getattr(bare, method)(path, **kwargs).status_code == 403, (method, path)
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1


# --- Jobs list: source filter, next interview, flagged manual rows ---

def _list(client, **params):
    r = client.get("/app/api/jobs", params=params)
    assert r.status_code == 200, r.text
    return r.json()["items"]


def test_source_filter_and_facets(client, conn):
    add_job(conn, "https://x/old")  # source NULL = discovered
    add_job(conn, "https://x/disc", source="discovered")
    add_job(conn, "https://x/mine", source="manual", added_at="2026-10-10T00:00:00+00:00")
    keys = lambda items: sorted(i["key"] for i in items)  # noqa: E731
    assert keys(_list(client, source="manual")) == [job_key("https://x/mine")]
    assert keys(_list(client, source="discovered")) == sorted([job_key("https://x/old"), job_key("https://x/disc")])
    assert len(_list(client, source=["manual", "discovered"])) == 3
    mine = _list(client, source="manual")[0]
    assert (mine["source"], mine["added_at"]) == ("manual", "2026-10-10T00:00:00+00:00")
    assert client.get("/app/api/jobs", params={"source": "bogus"}).status_code == 422
    facets = client.get("/app/api/facets").json()
    assert facets["source"] == [{"value": "manual", "count": 1}, {"value": "discovered", "count": 2}]


def test_manual_jobs_are_never_hidden_by_flags(client, conn):
    add_job(conn, "https://x/flagged", fit_flags='["prefilter"]')
    add_job(conn, "https://x/mine-flagged", fit_flags='["prefilter"]', source="manual")
    assert [i["key"] for i in _list(client, hide_flagged="true")] == [job_key("https://x/mine-flagged")]
    assert len(_list(client, hide_flagged="false")) == 2
    assert client.get("/app/api/facets").json()["flagged"] == 1  # only the one hide_flagged hides


def test_next_interview_sort_nulls_last(client, conn):
    for name in ("none", "soon", "later", "past"):
        add_job(conn, f"https://x/{name}")
    when = {"soon": _iso(2), "later": _iso(10), "past": _iso(-3)}
    for name, at in when.items():
        client.post(f"/app/api/jobs/{job_key(f'https://x/{name}')}/interviews", json={"scheduled_at": at})
    client.post(f"/app/api/jobs/{job_key('https://x/later')}/interviews", json={"scheduled_at": _iso(-1)})

    names = {job_key(f"https://x/{n}"): n for n in ("none", "soon", "later", "past")}
    asc = _list(client, sort="next_interview")
    assert [names[i["key"]] for i in asc][:2] == ["soon", "later"]
    assert {names[i["key"]] for i in asc[2:]} == {"none", "past"}
    desc = _list(client, sort="-next_interview")
    assert [names[i["key"]] for i in desc][:2] == ["later", "soon"]
    assert next(i for i in asc if names[i["key"]] == "soon")["next_interview"] == when["soon"]
    assert next(i for i in asc if names[i["key"]] == "past")["next_interview"] is None
