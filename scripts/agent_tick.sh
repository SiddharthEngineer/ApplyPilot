#!/usr/bin/env bash
# Unattended build-agent run: completes the next plan in agents/ROADMAP.md (prompt: agents/TICK_PROMPT.md).
# Installed in the `dev` user's crontab every 6 hours (UTC):
#   7 1,7,13,19 * * * /srv/ApplyPilot/scripts/agent_tick.sh
# Logs: /var/log/applypilot-agent/ticks.log (one line per run) and <UTC timestamp>.log (full agent output).
set -uo pipefail
export HOME=/home/dev
export PATH=/home/dev/.local/bin:/usr/local/bin:/usr/bin:/bin
# Long-lived Claude auth for cron, from `claude setup-token`: a mode-600 file containing
#   export CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-...
[ -f /home/dev/.agent_env ] && . /home/dev/.agent_env

REPO=/srv/ApplyPilot
LOG_DIR=/var/log/applypilot-agent
PLANS=(job-store-postgres job-posting-extraction dashboard-api dashboard-ui dashboard-deploy)
MAX_RUNTIME=5h45m   # finish before the next run starts
mkdir -p "$LOG_DIR"
stamp() { date -u +%FT%TZ; }

exec 9>/run/lock/applypilot-agent.lock
flock -n 9 || { echo "$(stamp) previous run still going, skipped" >> "$LOG_DIR/ticks.log"; exit 0; }

cd "$REPO" || exit 1
files=()
for p in "${PLANS[@]}"; do files+=("agents/plans/$p.md"); done
if ! grep -q '❌ Not started' "${files[@]}"; then
    echo "$(stamp) nothing queued" >> "$LOG_DIR/ticks.log"
    exit 0
fi

log="$LOG_DIR/$(date -u +%Y%m%dT%H%MZ).log"
echo "$(stamp) run start -> $log" >> "$LOG_DIR/ticks.log"

# Runs as `dev` (non-root), so permission checks are skipped; scripts/agent/settings.json still applies its deny rules.
timeout --kill-after=2m "$MAX_RUNTIME" claude -p "$(cat agents/TICK_PROMPT.md)" \
    --model claude-opus-5-5 \
    --settings scripts/agent/settings.json \
    --mcp-config scripts/agent/mcp.json \
    --dangerously-skip-permissions \
    --add-dir /srv/engineerfamily /home/dev/.applypilot \
    >> "$log" 2>&1
code=$?
echo "$(stamp) run end (exit $code)" >> "$LOG_DIR/ticks.log"
