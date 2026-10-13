"""Live Claude smoke tests: scoring, extraction (sync + prompt caching), a 2-job batch, tailoring and cover letters.

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


def _load_profile() -> dict:
    """Load profile from fixture or create synthetic."""
    path = FIXTURES_DIR / "profile_anonymized.json"
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return {
        "name": "Test User",
        "email": "test@example.com",
        "phone": "555-0100",
        "location": "San Francisco, CA",
    }


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
    """Test score_job with real LLM."""

    def test_score_returns_valid_range(self, ledger):
        from strelitzia.scoring.scorer import score_job

        resume = _load_resume()
        job = _load_enriched_jobs()[0]

        result = score_job(resume, job)

        assert isinstance(result, dict)
        assert "score" in result
        assert 1 <= result["score"] <= 10, f"Score {result['score']} out of range"
        assert isinstance(result.get("keywords", ""), str)
        assert isinstance(result.get("reasoning", ""), str)
        assert len(result.get("reasoning", "")) > 20, "Reasoning too short"
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
        from strelitzia.database import job_key
        from strelitzia.llm import get_client
        from strelitzia.llm_batch import open_batches, run_batch
        from strelitzia.scoring.scorer import SCORE_SCHEMA, _parse_score_response, score_messages

        resume = _load_resume()
        jobs = _load_enriched_jobs()
        client = get_client("scoring")
        items = [(job_key(j["url"]), client.request_params(score_messages(resume, j), max_tokens=2048,
                                                           schema=SCORE_SCHEMA)) for j in jobs]
        got = {}
        stats = run_batch("scoring", items, lambda cid, text, err: got.__setitem__(cid, (text, err)),
                          poll_seconds=15)
        assert stats["succeeded"] == 2, got
        for cid, _ in items:
            text, err = got[cid]
            assert err is None and 1 <= _parse_score_response(text)["score"] <= 10
        rows = _usage(ledger, "scoring")
        assert len(rows) == 2 and all(r["batch"] == 1 for r in rows)
        assert open_batches() == []


# ---------------------------------------------------------------------------
# Tailoring tests
# ---------------------------------------------------------------------------

@pytest.mark.llm
@pytest.mark.expensive
class TestTailorResume:
    """Test tailor_resume with real LLM."""

    def test_tailor_returns_valid_output(self, ledger):
        from strelitzia.scoring.tailor import tailor_resume

        resume = _load_resume()
        job = _load_enriched_jobs()[0]
        profile = _load_profile()

        tailored, report = tailor_resume(
            resume, job, profile,
            validation_mode="lenient",
            max_retries=1,
        )

        assert isinstance(tailored, str)
        assert len(tailored) > 100, "Tailored resume too short"
        assert report["attempts"] >= 1
        assert report["validation_mode"] == "lenient"
        assert _usage(ledger, "tailor")[0]["model"] == "claude-sonnet-5-5"

    def test_tailor_no_llm_leak_phrases(self):
        from strelitzia.scoring.tailor import tailor_resume

        resume = _load_resume()
        job = _load_enriched_jobs()[0]
        profile = _load_profile()

        tailored, _ = tailor_resume(
            resume, job, profile,
            validation_mode="lenient",
            max_retries=1,
        )

        leak_phrases = [
            "as an AI", "I'm an AI", "I am an AI",
            "large language model", "GPT", "Claude",
        ]
        for phrase in leak_phrases:
            assert phrase.lower() not in tailored.lower(), (
                f"LLM leak phrase found: {phrase}"
            )


# ---------------------------------------------------------------------------
# Cover letter tests
# ---------------------------------------------------------------------------

@pytest.mark.llm
@pytest.mark.expensive
class TestCoverLetter:
    """Test generate_cover_letter with real LLM."""

    def test_cover_starts_with_dear(self, ledger):
        from strelitzia.scoring.cover_letter import generate_cover_letter

        resume = _load_resume()
        job = _load_enriched_jobs()[0]
        profile = _load_profile()

        letter = generate_cover_letter(
            resume, job, profile,
            validation_mode="lenient",
            max_retries=1,
        )

        assert isinstance(letter, str)
        assert letter.strip().startswith("Dear"), (
            f"Cover letter should start with 'Dear', got: {letter[:50]}"
        )
        assert _usage(ledger, "cover"), "no llm_usage row for the cover letter"

    def test_cover_no_llm_leak_phrases(self):
        from strelitzia.scoring.cover_letter import generate_cover_letter

        resume = _load_resume()
        job = _load_enriched_jobs()[0]
        profile = _load_profile()

        letter = generate_cover_letter(
            resume, job, profile,
            validation_mode="lenient",
            max_retries=1,
        )

        leak_phrases = [
            "as an AI", "I'm an AI", "I am an AI",
            "large language model", "GPT", "Claude",
        ]
        for phrase in leak_phrases:
            assert phrase.lower() not in letter.lower(), (
                f"LLM leak phrase found: {phrase}"
            )
