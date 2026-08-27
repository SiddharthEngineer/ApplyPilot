# Current State

**Last updated:** 2026-08-27

## Active Plan: ZipRecruiter 403 Handling

Plan file: `agents/plans/ziprecruiter-403-handling.md`

### Progress

| Task | Status |
|------|--------|
| Task 1: Add `_site_counts` + `_SiteTracker` helper + unit tests | ✅ Complete |
| Task 2: Wire tracker through crawl + integration tests | ✅ Complete |
| Task 3: Surface disabled sites in pipeline discover output | ✅ Complete |
| Task 4: Update searches.example.yaml and README.md | ✅ Complete |

### Current Task

Completed. ZipRecruiter 403 Handling plan is fully implemented.

### Completed This Session

- **ZipRecruiter 403 Handling** — Detected and auto-skips boards (e.g. ZipRecruiter) that repeatedly return 0 results during a crawl, preventing log spam and wasted API calls. Key changes:
  - `jobspy.py`: Added `_site_counts(df, requested_sites)` to count DataFrame rows per site. Added `_SiteTracker` dataclass with `active_sites()`, `note()`, and `report()` methods — tracks per-crawl consecutive empty results and disables boards after `site_fail_threshold` (default 3) consecutive 0-result searches. Wired tracker through `_full_crawl`: creates tracker, filters sites via `active_sites()` before each search, calls `note()` after, logs WARNING for newly disabled boards, returns `disabled_sites` and `site_stats`. `_run_one_search` now returns per-site counts in `"sites"` key. `run_discovery` passes through keys and includes them in empty-config early return.
  - `pipeline.py`: `_run_discover` captures `run_discovery()` return value, prints yellow banner when `disabled_sites` is non-empty, sets `stats["jobspy"]` to `"ok (disabled: ...)"`.
  - `searches.example.yaml`: Added `site_fail_threshold: 3` to defaults block.
  - `README.md`: Documented auto-skip behavior and ZipRecruiter Cloudflare 403 block.
  - `tests/test_jobspy.py`: 25 tests — 5 for `_site_counts`, 14 for `_SiteTracker`, 6 integration tests verifying tracker wired through `_full_crawl` and `run_discovery`.
  - `tests/test_pipeline.py`: 4 tests verifying yellow banner, stats update, no banner when empty, error handling.

### Test Results

```
158 tests passed — zero failures (content-library tailoring tests excluded; pre-existing slow test timeout)
ruff check: only pre-existing lint issues (BLE001, DTZ005, F541, I001, SIM113, F841, UP035, UP017)
```

### Key Decisions

- **Disabled state is crawl-scoped, not persisted** — Each `applypilot run discover` starts fresh, so a board that recovers is automatically retried on the next run.
- **Threshold configurable via `defaults.site_fail_threshold`** — Follows existing pattern of pulling tunables from `searches.yaml`'s defaults block, default 3.
- **`_run_one_search` computes per-site counts before location filtering** — Counts reflect raw JobSpy output, not filtered results.
- **Tracker skips disabled boards at crawl level, not per-search** — `active_sites()` is called once per search iteration, filtering the site list passed to `_run_one_search`.

### Blockers

None.

### Recommended Next Step

All tasks in the ZipRecruiter 403 Handling plan are complete. No remaining work.

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
| `src/applypilot/discovery/jobspy.py` | JobSpy job discovery + site fail tracking |
| `tests/test_jobspy.py` | JobSpy site counting and tracker tests |
| `tests/test_pipeline.py` | Pipeline discover banner tests |
| `tests/test_content_library_e2e.py` | End-to-end integration tests for content-library tailoring |
| `tests/test_init_wizard.py` | Init wizard tests for content library support |
| `tests/test_doctor_content_library.py` | Doctor command content library validation tests |

## Testing

- Run `PYTHONPATH=src .venv/bin/python -m pytest tests/ -v` for unit tests
- Run `ruff check src/` for linting
