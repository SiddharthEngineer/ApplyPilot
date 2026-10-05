"""The batched `extract` stage, against a fake LLM (offline)."""

import json
import re
from unittest.mock import MagicMock, patch

import pytest

from applypilot import pipeline
from applypilot.database import init_db
from applypilot.enrichment import extract
from applypilot.enrichment.posting_model import EXTRACT_VERSION
from applypilot.enrichment.posting_prompt import job_id
from applypilot.llm import LLMQuotaExhausted

QUOTA = LLMQuotaExhausted("gemini-3.1-flash-lite", "GenerateRequestsPerDayPerProjectPerModel-FreeTier")


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
    return c


def _posting(jid: str, **overrides) -> dict:
    return {
        "job_id": jid, "company": "Globex", "title_normalized": "Data Engineer",
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
    """Answers each batch from the JOB_ID lines in the user message; hooks can drop/alter/raise."""

    def __init__(self, drop=(), overrides=None, fail_calls=()):
        self.calls: list[list[str]] = []
        self.drop = set(drop)
        self.overrides = overrides or {}
        self.fail_calls = fail_calls

    def chat(self, messages, **kwargs):
        ids = re.findall(r"^JOB_ID: (\w+)$", messages[1]["content"], re.MULTILINE)
        self.calls.append(ids)
        n = len(self.calls)
        if n in self.fail_calls:
            raise self.fail_calls[n]
        jobs = [_posting(i, **self.overrides.get(i, {})) for i in ids if not (i in self.drop and len(ids) > 1)]
        return json.dumps({"jobs": jobs})


def _run(conn, fake, **kwargs):
    client = MagicMock()
    client.chat.side_effect = fake.chat
    with patch.object(extract, "get_client", return_value=client):
        return extract.run_extraction(conn=conn, **kwargs)


def _row(conn, i):
    return dict(conn.execute("SELECT * FROM jobs WHERE url = ?", (_url(i),)).fetchone())


def test_batch_round_trip(conn):
    fake = FakeLLM()
    stats = _run(conn, fake, batch_size=5)
    assert stats["extracted"] == 7 and stats["errors"] == 0 and stats["requests"] == 2
    assert [len(c) for c in fake.calls] == [5, 2]
    row = _row(conn, 2)
    assert row["role_category"] == "data_engineering" and row["category_source"] == "llm"
    assert (row["location_city"], row["location_state"], row["location_country"]) == ("Chicago", "IL", "US")
    assert (row["salary_min"], row["salary_max"], row["salary_period"]) == (120000.0, 150000.0, "year")
    assert row["deadline"] == "2026-11-01" and row["posted_date"] == "2026-10-01"
    assert row["extract_version"] == EXTRACT_VERSION and row["extracted_at"]
    assert json.loads(row["details_json"])["summary"] == "Builds pipelines."
    assert "job_id" not in json.loads(row["details_json"])
    assert row["company"] == "Globex"  # was NULL
    assert _row(conn, 0)["company"] == "Acme"  # never overwritten
    assert stats["pending_left"] == 0


def test_limit_caps_jobs_and_requests(conn):
    fake = FakeLLM()
    stats = _run(conn, fake, limit=3, batch_size=5)
    assert fake.calls and len(fake.calls) == 1 and len(fake.calls[0]) == 3
    assert stats["extracted"] == 3 and stats["pending_left"] == 4


def test_ten_jobs_take_two_requests(tmp_path):
    c = init_db(tmp_path / "ten.db")
    c.executemany("INSERT INTO jobs (url, title, full_description) VALUES (?, 't', 'd')", [(_url(i),) for i in range(12)])
    c.commit()
    fake = FakeLLM()
    _run(c, fake, limit=10)  # default EXTRACT_BATCH_SIZE = 5
    assert len(fake.calls) == 2
    assert c.execute("SELECT COUNT(*) FROM jobs WHERE extracted_at IS NOT NULL").fetchone()[0] == 10


def test_ordering_best_score_then_newest(conn):
    fake = FakeLLM()
    _run(conn, fake, batch_size=1)
    order = [ids[0] for ids in fake.calls]
    expected = [job_id({"url": _url(i)}) for i in (3, 1, 6, 5, 4, 2, 0)]
    assert order == expected


def test_missing_job_is_retried_alone(conn):
    missing = job_id({"url": _url(3)})
    fake = FakeLLM(drop={missing})
    stats = _run(conn, fake, batch_size=5)
    assert fake.calls[-1] == [missing]  # retried on its own after the batches
    assert stats["extracted"] == 7 and stats["errors"] == 0 and stats["requests"] == 3
    row = _row(conn, 3)
    assert row["extracted_at"] and row["extract_attempts"] == 1 and row["extract_error"] is None


def test_failure_counts_attempts_and_gives_up(conn):
    missing = job_id({"url": _url(3)})
    # Missing from every multi-job reply; the single retry then fails with a bad reply.
    fake = FakeLLM(drop={missing}, fail_calls={3: ValueError("reply has no jobs")})
    stats = _run(conn, fake, batch_size=5)
    assert stats["extracted"] == 6 and stats["errors"] == 1
    row = _row(conn, 3)
    assert row["extracted_at"] is None and row["extract_attempts"] == 2
    assert "reply has no jobs" in row["extract_error"]


def test_invalid_enum_coerced(conn):
    jid = job_id({"url": _url(3)})
    fake = FakeLLM(overrides={jid: {"role_category": "astronaut", "work_mode": "moon", "salary_min": "lots"}})
    _run(conn, fake, limit=1)
    row = _row(conn, 3)
    assert (row["role_category"], row["work_mode"], row["salary_min"]) == ("other", "unknown", None)


def test_quota_stop_keeps_finished_batches(conn):
    fake = FakeLLM(fail_calls={2: QUOTA})
    stats = _run(conn, fake, batch_size=5)
    assert stats["stopped"] == "daily_quota"
    assert stats["extracted"] == 5 and stats["requests"] == 1
    assert conn.execute("SELECT COUNT(*) FROM jobs WHERE extracted_at IS NOT NULL").fetchone()[0] == 5
    # Quota-stopped jobs aren't charged an attempt.
    assert conn.execute("SELECT MAX(COALESCE(extract_attempts, 0)) FROM jobs").fetchone()[0] == 0


def test_overload_stops_without_charging_attempts(conn):
    from applypilot.llm import LLMOverloaded

    fake = FakeLLM(fail_calls={2: LLMOverloaded("gemini-3.1-flash-lite")})
    stats = _run(conn, fake, batch_size=5)
    assert stats["stopped"] == "overloaded"
    assert stats["extracted"] == 5 and stats["requests"] == 1
    assert conn.execute("SELECT MAX(COALESCE(extract_attempts, 0)) FROM jobs").fetchone()[0] == 0
    assert pipeline._stage_status(stats) == {"status": pipeline.OVERLOAD_STOPPED}


def test_repeated_request_failures_stop_the_run(conn):
    boom = RuntimeError("HTTP 500")
    fake = FakeLLM(fail_calls={1: boom, 2: boom, 3: boom, 4: boom, 5: boom})
    stats = _run(conn, fake, batch_size=1)
    assert stats["stopped"] == "errors" and len(fake.calls) == 3


def test_version_bump_reselects(conn):
    conn.execute("UPDATE jobs SET extracted_at = '2026-10-01', extract_version = ?", (EXTRACT_VERSION,))
    conn.execute("UPDATE jobs SET extract_version = ? WHERE url = ?", (EXTRACT_VERSION - 1, _url(4)))
    conn.commit()
    fake = FakeLLM()
    stats = _run(conn, fake)
    assert fake.calls == [[job_id({"url": _url(4)})]] and stats["extracted"] == 1


def test_nothing_pending_makes_no_request(conn):
    conn.execute("UPDATE jobs SET full_description = NULL")
    conn.commit()
    fake = FakeLLM()
    assert _run(conn, fake)["requests"] == 0 and fake.calls == []


def test_pipeline_stage_wiring():
    order = pipeline.STAGE_ORDER
    assert order.index("enrich") < order.index("extract")
    assert order.index("score") < order.index("extract")  # scoring gets the shared quota first
    assert pipeline._UPSTREAM["extract"] == "enrich"
    assert pipeline._UPSTREAM["score"] == "enrich"  # extract never blocks scoring
    assert "extract" in pipeline._STAGE_RUNNERS and "extract" in pipeline._PENDING_SQL


def test_pipeline_runner_classifies_then_extracts():
    with patch("applypilot.enrichment.classify.run_classify") as classify, \
            patch("applypilot.enrichment.extract.run_extraction", return_value={"stopped": "daily_quota"}) as run:
        result = pipeline._run_extract(limit=10)
    classify.assert_called_once_with(only_missing=True)
    run.assert_called_once_with(limit=10)
    assert result["status"] == pipeline.QUOTA_STOPPED


def test_extract_uses_flash_lite_by_default(monkeypatch):
    from applypilot.llm import _detect_provider
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    for var in ("LLM_URL", "OPENAI_API_KEY", "OPENCODE_API_KEY", "LLM_MODEL", "LLM_EXTRACT_MODEL"):
        monkeypatch.delenv(var, raising=False)
    assert _detect_provider("extract")[1] == "gemini-3.1-flash-lite"
    monkeypatch.setenv("LLM_EXTRACT_MODEL", "gemini-x")
    assert _detect_provider("extract")[1] == "gemini-x"
