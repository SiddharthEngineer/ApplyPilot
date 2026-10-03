# Plan: Job Store on Postgres
**Started:** 2026-10-03
**Status:** 🔄 In Progress

## Goal
ApplyPilot's job data moves out of the SQLite file at `~/.applypilot/applypilot.db` and into a dedicated
`applypilot` database on the engineerfamily Postgres server (the `analytics-db` container, Postgres 16). Then the
host CLI (and later its cron jobs) and the dashboard container both read and write the same store safely. If
`APPLYPILOT_DATABASE_URL` is set, ApplyPilot uses Postgres. If it's unset, ApplyPilot keeps using SQLite, so the
public fork's default and the hermetic test suite keep working unchanged.

## Success Criteria
1. `docker exec engineerfamily-analytics-db-1 psql -U applypilot -d applypilot -c 'select 1'` succeeds, and
   `pg_isready -h 127.0.0.1 -p 5432` succeeds from the VPS host. Port 5432 isn't reachable from the internet.
2. `pytest tests/ -q` passes with `APPLYPILOT_DATABASE_URL` unset (the SQLite path is unchanged).
3. `APPLYPILOT_TEST_DATABASE_URL=postgresql://…/applypilot_test pytest -m pg tests/ -q` passes against a real Postgres.
4. `applypilot db migrate --from ~/.applypilot/applypilot.db` copies every row. `applypilot db verify` reports
   equal row counts per table and equal `fit_score`/`tailored_resume_path` counts between SQLite and Postgres.
5. With `APPLYPILOT_DATABASE_URL` set in `/srv/ApplyPilot/.env`: `applypilot status` shows the migrated totals,
   `applypilot doctor` prints `Database: postgresql (applypilot@127.0.0.1)`, and `applypilot run score --limit 2` writes to Postgres.

## Task Chain

### Task 1: Provision the `applypilot` database on the engineerfamily Postgres server
**Runs:** local (VPS; cross-repo: engineerfamily)
**Files:** `/srv/engineerfamily` work happens in a separate clone, never in the live `/srv/engineerfamily` checkout
(it's on the `prod` branch and is the deploy target). Clone `git@github.com:SiddharthEngineer/engineerfamily.git` to
`/root/src/engineerfamily` (or `git pull` if it exists) and work on `main`.
- `docker-compose.prod.yml` (modify): `analytics-db` gets `ports: ["127.0.0.1:5432:5432"]`, so the host CLI can connect.
- `scripts/init-applypilot-db.sh` (new): idempotent. Runs `psql` in the `analytics-db` container as the superuser
  `umami` to create role `applypilot` (LOGIN, password from `APPLYPILOT_DB_PASSWORD`), database `applypilot` and
  database `applypilot_test` (both owned by `applypilot`). Skips anything that already exists.
- `.env.template` (modify): add `APPLYPILOT_DB_PASSWORD=`, with the `openssl rand -base64 32` note.
- `Makefile` (modify): add an `applypilot-db-init` target that runs the script.
- `README.md` (modify): add a row to the subdomain/services section noting that `analytics-db` also hosts the `applypilot` DB.
**What:** Generate the password with `openssl rand -hex 24` (hex, so there are no URL-escaping problems). Append it as
`APPLYPILOT_DB_PASSWORD` to `/srv/engineerfamily/.env` (untracked, owned by `deploy`; keep the owner and mode). Commit
on `main`, push, then deploy with `make tag-prod v=1.0.3` from the clone (the user approved deploying straight to prod).
After the GitHub workflow finishes, run `scripts/init-applypilot-db.sh` against the prod container.
**Acceptance:**
- `gh run list --repo SiddharthEngineer/engineerfamily --workflow deploy.yml -L 1` shows success.
- `docker exec engineerfamily-analytics-db-1 psql -U applypilot -d applypilot -c 'select 1'` returns 1.
- `ss -ltn | grep 5432` shows only `127.0.0.1:5432`.
- `https://engineerfamily.net`, `umami.engineerfamily.net` and `applypilot.engineerfamily.net` still return 200 (Umami is unaffected).
**Status:** ❌ Not started

### Task 2: Postgres connection adapter with SQLite-compatible rows
**Runs:** cloud
**Files:** `src/applypilot/db_pg.py` (new), `pyproject.toml` (modify: optional extra `postgres = ["psycopg[binary]>=3.2"]`),
`tests/test_db_pg_adapter.py` (new)
**What:** Wrap a `psycopg.Connection` in `PgConnection`, which exposes the subset of the `sqlite3.Connection` API that
ApplyPilot uses: `execute(sql, params=())`, `executemany`, `commit`, `rollback`, `close`, plus a cursor whose
`fetchone/fetchall` and iteration work as they do in SQLite. `translate_sql(sql: str) -> str` turns `?` placeholders
into `%s` (ignoring `?` inside quoted literals) and escapes literal `%` as `%%`. Rows come from a custom psycopg row factory
returning `PgRow`, which supports `row["col"]`, `row[0]`, `keys()` and `dict(row)`, the way `sqlite3.Row` does.
The module imports `psycopg` lazily, so the base install doesn't need it.
```python
def translate_sql(sql: str) -> str: ...
class PgRow(Sequence): ...   # __getitem__(int|str), keys()
class PgConnection:
    def __init__(self, url: str): ...
    def execute(self, sql: str, params: Sequence | Mapping = ()) -> PgCursor: ...
```
**Acceptance:**
- `pytest tests/test_db_pg_adapter.py -q` passes with no Postgres server (it covers `translate_sql` on quoted `?`,
  `LIKE '%x%'` and named params, plus `PgRow` behavior built from a fake description).
- `python -c "import applypilot.database"` works without psycopg installed.
**Status:** ❌ Not started

### Task 3: Backend dispatch in `database.py` and portable schema
**Runs:** cloud
**Files:** `src/applypilot/database.py` (modify), `src/applypilot/config.py` (modify: `DATABASE_URL = os.environ.get("APPLYPILOT_DATABASE_URL")`), `tests/test_database.py` (modify)
**What:** `get_connection()` returns a `PgConnection` when `APPLYPILOT_DATABASE_URL` starts with `postgresql://`, and keeps
today's per-thread SQLite connection otherwise. Connections are cached per thread, as now. `init_db()` runs the
same `CREATE TABLE IF NOT EXISTS jobs` DDL on both backends (it's already portable: `TEXT`, `INTEGER`, `TEXT PRIMARY KEY`).
On Postgres, it skips the WAL/busy-timeout PRAGMAs. `ensure_columns()` reads existing columns from
`information_schema.columns` on Postgres and from `PRAGMA table_info` on SQLite. Add `backend_name(conn) -> str` for
`doctor`. Timestamps stay ISO-8601 `TEXT` on both backends, because every module compares them as strings.
**Acceptance:**
- `pytest tests/ -q` passes (SQLite path).
- A new unit test sets a fake `postgresql://` URL with `PgConnection` monkeypatched, and asserts that dispatch picks it.
**Status:** ❌ Not started

### Task 4: Replace SQLite-only SQL across modules
**Runs:** cloud
**Files:** every module in `grep -rnE "INSERT OR|datetime\(|julianday|strftime\(|sqlite_master|executescript|lastrowid|sqlite3\.(IntegrityError|Row)" src/applypilot`
(currently about 8 statements, plus `except sqlite3.IntegrityError` in `database.store_jobs`) (modify)
**What:** Rewrite each statement in SQL that runs on SQLite ≥3.24 and on Postgres. `INSERT OR IGNORE` becomes
`INSERT … ON CONFLICT (url) DO NOTHING`, and `INSERT OR REPLACE` becomes `ON CONFLICT (url) DO UPDATE SET …`. `datetime('now')`
is replaced by an ISO timestamp passed from Python. Integrity errors are caught through a backend-neutral
`database.IntegrityError` tuple (`sqlite3.IntegrityError` plus `psycopg.errors.UniqueViolation` when importable).
Type hints `sqlite3.Connection` become a `Connection` alias. No behavior change on SQLite.
**Acceptance:**
- The grep above returns only `database.py`'s SQLite-branch code.
- `pytest tests/ -q` passes.
**Status:** ❌ Not started

### Task 5: Postgres test tier
**Runs:** cloud (code, skipped without a server); local (VPS run against `applypilot_test`)
**Files:** `tests/conftest.py` (modify: `pg` marker and a `pg_db` fixture that resets the schema in `applypilot_test`),
`tests/test_database_pg.py` (new), `pyproject.toml` (modify: register the marker)
**What:** When `APPLYPILOT_TEST_DATABASE_URL` is set, the `pg_db` fixture drops and recreates the `jobs` table and yields a
`PgConnection`. Without it, `pg` tests are skipped. Port the core `tests/test_database.py` cases (init, ensure_columns,
store_jobs dedupe, get_jobs_by_stage, get_stats, reset_score_errors) to run on both backends with
`@pytest.mark.parametrize("backend", ["sqlite", pytest.param("pg", marks=pytest.mark.pg)])`.
**Acceptance:**
- `pytest tests/ -q` passes, with pg tests skipped.
- On the VPS: `APPLYPILOT_TEST_DATABASE_URL=postgresql://applypilot:$PW@127.0.0.1:5432/applypilot_test pytest -m pg -q` passes.
**Status:** ❌ Not started

### Task 6: `applypilot db migrate` and `applypilot db verify`
**Runs:** cloud (code + SQLite→SQLite unit test); local (real migration on the VPS)
**Files:** `src/applypilot/migrate.py` (new), `src/applypilot/cli.py` (modify: `db` Typer sub-app), `tests/test_migrate.py` (new)
**What:** `migrate(src_sqlite: Path, dst_url: str, batch: int = 500) -> dict[str, int]` opens the source read-only, runs
`init_db` on the destination, then copies `jobs` in batches with `INSERT … ON CONFLICT (url) DO NOTHING`. It copies only
the column intersection and warns about source columns the destination lacks. Rerunning it is safe. Before copying,
the source is backed up to `<src>.bak-<YYYY-MM-DD>` with `sqlite3.Connection.backup`. `verify(src, dst)` compares row counts
plus `count(full_description)`, `count(fit_score)`, `count(tailored_resume_path)` and `count(resume_drive_url)`, and exits
non-zero on any mismatch. The unit test migrates between two SQLite files (the destination is a SQLite URL in tests).
**Acceptance:**
- `pytest tests/test_migrate.py -q` passes.
- On the VPS: `applypilot db migrate --from ~/.applypilot/applypilot.db --to "$APPLYPILOT_DATABASE_URL"`, then
  `applypilot db verify …` exits 0, and the jobs count equals the SQLite count (3,147 at planning time, or more if discovery ran since).
**Status:** ❌ Not started

### Task 7: Cut over the VPS to Postgres
**Runs:** local (VPS)
**Files:** `src/applypilot/cli.py` (modify: `doctor` prints the backend), `.env.example` (modify: document
`APPLYPILOT_DATABASE_URL`), `README.md` (modify: a "Database" section), `/srv/ApplyPilot/.env` (outside git)
**What:** `uv pip install -e ".[postgres]"` into `.venv`. Rerun `migrate` right before cutover (catching rows added
since Task 6), then add `APPLYPILOT_DATABASE_URL=postgresql://applypilot:<pw>@127.0.0.1:5432/applypilot` to
`/srv/ApplyPilot/.env`. Keep the SQLite file in place as the rollback: unsetting the variable switches back.
**Acceptance:**
- `applypilot doctor` shows `Database: postgresql`.
- `applypilot status` totals match `db verify`.
- `applypilot run score --limit 2` runs, and `select count(*) from jobs where scored_at > now()::text` (or the equivalent) shows the new rows in Postgres.
- `pytest tests/ -q` still passes, because tests don't read the user's `.env`. Check this explicitly: if `load_env` leaks
  `APPLYPILOT_DATABASE_URL` into tests, add an autouse fixture in `conftest.py` that unsets it.
**Status:** ❌ Not started

## Implementation Order
```
T1 (provision, VPS) ───────────────────────────────┐
T2 adapter → T3 dispatch → T4 portable SQL → T5 pg tests → T6 migrate ─┴→ T7 cutover
```
1. Task 2  2. Task 3  3. Task 4  4. Task 5  5. Task 1 (any time before Task 5's live run)  6. Task 6  7. Task 7

## Key Design Decisions
1. Postgres, not MySQL: engineerfamily has no MySQL server. Its only DB server is the Postgres 16 `analytics-db`, so a new database there gives "our own DB in the existing server" without adding a container.
2. A thin `?`→`%s` adapter instead of an ORM rewrite: about 100 call sites already use `sqlite3`-style SQL, and the adapter keeps every one working with a small, testable diff.
3. SQLite stays the default backend: the fork is public, and the 600+ hermetic tests rely on temp SQLite files.
4. Timestamps stay `TEXT` ISO-8601, because the code compares them as strings everywhere. Converting to `timestamptz` is a separate change if it's ever needed.
5. Port 5432 is published only on `127.0.0.1`: the host CLI and cron need it, and the internet must not reach it.
6. engineerfamily changes are made in a separate clone on `main` and deployed with `make tag-prod`, because `/srv/engineerfamily` is the live `prod` checkout that the deploy workflow resets.

## Historical Record
- 2026-10-03: Plan created. engineerfamily was assumed to run MySQL; it runs Postgres 16 (Umami's `analytics-db`), so the target is a new `applypilot` DB there.
