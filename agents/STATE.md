# Current State

**Last updated:** 2026-08-26

## Active Plan: Content Library Resume Tailoring

Plan file: `agents/plans/content_library.md`

### Progress

| Task | Status |
|------|--------|
| Task 1: Content Library Parser | ✅ Complete |
| Task 2: Content Library Tailoring Prompt | ✅ Complete |
| Task 3: Content Library Tailor Function | ✅ Complete |
| Task 4: Validation Updates | ✅ Complete |
| Task 5: CLI & Pipeline Integration | ✅ Complete |
| Task 6: PDF Rendering Update | ✅ Complete |
| Task 7: Batch Entry & End-to-End Test | Pending |

### Current Task

Task 6 completed. Next: Task 7 (Batch Entry & E2E Test).

### Completed This Session

- **Task 6: PDF Rendering Update** — Updated `src/applypilot/scoring/pdf.py` with one-page overflow detection: `render_pdf()` measures content height via Playwright and returns overflow dict; `convert_to_pdf()` returns dict with path and overflow info. Added role-group detection in `build_html()` — entries with role keywords get `role-entry` CSS class for visual distinction. Overflow warnings logged; `page_overflow` flag saved in report JSON. Moved report save after PDF generation so overflow info is included. Updated `run_tailoring()` to capture overflow in result dict. 14 new tests pass (89 total).

### Test Results

```
tests/test_content_library.py — 26 passed
tests/test_content_library_tailor_prompt.py — 16 passed
tests/test_content_library_tailor.py — 19 passed
tests/test_validator_source.py — 14 passed
tests/test_pdf_overflow.py — 14 passed
Total: 89 passed
ruff check — all pre-existing warnings, no new issues
```

### Key Decisions

- Combined `Context/Scope` field maps to `context` (not `scope_scale`) since it's a single value.
- Angle tags are normalized to uppercase; trailing periods/dashes stripped.
- Role dates extracted from parenthetical in role header; project dates from project header.
- Content library prompt includes ALL projects (not a filtered subset) — the LLM selects which to use.
- Prompt uses same JSON output schema as existing tailor prompt for downstream compatibility.
- `tailor_from_content_library()` adds `"source": "content-library"` to the report dict for traceability.
- Judge prompt for content-library mode omits the "preserve companies" rule — the LLM decides which roles/projects are relevant.
- Validation `source` parameter defaults to `"resume"` for backward compatibility.
- Content-library mode relaxes preserved-companies/projects checks but keeps all other validation (fabrication, banned words, sections).
- `--source` flag defaults to `"resume"` so existing `applypilot run tailor` works unchanged.
- CLI checks content library file existence upfront and fails fast with a clear message.
- `source` plumbed through all pipeline paths (sequential, streaming, stage runner) for consistency.
- Overflow detection uses `document.body.scrollHeight * (72/96)` to convert CSS pixels to points (1pt tolerance).
- Role detection uses keyword matching in entry titles (associate, engineer, intern, lead, etc.) — not subtitle presence, since roles have date subtitles.
- Report JSON is saved after PDF generation so `page_overflow` flag is included.
- `render_pdf()` and `convert_to_pdf()` return dicts (not just paths) for backward-compatible extension.

### Blockers

None.

### Recommended Next Step

Implement Task 7: Batch Entry & End-to-End Test — complete the batch entry point for content-library mode, add integration test with mock LLM, verify end-to-end flow.

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
