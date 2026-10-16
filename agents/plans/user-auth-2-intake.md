# Plan: User accounts, part 2: intake and the task queue per user
**Status:** ❌ Not started
**Risk:** review (rewrites every job-row read/write on the intake path)
**Issue:** #47
**Depends on:** user-auth-1-schema

## Goal
Adding a job, scraping it, extracting the posting and classifying it all act on one user's row, so the same URL added
by two users never touches the other user's row. Dashboard tasks carry the user who queued them. The dashboard still
behaves as the owner (login comes in part 5), so nothing visible changes.

## Context
- `src/strelitzia/intake.py:94-260`: `_row`, `find_existing`, `_same_posting`, `add_job`, `_set`, `save_pasted`, `_score`,
  `run_intake`: every `WHERE url = ?` becomes `WHERE user_id = ? AND url = ?`; `add_job` inserts `user_id`.
- `src/strelitzia/enrichment/detail.py`, `extract.py`, `classify.py`: stage functions read rows (they now carry `user_id`)
  and update them by url. Update by `(user_id, url)` taken from the row they were given. Grep: `grep -n "url = ?" src/strelitzia/enrichment/*.py`.
- `src/strelitzia/web/tasks.py:59-131`: `enqueue(conn, url, kind)`, `assess_task`, `generate_for_job(url, kind)`, `TaskRunner.run_one`.
- `src/strelitzia/web/app.py:476-570`: add-job and paste routes call intake; `:440-450` rescore; `:686-705` reassess_all.
- Until part 5 the routes pass `users.owner(conn).id`. Add one helper in app.py now, `def current_user(request) -> User`,
  returning the owner; part 5 replaces its body. Every route that touches a job calls it.
- Gotcha: `job_by_key` (`app.py:99-107`) must filter on `user_id` too, because job_key is a hash of the url and two users
  share it for the same posting.

## Tasks
### Task 1: intake per user
**Runs:** unattended
**Files:** src/strelitzia/intake.py (modify), tests/test_intake.py (modify)
**What:** `add_job(conn, user_id, url, manual=None)`, `save_pasted(conn, user_id, url, manual)`,
`run_intake(user_id, url, conn=None)`, `find_existing(conn, user_id, url)`; `_same_posting` only matches the same user's rows.
**Validate:** `pytest tests/test_intake.py -q` → passes, including a new test where users A and B add the same URL:
two rows, and B's intake never changes A's row.
**Status:** ❌

### Task 2: enrichment stages update by (user_id, url)
**Runs:** unattended
**Files:** src/strelitzia/enrichment/detail.py (modify), src/strelitzia/enrichment/extract.py (modify), src/strelitzia/enrichment/classify.py (modify), tests/test_posting_extract.py (modify)
**What:** Every UPDATE/SELECT by url also matches `user_id` from the job row. A function that took `urls` also takes `user_id`.
**Validate:** `pytest tests/test_posting_extract.py tests/test_posting_classify.py tests/test_enrich_smoke.py -q` → passes; a
two-user test shows extraction writes only the targeted row.
**Status:** ❌

### Task 3: tasks carry a user
**Runs:** unattended
**Files:** src/strelitzia/web/tasks.py (modify), src/strelitzia/web/app.py (modify), tests/web/test_tasks.py (modify)
**What:** `enqueue(conn, user_id, url, kind)` stores `user_id` (the column exists from part 1), and dedupes per user.
`generate_for_job(user_id, url, kind)`; the runner passes the task's `user_id`. `GET /tasks/{id}` and `/jobs/{key}/tasks` only
return the current user's tasks (404 otherwise). Add `current_user(request)` and use it in all job routes. `assess_task` passes
`user_id` to `run_scoring`, which accepts and ignores it until part 3 (`user_id: str | None = None`).
**Validate:** `pytest tests/web -q` → passes.
**Status:** ❌

## Validation
- `scripts/validate.sh --full` passes.
- `grep -n "url = ?" src/strelitzia/intake.py src/strelitzia/enrichment/*.py | grep -v user_id` → no output.

## Human test steps (preprod)
1. On preprod, Add job with a Greenhouse link: it appears and reaches extracted (or the paste panel), as before.

## Decisions
1. Routes act as the owner until part 5, so this part merges with no login and no visible change.
