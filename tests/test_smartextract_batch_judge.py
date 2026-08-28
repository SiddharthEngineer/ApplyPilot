"""Tests for batched judge API responses (Task 3: single LLM call for N responses)."""

import json
from unittest.mock import MagicMock, patch

from applypilot.discovery.smartextract import (
    _format_response_summary,
    judge_api_responses,
)

# ---------------------------------------------------------------------------
# _format_response_summary unit tests
# ---------------------------------------------------------------------------

class TestFormatResponseSummary:
    """Verify the numbered summary formatter used in batch prompts."""

    def test_basic_array_response(self):
        resp = {
            "url": "https://api.example.com/jobs",
            "status": 200,
            "size": 5000,
            "type": "array[10]",
            "first_item_keys": ["title", "company"],
            "first_item_sample": {"title": "Engineer", "company": "Acme"},
        }
        summary = _format_response_summary(resp, 1)
        assert summary.startswith("[1]")
        assert "https://api.example.com/jobs" in summary
        assert "200" in summary
        assert "5000" in summary
        assert "title" in summary
        assert "company" in summary

    def test_object_response_with_nested(self):
        resp = {
            "url": "https://api.example.com/search",
            "status": 200,
            "size": 8000,
            "type": "object",
            "keys": ["results", "total"],
            "nested_results": {
                "count": 25,
                "first_item_keys": ["title", "location"],
                "first_item_sample": {"title": "Dev", "location": "NYC"},
            },
        }
        summary = _format_response_summary(resp, 3)
        assert "[3]" in summary
        assert "results" in summary
        assert "total" in summary
        assert "25 items" in summary

    def test_no_structured_data(self):
        resp = {
            "url": "https://example.com/unknown",
            "status": 200,
            "size": 500,
        }
        summary = _format_response_summary(resp, 2)
        assert "[2]" in summary
        assert "no structured data" in summary

    def test_sample_truncated_to_300_chars(self):
        resp = {
            "url": "https://api.example.com/big",
            "status": 200,
            "size": 50000,
            "type": "array[100]",
            "first_item_keys": ["title"],
            "first_item_sample": {"title": "x" * 500},
        }
        summary = _format_response_summary(resp, 1)
        # The sample line should be truncated
        assert "x" * 500 not in summary


# ---------------------------------------------------------------------------
# Batch judge happy path tests
# ---------------------------------------------------------------------------

class TestJudgeBatchHappyPath:
    """Verify batch judge makes exactly 1 LLM call for multiple candidates."""

    @patch("applypilot.discovery.smartextract.get_client")
    def test_five_responses_one_llm_call(self, mock_get_client):
        """5 non-heuristic-skipped responses -> exactly 1 LLM call."""
        mock_client = MagicMock()
        mock_client.ask.return_value = json.dumps([
            {"index": 1, "relevant": True, "reason": "job listings"},
            {"index": 2, "relevant": False, "reason": "auth endpoint"},
            {"index": 3, "relevant": True, "reason": "job search results"},
            {"index": 4, "relevant": False, "reason": "analytics pixel"},
            {"index": 5, "relevant": True, "reason": "job postings"},
        ])
        mock_get_client.return_value = mock_client

        responses = [
            {
                "url": f"https://api.example.com/endpoint/{i}",
                "status": 200,
                "size": 3000 + i * 1000,
                "type": "array[10]",
                "first_item_keys": ["title", "company", "location"],
                "first_item_sample": {"title": f"Job {i}", "company": "Corp"},
            }
            for i in range(5)
        ]

        result = judge_api_responses(responses)

        assert mock_client.ask.call_count == 1
        assert len(result) == 3
        assert result[0]["url"] == "https://api.example.com/endpoint/0"
        assert result[1]["url"] == "https://api.example.com/endpoint/2"
        assert result[2]["url"] == "https://api.example.com/endpoint/4"

    @patch("applypilot.discovery.smartextract.get_client")
    def test_prompt_contains_all_response_summaries(self, mock_get_client):
        """Batch prompt includes summaries for all candidates."""
        mock_client = MagicMock()
        mock_client.ask.return_value = json.dumps([
            {"index": 1, "relevant": True, "reason": "jobs"},
            {"index": 2, "relevant": False, "reason": "auth"},
        ])
        mock_get_client.return_value = mock_client

        responses = [
            {
                "url": "https://api.example.com/jobs",
                "status": 200,
                "size": 5000,
                "type": "array[5]",
                "first_item_keys": ["title", "company"],
                "first_item_sample": {"title": "Eng", "company": "X"},
            },
            {
                "url": "https://api.example.com/auth",
                "status": 200,
                "size": 200,
                "type": "object",
                "keys": ["token"],
            },
        ]

        judge_api_responses(responses)

        prompt_used = mock_client.ask.call_args[0][0]
        assert "[1]" in prompt_used
        assert "[2]" in prompt_used
        assert "https://api.example.com/jobs" in prompt_used
        assert "https://api.example.com/auth" in prompt_used

    @patch("applypilot.discovery.smartextract.get_client")
    def test_batch_all_relevant(self, mock_get_client):
        """All responses judged relevant are kept."""
        mock_client = MagicMock()
        mock_client.ask.return_value = json.dumps([
            {"index": 1, "relevant": True, "reason": "jobs"},
            {"index": 2, "relevant": True, "reason": "more jobs"},
        ])
        mock_get_client.return_value = mock_client

        responses = [
            {
                "url": "https://api.example.com/a",
                "status": 200,
                "size": 5000,
                "type": "array[5]",
                "first_item_keys": ["title"],
            },
            {
                "url": "https://api.example.com/b",
                "status": 200,
                "size": 5000,
                "type": "array[5]",
                "first_item_keys": ["title"],
            },
        ]

        result = judge_api_responses(responses)
        assert mock_client.ask.call_count == 1
        assert len(result) == 2

    @patch("applypilot.discovery.smartextract.get_client")
    def test_batch_all_irrelevant(self, mock_get_client):
        """All responses judged irrelevant -> empty result."""
        mock_client = MagicMock()
        mock_client.ask.return_value = json.dumps([
            {"index": 1, "relevant": False, "reason": "analytics"},
            {"index": 2, "relevant": False, "reason": "tracking"},
        ])
        mock_get_client.return_value = mock_client

        responses = [
            {
                "url": "https://api.example.com/a",
                "status": 200,
                "size": 5000,
                "type": "array[5]",
                "first_item_keys": ["title"],
            },
            {
                "url": "https://api.example.com/b",
                "status": 200,
                "size": 5000,
                "type": "array[5]",
                "first_item_keys": ["title"],
            },
        ]

        result = judge_api_responses(responses)
        assert mock_client.ask.call_count == 1
        assert len(result) == 0

    @patch("applypilot.discovery.smartextract.get_client")
    def test_batch_single_candidate_uses_sequential(self, mock_get_client):
        """With exactly 1 candidate after heuristic, uses sequential (single-call) path."""
        mock_client = MagicMock()
        mock_client.ask.return_value = '{"relevant": true, "reason": "jobs"}'
        mock_get_client.return_value = mock_client

        responses = [
            {
                "url": "https://api.example.com/jobs",
                "status": 200,
                "size": 5000,
                "type": "array[5]",
                "first_item_keys": ["title"],
            },
        ]

        result = judge_api_responses(responses)
        assert mock_client.ask.call_count == 1
        assert len(result) == 1


# ---------------------------------------------------------------------------
# Batch judge fallback tests
# ---------------------------------------------------------------------------

class TestJudgeBatchFallback:
    """Verify fallback to sequential when batch response is unparseable."""

    @patch("applypilot.discovery.smartextract.get_client")
    def test_invalid_json_falls_back_to_sequential(self, mock_get_client):
        """If batch response is unparseable, falls back to N sequential calls."""
        mock_client = MagicMock()
        # First call (batch) returns garbage, then sequential calls return valid JSON
        mock_client.ask.side_effect = [
            "this is not json at all",
            '{"relevant": true, "reason": "jobs"}',
            '{"relevant": false, "reason": "auth"}',
        ]
        mock_get_client.return_value = mock_client

        responses = [
            {
                "url": "https://api.example.com/a",
                "status": 200,
                "size": 5000,
                "type": "array[5]",
                "first_item_keys": ["title"],
            },
            {
                "url": "https://api.example.com/b",
                "status": 200,
                "size": 200,
                "type": "object",
                "keys": ["token"],
            },
        ]

        result = judge_api_responses(responses)
        # 1 batch call + 2 sequential fallback calls = 3 total
        assert mock_client.ask.call_count == 3
        assert len(result) == 1
        assert result[0]["url"] == "https://api.example.com/a"

    @patch("applypilot.discovery.smartextract.get_client")
    def test_missing_verdicts_falls_back(self, mock_get_client):
        """If batch returns fewer verdicts than candidates, falls back."""
        mock_client = MagicMock()
        mock_client.ask.side_effect = [
            # Batch returns only 1 verdict for 2 candidates
            json.dumps([{"index": 1, "relevant": True, "reason": "ok"}]),
            '{"relevant": true, "reason": "jobs"}',
            '{"relevant": false, "reason": "nope"}',
        ]
        mock_get_client.return_value = mock_client

        responses = [
            {
                "url": "https://api.example.com/a",
                "status": 200,
                "size": 5000,
                "type": "array[5]",
                "first_item_keys": ["title"],
            },
            {
                "url": "https://api.example.com/b",
                "status": 200,
                "size": 5000,
                "type": "array[5]",
                "first_item_keys": ["title"],
            },
        ]

        result = judge_api_responses(responses)
        # 1 failed batch + 2 sequential = 3
        assert mock_client.ask.call_count == 3
        assert len(result) == 1

    @patch("applypilot.discovery.smartextract.get_client")
    def test_non_list_response_falls_back(self, mock_get_client):
        """If batch returns a dict instead of array, falls back."""
        mock_client = MagicMock()
        mock_client.ask.side_effect = [
            '{"relevant": true, "reason": "jobs"}',  # dict, not array
            '{"relevant": true, "reason": "jobs"}',
            '{"relevant": true, "reason": "jobs"}',
        ]
        mock_get_client.return_value = mock_client

        responses = [
            {
                "url": "https://api.example.com/a",
                "status": 200,
                "size": 5000,
                "type": "array[5]",
                "first_item_keys": ["title"],
            },
            {
                "url": "https://api.example.com/b",
                "status": 200,
                "size": 5000,
                "type": "array[5]",
                "first_item_keys": ["title"],
            },
        ]

        result = judge_api_responses(responses)
        assert mock_client.ask.call_count == 3
        assert len(result) == 2

    @patch("applypilot.discovery.smartextract.get_client")
    def test_sequential_fallback_keeps_on_error(self, mock_get_client):
        """Sequential fallback keeps responses on LLM error (defensive)."""
        mock_client = MagicMock()
        mock_client.ask.side_effect = [
            "invalid",
            Exception("LLM timeout"),
            '{"relevant": false, "reason": "nope"}',
        ]
        mock_get_client.return_value = mock_client

        responses = [
            {
                "url": "https://api.example.com/a",
                "status": 200,
                "size": 5000,
                "type": "array[5]",
                "first_item_keys": ["title"],
            },
            {
                "url": "https://api.example.com/b",
                "status": 200,
                "size": 5000,
                "type": "array[5]",
                "first_item_keys": ["title"],
            },
        ]

        result = judge_api_responses(responses)
        assert mock_client.ask.call_count == 3
        # First response kept due to error, second dropped as irrelevant
        assert len(result) == 1
        assert result[0]["url"] == "https://api.example.com/a"


# ---------------------------------------------------------------------------
# Integration: heuristic + batch combined
# ---------------------------------------------------------------------------

class TestJudgeHeuristicPlusBatch:
    """Combined heuristic filter + batch judge integration."""

    @patch("applypilot.discovery.smartextract.get_client")
    def test_heuristic_skips_then_batch_judges(self, mock_get_client):
        """3 telemetry + 2 real -> heuristic skips 3, batch judges 2 in 1 call."""
        mock_client = MagicMock()
        mock_client.ask.return_value = json.dumps([
            {"index": 1, "relevant": True, "reason": "job search results"},
            {"index": 2, "relevant": False, "reason": "tracking pixel"},
        ])
        mock_get_client.return_value = mock_client

        responses = [
            {"url": "https://example.com/recaptcha/verify", "size": 100, "status": 200},
            {"url": "https://example.com/telemetry/collect", "size": 200, "status": 200},
            {"url": "https://example.com/web-vitals/ga.js", "size": 300, "status": 200},
            {
                "url": "https://api.example.com/jobs/search",
                "size": 5000,
                "status": 200,
                "type": "array[25]",
                "first_item_keys": ["title", "company", "location"],
                "first_item_sample": {"title": "Data Scientist", "company": "Acme"},
            },
            {
                "url": "https://api.example.com/analytics/pixel",
                "size": 3000,
                "status": 200,
                "type": "object",
                "keys": ["event", "timestamp"],
            },
        ]

        result = judge_api_responses(responses)

        # 1 batch call for the 2 non-skipped candidates
        assert mock_client.ask.call_count == 1
        # Only the job search response kept
        assert len(result) == 1
        assert result[0]["url"] == "https://api.example.com/jobs/search"

    @patch("applypilot.discovery.smartextract.get_client")
    def test_batch_prompt_size_within_budget(self, mock_get_client):
        """Batched prompt is <=6000 chars for 5 responses (plan criterion)."""
        mock_client = MagicMock()
        mock_client.ask.return_value = json.dumps([
            {"index": i, "relevant": True, "reason": "jobs"}
            for i in range(1, 6)
        ])
        mock_get_client.return_value = mock_client

        responses = [
            {
                "url": f"https://api.example.com/jobs?page={i}",
                "status": 200,
                "size": 5000 + i * 1000,
                "type": "array[20]",
                "first_item_keys": ["title", "company", "location", "salary"],
                "first_item_sample": {
                    "title": f"Software Engineer {i}",
                    "company": f"TechCorp {i}",
                    "location": "Remote",
                    "salary": "$100k",
                },
            }
            for i in range(5)
        ]

        judge_api_responses(responses)

        prompt_used = mock_client.ask.call_args[0][0]
        assert len(prompt_used) < 6000, f"Prompt too long: {len(prompt_used)} chars"

    @patch("applypilot.discovery.smartextract.get_client")
    def test_empty_after_heuristic_no_call(self, mock_get_client):
        """If heuristic skips all, no LLM call regardless of batch capability."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        responses = [
            {"url": "https://example.com/recaptcha/api", "size": 100, "status": 200},
            {"url": "https://example.com/telemetry/events", "size": 200, "status": 200},
        ]

        result = judge_api_responses(responses)
        assert mock_client.ask.call_count == 0
        assert result == []
