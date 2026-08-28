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
    get_client,
    get_discovery_client,
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

    def test_discovery_default_is_flash_lite(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "g-key"}, clear=False):
            os.environ.pop("LLM_URL", None)
            os.environ.pop("OPENAI_API_KEY", None)
            os.environ.pop("LLM_MODEL", None)
            os.environ.pop("LLM_DISCOVERY_MODEL", None)
            _, model, _ = _detect_provider("discovery")
            assert model == "gemini-2.0-flash-lite"
            # Non-discovery purpose keeps the full-quality default
            _, model, _ = _detect_provider()
            assert model == "gemini-3.6-flash"

    def test_discovery_model_override_beats_llm_model(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "g-key", "LLM_MODEL": "explicit-model", "LLM_DISCOVERY_MODEL": "custom"}, clear=False):
            os.environ.pop("LLM_URL", None)
            os.environ.pop("OPENAI_API_KEY", None)
            _, model, _ = _detect_provider("discovery")
            assert model == "custom"

    def test_discovery_inherits_llm_model_when_set(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "g-key", "LLM_MODEL": "explicit-model"}, clear=False):
            os.environ.pop("LLM_URL", None)
            os.environ.pop("OPENAI_API_KEY", None)
            os.environ.pop("LLM_DISCOVERY_MODEL", None)
            _, model, _ = _detect_provider("discovery")
            assert model == "explicit-model"


class TestDiscoveryClient:
    def test_discovery_client_uses_flash_lite(self):
        import applypilot.llm as llm_mod
        with patch.dict(os.environ, {"GEMINI_API_KEY": "g-key"}, clear=False):
            os.environ.pop("LLM_URL", None)
            os.environ.pop("OPENAI_API_KEY", None)
            os.environ.pop("LLM_MODEL", None)
            os.environ.pop("LLM_DISCOVERY_MODEL", None)
            llm_mod._instance = None
            llm_mod._discovery_instance = None
            try:
                assert get_client().model == "gemini-3.6-flash"
                assert get_discovery_client().model == "gemini-2.0-flash-lite"
            finally:
                llm_mod._instance = None
                llm_mod._discovery_instance = None

    def test_discovery_client_independent_singleton(self):
        import applypilot.llm as llm_mod
        with patch.dict(os.environ, {"GEMINI_API_KEY": "g-key"}, clear=False):
            os.environ.pop("LLM_URL", None)
            os.environ.pop("OPENAI_API_KEY", None)
            os.environ.pop("LLM_MODEL", None)
            os.environ.pop("LLM_DISCOVERY_MODEL", None)
            llm_mod._instance = None
            llm_mod._discovery_instance = None
            try:
                d1 = get_discovery_client()
                d2 = get_discovery_client()
                assert d1 is d2
                # main client is a separate instance
                assert get_client() is not d1
            finally:
                llm_mod._instance = None
                llm_mod._discovery_instance = None



# ---------------------------------------------------------------------------
# Gemini compat → native fallback
# ---------------------------------------------------------------------------

class TestGeminiCompatFallback:
    def _make_gemini_client(self):
        return LLMClient(
            base_url="https://generativelanguage.googleapis.com/v1beta/openai",
            model="gemini-3.6-flash",
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


# ---------------------------------------------------------------------------
# RPM limiter
# ---------------------------------------------------------------------------

class TestRPMLimiter:
    def test_throttle_sleeps_when_limit_reached(self):
        client = LLMClient(
            base_url="https://api.openai.com/v1",
            model="gpt-4o-mini",
            api_key="test-key",
            rpm_limit=2,
            rpm_window=60.0,
        )
        success_resp = _make_response(200, json_data={"choices": [{"message": {"content": "ok"}}]})

        with (
            patch.object(client._client, "post", return_value=success_resp),
            patch("applypilot.llm.time.sleep") as mock_sleep,
            patch("applypilot.llm.time.monotonic", side_effect=[
                0.0, 0.1,    # call 1: throttle check, record
                0.2, 0.3,    # call 2: throttle check, record
                31.0,        # call 3: throttle check (now)
                31.1,        # call 3: after sleep re-check
                31.2,        # call 3: record
            ]),
        ):
            # Call 1: no sleep (0 < 2)
            client.chat([{"role": "user", "content": "a"}])
            assert mock_sleep.call_count == 0
            # Call 2: no sleep (1 < 2)
            client.chat([{"role": "user", "content": "b"}])
            assert mock_sleep.call_count == 0
            # Call 3: should sleep (2 >= 2)
            client.chat([{"role": "user", "content": "c"}])
            assert mock_sleep.call_count == 1
            sleep_arg = mock_sleep.call_args[0][0]
            assert sleep_arg > 29.0  # ~30s sleep

    def test_rpm_limit_zero_disables_throttling(self):
        client = LLMClient(
            base_url="https://api.openai.com/v1",
            model="gpt-4o-mini",
            api_key="test-key",
            rpm_limit=0,
        )
        success_resp = _make_response(200, json_data={"choices": [{"message": {"content": "ok"}}]})

        with (
            patch.object(client._client, "post", return_value=success_resp),
            patch("applypilot.llm.time.sleep") as mock_sleep,
        ):
            for _ in range(10):
                client.chat([{"role": "user", "content": "hi"}])
            mock_sleep.assert_not_called()

    def test_timestamps_expire_after_window(self):
        client = LLMClient(
            base_url="https://api.openai.com/v1",
            model="gpt-4o-mini",
            api_key="test-key",
            rpm_limit=1,
            rpm_window=60.0,
        )
        success_resp = _make_response(200, json_data={"choices": [{"message": {"content": "ok"}}]})

        with (
            patch.object(client._client, "post", return_value=success_resp),
            patch("applypilot.llm.time.sleep") as mock_sleep,
        ):
            # t=0: call 1 (no sleep)
            with patch("applypilot.llm.time.monotonic", side_effect=[0.0, 0.1]):
                client.chat([{"role": "user", "content": "a"}])
            assert mock_sleep.call_count == 0
            # t=61: window expired, call 2 (no sleep)
            with patch("applypilot.llm.time.monotonic", side_effect=[61.0, 61.1]):
                client.chat([{"role": "user", "content": "b"}])
            assert mock_sleep.call_count == 0

    def test_get_client_reads_env_vars(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "g-key", "LLM_RPM_LIMIT": "20", "LLM_RPM_WINDOW": "30"}, clear=False):
            os.environ.pop("LLM_URL", None)
            os.environ.pop("LLM_MODEL", None)
            os.environ.pop("OPENAI_API_KEY", None)
            import applypilot.llm as llm_mod
            llm_mod._instance = None
            try:
                client = get_client()
                assert client._rpm_limit == 20
                assert client._rpm_window == 30.0
            finally:
                llm_mod._instance = None
