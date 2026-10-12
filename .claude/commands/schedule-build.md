---
description: Queue a plan for an unattended build on the VPS, or show and manage the queue
argument-hint: <slug> [at <time> | in <3h>] | list | cancel <slug> | answer <slug> <text>
---

The queue is machine-local (`~/.local/state/strelitzia-agent/queue.json`, or the legacy `applypilot-agent` dir until M15). Cron runs `scripts/agent/run.sh` every 5 minutes. It
starts Claude only when an item is due, there's no pause, the item has used fewer than 2 attempts, and the Claude
"Current session" usage is below `AGENT_MAX_SESSION_USAGE` (default 70%; above it the queue pauses until the window resets).

Arguments: `$ARGUMENTS`

- **`list`** (or no arguments): `python3 scripts/agent/buildqueue.py list`, plus `scripts/agent/preprod.sh status`. Summarize it for the user.
- **`cancel <slug>`**: `python3 scripts/agent/buildqueue.py cancel <slug>`.
- **`answer <slug> <text>`**: `python3 scripts/agent/buildqueue.py answer <slug> "<text>"`. Normally the user just
  replies on the inbox issue and the runner picks it up.
- **`<slug> [at|in …]`**:
  1. Check that `agents/plans/<slug>.md` exists, has a `**Risk:**` line, and has at least one `Runs: unattended` task that isn't ✅.
     If not, stop and say why (suggest `/plan`).
  2. Check that the plan is committed and pushed: `git fetch -q origin && git cat-file -e origin/trunk:agents/plans/<slug>.md`.
     If it isn't, offer to commit and push it.
  3. Convert the time to `--at YYYY-MM-DDTHH:MM` (VPS local time) or `--in 3h`. Ask if it's ambiguous.
  4. `python3 scripts/agent/buildqueue.py add <slug> [--at … | --in …]`.
  5. Tell the user when the first eligible cron tick is (every 5 minutes), and that low-risk plans may merge on their own while review
     plans end as a PR on preprod with an ntfy push.
- If `crontab -l` doesn't contain `scripts/agent/run.sh`, warn the user that the runner isn't installed yet (see `scripts/agent/README.md`).
