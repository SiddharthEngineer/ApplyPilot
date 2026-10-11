# Unattended build runner

Hourly cron → `run.sh`. It checks for due work with plain bash/Python and starts `claude -p` only when a queued plan is due.

| File | Role |
|---|---|
| `run.sh` | Cron entry: upkeep (inbox replies, preprod lock), pick the next item, run `/implement --unattended` in a worktree, re-validate, then merge or send to review |
| `buildqueue.py` | Queue state, attempt and daily caps, pause (`~/.local/state/applypilot-agent/`) |
| `gate.py` | Risk gate: the plan's `**Risk:**` line plus diff checks decide auto-merge vs review |
| `preprod.sh` | Single-occupant preprod lock: deploy a PR, health-check it, release it on merge or close |
| `inbox.py` | Questions and notices as issues in a private repo, plus an ntfy push |
| `settings.json`, `mcp.json` | Permissions and the Playwright MCP for headless runs (the agent cannot merge, deploy or edit these) |

## Limits (so it can't run away)
- Claude starts only if an item is due, the queue isn't paused, there have been fewer than 4 runs in 24h (`AGENT_MAX_RUNS_PER_DAY`), and the item has used fewer than 2 attempts (`AGENT_MAX_ATTEMPTS`).
- Each run is killed after `AGENT_MAX_RUNTIME` (3h). A `flock` allows one run at a time.
- A usage-limit exit pauses the queue for 5h and gives the attempt back.
- A run that ends without a result file, or whose `ready` fails `validate.sh --full`, counts as a failed attempt. After 2 failures the item is `blocked` and you get a notice.

## One-time setup (you)
1. Create the private inbox repo: `gh repo create SiddharthEngineer/agent-inbox --private --description "Agent questions and notices"`.
2. Install the ntfy app and subscribe to a hard-to-guess topic (e.g. `applypilot-<random>`).
3. Run `claude setup-token` and write `~/.config/applypilot-agent/env` (mode 600):
   ```bash
   export CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-...
   AGENT_REPO=/srv/ApplyPilot            # dev clone the runner makes worktrees from
   AGENT_INBOX_REPO=SiddharthEngineer/agent-inbox
   NTFY_TOPIC=applypilot-<random>
   # Set these once preprod has an applypilot service (agents/plans/vps-layout-migration.md):
   # PREPROD_APP_DIR=/srv/ApplyPilot-preprod
   # PREPROD_DEPLOY_CMD="make -C /srv/engineerfamily-preprod applypilot-up"
   # PREPROD_HEALTH_URL=http://127.0.0.1:8080/app/api/health
   # PREPROD_NETRC=/home/dev/.config/applypilot-agent/preprod.netrc
   ```
4. Install the cron line (`crontab -e`): `23 * * * * /srv/ApplyPilot/scripts/agent/run.sh`

## Day to day
- Plan with Claude (`/plan`), then queue it (`/schedule-build <slug> [at 02:00]`). `/schedule-build list` shows the queue and the preprod holder.
- Logs: `~/.local/state/applypilot-agent/runs.log` (one line per event) and `logs/<time>-<slug>.log` (full JSON output).
- When an agent asks a question, reply on the inbox issue. The next hourly tick re-queues the item with your answer.
- Merging or closing the PR on preprod frees the lock. The next tick resets preprod to trunk and deploys the next waiting PR.
