# Plan: Gemini 404 Scoring Fix

**Started:** 2026-08-27
**Status:** 🔄 In Progress

---

## Goal

Fix job scoring (`applypilot run score`) when using `GEMINI_API_KEY` so that LLM calls no longer 404 on `https://generativelanguage.googleapis.com/v1beta/openai/chat/completions` and jobs receive real 1-10 scores instead of `score=0`. Covers 403/404/400 compat-layer "model not exposed" errors with automatic fallback to the native `generateContent` API and updates the default model to a current GA version. Preserve OpenAI/local-LLM paths unchanged. User-facing outcome: `applypilot run score` scores 326 jobs with real distributions instead of all zeros.

## Success Criteria

1. `LLMClient.chat()` with `GEMINI_API_KEY` succeeds for default model `gemini-2.5-flash` (or verified current alias) without manual `LLM_MODEL` override — no 404 after fallback.
2. A Gemini compat `403`, `404`, or `400` (model-not-found) automatically retries via native `POST /v1beta/models/{model}:generateContent?key=` and subsequent calls stay native (`_use_native_gemini` persists).
3. Non-Gemini providers do **not** fallback on 404/400 (OpenAI 404 remains an `HTTPStatusError`).
4. `pytest tests/test_llm.py` (new) passes: mocked 403→native, 404→native, 400→native, native success, fallback persistence, 429 retry, provider detection.
5. Scoring 326 jobs no longer logs `LLM error scoring job` with 404 for every job; `run_scoring()` returns `errors == 0` when LLM is healthy, and gives actionable log (`check GEMINI_API_KEY / LLM_MODEL`) when all jobs fail with 404/400.
6. `applypilot doctor` reflects the new default and warns if `LLM_MODEL` is not in the Gemini model list.
7. `ruff check src/` and `PYTHONPATH=src .venv/bin/python -m pytest tests/ -v` pass.

## Task Chain

### Task 1: Extend Gemini compat fallback to 404 and 400

**Files:** `src/applypilot/llm.py` (modify)

**What:** Generalize the 403-only fallback to cover 404 and 400. In `LLMClient._chat_compat()` (`src/applypilot/llm.py:149-178`) change the sentinel check from `resp.status_code == 403 and _is_gemini` to `resp.status_code in (400, 403, 404) and _is_gemini`. Raise `_GeminiCompatForbidden` for all three. Update `LLMClient.chat()` warning log (`src/applypilot/llm.py:210-227`) to include `status_code` and first 300 chars of body, and ensure `self._use_native_gemini = True` persists. Update docstring on `LLMClient` (`src/applypilot/llm.py:78-85`) to document 400/403/404 fallback. Keep `_chat_native_gemini()` unchanged (`src/applypilot/llm.py:98-145`). Do not alter OpenAI/local paths.

**Acceptance criteria:**
- `get_client()` with `GEMINI_API_KEY` + mocked `httpx.Client.post` returning 404 (or 400) on compat then 200 on native returns text from native.
- Repeated `chat()` calls after first 404/400 go directly to native without hitting compat.
- OpenAI client mocked to 404/400 still raises `HTTPStatusError` (no fallback).
- `ruff check src/applypilot/llm.py` passes.

**Status:** ❌ Not started

### Task 2: Add LLMClient unit tests

**Files:** `tests/test_llm.py` (new)

**What:** Create mocked tests for `LLMClient` using `unittest.mock.patch` on `httpx.Client` and env vars. Cover: (a) gemini compat 404→native success, (b) gemini compat 400→native success, (c) gemini compat 403→native success (existing behavior regression test), (d) gemini compat 404→native also 404 → `RuntimeError` with both bodies, (e) persistence `_use_native_gemini` across calls, (f) openai 404 no fallback, (g) 429 retry on compat (verify existing `_MAX_RETRIES` path still works after change), (h) `_detect_provider()` priority (gemini vs openai vs local). Use `httpx.Response` mocks or stub objects with `.status_code`, `.headers`, `.text`, `.json()`, `.raise_for_status()`.

**Acceptance criteria:**
- `pytest tests/test_llm.py -v` passes with 8+ tests; all use mocks, no network.
- Tests fail before Task 1 and pass after.

**Status:** ❌ Not started

### Task 3: Harden scoring observability

**Files:** `src/applypilot/scoring/scorer.py` (modify)

**What:** Improve `score_job()` (`src/applypilot/scoring/scorer.py:73-101`) error logging to include truncated response body and hint: `HTTP 404 on Gemini compat — check GEMINI_API_KEY, LLM_MODEL (default gemini-2.5-flash), and that model exists on https://ai.google.dev/gemini-api/docs/models`. In `run_scoring()` (`src/applypilot/scoring/scorer.py:104-180`) track consecutive 404s; if `errors == len(jobs)` and first error contains 404/400, log `log.error` summary with remediation and do not silently write all `fit_score=0` (still write but warn). Keep existing `score=0` return for isolated failures to avoid breaking pipeline.

**Acceptance criteria:**
- With injected mock client raising `HTTPStatusError(404)` for every job, `run_scoring()` logs contain `LLM_MODEL` hint and `errors == len(jobs)`.
- Single-job mocked failure still returns `{"score": 0}` without crashing.
- `pytest` tailoring/cover tests unaffected.

**Status:** ❌ Not started

### Task 4: Verify default model and doctor hint

**Files:** `src/applypilot/llm.py` (modify), `src/applypilot/cli.py` (modify, `doctor()`), `src/applypilot/config.py` (modify if needed), `.env.example` (modify), `README.md` (modify if needed)

**What:** Verify current Gemini model name against `https://ai.google.dev/gemini-api/docs/models` (or `generativelanguage.googleapis.com/v1beta/models?key=`). Update default from `gemini-2.0-flash` to `gemini-2.5-flash` (`src/applypilot/llm.py:38`, `src/applypilot/cli.py:474`, `src/applypilot/config.py` references, `.env.example:5`, `README.md:128`). Add a `doctor` check: if `GEMINI_API_KEY` set, try `GET /v1beta/models` listing and warn if configured `LLM_MODEL` not in list. Keep change minimal — single constant + docstring update.

**Acceptance criteria:**
- `applypilot doctor` prints `LLM API key — Gemini (gemini-2.5-flash)` with current default without warning.
- If `LLM_MODEL` is set to nonexistent model, `doctor` warns `model not found in Gemini model list`.
- `README.md` and `.env.example` updated if default changes.
- `ruff check` and `pytest` pass.

**Status:** ❌ Not started

---

## Implementation Order

```
Task 1 (llm fallback 400/403/404) → Task 2 (tests) → Task 3 (scorer observability) → Task 4 (model/doctor)
```

1. Task 1 — unblocks all scoring; small edit to `src/applypilot/llm.py:149-178`.
2. Task 2 — verifies Task 1 with mocks; depends on Task 1.
3. Task 3 — independent of model name, but benefits from Task 1 to test real path.
4. Task 4 — final polish; depends only on Task 1.

## Key Design Decisions

1. **Treat 400/404 like 403 for Gemini only** — all three mean "model not exposed on OpenAI-compat layer" (400 is `model not found`, 403 is preview-gated, 404 is endpoint/model mismatch); OpenAI 400/404 must not fallback to avoid masking real errors.
2. **Reuse existing native path** (`src/applypilot/llm.py:98-145`) and sentinel `_GeminiCompatForbidden` — no new endpoint code, minimal blast radius.
3. **Default to `gemini-2.5-flash`** — current GA model; `gemini-2.0-flash` may be deprecated or not exposed on compat, which is likely the trigger for the 404s in the wild.
4. **Mock-based tests not live API** — avoids needing real `GEMINI_API_KEY` in CI, matches existing `tests/test_content_library_*` mocking of `get_client`.
5. **Keep score=0 for isolated failures** — pipeline should not abort on one bad job description; only warn when 100% failure indicates config error.
6. **Verify model name via live list in doctor, not hardcode** — Gemini model IDs rotate; doctor is the right place for "is this model valid?" feedback.

## Historical Record

- 2026-08-27 — Plan created after investigating `404 Not Found` on `generativelanguage.googleapis.com/v1beta/openai/chat/completions` in job scoring logs (326 jobs, all `score=0`). Inspected `src/applypilot/llm.py`, `src/applypilot/scoring/scorer.py`, git `de13c8c` (403 fallback precedent). User confirmed to include 400 in fallback and update default to `gemini-2.5-flash`.
