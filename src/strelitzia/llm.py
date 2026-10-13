"""
LLM client for Strelitzia: every call goes to the Claude API through the `anthropic` SDK.

Each pipeline stage (`PURPOSES`) has its own model and effort, from the packaged config/llm_models.yaml;
`LLM_{PURPOSE}_MODEL` and `LLM_{PURPOSE}_EFFORT` override them. The key is `ANTHROPIC_API_KEY`, read from
~/.strelitzia/.env by `config.load_env()`.
"""

import logging
import os
from functools import cache

import anthropic

log = logging.getLogger(__name__)

# Pipeline stages that can each pick their own model via LLM_{PURPOSE}_MODEL.
PURPOSES = ("discovery", "extract", "scoring", "tailor", "cover")
EFFORTS = ("low", "medium", "high", "xhigh", "max")

# The SDK retries 408/409/429/5xx and connection errors with exponential backoff.
_MAX_RETRIES = 4
_TIMEOUT = 300.0  # seconds

_warned: set[str] = set()


def _warn_once(key: str, msg: str, *args: object) -> None:
    if key not in _warned:
        _warned.add(key)
        log.warning(msg, *args)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class LLMStopRun(RuntimeError):
    """No further request can succeed in this run (budget used up, bad key, API overloaded through every retry).

    Stage loops catch this, stop without counting an attempt for the job in progress, and leave the remaining
    jobs for the next run. `reason` is the stage's "stopped" value: "budget", "auth" or "overloaded".
    """

    def __init__(self, reason: str = "stopped", message: str | None = None) -> None:
        self.reason = reason
        super().__init__(message or reason)


class LLMResponseError(RuntimeError):
    """The model answered but the answer is unusable (refusal, cut off at max_tokens). Retryable per job."""


# ---------------------------------------------------------------------------
# Models, effort and prices (config/llm_models.yaml)
# ---------------------------------------------------------------------------

@cache
def load_models_config() -> dict:
    """The packaged llm_models.yaml: ``{"purposes": {...}, "models": {...}}``."""
    import yaml

    from strelitzia import config
    return yaml.safe_load((config.CONFIG_DIR / "llm_models.yaml").read_text(encoding="utf-8"))


def _purpose_config(purpose: str) -> dict:
    if purpose not in PURPOSES:
        raise ValueError(f"Unknown LLM purpose {purpose!r}; expected one of {', '.join(PURPOSES)}")
    return load_models_config()["purposes"][purpose]


def resolve_model(purpose: str) -> tuple[str, str]:
    """(model, effort) for a stage: LLM_{PURPOSE}_MODEL / LLM_{PURPOSE}_EFFORT, else llm_models.yaml.

    A model override that isn't a Claude model id (a leftover Gemini setting) is ignored with a one-time warning,
    and so is an unknown effort level.
    """
    defaults = _purpose_config(purpose)
    model, effort = defaults["model"], defaults["effort"]
    var = f"LLM_{purpose.upper()}_MODEL"
    override = os.environ.get(var, "").strip()
    if override:
        if override.startswith("claude-"):
            model = override
        else:
            _warn_once(var, "Ignoring %s=%s: not a Claude model id. Using %s.", var, override, model)
    var = f"LLM_{purpose.upper()}_EFFORT"
    override = os.environ.get(var, "").strip().lower()
    if override:
        if override in EFFORTS:
            effort = override
        else:
            _warn_once(var, "Ignoring %s=%s: expected one of %s.", var, override, ", ".join(EFFORTS))
    return model, effort


def model_prices(model: str) -> dict[str, float]:
    """USD per million tokens for a model. An unknown model is priced as the dearest known one (warned once)."""
    models = load_models_config()["models"]
    if model in models:
        return models[model]
    dearest = max(models.values(), key=lambda p: p["output"])
    _warn_once(f"price:{model}", "No price for %s in llm_models.yaml; counting it at the highest known price.", model)
    return dearest


def _usage_tokens(usage) -> dict[str, int]:
    """Token counts from an SDK `Usage` (or a dict with the same field names)."""
    def get(name: str) -> int:
        value = usage.get(name) if isinstance(usage, dict) else getattr(usage, name, None)
        return int(value or 0)
    return {
        "input_tokens": get("input_tokens"),
        "cache_read_tokens": get("cache_read_input_tokens"),
        "cache_write_tokens": get("cache_creation_input_tokens"),
        "output_tokens": get("output_tokens"),
    }


def cost_usd(model: str, usage, batch: bool = False) -> float:
    """Dollar cost of one response's `usage` (Batch API requests cost half)."""
    prices = model_prices(model)
    t = _usage_tokens(usage)
    total = (
        t["input_tokens"] * prices["input"]
        + t["cache_read_tokens"] * prices["cache_read"]
        + t["cache_write_tokens"] * prices["cache_write_5m"]
        + t["output_tokens"] * prices["output"]
    ) / 1_000_000
    return total * 0.5 if batch else total


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

def response_text(message) -> str:
    """The text of a Messages API response. Raises LLMResponseError for a refusal or a cut-off answer."""
    if message.stop_reason == "refusal":
        details = getattr(message, "stop_details", None)
        category = getattr(details, "category", None) if details else None
        raise LLMResponseError(f"The model declined the request (refusal{f': {category}' if category else ''})")
    if message.stop_reason == "max_tokens":
        raise LLMResponseError("The answer was cut off at max_tokens")
    return "".join(block.text for block in message.content if block.type == "text")


def _api_error(e: anthropic.APIError) -> Exception:
    """Map an SDK error (after its own retries) to what the stage loops expect."""
    if isinstance(e, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
        return LLMStopRun("auth", f"The Claude API rejected ANTHROPIC_API_KEY ({type(e).__name__}); check the key")
    if isinstance(e, (anthropic.RateLimitError, anthropic.OverloadedError)) or (
        isinstance(e, anthropic.APIStatusError) and e.status_code in (429, 529)
    ):
        return LLMStopRun("overloaded", f"The Claude API is overloaded or rate limited through every retry: {e}")
    return e


class LLMClient:
    """Claude client for one pipeline stage (model, effort and purpose fixed at construction)."""

    def __init__(self, purpose: str, model: str, effort: str) -> None:
        self.purpose = purpose
        self.model = model
        self.effort = effort
        self._sdk: anthropic.Anthropic | None = None

    @property
    def sdk(self) -> anthropic.Anthropic:
        """The SDK client, created on first use. The key comes only from ANTHROPIC_API_KEY (never a CLI profile)."""
        if self._sdk is None:
            key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
            if not key:
                raise LLMStopRun("auth", "ANTHROPIC_API_KEY is not set; run `strelitzia init` or add it to "
                                         "~/.strelitzia/.env")
            self._sdk = anthropic.Anthropic(api_key=key, max_retries=_MAX_RETRIES, timeout=_TIMEOUT)
        return self._sdk

    def request_params(
        self,
        messages: list[dict],
        max_tokens: int = 4096,
        schema: dict | None = None,
        effort: str | None = None,
    ) -> dict:
        """Messages API params for `messages` (role/content dicts; "system" entries become one cached system block).

        Used as-is by `chat()` and as the `params` of a Message Batches request.
        """
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        turns = [{"role": m["role"], "content": m["content"]} for m in messages if m["role"] != "system"]
        output_config: dict = {"effort": effort or self.effort}
        if schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": schema}
        params: dict = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": turns,
            "output_config": output_config,
        }
        if system:
            params["system"] = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
        return params

    def chat(
        self,
        messages: list[dict],
        max_tokens: int = 4096,
        schema: dict | None = None,
        effort: str | None = None,
    ) -> str:
        """Send one request and return the answer text (JSON text when `schema` is given)."""
        params = self.request_params(messages, max_tokens=max_tokens, schema=schema, effort=effort)
        try:
            message = self.sdk.messages.create(**params)
        except anthropic.APIError as e:
            raise _api_error(e) from e
        log.debug("%s: %s usage %s", self.purpose, self.model, _usage_tokens(message.usage))
        return response_text(message)

    def ask(self, prompt: str, **kwargs) -> str:
        """Convenience: single user prompt -> assistant response."""
        return self.chat([{"role": "user", "content": prompt}], **kwargs)


# ---------------------------------------------------------------------------
# Per-purpose clients
# ---------------------------------------------------------------------------

_clients: dict[str, LLMClient] = {}


def get_client(purpose: str = "discovery") -> LLMClient:
    """Return (or create) the LLMClient for a pipeline stage (one of `PURPOSES`)."""
    client = _clients.get(purpose)
    if client is None:
        model, effort = resolve_model(purpose)
        client = LLMClient(purpose, model, effort)
        log.info("LLM (%s): %s, effort %s", purpose, model, effort)
        _clients[purpose] = client
    return client


def get_discovery_client() -> LLMClient:
    """Alias for ``get_client("discovery")``."""
    return get_client("discovery")


def reset_clients() -> None:
    """Drop memoized clients (tests, env changes)."""
    _clients.clear()
