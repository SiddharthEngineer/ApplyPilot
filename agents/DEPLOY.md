# Deploys (VPS)

ApplyPilot runs inside the engineerfamily Docker stack (`SiddharthEngineer/engineerfamily`), served at
`https://applypilot.engineerfamily.net/app/` behind nginx basic auth.

## Layout
| Path | Role |
|---|---|
| `~/src/ApplyPilot` | Dev clone (`AGENT_REPO`). All edits, branches and commits happen here; unattended agents work in worktrees under `~/.local/state/applypilot-agent/worktrees/`. Nothing builds from it. |
| `~/src/engineerfamily` | engineerfamily dev clone (`main`). Commit here. |
| `/srv/ApplyPilot-prod` | Prod build context, detached at a trunk commit. Moved only by `make applypilot-deploy`; never edited by hand. Holds its own untracked `.env` (LLM keys). |
| `/srv/ApplyPilot-preprod` | Preprod build context, moved to a review PR's head by `scripts/agent/preprod.sh`. |
| `/srv/engineerfamily-prod` | prod checkout (branch `prod`, compose project `engineerfamily`). |
| `/srv/engineerfamily-preprod` | preprod checkout (branch `preprod`, compose project `engineerfamily-preprod`, port 8080). |

`/srv` checkouts are `deploy:srv`, setgid, group-writable (`core.sharedRepository=group`), so nothing ever needs `chown`.
Compose project names are pinned (`-p` and `COMPOSE_PROJECT_NAME`), so renaming a checkout never changes volume names.
Untracked server files (`.env`, `services/nginx/ssl/`, `.htpasswd*`) exist only in the `/srv` checkouts and are never committed.

## Prod deploy (the user approves each one)
1. engineerfamily changes: commit on `main` in `~/src/engineerfamily`, then `git push origin main`.
2. In `/srv/engineerfamily-prod`: `git -c safe.directory=/srv/engineerfamily-prod fetch origin`, then `… merge --ff-only origin/main`, then `git push origin prod`.
3. `make up` (whole stack), or `make applypilot-deploy [ref=<sha>]` (move `/srv/ApplyPilot-prod` to `origin/trunk` or `ref`, then rebuild only the dashboard).
4. Check that `https://engineerfamily.net`, `https://umami.engineerfamily.net` and `https://applypilot.engineerfamily.net` return 200 (`/app/` returns 401 without credentials).
   If one doesn't, `make applypilot-deploy ref=<previous sha>` (or revert on `main` and redeploy the same way).

## Preprod (review PRs)
`scripts/agent/preprod.sh` holds a one-PR lock: `deploy <pr>` checks the PR head out in `/srv/ApplyPilot-preprod`, runs
`make -C /srv/engineerfamily-preprod preprod-applypilot-up`, and health-checks `http://127.0.0.1:8080/app/api/health`
(credentials in `~/.config/applypilot-agent/preprod.netrc`). Merging or closing the PR frees the lock, and the next tick resets preprod to trunk.
Test at `https://applypilot-preprod.engineerfamily.net/app/` with the preprod login.
Preprod uses its own disposable `applypilot_preprod` DB (preprod analytics-db) and app dir (`~/.applypilot-preprod`), and no LLM keys; never prod data.
