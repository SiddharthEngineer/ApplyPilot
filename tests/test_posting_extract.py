"""The `extract` stage (one posting per request, batch or sync), against a fake LLM (offline)."""

import json
from unittest.mock import MagicMock, patch

import pytest

from strelitzia import pipeline, users
from strelitzia.database import MAX_EXTRACT_ATTEMPTS, backfill_job_keys, init_db
from strelitzia.enrichment import extract
from strelitzia.enrichment.posting_model import EXTRACT_VERSION, posting_schema
from strelitzia.enrichment.posting_prompt import job_id
from strelitzia.llm import LLMClient, LLMStopRun

STOP = LLMStopRun("budget", "monthly LLM budget used up")


def _url(i: int) -> str:
    return f"https://example.com/job/{i}"


@pytest.fixture
def conn(tmp_path):
    c = init_db(tmp_path / "t.db")
    # fit_score: job 3 best, then 1; the rest unscored (newest first by discovered_at).
    for i in range(7):
        c.execute(
            "INSERT INTO jobs (url, title, site, company, location, full_description, fit_score, discovered_at) "
            "VALUES (?, ?, 'indeed', ?, 'Chicago, IL', ?, ?, ?)",
            (_url(i), f"Data Engineer {i}", "Acme" if i == 0 else None, f"Posting {i}. Build pipelines.",
             {3: 9, 1: 5}.get(i), f"2026-10-0{i + 1}"),
        )
    c.commit()
    backfill_job_keys(c)
    return c


def _cid(conn, i: int) -> str:
    """The batch custom_id of the owner's job i."""
    return f"{job_id({'url': _url(i)})}-{users.owner(conn).id}"


def _posting(**overrides) -> dict:
    return {
        "company": "Globex", "title_normalized": "Data Engineer",
        "role_category": "data_engineering", "seniority": "senior", "employment_type": "full_time",
        "work_mode": "hybrid", "locations": [{"city": "Chicago", "state": "IL", "country": "US"}],
        "remote_region": None, "salary_min": 120000, "salary_max": 150000, "salary_currency": "USD",
        "salary_period": "year", "posted_date": "2026-10-01", "application_deadline": "2026-11-01",
        "start_date": None, "summary": "Builds pipelines.", "team": None, "responsibilities": ["Build pipelines"],
        "required_qualifications": ["SQL"], "preferred_qualifications": [], "skills_required": ["SQL"],
        "skills_preferred": [], "education_level": "bachelors", "education_fields": [],
        "years_experience_min": 3, "years_experience_max": None, "visa_sponsorship": "unknown",
        "security_clearance": "unknown", "travel": None, "benefits": [], "company_blurb": None,
        **overrides,
    }


class FakeLLM:
    """Answers each sync request (by job_url) and each batch item (by custom_id); hooks can alter or fail."""

    def __init__(self, overrides=None, fail_calls=(), batch_errors=None, bad_replies=()):
        self.calls: list[str] = []  # job urls, sync
        self.batches: list[list[tuple[str, dict]]] = []
        self.overrides = overrides or {}
        self.fail_calls = fail_calls
        self.batch_errors = batch_errors or {}
        self.bad_replies = set(bad_replies)

    def chat(self, messages, **kwargs):
        url = kwargs["job_url"]
        self.calls.append(url)
        n = len(self.calls)
        if n in self.fail_calls:
            raise self.fail_calls[n]
        return json.dumps(_posting(**self.overrides.get(url, {})))

    def run_batch(self, purpose, items, on_result, poll_seconds=30, estimate_usd=0.0, conn=None):
        assert purpose == "extract"
        self.batches.append(items)
        for cid, _ in reversed(items):  # any order
            if cid in self.batch_errors:
                on_result(cid, None, self.batch_errors[cid])
            elif cid in self.bad_replies:
                on_result(cid, '{"jobs": ', None)
            else:
                on_result(cid, json.dumps(_posting()), None)
        errored = sum(1 for cid, _ in items if cid in self.batch_errors)
        return {"batches": 1, "succeeded": len(items) - errored, "errored": errored,
                "callback_errors": 0, "cost_usd": 0.0}


def _client(fake):
    client = MagicMock()
    client.chat.side_effect = fake.chat
    client.request_params.side_effect = LLMClient("extract", "claude-haiku-5-5", "low").request_params
    client.model = "claude-haiku-5-5"
    return client


def _run(conn, fake, estimate=0.25, budget_error=None, resume=None, **kwargs):
    with patch.object(extract, "get_client", return_value=_client(fake)), \
            patch.object(extract, "run_batch", side_effect=fake.run_batch), \
            patch.object(extract, "resume_open_batches", side_effect=resume, return_value={"still_open": 0}) as resumed, \
            patch.object(extract, "estimate_cost", return_value=estimate) as est, \
            patch.object(extract, "check_budget", side_effect=budget_error) as budget:
        stats = extract.run_extraction(conn=conn, poll_seconds=0, **kwargs)
    fake.estimate, fake.budget, fake.resume = est, budget, resumed
    return stats


def _row(conn, i):
    return dict(conn.execute("SELECT * FROM jobs WHERE url = ?", (_url(i),)).fetchone())


def _check_saved(conn, stats):
    row = _row(conn, 2)
    assert row["role_category"] == "data_engineering" and row["category_source"] == "llm"
    assert (row["location_city"], row["location_state"], row["location_country"]) == ("Chicago", "IL", "US")
    assert (row["salary_min"], row["salary_max"], row["salary_period"]) == (120000.0, 150000.0, "year")
    assert row["deadline"] == "2026-11-01" and row["posted_date"] == "2026-10-01"
    assert row["extract_version"] == EXTRACT_VERSION == 4 and row["extracted_at"]
    assert json.loads(row["details_json"])["summary"] == "Builds pipelines."
    assert row["company"] == "Globex"  # was NULL
    assert _row(conn, 0)["company"] == "Acme"  # never overwritten
    assert stats["pending_left"] == 0


def test_batch_round_trip(conn):
    fake = FakeLLM()
    stats = _run(conn, fake)
    assert stats["mode"] == "batch" and stats["extracted"] == 7 and stats["errors"] == 0 and stats["requests"] == 7
    assert fake.calls == [] and len(fake.batches) == 1
    items = fake.batches[0]
    assert [cid for cid, _ in items] == [_cid(conn, i) for i in (3, 1, 6, 5, 4, 2, 0)]
    params = items[0][1]
    assert params["output_config"]["format"] == {"type": "json_schema", "schema": posting_schema()}
    assert params["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert params["messages"][0]["content"].startswith("TITLE: Data Engineer 3\n")
    # The estimate covers every request at batch price, and the budget is checked against it first.
    assert len(fake.estimate.call_args.args[1]) == 7 and fake.estimate.call_args.kwargs == {"batch": True}
    assert fake.budget.call_args.args[0] == 0.25
    assert stats["estimate_usd"] == 0.25
    assert fake.resume.call_args.kwargs["wait"] is True
    _check_saved(conn, stats)


def test_sync_round_trip(conn):
    fake = FakeLLM()
    stats = _run(conn, fake, mode="sync")
    assert stats["mode"] == "sync" and stats["extracted"] == 7 and stats["requests"] == 7
    assert fake.calls == [_url(i) for i in (3, 1, 6, 5, 4, 2, 0)]  # best score, then newest
    assert fake.batches == []
    assert fake.resume.call_args.kwargs["wait"] is False  # a sync run only collects batches that have ended
    _check_saved(conn, stats)


def test_small_runs_go_sync(conn):
    fake = FakeLLM()
    stats = _run(conn, fake, limit=extract.SYNC_MAX_JOBS)
    assert stats["mode"] == "sync" and len(fake.calls) == extract.SYNC_MAX_JOBS and fake.batches == []
    assert stats["pending_left"] == 7 - extract.SYNC_MAX_JOBS
    assert _run(conn, FakeLLM(), limit=6)["mode"] == "sync"  # only 2 left pending


def test_sync_request_sends_schema_and_job_url():
    client = MagicMock()
    client.chat.return_value = json.dumps(_posting())
    with patch.object(extract, "get_client", return_value=client):
        posting = extract.extract_one({"url": _url(1), "title": "T", "full_description": "d"}, today="2026-10-13")
    assert posting.company == "Globex"
    kwargs = client.chat.call_args.kwargs
    assert kwargs["schema"] == posting_schema() and kwargs["job_url"] == _url(1)
    assert "temperature" not in kwargs
    messages = client.chat.call_args.args[0]
    assert messages[0]["role"] == "system" and "Today is 2026-10-13" in messages[0]["content"]


def test_batch_errors_count_attempts(conn):
    bad = _cid(conn, 3)
    invalid = _cid(conn, 1)
    fake = FakeLLM(batch_errors={bad: "errored: overloaded_error: busy"}, bad_replies={invalid})
    stats = _run(conn, fake)
    assert stats["extracted"] == 5 and stats["errors"] == 2
    row = _row(conn, 3)
    assert row["extracted_at"] is None and row["extract_attempts"] == 1
    assert row["extract_error"] == "LLM error: errored: overloaded_error: busy"
    assert "invalid reply" in _row(conn, 1)["extract_error"]


def test_failed_jobs_wait_for_a_later_run_until_out_of_attempts(conn):
    bad = _cid(conn, 3)
    _run(conn, FakeLLM(batch_errors={bad: "expired"}))
    assert _row(conn, 3)["extract_attempts"] == 1
    fake = FakeLLM()
    assert _run(conn, fake)["extracted"] == 1 and fake.calls == [_url(3)]  # next run picks it up
    conn.execute("UPDATE jobs SET extracted_at = NULL, extract_attempts = ? WHERE url = ?",
                 (MAX_EXTRACT_ATTEMPTS, _url(3)))
    conn.commit()
    fake = FakeLLM()
    assert _run(conn, fake)["requests"] == 0 and fake.calls == []  # out of attempts


def test_resumed_results_are_saved_by_job_key(conn):
    conn.execute("UPDATE jobs SET extracted_at = '2026-10-12', extract_version = ? WHERE url = ?",
                 (EXTRACT_VERSION, _url(5)))
    conn.commit()

    def resume(purpose, on_result, poll_seconds=30, wait=True, conn=None):
        on_result(job_id({"url": _url(4)}), json.dumps(_posting(team="Resumed")), None)  # before user accounts
        on_result(_cid(conn, 5), json.dumps(_posting(team="Stale")), None)  # extracted since: skipped
        on_result("ffffffffffff", json.dumps(_posting()), None)  # a job deleted since
        return {"batches": 1, "still_open": 0}

    fake = FakeLLM()
    stats = _run(conn, fake, resume=resume)
    assert stats["resumed"] == 1
    assert json.loads(_row(conn, 4)["details_json"])["team"] == "Resumed"
    assert stats["requests"] == 5 and _url(4) not in fake.calls and _url(5) not in fake.calls  # not paid twice
    assert json.loads(_row(conn, 5)["details_json"] or "{}").get("team") != "Stale"


def test_a_failed_resume_stops_before_paying_again(conn):
    fake = FakeLLM()
    stats = _run(conn, fake, resume=LLMStopRun("overloaded"))
    assert stats["stopped"] == "overloaded" and fake.batches == [] and fake.calls == []


def test_invalid_enum_coerced(conn):
    fake = FakeLLM(overrides={_url(3): {"role_category": "astronaut", "work_mode": "moon", "salary_min": "lots"}})
    _run(conn, fake, limit=1)
    row = _row(conn, 3)
    assert (row["role_category"], row["work_mode"], row["salary_min"]) == ("other", "unknown", None)


def test_budget_stop_before_a_batch_submits_nothing(conn):
    fake = FakeLLM()
    stats = _run(conn, fake, budget_error=STOP)
    assert stats["stopped"] == "budget" and stats["requests"] == 0 and fake.batches == []
    assert pipeline._stage_status(stats) == {"status": pipeline.BUDGET_STOPPED}
    assert conn.execute("SELECT MAX(COALESCE(extract_attempts, 0)) FROM jobs").fetchone()[0] == 0


def test_sync_budget_stop_keeps_finished_jobs(conn):
    fake = FakeLLM(fail_calls={3: STOP})
    stats = _run(conn, fake, mode="sync")
    assert stats["stopped"] == "budget"
    assert stats["extracted"] == 2 and stats["requests"] == 2
    assert conn.execute("SELECT COUNT(*) FROM jobs WHERE extracted_at IS NOT NULL").fetchone()[0] == 2
    # Jobs left by a stop aren't charged an attempt.
    assert conn.execute("SELECT MAX(COALESCE(extract_attempts, 0)) FROM jobs").fetchone()[0] == 0


def test_overload_stops_without_charging_attempts(conn):
    fake = FakeLLM(fail_calls={2: LLMStopRun("overloaded")})
    stats = _run(conn, fake, mode="sync")
    assert stats["stopped"] == "overloaded" and stats["extracted"] == 1
    assert conn.execute("SELECT MAX(COALESCE(extract_attempts, 0)) FROM jobs").fetchone()[0] == 0
    assert pipeline._stage_status(stats) == {"status": pipeline.OVERLOAD_STOPPED}


def test_repeated_request_failures_stop_the_run(conn):
    boom = RuntimeError("HTTP 500")
    fake = FakeLLM(fail_calls={1: boom, 2: boom, 3: boom, 4: boom, 5: boom})
    stats = _run(conn, fake, mode="sync")
    assert stats["stopped"] == "errors" and len(fake.calls) == 3
    assert _row(conn, 3)["extract_error"] == "LLM error: HTTP 500"


def test_a_batch_where_everything_failed_reports_errors(conn):
    fake = FakeLLM(batch_errors={_cid(conn, i): "expired" for i in range(7)})
    assert _run(conn, fake)["stopped"] == "errors"


def test_version_bump_reselects(conn):
    conn.execute("UPDATE jobs SET extracted_at = '2026-10-01', extract_version = ?", (EXTRACT_VERSION,))
    conn.execute("UPDATE jobs SET extract_version = ? WHERE url = ?", (EXTRACT_VERSION - 1, _url(4)))
    conn.commit()
    fake = FakeLLM()
    stats = _run(conn, fake)
    assert fake.calls == [_url(4)] and stats["extracted"] == 1


def test_nothing_pending_makes_no_request(conn):
    conn.execute("UPDATE jobs SET full_description = NULL")
    conn.commit()
    fake = FakeLLM()
    assert _run(conn, fake)["requests"] == 0 and fake.calls == [] and fake.batches == []


def test_parse_posting():
    assert extract.parse_posting("```json\n" + json.dumps(_posting()) + "\n```").company == "Globex"
    with pytest.raises(ValueError):
        extract.parse_posting('{"company": ')
    with pytest.raises(ValueError):
        extract.parse_posting("[1, 2]")


def test_pipeline_stage_wiring():
    order = pipeline.STAGE_ORDER
    assert order.index("enrich") < order.index("extract")
    assert order.index("score") < order.index("extract")  # scoring gets the monthly budget first
    assert pipeline._UPSTREAM["extract"] == "enrich"
    assert pipeline._UPSTREAM["score"] == "enrich"  # extract never blocks scoring
    assert "extract" in pipeline._STAGE_RUNNERS and "extract" in pipeline._PENDING_SQL


def test_pipeline_runner_classifies_then_extracts():
    with patch("strelitzia.enrichment.classify.run_classify") as classify, \
            patch("strelitzia.enrichment.extract.run_extraction", return_value={"stopped": "budget"}) as run:
        result = pipeline._run_extract(limit=10)
    classify.assert_called_once_with(only_missing=True)
    run.assert_called_once_with(limit=10)
    assert result["status"] == pipeline.BUDGET_STOPPED


def test_extract_uses_haiku_by_default(monkeypatch):
    from strelitzia.llm import resolve_model
    monkeypatch.delenv("LLM_EXTRACT_MODEL", raising=False)
    assert resolve_model("extract")[0] == "claude-haiku-5-5"
    monkeypatch.setenv("LLM_EXTRACT_MODEL", "claude-sonnet-5-5")
    assert resolve_model("extract")[0] == "claude-sonnet-5-5"


def test_two_users_rows_for_one_posting_are_extracted_separately(conn):
    """B's row for job 2 gets its own request (a distinct custom_id) and only B's result; A's row only A's."""
    other = users.invite(conn, "b@example.com").id
    conn.execute("INSERT INTO jobs (user_id, url, title, site, full_description, discovered_at) "
                 "VALUES (?, ?, 'B copy', 'indeed', 'B pasted this.', '2026-10-09')", (other, _url(2)))
    conn.commit()
    backfill_job_keys(conn)
    b_cid = f"{job_id({'url': _url(2)})}-{other}"
    fake = FakeLLM(batch_errors={b_cid: "expired"})
    stats = _run(conn, fake)
    cids = [cid for cid, _ in fake.batches[0]]
    assert len(cids) == len(set(cids)) == 8 and b_cid in cids and _cid(conn, 2) in cids
    assert stats["extracted"] == 7 and stats["errors"] == 1

    def row(user_id):
        return dict(conn.execute("SELECT * FROM jobs WHERE user_id = ? AND url = ?", (user_id, _url(2))).fetchone())

    a, b = row(users.owner(conn).id), row(other)
    assert a["extracted_at"] and a["extract_attempts"] in (None, 0) and a["extract_error"] is None
    assert b["extracted_at"] is None and b["extract_attempts"] == 1 and b["extract_error"] == "LLM error: expired"

    extract._save_posting(conn, other, _url(2), extract.parse_posting(json.dumps(_posting(team="B only"))))
    assert json.loads(row(other)["details_json"])["team"] == "B only"
    assert json.loads(row(users.owner(conn).id)["details_json"])["team"] is None
