# Current State

**Last updated:** 2026-08-28 (OpenCode Model Selection session)

## Active Plan: Select Optimal OpenCode Models for ApplyPilot

Plan file: `agents/plans/opencode-model-selection.md`

### Progress

| Task | Status |
|------|--------|
| Task 1: Change plan worker default model to Nemotron 3.5 Lightning | ✅ Complete |
| Task 2: Fix auto-apply OpenCode default model | ✅ Complete |
| Task 3: Add model fallback list to plan worker | ✅ Complete |
| Task 4: Document model selection | ✅ Complete |

### Current Task

None — OpenCode model selection plan is fully complete.

### Completed This Session

- **OpenCode Model Selection** — Selected optimal OpenCode models for the two agent use cases and added fallback handling:
  - `scripts/plan_worker.py`: Default model changed from `opencode/mimo-v2.5-free` to `opencode/nemotron-3.5-lightning-free` (NVIDIA execution tier) in the `load_queue()` fallback, `worker_loop()` model lookup, and `show_status()` display. Added a module-level `MODEL_FALLBACKS` ordered list (`[nemotron-3.5-lightning-free, nemotron-3-ultra-free, big-pickle, mimo-v2.5-free]`); on a non-zero `run_agent()` exit the worker retries the same iteration with the next model before counting it as a retry/failure.
  - `agents/plan_queue.json`: `model` field migrated to `opencode/nemotron-3.5-lightning-free`.
  - `src/applypilot/cli.py`: `apply --model` is now backend-aware. `--backend opencode` resolves the default to the valid `opencode/nemotron-3-ultra-free` (single-pass patch rewrite, not Lightning); `--backend claude` keeps `haiku`. An explicit `--model` always wins.
  - `README.md` / `CONTRIBUTING.md`: Documented the OpenCode auto-apply default + override and the plan worker's model default + fallback list.

### Test Results (verified 2026-08-28)

```
tests/test_launcher.py: 13 passed ✅
tests/test_config.py, test_prompt.py, test_cred_server.py: 72 passed ✅

plan_worker.py --dry-run: logs Model=opencode/nemotron-3.5-lightning-free ✅
plan_worker.py --status: unchanged output ✅
plan_worker.py fallback logic (mock run_agent):
  fail light → retry ultra → success on ultra ✅
  all 4 models fail → preserves failure, tries all 4 ✅
cli.py model resolution cases (4) all pass ✅
ruff: no NEW violations from changed files (pre-existing violations still present) ✅
```

Note: `tests/test_pipeline.py` and `tests/test_llm.py` were not run to completion because they make live network/LLM calls and hang; they are unaffected by this session's changes.

### Key Decisions

- **Plan worker runs on the execution tier (Lightning)** — the worker *implements* plans; NVIDIA positions Lightning for long-running agents (wins accuracy-speed Pareto, ~30% faster agentic completion, 262K context, less exposure to the 30-min per-run timeout).
- **Auto-apply stays on Ultra, not Lightning** — auto-apply is a single-shot patch rewrite with no iteration loop, so raw single-pass reasoning quality outweighs speed; Ultra (1.0M context) also backs up Lightning on large multi-file plans.
- **Backend-aware default resolution in `cli.py`, not `launcher.py`** — the launcher stays model-agnostic (just forwards `--model`); backend-aware defaults are centralized at the CLI boundary where `backend` is known.
- **Fallback retries within the same iteration** — alternate-model retries do not consume the per-plan retry/iteration budget, so a transient model outage is invisible to completion tracking.
- **Persisted queue `model` migrated once** — a stored queue `model` field overrides code defaults, so it was updated alongside the code change.

### Blockers

None.

### Recommended Next Step

No remaining work for this plan. The next plan in the queue is `agents/plans/ziprecruiter-403-handling.md` (already in the live queue).

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
