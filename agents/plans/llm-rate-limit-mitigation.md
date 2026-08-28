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

**Files:** `src/applypilot/llm.py` (modify - DONE), `tests/test_llm.py` (modify - DONE)

**What:** Added sliding-window rate limiter inside `LLMClient` that proactively throttles before hitting the provider. `LLMClient.__init__()` at `src/applypilot/llm.py:88` now accepts `rpm_limit:int` and `rpm_window:float` and stores `self._rpm_limit`, `self._rpm_window`, `self._request_timestamps: deque[float]` (`:104-106`). `get_client()` at `:342` reads `LLM_RPM_LIMIT` (default `0` = disabled; set `12` for Gemini free-tier compat) and `LLM_RPM_WINDOW` (default `60.0`) from env. `_throttle_if_needed()` (`:110`) drops expired timestamps, sleeps `window - (now - oldest) + 0.5` when `len >= limit`, logs at DEBUG; `_record_request()` (`:136`) appends `time.monotonic()`. `chat()` (`:247`) calls throttle before each attempt and records on success. Complements existing reactive 429 backoff (`_RATE_LIMIT_BASE_WAIT=10`, `:72`).

**Acceptance criteria:**
- `LLMClient` has `_rpm_limit:int`, `_rpm_window:float`, `_request_timestamps:deque[float]` and `_throttle_if_needed()` enforcing `LLM_RPM_LIMIT`.
- With `LLM_RPM_LIMIT=2`, three sequential `client.chat()` (mocked `httpx.Client.post` 200) result in exactly one `time.sleep` of ~30s (verify via `time.monotonic`/`time.sleep` monkeypatch — `tests/test_llm.py:217`).
- `PYTHONPATH=src .venv/bin/python -m pytest tests/test_llm.py::TestRPMLimiter -v` passes (4 tests).
- `ruff check src/applypilot/llm.py` clean.

**Status:** ✅ Complete (2026-08-28, commit cca5c7a)

---

### Task 2: Heuristic pre-filter for Judge API responses (zero-LLM skip)

**Files:** `src/applypilot/discovery/smartextract.py` (modify - DONE), `tests/test_smartextract_heuristic.py` (new - DONE)

**What:** Deterministic blocklist filter before `judge_api_responses()` LLM. Implemented at `src/applypilot/discovery/smartextract.py:385-425`: `_NON_JOB_URL_RE` (recaptcha|reload\?k=|telemetry|web-vitals|get-session|/auth/|prodregistry|algolia.*telemetry), `_JOB_LIKE_KEYS` frozenset (title, job, position, company, location, salary, description, department, employment_type, date_posted), `def _is_obviously_not_jobs(resp: dict) -> bool` (if `size<200` and blocklist match → True; else blocklist match without job-like keys in `first_item_keys`/`keys` → True). `judge_api_responses()` (`:540`) filters first, logs `Judge heuristic SKIP: <url>` at INFO + `Heuristic skipped N/M (zero LLM cost)`.

**Acceptance criteria:**
- `_is_obviously_not_jobs()` returns `True` for recaptcha/telemetry/get-session URLs, `False` for mock job API with `first_item_keys=["title","company"]` — 13 unit tests at `tests/test_smartextract_heuristic.py:17`.
- `judge_api_responses()` with 3 telemetry + 1 real → exactly 1 `ask` call (integration at `tests/test_smartextract_heuristic.py:99`); all-skipped → 0 calls (`:128`).
- `PYTHONPATH=src .venv/bin/python -m pytest tests/test_smartextract_heuristic.py -v` — 17 passed; `ruff check` clean.

**Status:** ✅ Complete (2026-08-28, commit 35a3ace)

---

### Task 3: Batch Judge API responses into a single LLM call

**Files:** `src/applypilot/discovery/smartextract.py` (modify - DONE), `tests/test_smartextract_batch_judge.py` (new - DONE)

**What:** Collapse per-response judge loop into one batched call with sequential fallback. Added `JUDGE_BATCH_PROMPT` (`src/applypilot/discovery/smartextract.py:449`) listing `[1]..[N]` summaries via `_format_response_summary()` (`:467`, sample truncated 300 chars). `judge_api_responses()` (`:540-614`): if `len(candidates)==1` uses `_judge_sequential()` (`:493`); else builds batch prompt → single `client.ask()` → `extract_json()` expecting `list[dict{"index","relevant","reason"}]` → validates `len(verdict_map)==len(candidates)` else fallback. Fallback replays per-response `JUDGE_PROMPT` calls, keeping on LLM error.

**Acceptance criteria:**
- 5 inputs → exactly 1 `client.ask()` and correct `relevant` filtering (`tests/test_smartextract_batch_judge.py:86`); invalid JSON / non-list / missing verdicts → fallback to N+1 calls (`:245,:280,:314`).
- Batched prompt `len < 6000` for 5 responses (sample truncation) (`:428`); single-candidate path uses sequential (`:217`).
- `PYTHONPATH=src .venv/bin/python -m pytest tests/test_smartextract_batch_judge.py -v` — 16 passed (33 with heuristic suite); `ruff check` clean.

**Status:** ✅ Complete (2026-08-28, commit f661e77)

---

### Task 4: Per-domain strategy cache and target deduplication for smart-extract

**Files:** `src/applypilot/discovery/smartextract.py` (modify - DONE), `src/applypilot/cli.py` (modify - DONE), `src/applypilot/pipeline.py` (modify - DONE), `tests/test_smartextract_cache.py` (new - DONE)

**What:** In-memory + on-disk strategy cache keyed by `(site_name, domain)` and target deduplication. Implementation at `src/applypilot/discovery/smartextract.py:49-91,1091-1178,1234-1272,1368-1371`:

- ` _strategy_cache: dict[tuple[str,str],dict]` (`:52`), `_strategy_cache_enabled:bool` (`:53`), `_CACHE_FILE = CONFIG_DIR / ".smartextract_cache.json"` (`:55`, i.e. `src/applypilot/config/.smartextract_cache.json`, not `~/.applypilot/`), `_get_cache_key()` (`:58` via `urlparse`), `_load/_save_strategy_cache()` (`:64/:80`).
- `_run_one_site()` (`:1091`) checks cache before LLM strategy: `css_selectors` requires `child_tag` match, `json_ld` requires `json_ld` present, `api_response` **not cached** (API data is per-query — see `tests/test_smartextract_cache.py:299`). Invalidates on CAPTCHA signals in `full_html` or empty `card_candidates` / shape mismatch (`:1104-1124`). Persists on success for `css_selectors`/`json_ld` (`:1170`).
- `build_scrape_targets()` (`:1234`) dedup via `seen:set[tuple[str,str]]` for identical `(name, expanded_url)` — covers duplicate `sites.yaml` entries and same-query duplication.
- CLI `applypilot run --no-cache` (`src/applypilot/cli.py:112`) + pipeline plumbing `src/applypilot/pipeline.py:62,294,342,368` sets `_strategy_cache_enabled = not no_cache` in `run_smart_extract()` (`:1368`).

**Acceptance criteria:**
- With mocked `ask_llm`, `sites=[Eluta] x 3 queries` → exactly 1 strategy `ask` call, next 2 hit cache (`tests/test_smartextract_cache.py:161`); shape-mismatch and CAPTCHA bypass cache (`:192,:233`); `no_cache` disables entirely (`:265`); `api_response` not cached (`:299`).
- `build_scrape_targets()` with 12 search × 6 queries + 18 static → exactly 90 targets, duplicate entries dedup to 1 (`:74,:39`).
- `PYTHONPATH=src .venv/bin/python -m pytest tests/test_smartextract_cache.py -v` — 18 passed.

**Status:** ✅ Complete (2026-08-28, commit d8ee86b — previously unmarked; corrected here)

---

### Task 5: Tiered model configuration and cheaper defaults for discovery

**Files:** `src/applypilot/llm.py` (modify), `src/applypilot/config.py` (modify), `src/applypilot/discovery/smartextract.py` (modify), `.env.example` (modify)

**What:** Per-stage models so discovery uses cheaper model than tailoring. *Single-responsibility task — do not touch OpenCode/wizard/doctor.*

1. In `src/applypilot/llm.py`: change `def _detect_provider() -> tuple[str,str,str]` to `def _detect_provider(purpose: str | None = None) -> tuple[str,str,str]`. Add env `LLM_DISCOVERY_MODEL`, `LLM_SCORING_MODEL`, `LLM_TAILOR_MODEL`. When `purpose=="discovery"` and `GEMINI_API_KEY` set and neither `LLM_DISCOVERY_MODEL` nor `LLM_MODEL` set, default model is `"gemini-2.0-flash-lite"` (not `"gemini-3.6-flash"`); otherwise `LLM_DISCOVERY_MODEL` overrides, else inherits `LLM_MODEL`. `LLM_SCORING_MODEL`/`LLM_TAILOR_MODEL` default to `LLM_MODEL` when unset. Memoize second singleton: `_discovery_instance: LLMClient | None = None` and `def get_discovery_client() -> LLMClient` (mirrors `get_client()` at `llm.py:342` but calls `_detect_provider("discovery")` and reads `LLM_RPM_LIMIT`/`LLM_RPM_WINDOW` separately). Ensure `_instance` and `_discovery_instance` are independently reset on `env` change in tests (set to `None` in fixtures).
2. In `src/applypilot/discovery/smartextract.py`: change `from applypilot.llm import get_client` (`:34`) to `from applypilot.llm import get_client, get_discovery_client`; update `ask_llm()` (`:848`) and `judge_api_responses()` candidate path (`:568` `client = get_client()` → `get_discovery_client()`). Keep `scorer.py:18,98`, `tailor.py:21,504,599,644,701`, `cover_letter.py:16,148`, `enrichment/detail.py:28,466` on `get_client()`.
3. In `src/applypilot/config.py:225` add to `DEFAULTS`: `"llm_rpm_limit": 12, "llm_discovery_model": "gemini-2.0-flash-lite"`.
4. In `.env.example` add commented entries:
   ```
   # LLM_RPM_LIMIT=12            # Gemini free tier =15 RPM; 12 keeps headroom (0=disabled)
   # LLM_DISCOVERY_MODEL=gemini-2.0-flash-lite  # cheaper for judge/strategy; tailoring keeps gemini-3.6-flash
   # LLM_SCORING_MODEL=            # override scoring model (defaults to LLM_MODEL)
   # LLM_TAILOR_MODEL=             # override tailoring model (defaults to LLM_MODEL)
   ```

**Acceptance criteria:**
- `GEMINI_API_KEY=key` with no `LLM_MODEL`/`LLM_DISCOVERY_MODEL` → `get_client().model=="gemini-3.6-flash"` and `get_discovery_client().model=="gemini-2.0-flash-lite"` (patch `os.environ`, reset both singletons, assert — add 3 tests in `tests/test_llm.py` adjacent to `TestRPMLimiter`).
- `LLM_DISCOVERY_MODEL=custom` overrides regardless of `LLM_MODEL`.
- `grep -c get_discovery_client src/applypilot/discovery/smartextract.py` == 2 and `grep get_discovery_client src/applypilot/scoring/scorer.py` == 0.
- `.env.example` contains all four commented vars.
- `ruff check src/applypilot/llm.py src/applypilot/discovery/smartextract.py` clean; `PYTHONPATH=src .venv/bin/python -m pytest tests/test_llm.py -v` pass.

**Status:** ✅ Complete (2026-08-28)

---

### Task 6: Integrate OpenCode free models as an LLM provider

**Files:** `src/applypilot/llm.py` (modify), `src/applypilot/cli.py` (modify - doctor only), `src/applypilot/wizard/init.py` (modify), `tests/test_llm.py` (modify)

**What:** First-class OpenCode Zen gateway provider reusing OpenAI-compatible transport. *Depends on Task 5's `_detect_provider(purpose)` signature — implement Task 5 first.*

1. In `src/applypilot/llm.py:_detect_provider(purpose)`: check before Gemini/OpenAI/local. Priority: (a) if `OPENCODE_API_KEY` set → `base_url = os.environ.get("LLM_URL","").rstrip("/") or "https://opencode.ai/zen/v1"`, `model = LLM_MODEL or "opencode/nemotron-3-nano-free"`, `api_key = OPENCODE_API_KEY`; (b) elif `LLM_URL` contains `opencode.ai` → use that `LLM_URL`, same model default, `api_key = OPENCODE_API_KEY or LLM_API_KEY or ""`. Return early. Do not special-case `http://127.0.0.1:4096/v1` separately — existing `if local_url:` block already handles it and must keep priority when `LLM_URL` explicitly points there (verify `OPENCODE_API_KEY` does not override an explicit local URL test).
2. In `src/applypilot/cli.py:doctor()` (`:412`): near LLM key checks (`:481-518`) add `has_opencode = bool(os.environ.get("OPENCODE_API_KEY"))` branch before Gemini/OpenAI. When true, `model = LLM_MODEL or "opencode/nemotron-3-nano-free"`, result label `OpenCode (model)`. Do not require `GEMINI_API_KEY` in this branch. Keep existing `LLM_URL` (Local) branch as fallback.
3. In `src/applypilot/wizard/init.py:_setup_ai_features()` (`:509`): `choices=["gemini","openai","local","opencode"]` (add `opencode`). When `opencode` selected, prompt `OPENCODE_API_KEY` (default `env.get("OPENCODE_API_KEY","")`, mention "get from https://opencode.ai/zen or leave blank for `opencode auth` local"), prompt `LLM_MODEL` default `opencode/nemotron-3-nano-free`, write `OPENCODE_API_KEY=...` + `LLM_MODEL=...` (and optional `LLM_URL` if user chose gateway override). Keep `local` option unchanged (`http://127.0.0.1:4096/v1`).
4. In `tests/test_llm.py:TestDetectProvider` add 4 tests: opencode default model, opencode respects `LLM_MODEL`, opencode via `LLM_URL` containing `opencode.ai`, local `127.0.0.1` not hijacked.

**Acceptance criteria:**
- `OPENCODE_API_KEY=sk-test` alone → `_detect_provider() == ("https://opencode.ai/zen/v1","opencode/nemotron-3-nano-free","sk-test")`; with `LLM_MODEL=custom` → model `custom`.
- `LLM_URL=http://127.0.0.1:4096/v1` + `LLM_MODEL=opencode/nemotron-3-nano-free` → `base_url` that local URL regardless of `GEMINI_API_KEY` (existing local-url test must still pass).
- Mocked `httpx.Client.post` to `https://opencode.ai/zen/v1/chat/completions` returns success via `LLMClient.chat()`.
- `applypilot doctor` with `OPENCODE_API_KEY` shows `OpenCode (model)` line, does not print `MISSING` for Gemini.

**Status:** ❌ Not started

---

### Task 7: Wire new env vars through wizard, doctor, and docs

**Files:** `src/applypilot/wizard/init.py` (modify), `src/applypilot/cli.py` (modify - doctor), `.env.example` (modify), `src/applypilot/config.py` (modify - DEFAULTS already in Task 5 if not yet), `README.md` (modify)

**What:** Surface `LLM_DISCOVERY_MODEL`/`LLM_RPM_LIMIT`/`OPENCODE_API_KEY` in wizard, doctor, and docs. *Requires Tasks 5+6 finalized — implement last.*

1. `src/applypilot/wizard/init.py:_setup_ai_features()` after provider block: prompt `LLM_DISCOVERY_MODEL` (default `"gemini-2.0-flash-lite"` when provider gemini else `LLM_MODEL`, explain "Cheaper model for discovery classification; saves 5× input cost") and `LLM_RPM_LIMIT` (default `"12"`, note "Gemini free =15 RPM; 12 stays safe, 0=disabled"). Write both to `~/.applypilot/.env` (append `LLM_DISCOVERY_MODEL`/`LLM_RPM_LIMIT`). Offer OpenCode path from Task 6.
2. `src/applypilot/cli.py:doctor()` after LLM key block: print `Discovery model: <LLM_DISCOVERY_MODEL or LLM_MODEL or default>` and `RPM limit: <LLM_RPM_LIMIT> (window <LLM_RPM_WINDOW>s)` lines. If Gemini provider, validate `model` *and* `discovery_model` against `GET https://generativelanguage.googleapis.com/v1beta/models?key=...` same pattern as `:488-510`; warn with available models list if not found.
3. `.env.example`: ensure commented `LLM_DISCOVERY_MODEL`, `LLM_RPM_LIMIT`, `LLM_RPM_WINDOW`, `OPENCODE_API_KEY` with explanatory comments.
4. `src/applypilot/config.py:DEFAULTS` — if Task 5 not yet merged, add `llm_rpm_limit`/`llm_discovery_model` here as fallback.
5. `README.md`: after Requirements table (`:104`) or under `## Configuration` (`:117`) add `### Cost & Rate Limits` subsection covering: Gemini free 15 RPM, `LLM_RPM_LIMIT=12`, `LLM_DISCOVERY_MODEL=gemini-2.0-flash-lite` vs `gemini-3.6-flash` for tailoring, `--validation lenient` saves ~1 call/tailor attempt, `opencode/*` free models via `OPENCODE_API_KEY`/`LLM_URL`, `--no-cache` flag.

**Acceptance criteria:**
- Mocked `Prompt.ask` run of `applypilot init` writes `LLM_DISCOVERY_MODEL` and `LLM_RPM_LIMIT` to `.env` on defaults.
- `applypilot doctor` output contains `Discovery model:` and `RPM limit:` lines; with bad discovery model warns with `Available:` list (stub httpx.get in test).
- `.env.example` has all three new vars commented; `README.md` has `### Cost & Rate Limits` mentioning `gemini-2.0-flash-lite`, `LLM_RPM_LIMIT`, `opencode/*`.
- `ruff check src/` and `PYTHONPATH=src .venv/bin/python -m pytest tests/test_init_wizard.py tests/test_llm.py tests/test_config.py -v` pass.

**Status:** ❌ Not started

---

## Implementation Order

```
Task 1 (RPM limiter) ✅ done
  ├─→ Task 2 (heuristic) ✅ ──→ Task 3 (batch judge) ✅ ──→ Task 4 (strategy cache) ✅
  └─→ Task 5 (tiered models) ──→ Task 6 (OpenCode provider)
                                        ↓
                                  Task 7 (wizard/doctor/docs — needs 5+6)
```

1. Task 1 — RPM limiter ✅ (foundation).
2. Task 2 — Heuristic pre-filter ✅.
3. Task 3 — Batched judge ✅.
4. Task 4 — Strategy cache + dedup ✅ — this plan revision marks it complete (code landed in d8ee86b).
5. Task 5 — Tiered model config (next, cheapest cost win; needs RPM limiter).
6. Task 6 — OpenCode provider (alternative zero-cost; depends on Task 5 provider abstraction).
7. Task 7 — Wizard/doctor/docs (requires 5+6 env vars finalized).

Tasks 2 and 5 could parallelize after Task 1 but sequential minimizes `smartextract.py`/`llm.py` merge conflicts.

## Key Design Decisions

1. **Client-side RPM limiter complements reactive 429 backoff** — proactive sliding-window prevents 429 entirely; `LLM_RPM_LIMIT=0` keeps local/OpenAI unthrottled, `12` recommended for Gemini free tier.
2. **Heuristic pre-filter before LLM judge** — captures `recaptcha`/`telemetry` wasting 4 calls/page; deterministic filter is free, zero false negatives when job-key absence checked.
3. **Batch judge into one LLM call** — worst multiplier `N`→1; array output with sequential fallback safe for classification.
4. **Per-domain strategy cache** — searchable sites share DOM across 6 queries; validates `child_tag` shape; `api_response` excluded (per-query data).
5. **`gemini-2.0-flash-lite` as discovery default** — ~5× cheaper input than Flash, sufficient for classification; `gemini-3.6-flash` remains tailoring default for quality.
6. **OpenCode Zen gateway as provider, not separate CLI** — reuses `LLM_URL` OpenAI-compatible path; single `OPENCODE_API_KEY` env var, optional local `http://127.0.0.1:4096/v1`.

## Historical Record

- 2026-08-28 — Plan created. Root cause: 180-270 LLM calls /90-target crawl (1 judge/API +1 strategy +1 Phase2 per site), exceeding Gemini 15 RPM. Proposed 7 tasks.
- 2026-08-28 — Task 1 complete (cca5c7a): `src/applypilot/llm.py` RPM limiter (`_throttle_if_needed`, `_record_request`, env `LLM_RPM_LIMIT`/`LLM_RPM_WINDOW`) + 4 tests.
- 2026-08-28 — Task 2 complete (35a3ace): `smartextract.py:_is_obviously_not_jobs` + heuristic in `judge_api_responses` + 17 tests.
- 2026-08-28 — Task 3 complete (f661e77): `JUDGE_BATCH_PROMPT` + `_format_response_summary` + `_judge_sequential` fallback + 16 tests.
- 2026-08-28 — Task 4 complete (d8ee86b): per-domain strategy cache `(site,domain)` at `CONFIG_DIR/.smartextract_cache.json`, `build_scrape_targets` dedup `seen` set, `run --no-cache` + pipeline plumbing, 18 tests — plan status corrected in this revision (commit message was "Clean up plan queue").
