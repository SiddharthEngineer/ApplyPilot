"""LLM scoring errors leave jobs unscored and retryable (scoring-error-recovery plan)."""

from unittest.mock import MagicMock, patch

import httpx
import pytest

from applypilot.database import MAX_SCORE_ATTEMPTS, get_jobs_by_stage, get_stats, init_db, reset_score_errors
from applypilot.scoring import scorer

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
    req = httpx.Request("POST", "https://llm.example/v1/chat/completions")
    raise httpx.HTTPStatusError("404 Not Found", request=req, response=httpx.Response(404, request=req))


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
    _run(conn, resume, lambda *a, **k: "SCORE: 8\nKEYWORDS: python, sql\nREASONING: Good match.")
    fit_score, reasoning, scored_at, attempts = _row(conn)
    assert fit_score == 8 and isinstance(fit_score, int)
    assert "Good match." in reasoning
    assert scored_at is not None
    assert attempts == 0


def test_success_after_error(conn, resume):
    _run(conn, resume, RuntimeError("flaky"))
    _run(conn, resume, lambda *a, **k: "SCORE: 5\nKEYWORDS: x\nREASONING: Okay.")
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

    from applypilot.cli import app

    with patch("applypilot.cli._bootstrap"), \
            patch("applypilot.database.reset_score_errors", return_value=326) as reset, \
            patch("applypilot.pipeline.run_pipeline") as pipeline:
        result = CliRunner().invoke(app, ["run", "score", "--reset-errors"])
    assert result.exit_code == 0, result.output
    assert "Reset 326 job(s)" in result.output
    reset.assert_called_once()
    pipeline.assert_not_called()
