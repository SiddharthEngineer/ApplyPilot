"""Tests for smartextract heuristic pre-filter (Task 2: zero-LLM skip)."""

from unittest.mock import patch, MagicMock

import pytest

from applypilot.discovery.smartextract import (
    _is_obviously_not_jobs,
    judge_api_responses,
)


# ---------------------------------------------------------------------------
# _is_obviously_not_jobs unit tests
# ---------------------------------------------------------------------------

class TestIsObviouslyNotJobs:
    """Unit tests for the deterministic heuristic filter."""

    def test_recaptcha_url_small_response_skipped(self):
        resp = {"url": "https://www.google.com/recaptcha/api2/userverify?k=abc", "size": 120}
        assert _is_obviously_not_jobs(resp) is True

    def test_telemetry_url_skipped(self):
        resp = {"url": "https://cdn.example.com/telemetry/v2/collect", "size": 500}
        assert _is_obviously_not_jobs(resp) is True

    def test_web_vitals_url_skipped(self):
        resp = {"url": "https://example.com/web-vitals/ga.js", "size": 300}
        assert _is_obviously_not_jobs(resp) is True

    def test_get_session_url_skipped(self):
        resp = {"url": "https://auth.example.com/get-session", "size": 80}
        assert _is_obviously_not_jobs(resp) is True

    def test_auth_url_skipped(self):
        resp = {"url": "https://api.example.com/auth/token", "size": 150}
        assert _is_obviously_not_jobs(resp) is True

    def test_prodregistry_url_skipped(self):
        resp = {"url": "https://prodregistry.internal/events", "size": 400}
        assert _is_obviously_not_jobs(resp) is True

    def test_algolia_telemetry_skipped(self):
        resp = {"url": "https://1234.algolia.com/1/telemetry/events", "size": 200}
        assert _is_obviously_not_jobs(resp) is True

    def test_telemetry_with_job_keys_not_skipped(self):
        """Even if URL matches blocklist, job-like keys override."""
        resp = {
            "url": "https://api.example.com/telemetry/jobs",
            "size": 5000,
            "first_item_keys": ["title", "company", "location"],
        }
        assert _is_obviously_not_jobs(resp) is False

    def test_auth_url_without_job_keys_skipped(self):
        """Nested first_item_keys are NOT checked by the heuristic (too slow);
        the LLM judge handles edge cases like this."""
        resp = {
            "url": "https://api.example.com/auth/listings",
            "size": 3000,
            "keys": ["results", "status"],
        }
        assert _is_obviously_not_jobs(resp) is True

    def test_normal_api_response_not_skipped(self):
        resp = {
            "url": "https://api.example.com/v1/jobs/search?q=data+scientist",
            "size": 8000,
            "first_item_keys": ["title", "company", "location", "url"],
        }
        assert _is_obviously_not_jobs(resp) is False

    def test_normal_api_no_keys_not_skipped(self):
        resp = {
            "url": "https://api.linkedin.com/v2/jobSearch?query=engineer",
            "size": 12000,
        }
        assert _is_obviously_not_jobs(resp) is False

    def test_small_response_no_blocklist_match_not_skipped(self):
        resp = {"url": "https://api.example.com/health", "size": 50}
        assert _is_obviously_not_jobs(resp) is False

    def test_reload_k_url_skipped(self):
        resp = {"url": "https://example.com/reload?k=xyz123", "size": 90}
        assert _is_obviously_not_jobs(resp) is True


# ---------------------------------------------------------------------------
# judge_api_responses integration tests
# ---------------------------------------------------------------------------

class TestJudgeApiResponsesHeuristic:
    """Verify that judge_api_responses applies heuristic before LLM."""

    @patch("applypilot.discovery.smartextract.get_discovery_client")
    def test_skips_obvious_non_jobs_without_llm(self, mock_get_client):
        """3 telemetry + 1 real response -> only 1 LLM call, not 4."""
        mock_client = MagicMock()
        mock_client.ask.return_value = '{"relevant": true, "reason": "job objects"}'
        mock_get_client.return_value = mock_client

        responses = [
            {"url": "https://example.com/recaptcha/verify", "size": 100, "status": 200},
            {"url": "https://example.com/telemetry/v2/collect", "size": 200, "status": 200},
            {"url": "https://example.com/get-session", "size": 80, "status": 200},
            {
                "url": "https://api.example.com/jobs/search",
                "size": 5000,
                "status": 200,
                "type": "array[25]",
                "first_item_keys": ["title", "company", "location"],
                "first_item_sample": {"title": "Data Scientist", "company": "Acme"},
            },
        ]

        result = judge_api_responses(responses)

        # Only 1 LLM call for the one non-skipped response
        assert mock_client.ask.call_count == 1
        # The real response was kept
        assert len(result) == 1
        assert result[0]["url"] == "https://api.example.com/jobs/search"

    @patch("applypilot.discovery.smartextract.get_discovery_client")
    def test_all_skipped_no_llm_call(self, mock_get_client):
        """If all responses are heuristic-skipped, no LLM call is made."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        responses = [
            {"url": "https://example.com/recaptcha/api", "size": 100, "status": 200},
            {"url": "https://example.com/telemetry/events", "size": 200, "status": 200},
        ]

        result = judge_api_responses(responses)

        assert mock_client.ask.call_count == 0
        assert result == []

    @patch("applypilot.discovery.smartextract.get_discovery_client")
    def test_no_responses_returns_empty(self, mock_get_client):
        result = judge_api_responses([])
        assert result == []
        mock_get_client.assert_not_called()

    @patch("applypilot.discovery.smartextract.get_discovery_client")
    def test_telemetry_with_job_keys_gets_llm_judged(self, mock_get_client):
        """A telemetry URL with job-like keys should NOT be heuristic-skipped."""
        mock_client = MagicMock()
        mock_client.ask.return_value = '{"relevant": true, "reason": "job listing data"}'
        mock_get_client.return_value = mock_client

        responses = [
            {
                "url": "https://api.example.com/telemetry/jobs",
                "size": 5000,
                "status": 200,
                "type": "array[10]",
                "first_item_keys": ["title", "company"],
            },
        ]

        result = judge_api_responses(responses)

        # Should still go through LLM (not heuristic-skipped)
        assert mock_client.ask.call_count == 1
        assert len(result) == 1
