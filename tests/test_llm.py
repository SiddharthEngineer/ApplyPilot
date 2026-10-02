"""Tests for LLMClient Gemini compat fallback and provider detection."""

import json
import os
from unittest.mock import MagicMock, patch

import httpx
import pytest

from applypilot.llm import (
    LLMClient,
    LLMQuotaExhausted,
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
            assert model == "gemini-3.1-flash-lite"
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

    def test_opencode_default_model(self):
        with patch.dict(os.environ, {"OPENCODE_API_KEY": "sk-test"}, clear=True):
            base_url, model, api_key = _detect_provider()
            assert base_url == "https://opencode.ai/zen/v1"
            assert model == "opencode/nemotron-3-nano-free"
            assert api_key == "sk-test"

    def test_opencode_respects_llm_model(self):
        with patch.dict(os.environ, {"OPENCODE_API_KEY": "sk-test", "LLM_MODEL": "custom"}, clear=True):
            base_url, model, api_key = _detect_provider()
            assert base_url == "https://opencode.ai/zen/v1"
            assert model == "custom"
            assert api_key == "sk-test"

    def test_opencode_via_llm_url(self):
        with patch.dict(os.environ, {"LLM_URL": "https://opencode.ai/zen/v1"}, clear=True):
            os.environ.pop("OPENCODE_API_KEY", None)
            base_url, model, api_key = _detect_provider()
            assert base_url == "https://opencode.ai/zen/v1"
            assert model == "opencode/nemotron-3-nano-free"

    def test_local_127_not_hijacked_by_opencode(self):
        # Explicit local URL must keep priority over OPENCODE_API_KEY.
        with patch.dict(os.environ, {"LLM_URL": "http://127.0.0.1:4096/v1", "LLM_MODEL": "opencode/nemotron-3-nano-free", "OPENCODE_API_KEY": "sk-test"}, clear=True):
            os.environ.pop("GEMINI_API_KEY", None)
            base_url, _, _ = _detect_provider()
            assert base_url == "http://127.0.0.1:4096/v1"


class TestDiscoveryClient:
    def test_discovery_client_uses_flash_lite(self):
        import applypilot.llm as llm_mod
        with patch.dict(os.environ, {"GEMINI_API_KEY": "g-key"}, clear=False):
            os.environ.pop("LLM_URL", None)
            os.environ.pop("OPENAI_API_KEY", None)
            os.environ.pop("LLM_MODEL", None)
            os.environ.pop("LLM_DISCOVERY_MODEL", None)
            llm_mod.reset_clients()
            try:
                assert get_client().model == "gemini-3.6-flash"
                assert get_discovery_client().model == "gemini-3.1-flash-lite"
            finally:
                llm_mod.reset_clients()

    def test_discovery_client_independent_singleton(self):
        import applypilot.llm as llm_mod
        with patch.dict(os.environ, {"GEMINI_API_KEY": "g-key"}, clear=False):
            os.environ.pop("LLM_URL", None)
            os.environ.pop("OPENAI_API_KEY", None)
            os.environ.pop("LLM_MODEL", None)
            os.environ.pop("LLM_DISCOVERY_MODEL", None)
            llm_mod.reset_clients()
            try:
                d1 = get_discovery_client()
                d2 = get_discovery_client()
                assert d1 is d2
                # main client is a separate instance
                assert get_client() is not d1
            finally:
                llm_mod.reset_clients()



class TestPerStageRouting:
    """Success Criterion 1: LLM_{PURPOSE}_MODEL only affects that stage."""

    _ENV_DROP = ("LLM_URL", "OPENAI_API_KEY", "OPENCODE_API_KEY", "LLM_MODEL",
                 "LLM_DISCOVERY_MODEL", "LLM_SCORING_MODEL", "LLM_TAILOR_MODEL", "LLM_COVER_MODEL")

    @pytest.fixture(autouse=True)
    def _clean(self):
        import applypilot.llm as llm_mod
        llm_mod.reset_clients()
        yield
        llm_mod.reset_clients()

    @pytest.mark.parametrize("purpose", ["scoring", "tailor", "cover"])
    def test_stage_model_only_affects_that_stage(self, purpose):
        env = {"GEMINI_API_KEY": "g-key", f"LLM_{purpose.upper()}_MODEL": "gemini-3.1-flash-lite"}
        with patch.dict(os.environ, env, clear=False):
            for k in self._ENV_DROP:
                if k != f"LLM_{purpose.upper()}_MODEL":
                    os.environ.pop(k, None)
            assert get_client(purpose).model == "gemini-3.1-flash-lite"
            for other in ("scoring", "tailor", "cover", "default"):
                if other != purpose:
                    assert get_client(other).model == "gemini-3.6-flash"
            assert get_client("discovery").model == "gemini-3.1-flash-lite"

    def test_stage_model_falls_back_to_llm_model(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "g-key", "LLM_MODEL": "m-all"}, clear=False):
            for k in self._ENV_DROP:
                if k != "LLM_MODEL":
                    os.environ.pop(k, None)
            for purpose in ("scoring", "tailor", "cover", "discovery"):
                assert get_client(purpose).model == "m-all"

    def test_stage_model_applies_to_openai(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "o-key", "LLM_SCORING_MODEL": "gpt-x"}, clear=True):
            assert get_client("scoring").model == "gpt-x"
            assert get_client("tailor").model == "gpt-4o-mini"

    def test_discovery_alias(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "g-key"}, clear=True):
            assert get_discovery_client() is get_client("discovery")

    def test_same_model_shares_rpm_window(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "g-key", "LLM_MODEL": "m", "LLM_RPM_LIMIT": "2"}, clear=True):
            scoring, tailor = get_client("scoring"), get_client("tailor")
            assert scoring is not tailor
            assert scoring._request_timestamps is tailor._request_timestamps
            scoring._record_request()
            tailor._record_request()
            # Third request on the same model (from either client) must wait.
            with patch("applypilot.llm.time.sleep") as mock_sleep:
                get_client("cover")._throttle_if_needed()
            assert mock_sleep.call_count == 1

    def test_different_models_have_separate_rpm_windows(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "g-key", "LLM_SCORING_MODEL": "lite"}, clear=True):
            assert get_client("scoring")._request_timestamps is not get_client("tailor")._request_timestamps


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
# Daily-quota 429 → LLMQuotaExhausted (no retries)
# ---------------------------------------------------------------------------

def _gemini_429(quota_id: str, retry_delay: str | None = None, wrap_list: bool = False):
    details: list[dict] = [{
        "@type": "type.googleapis.com/google.rpc.QuotaFailure",
        "violations": [{
            "quotaMetric": "generativelanguage.googleapis.com/generate_content_free_tier_requests",
            "quotaId": quota_id,
        }],
    }]
    if retry_delay:
        details.append({"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": retry_delay})
    body: dict | list = {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "quota", "details": details}}
    if wrap_list:
        body = [body]
    resp = _make_response(429, text=json.dumps(body))
    resp.json.return_value = body
    return resp


_DAILY = "GenerateRequestsPerDayPerProjectPerModel-FreeTier"
_MINUTE = "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"


class TestDailyQuota:
    def _client(self):
        return LLMClient(base_url="https://generativelanguage.googleapis.com/v1beta/openai",
                         model="gemini-3.1-flash-lite", api_key="k")

    @pytest.mark.parametrize("wrap_list", [False, True])
    def test_daily_429_raises_after_one_request(self, wrap_list, caplog):
        client = self._client()
        with patch.object(client._client, "post", return_value=_gemini_429(_DAILY, wrap_list=wrap_list)) as post, \
                patch("applypilot.llm.time.sleep") as sleep, caplog.at_level("ERROR", logger="applypilot.llm"), \
                pytest.raises(LLMQuotaExhausted) as ei:
            client.chat([{"role": "user", "content": "hi"}])
        assert post.call_count == 1
        sleep.assert_not_called()
        assert ei.value.model == "gemini-3.1-flash-lite"
        assert ei.value.scope == _DAILY
        assert "Gemini daily quota exhausted for gemini-3.1-flash-lite" in caplog.text

    def test_daily_429_on_native_path(self):
        client = self._client()
        client._use_native_gemini = True
        with patch.object(client._client, "post", return_value=_gemini_429(_DAILY)) as post, \
                pytest.raises(LLMQuotaExhausted):
            client.chat([{"role": "user", "content": "hi"}])
        assert post.call_count == 1

    def test_daily_429_after_compat_fallback(self):
        client = self._client()
        with patch.object(client._client, "post", side_effect=[_make_response(404), _gemini_429(_DAILY)]), \
                pytest.raises(LLMQuotaExhausted):
            client.chat([{"role": "user", "content": "hi"}])

    def test_per_minute_429_retries_with_backoff(self):
        client = self._client()
        ok = _make_response(200, json_data={"choices": [{"message": {"content": "ok"}}]})
        with patch.object(client._client, "post", side_effect=[_gemini_429(_MINUTE), _gemini_429(_MINUTE), ok]) as post, \
                patch("applypilot.llm.time.sleep") as sleep:
            assert client.chat([{"role": "user", "content": "hi"}]) == "ok"
        assert post.call_count == 3
        assert [c.args[0] for c in sleep.call_args_list] == [10, 20]

    def test_per_minute_429_honors_retry_delay(self):
        client = self._client()
        ok = _make_response(200, json_data={"choices": [{"message": {"content": "ok"}}]})
        with patch.object(client._client, "post", side_effect=[_gemini_429(_MINUTE, retry_delay="7s"), ok]), \
                patch("applypilot.llm.time.sleep") as sleep:
            assert client.chat([{"role": "user", "content": "hi"}]) == "ok"
        sleep.assert_called_once_with(8.0)


# ---------------------------------------------------------------------------
# Structured JSON output (response_schema)
# ---------------------------------------------------------------------------

_SCHEMA = {"type": "OBJECT", "properties": {"score": {"type": "INTEGER"}}, "required": ["score"]}


class TestResponseSchema:
    def test_gemini_schema_goes_to_native_url(self):
        client = LLMClient(base_url="https://generativelanguage.googleapis.com/v1beta/openai",
                           model="gemini-3.1-flash-lite", api_key="k")
        with patch.object(client._client, "post", return_value=_make_native_response('{"score": 7}')) as post:
            assert client.chat([{"role": "system", "content": "s"}, {"role": "user", "content": "hi"}],
                               response_schema=_SCHEMA) == '{"score": 7}'
        assert post.call_count == 1
        url = post.call_args.args[0]
        assert url == "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-lite:generateContent"
        cfg = post.call_args.kwargs["json"]["generationConfig"]
        assert cfg["responseMimeType"] == "application/json"
        assert cfg["responseSchema"] == _SCHEMA
        # The key travels in a header, never the (logged) URL.
        assert post.call_args.kwargs["headers"]["x-goog-api-key"] == "k"
        assert "params" not in post.call_args.kwargs
        # Compat stays the default for calls without a schema.
        assert client._use_native_gemini is False

    def test_gemini_without_schema_uses_compat(self):
        client = LLMClient(base_url="https://generativelanguage.googleapis.com/v1beta/openai",
                           model="gemini-3.1-flash-lite", api_key="k")
        ok = _make_response(200, json_data={"choices": [{"message": {"content": "ok"}}]})
        with patch.object(client._client, "post", return_value=ok) as post:
            client.chat([{"role": "user", "content": "hi"}])
        assert post.call_args.args[0].endswith("/chat/completions")

    def test_other_providers_ignore_schema(self):
        client = LLMClient(base_url="https://api.openai.com/v1", model="gpt-4o-mini", api_key="k")
        ok = _make_response(200, json_data={"choices": [{"message": {"content": "ok"}}]})
        with patch.object(client._client, "post", return_value=ok) as post:
            client.chat([{"role": "user", "content": "hi"}], response_schema=_SCHEMA)
        assert post.call_args.args[0] == "https://api.openai.com/v1/chat/completions"
        assert "responseSchema" not in json.dumps(post.call_args.kwargs["json"])


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
            llm_mod.reset_clients()
            try:
                client = get_client()
                assert client._rpm_limit == 20
                assert client._rpm_window == 30.0
            finally:
                llm_mod.reset_clients()


# ---------------------------------------------------------------------------
# Per-model limits: LLM_RPM_LIMITS / LLM_RPD_LIMITS
# ---------------------------------------------------------------------------

class TestPerModelLimits:
    _LIMIT_VARS = ("LLM_RPM_LIMITS", "LLM_RPD_LIMITS", "LLM_RPM_LIMIT", "LLM_RPD_LIMIT")

    @pytest.fixture(autouse=True)
    def _isolate(self, tmp_path, monkeypatch):
        import applypilot.llm as llm_mod
        for k in self._LIMIT_VARS:
            monkeypatch.delenv(k, raising=False)
        monkeypatch.setattr(llm_mod, "daily_usage", llm_mod.DailyUsage(tmp_path / "usage.json"))
        monkeypatch.setattr(llm_mod, "_warned_limit_vars", set())
        llm_mod.reset_clients()
        yield llm_mod
        llm_mod.reset_clients()

    def test_multiline_json_with_trailing_commas(self, monkeypatch):
        from applypilot.llm import model_limits
        monkeypatch.setenv("LLM_RPM_LIMITS", '{\n  "gemini-3.1-flash-lite": 15,\n  "gemini-3.6-flash": 5,\n}')
        monkeypatch.setenv("LLM_RPD_LIMITS", '{"gemini-3.1-flash-lite": 500, "gemini-3.6-flash": 20,}')
        assert model_limits("gemini-3.1-flash-lite") == (15, 500)
        assert model_limits("gemini-3.6-flash") == (5, 20)
        assert model_limits("other-model") == (0, 0)

    def test_global_limit_is_fallback(self, monkeypatch):
        from applypilot.llm import model_limits
        monkeypatch.setenv("LLM_RPM_LIMITS", '{"a": 15}')
        monkeypatch.setenv("LLM_RPM_LIMIT", "10")
        assert model_limits("a") == (15, 0)
        assert model_limits("b") == (10, 0)

    def test_unquoted_dotenv_value_warns_once(self, monkeypatch, caplog):
        from applypilot.llm import model_limits
        monkeypatch.setenv("LLM_RPM_LIMITS", "{")  # what python-dotenv reads from an unquoted multi-line value
        with caplog.at_level("WARNING", logger="applypilot.llm"):
            assert model_limits("a") == (0, 0)
            model_limits("a")
        assert caplog.text.count("Ignoring LLM_RPM_LIMITS") == 1
        assert "single quotes" in caplog.text

    def test_get_client_uses_its_models_limits(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "g")
        for k in ("LLM_URL", "OPENAI_API_KEY", "OPENCODE_API_KEY", "LLM_MODEL", "LLM_DISCOVERY_MODEL",
                  "LLM_SCORING_MODEL", "LLM_TAILOR_MODEL", "LLM_COVER_MODEL"):
            monkeypatch.delenv(k, raising=False)
        monkeypatch.setenv("LLM_RPM_LIMITS", '{"gemini-3.1-flash-lite": 15, "gemini-3.6-flash": 5}')
        monkeypatch.setenv("LLM_RPD_LIMITS", '{"gemini-3.1-flash-lite": 500, "gemini-3.6-flash": 20}')
        d, t = get_client("discovery"), get_client("tailor")
        assert (d.model, d._rpm_limit, d._rpd_limit) == ("gemini-3.1-flash-lite", 15, 500)
        assert (t.model, t._rpm_limit, t._rpd_limit) == ("gemini-3.6-flash", 5, 20)

    def test_daily_limit_stops_before_sending(self, _isolate, caplog):
        ok = _make_response(200, json_data={"choices": [{"message": {"content": "ok"}}]})
        client = LLMClient("https://api.openai.com/v1", "m", "k", rpd_limit=2)
        with patch.object(client._client, "post", return_value=ok) as post:
            client.chat([{"role": "user", "content": "1"}])
            client.chat([{"role": "user", "content": "2"}])
            with caplog.at_level("ERROR", logger="applypilot.llm"), pytest.raises(LLMQuotaExhausted) as ei:
                client.chat([{"role": "user", "content": "3"}])
        assert post.call_count == 2
        assert "2/2 requests today" in ei.value.scope
        assert "Gemini daily quota exhausted for m" in caplog.text

    def test_daily_count_is_shared_across_clients_and_runs(self, _isolate, tmp_path):
        llm_mod = _isolate
        ok = _make_response(200, json_data={"choices": [{"message": {"content": "ok"}}]})
        first = LLMClient("https://api.openai.com/v1", "m", "k", rpd_limit=1)
        with patch.object(first._client, "post", return_value=ok):
            first.chat([{"role": "user", "content": "1"}])
        # A later run: new client, new DailyUsage object reading the same file.
        llm_mod.daily_usage = llm_mod.DailyUsage(tmp_path / "usage.json")
        second = LLMClient("https://api.openai.com/v1", "m", "k", rpd_limit=1)
        with pytest.raises(LLMQuotaExhausted):
            second.chat([{"role": "user", "content": "2"}])
        assert llm_mod.daily_usage.count("other") == 0

    def test_daily_count_resets_on_a_new_pacific_day(self, _isolate):
        usage = _isolate.daily_usage
        with patch.object(_isolate.DailyUsage, "today", return_value="2026-10-01"):
            usage.increment("m")
            assert usage.count("m") == 1
        with patch.object(_isolate.DailyUsage, "today", return_value="2026-10-02"):
            assert usage.count("m") == 0

    def test_no_rpd_limit_writes_no_file(self, _isolate, tmp_path):
        ok = _make_response(200, json_data={"choices": [{"message": {"content": "ok"}}]})
        client = LLMClient("https://api.openai.com/v1", "m", "k")
        with patch.object(client._client, "post", return_value=ok):
            client.chat([{"role": "user", "content": "1"}])
        assert not (tmp_path / "usage.json").exists()


# ---------------------------------------------------------------------------
# 503 → fallback model (no backoff retries)
# ---------------------------------------------------------------------------

class TestOverloadFallback:
    _BASE = "https://generativelanguage.googleapis.com/v1beta/openai"

    def _pair(self, cooldown: float = 300.0):
        fb = LLMClient(base_url=self._BASE, model="gemini-3.1-flash-lite", api_key="k")
        primary = LLMClient(base_url=self._BASE, model="gemini-3.6-flash", api_key="k",
                            fallback=fb, fallback_cooldown=cooldown)
        return primary, fb

    @staticmethod
    def _ok(text):
        return _make_response(200, json_data={"choices": [{"message": {"content": text}}]})

    def test_503_goes_straight_to_fallback(self):
        primary, fb = self._pair()
        with patch.object(primary._client, "post", return_value=_make_response(503)) as p_post, \
                patch.object(fb._client, "post", return_value=self._ok("from lite")) as f_post, \
                patch("applypilot.llm.time.sleep") as sleep:
            assert primary.chat([{"role": "user", "content": "hi"}]) == "from lite"
        assert p_post.call_count == 1 and f_post.call_count == 1
        sleep.assert_not_called()

    def test_cooldown_skips_primary(self):
        primary, fb = self._pair()
        with patch.object(primary._client, "post", return_value=_make_response(503)) as p_post, \
                patch.object(fb._client, "post", return_value=self._ok("lite")) as f_post:
            primary.chat([{"role": "user", "content": "a"}])
            primary.chat([{"role": "user", "content": "b"}])
        assert p_post.call_count == 1 and f_post.call_count == 2

    def test_primary_used_again_after_cooldown(self):
        primary, fb = self._pair(cooldown=0)
        with patch.object(primary._client, "post", side_effect=[_make_response(503), self._ok("primary")]) as p_post, \
                patch.object(fb._client, "post", return_value=self._ok("lite")):
            assert primary.chat([{"role": "user", "content": "a"}]) == "lite"
            assert primary.chat([{"role": "user", "content": "b"}]) == "primary"
        assert p_post.call_count == 2

    def test_503_counts_toward_daily_usage(self, tmp_path):
        from applypilot.llm import DailyUsage
        usage = DailyUsage(tmp_path / "usage.json")
        primary, fb = self._pair()
        primary._rpd_limit = 20
        with patch("applypilot.llm.daily_usage", usage), \
                patch.object(primary._client, "post", return_value=_make_response(503)), \
                patch.object(fb._client, "post", return_value=self._ok("lite")):
            primary.chat([{"role": "user", "content": "a"}])
        assert usage.count("gemini-3.6-flash") == 1

    def test_no_fallback_keeps_backoff_retries(self):
        client = LLMClient(base_url=self._BASE, model="gemini-3.6-flash", api_key="k")
        with patch.object(client._client, "post", side_effect=[_make_response(503), self._ok("ok")]) as post, \
                patch("applypilot.llm.time.sleep") as sleep:
            assert client.chat([{"role": "user", "content": "hi"}]) == "ok"
        assert post.call_count == 2 and sleep.call_count == 1

    def test_get_client_wires_fallback(self, monkeypatch):
        from applypilot.llm import reset_clients
        monkeypatch.setenv("GEMINI_API_KEY", "k")
        monkeypatch.setenv("LLM_TAILOR_MODEL", "gemini-3.6-flash")
        monkeypatch.delenv("LLM_FALLBACK_MODEL", raising=False)
        reset_clients()
        try:
            assert get_client("tailor").fallback.model == "gemini-3.1-flash-lite"
            monkeypatch.setenv("LLM_FALLBACK_MODEL", "none")
            reset_clients()
            assert get_client("tailor").fallback is None
            monkeypatch.setenv("LLM_FALLBACK_MODEL", "gemini-3.6-flash")  # same model -> no fallback
            reset_clients()
            assert get_client("tailor").fallback is None
        finally:
            reset_clients()

    def test_lite_model_has_no_fallback_to_itself(self, monkeypatch):
        from applypilot.llm import reset_clients
        monkeypatch.setenv("GEMINI_API_KEY", "k")
        monkeypatch.setenv("LLM_SCORING_MODEL", "gemini-3.1-flash-lite")
        monkeypatch.delenv("LLM_FALLBACK_MODEL", raising=False)
        reset_clients()
        try:
            assert get_client("scoring").fallback is None
        finally:
            reset_clients()
