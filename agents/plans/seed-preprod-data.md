# Plan: Seed preprod with fake jobs on every deploy
**Status:** ❌ Not started
**Risk:** review (touches `scripts/agent/preprod.sh`, which `gate.py` treats as high-risk, and wipes a database)

## Goal
Every preprod deploy (a review PR, or the reset to trunk) leaves `https://applypilot-preprod.engineerfamily.net/app/` showing the
same ~18 fake jobs spread across every pipeline stage, so the user can test a PR without real data. The data is wiped and reseeded on each deploy.
This replaces Task 4 of `vps-layout-migration.md`.

## Context
- **Where it runs.** Preprod's `applypilot` service (engineerfamily `docker-compose.preprod.yml`) is built from `/srv/ApplyPilot-preprod`
  and gets `APPLYPILOT_DATABASE_URL=postgresql://applypilot:…@analytics-db:5432/applypilot_preprod`. That DB isn't reachable from the host.
  The image holds only `src/` (`.dockerignore` drops `scripts/` and `tests/`), so the seed runs in a one-off container of the same image with the checkout
  mounted read-only at `/seed`. This command was checked live on 2026-10-12 (it saw `applypilot_preprod`, 0 jobs, and `/seed/tests/data/postings`):
  ```
  docker compose -p engineerfamily-preprod --project-directory /srv/engineerfamily-preprod \
    -f /srv/engineerfamily-preprod/docker-compose.base.yml -f /srv/engineerfamily-preprod/docker-compose.preprod.yml \
    run --rm --no-deps -T -v /srv/ApplyPilot-preprod:/seed:ro applypilot python /seed/scripts/seed_preprod.py
  ```
  It's already set as `PREPROD_SEED_CMD` in `~/.config/applypilot-agent/env`, which is user-owned and which you can't edit. Today's `preprod.sh` ignores it.
- **Schema and helpers.** `src/applypilot/database.py`: `init_db()` (creates `jobs`, `status_events`, `dashboard_tasks`, idempotent),
  `job_key(url)`, `store_jobs()` (insert pattern). User statuses go through `src/applypilot/tracking.py` `set_status(conn, url, status, submitted_at=…)`
  (`USER_STATUSES` at line 23), the same path as the dashboard's `POST /jobs/{key}/status` (`src/applypilot/web/app.py:140`).
- **Fixtures.** `tests/data/postings/<name>.meta.json` (title, site, company, location, salary, strategy, url) and `<name>.txt` (full description):
  6 public postings that are already committed. `tests/test_posting_prompt.py:17` shows how tests locate them.
- **Deploy flow.** `scripts/agent/preprod.sh` `deploy_ref()` (fetch, checkout, `PREPROD_DEPLOY_CMD`, sleep, `validate.sh --preprod`) serves
  both `deploy <pr>` and `release`. It runs from cron, where the cwd is `$HOME`.
- **Gotcha.** The seed wipes tables, so it must refuse to touch anything but the preprod DB. The prod DB is called `applypilot` and lives on the same kind of server.
- No preprod `.env` and no LLM keys, on purpose: seeded rows must not need Gemini to display.

## Tasks
### Task 1: `scripts/seed_preprod.py`
**Runs:** unattended
**Files:** scripts/seed_preprod.py (new), tests/test_seed_preprod.py (new)
**What:**
- `build_jobs(postings_dir: Path, now: datetime) -> list[dict]`: one job per fixture (from `.meta.json` + `.txt`), plus 12 synthetic jobs
  (`https://jobs.example.com/<slug>`, company `Example <Noun> Co`, varied titles and locations), deterministic for a given `now`.
  Spread all 18 across the stages: discovered only, enriched (`full_description`, `detail_scraped_at`), scored (`fit_score` 3-10 + `score_reasoning`),
  tailored and covered (`tailored_at`/`cover_letter_at` set, paths left NULL because no files exist), and user statuses
  `in_progress`, `submitted` (with `submitted_at`), `rejected` and `heard_back`. Timestamps fall over the last ~30 days.
- `seed(conn, jobs) -> int`: `DELETE FROM` `dashboard_tasks`, `status_events` and `jobs`, then insert the jobs (with `job_key`) and apply the user statuses via
  `tracking.set_status`, then commit. Returns the job count.
- `main(argv) -> int`: `--postings DIR` (default: `tests/data/postings` next to the script's repo). Target = `APPLYPILOT_DATABASE_URL`.
  **Guard:** exit 2 with a clear message unless that URL is postgres and its database name is exactly `applypilot_preprod`.
  `--sqlite PATH` (for tests) seeds that SQLite file instead and needs no URL. Then `init_db(target)`, `seed`, and print `seeded N jobs into <db name>`.
**Validate:** `.venv/bin/pytest tests/test_seed_preprod.py -q` → passes. Tests (SQLite in `tmp_path`): 18 jobs with every stage and every user status present;
running twice still leaves 18 (wipe works); the guard refuses `…/applypilot`, `…/applypilot_test`, no URL without `--sqlite`, and non-postgres URLs (exit 2, no writes).
`scripts/validate.sh` → ALL PASS.
**Status:** ✅ 2026-10-12 (commit `feat(seed-preprod-data): Task 1`). Fixtures fill the later stages (so detailed rows have real descriptions); rejected and heard_back jobs go through `submitted` first, so each has a submitted date.

### Task 2: Seed on every preprod deploy
**Runs:** unattended
**Files:** scripts/agent/preprod.sh (modify), tests/test_agent_runner.py (modify), agents/DEPLOY.md (modify), scripts/agent/README.md (modify)
**What:** In `deploy_ref()`, after `PREPROD_DEPLOY_CMD` succeeds and before the health check: if `PREPROD_SEED_CMD` is set and
`$PREPROD_APP_DIR/scripts/seed_preprod.py` exists, run `bash -c "$PREPROD_SEED_CMD"`. A non-zero exit fails the deploy, the same way a failed deploy command does.
If the script is missing (a PR branched before this plan), echo `seed skipped: no scripts/seed_preprod.py at <sha>` and carry on.
Update the header comment's config list. In DEPLOY.md's preprod section and the runner README's env block, add `PREPROD_SEED_CMD` (the command from Context) and note that preprod data is wiped and reseeded on every deploy.
**Validate:** `bash -n scripts/agent/preprod.sh`. Add a test that runs `preprod.sh` `deploy_ref` logic with stub commands, either by extracting it into a sourceable function
or by running the script with `PREPROD_*` pointing at `true`/`false` stubs and a temp git repo. It checks that the seed runs when the script exists, is skipped when it's missing,
and that a failing seed fails the deploy. `scripts/validate.sh --full` → ALL PASS.
**Status:** ❌

### Task 3: Turn it on and check it live
**Runs:** interactive (VPS: pulls the runner's tree, deploys preprod)
**Files:** none
**What:** After merge: `git -C ~/src/ApplyPilot pull --ff-only` (cron runs `preprod.sh` from that tree), then `scripts/agent/preprod.sh release`
(it redeploys trunk, which now seeds). Mark Task 4 of `vps-layout-migration.md` ✅ pointing here.
**Validate:** `preprod.sh release` prints `preprod reset to trunk`; `runs.log` has `seeded 18 jobs into applypilot_preprod`; the preprod jobs list shows 18 rows.
**Status:** ❌

## Validation
- `scripts/validate.sh --full` → ALL PASS.
- `.venv/bin/pytest tests/test_seed_preprod.py tests/test_agent_runner.py -q` → all pass.
- `APPLYPILOT_DATABASE_URL=postgresql://u:p@h/applypilot .venv/bin/python scripts/seed_preprod.py` → exit 2, refusing (no DB needed for the guard).

## Human test steps (preprod)
This PR's own preprod deploy runs the **old** `preprod.sh` from `~/src/ApplyPilot`, so it won't seed itself. Test after merge:
1. Tell Claude to do Task 3, or run `git -C ~/src/ApplyPilot pull --ff-only && ~/src/ApplyPilot/scripts/agent/preprod.sh release` on the VPS.
2. Open `https://applypilot-preprod.engineerfamily.net/app/` (preprod login). You should see 18 jobs: 6 real-looking postings (Coinbase, NVIDIA, …) and 12 `Example … Co` jobs,
   with scores, and some marked in progress, submitted, rejected or heard back.
3. Change one job's status, then run `preprod.sh release` again. The change is gone and there are still 18 jobs.

## Decisions
1. Wipe and reseed on every deploy (user, 2026-10-12): repeatable test steps matter more than keeping preprod edits.
2. Fixtures plus synthetic jobs (user, 2026-10-12): realistic descriptions from public postings already in the repo, with enough rows to cover every stage.
3. The seed runs in a one-off container of the preprod image with the checkout mounted, so neither engineerfamily nor the image changes.
4. The guard keys on the exact DB name `applypilot_preprod`, so it can never wipe prod's `applypilot` or the `applypilot_test` DB.
5. The command lives in the user's env as `PREPROD_SEED_CMD`, mirroring `PREPROD_DEPLOY_CMD`, because the compose details belong to the VPS rather than to this repo.
