"""Tests for LLMClient Gemini compat fallback and provider detection."""

import json
import os
from unittest.mock import MagicMock, patch

import httpx
import pytest

from applypilot.llm import (
    LLMClient,
    _detect_provider,
    _GeminiCompatForbidden,
)


def _make_response(status_code: int, text: str = "error body", json_data: dict | None = None):
    """Create a mock httpx.Response."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.text = text
    resp.json.return_value = json_data or {}
    resp.headers = {}
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            message=f"HTTP {status_code}",
            request=MagicMock(),
            response=resp,
        )
    return resp


def _make_native_response(text: str = "native response"):
    """Create a mock successful native Gemini response."""
    data = {"candidates": [{"content": {"parts": [{"text": text}]}}]}
    return _make_response(200, text=json.dumps(data), json_data=data)


# ---------------------------------------------------------------------------
# _detect_provider
# ---------------------------------------------------------------------------

class TestDetectProvider:
    def test_gemini_priority(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "g-key", "OPENAI_API_KEY": "o-key"}, clear=False):
            os.environ.pop("LLM_URL", None)
            os.environ.pop("LLM_MODEL", None)
            base_url, model, api_key = _detect_provider()
            assert "googleapis.com" in base_url
            assert api_key == "g-key"

    def test_openai_fallback(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "o-key"}, clear=False):
            os.environ.pop("GEMINI_API_KEY", None)
            os.environ.pop("LLM_URL", None)
            os.environ.pop("LLM_MODEL", None)
            base_url, model, api_key = _detect_provider()
            assert "openai.com" in base_url
            assert api_key == "o-key"

    def test_local_url_priority(self):
        with patch.dict(os.environ, {"LLM_URL": "http://localhost:8080/v1", "GEMINI_API_KEY": "g-key"}, clear=False):
            os.environ.pop("LLM_MODEL", None)
            base_url, model, api_key = _detect_provider()
            assert base_url == "http://localhost:8080/v1"

    def test_no_provider_raises(self):
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(RuntimeError, match="No LLM provider"):
                _detect_provider()

    def test_model_override(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "g-key", "LLM_MODEL": "my-model"}, clear=False):
            os.environ.pop("LLM_URL", None)
            os.environ.pop("OPENAI_API_KEY", None)
            _, model, _ = _detect_provider()
            assert model == "my-model"


# ---------------------------------------------------------------------------
# Gemini compat → native fallback
# ---------------------------------------------------------------------------

class TestGeminiCompatFallback:
    def _make_gemini_client(self):
        return LLMClient(
            base_url="https://generativelanguage.googleapis.com/v1beta/openai",
            model="gemini-2.5-flash",
            api_key="test-key",
        )

    def test_compat_404_falls_back_to_native(self):
        client = self._make_gemini_client()
        compat_resp = _make_response(404, text="Not Found")
        native_resp = _make_native_response("hello from native")

        with patch.object(client._client, "post", side_effect=[compat_resp, native_resp]):
            result = client.chat([{"role": "user", "content": "hi"}])
            assert result == "hello from native"
            assert client._use_native_gemini is True

    def test_compat_400_falls_back_to_native(self):
        client = self._make_gemini_client()
        compat_resp = _make_response(400, text="Bad Request - model not found")
        native_resp = _make_native_response("hello from native")

        with patch.object(client._client, "post", side_effect=[compat_resp, native_resp]):
            result = client.chat([{"role": "user", "content": "hi"}])
            assert result == "hello from native"
            assert client._use_native_gemini is True

    def test_compat_403_falls_back_to_native(self):
        client = self._make_gemini_client()
        compat_resp = _make_response(403, text="Forbidden")
        native_resp = _make_native_response("hello from native")

        with patch.object(client._client, "post", side_effect=[compat_resp, native_resp]):
            result = client.chat([{"role": "user", "content": "hi"}])
            assert result == "hello from native"
            assert client._use_native_gemini is True

    def test_compat_404_then_native_404_raises_runtime_error(self):
        client = self._make_gemini_client()
        compat_resp = _make_response(404, text="Not Found")
        native_resp = _make_response(404, text="Native also 404")

        with patch.object(client._client, "post", side_effect=[compat_resp, native_resp]):
            with pytest.raises(RuntimeError, match="Both Gemini endpoints failed"):
                client.chat([{"role": "user", "content": "hi"}])

    def test_persistence_of_native_flag(self):
        client = self._make_gemini_client()
        compat_resp = _make_response(404, text="Not Found")
        native_resp = _make_native_response("first")
        second_native_resp = _make_native_response("second")

        # First call: compat 404 → native
        with patch.object(client._client, "post", side_effect=[compat_resp, native_resp]):
            client.chat([{"role": "user", "content": "hi"}])
            assert client._use_native_gemini is True

        # Second call: should go directly to native without hitting compat
        with patch.object(client._client, "post", return_value=second_native_resp):
            result = client.chat([{"role": "user", "content": "hi again"}])
            assert result == "second"
            # Only one post call (to native), not two
            client._client.post.assert_called_once()

    def test_429_retry_on_compat(self):
        client = self._make_gemini_client()
        rate_limit_resp = _make_response(429, text="Rate limited")
        success_resp = _make_response(200, json_data={"choices": [{"message": {"content": "ok"}}]})

        with patch.object(client._client, "post", side_effect=[rate_limit_resp, success_resp]):
            with patch("applypilot.llm.time.sleep"):
                result = client.chat([{"role": "user", "content": "hi"}])
                assert result == "ok"


# ---------------------------------------------------------------------------
# OpenAI 404 does NOT fallback
# ---------------------------------------------------------------------------

class TestOpenAI404NoFallback:
    def test_openai_404_raises_http_status_error(self):
        client = LLMClient(
            base_url="https://api.openai.com/v1",
            model="gpt-4o-mini",
            api_key="test-key",
        )
        resp_404 = _make_response(404, text="Not Found")

        with patch.object(client._client, "post", return_value=resp_404):
            with pytest.raises(httpx.HTTPStatusError):
                client.chat([{"role": "user", "content": "hi"}])
            assert client._use_native_gemini is False

    def test_openai_400_raises_http_status_error(self):
        client = LLMClient(
            base_url="https://api.openai.com/v1",
            model="gpt-4o-mini",
            api_key="test-key",
        )
        resp_400 = _make_response(400, text="Bad Request")

        with patch.object(client._client, "post", return_value=resp_400):
            with pytest.raises(httpx.HTTPStatusError):
                client.chat([{"role": "user", "content": "hi"}])
            assert client._use_native_gemini is False


# ---------------------------------------------------------------------------
# _GeminiCompatForbidden sentinel
# ---------------------------------------------------------------------------

class TestGeminiCompatForbidden:
    def test_stores_response(self):
        resp = _make_response(404, text="not found")
        exc = _GeminiCompatForbidden(resp)
        assert exc.response is resp
        assert "404" in str(exc)
        assert "not found" in str(exc)

    def test_403_message(self):
        resp = _make_response(403, text="forbidden")
        exc = _GeminiCompatForbidden(resp)
        assert "403" in str(exc)
