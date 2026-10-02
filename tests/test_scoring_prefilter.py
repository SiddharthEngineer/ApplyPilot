"""Title pre-filter skips clearly irrelevant jobs before the LLM (gemini-free-tier-llm Task 4)."""

from unittest.mock import MagicMock, patch

import pytest

from applypilot.database import init_db
from applypilot.scoring import scorer

JOBS = {
    "https://example.com/de": "Senior Data Engineer",
    "https://example.com/rn": "Registered Nurse",
}


@pytest.fixture
def conn(tmp_path):
    c = init_db(tmp_path / "t.db")
    for url, title in JOBS.items():
        c.execute("INSERT INTO jobs (url, title, site, full_description) VALUES (?, ?, 'acme', 'desc')", (url, title))
    c.commit()
    return c


def _run(conn, tmp_path, **kwargs):
    resume = tmp_path / "resume.txt"
    resume.write_text("resume")
    client = MagicMock()
    client.chat.return_value = '{"score": 7, "keywords": "sql", "reasoning": "ok"}'
    with patch.object(scorer, "get_connection", return_value=conn), \
            patch.object(scorer, "RESUME_PATH", resume), \
            patch.object(scorer, "get_client", return_value=client), \
            patch.object(scorer, "load_search_config", return_value={"queries": [{"query": "Data Engineer"}]}), \
            patch.object(scorer, "load_profile", return_value={}):
        stats = scorer.run_scoring(**kwargs)
    return stats, client


def _score(conn, url):
    return tuple(conn.execute("SELECT fit_score, score_reasoning FROM jobs WHERE url = ?", (url,)).fetchone())


def test_tokens_drop_seniority_and_stopwords():
    assert scorer._title_tokens("Senior Data Engineer II - Remote") == {"data", "engineer"}
    assert scorer._title_tokens("C++ / .NET Developer") == {"c++", "net", "developer"}


def test_relevant_title_passes_and_nurse_is_filtered(conn, tmp_path, caplog):
    with caplog.at_level("INFO", logger="applypilot.scoring.scorer"):
        stats, client = _run(conn, tmp_path)
    assert client.chat.call_count == 1  # only the Data Engineer job reached the LLM
    assert _score(conn, "https://example.com/de")[0] == 7
    assert _score(conn, "https://example.com/rn") == (1, scorer.PREFILTER_REASONING)
    assert stats["prefiltered"] == 1 and stats["scored"] == 1
    assert "prefilter skipped 1/2 jobs" in caplog.text


def test_no_prefilter_scores_both(conn, tmp_path):
    stats, client = _run(conn, tmp_path, prefilter=False)
    assert client.chat.call_count == 2
    assert _score(conn, "https://example.com/rn")[0] == 7
    assert stats["prefiltered"] == 0


def test_profile_target_role_counts():
    jobs = [{"title": "Machine Learning Scientist"}, {"title": "Registered Nurse"}]
    with patch.object(scorer, "load_search_config", return_value={}), \
            patch.object(scorer, "load_profile", return_value={"experience": {"target_role": "ML Engineer, Machine Learning"}}):
        keep, skipped = scorer.prefilter_jobs(jobs, scorer.load_target_tokens())
    assert [j["title"] for j in keep] == ["Machine Learning Scientist"]
    assert [j["title"] for j in skipped] == ["Registered Nurse"]


def test_no_targets_filters_nothing():
    jobs = [{"title": "Registered Nurse"}]
    assert scorer.prefilter_jobs(jobs, set()) == (jobs, [])


def test_cli_flag_reaches_scoring():
    from applypilot import pipeline
    with patch("applypilot.scoring.scorer.run_scoring", return_value={}) as rs:
        pipeline._run_score(prefilter=False)
    rs.assert_called_once_with(prefilter=False)
