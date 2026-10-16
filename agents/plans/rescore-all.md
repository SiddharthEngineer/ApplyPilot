# Plan: Rescore all jobs
**Status:** ❌ Not started
**Risk:** review (queues paid LLM work)
**Issue:** #45
**Depends on:** contact-form

## Goal
The Jobs page has a "Rescore all" button. It shows how many active jobs would be re-assessed with Claude and the estimated
cost, then queues the re-assessment on confirm. "Active" means every job whose effective status isn't `rejected`, including jobs whose assessment is current. The Preferences save dialog gets the same option: a checkbox "Re-assess all
active jobs, not only the ones these changes affect".

## Context
- `src/strelitzia/scoring/scorer.py:280-350`: `_select_jobs(conn, user_id, key, limit, rescore, reassess, urls)` and
  `reassess_preview(conn, user_id, prefs)` (count + Batch-price estimate of stale jobs). `:352-420` `run_scoring(user_id, ...)`.
- `src/strelitzia/web/tasks.py:59-115`: `enqueue` and `assess_task`; kind `reassess_all` runs `run_scoring(mode="batch", reassess=True)`.
- `src/strelitzia/web/app.py:680-705`: `POST /preferences/preview` and `PUT /preferences` (`confirm_reassess`).
  `budget_stop(conn, estimate)` (per-user cap since user-auth-3).
- `src/strelitzia/tracking.py:20-62`: `STATUSES` and `status_sql(today)`: filter with `status_sql(...) != 'rejected'`.
  There is no withdrawn or archived status, so rejected is the only one left out.
- `dashboard/src/pages/JobsPage.tsx:60-70`: the list header with `<h1>Jobs</h1>`. `dashboard/src/pages/PreferencesPage.tsx:167-200, 309-325`:
  save and the confirm dialog. `dashboard/src/components/TaskProgress.tsx`: polling a task to show progress.
- Gotcha: jobs that were never extracted can't be assessed. Leave them out of the count and say so ("3 jobs skipped: not extracted yet").

## Tasks
### Task 1: scope "all active"
**Runs:** unattended
**Files:** src/strelitzia/scoring/scorer.py (modify), src/strelitzia/web/tasks.py (modify), tests/test_assessment.py (modify)
**What:** `_select_jobs(..., scope: Literal["stale", "active"] = "stale")`. `rescore_preview(conn, user_id, scope) ->
{jobs, skipped_unextracted, estimated_usd}`. New task kind `rescore_all` → `run_scoring(user_id, mode="batch", scope="active")`.
**Validate:** `pytest tests/test_assessment.py tests/web/test_tasks.py -q` → passes: "active" includes current assessments and
excludes rejected jobs.
**Status:** ❌

### Task 2: API
**Runs:** unattended
**Files:** src/strelitzia/web/app.py (modify), tests/web/test_preferences_api.py (modify)
**What:** `GET /app/api/rescore/preview` → the preview. `POST /app/api/rescore` `{confirm: true}` → `budget_stop(estimate)`,
then enqueue `rescore_all`, returning the task. `PUT /preferences` takes `reassess_scope: "stale" | "active"` (default "stale").
**Validate:** `pytest tests/web -q` → passes; a request over the user's cap returns 402 and queues nothing.
**Status:** ❌

### Task 3: dashboard
**Runs:** unattended
**Files:** dashboard/src/pages/JobsPage.tsx (modify), dashboard/src/pages/PreferencesPage.tsx (modify), dashboard/src/api.ts (modify), dashboard/src/__tests__/JobsTable.test.tsx (modify)
**What:** "Rescore all" button in the Jobs header → dialog "Re-assess N active jobs with Claude, about $X" (and the skipped
count) → confirm → TaskProgress until done, then reload the list. Preferences dialog: the checkbox above, which re-fetches the
preview for the chosen scope. Rebuild the bundle into `src/strelitzia/web/static`.
**Validate:** `cd dashboard && npm test -- --run && npm run build` → passes.
**Status:** ❌

## Validation
- `scripts/validate.sh --full` passes.

## Human test steps (preprod)
1. Jobs → Rescore all: the dialog shows a count and cost. Preprod has no LLM key, so confirming ends in the missing-key
   error, which is expected; check that it shows up as an error.

## Decisions
1. "All" means every job except rejected ones, after a cost confirmation (user, 2026-10-16: "not rejected/withdrawn/archived"; only rejected exists).
