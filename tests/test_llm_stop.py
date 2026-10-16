"""An LLMStopRun (budget used up, bad key, API overloaded) stops each LLM stage cleanly."""

from unittest.mock import MagicMock, patch

import pytest

from strelitzia import pipeline
from strelitzia.database import init_db
from strelitzia.discovery import smartextract
from strelitzia.llm import LLMStopRun
from strelitzia.scoring import scorer
from fit_helpers import add_extracted_job, reply

URLS = [f"https://example.com/job/{i}" for i in range(3)]
STOP = LLMStopRun("budget", "monthly LLM budget used up")


@pytest.fixture
def conn(tmp_path):
    c = init_db(tmp_path / "t.db")
    for url in URLS:
        add_extracted_job(c, url, site="indeed", location="Remote")
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
    client.chat.side_effect = STOP
    with patch.object(scorer, "get_connection", return_value=conn), \
            patch.object(scorer, "RESUME_PATH", resume), \
            patch.object(scorer, "get_client", return_value=client):
        stats = scorer.run_scoring(prefilter=False)
    assert client.chat.call_count == 1
    assert stats["stopped"] == "budget"
    assert stats["scored"] == 0 and stats["errors"] == 0
    # No job touched: still unscored, no attempt recorded, no error reasoning.
    assert _rows(conn, "fit_score, score_reasoning, score_attempts") == [(None, None, 0)] * 3


def test_scoring_keeps_scores_before_the_stop(conn, resume):
    client = MagicMock()
    client.chat.side_effect = [reply(skills=4), STOP]
    with patch.object(scorer, "get_connection", return_value=conn), \
            patch.object(scorer, "RESUME_PATH", resume), \
            patch.object(scorer, "get_client", return_value=client):
        stats = scorer.run_scoring(prefilter=False)
    assert client.chat.call_count == 2
    assert stats["scored"] == 1 and stats["stopped"] == "budget"
    scores = sorted(r[0] for r in _rows(conn, "fit_score") if r[0] is not None)
    assert scores == [75]
    assert [r[0] for r in _rows(conn, "score_attempts")] == [0, 0, 0]


def test_smartextract_stop_is_fatal(tmp_path):
    with patch.object(smartextract, "_run_one_site", side_effect=STOP):
        r = smartextract._run_one_site_safe("Site", "https://example.com")
    assert r["fatal"] and r["stopped"] == "budget"


def test_smartextract_stop_stops_remaining_targets(tmp_path):
    targets = [{"name": f"S{i}", "url": f"https://s{i}.example", "query": None} for i in range(3)]
    with patch.object(smartextract, "init_db", return_value=init_db(tmp_path / "t.db")), \
            patch.object(smartextract, "_run_one_site", side_effect=STOP) as one:
        stats = smartextract._run_all(targets, [], [])
    assert one.call_count == 1
    assert stats["stopped"] == "budget"


@pytest.mark.parametrize("judge", ["_judge_sequential", "judge_api_responses"])
def test_smartextract_judge_does_not_swallow_stop(judge):
    resp = {"url": "https://x/api/jobs", "status": 200, "size": 100, "type": "json",
            "data": {"jobs": [{"title": "Engineer", "location": "Remote"}]}}
    candidates = [resp, dict(resp, url="https://x/api/jobs2")] if judge == "judge_api_responses" else [resp]
    with patch.object(smartextract, "ask_llm", side_effect=STOP), \
            patch.object(smartextract, "_is_obviously_not_jobs", return_value=False), \
            pytest.raises(LLMStopRun):
        getattr(smartextract, judge)(candidates)


def test_pipeline_wrapper_reports_budget_stop():
    init_db()  # the pipeline's start does this; _run_score lists the users with jobs
    with patch("strelitzia.scoring.scorer.run_scoring", return_value={"stopped": "budget"}):
        assert pipeline._run_score() == {"status": pipeline.BUDGET_STOPPED}
    with patch("strelitzia.scoring.scorer.run_scoring", return_value={"scored": 1}):
        assert pipeline._run_score() == {"status": "ok"}


def test_streaming_stage_does_not_rerun_after_budget_stop():
    tracker = pipeline._StageTracker()
    runner = MagicMock(return_value={"status": pipeline.BUDGET_STOPPED})
    with patch.dict(pipeline._STAGE_RUNNERS, {"score": runner}), \
            patch.object(pipeline, "_count_pending", return_value=5), \
            patch.dict(pipeline._UPSTREAM, {"score": None}):
        pipeline._run_stage_streaming("score", tracker, MagicMock(is_set=MagicMock(return_value=False)))
    assert runner.call_count == 1
    assert tracker.get_results()["score"]["status"] == pipeline.BUDGET_STOPPED


def test_pipeline_scores_every_user_and_one_capped_user_does_not_stop_the_stage(tmp_path, monkeypatch):
    from strelitzia import database, users

    monkeypatch.setattr(database, "DB_PATH", tmp_path / "users.db")  # a second user's job must not leak
    conn = init_db()
    owner, alice = users.owner(conn).id, users.invite(conn, "alice-stop@example.com").id
    conn.execute("INSERT INTO jobs (user_id, url) VALUES (?, 'https://x/job/a')", (alice,))
    conn.commit()

    def run(user_id=None, **kwargs):
        return {"stopped": "budget"} if user_id == alice else {"scored": 1}

    with patch("strelitzia.scoring.scorer.run_scoring", side_effect=run) as scoring:
        assert pipeline._run_score() == {"status": "ok"}
    assert [c.kwargs["user_id"] for c in scoring.call_args_list] == [owner, alice]
