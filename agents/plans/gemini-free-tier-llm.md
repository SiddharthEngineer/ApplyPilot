# Plan: Run the Pipeline Within Gemini's Free Tier
**Started:** 2026-10-01
**Status:** 🔄 In Progress

## Goal
All LLM stages stay on Google Gemini's **free tier**. The user chose this over routing pipeline calls
through a Claude subscription (2026-10-01). Verified 2026-10-01 with the user's key: both the
OpenAI-compat and native endpoints return 200 for `gemini-3.6-flash` and `gemini-3.1-flash-lite`.
The key also lists `gemini-3.5-flash-lite`, `gemini-3.7-flash`, and `gemini-3.8-flash`. The constraint
is quota, not money: free-tier limits are per model, per minute and **per day**. Today:
- A daily-quota 429 is retried 5× with backoff like a transient 429, which wastes time.
- The `LLM_SCORING_MODEL`/`LLM_TAILOR_MODEL` vars are documented in `.env.example` but **never read** (only `purpose="discovery"` is wired), so scoring can't use the higher-quota lite model.
- JSON is parsed out of free text, so malformed output costs extra retries, and each retry is another quota request.

After this plan, each stage uses the right model for its quota, a run stops cleanly when a model's
daily quota is exhausted (and resumes the next day), structured JSON output cuts retries, and cheap
pre-filters keep obviously irrelevant jobs from using LLM calls at all.

## Success Criteria
1. `LLM_SCORING_MODEL=gemini-3.1-flash-lite` makes only the scoring client use that model (unit test), and likewise for `LLM_TAILOR_MODEL` and `LLM_COVER_MODEL`.
2. A simulated daily-quota 429 (`RESOURCE_EXHAUSTED` with a per-day quota id) makes the stage stop after **one** request, log `"Gemini daily quota exhausted for <model>"`, and leave remaining jobs untouched (unit test).
3. Scoring and tailoring requests use `responseMimeType: application/json` with a schema. Over 20 live scored jobs, there are 0 JSON parse retries (logged count).
4. `applypilot run score` skips jobs whose title matches none of the search queries or target titles, without an LLM call, and logs the skipped count.
5. `applypilot doctor` prints the model per stage and the free-tier limits from `docs` (see Task 5).
6. `pytest tests/ -q` passes. `pytest -m llm --run-llm tests/test_llm.py` passes on the free key.

## Task Chain

### Task 1: Per-stage model routing
**Runs:** cloud
**Files:** `src/applypilot/llm.py` (modify), `src/applypilot/scoring/scorer.py` (modify), `src/applypilot/scoring/tailor.py` (modify), `src/applypilot/scoring/cover_letter.py` (modify), `tests/test_llm.py` (modify)
**What:** Generalize `_detect_provider(purpose)` so that for every purpose in
`{"discovery","scoring","tailor","cover"}` the model resolves as `LLM_{PURPOSE}_MODEL` → `LLM_MODEL` → provider
default. Keep the existing discovery default (`gemini-3.1-flash-lite`). Replace the two singletons with
`get_client(purpose: str = "default")` backed by a `dict[str, LLMClient]`, and keep `get_discovery_client()`
as an alias for `get_client("discovery")`. Callers become `get_client("scoring")`,
`get_client("tailor")`, and `get_client("cover")`. **All purposes share one RPM limiter per model**, so
two clients on the same model don't double the effective rate (module-level `dict[str, deque]` keyed by model).
**Acceptance:**
- `grep -n 'get_client("scoring")' src/applypilot/scoring/scorer.py` matches, and likewise for tailor/cover.
- New tests for Success Criterion 1 and the shared limiter. Existing `tests/test_llm.py` still passes.
**Status:** ✅ Complete (2026-10-02)

### Task 2: Daily-quota detection and clean stop
**Runs:** cloud
**Files:** `src/applypilot/llm.py` (modify), `src/applypilot/scoring/scorer.py` (modify), `src/applypilot/scoring/tailor.py` (modify), `src/applypilot/scoring/cover_letter.py` (modify), `src/applypilot/discovery/smartextract.py` (modify), `tests/test_llm.py` (modify)
**What:** Add `class LLMQuotaExhausted(RuntimeError)` with fields `model: str` and `scope: str`. In
`LLMClient.chat()`, on HTTP 429, parse the Gemini error body (`error.status == "RESOURCE_EXHAUSTED"`, and
`error.details[].violations[].quotaId` containing `PerDay`). For a per-day quota, raise
`LLMQuotaExhausted` immediately instead of retrying. Per-minute 429s keep the existing backoff and honor
the `retryDelay` value in the error details, if present. In each stage loop, catch `LLMQuotaExhausted`,
stop the stage, and return stats with `"stopped": "daily_quota"` without recording an attempt on the
current job. This depends on scoring-error-recovery Task 1, so an aborted job stays NULL.
**Acceptance:**
- Success Criterion 2. Also a test that a per-minute 429 still retries with backoff (patch `time.sleep`).
**Status:** ✅ Complete (2026-10-02)

### Task 3: Structured JSON output for Gemini
**Runs:** cloud (code + unit tests); live parse-retry count: local
**Files:** `src/applypilot/llm.py` (modify), `src/applypilot/scoring/scorer.py` (modify), `src/applypilot/scoring/tailor.py` (modify), `tests/test_llm.py` (modify)
**What:** Add an optional `response_schema: dict | None = None` argument to `chat()`. When the client is
Gemini, send it via the native API's `generationConfig.responseMimeType = "application/json"` and
`responseSchema` (native path; switch to native for schema calls even if compat works). Other providers
ignore it and keep text parsing. Scoring passes `{score:int, keywords:string, reasoning:string}`.
Tailoring passes the resume JSON schema (resume-template-tailoring Task 4 will replace it with the
`TailoredResume` schema). Log a counter of JSON-parse retries per stage at the end of the run.
**Acceptance:**
- Unit test: a Gemini client with a schema sends `responseSchema` to the native URL.
- Success Criterion 3 verified live and recorded in Historical Record.
**Status:** ❌ Not started

### Task 4: Pre-filter jobs before scoring
**Runs:** cloud
**Files:** `src/applypilot/scoring/scorer.py` (modify), `src/applypilot/database.py` (modify), `tests/test_scoring_prefilter.py` (new)
**What:** Before calling the LLM, compute a cheap relevance check. The job title must share at least one
significant token (lower-cased, stop-words removed) with any `queries[].query` in `searches.yaml` or
`profile.json` target titles. Non-matching jobs get `fit_score = 1` and
`score_reasoning = "prefilter: title not relevant"`, with no LLM call. Add `--no-prefilter` to `run score`
to bypass it. Log `"prefilter skipped N/M jobs"`.
**Acceptance:**
- Tests: "Senior Data Engineer" passes for query "Data Engineer"; "Registered Nurse" is filtered; `--no-prefilter` scores both.
**Status:** ❌ Not started

### Task 5: Model choice + doctor + docs
**Runs:** local (quota lookup, user `.env`, doctor run)
**Files:** `src/applypilot/cli.py` (modify), `.env.example` (modify), `README.md` (modify), `~/.applypilot/.env` (modify, user data)
**What:** Look up the current free-tier per-model limits (RPM and requests/day) on the official Gemini
rate-limits page, and pick per-stage defaults. Use a **lite** model for discovery and scoring (highest
daily quota, highest volume) and the best flash model the key lists for tailoring and cover letters
(lowest volume, quality matters). Note the chosen models and quotas with the date in this plan's
Historical Record. In `doctor()`, print the model per stage. In README "Cost & Rate Limits", replace the
hardcoded "15 RPM" with a pointer to the official page and the per-stage env vars. Update the user's `.env`
with the chosen `LLM_*_MODEL` values and an `LLM_RPM_LIMIT` matching the scoring model's free RPM.
Add a README note that the Gemini free tier may use submitted content (resume, job descriptions) to improve
Google's products, per Google's terms. Mention the paid tier as the opt-out.
**Acceptance:**
- `applypilot doctor` shows 4 per-stage model lines.
- Historical Record lists models + quotas + date.
**Status:** ❌ Not started

## Implementation Order
```
Task 1 ──► Task 2 ──► Task 3 ──► Task 5
Task 4 (independent)
```
1. Task 1: per-stage routing (prerequisite for using the right model per quota)
2. Task 2: quota-aware stop (needs scoring-error-recovery Task 1)
3. Task 3: structured output
4. Task 4: pre-filter
5. Task 5: pick models, doctor, docs, user config

## Key Design Decisions
1. Stay on the Gemini free tier, per the user's 2026-10-01 decision. The Claude subscription isn't used for pipeline LLM calls.
2. Treat a daily-quota 429 as a stop signal, not a retry, because no backoff inside one run can recover a per-day limit.
3. Share one RPM limiter per model across purposes, so per-stage clients can't jointly exceed a model's per-minute limit.
4. Use native-API structured output because every malformed-JSON retry costs a request from a small daily budget.
5. Use a title pre-filter because scoring is the highest-volume stage, and many discovered jobs are clearly off-target.
6. Don't hardcode quota numbers in code. Google changes them, so they live in docs and the user's `.env`.

## Historical Record
- 2026-10-01: Plan created, replacing `claude-llm-provider` (roadmap initiative R4) after the user chose the Gemini free tier. Verified compat + native 200 for gemini-3.6-flash and gemini-3.1-flash-lite.
- 2026-10-02: Task 1 done. `get_client(purpose)` backed by `_clients` dict; `LLM_{PURPOSE}_MODEL` → `LLM_MODEL` → default for every provider; RPM window shared per model via `_rpm_timestamps`; `reset_clients()` replaces the `_instance`/`_discovery_instance` resets in tests. `enrichment/detail.py` and `scripts/capture_fixtures.py` stay on `get_client()` ("default").
- 2026-10-02: Task 2 done. `LLMQuotaExhausted(model, scope)`; `_parse_quota_error` reads native `{"error":…}` and compat `[{"error":…}]` bodies; per-minute 429s wait `retryDelay`+1s (cap 90s). Scoring/tailor/cover break out of their loops with `"stopped": "daily_quota"` and record no attempt on the aborted job. SmartExtract re-raises through the judge/Phase 1/Phase 2/exec handlers and marks the site `fatal` (parallel mode cancels pending sites). Beyond the plan: `pipeline.py` shows `stopped: daily quota` (yellow, not an error) and streaming mode no longer re-runs a stopped stage, which would have spent one request per poll. Tests: `tests/test_llm.py::TestDailyQuota`, `tests/test_quota_stop.py`.
