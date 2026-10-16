"""Preferences, re-assessment preview and rescore API (fit-preferences Task 3). Synthetic preferences and jobs."""

import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from fit_helpers import POSTING, add_extracted_job, reply

from strelitzia import config
from strelitzia.database import get_connection, init_db, job_key
from strelitzia.scoring import scorer
from strelitzia.scoring.assessment import current_assessment_key, parse_assessment, recompute_fit
from strelitzia.scoring.preferences import Preferences
from strelitzia.web import tasks
from strelitzia.web.app import create_app
from strelitzia.profile import UserProfile

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
        {"salary", "locations", "role_category"}
    assert "remote" in body["options"]["work_mode"] and "data_science" in body["options"]["role_category"]
    assert body["options"]["importance"][-1] == "dealbreaker"
    flags = {f["value"]: f for f in body["options"]["red_flags"]}
    assert "other" not in flags and flags["mostly_sales"]["label"] == "Mostly sales" and flags["mostly_sales"]["help"]
    assert set(prefs["red_flags_counted"]) == set(flags) - {"relocation_required"}
    for removed in ("company_blocklist", "education", "experience", "skills_match", "experience_match",
                    "domain_interest", "red_flags"):
        assert removed not in prefs and removed not in body["labels"]
    assert body["labels"]["role_category"] == "Job field"


def test_roles_by_field_and_custom_target_roles(client):
    options = client.get("/app/api/preferences").json()["options"]
    roles = options["roles_by_field"]
    assert set(roles) == set(options["role_category"]) - {"other"}
    assert "marketing" in options["role_category"] and "Data Scientist" in roles["data_science"]
    # target_roles stays a plain list: titles from the map and custom ones are both kept.
    prefs = client.get("/app/api/preferences").json()["prefs"]
    prefs["target_roles"] = ["Data Scientist", "Chief Vibes Officer"]
    assert client.put("/app/api/preferences", json={"prefs": prefs}).status_code == 200
    assert client.get("/app/api/preferences").json()["prefs"]["target_roles"] == ["Data Scientist", "Chief Vibes Officer"]


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
            patch.object(scorer, "load_profile", return_value=UserProfile()):
        r = client.post("/app/api/preferences/preview",
                        json={"prefs": _prefs(client, target_roles=["Registered Nurse"])})
    assert r.json() == {"reassess_needed": False, "jobs_to_reassess": 0, "estimated_usd": 0.0}


def test_a_reassessment_with_some_errors_still_succeeds():
    with patch.object(scorer, "run_scoring", return_value={"scored": 99, "errors": 1}):
        tasks.generate_for_job("u1", None, "reassess_all")


def test_saving_preferences_reranks_none_of_another_users_jobs(client, conn):
    """The owner's save re-ranks, previews and re-assesses the owner's jobs only."""
    from strelitzia import users

    other = users.invite(conn, "b@example.com").id
    add_extracted_job(conn, URLS[1], user_id=other, job_key=job_key(URLS[1]), posting=LOW_PAY,
                      fit_assessment=json.dumps(parse_assessment(reply(skills=4))), assessment_hash="1:old")
    recompute_fit(conn, other)
    before = conn.execute("SELECT fit_score, fit_flags FROM jobs WHERE user_id = ?", (other,)).fetchone()[:]
    with patch.object(scorer, "estimate_cost", return_value=0.01):
        preview = client.post("/app/api/preferences/preview", json={"prefs": _prefs(client, skills_want=["Rust"])})
    assert preview.json()["jobs_to_reassess"] == 3  # not B's stale job
    r = client.put("/app/api/preferences", json={"prefs": _prefs(client, salary=SALARY_DEALBREAKER)})
    assert r.json()["rescored"] == 3
    assert conn.execute("SELECT fit_score, fit_flags FROM jobs WHERE user_id = ?", (other,)).fetchone()[:] == before
    assert before[1] is None  # B has no salary dealbreaker
    assert Preferences.from_dict(json.loads(conn.execute(
        "SELECT prefs_json FROM user_preferences WHERE user_id = ?", (users.owner(conn).id,)).fetchone()[0])).salary \
        .importance == "dealbreaker"
    assert conn.execute("SELECT 1 FROM user_preferences WHERE user_id = ?", (other,)).fetchone() is None


def test_budget_stop_holds_a_user_to_their_cap(conn):
    from fastapi import HTTPException

    from strelitzia import users
    from strelitzia.web.app import budget_stop

    alice = users.invite(conn, "alice@example.com").id  # $5 cap
    _spend(conn, 5.0)  # the owner's spend (a NULL user)
    budget_stop(conn, alice, 4.0)  # someone else's spend doesn't count
    conn.execute("INSERT INTO llm_usage (id, ts, purpose, model, batch, cost_usd, user_id) VALUES ('a', ?, 'scoring', "
                 "'claude-haiku-5-5', 0, 4.5, ?)", ("2099-01-01T00:00:00", alice))
    conn.commit()
    with pytest.raises(HTTPException) as e:
        budget_stop(conn, alice, 1.0)
    assert e.value.status_code == 402 and e.value.detail["month_spend"] == 4.5  # alice's own spend
    assert "your cap of $5.00" in e.value.detail["message"]
    budget_stop(conn, users.owner(conn).id, 1.0)  # the owner has no cap


def test_rescore_all_preview_and_queue(client, conn):
    conn.execute("UPDATE jobs SET user_status = 'rejected' WHERE url = ?", (URLS[2],))
    add_extracted_job(conn, "https://e.com/unextracted", job_key=job_key("https://e.com/unextracted"),
                      details_json=None)
    conn.commit()
    with patch.object(scorer, "estimate_cost", return_value=0.01):
        preview = client.get("/app/api/rescore/preview").json()
        assert preview == {"jobs": 2, "skipped_unextracted": 1, "estimated_usd": 0.01}  # assessments are current
        assert client.post("/app/api/rescore", json={}).status_code == 400
        assert conn.execute("SELECT COUNT(*) FROM dashboard_tasks").fetchone()[0] == 0
        r = client.post("/app/api/rescore", json={"confirm": True})
    task = r.json()["task"]
    assert r.status_code == 200 and task["kind"] == "rescore_all" and task["url"] is None
    assert client.get(f"/app/api/tasks/{task['id']}").json()["state"] == "queued"
    conn.execute("UPDATE dashboard_tasks SET state = 'running' WHERE id = ?", (task["id"],))
    conn.commit()
    with patch.object(scorer, "estimate_cost", return_value=0.01):
        again = client.post("/app/api/rescore", json={"confirm": True}).json()["task"]
    assert again["id"] == task["id"]  # not a second paid run while the first is going
    assert conn.execute("SELECT COUNT(*) FROM dashboard_tasks").fetchone()[0] == 1


def test_rescore_all_over_the_cap_returns_402_and_queues_nothing(client, conn, monkeypatch):
    monkeypatch.setenv("LLM_MONTHLY_BUDGET_USD", "5")
    _spend(conn, 4.99)
    with patch.object(scorer, "estimate_cost", return_value=0.40):
        r = client.post("/app/api/rescore", json={"confirm": True})
    assert r.status_code == 402 and r.json()["detail"]["estimated_usd"] == 0.4
    assert conn.execute("SELECT COUNT(*) FROM dashboard_tasks").fetchone()[0] == 0


def test_preferences_reassess_scope_active(client, conn):
    prefs = _prefs(client, salary=SALARY_DEALBREAKER)  # changes no assessment input: nothing is stale
    with patch.object(scorer, "estimate_cost", return_value=0.01):
        stale = client.post("/app/api/preferences/preview", json={"prefs": prefs}).json()
        active = client.post("/app/api/preferences/preview", json={"prefs": prefs, "reassess_scope": "active"}).json()
        assert stale["jobs_to_reassess"] == 0 and active["jobs_to_reassess"] == 3 and active["reassess_needed"]
        assert client.put("/app/api/preferences", json={"prefs": prefs, "confirm_reassess": True}).json()["task"] is None
        task = client.put("/app/api/preferences", json={"prefs": prefs, "confirm_reassess": True,
                                                        "reassess_scope": "active"}).json()["task"]
    assert task["kind"] == "rescore_all"
    assert client.put("/app/api/preferences", json={"prefs": prefs, "reassess_scope": "all"}).status_code == 422


# ── Education and experience come from the Profile ────────────────────────

def _breakdown(client, url) -> dict:
    detail = client.get(f"/app/api/jobs/{job_key(url)}").json()
    return {c["key"]: c for c in detail["fit_breakdown"]}, detail["fit_flags"] or []


def test_profile_education_and_years_rescore_without_llm_calls(client, conn):
    masters = "https://e.com/masters"
    senior = "https://e.com/senior"
    for url, posting in ((masters, {**POSTING, "education_level": "masters", "years_experience_min": 2}),
                         (senior, {**POSTING, "years_experience_min": 5})):
        add_extracted_job(conn, url, job_key=job_key(url), posting=posting,
                          fit_assessment=json.dumps(parse_assessment(reply(skills=4))),
                          assessment_hash=current_assessment_key(conn))
    recompute_fit(conn)
    comps, _ = _breakdown(client, masters)
    assert comps["education"]["score"] == 0.5 and comps["experience"]["score"] == 0.5  # no profile values yet

    r = client.put("/app/api/profile", json={"education_level": "bachelors", "years_experience": 2})
    assert r.status_code == 200
    comps, flags = _breakdown(client, masters)
    assert comps["education"]["score"] == 0.4 and not comps["education"]["dealbreaker_hit"]  # weakened only
    assert comps["experience"]["score"] == 1.0 and flags == []
    comps, flags = _breakdown(client, senior)
    assert comps["experience"]["dealbreaker_hit"] and flags == ["experience"]
    assert _usage_rows(conn) == 0


def test_profile_save_without_qualification_changes_skips_the_rescore(client, conn):
    client.put("/app/api/profile", json={"education_level": "bachelors"})
    with patch("strelitzia.web.app.recompute_fit") as rescore:
        assert client.put("/app/api/profile", json={"first_name": "Ada", "education_level": "bachelors"}).status_code == 200
        rescore.assert_not_called()
        client.put("/app/api/profile", json={"years_experience": 4})
        rescore.assert_called_once()


def _store_old_prefs(conn, data: dict) -> None:
    from strelitzia import users

    conn.execute("INSERT INTO user_preferences (user_id, prefs_json, updated_at) VALUES (?, ?, ?) "
                 "ON CONFLICT (user_id) DO UPDATE SET prefs_json = excluded.prefs_json",
                 (users.owner(conn).id, json.dumps(data), "2026-10-01T00:00:00+00:00"))
    conn.commit()


def _unmark(conn) -> None:
    """Forget that this database already ran the migration (the client fixture's start did, on an empty one)."""
    conn.execute("DELETE FROM app_state WHERE key = 'migrated:prefs-cleanup-1'")
    conn.commit()


def _stored_prefs(conn) -> dict:
    return json.loads(conn.execute("SELECT prefs_json FROM user_preferences").fetchone()[0])


def test_startup_moves_old_education_and_experience_to_the_profile(client, conn, db_path):
    _store_old_prefs(conn, {"education": {"importance": "dealbreaker", "value": "masters"},
                            "experience": {"importance": "nice", "value": 6},
                            "company_blocklist": {"importance": "dealbreaker", "value": ["Acme Robotics"]},
                            "salary": {"importance": "dealbreaker", "value": {"min": 120000}}})
    conn.execute("UPDATE jobs SET fit_flags = '[\"company_blocklist\"]', fit_breakdown = '[]'")
    conn.commit()
    assert client.get("/app/api/preferences").json()["prefs"]["salary"]["value"]["min"] == 120000
    assert _stored_prefs(conn)["education"]["value"] == "masters"  # a GET changes nothing

    _unmark(conn)
    init_db(db_path)  # the next start migrates, once
    profile = client.get("/app/api/profile").json()["profile"]
    assert profile["education_level"] == "masters" and profile["years_experience"] == 6
    stored = _stored_prefs(conn)
    assert not {"education", "experience", "company_blocklist"} & stored.keys()
    assert stored["salary"]["importance"] == "dealbreaker" and "red_flags_counted" in stored
    # Every job was re-ranked with the new rules: the skip list's flag is gone, the profile's values count.
    detail = client.get(f"/app/api/jobs/{job_key(URLS[0])}").json()
    assert detail["fit_flags"] == ["salary"]
    assert {c["key"] for c in detail["fit_breakdown"]} >= {"education", "experience", "skills_match"}
    assert next(c for c in detail["fit_breakdown"] if c["key"] == "experience")["score"] == 1.0  # 6 years >= 3

    client.put("/app/api/profile", json={"years_experience": 1})
    init_db(db_path)
    assert client.get("/app/api/profile").json()["profile"]["years_experience"] == 1  # it ran once


def test_old_values_never_overwrite_the_profile(client, conn, db_path):
    client.put("/app/api/profile", json={"education_level": "phd", "years_experience": 10})
    _store_old_prefs(conn, {"education": {"value": "bachelors"}, "experience": {"value": 1}})
    _unmark(conn)
    init_db(db_path)
    profile = client.get("/app/api/profile").json()["profile"]
    assert profile["education_level"] == "phd" and profile["years_experience"] == 10
    assert "education" not in _stored_prefs(conn)


def test_startup_reranks_users_without_saved_preferences(client, conn, db_path):
    conn.execute("UPDATE jobs SET fit_breakdown = '[]'")
    conn.commit()
    _unmark(conn)
    init_db(db_path)
    detail = client.get(f"/app/api/jobs/{job_key(URLS[0])}").json()
    assert {c["key"] for c in detail["fit_breakdown"]} >= {"education", "experience", "skills_match"}
    assert conn.execute("SELECT COUNT(*) FROM user_preferences").fetchone()[0] == 0  # nothing to save for them
