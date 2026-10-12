# Deploys (VPS)

ApplyPilot runs inside the engineerfamily Docker stack (`SiddharthEngineer/engineerfamily`), served at
`https://applypilot.engineerfamily.net/app/` behind nginx basic auth.

## Layout
| Path | Role |
|---|---|
| `~/src/ApplyPilot` | Dev clone (`AGENT_REPO`). All edits, branches and commits happen here; unattended agents work in worktrees under `~/.local/state/applypilot-agent/worktrees/`. Nothing builds from it. |
| `~/src/engineerfamily` | engineerfamily dev clone (`main`). Commit here. |
| `/srv/ApplyPilot-prod` | Prod build context, detached at a trunk commit. Moved only by `make applypilot-deploy`; never edited by hand. Its untracked `.env` duplicates `~/.applypilot/.env` and goes away in R16 (`plans/llm-limits-config.md`). |
| `/srv/ApplyPilot-preprod` | Preprod build context, moved to a review PR's head by `scripts/agent/preprod.sh`. |
| `/srv/engineerfamily-prod` | prod checkout (branch `prod`, compose project `engineerfamily`). |
| `/srv/engineerfamily-preprod` | preprod checkout (branch `preprod`, compose project `engineerfamily-preprod`, port 8080). |

`/srv` checkouts are `deploy:srv`, setgid, group-writable (`core.sharedRepository=group`), so nothing ever needs `chown`.
Compose project names are pinned (`-p` and `COMPOSE_PROJECT_NAME`), so renaming a checkout never changes volume names.
Untracked server files (`.env`, `services/nginx/ssl/`, `.htpasswd*`) exist only in the `/srv` checkouts and are never committed.

## How a change reaches each environment
| Change | Preprod | Prod (the user approves each one) |
|---|---|---|
| ApplyPilot (this repo) | Review PRs only: the runner's `preprod.sh` deploys the PR head (see below). Low-risk PRs auto-merge and skip preprod. | `make -C ~/src/engineerfamily applypilot-deploy [ref=<sha>]`: moves `/srv/ApplyPilot-prod` to `origin/trunk` (or `ref`) and rebuilds only the dashboard. |
| engineerfamily | Merge to `main`, then `make tag-preprod v=X.Y.Z` | Merge to `main`, then `make tag-prod v=X.Y.Z` |

The engineerfamily tags dispatch its `deploy.yml` workflow. That workflow resets the `preprod`/`prod` branch to the tag and SSHes to the
locked-down `deploy` account, which pulls the branch into `/srv/engineerfamily-{preprod,prod}` and rebuilds the stack (`scripts/vps/engineerfamily-deploy`).
Nobody edits or merges inside `/srv` by hand.

Every prod and preprod `make` target in engineerfamily acts on the `/srv` checkouts wherever it's run from. So `make ps`, `make logs`
and `make up` from `~/src/engineerfamily` operate on what's deployed, never on the dev tree. `make local-up` runs the dev tree.
`make up` and `make preprod-up` restart the whole stack. Prefer the narrow `applypilot-*` targets.

## Prod checks
1. Check that `https://engineerfamily.net`, `https://umami.engineerfamily.net` and `https://applypilot.engineerfamily.net` return 200 (`/app/` returns 401 without credentials).
2. If one doesn't: for ApplyPilot, run `make -C ~/src/engineerfamily applypilot-deploy ref=<previous sha>`. For engineerfamily, re-tag the previous version with `make tag-prod`.

## Preprod (review PRs)
`scripts/agent/preprod.sh` holds a one-PR lock: `deploy <pr>` checks the PR head out in `/srv/ApplyPilot-preprod`, runs
`make -C /srv/engineerfamily-preprod preprod-applypilot-up`, and health-checks `http://127.0.0.1:8080/app/api/health`
(credentials in `~/.config/applypilot-agent/preprod.netrc`). Merging or closing the PR frees the lock, and the next tick resets preprod to trunk.
Test at `https://applypilot-preprod.engineerfamily.net/app/` with the preprod login.
Preprod uses its own disposable `applypilot_preprod` DB (preprod analytics-db) and app dir (`~/.applypilot-preprod`), never prod data.
It has **no `.env` and no LLM keys, on purpose** (user, 2026-10-12): score and tailor actions fail there with a missing-key error. Don't add one.
