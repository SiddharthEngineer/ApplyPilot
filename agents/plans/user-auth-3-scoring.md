# Plan: User accounts, part 3: per-user preferences, scoring and spend caps
**Status:** ✅ Done (2026-10-16)
**Risk:** review (scoring and the LLM budget gate)
**Issue:** #47
**Depends on:** user-auth-2-intake

## Goal
Each user has their own preferences, and their jobs are scored against them. Every Claude call records which user it was
for. Invited users have a monthly spend cap (default $5, editable per user; the owner has none). A user over their cap gets
the same budget-stop error the global budget gives today, and other users are unaffected.

## Context
- `src/strelitzia/scoring/preferences.py:423-443`: `load_preferences(conn)`/`save_preferences(conn, prefs)` use the
  `user_id IS NULL` row. Part 1 assigned that row to the owner.
- `src/strelitzia/scoring/scorer.py:280-420`: `_select_jobs`, `reassess_preview`, `run_scoring(..., urls, reassess)`.
  `:95-115` `load_target_tokens` (profile file; leave the file read for now, #48 moves it).
- `src/strelitzia/scoring/assessment.py`: `current_assessment_key(prefs, resume)`, `recompute_fit(conn, prefs=...)` (all jobs today).
- `src/strelitzia/llm.py:182-260`: `record_usage(..., job_url, conn, row_id)`, `month_spend`, `check_budget`; `:379` the sync call site.
  `src/strelitzia/llm_batch.py:88`: batch results record usage.
- `src/strelitzia/web/app.py`: `budget_stop(conn, estimate)` and the preferences routes `:656-705`.
- Gotcha: a Message Batch can mix jobs from several users only if each result knows its user. Keep it simple: a batch is
  submitted per user (custom_id already maps to the job url; add the user to the batch row or the custom_id).

## Tasks
### Task 1: preferences per user
**Runs:** unattended
**Files:** src/strelitzia/scoring/preferences.py (modify), tests/test_preferences.py (modify)
**What:** `load_preferences(conn, user_id)`, `save_preferences(conn, user_id, prefs)`: upsert on `user_id`.
**Validate:** `pytest tests/test_preferences.py -q` → passes, with two users keeping separate preferences.
**Status:** ✅ 2026-10-16 (already per-user since part 1, keyword `user_id`; test added)

### Task 2: scoring per user
**Runs:** unattended
**Files:** src/strelitzia/scoring/scorer.py (modify), src/strelitzia/scoring/assessment.py (modify), src/strelitzia/web/app.py (modify), tests/test_assessment.py (modify)
**What:** `run_scoring(user_id, ...)`, `_select_jobs(conn, user_id, ...)`, `reassess_preview(conn, user_id, prefs)`,
`recompute_fit(conn, user_id, prefs=None)`: only that user's jobs, scored with that user's preferences. Writes match
`(user_id, url)`. The preferences routes use `current_user(request).id`.
**Validate:** `pytest tests/test_assessment.py tests/test_scoring_prefilter.py tests/web/test_preferences_api.py -q` → passes;
saving user A's preferences re-ranks none of user B's jobs.
**Status:** ✅ 2026-10-16 (also tags `llm_batches` with their user, one batch per user incl. extraction, since collecting a user's open scoring batches needs it; `recompute_fit(conn, user_id=None, urls=None, prefs=None)`, None = the owner)

### Task 3: usage per user and the cap
**Runs:** unattended
**Files:** src/strelitzia/llm.py (modify), src/strelitzia/llm_batch.py (modify), src/strelitzia/users.py (modify), tests/test_llm_usage.py (modify)
**What:** `record_usage(..., user_id=None)` stores it; the LLM client gets a `user_id` attribute set by the scorer/intake
callers. `user_month_spend(conn, user_id) -> float`. `check_budget(estimated_usd=0.0, conn=None, user_id=None)`: the
global budget as today, plus the user's cap from `users.monthly_cap_usd` (None = no cap), raising `LLMStopRun("budget", ...)`
with a message naming the user's cap. `budget_stop` in app.py passes the current user.
**Validate:** `pytest tests/test_llm_usage.py tests/test_llm_stop.py -q` → passes: a user at $5.00 of a $5 cap is stopped;
the owner with the same spend isn't.
**Status:** ✅ 2026-10-16 (the `llm.py` code landed with Task 2; the user is set with the context-local `llm.acting_for(user_id)` (`LLMClient.user_id` reads it) by `run_scoring` and the dashboard task runner; a cap stop carries `LLMStopRun.user_id`, so extraction skips only that user; `users.py` needed no change)

## Validation
- `scripts/validate.sh --full` passes.

## Human test steps (preprod)
1. Preferences page on preprod: change one importance, save; the jobs list re-ranks as before.

## Decisions
1. Default cap $5/month per invited user, owner uncapped (user, 2026-10-16). Scoring stays on the owner's API key.
2. One Message Batch per user, so each result is billed to the right user without a lookup.
