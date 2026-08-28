# Current State

**Last updated:** 2026-08-27 (Gemini 3.6-Flash Migration session)

## Active Plan: Migrate Default Gemini Model to 3.6-Flash

Plan file: `agents/plans/gemini-3.6-flash-migration.md`

### Progress

| Task | Status |
|------|--------|
| Task 1: Update core LLM client default model | ✅ Complete |
| Task 2: Update CLI doctor and init wizard defaults | ✅ Complete |
| Task 3: Update documentation, configuration examples, and error hints | ✅ Complete |
| Task 4: Live integration verification | ✅ Complete |

### Current Task

None — Gemini 3.6-Flash migration plan is fully complete.

### Completed This Session

- **Gemini 3.6-Flash Model Migration** — Migrated ApplyPilot's default Gemini model from `gemini-2.5-flash` (retired/404 for new users) to `gemini-3.6-flash`. Key changes:
  - `llm.py`: Updated `_detect_provider()` default model to `gemini-3.6-flash`.
  - `cli.py`: Updated `applypilot doctor` default model validation to `gemini-3.6-flash`.
  - `wizard/init.py`: Updated setup wizard default model prompt to `gemini-3.6-flash`.
  - `scoring/scorer.py`: Updated error log hints to reference `gemini-3.6-flash`.
  - `.env.example`: Updated recommended Gemini model comment.
  - `tests/test_llm.py`: Updated client mock fixture.
  - Live verification: Confirmed `LLMClient.chat()` successfully calls `gemini-3.6-flash` via OpenAI compat endpoint (`HTTP 200 OK`, returning `"PONG"`).

### Test Results (verified 2026-08-27)

```
tests/test_workday_ssl.py: 5 passed ✅
tests/test_jobspy.py: 25 passed ✅
ruff check tests/test_workday_ssl.py: All checks passed ✅

Integration test (live Workday endpoints):
  Manulife: 62 results ✅
  TD Bank: 93 results ✅
  Sun Life: 18 results ✅
  Desjardins: 2 results ✅
  Intact Financial: 28 results ✅
  All without SSL errors ✅
```

### Key Decisions

- **Treat 400/404 like 403 for Gemini only** — all three mean "model not exposed on OpenAI-compat layer"; OpenAI 400/404 must not fallback to avoid masking real errors.
- **Reuse existing native path and sentinel `_GeminiCompatForbidden`** — no new endpoint code, minimal blast radius.
- **Default to `gemini-3.6-flash`** — current GA model; `gemini-2.5-flash` returns 404 for new users (verified via live API in 2026-08-27 session).
- **Verify model name via live list in doctor** — Gemini model IDs rotate; doctor is the right place for validation feedback.

### Blockers

None.

### Recommended Next Step

No remaining work for this plan. All tasks verified and complete.

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
