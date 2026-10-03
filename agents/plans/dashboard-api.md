# Plan: Dashboard API (FastAPI)
**Started:** 2026-10-03
**Status:** 🔄 In Progress

## Goal
`applypilot serve` starts a FastAPI app that is the backend of the job dashboard. It lists jobs with filters and
sorting, returns a job's full details, records application status, and runs "generate resume / cover letter" for
a single job in the background. Generation reuses the existing tailor, cover and Drive-sync code. Status uses the
model below. Active and Inactive are derived from the deadline. The other statuses are set by the user.

| Status | Color | Set by |
|---|---|---|
| Active | grey | default: no user status, and the deadline is empty or today or later |
| Inactive | orange | derived: no user status, and the deadline is before today |
| In progress | yellow | a resume/cover generation is started, or the user sets it |
| Submitted | blue | the "Submitted" button; `submitted_at` defaults to now and can be edited. Also set when `applypilot apply` recorded `apply_status='applied'` |
| Rejected | red | button; sets `responded_at` |
| Heard back | green | button; sets `responded_at` |

"No response yet" means Submitted with no `responded_at`. `days_since_submitted` = today − `submitted_at`, and it is shown only while Submitted.

## Success Criteria
1. `pytest tests/web -q` passes offline: FastAPI `TestClient` against a temp SQLite DB with a fake LLM and a fake Drive.
2. `GET /app/api/jobs?status=submitted&role=data_science&work_mode=remote&sort=-fit_score&page=1` returns the right rows, and every filter in the brief has a test.
3. `POST /app/api/jobs/{key}/generate {"resume":true,"cover":true}` returns a task id. The task reaches `done`, the job's
   effective status becomes `in_progress`, and `resume_drive_url`/`drive_folder_url` are set (fake Drive in tests; one live run on the VPS).
4. Without the `X-ApplyPilot: 1` header, mutating endpoints return 403 (CSRF guard behind basic auth).
5. `applypilot serve --port 8765` serves `/app/api/health`, returning `{"ok": true, "db": "postgresql"}` on the VPS.

## Task Chain

### Task 1: Status model and tracking columns
**Runs:** cloud
**Files:** `src/applypilot/tracking.py` (new), `src/applypilot/database.py` (modify: `_ALL_COLUMNS` plus a `status_events` table),
`tests/web/test_tracking.py` (new)
**What:** Add these columns to `jobs`: `user_status TEXT` (NULL|in_progress|submitted|rejected|heard_back), `status_updated_at TEXT`,
`submitted_at TEXT`, `responded_at TEXT`, `notes TEXT`. Add a table `status_events(id TEXT PRIMARY KEY, url TEXT, from_status TEXT,
to_status TEXT, at TEXT, source TEXT)`. The ids are `uuid4().hex`, which is portable and avoids SERIAL vs AUTOINCREMENT. It's created in `init_db` with
`CREATE TABLE IF NOT EXISTS` and has an index on `url`.
```python
STATUSES = ("active", "inactive", "in_progress", "submitted", "rejected", "heard_back")
COLORS = {"active": "grey", "inactive": "orange", "in_progress": "yellow",
          "submitted": "blue", "rejected": "red", "heard_back": "green"}
def effective_status(job: Mapping, today: date) -> str: ...
def days_since_submitted(job: Mapping, today: date) -> int | None: ...
def set_status(conn, url: str, status: str | None, *, submitted_at: str | None = None,
               source: str = "dashboard") -> dict: ...
def status_sql(today: str) -> str:  # SQL CASE expression matching effective_status, for filtering in SQL
```
Precedence: rejected/heard_back > submitted (user_status, or `apply_status='applied'`) > in_progress > inactive (deadline < today) > active.
`set_status("submitted")` sets `submitted_at` (default now, UTC ISO) and clears `responded_at`. Rejected and heard_back set `responded_at`.
`None` resets to the derived status. Every change appends to `status_events`.
**Acceptance:** `pytest tests/web/test_tracking.py -q` passes. A table-driven test checks that `effective_status` and `status_sql` agree on ≥ 12 cases.
**Status:** ❌ Not started

### Task 2: App skeleton and `applypilot serve`
**Runs:** cloud
**Files:** `src/applypilot/web/__init__.py`, `src/applypilot/web/app.py` (new), `src/applypilot/cli.py` (modify),
`pyproject.toml` (modify: extra `web = ["fastapi>=0.115", "uvicorn[standard]>=0.30"]`), `tests/web/conftest.py`, `tests/web/test_app.py` (new)
**What:** `create_app(static_dir: Path | None = None) -> FastAPI`, with every route under the `/app` prefix (nginx proxies
`/app/` unchanged): `/app/api/...` for JSON, and `/app/` plus `/app/assets/*` for the built UI, with an SPA fallback to `index.html`
for unknown `/app/*` paths. `GET /app/api/health` returns `{ok, db: backend_name, version}`. Middleware rejects POST/PATCH/DELETE without the
`X-ApplyPilot: 1` header (403). `applypilot serve --host 127.0.0.1 --port 8765` runs uvicorn with one worker, which the
background task runner needs. The test conftest builds the app on a temp SQLite DB with `APPLYPILOT_DATABASE_URL` unset.
**Acceptance:** `pytest tests/web/test_app.py -q` covers health, the SPA fallback and the CSRF 403.
**Status:** ❌ Not started

### Task 3: Jobs list endpoint with filters, sorting and facets
**Runs:** cloud
**Files:** `src/applypilot/web/queries.py` (new), `src/applypilot/web/app.py` (modify), `tests/web/test_jobs_list.py` (new)
**What:** `GET /app/api/jobs` takes these query parameters:
- `status` (repeatable: active|inactive|in_progress|submitted|rejected|heard_back|no_response|all)
- `role` (repeatable role_category)
- `work_mode` (repeatable)
- `location` (substring over location/location_city/location_state; `remote` matches work_mode=remote)
- `found_from`/`found_to`, `due_from`/`due_to` (YYYY-MM-DD)
- `score_min`/`score_max`
- `q` (title/company search)
- `sort` (one of `discovered_at|deadline|fit_score`, `-` prefix for descending; default `-discovered_at`; NULLs last)
- `page`/`page_size` (≤ 200)

It returns `{total, page, items: [{key, title, company, role_category, location, work_mode, fit_score, discovered_at,
deadline, status, status_color, days_since_submitted}]}`. Build the SQL with a whitelist of sort columns and bound parameters only.
`status` filtering uses `tracking.status_sql(today)`. `key` = `drive_layout.job_key(url)`. Add a `job_key TEXT` column, backfill it
in `ensure_columns`/`init_db`, index it, and set it in `store_jobs`. `GET /app/api/facets` returns the counts per status, role_category
and work_mode, plus the top 30 locations, for the filter dropdowns. Jobs without a `full_description` are still listed.
**Acceptance:** `pytest tests/web/test_jobs_list.py -q` has a test for every filter and sort, NULL ordering, pagination, an injection attempt on
`sort` (rejected with 422), and facet counts.
**Status:** ❌ Not started

### Task 4: Job detail and status endpoints
**Runs:** cloud
**Files:** `src/applypilot/web/app.py` (modify), `tests/web/test_job_detail.py` (new)
**What:**
- `GET /app/api/jobs/{key}` returns every `jobs` column except the large raw fields, which are returned as `description_text`, plus
  `details` (parsed `details_json`, or null), `status`, `status_color`, `days_since_submitted`, `events` (status history, newest first)
  and `links: {posting: url, apply: application_url, drive_folder: drive_folder_url, resume: resume_drive_url, cover_letter: cover_letter_drive_url}`.
- `POST /app/api/jobs/{key}/status {status: "submitted"|"rejected"|"heard_back"|"in_progress"|null, submitted_at?: "YYYY-MM-DD"}`
- `PATCH /app/api/jobs/{key} {submitted_at?, notes?}` for editing the submitted date and notes.

An unknown key returns 404.
**Acceptance:** `pytest tests/web/test_job_detail.py -q` passes, covering detail shape, the submit default date, a manual date edit, the reject/heard-back
transitions and the event log.
**Status:** ❌ Not started

### Task 5: Single-job tailoring and cover letters, and the Drive folder link
**Runs:** cloud
**Files:** `src/applypilot/scoring/tailor.py` (modify `run_tailoring`), `src/applypilot/scoring/cover_letter.py` (modify
`run_cover_letters`), `src/applypilot/storage/sync.py` (modify `sync_job`), `src/applypilot/storage/drive.py` (modify if needed to
return the folder id), `src/applypilot/database.py` (modify: `drive_folder_id TEXT`, `drive_folder_url TEXT`), and tests in
`tests/test_content_library_tailor.py`, `tests/test_drive_sync.py` (modify)
**What:** `run_tailoring(..., urls: list[str] | None = None)` and `run_cover_letters(..., urls=None)`: when `urls` is given,
select exactly those jobs, ignore `min_score` and the "already tailored" filter (regenerate), and keep all other behavior. `sync_job`
records the job's leaf folder (company/role/date) as `drive_folder_id` and `drive_folder_url = https://drive.google.com/drive/folders/<id>`.
**Acceptance:** Existing tailor/cover/drive tests pass. New tests cover `urls=` selecting an unscored job and the folder URL being saved (fake Drive).
**Status:** ❌ Not started

### Task 6: Background generation tasks
**Runs:** cloud (code + tests); local (one live generation on the VPS)
**Files:** `src/applypilot/web/tasks.py` (new), `src/applypilot/database.py` (modify: `dashboard_tasks` table),
`src/applypilot/web/app.py` (modify), `tests/web/test_tasks.py` (new)
**What:** Add a table `dashboard_tasks(id TEXT PRIMARY KEY, url TEXT, kind TEXT, state TEXT, created_at TEXT, started_at TEXT,
finished_at TEXT, error TEXT)`, where kind is resume|cover|both and state is queued|running|done|error. One daemon thread, started on app
startup, takes queued tasks one at a time (LLM quota and Chromium are serialized). On startup, `running` tasks are marked
`error: "interrupted"`. A task runs `run_tailoring(urls=[url])` and/or `run_cover_letters(urls=[url])`, then `sync_job` when Drive is
configured (`storage.drive` credentials present). Otherwise it leaves the local PDFs. Endpoints:
- `POST /app/api/jobs/{key}/generate {resume: bool, cover: bool}` enqueues a task and calls
  `set_status(url, "in_progress", source="generate")` only when the job has no user status.
- `GET /app/api/tasks/{id}` and `GET /app/api/jobs/{key}/tasks` report progress.

`LLMQuotaExhausted` sets `error` to a readable "daily Gemini quota reached" message.
**Acceptance:**
- `pytest tests/web/test_tasks.py -q` passes. With fake tailor/cover/drive, it checks that tasks run in order, that status becomes in_progress,
  that the interrupted recovery works, and that the quota error message appears.
- On the VPS: `applypilot serve`, then `curl -H 'X-ApplyPilot: 1' -X POST .../generate` for one scored job reaches `done`, and the Drive links are set.
**Status:** ❌ Not started

## Implementation Order
```
T1 status model ─→ T2 skeleton ─→ T3 list ─→ T4 detail/status ─→ T6 tasks
                                  T5 single-job tailor + folder link ─┘
```
1. Task 1  2. Task 2  3. Task 3  4. Task 4  5. Task 5  6. Task 6

## Key Design Decisions
1. The API is FastAPI inside the ApplyPilot package, so the dashboard calls tailor, cover and Drive code in-process. It needs no subprocess or second copy of that logic.
2. Active and Inactive are derived at query time from `deadline`, not stored, so they never go stale and need no cron.
3. A job with no stated deadline stays Active. The postings rarely give one; a later "posting closed" liveness check can mark these Inactive.
4. Rows are keyed by `job_key(url)`, the hash already used for Drive `appProperties`, which gives short, stable URLs.
5. A single uvicorn worker runs one background thread with a DB-backed queue: generation is slow and quota-bound, and that's simpler than Celery or Redis.
6. Basic auth at nginx plus a required custom header blocks cross-site form POSTs, because browsers resend basic-auth credentials automatically.
7. `apply_status='applied'` counts as Submitted, so applications made with `applypilot apply` show up.

## Historical Record
- 2026-10-03: Plan created.
