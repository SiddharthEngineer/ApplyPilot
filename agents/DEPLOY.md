# Deploys (VPS)

Strelitzia runs inside the engineerfamily Docker stack (`SiddharthEngineer/engineerfamily`) as the compose service `strelitzia`,
served at `https://strelitzia.engineerfamily.net/app/` behind nginx basic auth. `applypilot.engineerfamily.net` (and `-preprod`) 301 to the
new names, keeping the path. The Postgres DBs `applypilot` / `applypilot_preprod`, their role, the engineerfamily `.env` keys
`APPLYPILOT_DB_PASSWORD*` and `services/nginx/.htpasswd-applypilot` keep their old names for good.
For running a deploy step by step, Claude has the `engineerfamily-deploy` skill (`~/.claude/skills/`).

## Layout
| Path | Role |
|---|---|
| `~/src/Strelitzia` | Dev clone (`AGENT_REPO`). All edits, branches and commits happen here; unattended agents work in worktrees under `~/.local/state/strelitzia-agent/worktrees/`. Nothing builds from it. |
| `~/src/engineerfamily` | engineerfamily dev clone (`main`). Commit here. |
| `/srv/Strelitzia-prod` | Prod build context, detached at a trunk commit. Moved only by `make strelitzia-deploy`; never edited by hand. It has no `.env`: the dashboard reads `~/.strelitzia/.env` through its `~/.strelitzia` mount. |
| `/srv/Strelitzia-preprod` | Preprod build context, moved to a review PR's head by `scripts/agent/preprod.sh`. |
| `/srv/engineerfamily-prod` | prod checkout (branch `prod`, compose project `engineerfamily`). |
| `/srv/engineerfamily-preprod` | preprod checkout (compose project `engineerfamily-preprod`, port 8080). |

The old `/srv/ApplyPilot-*`, `~/.applypilot*` and `*/applypilot-agent` paths are gone (2026-10-13): nothing uses them and DB rows
hold `/home/dev/.strelitzia/...` paths.
`/srv` checkouts are `deploy:srv`, setgid, group-writable (`core.sharedRepository=group`), so nothing ever needs `chown`. git needs
each real path in `safe.directory` (`git config --global --get-all safe.directory`); the symlinked old path doesn't count.
Compose project names are pinned (`-p` and `COMPOSE_PROJECT_NAME`), so renaming a checkout never changes volume names.
Untracked server files (`.env`, `services/nginx/ssl/`, `.htpasswd*`) exist only in the `/srv` checkouts and are never committed.

## How a change reaches each environment
| Change | Preprod | Prod (the user approves each one) |
|---|---|---|
| Strelitzia (this repo) | Review PRs only: the runner's `preprod.sh` deploys the PR head (see below). Low-risk PRs auto-merge and skip preprod. | `make -C ~/src/engineerfamily strelitzia-deploy [ref=<sha>]`: moves `/srv/Strelitzia-prod` to `origin/trunk` (or `ref`) and rebuilds only the dashboard. |
| engineerfamily | `make -C ~/src/engineerfamily preprod-deploy [ref=<sha\|origin/branch>]`, no tag needed | Merge to `main`, then `make tag-prod v=X.Y.Z` and `make deploy-prod ref=vX.Y.Z` |

Prod tagging alone deploys nothing: `make deploy-prod ref=<tag>` dispatches engineerfamily's `deploy.yml` workflow, which resets the
`prod` branch to the tag and SSHes to the locked-down `deploy` account to rebuild the stack (`scripts/vps/engineerfamily-deploy`).

Every prod and preprod `make` target in engineerfamily acts on the `/srv` checkouts wherever it's run from. So `make ps`, `make logs`
and `make up` from `~/src/engineerfamily` operate on what's deployed, never on the dev tree. `make local-up` runs the dev tree.
`make up` and `make preprod-up` restart the whole stack. Prefer the narrow `strelitzia-*` targets (the `applypilot-*` names are deprecated aliases).

## Prod checks
Check from outside, through Cloudflare. A loopback check doesn't prove the hostname routes to the right nginx.
1. `https://engineerfamily.net`, `https://umami.engineerfamily.net` and `https://strelitzia.engineerfamily.net` return 200
   (`/app/` returns 401 without credentials), and `https://applypilot.engineerfamily.net/app/` returns 301.
2. If one doesn't: for Strelitzia, run `make -C ~/src/engineerfamily strelitzia-deploy ref=<previous sha>`. For engineerfamily, run `make deploy-prod ref=<previous tag>`.

## Preprod (review PRs)
`scripts/agent/preprod.sh` holds a one-PR lock: `deploy <pr>` checks the PR head out in `$PREPROD_APP_DIR` (`/srv/Strelitzia-preprod`), runs
`make -C /srv/engineerfamily-preprod preprod-strelitzia-up`, and health-checks `http://127.0.0.1:8080/app/api/health`
(credentials in `~/.config/strelitzia-agent/preprod.netrc`). Merging or closing the PR frees the lock, and the next tick resets preprod to trunk.
Test at `https://strelitzia-preprod.engineerfamily.net/app/` with the preprod login.
Preprod uses its own disposable `applypilot_preprod` DB (preprod analytics-db) and app dir (`~/.strelitzia-preprod`), never prod data.
**Preprod data is wiped and reseeded on every deploy** (a PR deploy or the reset to trunk): after the deploy command, `preprod.sh` runs
`PREPROD_SEED_CMD`, which runs `scripts/seed_preprod.py` in a one-off container of the preprod image with the checkout mounted at `/seed`.
It leaves 18 fake jobs across every pipeline stage and user status, and refuses any DB not named exactly `applypilot_preprod`.
A PR branched before the script existed deploys with `seed skipped` in `runs.log`.
```
PREPROD_SEED_CMD="docker compose -p engineerfamily-preprod --project-directory /srv/engineerfamily-preprod -f /srv/engineerfamily-preprod/docker-compose.base.yml -f /srv/engineerfamily-preprod/docker-compose.preprod.yml run --rm --no-deps -T -v /srv/Strelitzia-preprod:/seed:ro strelitzia python /seed/scripts/seed_preprod.py"
```
It has **no `.env` and no LLM keys, on purpose** (user, 2026-10-12): score and tailor actions fail there with a missing-key error. Don't add one.
