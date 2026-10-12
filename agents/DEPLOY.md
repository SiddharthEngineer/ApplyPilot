# Deploys (VPS)

Strelitzia (formerly ApplyPilot) runs inside the engineerfamily Docker stack (`SiddharthEngineer/engineerfamily`), served at
`https://applypilot.engineerfamily.net/app/` behind nginx basic auth. The domain moves to `strelitzia.engineerfamily.net` (301 from
`applypilot.*`) in M16, an engineerfamily change. Until then the engineerfamily side keeps its names: the compose service `applypilot`,
the `applypilot-*` make targets, `APPLYPILOT_DATABASE_URL`, and the `applypilot` / `applypilot_preprod` Postgres DBs (the DB names stay
for good). Strelitzia 0.4 reads all of those through its deprecated fallbacks, so that stack runs it unchanged.

## Layout
| Path | Role |
|---|---|
| `~/src/ApplyPilot` → `~/src/Strelitzia` | Dev clone (`AGENT_REPO`), at the old path (until the post-merge migration, M15). All edits, branches and commits happen here; unattended agents work in worktrees under `~/.local/state/applypilot-agent/worktrees/` (→ `strelitzia-agent`). Nothing builds from it. |
| `~/src/engineerfamily` | engineerfamily dev clone (`main`). Commit here. |
| `/srv/ApplyPilot-prod` → `/srv/Strelitzia-prod` | Prod build context (until the post-merge migration, M15), detached at a trunk commit. Moved only by `make applypilot-deploy`; never edited by hand. It has no `.env`: the dashboard reads `~/.applypilot/.env` through its `~/.applypilot` mount (M13, 2026-10-12; `~/.strelitzia` after M15, with a symlink at the old path). |
| `/srv/ApplyPilot-preprod` → `/srv/Strelitzia-preprod` | Preprod build context (until the post-merge migration, M15), moved to a review PR's head by `scripts/agent/preprod.sh`. |
| `/srv/engineerfamily-prod` | prod checkout (branch `prod`, compose project `engineerfamily`). |
| `/srv/engineerfamily-preprod` | preprod checkout (branch `preprod`, compose project `engineerfamily-preprod`, port 8080). |

`/srv` checkouts are `deploy:srv`, setgid, group-writable (`core.sharedRepository=group`), so nothing ever needs `chown`.
Compose project names are pinned (`-p` and `COMPOSE_PROJECT_NAME`), so renaming a checkout never changes volume names.
Untracked server files (`.env`, `services/nginx/ssl/`, `.htpasswd*`) exist only in the `/srv` checkouts and are never committed.

## How a change reaches each environment
| Change | Preprod | Prod (the user approves each one) |
|---|---|---|
| Strelitzia (this repo) | Review PRs only: the runner's `preprod.sh` deploys the PR head (see below). Low-risk PRs auto-merge and skip preprod. | `make -C ~/src/engineerfamily applypilot-deploy [ref=<sha>]`: moves `/srv/ApplyPilot-prod` to `origin/trunk` (or `ref`) and rebuilds only the dashboard. |
| engineerfamily | Merge to `main`, then `make tag-preprod v=X.Y.Z` and `make deploy-preprod ref=vX.Y.Z-preprod` | Merge to `main`, then `make tag-prod v=X.Y.Z` and `make deploy-prod ref=vX.Y.Z` |

Tagging alone deploys nothing: `make deploy-{preprod,prod} ref=<tag>` dispatches engineerfamily's `deploy.yml` workflow. That workflow resets the `preprod`/`prod` branch to the tag and SSHes to the
locked-down `deploy` account, which pulls the branch into `/srv/engineerfamily-{preprod,prod}` and rebuilds the stack (`scripts/vps/engineerfamily-deploy`).
Nobody edits or merges inside `/srv` by hand.

Every prod and preprod `make` target in engineerfamily acts on the `/srv` checkouts wherever it's run from. So `make ps`, `make logs`
and `make up` from `~/src/engineerfamily` operate on what's deployed, never on the dev tree. `make local-up` runs the dev tree.
`make up` and `make preprod-up` restart the whole stack. Prefer the narrow `applypilot-*` targets.

## Prod checks
1. Check that `https://engineerfamily.net`, `https://umami.engineerfamily.net` and `https://applypilot.engineerfamily.net` return 200 (`/app/` returns 401 without credentials).
2. If one doesn't: for Strelitzia, run `make -C ~/src/engineerfamily applypilot-deploy ref=<previous sha>`. For engineerfamily, run `make deploy-prod ref=<previous tag>`.

## Preprod (review PRs)
`scripts/agent/preprod.sh` holds a one-PR lock: `deploy <pr>` checks the PR head out in `$PREPROD_APP_DIR` (`/srv/ApplyPilot-preprod` (until the post-merge migration, M15)), runs
`make -C /srv/engineerfamily-preprod preprod-applypilot-up`, and health-checks `http://127.0.0.1:8080/app/api/health`
(credentials in `~/.config/applypilot-agent/preprod.netrc`, `strelitzia-agent` after M15). Merging or closing the PR frees the lock, and the next tick resets preprod to trunk.
Test at `https://applypilot-preprod.engineerfamily.net/app/` with the preprod login.
Preprod uses its own disposable `applypilot_preprod` DB (preprod analytics-db) and app dir (`~/.applypilot-preprod`, `~/.strelitzia-preprod` after M15), never prod data.
**Preprod data is wiped and reseeded on every deploy** (a PR deploy or the reset to trunk): after the deploy command, `preprod.sh` runs
`PREPROD_SEED_CMD`, which runs `scripts/seed_preprod.py` in a one-off container of the preprod image with the checkout mounted at `/seed`.
It leaves 18 fake jobs across every pipeline stage and user status, and refuses any DB not named exactly `applypilot_preprod`.
A PR branched before the script existed deploys with `seed skipped` in `runs.log`.
```
PREPROD_SEED_CMD="docker compose -p engineerfamily-preprod --project-directory /srv/engineerfamily-preprod -f /srv/engineerfamily-preprod/docker-compose.base.yml -f /srv/engineerfamily-preprod/docker-compose.preprod.yml run --rm --no-deps -T -v /srv/ApplyPilot-preprod:/seed:ro applypilot python /seed/scripts/seed_preprod.py"
```
It has **no `.env` and no LLM keys, on purpose** (user, 2026-10-12): score and tailor actions fail there with a missing-key error. Don't add one.
