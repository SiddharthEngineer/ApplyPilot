# Plan: VPS layout migration (dev clones out of /srv, pinned prod/preprod checkouts, preprod ApplyPilot)
**Status:** ✅ Complete (2026-10-12): Tasks 1-3, 5 and 6 done; Task 4 moved to `seed-preprod-data` (R17)
**Risk:** review (touches prod infra; every task is interactive)

## Goal
Agents and the user develop in `/home/dev/src/{ApplyPilot,engineerfamily}`. `/srv` holds deploy-only checkouts:
`/srv/ApplyPilot-prod` and `/srv/engineerfamily-prod` (prod), and `/srv/ApplyPilot-preprod` and `/srv/engineerfamily-preprod` (preprod).
Prod builds from a pinned trunk commit instead of a live working tree. Preprod gets its own ApplyPilot service with
disposable data, so `scripts/agent/preprod.sh` can deploy review PRs there.

## Context
- `/srv/engineerfamily/docker-compose.prod.yml:91-107`: the `applypilot` service builds `context: ../ApplyPilot` and mounts `../ApplyPilot/.env` and `~/.applypilot`.
- No compose file sets `name:`, so the project name is the directory name (`engineerfamily`, `engineerfamily-preprod`). Renaming a
  directory without pinning the name would create a **new project with new, empty volumes** (prod Postgres and Umami), and
  container names like `engineerfamily-nginx-1` (hard-coded in `scripts/vps/engineerfamily-deploy:9-10`) would change.
  `docker-compose.base.yml` is shared by both envs, so pin the name per env (`-p` in the Makefile, or `COMPOSE_PROJECT_NAME` in each checkout's `.env`), not with `name:` in the base file.
- `/srv/engineerfamily-preprod` is at a7937ec, before the ApplyPilot service and DB were added. It needs the current `main` first.
- `/srv` is `root:root 755`, so creating directories there takes `sudo` (the user runs those). Deploy dirs are `deploy:srv`, setgid, group-writable.
- This Claude project's memory is keyed to `/srv/ApplyPilot` (`~/.claude/projects/-srv-ApplyPilot/memory/`). Copy it to the new path's project dir.

## Tasks
### Task 1: Pin compose project names
**Runs:** interactive (engineerfamily prod)
**Files:** engineerfamily `Makefile` and `scripts/vps/engineerfamily-deploy` (modify)
**What:** Pass `-p engineerfamily` to every prod compose call and `-p engineerfamily-preprod` to every preprod call. Deploy, and confirm `docker ps` shows the same container names and `docker volume ls` the same volumes.
**Validate:** `docker compose -p engineerfamily ls` shows the running project; the three prod URLs return 200 (`/app/` returns 401).
**Status:** ✅ 2026-10-12: `-p` in Makefile, deploy script and init-applypilot-db.sh, plus `COMPOSE_PROJECT_NAME` in each checkout's `.env` (engineerfamily 5263fbd). Same containers and volumes after the rename.

### Task 2: Pinned prod checkout of ApplyPilot
**Runs:** interactive (sudo, prod)
**Files:** engineerfamily `docker-compose.prod.yml`, `Makefile` (modify); `agents/DEPLOY.md` (modify)
**What:** The user runs `sudo install -d -o deploy -g srv -m 2775 /srv/ApplyPilot-prod`, then clones there and checks out `origin/trunk` detached.
Point the build context and the `.env` mount at `../ApplyPilot-prod`, with the `.env` copied by the user. Add `make applypilot-deploy`, which fetches, checks out `origin/trunk` and rebuilds.
**Validate:** `make applypilot-deploy` and the URLs are healthy; `docker inspect` shows the image built from the `-prod` commit.
**Status:** ✅ 2026-10-12: `/srv/ApplyPilot-prod` at fd33f1b with its own `.env`; `make applypilot-deploy [ref=…]`. Prod jobs DB untouched (3147 rows).

### Task 3: Preprod ApplyPilot service with disposable data
**Runs:** interactive (preprod)
**Files:** engineerfamily `docker-compose.preprod.yml`, nginx preprod config, `Makefile`, an `init-applypilot-db` variant for preprod (modify/new)
**What:** Update `/srv/engineerfamily-preprod` to current `main`. Add an `applypilot` service building `../ApplyPilot-preprod` (the user creates it like Task 2), using an `applypilot_preprod` DB in the preprod analytics-db and an empty app dir. Add an nginx `/app/` route on :8080 with its own basic auth, and `make applypilot-up` for preprod.
**Validate:** `curl -fsS --netrc-file … http://127.0.0.1:8080/app/api/health` → `{"ok": true, …}`.
**Status:** ✅ 2026-10-11: engineerfamily 5c3a651. `/app/` on applypilot-preprod (and 127.0.0.1:8080), preprod basic auth, `applypilot_preprod` DB, app dir `~/.applypilot-preprod`, no LLM keys.

### Task 4: Seed preprod data
**Runs:** interactive
**Files:** `scripts/seed_preprod.py` (new)
**What:** Load a small fake job set (built from `tests/data/`, never personal data) into `applypilot_preprod`. It is run by `PREPROD_DEPLOY_CMD` on every deploy so preprod starts clean.
**Validate:** the jobs list on preprod shows the seeded rows.
**Status:** ⏸ (moved to its own unattended plan, `seed-preprod-data.md`)

### Task 5: Wire the runner to preprod and test it end to end
**Runs:** interactive
**Files:** `~/.config/applypilot-agent/env` (user; not committed)
**What:** Set `PREPROD_APP_DIR`, `PREPROD_DEPLOY_CMD`, `PREPROD_HEALTH_URL` and `PREPROD_NETRC`. Open a throwaway docs PR, then `scripts/agent/preprod.sh deploy <n>`, `status`, close the PR, and `status` again (it auto-releases and resets to trunk).
**Validate:** each command prints the expected state, an ntfy push arrives, and the PR gets the `on-preprod` label and then loses it.
**Status:** ✅ 2026-10-12: `preprod.sh deploy 22` passed (health, `on-preprod` label, PR comment); after PR #22 merged, the 05:10 cron tick released the lock, marked the item `done` and reset preprod to trunk.

### Task 6: Move the dev clone and rename the prod engineerfamily checkout
**Runs:** interactive (sudo, prod)
**Files:** engineerfamily `scripts/vps/engineerfamily-deploy`, `.github/workflows/deploy.yml`, `scripts/setup-vps.sh`, README (modify); `agents/DEPLOY.md`, `scripts/agent/README.md`, `.claude/settings.local.json` (modify)
**What:** Clone ApplyPilot to `/home/dev/src/ApplyPilot` (move `personal/` and `.env` over by hand, and recreate `.venv`). Copy the Claude memory dir.
Point the crontab and `AGENT_REPO` at the new path. With Task 1 done, `sudo mv /srv/engineerfamily /srv/engineerfamily-prod` and update the paths in the deploy script and workflow. Remove `/srv/ApplyPilot` once everything is verified.
**Validate:** the prod URLs return 200, `docker volume ls` is unchanged, `crontab -l` shows the new path, and a `/schedule-build` of a low-risk docs plan completes.
**Status:** ✅ 2026-10-12: `/srv/ApplyPilot` and `/srv/espn-predraft-api` moved to `~/src` (venv rebuilt, validate.sh passes), `/srv/engineerfamily` renamed to `-prod` and the deploy script reinstalled, crontab, `AGENT_REPO` and Claude memory updated. The unattended-build check was covered by R14 (`remove-dead-enrichment-code`, PR #22), which went through the queue end to end.

## Validation
- Prod URLs are healthy and the volumes are unchanged after every task.
- `scripts/agent/preprod.sh deploy` and `release` work against a real PR (Task 5).

## Human test steps (preprod)
Done live with the agent during each task.

## Decisions
1. Pin compose project names before any rename, because the project name decides the volume names.
2. Preprod data is disposable and seeded from fake rows, so personal job data never reaches preprod.
3. Dev clones live in `/home/dev/src`. `/srv` holds only deploy checkouts that nothing edits by hand.
