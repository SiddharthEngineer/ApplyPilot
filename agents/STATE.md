# Current State

**Last updated:** 2026-08-26

## Active Plan: Hide Passwords from the LLM

Plan file: `agents/plans/hide_passwords.md`

### Progress

| Task | Status |
|------|--------|
| Task 1: Create cred_server.py | ✅ Complete |
| Task 2: Refactor launcher.py | ✅ Complete |
| Task 3: Update prompt.py | ✅ Complete |
| Task 4: Tests, Verification, Documentation | ✅ Complete |

### Current Task

All tasks complete. The Hide Passwords from the LLM plan is fully implemented.

### Completed This Session

- **Hide Passwords from LLM** — Created `src/applypilot/apply/cred_server.py`: a standalone MCP credential server over stdio that reads passwords from env vars, connects to Chrome via CDP, and fills login forms. The LLM calls `ats_login(ats="workday", email="...", cdp_port=9222)` and never sees the password. Refactored `launcher.py`: added `site_passwords` parameter to `_make_mcp_config()` and `_make_opencode_config()`, added `"cred"` MCP server entry with per-ATS env vars, updated `_build_opencode_cmd()` to pipe prompt via stdin (not CLI arg) to prevent `ps aux` leakage, updated `run_job()` to load profile and pass passwords to config builders. Updated `prompt.py`: removed password table from step 5c, replaced with `ats_login` tool call instructions with URL pattern table, removed CapSolver API key from prompt text (replaced with env var reference), added `cdp_port` parameter to `build_prompt()`, removed unused `os` import. Created 42 new tests across 3 test files: `test_cred_server.py` (17 tests for env var reading, tool dispatch, MCP protocol), `test_launcher.py` (15 tests for MCP config, OpenCode config, command builder), `test_prompt.py` (10 tests for no passwords in prompt, ats_login tool, cdp_port interpolation). All 182 tests pass, lint clean.

### Test Results

```
tests/test_cred_server.py — 17 passed
tests/test_launcher.py — 15 passed
tests/test_prompt.py — 10 passed
Total: 182 passed (was 140)
ruff check new files — All checks passed
```

### Key Decisions

- **MCP server over stdio** — No external MCP library needed; JSON-RPC 2.0 implemented with `json` and `sys`. Standalone process launched by MCP config.
- **OpenCode prompt via stdin** — OpenCode doesn't have `--prompt-file`, so both backends now pipe prompt via stdin (matching Claude's approach). Prevents passwords from appearing in `ps aux`.
- **PlaywrightError-specific catches** — CDP connection and form filling catch specific exceptions (`PwError`, `OSError`, `TimeoutError`) instead of bare `Exception`.
- **CapSolver key via env var** — The key is passed to the cred-server process as `CAPSOLVER_API_KEY` env var. The prompt tells the LLM to read it from env via `browser_evaluate`.
- **Backward compatible** — Old profiles with `site_passwords` work unchanged. Passwords flow from `profile.json` → `launcher.py` → MCP server env vars → cred-server process. The LLM never sees them.

### Blockers

None.

### Recommended Next Step

All tasks in the Hide Passwords from the LLM plan are complete. No remaining work.

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
