"""LLM assessment errors leave jobs unassessed and retryable (scoring-error-recovery plan)."""

from unittest.mock import MagicMock, patch

import anthropic
import httpx2
import pytest

from strelitzia.database import MAX_SCORE_ATTEMPTS, get_jobs_by_stage, get_stats, init_db, reset_score_errors
from strelitzia.scoring import scorer
from strelitzia.scoring.assessment import ASSESS_PROMPT, assessment_key
from strelitzia.scoring.preferences import Preferences
from fit_helpers import add_extracted_job, reply

JOB_URL = "https://example.com/job/1"


@pytest.fixture
def conn(tmp_path):
    c = init_db(tmp_path / "t.db")
    add_extracted_job(c, JOB_URL, title="Engineer", site="indeed", location="Remote")
    return c


@pytest.fixture
def resume(tmp_path):
    path = tmp_path / "resume.txt"
    path.write_text("Test resume")
    return path


def _run(conn, resume, chat):
    client = MagicMock()
    client.chat.side_effect = chat
    with patch.object(scorer, "get_connection", return_value=conn), \
            patch.object(scorer, "load_resume_text", return_value=resume.read_text()), \
            patch.object(scorer, "get_client", return_value=client):
        return scorer.run_scoring(prefilter=False)


def _row(conn):
    return conn.execute(
        "SELECT fit_score, score_reasoning, scored_at, score_attempts FROM jobs WHERE url = ?", (JOB_URL,)
    ).fetchone()


def _http_404(*_args, **_kwargs):
    req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    raise anthropic.NotFoundError("model not found", response=httpx2.Response(404, request=req), body=None)


JOB = {"title": "T", "site": "s", "full_description": "d"}


def test_assess_job_error_returns_none():
    client = MagicMock()
    client.chat.side_effect = RuntimeError("boom")
    with patch.object(scorer, "get_client", return_value=client):
        result = scorer.assess_job(Preferences(), "resume", JOB)
    assert result["assessment"] is None
    assert result["error"] == "boom"
    assert result["reasoning"].startswith("LLM error")


def test_unparseable_reply_is_an_error():
    client = MagicMock()
    client.chat.return_value = "I cannot help with that."
    with patch.object(scorer, "get_client", return_value=client):
        result = scorer.assess_job(Preferences(), "resume", JOB)
    assert result["assessment"] is None
    assert "unparseable" in result["reasoning"]


def test_error_leaves_fit_score_null(conn, resume):
    result = _run(conn, resume, _http_404)
    fit_score, reasoning, scored_at, attempts = _row(conn)
    assert fit_score is None
    assert scored_at is None
    assert reasoning.startswith("LLM error: HTTP 404")
    assert attempts == 1
    assert result["errors"] == 1 and result["scored"] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE score_reasoning LIKE 'LLM error%' AND fit_score IS NOT NULL"
    ).fetchone()[0] == 0


def test_retries_stop_after_max_attempts(conn, resume):
    for attempt in range(1, MAX_SCORE_ATTEMPTS + 1):
        assert len(get_jobs_by_stage(conn, stage="pending_score")) == 1
        _run(conn, resume, RuntimeError("timeout"))
        assert _row(conn)[3] == attempt
    assert get_jobs_by_stage(conn, stage="pending_score") == []
    assert get_stats(conn)["unscored"] == 0


def test_success_stores_integer_score(conn, resume):
    _run(conn, resume, lambda *a, **k: reply(skills=5, summary="Good match."))
    fit_score, reasoning, scored_at, attempts = _row(conn)
    assert fit_score == 100 and isinstance(fit_score, int)  # default preferences: only skills (5/5) counts
    assert "Good match." in reasoning and reasoning.startswith("SQL, Python")
    assert scored_at is not None
    assert attempts == 0


def test_success_after_error(conn, resume):
    _run(conn, resume, RuntimeError("flaky"))
    _run(conn, resume, lambda *a, **k: reply(skills=3))
    assert _row(conn)[0] == 50


def test_reset_score_errors(tmp_path):
    c = init_db(tmp_path / "r.db")
    rows = [
        # Old behavior: error saved as score 0, with empty keywords + newline in front (real DB format).
        ("https://e.com/1", 0, "\nLLM error: Client error '404 Not Found'", "2026-08-27", 0),
        # New behavior: NULL score, attempts exhausted.
        ("https://e.com/2", None, "LLM error: timeout", None, 3),
        # Real score.
        ("https://e.com/3", 7, "python\nGood fit.", "2026-08-27", 0),
    ]
    c.executemany(
        "INSERT INTO jobs (url, full_description, details_json, fit_score, score_reasoning, scored_at, "
        "score_attempts) VALUES (?, 'desc', '{}', ?, ?, ?, ?)",
        rows,
    )
    c.execute("UPDATE jobs SET assessment_hash = '1:h' WHERE url = 'https://e.com/3'")
    c.commit()

    assert reset_score_errors(c) == 2
    reset = c.execute(
        "SELECT fit_score, score_reasoning, scored_at, score_attempts FROM jobs WHERE url != 'https://e.com/3'"
    ).fetchall()
    assert all(tuple(r) == (None, None, None, 0) for r in reset)
    assert tuple(c.execute("SELECT fit_score, score_reasoning FROM jobs WHERE url = 'https://e.com/3'").fetchone()) \
        == (7, "python\nGood fit.")
    assert {r["url"] for r in get_jobs_by_stage(c, stage="pending_score")} == {"https://e.com/1", "https://e.com/2"}


def test_each_score_is_saved_before_the_next_call(tmp_path, resume):
    """A crash mid-run keeps the scores already returned (they're committed per call)."""
    c = init_db(tmp_path / "per_call.db")
    add_extracted_job(c, "https://example.com/a")
    add_extracted_job(c, "https://example.com/b")
    replies = iter([reply(skills=4)])

    def chat(*_a, **_k):
        try:
            return next(replies)
        except StopIteration:
            raise KeyboardInterrupt from None

    with pytest.raises(KeyboardInterrupt):
        _run(c, resume, chat)
    scores = dict(c.execute("SELECT url, fit_score FROM jobs").fetchall())
    assert sorted(scores.values(), key=lambda v: (v is None, v)) == [75, None]


# ── Batch mode ───────────────────────────────────────────────────────────

BATCH_URLS = [f"https://example.com/batch/{i}" for i in range(8)]
CURRENT_KEY = assessment_key(Preferences(), "Test resume")


@pytest.fixture
def batch_conn(tmp_path):
    c = init_db(tmp_path / "b.db")
    for u in BATCH_URLS:
        add_extracted_job(c, u)
    from strelitzia.database import backfill_job_keys
    backfill_job_keys(c)
    return c


def _run_batch_mode(conn, resume, results, budget_error=None, resumed=None, resume_error=None):
    """run_scoring in batch mode; `results(custom_id)` gives (text, error) per job."""
    from strelitzia.llm import LLMClient

    seen = {}

    def fake_run_batch(purpose, items, on_result, poll_seconds=30, estimate_usd=0.0, conn=None, user_id=None):
        seen["items"] = items
        seen["estimate_usd"] = estimate_usd
        for cid, _ in reversed(items):
            on_result(cid, *results(cid))
        return {}

    def fake_resume(purpose, on_result, poll_seconds=30, wait=True, conn=None, user_id=None):
        seen["wait"] = wait
        if resume_error:
            raise resume_error
        for url, text in (resumed or {}).items():
            text, key = text if isinstance(text, tuple) else (text, CURRENT_KEY)
            on_result(scorer.batch_custom_id(url, key), text, None)
        return {"still_open": 0}

    client = MagicMock()
    client.request_params.side_effect = LLMClient("scoring", "claude-haiku-5-5", "low").request_params
    client.model = "claude-haiku-5-5"
    with patch.object(scorer, "get_connection", return_value=conn), \
            patch.object(scorer, "load_resume_text", return_value=resume.read_text()), \
            patch.object(scorer, "get_client", return_value=client), \
            patch.object(scorer, "run_batch", side_effect=fake_run_batch), \
            patch.object(scorer, "resume_open_batches", side_effect=fake_resume), \
            patch.object(scorer, "estimate_cost", return_value=0.04), \
            patch.object(scorer, "check_budget", side_effect=budget_error) as budget:
        stats = scorer.run_scoring(prefilter=False, poll_seconds=0)
    client.chat.assert_not_called()
    seen_items = seen.get("items")
    budget.seen = seen
    return stats, seen_items, budget


def _score_of(conn, url):
    return tuple(conn.execute("SELECT fit_score, score_attempts FROM jobs WHERE url = ?", (url,)).fetchone())


def test_batch_scores_every_job_by_custom_id(batch_conn, resume):
    from strelitzia.database import job_key
    skills = {job_key(u): 1 + i % 5 for i, u in enumerate(BATCH_URLS)}
    stats, items, budget = _run_batch_mode(batch_conn, resume,
                                           lambda cid: (reply(skills=skills[cid.split("-")[0]]), None))
    assert stats["mode"] == "batch" and stats["scored"] == 8 and stats["estimate_usd"] == 0.04
    assert budget.call_args.args[0] == 0.04
    assert budget.seen["estimate_usd"] == 0.04 and budget.seen["wait"] is True  # reserved against the budget
    assert [_score_of(batch_conn, u)[0] for u in BATCH_URLS] == [0, 25, 50, 75, 100, 0, 25, 50]
    params = items[0][1]
    assert [b["cache_control"] for b in params["system"]] == [{"type": "ephemeral"}] * 2
    assert params["system"][0]["text"] == ASSESS_PROMPT
    assert "Test resume" in params["system"][1]["text"]
    assert params["output_config"]["format"]["schema"] == scorer.ASSESS_SCHEMA


def test_batch_errors_count_an_attempt(batch_conn, resume):
    stats, _, _ = _run_batch_mode(batch_conn, resume, lambda cid: (None, "expired"))
    assert stats["errors"] == 8 and stats["scored"] == 0
    assert _score_of(batch_conn, BATCH_URLS[0]) == (None, 1)


def test_batch_budget_stop_submits_nothing(batch_conn, resume):
    from strelitzia.llm import LLMStopRun
    stats, items, _ = _run_batch_mode(batch_conn, resume, lambda cid: (None, "x"),
                                      budget_error=LLMStopRun("budget", "over"))
    assert stats["stopped"] == "budget" and items is None
    assert _score_of(batch_conn, BATCH_URLS[0]) == (None, 0)


def test_open_batches_are_collected_first(batch_conn, resume):
    from strelitzia.scoring.assessment import assessment_key
    from strelitzia.scoring.preferences import Preferences
    key = assessment_key(Preferences(), "Test resume")
    batch_conn.execute("UPDATE jobs SET fit_score = 40, assessment_hash = ? WHERE url = ?",
                       (key, BATCH_URLS[1]))  # assessed since, with the current inputs
    batch_conn.commit()
    stats, items, _ = _run_batch_mode(
        batch_conn, resume, lambda cid: (reply(skills=3), None),
        resumed={BATCH_URLS[0]: reply(skills=5), BATCH_URLS[1]: reply(skills=1)})
    assert stats["resumed"] == 1 and len(items) == 6
    assert _score_of(batch_conn, BATCH_URLS[0])[0] == 100
    assert _score_of(batch_conn, BATCH_URLS[1])[0] == 40  # the newer assessment wins


def test_a_failed_resume_stops_before_paying_again(batch_conn, resume):
    from strelitzia.llm import LLMStopRun
    stats, items, _ = _run_batch_mode(batch_conn, resume, lambda cid: (None, "x"),
                                      resume_error=LLMStopRun("overloaded"))
    assert stats["stopped"] == "overloaded" and items is None


def test_sync_runs_collect_only_ended_batches(conn, resume):
    client = MagicMock()
    client.chat.return_value = reply(skills=3)
    with patch.object(scorer, "get_connection", return_value=conn), \
            patch.object(scorer, "load_resume_text", return_value=resume.read_text()), \
            patch.object(scorer, "get_client", return_value=client), \
            patch.object(scorer, "resume_open_batches", return_value={"still_open": 1}) as resume_batches:
        assert scorer.run_scoring(prefilter=False, mode="sync")["mode"] == "sync"
    assert resume_batches.call_args.kwargs["wait"] is False
    assert _row(conn)[0] == 50


def test_a_leftover_batch_made_with_other_inputs_is_saved_as_stale(batch_conn, resume):
    old_key = assessment_key(Preferences(skills_want=["Cobol"]), "Test resume")
    stats, _, _ = _run_batch_mode(batch_conn, resume, lambda cid: (reply(skills=3), None),
                                  resumed={BATCH_URLS[0]: (reply(skills=5), old_key)})
    assert stats["resumed"] == 1
    row = batch_conn.execute("SELECT fit_score, assessment_hash FROM jobs WHERE url = ?", (BATCH_URLS[0],)).fetchone()
    assert row[0] == 100 and row[1] != CURRENT_KEY and old_key.startswith(row[1])  # stale: truncated old hash


def test_old_scorer_batch_results_are_skipped(batch_conn, resume):
    from strelitzia.database import job_key
    assert scorer._parse_custom_id(job_key(BATCH_URLS[0]), CURRENT_KEY) is None
    assert scorer._parse_custom_id(scorer.batch_custom_id(BATCH_URLS[0], CURRENT_KEY), CURRENT_KEY) == \
        (job_key(BATCH_URLS[0]), CURRENT_KEY)
