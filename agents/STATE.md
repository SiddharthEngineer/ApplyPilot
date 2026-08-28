# Current State

**Last updated:** 2026-08-28 (LLM rate-limit mitigation Task 1 complete)

## Active Plan

`llm-rate-limit-mitigation.md` — In Progress (Task 1 done, Tasks 2–7 remaining)

### Progress — Discover Crawl Resilience

| Task | Status |
|------|--------|
| Task 1: Normalize & validate `country_indeed` before JobSpy call | ✅ Complete |
| Task 3: Exclude hard scrape errors from consecutive empty counting | ✅ Complete |
| Task 2: Revert wizard default `site_fail_threshold` to 3 | ✅ Complete |
| Task 4: Document board flakiness and auto-skip in README | ✅ Complete |

### Current Task

None — plan complete.

### Progress — ZipRecruiter 403 follow-up (Tasks 5–7)

| Task | Status |
|------|--------|
| Task 5: Wizard writes explicit `sites` + `site_fail_threshold` | ✅ Complete |
| Task 6: Migrate user's live search config (`~/.applypilot/searches.yaml`) | ✅ Complete |
| Task 7: Clarify auto-skip log behavior in README | ✅ Complete |

### Progress — LLM Rate-Limit Mitigation

| Task | Status |
|------|--------|
| Task 1: Add client-side RPM limiter to LLMClient | ✅ Complete |
| Task 2: Heuristic pre-filter for Judge API responses | ❌ Not started |
| Task 3: Batch Judge API responses into a single LLM call | ❌ Not started |
| Task 4: Per-domain strategy cache and target deduplication | ❌ Not started |
| Task 5: Tiered model configuration and cheaper defaults | ❌ Not started |
| Task 6: Integrate OpenCode free models as an LLM provider | ❌ Not started |
| Task 7: Wire new env vars through wizard, doctor, and docs | ❌ Not started |

### Current Task

Task 1 complete. Next: Task 2 (heuristic pre-filter for judge API responses).

### Completed This Session

- **LLM Rate-Limit Mitigation — Task 1: RPM limiter** — added client-side rate limiting to `LLMClient`:
  - `src/applypilot/llm.py`: Added `collections.deque` import, `_rpm_limit`, `_rpm_window`, `_request_timestamps` fields to `LLMClient.__init__()`, `_throttle_if_needed()` method (sliding-window sleep), `_record_request()` method. Updated `chat()` to call throttle before each attempt and record on success. Updated `get_client()` to read `LLM_RPM_LIMIT` (default 0=disabled) and `LLM_RPM_WINDOW` (default 60s) env vars.
  - `tests/test_llm.py`: Added 4 tests in `TestRPMLimiter` — throttle sleep verification (limit=2, 3rd call sleeps ~30s), limit=0 disables throttling, timestamps expire after window, `get_client()` reads env vars. All 19 LLM tests pass.

### Test Results (verified 2026-08-28)

```
tests/test_llm.py: 19 passed ✅ (4 new RPM limiter tests)
tests/test_config.py: 5 passed ✅
tests/test_init_wizard.py: 41 passed ✅
ruff: clean on changed files ✅
```

| Task | Status |
|------|--------|
| Task 1: Change plan worker default model to Nemotron 3.5 Lightning | ✅ Complete |
| Task 2: Fix auto-apply OpenCode default model | ✅ Complete |
| Task 3: Add model fallback list to plan worker | ✅ Complete |
| Task 4: Document model selection | ✅ Complete |

### Current Task

None — both plans complete.

### Completed This Session

- **Discover Crawl Resilience (Tasks 1–4)** — finished the plan:
  - `src/applypilot/discovery/jobspy.py`: Added `_normalize_country()` helper that validates against JobSpy's supported country allowlist; falls back to `"usa"` with a warning for unknown values. Wired into `_run_one_search` and `search_jobs`. Modified `_full_crawl` to skip `tracker.note()` when `result["errors"] > 0` so hard errors don't penalize boards.
  - `src/applypilot/wizard/init.py`: Changed `site_fail_threshold` default from `1` to `3` in generated `searches.yaml`.
  - `tests/test_jobspy.py`: Added 6 `_normalize_country` unit tests + 1 integration test for error-excluded tracker.
  - `tests/test_init_wizard.py`: Updated threshold assertion to `== 3`.
  - `README.md`: Documented board flakiness, auto-skip behavior, and country validation fallback.

### Test Results (verified 2026-08-28)

```
tests/test_init_wizard.py: 41 passed ✅
tests/test_jobspy.py: 32 passed ✅
tests/test_pipeline.py: 9 passed ✅
tests/test_launcher.py: 13 passed ✅
ruff: clean on changed files ✅
```

### Recommended Next Step

Task 2: Heuristic pre-filter for Judge API responses (zero-LLM skip) in `smartextract.py`.

- **Plan completion must be plan-specific.** The global STATE.md "no remaining work" phrase is not a safe completion signal because it is shared across plans. The reliable signal is the plan file's own `Status: ✅ Completed` line, which the implementing agent updates.
- **Wizard and live config exclude `zip_recruiter` rather than lean on the threshold.** Zero requests = zero log noise; `site_fail_threshold: 1` remains as a safety net for any other board that starts returning 0 results.

### Blockers

None.

### Recommended Next Step

No queued plans. Next session can enqueue a new plan, or run the full test suite to reconfirm counts.

### Historical Context — OpenCode Model Selection (prior session)

- **OpenCode Model Selection** — Selected optimal OpenCode models for the two agent use cases and added fallback handling:
  - `scripts/plan_worker.py`: Default model changed from `opencode/mimo-v2.5-free` to `opencode/nemotron-3.5-lightning-free` (NVIDIA execution tier) in the `load_queue()` fallback, `worker_loop()` model lookup, and `show_status()` display. Added a module-level `MODEL_FALLBACKS` ordered list (`[nemotron-3.5-lightning-free, nemotron-3-ultra-free, big-pickle, mimo-v2.5-free]`); on a non-zero `run_agent()` exit the worker retries the same iteration with the next model before counting it as a retry/failure.
  - `agents/plan_queue.json`: `model` field migrated to `opencode/nemotron-3.5-lightning-free`.
  - `src/applypilot/cli.py`: `apply --model` is now backend-aware. `--backend opencode` resolves the default to the valid `opencode/nemotron-3-ultra-free` (single-pass patch rewrite, not Lightning); `--backend claude` keeps `haiku`. An explicit `--model` always wins.
  - `README.md` / `CONTRIBUTING.md`: Documented the OpenCode auto-apply default + override and the plan worker's model default + fallback list.

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

### Historical Context — --auto Flag Restoration (2026-08-28)

- **Restore --auto flag to plan_worker.py** — The `--auto` flag was removed from `scripts/plan_worker.py` by a previous agent, preventing build agents from having all permissions enabled. Restored the flag in `run_agent()` at line 156. This flag is the OpenCode equivalent of Claude Code's `--permission-mode bypassPermissions`, allowing agents to run autonomously without user confirmation. Command structure now matches `launcher.py:_build_opencode_cmd()`.
