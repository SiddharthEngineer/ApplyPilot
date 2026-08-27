# Current State

**Last updated:** 2026-08-27

## Active Plan: CAPTCHA Solve via cred-server Tool

Plan file: `agents/plans/captcha-solve-tool.md`

### Progress

| Task | Status |
|------|--------|
| Task 1: Add `captcha_solve` tool to cred_server.py | ✅ Complete |
| Task 2: Unit tests for `captcha_solve` | ✅ Complete |
| Task 3: Rewrite CAPTCHA SOLVE prompt section | ✅ Complete |
| Task 4: Update prompt tests | ✅ Complete |

### Current Task

Completed. CAPTCHA Solve via cred-server tool plan is fully implemented.

### Completed This Session

- **CAPTCHA Solve via cred-server tool** — Moved CapSolver API key handling and HTTP calls from the LLM's browser context (broken) to a new `captcha_solve` MCP tool on the cred-server (mirrors existing `ats_login` pattern). Key changes:
  - `cred_server.py`: Added `captcha_solve` tool definition with input schema (captcha_type enum for 5 types, website_url, website_key, page_action, metadata). Added `_get_capsolver_key()` env reader, `CAPTCHA_TYPE_MAP` constant, and `_solve_captcha()` async function with createTask→poll→getTaskResult flow via `_httpx.AsyncClient`. Added dispatch in `_handle_tool_call`. Moved `httpx` import to module level as `_httpx` with `try/except ImportError` for clean testing.
  - `prompt.py`: Rewrote `_build_captcha_section()`: removed broken FIRST/STEP 1/STEP 2 (browser_evaluate createTask/poll with `api.capsolver.com`), replaced with instructions to call `captcha_solve` tool on the cred server. Kept STEP 3 token injection JS. Updated MANUAL FALLBACK to reference `captcha_solve` returns. No more `CAPSOLVER_API_KEY` or `api.capsolver.com` in prompt text.
  - `tests/test_cred_server.py`: Added `TestCaptchaSolve` class with 15 tests covering tool definition, schema, enum values, missing key error, unsupported type, mocked successful hcaptcha/turnstile solves, CapSolver error, key leak prevention, `_get_capsolver_key` env reading, `CAPTCHA_TYPE_MAP` values, and dispatch. Updated `test_tools_list_returns_tools` to expect 2 tools.
  - `tests/test_prompt.py`: Updated `TestCaptchaSection` with 7 tests verifying `captcha_solve` appears, no `api.capsolver.com` in section, no `CAPSOLVER_API_KEY` in section, token injection preserved, manual fallback references tool.

### Test Results

```
212 total tests passed — zero failures
ruff check: 2 pre-existing lint issues in prompt.py (unused var, datetime.now without tz)
```

### Key Decisions

- **Module-level `_httpx` import with try/except** — Importing httpx at module level as `_httpx` (with fallback to `None`) allows clean patching in tests while preserving the `httpx_not_installed` error path.
- **`captcha_solve` returns structured JSON** — Identical shape to `ats_login` (`{"success": bool, "token"|"message": ...}`), making the LLM's MANUAL FALLBACK logic map cleanly.
- **Token injection stays in browser_evaluate** — Only the key-reading and CapSolver HTTP steps moved server-side; token injection correctly runs in the browser via `browser_evaluate`.
- **CapSolver API key never in prompt** — The LLM never sees the key; it calls `captcha_solve` which reads it server-side.

### Blockers

None.

### Recommended Next Step

All tasks in the CAPTCHA Solve via cred-server tool plan are complete. No remaining work.

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
| `tests/test_content_library_e2e.py` | End-to-end integration tests for content-library tailoring |
| `tests/test_init_wizard.py` | Init wizard tests for content library support |
| `tests/test_doctor_content_library.py` | Doctor command content library validation tests |

## Testing

- Run `PYTHONPATH=src .venv/bin/python -m pytest tests/ -v` for unit tests
- Run `ruff check src/` for linting
