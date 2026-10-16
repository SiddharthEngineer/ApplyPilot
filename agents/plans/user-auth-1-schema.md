# Plan: User accounts, part 1: users table and per-user job rows
**Status:** ❌ Not started
**Risk:** review (schema migration of the live jobs table, including a primary-key change)
**Issue:** #47

## Goal
The database gets ready for more than one user, and nothing changes for the user yet. A `users` table holds accounts
(the owner plus invited emails). Every job, task, status event, contact, interview, draft, panel run, preference row and
`llm_usage` row gets a `user_id`. On first start, the owner account (`STRELITZIA_OWNER_EMAIL`) is created and every
existing row is assigned to it. Jobs become unique on `(user_id, url)` instead of `url`, so two users can track the same
posting as separate rows. Later parts use this: part 2 (intake), part 3 (scoring), part 4 (workspace), part 5 (login).

## Context
- `src/strelitzia/database.py:111-195`: `init_db` creates `jobs` with `url TEXT PRIMARY KEY`. `:196-279`: `_ensure_tables`
  (side tables). `:296-330`: `job_key`/`backfill_job_keys` (key = hash of url). `:435-500`: `table_columns`/`ensure_columns`
  (add-column registry pattern to mirror). `:621-735`: `store_jobs`, `get_jobs_by_stage`, `get_jobs_by_urls`.
- `src/strelitzia/db_pg.py`: the Postgres adapter (`?` placeholders are rewritten). Both backends must migrate.
- `scripts/seed_preprod.py`: seeds 18 fake jobs. It must create the owner user and set `user_id` on what it inserts.
- `user_preferences` already has a `user_id` column (NULL for now). `llm_usage.user_id` exists too (`database.py:212-219`).
- Gotchas: SQLite can't change a primary key in place, so rebuild the table (`CREATE TABLE jobs_new ... ; INSERT SELECT; DROP; RENAME`)
  inside one transaction. Postgres can: `ALTER TABLE jobs DROP CONSTRAINT jobs_pkey, ADD PRIMARY KEY (user_id, url)`.
  Many columns came from `ensure_columns` over time, so the rebuild copies whatever columns exist (`table_columns`).
  Never drop data. Make the migration idempotent: it runs on every start and does nothing once `jobs.user_id` exists
  and the PK is composite.
- The `contacts` table gets `user_id` (contacts are per user). `job_contacts`, `interviews`, `drafts`, `status_events`,
  `dashboard_tasks`, `panel_runs` get `user_id` as well, so queries can filter without joining `jobs`.

## Tasks
### Task 1: users and invites
**Runs:** unattended
**Files:** src/strelitzia/users.py (new), src/strelitzia/database.py (modify), tests/test_users.py (new)
**What:** Table `users (id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, name TEXT, role TEXT NOT NULL DEFAULT 'user',
status TEXT NOT NULL DEFAULT 'invited', monthly_cap_usd DOUBLE PRECISION NULL, created_at TEXT, last_login_at TEXT)`.
role is `owner|user`, status is `invited|active|disabled`. Emails are stored lowercased. In `users.py`:
`@dataclass User(id, email, name, role, status, monthly_cap_usd)`, `owner(conn) -> User` (creates the owner from
`env("OWNER_EMAIL")` on first call, falling back to `owner@localhost` when unset so tests and CLI work), `get(conn, id)`,
`by_email(conn, email)`, `invite(conn, email, cap=DEFAULT_CAP_USD) -> User`, `activate(conn, email, name) -> User | None`
(returns None for an email that isn't invited), `set_status`, `set_cap`, `list_users(conn)`. `DEFAULT_CAP_USD = 5.0`.
The owner's cap is None (no cap).
**Validate:** `pytest tests/test_users.py -q` → passes, including invite→activate, an un-invited email → None, case-insensitive emails.
**Status:** ❌

### Task 2: user_id on every per-user table, owner backfill, composite job key
**Runs:** unattended
**Files:** src/strelitzia/database.py (modify), src/strelitzia/db_pg.py (modify if the adapter needs a helper), tests/test_database.py (modify), tests/test_database_pg.py (modify)
**What:** `migrate_users(conn)`, called from `init_db` after `_ensure_tables`: add `user_id TEXT` to jobs and every side
table above, set NULL `user_id` to `users.owner(conn).id` everywhere (including `user_preferences` and `llm_usage`),
then rebuild/alter `jobs` to `PRIMARY KEY (user_id, url)` and replace `idx_jobs_job_key` with a UNIQUE index on
`(user_id, job_key)`. Add indexes on `user_id` for the side tables. Fresh databases get the new shape from `init_db` directly.
**Validate:** `pytest tests/test_database.py tests/test_database_pg.py -q` → passes: a test builds an old-shape DB with jobs
and side rows, runs `init_db` twice, and checks every row belongs to the owner, the row count is unchanged, and two
users can insert the same url.
**Status:** ❌

### Task 3: job helpers take a user
**Runs:** unattended
**Files:** src/strelitzia/database.py (modify), scripts/seed_preprod.py (modify), tests/test_seed_preprod.py (modify)
**What:** `store_jobs(conn, jobs, ..., user_id: str)`, `get_jobs_by_stage(..., user_id: str | None = None)`,
`get_jobs_by_urls(conn, urls, user_id: str | None = None)`, `get_stats(conn, user_id=None)`: filter by user when given;
`ON CONFLICT (user_id, url)`. Callers that don't pass a user yet keep working (None = all users) until parts 2–4 thread it.
`backfill_job_keys` stays per row. The seed script uses `users.owner(conn).id`.
**Validate:** `scripts/validate.sh` → green.
**Status:** ❌

## Validation
- `scripts/validate.sh --full` passes.
- On a copy of a SQLite DB with existing jobs: `python -c "from strelitzia.database import init_db; init_db('/tmp/x.db')"`
  twice, then `sqlite3 /tmp/x.db "select count(*), count(distinct user_id) from jobs"` → the same count as before, and 1.

## Human test steps (preprod)
1. Open https://strelitzia-preprod.engineerfamily.net/app/: the 18 seeded jobs, contacts and preferences look as before.

## Decisions
1. Separate job rows per user (user, 2026-10-16): extraction runs twice for a shared URL, which costs fractions of a cent.
2. `user_id` is copied onto side tables rather than joined through jobs, so every query filters with one indexed column.
3. Without `STRELITZIA_OWNER_EMAIL` the owner is `owner@localhost`; part 5 re-keys it to the real email at first login.
