# Current State

**Last updated:** 2026-08-26

## Active Plan: Content Library Resume Tailoring

Plan file: `agents/plans/content_library.md`

### Progress

| Task | Status |
|------|--------|
| Task 1: Content Library Parser | ✅ Complete |
| Task 2: Content Library Tailoring Prompt | ✅ Complete |
| Task 3: Content Library Tailor Function | Pending |
| Task 4: Validation Updates | Pending |
| Task 5: CLI & Pipeline Integration | Pending |
| Task 6: PDF Rendering Update | Pending |
| Task 7: Batch Entry & End-to-End Test | Pending |

### Current Task

Task 3: Content Library Tailor Function — add `tailor_from_content_library()` in `src/applypilot/scoring/tailor.py`.

### Completed This Session

- **Task 1: Content Library Parser** — Created `src/applypilot/scoring/content_library.py` with `Project`, `RoleSection`, `ContentLibrary` dataclasses and `parse_content_library()` function. All 19 projects from `personal/content_library.md` parse correctly. 26 unit tests pass, linting clean.
- **Task 2: Content Library Tailoring Prompt** — Added `_build_content_library_tailor_prompt(profile, content_library)` to `src/applypilot/scoring/tailor.py`. The prompt formats all projects grouped by role, includes angle tags, skills boundary, banned words, and a 5-step selection process. 16 unit tests pass (13 unit + 3 real-library integration tests).

### Test Results

```
tests/test_content_library.py — 26 passed
tests/test_content_library_tailor_prompt.py — 16 passed
Total: 42 passed
ruff check — all pre-existing warnings, no new issues
```

### Key Decisions

- Combined `Context/Scope` field maps to `context` (not `scope_scale`) since it's a single value.
- Angle tags are normalized to uppercase; trailing periods/dashes stripped.
- Role dates extracted from parenthetical in role header; project dates from project header.
- Content library prompt includes ALL projects (not a filtered subset) — the LLM selects which to use.
- Prompt uses same JSON output schema as existing tailor prompt for downstream compatibility.

### Blockers

None.

### Recommended Next Step

Implement Task 3: Content Library Tailor Function in `src/applypilot/scoring/tailor.py`.

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
| `src/applypilot/scoring/content_library.py` | Content library parser (NEW) |
| `src/applypilot/scoring/tailor.py` | Resume tailoring with LLM + validation + judge |
| `src/applypilot/scoring/validator.py` | Banned words, fabrication detection, structural checks |
| `src/applypilot/scoring/pdf.py` | Text-to-PDF via Playwright |
| `src/applypilot/cli.py` | CLI entry points |
| `src/applypilot/config.py` | Paths, tier system, profile/config loaders |
| `src/applypilot/pipeline.py` | 6-stage pipeline orchestrator |

## Testing

- Run `PYTHONPATH=src .venv/bin/python -m pytest tests/ -v` for unit tests
- Run `ruff check src/` for linting
