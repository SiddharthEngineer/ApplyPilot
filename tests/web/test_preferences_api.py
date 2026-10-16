"""Preferences, re-assessment preview and rescore API (fit-preferences Task 3). Synthetic preferences and jobs."""

import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from fit_helpers import POSTING, add_extracted_job, reply

from strelitzia import config
from strelitzia.database import get_connection, job_key
from strelitzia.scoring import scorer
from strelitzia.scoring.assessment import current_assessment_key, parse_assessment, recompute_fit
from strelitzia.scoring.preferences import Preferences
from strelitzia.web import tasks
from strelitzia.web.app import create_app

HEADERS = {"X-Strelitzia": "1"}
LOW_PAY = {**POSTING, "salary_min": 60000, "salary_max": 70000}
URLS = ["https://e.com/well-paid", "https://e.com/low-pay", "https://e.com/other"]


@pytest.fixture(autouse=True)
def resume(tmp_path, monkeypatch):
    path = tmp_path / "resume.txt"
    path.write_text("Synthetic resume: Python, SQL.")
    monkeypatch.setattr(config, "RESUME_PATH", path)
    monkeypatch.delenv("LLM_MONTHLY_BUDGET_USD", raising=False)
    return path


@pytest.fixture
def client(db_path):
    """The app with tasks queued but not run (no LLM work happens in these tests)."""
    with TestClient(create_app(db=db_path, run_tasks=False), headers=HEADERS) as c:
        yield c


@pytest.fixture
def conn(client, db_path):
    c = get_connection(db_path)
    key = current_assessment_key(c)
    for url in URLS:
        add_extracted_job(c, url, job_key=job_key(url), posting=LOW_PAY if "low" in url else POSTING,
                          fit_assessment=json.dumps(parse_assessment(reply(skills=4))), assessment_hash=key)
    recompute_fit(c)
    return c


def _prefs(client, **changes) -> dict:
    prefs = client.get("/app/api/preferences").json()["prefs"]
    prefs.update(changes)
    return prefs


def _jobs(client, **params) -> dict:
    return {i["key"]: i for i in client.get("/app/api/jobs", params=params).json()["items"]}


def _usage_rows(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM llm_usage").fetchone()[0]


SALARY_DEALBREAKER = {"importance": "dealbreaker", "value": {"min": 80000, "currency": "USD", "period": "year"}}


def test_get_defaults_and_options(client):
    body = client.get("/app/api/preferences").json()
    prefs = body["prefs"]
    assert {k for k, v in prefs.items() if isinstance(v, dict) and v["importance"] != "dont_care"} == \
        {"salary", "locations", "role_category", "skills_match"}
    assert "remote" in body["options"]["work_mode"] and "data_science" in body["options"]["role_category"]
    assert body["options"]["importance"][-1] == "dealbreaker" and "mostly_sales" in body["options"]["red_flags"]
    assert body["labels"]["role_category"] == "Job field"


def test_preview_salary_change_needs_no_reassessment(client, conn):
    r = client.post("/app/api/preferences/preview", json={"prefs": _prefs(client, salary=SALARY_DEALBREAKER)})
    assert r.json() == {"reassess_needed": False, "jobs_to_reassess": 0, "estimated_usd": 0.0}


def test_preview_skills_change_counts_every_job(client, conn):
    with patch.object(scorer, "estimate_cost", return_value=0.01) as est:
        r = client.post("/app/api/preferences/preview", json={"prefs": _prefs(client, skills_want=["Rust"])})
    assert r.json() == {"reassess_needed": True, "jobs_to_reassess": 3, "estimated_usd": 0.01}
    assert est.call_args.kwargs == {"batch": True}
    assert "Rust" in est.call_args.args[1][0]["system"][1]["text"]
    # Nothing was saved.
    assert client.get("/app/api/preferences").json()["prefs"]["skills_want"] == []


def test_salary_dealbreaker_reranks_now_without_llm_calls(client, conn):
    low = job_key(URLS[1])
    before = _jobs(client)[low]["fit_score"]
    r = client.put("/app/api/preferences", json={"prefs": _prefs(client, salary=SALARY_DEALBREAKER)})
    assert r.status_code == 200 and r.json()["rescored"] == 3 and r.json()["task"] is None
    assert low not in _jobs(client)  # flagged jobs are hidden by default
    shown = _jobs(client, hide_flagged=False)[low]
    assert shown["fit_flags"] == ["salary"] and "salary below minimum" in shown["flag_reasons"][0]
    assert shown["fit_score"] != before
    assert _usage_rows(conn) == 0
    assert client.get("/app/api/facets").json()["flagged"] == 1
    detail = client.get(f"/app/api/jobs/{low}").json()
    assert detail["fit_flags"] == ["salary"] and detail["fit_assessment"]["skills_match"]["score"] == 4
    assert any(c["key"] == "salary" and c["dealbreaker_hit"] for c in detail["fit_breakdown"])


def test_put_without_confirm_leaves_assessments_alone(client, conn):
    hashes = dict(conn.execute("SELECT url, assessment_hash FROM jobs").fetchall())
    r = client.put("/app/api/preferences", json={"prefs": _prefs(client, skills_want=["Rust"])})
    assert r.status_code == 200 and r.json()["task"] is None
    assert dict(conn.execute("SELECT url, assessment_hash FROM jobs").fetchall()) == hashes
    assert all(job["assessment_stale"] for job in _jobs(client).values())
    assert client.get(f"/app/api/jobs/{job_key(URLS[0])}").json()["assessment_stale"] is True
    assert conn.execute("SELECT COUNT(*) FROM dashboard_tasks").fetchone()[0] == 0


def test_put_with_confirm_queues_one_reassessment(client, conn):
    body = {"prefs": _prefs(client, skills_want=["Rust"]), "confirm_reassess": True}
    with patch.object(scorer, "estimate_cost", return_value=0.01):
        task = client.put("/app/api/preferences", json=body).json()["task"]
        again = client.put("/app/api/preferences", json=body).json()["task"]
    assert task["kind"] == "reassess_all" and task["state"] == "queued" and task["url"] is None
    assert again["id"] == task["id"]  # still stale until the task runs: the queued task is reused
    assert conn.execute("SELECT COUNT(*) FROM dashboard_tasks").fetchone()[0] == 1
    assert Preferences.from_dict(client.get("/app/api/preferences").json()["prefs"]).skills_want == ["Rust"]


def test_confirm_without_input_change_queues_nothing(client, conn):
    r = client.put("/app/api/preferences", json={"prefs": _prefs(client, salary=SALARY_DEALBREAKER),
                                                 "confirm_reassess": True})
    assert r.json()["task"] is None


def _spend(conn, usd):
    conn.execute("INSERT INTO llm_usage (id, ts, purpose, model, batch, cost_usd) VALUES ('x', ?, 'scoring', "
                 "'claude-haiku-5-5', 0, ?)", ("2099-01-01T00:00:00", usd))  # always in "this month" or later
    conn.commit()


def test_budget_stop_returns_402_and_saves_nothing(client, conn, monkeypatch):
    monkeypatch.setenv("LLM_MONTHLY_BUDGET_USD", "5")
    _spend(conn, 4.99)
    body = {"prefs": _prefs(client, skills_want=["Rust"]), "confirm_reassess": True}
    with patch.object(scorer, "estimate_cost", return_value=0.40):
        r = client.put("/app/api/preferences", json=body)
    assert r.status_code == 402
    detail = r.json()["detail"]
    assert detail["month_spend"] == 4.99 and detail["estimated_usd"] == 0.4
    assert "budget" in detail["message"].lower()
    assert client.get("/app/api/preferences").json()["prefs"]["skills_want"] == []
    assert conn.execute("SELECT COUNT(*) FROM dashboard_tasks").fetchone()[0] == 0
    # Saving without re-assessing still works.
    assert client.put("/app/api/preferences", json={**body, "confirm_reassess": False}).status_code == 200

    _spend_more = client.post(f"/app/api/jobs/{job_key(URLS[0])}/rescore")
    assert _spend_more.status_code == 200  # 4.99 < 5: a single job may still run
    conn.execute("UPDATE llm_usage SET cost_usd = 5")
    conn.commit()
    assert client.post(f"/app/api/jobs/{job_key(URLS[0])}/rescore").status_code == 402


def test_rescore_queues_a_task(client, conn):
    r = client.post(f"/app/api/jobs/{job_key(URLS[0])}/rescore")
    task = r.json()
    assert task["kind"] == "rescore" and task["url"] == URLS[0] and task["key"] == job_key(URLS[0])
    assert client.get(f"/app/api/tasks/{task['id']}").json()["kind"] == "rescore"
    # Rescoring doesn't touch the application status the way generation does.
    assert client.get(f"/app/api/jobs/{job_key(URLS[0])}").json()["status"] != "in_progress"
    assert client.post("/app/api/jobs/nope/rescore").status_code == 404


def test_score_filter_is_0_to_100(client, conn):
    assert client.get("/app/api/jobs", params={"score_min": 70, "score_max": 100}).status_code == 200
    assert client.get("/app/api/jobs", params={"score_max": 101}).status_code == 422


@pytest.mark.parametrize(("kind", "url", "stats", "error"), [
    ("rescore", URLS[0], {"scored": 1, "errors": 0}, None),
    ("rescore", URLS[0], {"scored": 0, "errors": 1}, "failed"),
    ("rescore", URLS[0], {"scored": 0, "errors": 0}, "extracted"),
    ("reassess_all", None, {"scored": 3, "errors": 0}, None),
    ("reassess_all", None, {"scored": 0, "errors": 0, "stopped": "budget"}, "budget"),
])
def test_assess_task(kind, url, stats, error):
    with patch.object(scorer, "run_scoring", return_value=stats) as run:
        if error:
            with pytest.raises((tasks.GenerationFailed, tasks.LLMStopRun)) as e:
                tasks.generate_for_job("u1", url, kind)
            assert error in (str(e.value) + getattr(e.value, "reason", ""))
        else:
            tasks.generate_for_job("u1", url, kind)
    if kind == "rescore":
        run.assert_called_once_with(urls=[url], mode="sync", prefilter=False, user_id="u1")
    else:
        run.assert_called_once_with(mode="batch", reassess=True, user_id="u1")


def test_enqueue_checks_the_job_argument(conn):
    with pytest.raises(ValueError):
        tasks.enqueue(conn, "u1", None, "rescore")
    with pytest.raises(ValueError):
        tasks.enqueue(conn, "u1", URLS[0], "reassess_all")


def test_stale_jobs_can_be_reassessed_from_any_save(client, conn):
    client.put("/app/api/preferences", json={"prefs": _prefs(client, skills_want=["Rust"])})  # without re-assessing
    with patch.object(scorer, "estimate_cost", return_value=0.01):
        r = client.post("/app/api/preferences/preview", json={"prefs": _prefs(client, salary=SALARY_DEALBREAKER)})
    assert r.json()["reassess_needed"] is True and r.json()["jobs_to_reassess"] == 3


def test_preview_leaves_out_titles_the_prefilter_skips(client, conn):
    with patch.object(scorer, "estimate_cost", return_value=0.01), \
            patch.object(scorer, "load_search_config", return_value={}), \
            patch.object(scorer, "load_profile", return_value={}):
        r = client.post("/app/api/preferences/preview",
                        json={"prefs": _prefs(client, target_roles=["Registered Nurse"])})
    assert r.json() == {"reassess_needed": False, "jobs_to_reassess": 0, "estimated_usd": 0.0}


def test_a_reassessment_with_some_errors_still_succeeds():
    with patch.object(scorer, "run_scoring", return_value={"scored": 99, "errors": 1}):
        tasks.generate_for_job("u1", None, "reassess_all")
