# Current State

**Last updated:** 2026-08-26

## Recent: Fix Wizard Location Config

### Completed This Session

- **Wizard location filtering** — Updated `_setup_searches()` in `wizard/init.py` to prompt for multiple location accept patterns (comma-separated) and write `location_accept:` to `searches.yaml`. This fixes the root cause where all non-remote jobs were silently rejected because `location_accept` was empty.
- **Example YAML schema fix** — Updated `config/searches.example.yaml` to use flat keys (`location_accept`, `sites`) that match what `jobspy.py` actually reads. The old nested `location.accept_patterns` and `boards` keys were incompatible with the code.

### Key Decisions

- Default location patterns: `"{location}, Remote, US"` — covers the common case.
- The `location_accept` key is a flat top-level list in YAML (not nested under `location:`), matching `jobspy.py:_load_location_config()`.
- `boards` renamed to `sites` in example YAML to match `jobspy.py:464`.

### Test Results

```
ruff check — All checks passed
Total: 117 passed
```

## Active Plan: Site-Specific Passwords

Plan file: `agents/plans/site_specific_passwords.md`

### Progress

| Task | Status |
|------|--------|
| SITE_PASSWORDS registry in config.py | ✅ Complete |
| Backward-compat migration in load_profile() | ✅ Complete |
| Wizard site-specific password prompts | ✅ Complete |
| Prompt builder site-aware login instructions | ✅ Complete |
| profile.example.json update | ✅ Complete |
| Tests for wizard and migration | ✅ Complete |

### Current Task

All tasks complete. The Site-Specific Passwords plan is fully implemented.

### Completed This Session

- **Site-Specific Passwords** — Replaced single `personal.password` field with `site_passwords` dict mapping ATS platform names (workday, greenhouse, lever, ashby) to credentials. Added `SITE_PASSWORDS` registry in `config.py` with ATS descriptions and domain patterns. Added backward-compat migration in `load_profile()` that migrates old `personal.password` to `site_passwords.workday`. Updated wizard to prompt per-ATS with descriptive labels. Updated prompt builder to include site-specific password lookup table. Updated `profile.example.json`. Created 6 new tests (3 wizard, 3 migration). All 117 tests pass.

### Test Results

```
tests/test_init_wizard.py — 15 passed (was 9)
Total: 117 passed
ruff check — pre-existing issues only (not introduced by this change)
```

### Key Decisions

- `SITE_PASSWORDS` is a module-level dict in config.py, not a YAML file — the registry is small and static.
- Migration copies legacy password to `site_passwords.workday` since Workday is the most common ATS.
- Migration writes back to disk immediately so subsequent loads don't re-migrate.
- Prompt builder falls back to `personal.password` if `site_passwords` is missing (belt-and-suspenders).
- Password prompts use `password=True` to mask input in the terminal.
- Added `RESULT:FAILED:no_password_configured` result code for when sign-in fails and no password is configured.

### Blockers

None.

### Recommended Next Step

All tasks in the Site-Specific Passwords plan are complete. No remaining work.

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
| `tests/test_content_library_e2e.py` | End-to-end integration tests for content-library tailoring |
| `tests/test_init_wizard.py` | Init wizard tests for content library support |
| `tests/test_doctor_content_library.py` | Doctor command content library validation tests |

## Testing

- Run `PYTHONPATH=src .venv/bin/python -m pytest tests/ -v` for unit tests
- Run `ruff check src/` for linting
