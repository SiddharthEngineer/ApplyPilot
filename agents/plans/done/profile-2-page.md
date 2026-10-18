# Plan: Profile, part 2: the Profile page and resume parsing
**Status:** ✅ Done 2026-10-16 (PR open)
**Risk:** review (new page, file upload, an LLM call)
**Issue:** #48
**Depends on:** profile-1-store

## Goal
A Profile page (nav: Jobs · Contacts · **Profile** · Preferences · …) where the user fills in who they are: name, phone,
email, LinkedIn, GitHub, location, current title, years of experience, education level and skills, plus their content library
and resume. Uploading a PDF or DOCX resume extracts its text, and Claude (Haiku, about $0.002) parses it into a draft that fills
the empty fields. The user reviews the draft and saves. The page header explains the split: "Profile is about you;
Preferences is about the job you want."

## Context
- `src/strelitzia/profile.py` (part 1): `UserProfile`, `load_profile`, `save_profile`, `save_resume_file`.
- Text extraction: `pypdf` and `python-docx` are already dependencies (`pyproject.toml`). FastAPI uploads need
  `python-multipart`; add it to the `web` extra. Limit uploads to 5 MB, PDF or DOCX only.
- LLM: `src/strelitzia/llm.py` client with a JSON schema (see `src/strelitzia/enrichment/posting_prompt.py` for a
  structured-output prompt to mirror, and `llm_json.py`). Add purpose `profile_parse` to
  `src/strelitzia/config/llm_models.yaml` on Haiku. Call `check_budget(user_id=...)` first (per-user cap).
- `dashboard/src/pages/PreferencesPage.tsx:88-100`: the `Group` component and page layout to mirror. `BlurField`.
- `dashboard/src/App.tsx`: nav and routes. `dashboard/src/api.ts`: `request<T>()` (JSON). For the upload, use `fetch`
  with `FormData` and the `X-Strelitzia: 1` header, as `pasteResult` does at `api.ts:87-95`.
- Gotcha: tests mock the LLM (`tests/conftest.py` blocks network). Never log resume text.

## Tasks
### Task 1: resume parsing
**Runs:** unattended
**Files:** src/strelitzia/scoring/resume_parse.py (new), src/strelitzia/config/llm_models.yaml (modify), tests/test_resume_parse.py (new)
**What:** `extract_text(name, mime, data) -> str` (pypdf/python-docx; ValueError for anything else or empty text).
`parse_resume(text, user_id) -> dict` returns profile fields the resume states (no guesses; omitted fields stay absent), with
`years_experience` as a number and `education_level` from the enum.
**Validate:** `pytest tests/test_resume_parse.py -q` → passes with a fake PDF/DOCX built in the test and a mocked LLM response.
**Status:** ✅ 2026-10-16 (this commit)

### Task 2: profile API
**Runs:** unattended
**Files:** src/strelitzia/web/app.py (modify), pyproject.toml (modify), tests/web/test_profile_api.py (new)
**What:** `GET /app/api/profile` (fields + `has_resume`, `resume_file_name`, content library text, the options for education),
`PUT /app/api/profile` (validate the URLs and email like contacts do), `POST /app/api/profile/resume` (multipart: store file
and text, then return `{draft}` from `parse_resume`, merged only into fields that are empty; nothing else is saved),
`GET /app/api/profile/resume` (download). Saving a changed resume text or content library makes assessments stale, as today.
**Validate:** `pytest tests/web/test_profile_api.py -q` → passes; a 6 MB or .txt upload is a 422; over the cap is a 402.
**Status:** ✅ 2026-10-16 (this commit)

### Task 3: Profile page
**Runs:** unattended
**Files:** dashboard/src/pages/ProfilePage.tsx (new), dashboard/src/App.tsx (modify), dashboard/src/api.ts (modify), dashboard/src/types.ts (modify)
**What:** Groups: About you (names, contact links, location), Experience (current title, years, education, skills as tags),
Resume (upload, current file, "Parsing…", and the draft shown highlighted until Save), Content library (a monospace textarea
and a parse-error message from the API). The page header line from the Goal. Rebuild the bundle into `src/strelitzia/web/static`.
**Validate:** `cd dashboard && npm test -- --run && npm run build` → passes; add `dashboard/src/__tests__/ProfilePage.test.tsx`
covering the draft review (draft values show but aren't saved until Save).
**Status:** ✅ 2026-10-16 (this commit)

## Validation
- `scripts/validate.sh --full` passes.

## Human test steps (preprod)
1. Profile page: fields show the seeded profile. Edit the phone number and save; reload, and it's still there.
2. Upload a PDF resume: preprod has no LLM key, so the text is stored and parsing shows the missing-key error, which is expected.

## Decisions
1. Parsing fills only empty fields as a draft that the user reviews (user, 2026-10-16).
