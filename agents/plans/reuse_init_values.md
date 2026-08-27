# Plan: Pre-fill Init Wizard Defaults from Existing Config

**Started:** 2026-08-26
**Status:** ✅ Complete

---

## Goal

When `applypilot init` is re-run, pre-fill each prompt with the value previously saved to `~/.applypilot/profile.json`, `~/.applypilot/searches.yaml`, or `~/.applypilot/.env`. Users can press Enter to keep existing values, making reconfiguration much faster.

## Success Criteria

1. Every `Prompt.ask` in `_setup_profile()` defaults to the saved value from `profile.json` (personal info, work auth, compensation, experience, skills, resume facts, availability).
2. `_setup_site_passwords()` pre-fills from saved `site_passwords`.
3. `_setup_searches()` pre-fills location, radius, accept patterns, and role queries from `searches.yaml`.
4. `_setup_ai_features()` detects provider from existing `.env` keys and pre-fills API key/model.
5. `run_wizard()` loads existing configs at startup and passes them to each step.
6. A distinct "ApplyPilot Reconfigure" banner is shown when an existing profile is detected.
7. All existing tests still pass. New tests cover the pre-fill paths.

---

## Design

### Helper functions

Four new helpers in `src/applypilot/wizard/init.py`:

- **`_load_existing_profile()`** — safely loads `profile.json` if it exists, returns `None` on missing/corrupt file.
- **`_load_existing_env()`** — parses `.env` into a `dict[str, str]` of key=value pairs.
- **`_str_to_bool(val, default)`** — converts string/bool values to bool for `Confirm.ask` defaults (handles "Yes"/"No", "true"/"false", bool passthrough).
- **`_join_list(val)`** — joins a list to a comma-separated string for multi-value prompts (skills, companies, etc.).

### Function signature changes

Each setup function gains an optional `existing` parameter (defaults to `None`, backward compatible):

| Function | Parameter type | Source |
|---|---|---|
| `_setup_profile(existing)` | `dict \| None` | parsed `profile.json` |
| `_setup_site_passwords(existing)` | `dict[str, str] \| None` | `profile["site_passwords"]` |
| `_setup_searches(existing)` | `dict \| None` | parsed `searches.yaml` |
| `_setup_ai_features(existing_env)` | `dict[str, str] \| None` | parsed `.env` |

### Default derivation

Inside each function, `ex = existing or {}` provides a safe reference. Each prompt passes `default=...` derived from the saved value:

- **String fields**: `default=personal.get("full_name", "")`
- **Bool fields** (`Confirm.ask`): `default=_str_to_bool(wa.get("legally_authorized_to_work"), True)`
- **List fields** (skills, companies, etc.): `default=_join_list(sb.get("programming_languages", []))`
- **Salary range**: reconstructed from `salary_range_min`/`salary_range_max` as `"80000-100000"`
- **AI provider**: detected from env keys (`GEMINI_API_KEY` → `"gemini"`, etc.)
- **Search queries**: joined from `existing["queries"][*]["query"]`

### API key handling

API keys are pre-filled from existing `.env` but shown as the actual value (not masked) since the terminal is the user's own. If the user clears the field, the existing value is kept via: `if not api_key and existing_key: api_key = existing_key`.

### run_wizard() changes

At the top of `run_wizard()`:
```python
existing_profile = _load_existing_profile()
existing_searches = load_search_config() if SEARCH_CONFIG_PATH.exists() else None
existing_env = _load_existing_env()
```

Each setup function receives its respective existing config. A re-run banner is shown:
```
ApplyPilot Reconfigure
Existing values are pre-filled — press Enter to keep them.
```

---

## Files to modify

| File | Change |
|---|---|
| `src/applypilot/wizard/init.py` | Add 4 helpers. Modify `_setup_profile()`, `_setup_site_passwords()`, `_setup_searches()`, `_setup_ai_features()` signatures and bodies. Update `run_wizard()` to load/pass configs. Add `load_search_config` import. |
| `tests/test_init_wizard.py` | Add tests for helpers (`_str_to_bool`, `_join_list`, `_load_existing_profile`, `_load_existing_env`). Add pre-fill tests for `_setup_site_passwords`, `_setup_profile`, `_setup_searches`, `_setup_ai_features`. |

---

## Implementation Tasks

### Task 1: Add helper functions
- [x] `_load_existing_profile()` — safe JSON load with error handling
- [x] `_load_existing_env()` — .env parser (skip comments, split on `=`)
- [x] `_str_to_bool(val, default)` — string/bool coercion
- [x] `_join_list(val)` — list-to-comma-string

### Task 2: Modify `_setup_site_passwords()`
- [x] Add `existing: dict[str, str] | None = None` parameter
- [x] Use `existing.get(ats_key, "")` as default for each prompt

### Task 3: Modify `_setup_profile()`
- [x] Add `existing: dict | None = None` parameter
- [x] Extract `personal`, `wa`, `comp`, `exp`, `sb`, `rf`, `avail` from existing
- [x] Add `default=...` to all 13 personal info prompts
- [x] Add `default=_str_to_bool(...)` to both `Confirm.ask` calls
- [x] Add `default=...` to work permit, compensation, experience prompts
- [x] Add `default=_join_list(...)` to skills and resume facts prompts
- [x] Reconstruct salary range default from min/max
- [x] Preserve existing EEO defaults
- [x] Pass `existing.get("site_passwords")` to `_setup_site_passwords()`

### Task 4: Modify `_setup_searches()`
- [x] Add `existing: dict | None = None` parameter
- [x] Extract `defaults`, `queries`, `location_accept` from existing
- [x] Pre-fill location, distance, accept patterns, roles

### Task 5: Modify `_setup_ai_features()`
- [x] Add `existing_env: dict[str, str] | None = None` parameter
- [x] Detect provider from existing env keys
- [x] Pre-fill API key (with keep-if-empty fallback), model, URL

### Task 6: Update `run_wizard()`
- [x] Load existing profile, searches, env at startup
- [x] Pass to respective setup functions
- [x] Show "Reconfigure" banner when existing profile detected

### Task 7: Tests
- [x] `_str_to_bool` — true strings, false strings, bool passthrough, unknown, None
- [x] `_join_list` — list, empty list, string passthrough, None
- [x] `_load_existing_profile` — valid, missing, invalid JSON
- [x] `_load_existing_env` — parsed file, missing file
- [x] `_setup_site_passwords` existing passwords used as defaults
- [x] `_setup_profile` personal info prefilled from existing
- [x] `_setup_profile` empty profile allows fresh input
- [x] `_setup_searches` existing config used as defaults
- [x] `_setup_searches` no existing uses fallbacks
- [x] `_setup_ai_features` Gemini key detected
- [x] `_setup_ai_features` OpenAI key detected
- [x] `_setup_ai_features` no existing defaults to gemini

---

## Acceptance criteria

1. ✅ Every `Prompt.ask` in the wizard defaults to the saved value from the corresponding config file.
2. ✅ `Confirm.ask` calls correctly convert saved "Yes"/"No" strings to boolean defaults.
3. ✅ List fields (skills, companies, projects, etc.) are joined back to comma-separated strings for the default.
4. ✅ AI provider is auto-detected from existing `.env` keys.
5. ✅ A "Reconfigure" banner is shown when an existing profile is detected.
6. ✅ First-run experience is unchanged (all defaults are empty/fallback).
7. ✅ All 38 tests pass. Ruff lint and format clean.
