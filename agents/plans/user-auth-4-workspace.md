# Plan: User accounts, part 4: contacts, tracking, workbench and panel per user
**Status:** ❌ Not started
**Risk:** review (data isolation)
**Issue:** #47
**Depends on:** user-auth-3-scoring

## Goal
Contacts, interviews, status history, resume drafts and panel runs belong to one user. No API can read or change another
user's rows: a request for someone else's contact, interview or panel run is a 404.

## Context
- `src/strelitzia/workspace.py:24-190`: `_row(conn, table, id)`, `_insert`, `_update`, contacts and interviews CRUD,
  `link_contact(conn, job_url, contact_id, relation)`, `job_contacts`, `contact_jobs`, `search_contacts`.
- `src/strelitzia/tracking.py:96-150`: `set_status(conn, url, ...)`, `set_submitted_at`, `status_events`.
- `src/strelitzia/scoring/workbench.py`: drafts and packets by job url; `published_static_docs(conn)` reads `app_state`
  keys. Those Drive Doc ids become per user: store them under `"<user_id>:<key>"` (Drive itself becomes per user in part 6).
- `src/strelitzia/scoring/panel.py`: panel runs/items/grades. Runs belong to a user; items reference that user's jobs only.
- `src/strelitzia/web/app.py:192-440, 576-655`: the routes calling these. Use `current_user(request).id` (from part 2).
- Gotcha: `link_contact` must refuse to link user A's contact to user B's job (404).

## Tasks
### Task 1: workspace per user
**Runs:** unattended
**Files:** src/strelitzia/workspace.py (modify), tests/test_workspace.py (modify)
**What:** Every function takes `user_id` right after `conn`; `_row`/`_update`/delete filter on it and raise `NotFound`
for other users' ids; inserts set it.
**Validate:** `pytest tests/test_workspace.py -q` → passes, with cross-user reads, updates, links and deletes raising NotFound.
**Status:** ❌

### Task 2: tracking, workbench and panel per user
**Runs:** unattended
**Files:** src/strelitzia/tracking.py (modify), src/strelitzia/scoring/workbench.py (modify), src/strelitzia/scoring/panel.py (modify), tests/test_panel.py (modify)
**What:** Same pattern: a `user_id` parameter, filtered reads and writes. Panel sampling only picks that user's jobs.
**Validate:** `pytest tests/test_panel.py tests/test_workbench_import.py tests/test_workbench_packet.py tests/web/test_tracking.py -q` → passes.
**Status:** ❌

### Task 3: routes
**Runs:** unattended
**Files:** src/strelitzia/web/app.py (modify), src/strelitzia/web/queries.py (modify), tests/web/test_workspace_api.py (modify)
**What:** Every route passes the current user. `queries.list_jobs`/`facets` take `user_id` and add `jobs.user_id = ?`
to `where_clause`. Add a two-user API test helper in `tests/web/web_helpers.py` (seed a second user and swap
`current_user` with a dependency override or monkeypatch).
**Validate:** `pytest tests/web -q` → passes, including a test that user B's GETs on user A's job, contact and interview are 404.
**Status:** ❌

## Validation
- `scripts/validate.sh --full` passes.

## Human test steps (preprod)
1. Open a job on preprod: contacts, interviews and status history show as before. Add and remove a contact.

## Decisions
1. Cross-user access is a 404, never a 403, so ids don't leak.
