# Plan: Preferences cleanup, part 1: qualification rules from the profile, fixed AI weights, red-flag picks
**Status:** ❌ Not started
**Risk:** review (changes how the fit score is computed)
**Issue:** #46
**Depends on:** profile-2-page

## Goal
Education and years of experience stop being preferences. They come from the user's Profile, and the fit score
applies fixed rules to them. Several preference rows are removed. The AI-judged dimensions use fixed weights, and the
user picks which red flags count against a job. Nothing here calls the LLM: saving recomputes the score in code.

Rules (from #46 and the user, 2026-10-16):
- **Education.** Ranks are none < high_school < associate < bachelors < masters < phd; associate is a full step.
  - Meets or exceeds the requirement: 1.0.
  - Exactly one step below: 0.4 (weakens the score).
  - Two or more steps below: dealbreaker.
  - Posting doesn't say, or the profile has no education: 0.5 (unknown), never a dealbreaker.
- **Experience.** `need = years_experience_min`.
  - `have >= need`: 1.0.
  - Close (`need - have <= 1` or `have >= 0.75 * need`): 0.4.
  - Otherwise: dealbreaker.
  - Unknown as above.
- Both count at the "very_important" weight, and their dealbreaker flag is built in (no importance setting).
- **Removed preferences:** `company_blocklist`, `education`, `experience`, plus the importance rows `skills_match`,
  `domain_interest`, `experience_match` and `red_flags`.
- **Fixed weights instead:** skills_match "very_important"; experience_match "important"; domain_interest "important"
  when industries_favor or industries_avoid is set, else not counted.
- **Red flags.** New field `red_flags_counted: list[str]`, a subset of `RED_FLAGS` minus "other"; the default is every
  flag except relocation_required. A posting with any counted flag gets a red_flags component of 0.0 at "important"
  weight; with none, 1.0. Flags still show on the job page either way.

## Context
- `src/strelitzia/scoring/preferences.py:48-75` (`Preferences`), `:108-130` (`LLM_INPUTS`, `LLM_CRITERIA`, `LABELS`,
  `OPTIONS`), `:179-200` (`_VALUE_COERCE`), `:342-365` (`_score_education`, `_score_experience`), `:365-400`
  (`_score_company`, component list), `:405-418` `combine`. `Component` has `key, importance, score, dealbreaker_hit`.
- `src/strelitzia/scoring/assessment.py:38-43` (`RED_FLAGS`, `RATED`), and where it builds the assessment components.
- `src/strelitzia/profile.py` (#48): `load_profile(conn, user_id)` gives `education_level` and `years_experience`.
  `recompute_fit(conn, user_id, prefs)` must load the profile once and pass it to scoring.
- Migration: when stored preferences still have an education or experience value and the user's profile field is empty,
  copy it into the profile, then drop the key. `Preferences.from_dict` already drops unknown keys.
- Saving profile education/years must trigger `recompute_fit` for that user (code only, no LLM).

## Tasks
### Task 1: qualification rules
**Runs:** unattended
**Files:** src/strelitzia/scoring/preferences.py (modify), tests/test_preferences.py (modify)
**What:** `score_qualifications(profile, posting) -> list[Component]` with the rules above. Remove the education and
experience criteria. Table-driven tests: HS vs bachelors is a dealbreaker; associate vs bachelors scores 0.4;
masters vs phd scores 0.4; 3 of 4 years scores 0.4; 6 of 8 years scores 0.4; 2 of 5 years is a dealbreaker; unknown scores 0.5.
**Validate:** `pytest tests/test_preferences.py -q` → passes.
**Status:** ❌

### Task 2: fixed weights, red-flag picks, removed rows
**Runs:** unattended
**Files:** src/strelitzia/scoring/preferences.py (modify), src/strelitzia/scoring/assessment.py (modify), tests/test_assessment.py (modify)
**What:** As in the Goal. `OPTIONS["red_flags"]` lists the pickable flags with labels. `LABELS` loses the removed keys.
**Validate:** `pytest tests/test_assessment.py tests/test_preferences.py -q` → passes.
**Status:** ❌

### Task 3: wire profile into recompute and migrate old values
**Runs:** unattended
**Files:** src/strelitzia/web/app.py (modify), src/strelitzia/profile.py (modify), tests/web/test_preferences_api.py (modify)
**What:** `recompute_fit` gets the profile; `PUT /profile` calls it when education or years change; the one-time move of
old preference values into the profile happens in `load_preferences` (or at startup).
**Validate:** `pytest tests/web -q` → passes.
**Status:** ❌

## Validation
- `scripts/validate.sh --full` passes.

## Human test steps (preprod)
1. Profile: set education to Bachelor's and years to 2. A seeded job that asks for a Master's shows as weakened, not a
   dealbreaker. One that asks for 5+ years shows the dealbreaker badge.

## Decisions
1. The qualification rules run in code, not in the AI prompt: the posting fields are already extracted, so this is free and deterministic.
2. Experience relevance (Claude's 1–5 rating) stays, at a fixed "important" weight (user, 2026-10-16).
