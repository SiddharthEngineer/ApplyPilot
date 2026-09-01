"""Live JobSpy per-site smoke tests.

Each test hits one JobSpy board once with results_per_site=1.
Marked @live @expensive — run with: pytest -m live --run-live -v
"""

import sqlite3
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path: Path):
    """Use a temporary database for each test."""
    (tmp_path / "tailored_resumes").mkdir(exist_ok=True)
    (tmp_path / "cover_letters").mkdir(exist_ok=True)
    (tmp_path / "logs").mkdir(exist_ok=True)

    from applypilot.database import init_db

    conn = init_db(tmp_path / "applypilot.db")
    yield conn
    conn.close()


@pytest.mark.live
@pytest.mark.expensive
@pytest.mark.parametrize(
    "site",
    [
        "indeed",
        "linkedin",
        "zip_recruiter",
        "glassdoor",
        "google",
    ],
)
def test_jobspy_single_site(site: str, _isolated_db: sqlite3.Connection):
    """Run one JobSpy search per site and verify DB insert."""
    from applypilot.database import get_stats
    from applypilot.discovery.jobspy import search_jobs

    result = search_jobs(
        query="software engineer",
        location="San Francisco, CA",
        sites=[site],
        results_per_site=1,
        hours_old=72,
        conn=_isolated_db,
    )

    # Should not raise
    assert isinstance(result, dict)
    assert "total" in result

    total = result.get("total", 0)

    # Boards known to be flaky: allow 0 results (xfail)
    if site in ("indeed", "linkedin", "glassdoor", "google", "zip_recruiter") and total == 0:
        pytest.xfail(f"{site} returned 0 results (likely blocked/flaky)")

    # If we got results, verify DB was updated
    if total > 0:
        stats = get_stats(_isolated_db)
        assert stats["total"] >= 1, f"DB should have >=1 job after {site} search"
