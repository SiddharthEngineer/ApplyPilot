# Deploys (VPS)

ApplyPilot runs inside the engineerfamily Docker stack (`SiddharthEngineer/engineerfamily`), served at
`https://applypilot.engineerfamily.net/app/` behind nginx basic auth.

## Layout today (until agents/plans/vps-layout-migration.md lands)
| Path | Role |
|---|---|
| `/srv/ApplyPilot` | Dev tree **and** prod build context (`docker-compose.prod.yml` builds `context: ../ApplyPilot`). Uncommitted edits here ship on the next prod build, so unattended agents work in worktrees under `~/.local/state/applypilot-agent/worktrees/`. |
| `/home/dev/src/engineerfamily` | engineerfamily working clone (`main`). Commit here. |
| `/srv/engineerfamily` | prod checkout (branch `prod`, owner `deploy:srv`, setgid, group-writable). |
| `/srv/engineerfamily-preprod` | preprod checkout (port 8080). **It has no applypilot service yet**, so review PRs can't be deployed until the migration. |

## Prod deploy (the user approves each one)
1. engineerfamily changes: commit on `main` in `/home/dev/src/engineerfamily`, then `git push origin main`.
2. In `/srv/engineerfamily`: `git -c safe.directory=/srv/engineerfamily fetch origin`, then `… merge --ff-only origin/main`, then `git push origin prod`.
3. `make up` (whole stack) or `make applypilot-up` (rebuild only the dashboard from `/srv/ApplyPilot`).
4. Check that `https://engineerfamily.net`, `https://umami.engineerfamily.net` and `https://applypilot.engineerfamily.net` return 200 (`/app/` returns 401 without credentials).
   If one doesn't, revert on `main` and redeploy the same way.

Untracked server files (`.env`, `services/nginx/ssl/`, `.htpasswd*`) exist only in the `/srv` checkouts and are never committed.
`dev` and `deploy` share group `srv` (umask 002, `core.sharedRepository=group`), so nothing ever needs `chown`.

## Preprod (review PRs)
`scripts/agent/preprod.sh` holds a one-PR lock: `deploy <pr>` checks the PR head out in `PREPROD_APP_DIR`, runs
`PREPROD_DEPLOY_CMD`, and health-checks it. Merging or closing the PR frees the lock, and the next tick resets preprod to trunk.
Preprod uses its own disposable `applypilot_preprod` DB and app dir, never prod data.
