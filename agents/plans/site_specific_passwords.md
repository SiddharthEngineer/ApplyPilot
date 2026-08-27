# Plan: Site-Specific Passwords

**Started:** 2026-08-26
**Status:** ✅ Complete

---

## Goal

Replace the single `personal.password` field with a `site_passwords` dict that maps ATS platform names to credentials. The wizard prompts for passwords per-ATS, and the auto-apply agent uses the correct password for each site.

## Success Criteria

1. `applypilot init` prompts for passwords per-ATS (Workday, Greenhouse, Lever, Ashby) with descriptive labels.
2. Passwords are stored under `site_passwords` in `profile.json`.
3. Old profiles with `personal.password` are migrated automatically on load.
4. The auto-apply prompt includes a site-specific password lookup table.
5. The agent knows to identify the ATS from the URL and use the matching password.
6. All existing tests pass.
7. New tests cover the wizard password prompts and prompt builder changes.

---

## Task Chain

### Task 1: SITE_PASSWORDS Registry & Migration

**Files:** `src/applypilot/config.py` (modify)

**What:** Add a canonical registry of supported ATS platforms and backward-compat migration logic.

**Changes:**
1. Add `SITE_PASSWORDS` dict with ATS names, descriptions, and domain patterns:
   - Workday: `*.myworkdayjobs.com`
   - Greenhouse: `boards.greenhouse.io`
   - Lever: `jobs.lever.co`
   - Ashby: `jobs.ashbyhq.com`
2. Update `load_profile()` to migrate old profiles:
   - If `site_passwords` key is missing and `personal.password` is non-empty, copy it to `site_passwords.workday` (most common use case)
   - Write the migrated profile back to disk

**Acceptance criteria:**
- `SITE_PASSWORDS` dict contains all 4 ATS platforms with descriptions
- `load_profile()` migrates legacy `personal.password` to `site_passwords.workday`
- Migration writes back to disk so subsequent loads don't re-migrate
- Existing profiles with `site_passwords` are not modified

**Status:** ✅ Complete (2026-08-26) — Added `SITE_PASSWORDS` registry dict with ATS names, descriptions, and domain patterns. Added backward-compat migration in `load_profile()` that copies legacy password to `site_passwords.workday` and writes back to disk.

---

### Task 2: Wizard Site-Specific Password Prompts

**Files:** `src/applypilot/wizard/init.py` (modify)

**What:** Update the init wizard to prompt for passwords per-ATS instead of a single password.

**Changes:**
1. Remove the single `password` prompt at line 181
2. Add `_setup_site_passwords()` function that:
   - Shows a panel explaining the purpose
   - For each ATS in `SITE_PASSWORDS`, prompts with description and `password=True`
   - Returns a `dict[str, str]` mapping ATS names to passwords
3. Call `_setup_site_passwords()` from `_setup_profile()` and store result as `profile["site_passwords"]`
4. Import `SITE_PASSWORDS` from config

**Acceptance criteria:**
- Wizard prompts for all 4 ATS platforms with descriptive labels
- Passwords are masked during input (`password=True`)
- Empty passwords are allowed (user doesn't use that ATS)
- Confirmation message shows which passwords were configured

**Status:** ✅ Complete (2026-08-26) — Removed single `password` prompt. Added `_setup_site_passwords()` function with descriptive labels for each ATS. Called from `_setup_profile()`. Imported `SITE_PASSWORDS` from config.

---

### Task 3: Prompt Builder Site-Aware Login Instructions

**Files:** `src/applypilot/apply/prompt.py` (modify)

**What:** Update the auto-apply prompt to include a site-specific password lookup table.

**Changes:**
1. Load `site_passwords` from the profile (with fallback to `personal.password` for backward compat)
2. Build a password lookup table in the prompt
3. Replace the single login instruction with site-aware instructions:
   - Show a table mapping ATS → URL pattern → Email → Password
   - Instruct agent to identify ATS from URL
   - Handle blank passwords (try sign-in anyway)
   - Add `RESULT:FAILED:no_password_configured` for failed sign-in with no password

**Acceptance criteria:**
- Prompt includes a table with all 4 ATS platforms and their credentials
- Agent is instructed to identify ATS from URL pattern
- Blank passwords trigger appropriate fallback behavior
- New result code `RESULT:FAILED:no_password_configured` is documented

**Status:** ✅ Complete (2026-08-26) — Updated `build_prompt()` to load `site_passwords` with fallback to `personal.password`. Added site-specific password lookup table to login instructions. Added `RESULT:FAILED:no_password_configured` result code.

---

### Task 4: Profile Example Update

**Files:** `profile.example.json` (modify)

**What:** Update the example profile to show the new schema with `site_passwords`.

**Changes:**
1. Add `site_passwords` section with placeholder values for all 4 ATS platforms
2. Clear the `personal.password` field (deprecated but kept for backward compat)

**Acceptance criteria:**
- `profile.example.json` includes `site_passwords` section
- `personal.password` is present but empty (deprecation note in design)

**Status:** ✅ Complete (2026-08-26) — Added `site_passwords` section with placeholder values. Cleared `personal.password` field.

---

### Task 5: Tests

**Files:** `tests/test_init_wizard.py` (modify)

**What:** Add tests for the new site passwords wizard step and migration logic.

**Test cases:**
1. **Test wizard prompts for each ATS**: Mock user providing passwords for all 4 platforms, verify correct dict returned
2. **Test all blank passwords**: Mock user leaving all passwords blank, verify empty dict returned
3. **Test partial passwords**: Mock user filling some passwords, verify correct mix
4. **Test migration when site_passwords missing**: Create profile with `personal.password`, verify migration to `site_passwords.workday`
5. **Test no migration when site_passwords exists**: Create profile with existing `site_passwords`, verify preserved
6. **Test migration with empty legacy password**: Verify migration works even when legacy password is empty

**Acceptance criteria:**
- All new tests pass
- Existing init tests still pass
- Migration tests verify both directions (migrate / don't migrate)

**Status:** ✅ Complete (2026-08-26) — Created 6 unit tests in `tests/test_init_wizard.py`: 3 wizard tests (prompts for each ATS, all blank, partial) and 3 migration tests (missing site_passwords, existing site_passwords, empty legacy password). All 117 tests pass.

---

## Implementation Order

```
Task 1 (Registry & Migration) → Task 2 (Wizard) → Task 3 (Prompt Builder) → Task 4 (Example) → Task 5 (Tests)
```

Tasks 1-5: ✅ Complete (2026-08-26)

Each task is a coherent unit of work that can be implemented and verified independently. Task 1 provides the data model and migration. Task 2 updates the user-facing wizard. Task 3 updates the agent prompt. Task 4 updates documentation. Task 5 verifies everything works.

## Key Design Decisions

1. **ATS registry in config.py, not YAML** — The registry is small (4 entries) and static; a dict is simpler than a YAML file.
2. **Migration to workday by default** — Workday is the most common ATS platform, so legacy passwords are migrated there.
3. **Migration writes back to disk** — Prevents repeated migration attempts on every profile load.
4. **Fallback to personal.password** — Belt-and-suspenders: if `site_passwords` is missing, the prompt builder falls back to the legacy field.
5. **Password masking** — All password prompts use `password=True` to prevent shoulder surfing.
6. **New result code** — `RESULT:FAILED:no_password_configured` gives clear feedback when sign-in fails due to missing credentials.

---

## Historical Record

**Phase 1 (Complete):** Site-specific passwords implemented 2026-08-26. Tasks 1-5 complete, 117 tests pass, lint clean (pre-existing issues only). Registry, migration, wizard, prompt builder, example, and tests all working.

The previous plan (Content Library Resume Tailoring, completed 2026-08-26) is preserved in `agents/plans/content_library.md`.
