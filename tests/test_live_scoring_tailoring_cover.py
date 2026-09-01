"""Live LLM scoring/tailoring/cover smoke tests.

Tests score_job, tailor_resume, and generate_cover_letter against real Gemini API.
Marked @llm @expensive — run with: pytest -m llm --run-llm -v

Uses gemini-2.0-flash-lite for cost efficiency (~5x cheaper than gemini-3.6-flash).
"""

import json
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _setup_llm(request, monkeypatch: pytest.MonkeyPatch):
    """Configure LLM for tests and reset singletons.

    # live/llm env exception — model pin (no per-call model arg available)
    """
    from applypilot.llm import _detect_provider

    # Check if any provider is available
    try:
        _detect_provider()
    except RuntimeError:
        pytest.skip("No LLM provider configured")

    # Force flash-lite for cost efficiency
    monkeypatch.setenv("LLM_MODEL", "gemini-2.0-flash-lite")
    monkeypatch.setenv("LLM_DISCOVERY_MODEL", "gemini-2.0-flash-lite")

    import applypilot.llm as llm_mod
    llm_mod._instance = None
    llm_mod._discovery_instance = None

    yield

    llm_mod._instance = None
    llm_mod._discovery_instance = None


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

    def test_score_returns_valid_range(self):
        from applypilot.scoring.scorer import score_job

        resume = _load_resume()
        job = _load_enriched_jobs()[0]

        result = score_job(resume, job)

        assert isinstance(result, dict)
        assert "score" in result
        assert 1 <= result["score"] <= 10, f"Score {result['score']} out of range"
        assert isinstance(result.get("keywords", ""), str)
        assert isinstance(result.get("reasoning", ""), str)
        assert len(result.get("reasoning", "")) > 20, "Reasoning too short"

    def test_score_uses_flash_lite(self):
        from applypilot.llm import get_client
        from applypilot.scoring.scorer import score_job

        resume = _load_resume()
        job = _load_enriched_jobs()[0]

        client = get_client()
        assert "flash-lite" in client.model or "flash" in client.model, (
            f"Expected flash-lite model, got {client.model}"
        )

        result = score_job(resume, job)
        assert 1 <= result["score"] <= 10


# ---------------------------------------------------------------------------
# Tailoring tests
# ---------------------------------------------------------------------------

@pytest.mark.llm
@pytest.mark.expensive
class TestTailorResume:
    """Test tailor_resume with real LLM."""

    def test_tailor_returns_valid_output(self):
        from applypilot.scoring.tailor import tailor_resume

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

    def test_tailor_no_llm_leak_phrases(self):
        from applypilot.scoring.tailor import tailor_resume

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

    def test_cover_starts_with_dear(self):
        from applypilot.scoring.cover_letter import generate_cover_letter

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

    def test_cover_no_llm_leak_phrases(self):
        from applypilot.scoring.cover_letter import generate_cover_letter

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
