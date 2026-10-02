"""
Unified LLM client for ApplyPilot.

Auto-detects provider from environment:
  GEMINI_API_KEY  -> Google Gemini (default: gemini-3.6-flash)
  OPENAI_API_KEY  -> OpenAI (default: gpt-4o-mini)
  OPENCODE_API_KEY -> OpenCode Zen gateway (default: opencode/nemotron-3-nano-free)
  LLM_URL         -> Local llama.cpp / Ollama / OpenCode gateway compatible endpoint

LLM_MODEL env var overrides the model name for any provider.
"""

import logging
import os
import time
from collections import deque

import httpx

log = logging.getLogger(__name__)

# Pipeline stages that can each pick their own model via LLM_{PURPOSE}_MODEL.
PURPOSES = ("discovery", "scoring", "tailor", "cover")

# ---------------------------------------------------------------------------
# Provider detection
# ---------------------------------------------------------------------------

def _detect_provider(purpose: str | None = None) -> tuple[str, str, str]:
    """Return (base_url, model, api_key) based on environment variables.

    Reads env at call time (not module import time) so that load_env() called
    in _bootstrap() is always visible here.

    `purpose` selects a per-stage model: for any purpose in `PURPOSES` the
    model resolves as `LLM_{PURPOSE}_MODEL` -> `LLM_MODEL` -> provider default.
    On Gemini the discovery default is the cheaper `gemini-3.1-flash-lite`
    (enough for classification/judge, with a higher free-tier quota).
    """
    gemini_key = os.environ.get("GEMINI_API_KEY", "")
    openai_key = os.environ.get("OPENAI_API_KEY", "")
    opencode_key = os.environ.get("OPENCODE_API_KEY", "")
    local_url = os.environ.get("LLM_URL", "")
    model_override = os.environ.get("LLM_MODEL", "")
    if purpose in PURPOSES:
        model_override = os.environ.get(f"LLM_{purpose.upper()}_MODEL", "") or model_override

    # Explicit local endpoint (Ollama/llama.cpp) always wins when LLM_URL points
    # somewhere other than the OpenCode gateway. An OpenCode gateway URL is
    # handled by the OpenCode branch below so OPENCODE_API_KEY + a local URL do
    # not hijack each other.
    if local_url and "opencode.ai" not in local_url:
        return (
            local_url.rstrip("/"),
            model_override or "local-model",
            os.environ.get("LLM_API_KEY", ""),
        )

    # OpenCode Zen gateway (free models) — first-class provider reusing the
    # OpenAI-compatible transport. Priority: explicit OPENCODE_API_KEY, then a
    # LLM_URL pointing at opencode.ai.
    if opencode_key:
        base = local_url.rstrip("/") if "opencode.ai" in local_url else "https://opencode.ai/zen/v1"
        model = model_override or "opencode/nemotron-3-nano-free"
        return (base, model, opencode_key)
    if "opencode.ai" in local_url:
        model = model_override or "opencode/nemotron-3-nano-free"
        api_key = opencode_key or os.environ.get("LLM_API_KEY", "")
        return (local_url.rstrip("/"), model, api_key)

    if gemini_key and not local_url:
        default = "gemini-3.1-flash-lite" if purpose == "discovery" else "gemini-3.6-flash"
        model = model_override or default
        return (
            "https://generativelanguage.googleapis.com/v1beta/openai",
            model,
            gemini_key,
        )

    if openai_key and not local_url:
        return (
            "https://api.openai.com/v1",
            model_override or "gpt-4o-mini",
            openai_key,
        )

    if local_url:
        return (
            local_url.rstrip("/"),
            model_override or "local-model",
            os.environ.get("LLM_API_KEY", ""),
        )

    raise RuntimeError(
        "No LLM provider configured. "
        "Set GEMINI_API_KEY, OPENAI_API_KEY, or LLM_URL in your environment."
    )


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

_MAX_RETRIES = 5
_TIMEOUT = 120  # seconds

# Base wait on first 429/503 (doubles each retry, caps at 60s).
# Gemini free tier is 15 RPM = 4s minimum between requests; 10s gives headroom.
_RATE_LIMIT_BASE_WAIT = 10


_GEMINI_COMPAT_BASE = "https://generativelanguage.googleapis.com/v1beta/openai"
_GEMINI_NATIVE_BASE = "https://generativelanguage.googleapis.com/v1beta"


class LLMQuotaExhausted(RuntimeError):
    """A per-day quota is used up: no retry inside this run can succeed.

    Stage loops catch this, stop, and leave the remaining jobs for the next run.
    """

    def __init__(self, model: str, scope: str) -> None:
        self.model = model
        self.scope = scope
        super().__init__(f"Gemini daily quota exhausted for {model} ({scope})")


def _parse_quota_error(resp: httpx.Response) -> tuple[str | None, float | None]:
    """Read a Gemini 429 body. Returns (per-day quotaId or None, retryDelay seconds or None).

    Native errors are ``{"error": {...}}``; the OpenAI-compat layer wraps the
    same object in a list.
    """
    try:
        body = resp.json()
    except Exception:  # noqa: BLE001 - non-JSON body: treat as a plain transient 429
        return None, None
    if isinstance(body, list):
        body = body[0] if body else {}
    err = body.get("error") if isinstance(body, dict) else None
    if not isinstance(err, dict):
        return None, None

    per_day: str | None = None
    retry_delay: float | None = None
    for detail in err.get("details") or []:
        if not isinstance(detail, dict):
            continue
        for v in detail.get("violations") or []:
            quota_id = v.get("quotaId", "") if isinstance(v, dict) else ""
            if "PerDay" in quota_id and err.get("status") == "RESOURCE_EXHAUSTED":
                per_day = quota_id
        delay = detail.get("retryDelay")
        if isinstance(delay, str) and delay.endswith("s"):
            try:
                retry_delay = float(delay[:-1])
            except ValueError:
                pass
    return per_day, retry_delay


class LLMClient:
    """Thin LLM client supporting OpenAI-compatible and native Gemini endpoints.

    For Gemini keys, starts on the OpenAI-compat layer. On a 400/403/404 (which
    happens with preview/experimental models not exposed via compat), it
    automatically switches to the native generateContent API and stays there
    for the lifetime of the process.
    """

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str,
        rpm_limit: int = 0,
        rpm_window: float = 60.0,
        request_timestamps: deque[float] | None = None,
    ) -> None:
        self.base_url = base_url
        self.model = model
        self.api_key = api_key
        self._client = httpx.Client(timeout=_TIMEOUT)
        # True once we've confirmed the native Gemini API works for this model
        self._use_native_gemini: bool = False
        self._is_gemini: bool = base_url.startswith(_GEMINI_COMPAT_BASE)
        # Client-side RPM limiter
        self._rpm_limit: int = rpm_limit
        self._rpm_window: float = rpm_window
        # get_client() passes a deque shared by every client on the same model,
        # so per-stage clients can't jointly exceed that model's RPM.
        self._request_timestamps: deque[float] = (
            request_timestamps if request_timestamps is not None else deque()
        )

    # -- RPM limiter --------------------------------------------------------

    def _throttle_if_needed(self) -> None:
        """Sleep if we'd exceed the RPM limit within the current window."""
        if self._rpm_limit <= 0:
            return

        now = time.monotonic()
        # Drop timestamps outside the window
        while self._request_timestamps and self._request_timestamps[0] <= now - self._rpm_window:
            self._request_timestamps.popleft()

        if len(self._request_timestamps) >= self._rpm_limit:
            oldest = self._request_timestamps[0]
            sleep_time = self._rpm_window - (now - oldest) + 0.5
            log.debug(
                "RPM limiter: %d requests in %.1fs window (limit %d). Sleeping %.1fs.",
                len(self._request_timestamps),
                self._rpm_window,
                self._rpm_limit,
                sleep_time,
            )
            time.sleep(sleep_time)
            # Re-drop after sleeping
            now = time.monotonic()
            while self._request_timestamps and self._request_timestamps[0] <= now - self._rpm_window:
                self._request_timestamps.popleft()

    def _record_request(self) -> None:
        """Record the timestamp of a request for RPM tracking."""
        if self._rpm_limit > 0:
            self._request_timestamps.append(time.monotonic())

    # -- Native Gemini API --------------------------------------------------

    def _chat_native_gemini(
        self,
        messages: list[dict],
        temperature: float,
        max_tokens: int,
    ) -> str:
        """Call the native Gemini generateContent API.

        Used automatically when the OpenAI-compat endpoint returns 400/403/404,
        which happens for preview/experimental models not exposed via compat.

        Converts OpenAI-style messages to Gemini's contents/systemInstruction
        format transparently.
        """
        contents: list[dict] = []
        system_parts: list[dict] = []

        for msg in messages:
            role = msg["role"]
            text = msg.get("content", "")
            if role == "system":
                system_parts.append({"text": text})
            elif role == "user":
                contents.append({"role": "user", "parts": [{"text": text}]})
            elif role == "assistant":
                # Gemini uses "model" instead of "assistant"
                contents.append({"role": "model", "parts": [{"text": text}]})

        payload: dict = {
            "contents": contents,
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
            },
        }
        if system_parts:
            payload["systemInstruction"] = {"parts": system_parts}

        url = f"{_GEMINI_NATIVE_BASE}/models/{self.model}:generateContent"
        resp = self._client.post(
            url,
            json=payload,
            headers={"Content-Type": "application/json"},
            params={"key": self.api_key},
        )
        resp.raise_for_status()
        data = resp.json()
        return data["candidates"][0]["content"]["parts"][0]["text"]

    # -- OpenAI-compat API --------------------------------------------------

    def _chat_compat(
        self,
        messages: list[dict],
        temperature: float,
        max_tokens: int,
    ) -> str:
        """Call the OpenAI-compatible endpoint."""
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        resp = self._client.post(
            f"{self.base_url}/chat/completions",
            json=payload,
            headers=headers,
        )

        # 400/403/404 on Gemini compat = model not available on compat layer.
        # Raise a specific sentinel so chat() can switch to native API.
        if resp.status_code in (400, 403, 404) and self._is_gemini:
            raise _GeminiCompatForbidden(resp)

        return self._handle_compat_response(resp)

    @staticmethod
    def _handle_compat_response(resp: httpx.Response) -> str:
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]

    # -- public API ---------------------------------------------------------

    def chat(
        self,
        messages: list[dict],
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> str:
        """Send a chat completion request and return the assistant message text."""
        # Qwen3 optimization: prepend /no_think to skip chain-of-thought
        # reasoning, saving tokens on structured extraction tasks.
        if "qwen" in self.model.lower() and messages:
            first = messages[0]
            if first.get("role") == "user" and not first["content"].startswith("/no_think"):
                messages = [{"role": first["role"], "content": f"/no_think\n{first['content']}"}] + messages[1:]

        for attempt in range(_MAX_RETRIES):
            try:
                self._throttle_if_needed()

                # Route to native Gemini if we've already confirmed it's needed
                if self._use_native_gemini:
                    result = self._chat_native_gemini(messages, temperature, max_tokens)
                    self._record_request()
                    return result

                result = self._chat_compat(messages, temperature, max_tokens)
                self._record_request()
                return result

            except _GeminiCompatForbidden as exc:
                # Model not available on OpenAI-compat layer — switch to native.
                log.warning(
                    "Gemini compat endpoint returned %d for model '%s'. "
                    "Switching to native generateContent API. "
                    "(Preview/experimental models are often compat-only on native.) "
                    "Body: %s",
                    exc.response.status_code, self.model, exc.response.text[:300],
                )
                self._use_native_gemini = True
                # Retry immediately with native — don't count as a rate-limit wait
                try:
                    return self._chat_native_gemini(messages, temperature, max_tokens)
                except httpx.HTTPStatusError as native_exc:
                    if native_exc.response.status_code == 429:
                        self._raise_if_daily_quota(native_exc.response)
                    raise RuntimeError(
                        f"Both Gemini endpoints failed. Compat: {exc.response.status_code}. "
                        f"Native: {native_exc.response.status_code} — "
                        f"{native_exc.response.text[:200]}"
                    ) from native_exc

            except httpx.HTTPStatusError as exc:
                resp = exc.response
                retry_delay = None
                if resp.status_code == 429:
                    retry_delay = self._raise_if_daily_quota(resp)
                if resp.status_code in (429, 503) and attempt < _MAX_RETRIES - 1:
                    # Respect Retry-After header if provided (Gemini sends this).
                    retry_after = (
                        resp.headers.get("Retry-After")
                        or resp.headers.get("X-RateLimit-Reset-Requests")
                    )
                    if retry_delay is not None:
                        # Gemini's RetryInfo says when the per-minute window frees up.
                        wait = min(retry_delay + 1, 90)
                    elif retry_after:
                        try:
                            wait = float(retry_after)
                        except (ValueError, TypeError):
                            wait = _RATE_LIMIT_BASE_WAIT * (2 ** attempt)
                    else:
                        wait = min(_RATE_LIMIT_BASE_WAIT * (2 ** attempt), 60)

                    log.warning(
                        "LLM rate limited (HTTP %s). Waiting %ds before retry %d/%d. "
                        "Tip: Gemini free tier = 15 RPM. Consider a paid account "
                        "or switching to a local model.",
                        resp.status_code, wait, attempt + 1, _MAX_RETRIES,
                    )
                    time.sleep(wait)
                    continue
                raise

            except httpx.TimeoutException:
                if attempt < _MAX_RETRIES - 1:
                    wait = min(_RATE_LIMIT_BASE_WAIT * (2 ** attempt), 60)
                    log.warning(
                        "LLM request timed out, retrying in %ds (attempt %d/%d)",
                        wait, attempt + 1, _MAX_RETRIES,
                    )
                    time.sleep(wait)
                    continue
                raise

        raise RuntimeError("LLM request failed after all retries")

    def _raise_if_daily_quota(self, resp: httpx.Response) -> float | None:
        """Raise LLMQuotaExhausted for a per-day 429; else return its retryDelay (if any)."""
        per_day, retry_delay = _parse_quota_error(resp)
        if per_day:
            log.error("Gemini daily quota exhausted for %s (%s). Stopping; resume tomorrow.",
                      self.model, per_day)
            raise LLMQuotaExhausted(self.model, per_day)
        return retry_delay

    def ask(self, prompt: str, **kwargs) -> str:
        """Convenience: single user prompt -> assistant response."""
        return self.chat([{"role": "user", "content": prompt}], **kwargs)

    def close(self) -> None:
        self._client.close()


class _GeminiCompatForbidden(Exception):
    """Sentinel: Gemini OpenAI-compat returned 400/403/404. Switch to native API."""
    def __init__(self, response: httpx.Response) -> None:
        self.response = response
        super().__init__(f"Gemini compat {response.status_code}: {response.text[:200]}")


# ---------------------------------------------------------------------------
# Per-purpose clients
# ---------------------------------------------------------------------------

_clients: dict[str, LLMClient] = {}
# One RPM window per model, shared by all purposes that use it.
_rpm_timestamps: dict[str, deque[float]] = {}


def get_client(purpose: str = "default") -> LLMClient:
    """Return (or create) the LLMClient for a pipeline stage.

    `purpose` is one of `PURPOSES` or "default" (plain `LLM_MODEL`). Each
    purpose is memoized separately so stages can run different models in the
    same process.
    """
    client = _clients.get(purpose)
    if client is None:
        base_url, model, api_key = _detect_provider(purpose)
        rpm_limit = int(os.environ.get("LLM_RPM_LIMIT", "0"))
        rpm_window = float(os.environ.get("LLM_RPM_WINDOW", "60"))
        log.info("LLM provider (%s): %s  model: %s", purpose, base_url, model)
        client = LLMClient(
            base_url, model, api_key,
            rpm_limit=rpm_limit, rpm_window=rpm_window,
            request_timestamps=_rpm_timestamps.setdefault(model, deque()),
        )
        _clients[purpose] = client
    return client


def get_discovery_client() -> LLMClient:
    """Alias for ``get_client("discovery")``."""
    return get_client("discovery")


def reset_clients() -> None:
    """Drop memoized clients and RPM windows (tests, env changes)."""
    _clients.clear()
    _rpm_timestamps.clear()
