# Current State

**Last updated:** 2026-08-27

## Active Plan: Plan Queue Worker

Plan file: `agents/plan_queue.json`

### Progress

| Task | Status |
|------|--------|
| Task 1: Create plan_queue.json | ✅ Complete |
| Task 2: Create scripts/plan_worker.py | ✅ Complete |
| Task 3: CLI helpers (--enqueue/--dequeue/--status/--dry-run) | ✅ Complete |
| Task 4: Test dry-run and status commands | ✅ Complete |

### Current Task

Completed. Plan queue worker is ready for use.

### Completed This Session

- **Plan Queue Worker** — Created `scripts/plan_worker.py`: a reusable orchestrator that continuously implements plans from `agents/plan_queue.json`. Reads the top plan from the queue, launches an opencode agent session via `opencode run --auto --model <model>`, checks for completion by inspecting STATE.md and plan file status markers, dequeues completed plans, and immediately starts the next. Supports max iterations per plan (default 20), retry on failure (up to 2 retries), 30-minute hard timeout per run, and structured logging to `plan_worker.log`. CLI flags: `--enqueue PATH`, `--dequeue PATH`, `--status`, `--dry-run`. Moved `plan_queue.json` to `agents/` folder. Added `plan_worker.log` to `.gitignore`.

### Test Results

```
scripts/plan_worker.py --status — Shows queue correctly
scripts/plan_worker.py --dry-run — Prompt preview correct, no execution
scripts/plan_worker.py --enqueue/--dequeue — Queue manipulation works
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
