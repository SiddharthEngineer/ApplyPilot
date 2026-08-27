# Current State

**Last updated:** 2026-08-27 (Workday SSL fix session)

## Active Plan: Fix Workday Scraper SSL Certificate Verification

Plan file: `agents/plans/fix-workday-ssl-cert.md`

### Progress

| Task | Status |
|------|--------|
| Task 1: Add SSL context configuration to workday.py | ✅ Complete |
| Task 2: Add unit test for SSL context configuration | ✅ Complete |
| Task 3: Verify fix with manual integration test | ❌ Not started |

### Current Task

Task 3: Manual integration test against failing employers (pending manual verification)

### Completed This Session

- **Workday SSL Certificate Fix** — Fixed `SSL: CERTIFICATE_VERIFY_FAILED` error when scraping Workday employer portals on macOS. Key changes:
  - `workday.py`: Added `ssl` and `certifi` imports. Created module-level `_ssl_context = ssl.create_default_context(cafile=certifi.where())`. Updated `setup_proxy()` to inject `HTTPSHandler(context=_ssl_context)` into the opener chain. Updated `_urlopen()` to pass `context=_ssl_context` when no proxy is configured.
  - `tests/test_workday_ssl.py`: Created 5 tests verifying SSL context existence, certifi CA bundle loading, CERT_REQUIRED verify mode, proxy setup preservation, and TLS protocol version.
  - Uses existing `certifi` transitive dependency (via `httpx`) — no new dependencies needed.

- **Gemini 404 Scoring Fix** — Fixed job scoring when using `GEMINI_API_KEY` so that LLM calls no longer 404 on the OpenAI-compat endpoint. Key changes:
  - `llm.py`: Extended `_chat_compat()` fallback from 403-only to 400/403/404 for Gemini providers. Updated `_GeminiCompatForbidden` exception to handle all three status codes. Enhanced warning logs to include status code and response body. Updated docstrings and default model from `gemini-2.0-flash` to `gemini-2.5-flash`.
  - `tests/test_llm.py`: Created 15 mocked tests covering gemini 404→native, 400→native, 403→native, native success, fallback persistence, 429 retry, provider detection, and OpenAI 404 no-fallback behavior.
  - `scoring/scorer.py`: Added `httpx.HTTPStatusError` handler in `score_job()` with Gemini-specific hints (check GEMINI_API_KEY, LLM_MODEL). Added systemic failure detection in `run_scoring()` — logs actionable error when all jobs fail with 404/400.
  - `cli.py`: Added doctor model validation — queries Gemini API model list and warns if configured `LLM_MODEL` not found.
  - `.env.example`, `wizard/init.py`: Updated default model references to `gemini-2.5-flash`.

### Test Results (verified 2026-08-27)

```
tests/test_workday_ssl.py: 5 passed ✅
tests/test_jobspy.py: 25 passed ✅
ruff check tests/test_workday_ssl.py: All checks passed ✅
```

### Key Decisions

- **Treat 400/404 like 403 for Gemini only** — all three mean "model not exposed on OpenAI-compat layer"; OpenAI 400/404 must not fallback to avoid masking real errors.
- **Reuse existing native path and sentinel `_GeminiCompatForbidden`** — no new endpoint code, minimal blast radius.
- **Default to `gemini-2.5-flash`** — current GA model (released June 2025, retiring October 2026); `gemini-2.0-flash` may be deprecated or not exposed on compat.
- **Verify model name via live list in doctor** — Gemini model IDs rotate; doctor is the right place for validation feedback.

### Blockers

None.

### Recommended Next Step

Task 3 (manual integration test) requires running `applypilot discover workday --employers manulife,sunlife,desjardins,intact --workers 1` against live Workday endpoints to verify SSL errors are resolved and jobs are returned. This is a manual verification step that cannot be automated in unit tests.

## Project Overview

ApplyPilot v0.3.0 is a 6-stage autonomous job application pipeline. Stage 6 (auto-apply) supports two agent backends:

| Backend | Default | Cost | CLI |
|---------|---------|------|-----|
| `claude` | Yes | Anthropic API | `claude -p` |
| `opencode` | No | Free (own API keys) | `opencode run` |

Users choose via `applypilot apply --backend <claude|opencode>`.

## Key Files

| File | Role |
|------|------|
| `src/applypilot/llm.py` | LLM client with Gemini compat/native fallback |
| `src/applypilot/scoring/content_library.py` | Content library parser |
| `src/applypilot/scoring/tailor.py` | Resume tailoring with LLM + validation + judge |
| `src/applypilot/scoring/validator.py` | Banned words, fabrication detection, structural checks |
| `src/applypilot/scoring/pdf.py` | Text-to-PDF via Playwright |
| `src/applypilot/cli.py` | CLI entry points |
| `src/applypilot/config.py` | Paths, tier system, profile/config loaders |
| `src/applypilot/pipeline.py` | 6-stage pipeline orchestrator |
| `src/applypilot/wizard/init.py` | Interactive setup wizard |
| `src/applypilot/apply/cred_server.py` | MCP credential server (hides passwords from LLM) |
| `src/applypilot/apply/launcher.py` | Apply orchestration: config builders, job execution |
| `src/applypilot/apply/prompt.py` | Prompt builder for the apply agent |
| `src/applypilot/discovery/jobspy.py` | JobSpy job discovery + site fail tracking |
| `tests/test_llm.py` | LLMClient Gemini fallback and provider detection tests |
| `tests/test_jobspy.py` | JobSpy site counting and tracker tests |
| `tests/test_pipeline.py` | Pipeline discover banner tests |
| `tests/test_content_library_e2e.py` | End-to-end integration tests for content-library tailoring |
| `tests/test_init_wizard.py` | Init wizard tests for content library support |
| `tests/test_doctor_content_library.py` | Doctor command content library validation tests |

## Testing

- Run `PYTHONPATH=src .venv/bin/python -m pytest tests/ -v` for unit tests
- Run `ruff check src/` for linting
