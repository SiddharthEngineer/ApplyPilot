"""Resume panel routes: the page summary, creating a run, pasting model replies, your picks and the report."""

import json

import pytest
from web_helpers import act_as, add_job, second_user

from strelitzia import users
from strelitzia.scoring import panel

N = 5


def _seed(conn, count=N):
    for no in range(1, count + 1):
        url = f"https://example.com/jobs/{no:02d}"
        add_job(conn, url, title=f"Data Engineer {no}", company="Initech",
                details_json=json.dumps({"skills_required": ["Airflow"], "skills_preferred": ["dbt"]}))
        for version in (1, 2):
            conn.execute(
                "INSERT INTO drafts (id, job_url, version, rules_version, resume_json, resume_text, cover_json, "
                "coverage_json, created_at) VALUES (?, ?, ?, ?, ?, ?, 'null', '{}', ?)",
                (f"d{no}-{version}", url, version, f"r{version}", json.dumps({"headline": f"H{no}.{version}"}),
                 f"Alex Tester\nResume {no} take {version}\n", f"2026-0{version}-01T00:00:{no:02d}+00:00"),
            )
    conn.commit()


def _reply(winners, count=None):
    grade = {"qualifications": [{"text": "Airflow", "kind": "required", "status": "met", "evidence": "Airflow"}],
             "overall": "B", "clarity": 4, "red_flags": []}
    items = [{"item": i, "first": grade, "second": grade, "winner": w, "why": "x"} for i, w in enumerate(winners, 1)]
    return "Grades below.\n```json\n" + json.dumps({"panel": 1, "items": items[:count]}) + "\n```"


@pytest.fixture
def run(client, conn):
    _seed(conn)
    res = client.post("/app/api/panel/runs", json={"n": N, "mode": "latest_vs_previous"})
    assert res.status_code == 201, res.text
    return res.json()


def test_panel_summary_counts_and_due(client, conn):
    body = client.get("/app/api/panel").json()
    assert body["due"] is None and body["needed"] == 5 and body["runs"] == []
    assert body["comparable"] == {"latest_vs_previous": 0, "rules_a_vs_b": 0}
    assert body["project_name"] == "Strelitzia Panel" and "not_met" in body["rubric"]["text"]
    assert body["rubric"]["url"] is None
    _seed(conn)
    body = client.get("/app/api/panel").json()
    assert body["due"] == "no panel has been run yet"
    assert body["comparable"] == {"latest_vs_previous": 5, "rules_a_vs_b": 5}


def test_create_run_needs_comparable_jobs(client):
    res = client.post("/app/api/panel/runs", json={"n": 5})
    assert res.status_code == 409 and "two comparable drafts" in res.json()["detail"]
    assert client.post("/app/api/panel/runs", json={"n": 0}).status_code == 422
    assert client.post("/app/api/panel/runs", json={"mode": "nope"}).status_code == 422


def test_run_is_blind_until_your_picks(client, run):
    assert run["status"] == "open" and len(run["items"]) == 5 and run["report"] is None
    assert [p["model"] for p in run["packets"]] == ["sonnet", "opus"]
    p1, p2 = run["packets"][0]["prompt"], run["packets"][1]["prompt"]
    assert "Strelitzia panel rubric" in p1 and p1 != p2
    item = run["items"][0]
    assert item["title"] == "Data Engineer 5" and item["your_pick"] is None
    assert {item["first_text"], item["second_text"]} == {"Alex Tester\nResume 5 take 1\n", "Alex Tester\nResume 5 take 2\n"}
    assert p1.index(item["first_text"].strip()) < p1.index(item["second_text"].strip())
    assert p2.index(item["second_text"].strip()) < p2.index(item["first_text"].strip())
    assert "older" not in json.dumps(run["items"]) and "newer" not in json.dumps(run["items"])
    assert client.get(f"/app/api/panel/runs/{run['id']}").json()["id"] == run["id"]
    assert client.get("/app/api/panel/runs/nope").status_code == 404
    assert client.get("/app/api/panel").json()["runs"][0]["id"] == run["id"]


def test_model_replies_picks_and_report(client, run):
    url = f"/app/api/panel/runs/{run['id']}/grades"
    res = client.post(url, json={"grader": "sonnet", "text": _reply(["first"] * 5)})
    assert res.status_code == 200 and res.json()["graded"] == 5
    assert res.json()["run"]["graded"] == {"sonnet": 5, "opus": 0, "you": 0}
    assert client.post(url, json={"grader": "opus", "text": _reply(["second"] * 5)}).status_code == 200
    for no in range(1, 6):
        res = client.post(url, json={"grader": "you", "item": no, "winner": "first"})
        assert res.status_code == 200
    out = res.json()["run"]
    assert out["status"] == "complete" and out["items"][0]["your_pick"] == "first"
    locked = client.post(url, json={"grader": "you", "item": 1, "winner": "second"})
    assert locked.status_code == 409 and "locked" in locked.json()["detail"]
    report = out["report"]
    # Opus saw the swapped packet and also picked the version you saw first.
    assert report["agreement"] == {"sonnet_vs_opus": 1.0, "sonnet_vs_you": 1.0, "opus_vs_you": 1.0}
    assert report["verdict"] in ("newer is better", "no clear gain", "worse")
    assert report["items"][0]["winners"]["you"] in ("newer", "older")
    assert client.get("/app/api/panel").json()["due"] is None


def test_reply_with_the_wrong_item_count_is_an_error(client, conn, run):
    url = f"/app/api/panel/runs/{run['id']}/grades"
    res = client.post(url, json={"grader": "sonnet", "text": _reply(["first"] * 5, count=4)})
    assert res.status_code == 422
    assert "grades 4 items, but this panel has 5" in res.json()["detail"] and res.json()["errors"]
    assert client.post(url, json={"grader": "opus", "text": "no json"}).status_code == 422
    assert client.post(url, json={"grader": "you", "item": 1}).status_code == 422
    assert client.post(url, json={"grader": "you", "item": 9, "winner": "tie"}).status_code == 404
    assert client.post(url, json={"grader": "gpt", "text": "x"}).status_code == 422
    assert client.post("/app/api/panel/runs/nope/grades", json={"grader": "sonnet", "text": "x"}).status_code == 404
    assert panel.grades(conn, users.owner(conn).id, run["id"]) == {}


def test_another_users_panel_run_is_404(client, conn, monkeypatch):
    _seed(conn)
    run = client.post("/app/api/panel/runs", json={"n": N}).json()
    act_as(monkeypatch, second_user(conn))
    assert client.get(f"/app/api/panel/runs/{run['id']}").status_code == 404
    res = client.post(f"/app/api/panel/runs/{run['id']}/grades", json={"grader": "you", "item": 1, "winner": "tie"})
    assert res.status_code == 404
    page = client.get("/app/api/panel").json()
    assert page["runs"] == [] and page["comparable"] == {"latest_vs_previous": 0, "rules_a_vs_b": 0}
    assert client.post("/app/api/panel/runs", json={"n": N}).status_code == 409
    assert panel.grades(conn, users.owner(conn).id, run["id"]) == {}
