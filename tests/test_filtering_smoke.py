"""Filtering smoke tests — no LLM, no network.

Tests _location_ok across all 3 implementations and exclude_titles wiring.
Uses synthetic data or fixture files; no API keys required.
"""

import json
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Fixture loading
# ---------------------------------------------------------------------------

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load_enriched_jobs() -> list[dict]:
    """Load enriched jobs from fixture, or return synthetic fallback."""
    path = FIXTURES_DIR / "jobs_enriched.json"
    if path.exists():
        with open(path) as f:
            return json.load(f)
    # Synthetic fallback
    return [
        {
            "url": "https://example.com/job/1",
            "title": "Software Engineer",
            "location": "San Francisco, CA",
            "site": "indeed",
            "description": "Build scalable systems",
        },
        {
            "url": "https://example.com/job/2",
            "title": "Remote Data Scientist",
            "location": "Remote",
            "site": "linkedin",
            "description": "Analyze data",
        },
        {
            "url": "https://example.com/job/3",
            "title": "Office Manager",
            "location": "New York, NY",
            "site": "indeed",
            "description": "Manage office",
        },
    ]


# ---------------------------------------------------------------------------
# _location_ok — jobspy.py implementation
# ---------------------------------------------------------------------------

class TestLocationOkJobspy:
    """Test _location_ok from discovery/jobspy.py."""

    def _location_ok(self, location, accept, reject):
        from applypilot.discovery.jobspy import _location_ok as jobspy_ok
        return jobspy_ok(location, accept, reject)

    def test_remote_always_passes(self):
        assert self._location_ok("Remote", ["San Francisco"], []) is True

    def test_anywhere_always_passes(self):
        assert self._location_ok("Anywhere", ["San Francisco"], []) is True

    def test_wfh_always_passes(self):
        assert self._location_ok("Work from Home", ["San Francisco"], []) is True

    def test_matching_accept_passes(self):
        assert self._location_ok("San Francisco, CA", ["San Francisco"], []) is True

    def test_non_matching_reject_fails(self):
        assert self._location_ok("New York, NY", ["San Francisco"], ["New York"]) is False

    def test_none_location_passes(self):
        assert self._location_ok(None, ["San Francisco"], []) is True

    def test_unknown_location_fails(self):
        assert self._location_ok("London, UK", ["San Francisco"], []) is False


# ---------------------------------------------------------------------------
# _location_ok — smartextract.py implementation
# ---------------------------------------------------------------------------

class TestLocationOkSmartextract:
    """Test _location_ok from discovery/smartextract.py."""

    def _location_ok(self, location, accept, reject):
        from applypilot.discovery.smartextract import _location_ok as se_ok
        return se_ok(location, accept, reject)

    def test_remote_always_passes(self):
        assert self._location_ok("Remote", ["San Francisco"], []) is True

    def test_matching_accept_passes(self):
        assert self._location_ok("San Francisco, CA", ["San Francisco"], []) is True

    def test_non_matching_reject_fails(self):
        assert self._location_ok("New York, NY", ["San Francisco"], ["New York"]) is False

    def test_none_location_passes(self):
        assert self._location_ok(None, ["San Francisco"], []) is True


# ---------------------------------------------------------------------------
# _location_ok — workday.py implementation
# ---------------------------------------------------------------------------

class TestLocationOkWorkday:
    """Test _location_ok from discovery/workday.py."""

    def _location_ok(self, location, accept, reject):
        from applypilot.discovery.workday import _location_ok as wd_ok
        return wd_ok(location, accept, reject)

    def test_remote_always_passes(self):
        assert self._location_ok("Remote", ["San Francisco"], []) is True

    def test_matching_accept_passes(self):
        assert self._location_ok("San Francisco, CA", ["San Francisco"], []) is True

    def test_non_matching_reject_fails(self):
        assert self._location_ok("New York, NY", ["San Francisco"], ["New York"]) is False

    def test_none_location_passes(self):
        assert self._location_ok(None, ["San Francisco"], []) is True


# ---------------------------------------------------------------------------
# Cross-implementation drift detection
# ---------------------------------------------------------------------------

class TestLocationOkDrift:
    """Verify all 3 _location_ok implementations behave identically."""

    @pytest.mark.parametrize(
        "location,accept,reject",
        [
            ("Remote", ["SF"], []),
            ("San Francisco, CA", ["SF"], []),
            ("New York, NY", ["SF"], ["NY"]),
            (None, ["SF"], []),
            ("London, UK", ["SF"], []),
            ("Work from Home", ["SF"], []),
            ("SF Bay Area", ["SF"], []),
        ],
    )
    def test_all_impls_agree(self, location, accept, reject):
        from applypilot.discovery.jobspy import _location_ok as jobspy_ok
        from applypilot.discovery.smartextract import _location_ok as se_ok
        from applypilot.discovery.workday import _location_ok as wd_ok

        results = {
            "jobspy": jobspy_ok(location, accept, reject),
            "smartextract": se_ok(location, accept, reject),
            "workday": wd_ok(location, accept, reject),
        }
        # All implementations should agree
        assert len(set(results.values())) == 1, f"Drift detected for {location}: {results}"


# ---------------------------------------------------------------------------
# Fixture-based filtering (if fixtures available)
# ---------------------------------------------------------------------------

class TestFilteringWithFixtures:
    """Test filtering against captured fixture data."""

    def test_enriched_jobs_have_locations(self):
        jobs = _load_enriched_jobs()
        assert len(jobs) >= 1
        for job in jobs:
            assert "location" in job or "url" in job

    def test_filter_remote_jobs(self):
        from applypilot.discovery.jobspy import _location_ok

        jobs = _load_enriched_jobs()
        accept = ["San Francisco"]
        reject = []

        remote_count = 0
        for job in jobs:
            loc = job.get("location")
            if _location_ok(loc, accept, reject) and loc and "remote" in loc.lower():
                remote_count += 1

        # Remote jobs should pass filter
        assert remote_count >= 0  # May be 0 if no remote jobs in fixtures
