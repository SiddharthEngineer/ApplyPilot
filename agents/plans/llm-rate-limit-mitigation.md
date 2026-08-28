# Plan: LLM Rate-Limit Mitigation & Cost Optimization

**Started:** 2026-08-28
**Status:** 🔄 In Progress

---

## Goal

Eliminate the `429 Too Many Requests` collapse seen in `applypilot run` smart-extract (90 targets x 2-4 LLM calls each = 180-270 calls, exhausting Gemini free tier 15 RPM within minutes) and reduce per-run LLM cost by 60-80% without degrading scoring/tailoring quality. User-facing outcome: `applypilot run` completes the full `discover -> enrich -> score -> tailor -> cover` pipeline on Gemini free tier without manual retries, with optional zero-cost path via OpenCode free models (`opencode/*`) configured through `LLM_URL`/`LLM_MODEL` or a new `OPENCODE_API_KEY` provider.

## Success Criteria

1. `applypilot run` on a 90-target smart-extract crawl (6 queries x 12 searchable + 18 static sites) completes without any `429 Too Many Requests` in logs; total wall time for smart-extract stage drops from 20+ min (with backoffs) to <8 min on Gemini free tier.
2. Total LLM calls in smart-extract per full crawl reduced from ~180-270 to <=60 (measured by counting `LLM provider:` log lines or `get_client().chat` invocations); 429 retry count is 0 in a `LOG_LEVEL=INFO` run.
3. LLM cost per full pipeline (6 queries, ~50 scored jobs, 10 tailored) measured via token counts or call counts is <=40% of baseline (baseline = `gemini-3.6-flash` for all stages); verified by running `applypilot run score tailor --validation lenient` and counting calls.
4. Client-side RPM limiter enforces configurable rate (default 12 RPM for Gemini free tier) regardless of stage; `LLMClient.chat()` sleeps proactively before exceeding limit, not only reactively on 429.
5. At least two of the three smart-extract optimizations are verified independently: (a) heuristic pre-filter skips CAPTCHA/telemetry URLs without LLM, (b) batched judge uses 1 LLM call for N API responses, (c) per-domain strategy cache reuses strategy for repeat queries to same site.
6. Opencode free model path works: setting `LLM_URL=https://opencode.ai/zen/v1` (or equivalent OpenCode gateway) + `LLM_MODEL=opencode/nemotron-3-nano-free` (or `gemini-2.0-flash-lite`) routes all LLM calls through that endpoint; `applypilot doctor` reports the provider and `tests/test_llm.py` passes with mocked provider.
7. Wizard (`applypilot init`) and `applypilot doctor` expose the new env vars (`LLM_DISCOVERY_MODEL`, `LLM_RPM_LIMIT`, `OPENCODE_API_KEY` or `LLM_URL` path) and `.env.example` documents them with sensible defaults; `ruff check src/` passes.
8. Existing pipeline behavior is preserved: `tests/test_llm.py`, `tests/test_pipeline.py`, `tests/test_config.py` pass, and `applypilot run --dry-run` still previews all stages.

---

## Task Chain

### Task 1: Add client-side RPM limiter to LLMClient

**Files:** `src/applypilot/llm.py` (modify), `tests/test_llm.py` (modify)

**What:** Add a token-bucket / sliding-window rate limiter inside `LLMClient` that proactively throttles before hitting the provider. Read `LLM_RPM_LIMIT` env (default `12` for Gemini compat base, `60` for OpenAI/local) at `get_client()` time and store `self._rpm_limit`. Before each `chat()` attempt (including retries), check `self._request_timestamps` deque of last 60s; if `len >= limit`, sleep `60 - (now - oldest) + 0.5` seconds. Make window and limit configurable via `LLM_RPM_LIMIT` and `LLM_RPM_WINDOW` (default 60s). Log at `DEBUG` when throttling. This complements the existing reactive `429` backoff (`_RATE_LIMIT_BASE_WAIT`) rather than replacing it. Add unit test that verifies 3 rapid calls with limit=2 inserts a sleep (mock `time.sleep`).

**Acceptance criteria:**
- `LLMClient` has `_rpm_limit: int` and `_request_timestamps: deque[float]` and a `_throttle_if_needed()` method that enforces `LLM_RPM_LIMIT`.
- With `LLM_RPM_LIMIT=2`, three sequential `client.chat()` calls (mocked `httpx.Client.post` returning 200) result in exactly one `time.sleep` call of ~30s (verify via monkeypatched sleep).
- Existing `tests/test_llm.py` pass and new test `test_rpm_limiter` passes.
- `ruff check src/applypilot/llm.py` reports no violations.

**Status:** ✅ Complete

---

### Task 2: Heuristic pre-filter for Judge API responses (zero-LLM skip)

**Files:** `src/applypilot/discovery/smartextract.py` (modify), `tests/test_smartextract_heuristic.py` (new) or add to existing smartextract tests

**What:** Before calling `judge_api_responses()` LLM, filter `api_responses` with deterministic heuristics that already appear in logs as wasted calls: drop URLs matching `recaptcha`, `reload?k=`, `telemetry`, `web-vitals`, `get-session`, `auth/`, `prodregistry`, `algolia.*telemetry` unless the response `data` contains job-like keys (`title`, `job`, `position`, `company`, `location`). Implement `def _is_obviously_not_jobs(resp: dict) -> bool` that checks `resp["url"]` against a blocklist regex and `resp.get("type")`/`size` thresholds (e.g., `size < 200` with auth keys). Log `Judge heuristic SKIP: <url>` at INFO. Only remaining candidates go to the LLM judge. This removes the 1-2 telemetry/captcha calls per page that currently consume LLM quota for no value (visible in the user log: 4 telemetry/recaptcha judges before any real data).

**Acceptance criteria:**
- `_is_obviously_not_jobs()` returns `True` for URLs containing `recaptcha.net`, `telemetry`, `get-session` and `False` for a mock job API response with `first_item_keys=["title","company"]`.
- `judge_api_responses()` calls the heuristic first and only invokes `client.ask()` for non-skipped responses; verify by mocking `get_client` and asserting `ask` call count drops from N to M for a fixture with 3 telemetry + 1 real response.
- Smart-extract log for the 90-target fixture shows `Kept 0/1 relevant` only for non-telemetry responses; no LLM calls for recaptcha URLs.
- Tests pass: new heuristic tests + any existing smartextract tests.

**Status:** ✅ Complete

---

### Task 3: Batch Judge API responses into a single LLM call

**Files:** `src/applypilot/discovery/smartextract.py` (modify)

**What:** Replace the current per-response loop in `judge_api_responses()` (`for resp in api_responses: client.ask(JUDGE_PROMPT.format(...))` at `smartextract.py:362-408`) with a single batched prompt that lists all remaining (post-heuristic) API responses numbered `[1]..[N]` and asks the LLM to return a JSON array `[{"index": 1, "relevant": true/false, "reason": "..."}, ...]`. New prompt `JUDGE_BATCH_PROMPT` formats each response summary (url, status, size, type, keys, sample truncated to 300 chars) and instructs `Return ONLY valid JSON array, no markdown`. Parse with `extract_json()`, map `index -> relevant`, and fall back to sequential per-response calls if parsing fails. This collapses `N=5` judge calls (as seen in `Talent.com` log) into 1, saving 80% of judge calls.

**Acceptance criteria:**
- `judge_api_responses()` with 5 input responses makes exactly 1 `client.ask()` call (mocked) and returns `relevant` list correctly when the LLM returns `[{"index":1,"relevant":true,...}, ...]`.
- Fallback path: if the batch response is unparseable JSON, the function retries sequentially per-response (verify by returning invalid JSON from mock and asserting N follow-up calls).
- Token budget: batched prompt is <=4000 chars for 5 responses (truncate `sample` to 300 chars each); verified by asserting `len(prompt) < 6000`.
- No change to `format_strategy_briefing` or `STRATEGY_PROMPT` behavior.

**Status:** ❌ Not started

---

### Task 4: Per-domain strategy cache and target deduplication for smart-extract

**Files:** `src/applypilot/discovery/smartextract.py` (modify), `src/applypilot/config/sites.yaml` (modify - optional comment), `tests/test_smartextract_cache.py` (new)

**What:** Add an in-memory (and optionally on-disk `~/.applypilot/.smartextract_cache.json`) cache keyed by `(site_name, domain)` that stores the last successful `strategy` + `extraction` plan (e.g., `Eluta -> css_selectors` with selectors). When `build_scrape_targets()` produces 6 queries for the same searchable site (`Eluta x 6`), `_run_one_site()` first checks `cache.get(site_name)`; if hit and the cached strategy was `css_selectors` or `json_ld` and the page's `card_candidates` shape matches (same `child_tag` count), reuse it by directly calling `execute_css_selectors()` / `execute_json_ld()` without the LLM strategy call. Add a CLI flag `--no-cache` to bypass. Also deduplicate targets: if `sites.yaml` has duplicate search sites or the same `(name, query)` appears twice, `build_scrape_targets()` deduplicates via `set()`. Log `Cache HIT for Eluta -> css_selectors (skipping strategy LLM)` at INFO. This saves 5/6 strategy calls per searchable site (e.g., 12 sites x 6 queries = 72 targets -> 12 strategy calls instead of 72).

**Acceptance criteria:**
- With `sites=[Eluta]` and `queries=[Data Scientist, Software Engineer, AI Engineer]`, a mocked `ask_llm` for strategy is called exactly once (first query); subsequent queries reuse cache and make 0 strategy calls (verify via mock call count).
- `build_scrape_targets()` deduplicates identical `(name, url)` pairs; 90-target expansion with 6 queries x 12 sites produces <=72 search targets + 18 static = 90, not 90+duplicates.
- Cache is invalidated if `page_title` contains CAPTCHA signals or `card_candidates` is empty (fallback to fresh LLM).
- Test `test_strategy_cache_hit_and_miss` passes.

**Status:** ❌ Not started

---

### Task 5: Tiered model configuration and cheaper defaults for discovery

**Files:** `src/applypilot/llm.py` (modify), `src/applypilot/config.py` (modify), `src/applypilot/discovery/smartextract.py` (modify), `.env.example` (modify)

**What:** Introduce environment variables for per-stage models so discovery can use a cheaper/lighter model than tailoring: `LLM_DISCOVERY_MODEL` (default `gemini-2.0-flash-lite` when `GEMINI_API_KEY` is set, else inherits `LLM_MODEL`), `LLM_SCORING_MODEL`, `LLM_TAILOR_MODEL` (both default to `LLM_MODEL`). Refactor `_detect_provider()` to accept an optional `purpose: str` argument and add helper `def get_discovery_client() -> LLMClient` that memoizes a separate singleton per purpose (or passes `model=LLM_DISCOVERY_MODEL` to `LLMClient`). Update `smartextract.py:ask_llm()` and `judge_api_responses()` to call `get_discovery_client()` instead of `get_client()`, while `scorer.py`, `tailor.py`, `cover_letter.py` keep `get_client()` (higher-quality model). For Gemini, default discovery to `gemini-2.0-flash-lite` (5x cheaper input tokens, ~2x higher free-tier RPM in practice, and sufficient for classification tasks). Document in `.env.example` with comments explaining `LLM_DISCOVERY_MODEL=gemini-2.0-flash-lite` saves cost.

**Acceptance criteria:**
- `GEMINI_API_KEY=key` with no `LLM_MODEL`/`LLM_DISCOVERY_MODEL` set results in `get_client().model == "gemini-3.6-flash"` and `get_discovery_client().model == "gemini-2.0-flash-lite"` (verify via env patching in test).
- `LLM_DISCOVERY_MODEL=custom` overrides the discovery default regardless of `LLM_MODEL`.
- `smartextract.py` imports and uses `get_discovery_client()`; `scorer.py` still uses `get_client()` (grep check).
- `.env.example` contains commented `LLM_DISCOVERY_MODEL` and `LLM_RPM_LIMIT` entries.
- `applypilot doctor` reports both models when they differ.

**Status:** ❌ Not started

---

### Task 6: Integrate OpenCode free models as an LLM provider

**Files:** `src/applypilot/llm.py` (modify), `src/applypilot/cli.py` (modify), `src/applypilot/wizard/init.py` (modify), `tests/test_llm.py` (modify)

**What:** Add first-class support for OpenCode's Zen gateway / free models as an LLM provider alongside Gemini/OpenAI/local. Detection order in `_detect_provider(purpose)`: if `OPENCODE_API_KEY` is set (or `LLM_URL` points to `opencode.ai`), return `(base_url="https://opencode.ai/zen/v1" or LLM_URL, model=LLM_MODEL or "opencode/nemotron-3-nano-free", api_key=OPENCODE_API_KEY)`. The Zen gateway is OpenAI-compatible, so `LLMClient` needs no new transport; just ensure `Authorization: Bearer <key>` header is set and `model` is forwarded verbatim (e.g., `opencode/nemotron-3-nano-free`, `opencode/gemini-2.0-flash-lite-free`). If the user has `opencode` CLI installed, `wizard/init.py` should offer "Use OpenCode free models (no API key needed if you run `opencode auth`)" and set `LLM_URL=http://127.0.0.1:4096/v1` (local OpenCode server) if they choose it. Add `doctor` check that validates OpenCode provider: `GET {base_url}/models` with the key and lists availability. This is the user-requested "explore somehow using the opencode free models" path.

**Acceptance criteria:**
- With `OPENCODE_API_KEY=sk-test` and no Gemini key, `_detect_provider()` returns `("https://opencode.ai/zen/v1", "opencode/nemotron-3-nano-free", "sk-test")` (or respects `LLM_MODEL` override).
- With `LLM_URL=http://127.0.0.1:4096/v1` and `LLM_MODEL=opencode/nemotron-3-nano-free`, `get_client()` connects to that URL regardless of Gemini key (existing local-url path already handles this; verify no regression).
- `LLMClient.chat()` against a mocked `https://opencode.ai/zen/v1/chat/completions` returns success (httpx MockTransport).
- `applypilot doctor` shows `LLM API key: OpenCode (opencode/nemotron-3-nano-free)` when `OPENCODE_API_KEY` is set, and does not require `GEMINI_API_KEY`.
- Wizard offers OpenCode as a provider option and writes correct `.env` lines.

**Status:** ❌ Not started

---

### Task 7: Wire new env vars through wizard, doctor, and docs

**Files:** `src/applypilot/wizard/init.py` (modify), `src/applypilot/cli.py` (modify), `.env.example` (modify), `src/applypilot/config.py` (modify), `README.md` (modify)

**What:** Update the first-time setup wizard (`wizard/init.py:550-580`) to prompt for `LLM_DISCOVERY_MODEL` (default `gemini-2.0-flash-lite` with explanation "Cheaper model for discovery; saves 5x cost") and `LLM_RPM_LIMIT` (default `12`, with note "Gemini free tier = 15 RPM; set 12 to stay safe"), and to offer OpenCode provider (from Task 6). Update `cli.py:doctor()` to validate `LLM_DISCOVERY_MODEL` against the Gemini model list (if Gemini provider) and to print `Discovery model: ...`, `RPM limit: ...`, `Provider: ...`. Update `.env.example` to document all new vars with comments and example values. Add a short "Cost & Rate Limits" section to `README.md` explaining free-tier limits, `--validation lenient` (already saves 1 call per tailor attempt), `--workers 1` (default), and the new `LLM_DISCOVERY_MODEL`/`LLM_RPM_LIMIT` knobs. Update `config.py:DEFAULTS` to include `llm_rpm_limit=12` and `llm_discovery_model="gemini-2.0-flash-lite"`.

**Acceptance criteria:**
- `applypilot init` (mocked `Prompt.ask`) writes `LLM_DISCOVERY_MODEL` and `LLM_RPM_LIMIT` to `~/.applypilot/.env` when the user accepts defaults.
- `applypilot doctor` prints `Discovery model:` and `RPM limit:` lines and validates the discovery model against the live Gemini model list (or warns if not found, same pattern as existing `cli.py:480-501`).
- `.env.example` contains `LLM_DISCOVERY_MODEL`, `LLM_RPM_LIMIT`, `OPENCODE_API_KEY` with comments.
- `README.md` has a "Cost & Rate Limits" subsection under Requirements or Configuration that mentions `gemini-2.0-flash-lite`, `LLM_RPM_LIMIT`, and `opencode/*` free models.
- `ruff check src/` and `pytest tests/test_init_wizard.py tests/test_llm.py tests/test_config.py` pass.

**Status:** ❌ Not started

---

## Implementation Order

```
Task 1 (RPM limiter — foundation, blocks all other LLM tasks)
  ├─→ Task 2 (heuristic filter) ──→ Task 3 (batch judge) ──→ Task 4 (strategy cache)
  │         independent of each other but sequential for clean diffs
  └─→ Task 5 (tiered models) ──→ Task 6 (OpenCode provider)
                                        ↓
                                  Task 7 (wizard/doctor/docs — needs Tasks 5-6)
```

1. Task 1 — RPM limiter (correctness foundation; all subsequent LLM calls benefit).
2. Task 2 — Heuristic pre-filter (zero-risk, immediate 20-30% call reduction).
3. Task 3 — Batched judge (biggest single win; depends on Task 2's filtered list shape).
4. Task 4 — Strategy cache + dedup (amortizes strategy calls; independent of Tasks 2-3 but logically after).
5. Task 5 — Tiered model config (cheaper discovery model; needs Task 1's RPM limit to be tunable per purpose).
6. Task 6 — OpenCode provider (alternative zero-cost path; builds on Task 5's provider abstraction).
7. Task 7 — Wizard/doctor/docs (requires Tasks 5-6 env vars finalized).

Tasks 2 and 5 could be parallelized after Task 1 if two sessions are available, but the sequential order above minimizes merge conflicts in `smartextract.py` and `llm.py`.

## Key Design Decisions

1. **Client-side RPM limiter instead of just increasing backoff** — Reactive backoff (existing `10s * 2^attempt`) still burns quota and wall time; a proactive sliding-window prevents 429 entirely and is the only way to stay under Gemini's 15 RPM without relying on `Retry-After` headers that often omit the true quota window.
2. **Heuristic pre-filter before LLM judge** — Captures the obvious `recaptcha`/`telemetry`/`get-session` calls that the log shows wasting 4 LLM calls per page; deterministic filtering is free and has zero false negatives when checking URL blocklist + job-key absence.
3. **Batch judge into one LLM call** — The current per-response judge is the worst call-multiplier (`N` calls where `N` is intercepted API count); batching is safe because the judge task is classification, not extraction, and the array output is easy to parse with fallback to sequential.
4. **Per-domain strategy cache** — Searchable sites like `Eluta` use the same DOM across 6 queries; caching `css_selectors` for the same `site_name` saves 5/6 strategy calls with near-zero risk since the selector is validated against `card_candidates` shape before reuse.
5. **`gemini-2.0-flash-lite` as discovery default** — Flash-Lite is ~5x cheaper than Flash on input tokens and handles classification (judge/strategy) at equal quality; `gemini-3.6-flash` remains the tailoring default where reasoning quality matters, preserving user-visible resume/cover quality while cutting discovery cost ~80%.
6. **OpenCode Zen gateway as a provider, not a separate CLI** — The `llm.py` client already supports OpenAI-compatible endpoints via `LLM_URL`; adding `OPENCODE_API_KEY` as a detected provider reuses that transport and lets users switch to free `opencode/*` models with a single env var, without forking the pipeline or requiring `opencode` CLI to be running (though `http://127.0.0.1:4096/v1` local path is also supported).

## Historical Record

- 2026-08-28 — Plan created. Root cause: smart-extract makes 180-270 LLM calls per 90-target crawl (1 judge per API response + 1 strategy + 1 Phase2 per site), exceeding Gemini free tier 15 RPM. Plan proposes 7 tasks: RPM limiter, heuristic filter, batched judge, strategy cache, tiered models, OpenCode provider, wizard/docs wiring.

