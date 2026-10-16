# Plan: Profile, part 1: per-user profile, resume and content library in the database
**Status:** ❌ Not started
**Risk:** review (moves the scoring and workbench inputs from files to the DB)
**Issue:** #48
**Depends on:** rescore-all

## Goal
Each user's profile (the fields below), resume text and content library live in the database, so a second user can be
scored and use the workbench. On first start, the owner's existing files in `~/.strelitzia` are imported into the owner's
profile. Scoring and the workbench read the current user's profile instead of the files. Nothing visible changes yet;
part 2 adds the Profile page.

Profile fields (#48): first name, last name, phone, email, LinkedIn URL, GitHub URL, location, current job title, years of
experience in the current field, education level (`EDUCATION_LEVELS` without "unknown"), skills (list of strings),
content library (markdown), resume text, and the uploaded resume file (name, mime type, bytes).

## Context
- `src/strelitzia/config.py:60-125`: `PROFILE_PATH`, `RESUME_PATH`, `CONTENT_LIBRARY_PATH`, `load_profile()`.
  `profile.example.json` shows the file's shape: map `personal.*`, `experience.*`, `skills_boundary.*` into the new fields.
  Keep the rest of profile.json (`resume_facts`, `work_authorization`, ...) and `resume_fixed.yaml` in an `extras` JSON
  column, because the resume template and validator use them.
- `src/strelitzia/scoring/assessment.py:121-128`: `load_resume_text()` (file). `src/strelitzia/scoring/scorer.py:95-115`:
  `load_target_tokens` reads profile.json. `:352-420` `run_scoring(user_id, ...)`.
- `src/strelitzia/scoring/workbench.py:481-499`: `Inputs.load()` (library, base resume, profile, fixed) from files.
- `src/strelitzia/scoring/content_library.py:214-230`: `parse_content_library(path)`; add `parse_content_library_text(text)`
  and make the path version call it.
- `src/strelitzia/scoring/template.py:225-280` and `validator.py`: take the profile dict. Give them a dict built from the
  DB profile in the same shape as profile.json (`UserProfile.as_profile_json()`), so they don't change.
- `src/strelitzia/web/app.py:230-235`: `_workbench_inputs()`.
- Gotchas: the assessment cache key includes the resume text (`current_assessment_key`). Importing the same file text
  keeps existing assessments current. Never put personal data in tests: use fake profiles.

## Tasks
### Task 1: profile store and import
**Runs:** unattended
**Files:** src/strelitzia/profile.py (new), src/strelitzia/database.py (modify), tests/test_profile_store.py (new)
**What:** Table `user_profiles (user_id TEXT PRIMARY KEY, data_json TEXT, resume_text TEXT, content_library_md TEXT,
resume_file BYTEA/BLOB, resume_file_name TEXT, resume_mime TEXT, updated_at TEXT)`. `@dataclass UserProfile`
with the fields above plus `extras: dict`; `load_profile(conn, user_id) -> UserProfile` (empty when missing),
`save_profile(conn, user_id, profile)`, `save_resume_file(conn, user_id, name, mime, data, text)`,
`import_owner_files(conn)` (once, when the owner has no row and the files exist), `UserProfile.as_profile_json() -> dict`.
**Validate:** `pytest tests/test_profile_store.py -q` → passes: round trip, and the import of a fake profile.json/resume/library in tmp_path.
**Status:** ✅ 2026-10-16

### Task 2: scoring reads the DB profile
**Runs:** unattended
**Files:** src/strelitzia/scoring/assessment.py (modify), src/strelitzia/scoring/scorer.py (modify), tests/test_assessment.py (modify)
**What:** `load_resume_text(conn, user_id)` and `load_target_tokens(prefs, profile)` use the DB profile.
`run_scoring` loads both per user. A user with no resume text gets the existing "add your resume" stop.
**Validate:** `pytest tests/test_assessment.py tests/test_scoring_prefilter.py -q` → passes.
**Status:** ❌

### Task 3: workbench reads the DB profile
**Runs:** unattended
**Files:** src/strelitzia/scoring/content_library.py (modify), src/strelitzia/scoring/workbench.py (modify), src/strelitzia/web/app.py (modify), tests/test_content_library.py (modify)
**What:** `Inputs.for_user(conn, user_id)` builds library, base resume, profile dict and fixed from the DB.
`_workbench_inputs(request)` uses it; the 409 says "Add your content library on the Profile page".
**Validate:** `pytest tests/test_content_library.py tests/test_workbench_packet.py tests/test_workbench_import.py tests/web -q` → passes.
**Status:** ❌

## Validation
- `scripts/validate.sh --full` passes.

## Human test steps (preprod)
1. Open in Claude on a seeded job: the packet still builds (the seed profile was imported).

## Decisions
1. Profile data moves into the DB in #48 rather than #47 (user, 2026-10-16).
2. The uploaded resume file is kept in the DB (about 100 KB per user), so the server never needs a per-user file directory.
