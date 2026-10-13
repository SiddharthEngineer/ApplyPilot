"""LLM scoring errors leave jobs unscored and retryable (scoring-error-recovery plan)."""

from unittest.mock import MagicMock, patch

import anthropic
import httpx2
import pytest

from strelitzia.database import MAX_SCORE_ATTEMPTS, get_jobs_by_stage, get_stats, init_db, reset_score_errors
from strelitzia.scoring import scorer

JOB_URL = "https://example.com/job/1"


@pytest.fixture
def conn(tmp_path):
    c = init_db(tmp_path / "t.db")
    c.execute(
        "INSERT INTO jobs (url, title, site, location, full_description) VALUES (?, ?, ?, ?, ?)",
        (JOB_URL, "Engineer", "indeed", "Remote", "Build things."),
    )
    c.commit()
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
            patch.object(scorer, "RESUME_PATH", resume), \
            patch.object(scorer, "get_client", return_value=client):
        return scorer.run_scoring(prefilter=False)


def _row(conn):
    return conn.execute(
        "SELECT fit_score, score_reasoning, scored_at, score_attempts FROM jobs WHERE url = ?", (JOB_URL,)
    ).fetchone()


def _http_404(*_args, **_kwargs):
    req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    raise anthropic.NotFoundError("model not found", response=httpx2.Response(404, request=req), body=None)


def test_score_job_error_returns_none():
    client = MagicMock()
    client.chat.side_effect = RuntimeError("boom")
    with patch.object(scorer, "get_client", return_value=client):
        result = scorer.score_job("resume", {"title": "T", "site": "s", "full_description": "d"})
    assert result["score"] is None
    assert result["error"] == "boom"
    assert result["reasoning"].startswith("LLM error")


def test_unparseable_reply_is_an_error():
    client = MagicMock()
    client.chat.return_value = "I cannot help with that."
    with patch.object(scorer, "get_client", return_value=client):
        result = scorer.score_job("resume", {"title": "T", "site": "s", "full_description": "d"})
    assert result["score"] is None
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
    _run(conn, resume, lambda *a, **k: '{"score": 8, "keywords": "python, sql", "reasoning": "Good match."}')
    fit_score, reasoning, scored_at, attempts = _row(conn)
    assert fit_score == 8 and isinstance(fit_score, int)
    assert "Good match." in reasoning
    assert scored_at is not None
    assert attempts == 0


def test_success_after_error(conn, resume):
    _run(conn, resume, RuntimeError("flaky"))
    _run(conn, resume, lambda *a, **k: '{"score": 5, "keywords": "x", "reasoning": "Okay."}')
    assert _row(conn)[0] == 5


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
        "INSERT INTO jobs (url, full_description, fit_score, score_reasoning, scored_at, score_attempts) "
        "VALUES (?, 'desc', ?, ?, ?, ?)",
        rows,
    )
    c.commit()

    assert reset_score_errors(c) == 2
    reset = c.execute(
        "SELECT fit_score, score_reasoning, scored_at, score_attempts FROM jobs WHERE url != 'https://e.com/3'"
    ).fetchall()
    assert all(tuple(r) == (None, None, None, 0) for r in reset)
    assert tuple(c.execute("SELECT fit_score, score_reasoning FROM jobs WHERE url = 'https://e.com/3'").fetchone()) \
        == (7, "python\nGood fit.")
    assert {r["url"] for r in get_jobs_by_stage(c, stage="pending_score")} == {"https://e.com/1", "https://e.com/2"}


def test_cli_reset_errors(tmp_path):
    from typer.testing import CliRunner

    from strelitzia.cli import app

    with patch("strelitzia.cli._bootstrap"), \
            patch("strelitzia.database.reset_score_errors", return_value=326) as reset, \
            patch("strelitzia.pipeline.run_pipeline") as pipeline:
        result = CliRunner().invoke(app, ["run", "score", "--reset-errors"])
    assert result.exit_code == 0, result.output
    assert "Reset 326 job(s)" in result.output
    reset.assert_called_once()
    pipeline.assert_not_called()


def test_each_score_is_saved_before_the_next_call(tmp_path, resume):
    """A crash mid-run keeps the scores already returned (they're committed per call)."""
    c = init_db(tmp_path / "per_call.db")
    c.executemany(
        "INSERT INTO jobs (url, title, site, location, full_description) VALUES (?, 'Engineer', 'indeed', 'Remote', 'd')",
        [("https://example.com/a",), ("https://example.com/b",)],
    )
    c.commit()
    replies = iter(['{"score": 8, "keywords": "k", "reasoning": "r"}'])

    def chat(*_a, **_k):
        try:
            return next(replies)
        except StopIteration:
            raise KeyboardInterrupt from None

    with pytest.raises(KeyboardInterrupt):
        _run(c, resume, chat)
    scores = dict(c.execute("SELECT url, fit_score FROM jobs").fetchall())
    assert sorted(scores.values(), key=lambda v: (v is None, v)) == [8, None]


# ── Batch mode ───────────────────────────────────────────────────────────

BATCH_URLS = [f"https://example.com/batch/{i}" for i in range(8)]


@pytest.fixture
def batch_conn(tmp_path):
    c = init_db(tmp_path / "b.db")
    c.executemany("INSERT INTO jobs (url, title, site, full_description) VALUES (?, 'Engineer', 'acme', 'd')",
                  [(u,) for u in BATCH_URLS])
    c.commit()
    from strelitzia.database import backfill_job_keys
    backfill_job_keys(c)
    return c


def _run_batch_mode(conn, resume, results, budget_error=None, resumed=None):
    """run_scoring in batch mode; `results(custom_id)` gives (text, error) per job."""
    from strelitzia.database import job_key
    from strelitzia.llm import LLMClient

    seen = {}

    def fake_run_batch(purpose, items, on_result, poll_seconds=30):
        seen["items"] = items
        for cid, _ in reversed(items):
            on_result(cid, *results(cid))
        return {}

    def fake_resume(purpose, on_result, poll_seconds=30):
        for url, text in (resumed or {}).items():
            on_result(job_key(url), text, None)
        return {}

    client = MagicMock()
    client.request_params.side_effect = LLMClient("scoring", "claude-haiku-5-5", "low").request_params
    client.model = "claude-haiku-5-5"
    with patch.object(scorer, "get_connection", return_value=conn), \
            patch.object(scorer, "RESUME_PATH", resume), \
            patch.object(scorer, "get_client", return_value=client), \
            patch.object(scorer, "run_batch", side_effect=fake_run_batch), \
            patch.object(scorer, "resume_open_batches", side_effect=fake_resume), \
            patch.object(scorer, "estimate_cost", return_value=0.04), \
            patch.object(scorer, "check_budget", side_effect=budget_error) as budget:
        stats = scorer.run_scoring(prefilter=False, poll_seconds=0)
    client.chat.assert_not_called()
    return stats, seen.get("items"), budget


def _score_of(conn, url):
    return tuple(conn.execute("SELECT fit_score, score_attempts FROM jobs WHERE url = ?", (url,)).fetchone())


def test_batch_scores_every_job_by_custom_id(batch_conn, resume):
    from strelitzia.database import job_key
    scores = {job_key(u): 1 + i for i, u in enumerate(BATCH_URLS)}
    stats, items, budget = _run_batch_mode(
        batch_conn, resume, lambda cid: (f'{{"score": {scores[cid]}, "keywords": "", "reasoning": "r"}}', None))
    assert stats["mode"] == "batch" and stats["scored"] == 8 and stats["estimate_usd"] == 0.04
    budget.assert_called_once_with(0.04)
    assert [_score_of(batch_conn, u)[0] for u in BATCH_URLS] == list(range(1, 9))
    params = items[0][1]
    assert params["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "Test resume" in params["system"][0]["text"]
    assert params["output_config"]["format"]["schema"] == scorer.SCORE_SCHEMA


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
    stats, items, _ = _run_batch_mode(
        batch_conn, resume, lambda cid: ('{"score": 6, "keywords": "", "reasoning": "r"}', None),
        resumed={BATCH_URLS[0]: '{"score": 9, "keywords": "", "reasoning": "old run"}'})
    assert stats["resumed"] == 1 and len(items) == 7
    assert _score_of(batch_conn, BATCH_URLS[0])[0] == 9
