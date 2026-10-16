# Plan: Preferences cleanup, part 2: more job fields and a role map
**Status:** ✅ Done (2026-10-16)
**Risk:** review (changes the extraction enum)
**Issue:** #46
**Depends on:** prefs-cleanup-1-scoring

## Goal
The job-field list covers almost every field, not just data and engineering jobs. A JSON map lists common roles for each
field, so the user picks target roles (with an "add other" box for anything missing) instead of typing them. Existing
jobs keep their category, so nothing is re-extracted (no LLM cost).

## Context
- `src/strelitzia/enrichment/posting_model.py:20-45`: `EXTRACT_VERSION`, `ROLE_CATEGORIES` (11 values today),
  and the enums used in the extraction schema (`:201`).
- `src/strelitzia/enrichment/posting_prompt.py`: the prompt text that explains each category to Claude.
- `src/strelitzia/scoring/preferences.py:120-130`: `OPTIONS["role_category"]` and labels. `dashboard/src/labels.ts`: UI labels.
- Gotchas: keep every existing category value unchanged (stored rows use them) and only add new ones. Don't bump
  `EXTRACT_VERSION`: a bump marks every job for re-extraction, and the ROADMAP rule says paid bulk runs need the user's go-ahead.
  #43 hit the 16-union schema limit; adding enum values doesn't add unions, but run `tests/test_structured_output.py` to be sure.

## Tasks
### Task 1: expanded categories and the role map
**Runs:** unattended
**Files:** src/strelitzia/enrichment/posting_model.py (modify), src/strelitzia/enrichment/posting_prompt.py (modify), src/strelitzia/config/roles.json (new), tests/test_posting_model.py (modify)
**What:** Add about 20 fields, for example: design_ux, marketing, sales, customer_success, operations, finance_accounting,
consulting, legal, hr_recruiting, healthcare_clinical, education_teaching, science_lab, mechanical_civil_engineering,
manufacturing, supply_chain_logistics, cybersecurity, writing_content, government_public_policy, trades_skilled, hospitality_retail.
`roles.json`: `{ "<category>": ["Role title", ...] }` with 8–20 common titles each, covering every category except "other".
A test checks every category except "other" has roles and that the JSON has no unknown keys.
**Validate:** `pytest tests/test_posting_model.py tests/test_structured_output.py tests/test_posting_prompt.py -q` → passes.
**Status:** ✅ 2026-10-16 (this commit)

### Task 2: serve the map
**Runs:** unattended
**Files:** src/strelitzia/scoring/preferences.py (modify), src/strelitzia/web/app.py (modify), dashboard/src/labels.ts (modify), tests/web/test_preferences_api.py (modify)
**What:** `GET /preferences` adds `options.roles_by_field` from roles.json and labels for the new categories. `target_roles`
stays a list of strings (picked or custom).
**Validate:** `pytest tests/web/test_preferences_api.py -q` → passes.
**Status:** ✅ 2026-10-16 (this commit)

## Validation
- `scripts/validate.sh --full` passes.

## Human test steps (preprod)
None separately (the UI ships in part 3).

## Decisions
1. Target roles are picked from the map, plus custom entries (user, 2026-10-16).
2. Categories are only added, never renamed, so existing extractions stay valid.
