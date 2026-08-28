# Current State

**Last updated:** 2026-08-28 (LLM rate-limit mitigation Task 4 complete — plan corrected, d8ee86b)

## Active Plan

`llm-rate-limit-mitigation.md` — In Progress (Tasks 1–4 done, Tasks 5–7 remaining)

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
| Task 2: Heuristic pre-filter for Judge API responses | ✅ Complete |
| Task 3: Batch Judge API responses into a single LLM call | ✅ Complete |
| Task 4: Per-domain strategy cache and target deduplication | ✅ Complete |
| Task 5: Tiered model configuration and cheaper defaults | ❌ Not started |
| Task 6: Integrate OpenCode free models as an LLM provider | ❌ Not started |
| Task 7: Wire new env vars through wizard, doctor, and docs | ❌ Not started |

### Current Task

Task 4 complete (code landed in d8ee86b, plan status corrected). Next: Task 5 (tiered model configuration and cheaper defaults).

### Completed This Session

- **LLM Rate-Limit Mitigation — Plan correction + Task 4 reconciliation** — Audited `llm-rate-limit-mitigation.md` against `HEAD` and `git log`; rewrote plan to unblock BigPickle:
  - Task 1: corrected defaults (`LLM_RPM_LIMIT=0` disabled vs plan's stale `12`), pinned line refs `src/applypilot/llm.py:88-110,342`.
  - Task 2/3: pinned exact refs `smartextract.py:385-425,449-614` and test paths.
  - Task 4: marked ✅ Complete — code already in `d8ee86b` (`smartextract.py:49-91` cache, `cli.py:112` --no-cache, `pipeline.py:62,294`, 18 tests at `tests/test_smartextract_cache.py`) but plan still said ❌ Not started; corrected cache path `CONFIG_DIR/.smartextract_cache.json`, noted `api_response` not cached.
  - Tasks 5-7: expanded to single-responsibility steps with explicit `DEFAULTS` keys, `get_discovery_client()` singleton, `OPENCODE_API_KEY` priority, and doctor/wizard wiring to prevent implementation guesswork.
- **LLM Rate-Limit Mitigation — Task 4: Per-domain strategy cache and target deduplication** — (landed in `d8ee86b` as "Clean up plan queue"):
  - `src/applypilot/discovery/smartextract.py`: `_strategy_cache`, `_CACHE_FILE`, `_get_cache_key()`, `_load/_save_strategy_cache()`, `_run_one_site()` cache hit/miss + CAPTCHA/shape invalidation, `build_scrape_targets()` `seen` dedup. `api_response` intentionally not cached.
  - `src/applypilot/cli.py` + `src/applypilot/pipeline.py`: `--no-cache` flag plumbing.
  - `tests/test_smartextract_cache.py`: 18 tests (5 dedup, 2 key, 3 persistence, 8 cache-hit/miss/no-cache/captcha).
- **LLM Rate-Limit Mitigation — Task 3: Batch judge** — Collapsed per-response judge loop into a single batched LLM call with fallback:
  - `src/applypilot/discovery/smartextract.py`: Added `JUDGE_BATCH_PROMPT` that lists all candidates numbered `[1]..[N]` and asks the LLM to return a JSON array of verdicts. Added `_format_response_summary()` helper that formats each response (url, status, size, type, fields, sample truncated to 300 chars). Extracted `_judge_sequential()` for fallback. Refactored `judge_api_responses()`: with >1 candidate, builds a batched prompt and makes 1 LLM call; parses the array response and maps index→verdict; falls back to sequential if batch parsing fails (invalid JSON, non-list response, missing verdicts). Single candidate still uses sequential path directly. 16 new tests in `tests/test_smartextract_batch_judge.py`: 4 summary formatter tests, 5 happy-path batch tests (5 responses→1 call, prompt content, all-relevant, all-irrelevant, single-candidate sequential), 4 fallback tests (invalid JSON, missing verdicts, non-list response, error handling), 3 integration tests (heuristic+batch combined, prompt size budget, empty-after-heuristic). All 33 tests (17 heuristic + 16 batch) pass, ruff clean.

### Test Results (verified 2026-08-28)

```
tests/test_smartextract_heuristic.py: 17 passed ✅
tests/test_smartextract_batch_judge.py: 16 passed ✅
tests/test_smartextract_cache.py: 18 passed ✅ (Task 4 — previously uncounted)
tests/test_llm.py: 19 passed ✅
tests/test_config.py: 5 passed ✅
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
