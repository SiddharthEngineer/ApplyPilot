"""The Claude client in strelitzia.llm (claude-llm-provider Task 1), against a mocked SDK."""

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import anthropic
import httpx2
import pytest

from strelitzia import llm
from strelitzia.database import init_db
from strelitzia.llm import (
    LLMClient,
    LLMResponseError,
    LLMStopRun,
    cost_usd,
    get_client,
    resolve_model,
)

PURPOSE_VARS = [f"LLM_{p.upper()}_{kind}" for p in llm.PURPOSES for kind in ("MODEL", "EFFORT")]


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    for var in [*PURPOSE_VARS, "LLM_MONTHLY_BUDGET_USD"]:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    conn = init_db(tmp_path / "t.db")
    monkeypatch.setattr(llm, "_ledger_conn", lambda: conn)
    llm.reset_clients()
    llm._warned.clear()
    yield
    llm.reset_clients()


def _message(text="ok", stop_reason="end_turn", usage=None, **extra):
    usage = usage or SimpleNamespace(input_tokens=10, output_tokens=5, cache_read_input_tokens=0,
                                     cache_creation_input_tokens=0)
    return SimpleNamespace(content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)],
                           stop_reason=stop_reason, usage=usage, **extra)


def _client(purpose="scoring", message=None):
    client = get_client(purpose)
    client._sdk = MagicMock()
    client._sdk.messages.create.return_value = message or _message()
    return client


def _status_error(cls, status):
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls("boom", response=httpx2.Response(status, request=request), body=None)


class TestRouting:
    def test_defaults_per_purpose(self):
        assert resolve_model("extract") == ("claude-haiku-5-5", "low")
        assert resolve_model("scoring") == ("claude-haiku-5-5", "low")
        assert resolve_model("discovery") == ("claude-haiku-5-5", "low")
        assert resolve_model("tailor") == ("claude-sonnet-5-5", "medium")
        assert resolve_model("cover") == ("claude-sonnet-5-5", "medium")

    def test_env_overrides(self, monkeypatch):
        monkeypatch.setenv("LLM_SCORING_MODEL", "claude-sonnet-5-5")
        monkeypatch.setenv("LLM_SCORING_EFFORT", "HIGH")
        assert resolve_model("scoring") == ("claude-sonnet-5-5", "high")

    def test_stale_gemini_override_is_ignored_once(self, monkeypatch, caplog):
        monkeypatch.setenv("LLM_SCORING_MODEL", "gemini-3.1-flash-lite")
        monkeypatch.setenv("GEMINI_API_KEY", "g")
        with caplog.at_level(logging.WARNING, logger="strelitzia.llm"):
            assert resolve_model("scoring")[0] == "claude-haiku-5-5"
            assert resolve_model("scoring")[0] == "claude-haiku-5-5"
        assert caplog.text.count("Ignoring LLM_SCORING_MODEL") == 1

    def test_bad_effort_is_ignored(self, monkeypatch):
        monkeypatch.setenv("LLM_TAILOR_EFFORT", "extreme")
        assert resolve_model("tailor") == ("claude-sonnet-5-5", "medium")

    def test_unknown_purpose(self):
        with pytest.raises(ValueError):
            resolve_model("nope")

    def test_clients_are_memoized_per_purpose(self):
        assert get_client("tailor") is get_client("tailor")
        assert get_client("tailor") is not get_client("cover")
        assert get_client().purpose == "discovery"
        assert llm.get_discovery_client() is get_client("discovery")


class TestChat:
    def test_system_block_is_cached_and_no_temperature(self):
        client = _client()
        assert client.chat([{"role": "system", "content": "rubric"}, {"role": "user", "content": "job"}],
                           max_tokens=300) == "ok"
        params = client._sdk.messages.create.call_args.kwargs
        assert params["system"] == [{"type": "text", "text": "rubric", "cache_control": {"type": "ephemeral"}}]
        assert params["messages"] == [{"role": "user", "content": "job"}]
        assert params["model"] == "claude-haiku-5-5"
        assert params["max_tokens"] == 300
        assert params["output_config"] == {"effort": "low"}
        assert "temperature" not in params

    def test_no_system_block_without_a_system_message(self):
        client = _client()
        client.ask("hi")
        assert "system" not in client._sdk.messages.create.call_args.kwargs

    def test_schema_and_effort_forwarded(self):
        schema = {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"],
                  "additionalProperties": False}
        client = _client(message=_message('{"a": "x"}'))
        assert client.chat([{"role": "user", "content": "q"}], schema=schema, effort="medium") == '{"a": "x"}'
        assert client._sdk.messages.create.call_args.kwargs["output_config"] == {
            "effort": "medium", "format": {"type": "json_schema", "schema": schema}}

    def test_request_params_match_chat(self):
        client = _client()
        messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
        client.chat(messages, max_tokens=99)
        assert client._sdk.messages.create.call_args.kwargs == client.request_params(messages, max_tokens=99)

    def test_refusal_is_an_error(self):
        client = _client(message=_message("", stop_reason="refusal",
                                          stop_details=SimpleNamespace(category="cyber")))
        with pytest.raises(LLMResponseError, match="refusal: cyber"):
            client.ask("q")

    def test_max_tokens_is_an_error(self):
        client = _client(message=_message("{\"a\": ", stop_reason="max_tokens"))
        with pytest.raises(LLMResponseError, match="max_tokens"):
            client.ask("q")

    def test_missing_key_stops_the_run(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY")
        with pytest.raises(LLMStopRun) as e:
            get_client("scoring").ask("q")
        assert e.value.reason == "auth"

    def test_sdk_gets_the_env_key(self):
        sdk = get_client("scoring").sdk
        assert sdk.api_key == "sk-ant-test"
        assert sdk.max_retries == llm._MAX_RETRIES

    @pytest.mark.parametrize(("cls", "status", "reason"), [
        (anthropic.AuthenticationError, 401, "auth"),
        (anthropic.PermissionDeniedError, 403, "auth"),
        (anthropic.RateLimitError, 429, "overloaded"),
    ])
    def test_api_errors_stop_the_run(self, cls, status, reason):
        client = _client()
        client._sdk.messages.create.side_effect = _status_error(cls, status)
        with pytest.raises(LLMStopRun) as e:
            client.ask("q")
        assert e.value.reason == reason

    def test_bad_request_is_a_plain_error(self):
        client = _client()
        client._sdk.messages.create.side_effect = _status_error(anthropic.BadRequestError, 400)
        with pytest.raises(anthropic.BadRequestError):
            client.ask("q")


class TestCost:
    USAGE = SimpleNamespace(input_tokens=1_000_000, output_tokens=1_000_000, cache_read_input_tokens=1_000_000,
                            cache_creation_input_tokens=1_000_000)

    def test_haiku_sync_and_batch(self):
        assert cost_usd("claude-haiku-5-5", self.USAGE) == pytest.approx(0.10 + 0.50 + 0.01 + 0.125)
        assert cost_usd("claude-haiku-5-5", self.USAGE, batch=True) == pytest.approx((0.10 + 0.50 + 0.01 + 0.125) / 2)

    def test_sonnet_from_a_dict(self):
        usage = {"input_tokens": 2000, "output_tokens": 500, "cache_read_input_tokens": None}
        assert cost_usd("claude-sonnet-5-5", usage) == pytest.approx((2000 * 2.00 + 500 * 10.00) / 1e6)

    def test_dated_model_id_uses_its_base_price(self):
        assert cost_usd("claude-haiku-5-5-20261001", self.USAGE) == cost_usd("claude-haiku-5-5", self.USAGE)

    def test_unknown_model_uses_the_dearest_price(self, caplog):
        with caplog.at_level(logging.WARNING, logger="strelitzia.llm"):
            assert cost_usd("claude-future-9", self.USAGE) == cost_usd("claude-opus-5-5", self.USAGE)
        assert "claude-future-9" in caplog.text


def test_client_is_constructed_lazily(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    client = LLMClient("scoring", "claude-haiku-5-5", "low")  # no key needed until the first request
    assert client.request_params([{"role": "user", "content": "q"}])["model"] == "claude-haiku-5-5"
