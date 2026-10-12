#!/usr/bin/env bash
# Cron entry point for unattended builds. Installed every 5 minutes in the `dev` crontab:
#   */5 * * * * /home/dev/src/ApplyPilot/scripts/agent/run.sh
# Everything before `claude -p` is plain bash/Python, so an empty queue costs no Claude usage.
#
# 1. Upkeep: pick up your inbox replies, release/advance the preprod lock.
# 2. `buildqueue.py next` picks a due item (honours pause, optional daily cap, attempt limit) or we exit.
#    Then usage.py checks the Claude "Current session" usage (one tiny Haiku probe): at or above
#    AGENT_MAX_SESSION_USAGE (default 70%) the queue pauses until the 5-hour window resets.
# 3. The agent runs `/implement <plan> --unattended` in its own worktree on branch agent/<slug>, validates,
#    pushes and opens a PR, then writes a result file. It cannot merge.
# 4. This script decides the merge: gate.py (plan risk + diff checks) and green CI → rebase-merge;
#    otherwise the PR waits for preprod (preprod.sh) and you.
set -uo pipefail
export HOME=${HOME:-/home/dev}
export PATH=$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin
# CLAUDE_CODE_OAUTH_TOKEN (from `claude setup-token`), AGENT_REPO, AGENT_INBOX_REPO, NTFY_TOPIC, PREPROD_*.
[ -f "$HOME/.config/applypilot-agent/env" ] && . "$HOME/.config/applypilot-agent/env"

HERE=$(cd "$(dirname "$0")" && pwd)
AGENT_REPO=${AGENT_REPO:-$(cd "$HERE/../.." && pwd)}
STATE=${APPLYPILOT_AGENT_STATE:-$HOME/.local/state/applypilot-agent}
GH_REPO=${GH_REPO:-SiddharthEngineer/ApplyPilot}
MAX_RUNTIME=${AGENT_MAX_RUNTIME:-3h}
export APPLYPILOT_AGENT_STATE=$STATE GH_REPO
mkdir -p "$STATE/logs" "$STATE/results" "$STATE/worktrees"
Q=(python3 "$HERE/buildqueue.py")
stamp() { date -u +%FT%TZ; }
say() { echo "$(stamp) $*" >> "$STATE/runs.log"; }

exec 9>"$STATE/run.lock"
flock -n 9 || exit 0

[ -n "${AGENT_INBOX_REPO:-}" ] && python3 "$HERE/inbox.py" poll >> "$STATE/runs.log" 2>&1
"$HERE/preprod.sh" tick >> "$STATE/runs.log" 2>&1

slug=$("${Q[@]}" next) || exit 0

gate=$(python3 "$HERE/usage.py" check 2>&1)
case $? in
    0) ;;
    10) "${Q[@]}" pause "$(echo "$gate" | tail -n1)" >> "$STATE/runs.log"
        say "$slug: not started, $(echo "$gate" | head -n1); queue paused until the window resets"
        exit 0 ;;
    *) say "$slug: not started, $(echo "$gate" | head -n1)"
       exit 0 ;;
esac

notify() { [ -n "${AGENT_INBOX_REPO:-}" ] && python3 "$HERE/inbox.py" notify --slug "$slug" --title "$1" --body "$2" >/dev/null; }

git -C "$AGENT_REPO" fetch -q origin
if ! git -C "$AGENT_REPO" cat-file -e "origin/trunk:agents/plans/$slug.md" 2>/dev/null; then
    "${Q[@]}" set "$slug" status=blocked
    say "$slug: agents/plans/$slug.md is not on origin/trunk, blocked"
    notify "plan not on trunk" "Commit and push agents/plans/$slug.md, then re-queue it with /schedule-build $slug."
    exit 0
fi

wt=$STATE/worktrees/$slug
branch=agent/$slug
if [ ! -d "$wt" ]; then
    if git -C "$AGENT_REPO" ls-remote --exit-code -q origin "$branch" >/dev/null; then
        git -C "$AGENT_REPO" worktree add -q -B "$branch" "$wt" "origin/$branch"
    else
        git -C "$AGENT_REPO" worktree add -q -B "$branch" "$wt" origin/trunk
    fi
fi

"${Q[@]}" start "$slug"
result=$STATE/results/$slug.json
log=$STATE/logs/$(date -u +%Y%m%dT%H%MZ)-$slug.log
rm -f "$result"
notes=$("${Q[@]}" get "$slug" notes 2>/dev/null || echo "[]")
say "$slug: run start (log $log)"

prompt="/implement agents/plans/$slug.md --unattended

Result file: $result
Notes from the queue (including the user's answers to earlier questions): $notes"

(
    cd "$wt" || exit 1
    AGENT_SLUG=$slug AGENT_RESULT_FILE=$result timeout --kill-after=2m "$MAX_RUNTIME" claude -p "$prompt" \
        --model claude-opus-5-5 \
        --settings "$wt/scripts/agent/settings.json" \
        --mcp-config "$wt/scripts/agent/mcp.json" \
        --permission-mode auto --permission-prompts none \
        --output-format json
) > "$log" 2>&1
code=$?
say "$slug: claude exit $code"

# A usage-limit exit isn't the plan's fault: give the attempt back and pause the queue.
if [ ! -f "$result" ] && grep -qiE 'usage limit|rate.limit|limit reached' "$log"; then
    attempts=$("${Q[@]}" get "$slug" attempts)
    "${Q[@]}" set "$slug" status=queued "attempts=$((attempts - 1))"
    "${Q[@]}" pause 5 >> "$STATE/runs.log"
    say "$slug: usage limit hit, queue paused 5h"
    exit 0
fi

# The agent reports `ready` (PR open, validated) / `question` / `blocked`. Turn `ready` into merged or review.
status=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("status",""))' "$result" 2>/dev/null)
if [ "$status" = ready ] && ! (cd "$wt" && scripts/validate.sh --full >> "$log" 2>&1); then
    say "$slug: agent said ready but validate.sh --full failed; counting a failed attempt"
    rm -f "$result"
    status=""
fi
if [ "$status" = ready ]; then
    pr=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["pr"])' "$result")
    verdict=$(cd "$wt" && python3 scripts/agent/gate.py "agents/plans/$slug.md")
    tier=$(echo "$verdict" | python3 -c 'import json,sys; print(json.load(sys.stdin)["tier"])')
    if [ "$tier" = low ] && timeout 30m gh pr checks "$pr" --repo "$GH_REPO" --watch --fail-fast >/dev/null 2>&1 \
        && gh pr merge "$pr" --repo "$GH_REPO" --rebase --delete-branch >/dev/null; then
        final=merged
        notify "merged PR #$pr" "Low-risk change merged to trunk after green CI: https://github.com/$GH_REPO/pull/$pr"
    else
        final=review
        gh pr edit "$pr" --repo "$GH_REPO" --add-label awaiting-preprod >/dev/null 2>&1 \
            || { gh label create awaiting-preprod --repo "$GH_REPO" --force >/dev/null; gh pr edit "$pr" --repo "$GH_REPO" --add-label awaiting-preprod >/dev/null; }
        gh pr comment "$pr" --repo "$GH_REPO" --body "Risk gate: \`$tier\`
\`\`\`json
$verdict
\`\`\`" >/dev/null
    fi
    python3 - "$result" "$final" <<'EOF'
import json, sys
path, final = sys.argv[1], sys.argv[2]
data = json.load(open(path)); data["status"] = final
json.dump(data, open(path, "w"))
EOF
fi

new=$("${Q[@]}" finish "$slug" "$result")
say "$slug: -> $new"
case "$new" in
    done|awaiting-preprod)
        git -C "$AGENT_REPO" worktree remove --force "$wt" ;;
    blocked)
        notify "blocked after $("${Q[@]}" get "$slug" attempts) attempt(s)" "Log: $log (on the VPS). Last result: $(cat "$result" 2>/dev/null || echo none)" ;;
esac

"$HERE/preprod.sh" tick >> "$STATE/runs.log" 2>&1
exit 0
