"""A daily-quota 429 stops each LLM stage cleanly (gemini-free-tier-llm Task 2)."""

from unittest.mock import MagicMock, patch

import pytest

from applypilot import pipeline
from applypilot.database import init_db
from applypilot.discovery import smartextract
from applypilot.llm import LLMQuotaExhausted
from applypilot.scoring import cover_letter, scorer, tailor

URLS = [f"https://example.com/job/{i}" for i in range(3)]
QUOTA = LLMQuotaExhausted("gemini-3.1-flash-lite", "GenerateRequestsPerDayPerProjectPerModel-FreeTier")


@pytest.fixture
def conn(tmp_path):
    c = init_db(tmp_path / "t.db")
    for url in URLS:
        c.execute(
            "INSERT INTO jobs (url, title, site, location, full_description, fit_score, tailored_resume_path) "
            "VALUES (?, 'Data Engineer', 'indeed', 'Remote', 'Build pipelines.', NULL, NULL)",
            (url,),
        )
    c.commit()
    return c


@pytest.fixture
def resume(tmp_path):
    path = tmp_path / "resume.txt"
    path.write_text("Test resume")
    return path


def _rows(conn, cols):
    return [tuple(r) for r in conn.execute(f"SELECT {cols} FROM jobs ORDER BY url").fetchall()]


def test_scoring_stops_after_one_request(conn, resume):
    client = MagicMock()
    client.chat.side_effect = QUOTA
    with patch.object(scorer, "get_connection", return_value=conn), \
            patch.object(scorer, "RESUME_PATH", resume), \
            patch.object(scorer, "get_client", return_value=client):
        stats = scorer.run_scoring(prefilter=False)
    assert client.chat.call_count == 1
    assert stats["stopped"] == "daily_quota"
    assert stats["scored"] == 0 and stats["errors"] == 0
    # No job touched: still unscored, no attempt recorded, no error reasoning.
    assert _rows(conn, "fit_score, score_reasoning, score_attempts") == [(None, None, 0)] * 3


def test_scoring_keeps_scores_before_the_stop(conn, resume):
    client = MagicMock()
    client.chat.side_effect = ["SCORE: 8\nKEYWORDS: sql\nREASONING: good", QUOTA]
    with patch.object(scorer, "get_connection", return_value=conn), \
            patch.object(scorer, "RESUME_PATH", resume), \
            patch.object(scorer, "get_client", return_value=client):
        stats = scorer.run_scoring(prefilter=False)
    assert client.chat.call_count == 2
    assert stats["scored"] == 1 and stats["stopped"] == "daily_quota"
    scores = sorted(r[0] for r in _rows(conn, "fit_score") if r[0] is not None)
    assert scores == [8]
    assert [r[0] for r in _rows(conn, "score_attempts")] == [0, 0, 0]


def _ready_for_tailor(conn):
    conn.execute("UPDATE jobs SET fit_score = 9")
    conn.commit()


def test_tailoring_stops_without_counting_attempt(conn, resume, tmp_path):
    _ready_for_tailor(conn)
    with patch.object(tailor, "get_connection", return_value=conn), \
            patch.object(tailor, "load_profile", return_value={}), \
            patch.object(tailor, "RESUME_PATH", resume), \
            patch.object(tailor, "TAILORED_DIR", tmp_path / "tailored"), \
            patch.object(tailor, "tailor_resume", side_effect=QUOTA) as tr:
        stats = tailor.run_tailoring(min_score=7)
    assert tr.call_count == 1
    assert stats["stopped"] == "daily_quota" and stats["errors"] == 0
    assert _rows(conn, "tailor_attempts, tailored_resume_path") == [(0, None)] * 3


def test_cover_letters_stop_without_counting_attempt(conn, resume, tmp_path):
    conn.execute("UPDATE jobs SET fit_score = 9, tailored_resume_path = '/x.txt'")
    conn.commit()
    with patch.object(cover_letter, "get_connection", return_value=conn), \
            patch.object(cover_letter, "load_profile", return_value={}), \
            patch.object(cover_letter, "RESUME_PATH", resume), \
            patch.object(cover_letter, "COVER_LETTER_DIR", tmp_path / "cl"), \
            patch.object(cover_letter, "generate_cover_letter", side_effect=QUOTA) as gen:
        stats = cover_letter.run_cover_letters(min_score=7)
    assert gen.call_count == 1
    assert stats["stopped"] == "daily_quota" and stats["errors"] == 0
    assert _rows(conn, "cover_attempts, cover_letter_path") == [(0, None)] * 3


def test_smartextract_quota_is_fatal(tmp_path):
    with patch.object(smartextract, "_run_one_site", side_effect=QUOTA):
        r = smartextract._run_one_site_safe("Site", "https://example.com")
    assert r["fatal"] and r["stopped"] == "daily_quota"


def test_smartextract_quota_stops_remaining_targets(tmp_path):
    targets = [{"name": f"S{i}", "url": f"https://s{i}.example", "query": None} for i in range(3)]
    with patch.object(smartextract, "init_db", return_value=init_db(tmp_path / "t.db")), \
            patch.object(smartextract, "_run_one_site", side_effect=QUOTA) as one:
        stats = smartextract._run_all(targets, [], [])
    assert one.call_count == 1
    assert stats["stopped"] == "daily_quota"


@pytest.mark.parametrize("judge", ["_judge_sequential", "judge_api_responses"])
def test_smartextract_judge_does_not_swallow_quota(judge):
    resp = {"url": "https://x/api/jobs", "status": 200, "size": 100, "type": "json",
            "data": {"jobs": [{"title": "Engineer", "location": "Remote"}]}}
    candidates = [resp, dict(resp, url="https://x/api/jobs2")] if judge == "judge_api_responses" else [resp]
    with patch.object(smartextract, "ask_llm", side_effect=QUOTA), \
            patch.object(smartextract, "_is_obviously_not_jobs", return_value=False), \
            pytest.raises(LLMQuotaExhausted):
        getattr(smartextract, judge)(candidates)


def test_pipeline_wrapper_reports_quota_stop():
    with patch("applypilot.scoring.scorer.run_scoring", return_value={"stopped": "daily_quota"}):
        assert pipeline._run_score() == {"status": pipeline.QUOTA_STOPPED}
    with patch("applypilot.scoring.scorer.run_scoring", return_value={"scored": 1}):
        assert pipeline._run_score() == {"status": "ok"}


def test_streaming_stage_does_not_rerun_after_quota_stop():
    tracker = pipeline._StageTracker()
    runner = MagicMock(return_value={"status": pipeline.QUOTA_STOPPED})
    with patch.dict(pipeline._STAGE_RUNNERS, {"score": runner}), \
            patch.object(pipeline, "_count_pending", return_value=5), \
            patch.dict(pipeline._UPSTREAM, {"score": None}):
        pipeline._run_stage_streaming("score", tracker, MagicMock(is_set=MagicMock(return_value=False)))
    assert runner.call_count == 1
    assert tracker.get_results()["score"]["status"] == pipeline.QUOTA_STOPPED
