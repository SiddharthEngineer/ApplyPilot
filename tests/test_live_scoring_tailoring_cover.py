"""Live Claude smoke tests: scoring, extraction (sync + prompt caching) and a 2-job batch.

Resumes and cover letters are written in Claude chats now (strelitzia.scoring.workbench), so nothing here tailors.

Marked @llm @expensive (real Claude API calls, about $0.05 in all). Run with:
    pytest -m llm --run-llm tests/test_live_scoring_tailoring_cover.py -q
Needs ANTHROPIC_API_KEY. Usage rows go to a temp database, so the real ledger and budget are untouched.
"""

import json
import os
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def ledger(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """Default models, a temp usage ledger, fresh clients. Yields the ledger connection.

    # live/llm env exception — clears per-stage overrides so the packaged Claude defaults are what's tested
    """
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set")
    import strelitzia.llm as llm_mod
    from strelitzia.database import init_db

    for purpose in llm_mod.PURPOSES:
        monkeypatch.delenv(f"LLM_{purpose.upper()}_MODEL", raising=False)
        monkeypatch.delenv(f"LLM_{purpose.upper()}_EFFORT", raising=False)
    conn = init_db(tmp_path / "live.db")
    monkeypatch.setattr(llm_mod, "_ledger_conn", lambda: conn)
    llm_mod.reset_clients()
    yield conn
    llm_mod.reset_clients()


def _usage(conn, purpose: str) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM llm_usage WHERE purpose = ? ORDER BY ts, rowid",
                                          (purpose,)).fetchall()]


def _load_enriched_jobs() -> list[dict]:
    """Load enriched jobs from fixture or create synthetic."""
    path = FIXTURES_DIR / "jobs_enriched.json"
    if path.exists():
        with open(path) as f:
            return json.load(f)[:2]
    return [
        {
            "url": "https://example.com/job/1",
            "title": "Software Engineer",
            "site": "indeed",
            "location": "San Francisco, CA",
            "full_description": "We are looking for a software engineer with Python and AWS experience.",
        },
        {
            "url": "https://example.com/job/2",
            "title": "Data Scientist",
            "site": "linkedin",
            "location": "Remote",
            "full_description": "We need a data scientist with machine learning skills.",
        },
    ]


def _load_resume() -> str:
    """Load resume from fixture or create synthetic."""
    path = FIXTURES_DIR / "resume_sample.txt"
    if path.exists():
        return path.read_text()
    return """Test User
San Francisco, CA | test@example.com

EXPERIENCE
Software Engineer | Test Corp | 2020 - Present
- Built scalable backend systems
- Led team of 3 engineers

SKILLS
Python, JavaScript, SQL, Git, Docker, AWS
"""


# ---------------------------------------------------------------------------
# Scoring tests
# ---------------------------------------------------------------------------

@pytest.mark.llm
@pytest.mark.expensive
class TestScoreJob:
    """Test assess_job with real LLM."""

    def test_assessment_has_every_rating(self, ledger):
        from strelitzia.scoring.preferences import Preferences
        from strelitzia.scoring.scorer import assess_job

        resume = _load_resume()
        job = _load_enriched_jobs()[0]

        result = assess_job(Preferences(skills_want=["Python"]), resume, job)

        a = result["assessment"]
        assert a is not None, result
        for key in ("skills_match", "experience_match", "domain_interest"):
            assert 1 <= a[key]["score"] <= 5 and len(a[key]["evidence"]) > 20, a
        assert isinstance(a["red_flags"], list) and a["summary"]
        (row,) = _usage(ledger, "scoring")
        assert row["model"] == "claude-haiku-5-5" and row["batch"] == 0 and row["cost_usd"] > 0
        assert row["job_url"] == job["url"]


# ---------------------------------------------------------------------------
# Extraction (sync) and prompt caching
# ---------------------------------------------------------------------------

@pytest.mark.llm
@pytest.mark.expensive
class TestExtraction:
    def test_sync_extraction_reads_the_cached_system_prompt(self, ledger):
        from strelitzia.enrichment.extract import extract_one

        jobs = _load_enriched_jobs()
        postings = [extract_one(job) for job in jobs]
        assert all(p.summary for p in postings)
        rows = _usage(ledger, "extract")
        assert len(rows) == 2
        # The extraction rules are well over the 512-token minimum, so the second request reads them from cache.
        assert rows[1]["cache_read_tokens"] > 0, rows


# ---------------------------------------------------------------------------
# Message Batches
# ---------------------------------------------------------------------------

@pytest.mark.llm
@pytest.mark.expensive
class TestBatch:
    def test_two_job_scoring_batch(self, ledger):
        from strelitzia.llm_batch import open_batches, run_batch
        from strelitzia.scoring.assessment import parse_assessment
        from strelitzia.scoring.preferences import Preferences
        from strelitzia.scoring.scorer import assessment_requests

        resume = _load_resume()
        jobs = _load_enriched_jobs()
        items = assessment_requests(Preferences(), resume, jobs)
        got = {}
        stats = run_batch("scoring", items, lambda cid, text, err: got.__setitem__(cid, (text, err)),
                          poll_seconds=15)
        assert stats["succeeded"] == 2, got
        for cid, _ in items:
            text, err = got[cid]
            assert err is None and parse_assessment(text) is not None
        rows = _usage(ledger, "scoring")
        assert len(rows) == 2 and all(r["batch"] == 1 for r in rows)
        assert open_batches() == []


# ---------------------------------------------------------------------------
# Tailoring tests
# ---------------------------------------------------------------------------
