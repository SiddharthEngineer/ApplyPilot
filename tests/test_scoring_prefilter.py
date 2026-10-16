"""Title pre-filter skips clearly irrelevant jobs before the LLM."""

from unittest.mock import MagicMock, patch

import pytest

from strelitzia.database import init_db
from strelitzia.scoring import scorer
from strelitzia.scoring.preferences import Preferences, save_preferences
from strelitzia.profile import UserProfile
from fit_helpers import add_extracted_job, default_fit, reply

JOBS = {
    "https://example.com/de": "Senior Data Engineer",
    "https://example.com/rn": "Registered Nurse",
}


@pytest.fixture
def conn(tmp_path):
    c = init_db(tmp_path / "t.db")
    for url, title in JOBS.items():
        add_extracted_job(c, url, title=title)
    return c


def _run(conn, tmp_path, **kwargs):
    resume = tmp_path / "resume.txt"
    resume.write_text("resume")
    client = MagicMock()
    client.chat.return_value = reply(skills=4)
    with patch.object(scorer, "get_connection", return_value=conn), \
            patch.object(scorer, "load_resume_text", return_value=resume.read_text()), \
            patch.object(scorer, "get_client", return_value=client), \
            patch.object(scorer, "load_search_config", return_value={"queries": [{"query": "Data Engineer"}]}), \
            patch.object(scorer, "load_profile", return_value=UserProfile()):
        stats = scorer.run_scoring(**kwargs)
    return stats, client


def _score(conn, url):
    return tuple(conn.execute("SELECT fit_score, score_reasoning, fit_flags FROM jobs WHERE url = ?",
                              (url,)).fetchone())


def test_tokens_drop_seniority_and_stopwords():
    assert scorer._title_tokens("Senior Data Engineer II - Remote") == {"data", "engineer"}
    assert scorer._title_tokens("C++ / .NET Developer") == {"c++", "net", "developer"}


def test_relevant_title_passes_and_nurse_is_filtered(conn, tmp_path, caplog):
    with caplog.at_level("INFO", logger="strelitzia.scoring.scorer"):
        stats, client = _run(conn, tmp_path)
    assert client.chat.call_count == 1  # only the Data Engineer job reached the LLM
    assert _score(conn, "https://example.com/de")[0] == default_fit(4)
    assert _score(conn, "https://example.com/rn") == (0, scorer.PREFILTER_REASONING, '["prefilter"]')
    assert stats["prefiltered"] == 1 and stats["scored"] == 1
    assert "prefilter skipped 1/2 jobs" in caplog.text


def test_no_prefilter_scores_both(conn, tmp_path):
    stats, client = _run(conn, tmp_path, prefilter=False)
    assert client.chat.call_count == 2
    assert _score(conn, "https://example.com/rn")[:3:2] == (default_fit(4), None)
    assert stats["prefiltered"] == 0


def test_profile_target_role_counts():
    jobs = [{"title": "Machine Learning Scientist"}, {"title": "Registered Nurse"}]
    profile = UserProfile(extras={"experience": {"target_role": "ML Engineer, Machine Learning"}})
    with patch.object(scorer, "load_search_config", return_value={}):
        keep, skipped = scorer.prefilter_jobs(jobs, scorer.load_target_tokens(None, profile))
    assert [j["title"] for j in keep] == ["Machine Learning Scientist"]
    assert [j["title"] for j in skipped] == ["Registered Nurse"]


def test_no_targets_filters_nothing():
    jobs = [{"title": "Registered Nurse"}]
    assert scorer.prefilter_jobs(jobs, set()) == (jobs, [])


def test_cli_flag_reaches_scoring():
    from strelitzia import pipeline, users
    owner = users.owner(init_db()).id  # the pipeline's start creates the DB; only the owner has jobs
    with patch("strelitzia.scoring.scorer.run_scoring", return_value={}) as rs:
        pipeline._run_score(prefilter=False)
    rs.assert_called_once_with(prefilter=False, user_id=owner)


def test_limit_caps_llm_calls_after_prefilter(tmp_path):
    c = init_db(tmp_path / "lim.db")
    rows = [("https://example.com/rn", "Registered Nurse")] + [
        (f"https://example.com/de{i}", "Data Engineer") for i in range(5)
    ]
    for url, title in rows:
        add_extracted_job(c, url, title=title)
    stats, client = _run(c, tmp_path, limit=2)
    assert client.chat.call_count == 2
    assert stats["scored"] == 2 and stats["prefiltered"] == 1  # the prefilter still covers every pending job
    assert c.execute("SELECT COUNT(*) FROM jobs WHERE assessment_hash IS NULL").fetchone()[0] == 3


def test_limit_reaches_scoring():
    from strelitzia import pipeline, users
    owner = users.owner(init_db()).id
    with patch("strelitzia.scoring.scorer.run_scoring", return_value={}) as rs:
        pipeline._run_score(prefilter=True, limit=2)
    rs.assert_called_once_with(prefilter=True, limit=2, user_id=owner)


def test_sync_flag_reaches_score_and_extract():
    from strelitzia import pipeline, users

    owner = users.owner(init_db()).id
    with patch("strelitzia.scoring.scorer.run_scoring", return_value={}) as rs:
        pipeline._run_score(prefilter=True, sync=True)
    rs.assert_called_once_with(prefilter=True, mode="sync", user_id=owner)
    with patch("strelitzia.enrichment.classify.run_classify"), \
            patch("strelitzia.enrichment.extract.run_extraction", return_value={}) as rx:
        pipeline._run_extract(limit=3, sync=True)
    rx.assert_called_once_with(limit=3, mode="sync")


def test_preferences_target_roles_count(conn, tmp_path):
    prefs = Preferences(target_roles=["Registered Nurse"])
    save_preferences(conn, prefs)
    stats, client = _run(conn, tmp_path)
    assert stats["prefiltered"] == 0 and client.chat.call_count == 2


def test_a_prefiltered_job_is_not_picked_up_again(conn, tmp_path):
    _run(conn, tmp_path)
    stats, client = _run(conn, tmp_path)
    assert client.chat.call_count == 0 and stats["scored"] == 0
