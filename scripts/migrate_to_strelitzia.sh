#!/usr/bin/env bash
# Post-merge VPS migration from the ApplyPilot names to Strelitzia (ROADMAP M15). Run it by hand, as the dev user.
#
#   scripts/migrate_to_strelitzia.sh             # dry run (default): print every action, change nothing
#   scripts/migrate_to_strelitzia.sh --apply     # perform them
#
# Idempotent: each step is skipped when it's already done, so it can be rerun after a partial run.
# Every moved directory leaves a symlink at its old path, because DB rows, git worktrees, the runner's env file and the
# engineerfamily compose still hold the old absolute paths. Steps that need root (/srv, /var/log) are only printed.
#
# Test hooks: MIGRATE_SRV (default /srv), MIGRATE_VARLOG (default /var/log), CRONTAB (default crontab).
set -uo pipefail

MODE=dry-run
for arg in "$@"; do
    case "$arg" in
        --dry-run) MODE=dry-run ;;
        --apply) MODE=apply ;;
        -h|--help) sed -n '2,11p' "$0"; exit 0 ;;
        *) echo "unknown option: $arg" >&2; exit 2 ;;
    esac
done

HERE=$(cd "$(dirname "$0")" && pwd)
SRV=${MIGRATE_SRV:-/srv}
VARLOG=${MIGRATE_VARLOG:-/var/log}
CRONTAB=${CRONTAB:-crontab}
FETCH_URL=https://github.com/SiddharthEngineer/Strelitzia.git
PUSH_URL=git@github-strelitzia:SiddharthEngineer/Strelitzia.git

changes=0
step() { echo; echo "== $*"; }
skip() { echo "   ok: $*"; }
# act <description> <command...>: print, and run it with --apply.
act() {
    local what=$1; shift
    changes=$((changes + 1))
    if [ "$MODE" = apply ]; then
        echo "   do: $what"
        "$@" || { echo "FAILED: $what" >&2; exit 1; }
    else
        echo "   would: $what"
    fi
}

# move_with_link <old> <new>: move a directory and leave a symlink at the old path.
move_with_link() {
    local old=$1 new=$2
    if [ -L "$old" ]; then
        if [ "$(readlink -f "$old")" = "$(readlink -f "$new")" ]; then skip "$old -> $new"; else
            echo "   WARN: $old is a symlink to $(readlink "$old"), not $new; left alone"; fi
    elif [ -e "$old" ] && [ -e "$new" ]; then
        echo "   WARN: both $old and $new exist; merge them by hand, then rerun"
    elif [ -e "$old" ]; then
        act "mv $old $new" mv "$old" "$new"
        act "ln -s $new $old" ln -s "$new" "$old"
    elif [ -e "$new" ]; then
        skip "$new (no $old)"
    else
        skip "neither $old nor $new exists"
    fi
}

echo "Strelitzia migration ($MODE)"

step "1. Runner idle"
state=$HOME/.local/state/strelitzia-agent
[ -e "$state" ] || state=$HOME/.local/state/applypilot-agent
# Read-only checks: `preprod.sh status` could release the lock and redeploy, so read the lock file instead.
# A dry run only warns, so the plan can be reviewed while the runner is busy.
busy=
if ! queue=$(STRELITZIA_AGENT_STATE=$state python3 "$HERE/agent/buildqueue.py" list 2>&1); then
    busy="can't read the build queue (buildqueue.py list: $(printf '%s\n' "$queue" | tail -n1))."
elif printf '%s\n' "$queue" | grep -q '^running '; then
    busy="a build is running (buildqueue.py list). Rerun when it finishes."
elif [ -f "$state/preprod.lock" ]; then
    busy="preprod is held by PR #$(cut -f1 "$state/preprod.lock") ($state/preprod.lock). Merge or close it (the next runner tick releases the lock), then rerun."
fi
if [ -z "$busy" ]; then
    skip "no running build, preprod lock free"
elif [ "$MODE" = apply ]; then
    echo "refusing: $busy" >&2
    exit 1
else
    echo "   WARN: --apply would refuse now: $busy"
fi

step "2. Data dirs"
move_with_link "$HOME/.applypilot" "$HOME/.strelitzia"
move_with_link "$HOME/.applypilot-preprod" "$HOME/.strelitzia-preprod"

step "3. Runner config and state"
move_with_link "$HOME/.config/applypilot-agent" "$HOME/.config/strelitzia-agent"
move_with_link "$HOME/.local/state/applypilot-agent" "$HOME/.local/state/strelitzia-agent"

step "4. Dev clone, remotes, SSH alias, Claude memory"
repo=$HOME/src/Strelitzia
move_with_link "$HOME/src/ApplyPilot" "$repo"
git_dir=$repo
[ -e "$repo" ] || git_dir=$HOME/src/ApplyPilot  # dry run: not moved yet
if ! git -C "$git_dir" rev-parse --git-dir >/dev/null 2>&1; then
    skip "no git clone at $git_dir"
else
    if [ "$(git -C "$git_dir" remote get-url origin 2>/dev/null)" = "$FETCH_URL" ]; then skip "origin fetch URL"; else
        act "git remote set-url origin $FETCH_URL" git -C "$git_dir" remote set-url origin "$FETCH_URL"; fi
    if [ "$(git -C "$git_dir" remote get-url --push origin 2>/dev/null)" = "$PUSH_URL" ]; then skip "origin push URL"; else
        act "git remote set-url --push origin $PUSH_URL" git -C "$git_dir" remote set-url --push origin "$PUSH_URL"; fi
fi
ssh_config=$HOME/.ssh/config
if grep -q '^Host github-strelitzia$' "$ssh_config" 2>/dev/null; then
    skip "Host github-strelitzia in $ssh_config"
elif grep -q '^Host github-applypilot$' "$ssh_config" 2>/dev/null; then
    # Same deploy key as github-applypilot: copy that block under the new alias.
    block=$(awk '/^(Host|Match) /{inside = ($0 == "Host github-applypilot")} inside && !/^(Host|Match) /' "$ssh_config")
    act "append Host github-strelitzia (same key as github-applypilot) to $ssh_config" \
        bash -c '[ -z "$(tail -c1 "$2")" ] || echo >> "$2"; printf "Host github-strelitzia\n%s\n" "$1" >> "$2"' \
        _ "$block" "$ssh_config"
else
    echo "   WARN: no Host github-applypilot in $ssh_config; add a github-strelitzia alias by hand"
fi
projects=$HOME/.claude/projects
old_mem=$projects/${HOME//\//-}-src-ApplyPilot
new_mem=$projects/${HOME//\//-}-src-Strelitzia
if [ -e "$new_mem" ]; then skip "$new_mem"; elif [ -d "$old_mem" ]; then
    act "cp -a $old_mem $new_mem" cp -a "$old_mem" "$new_mem"; else skip "no Claude project dir at $old_mem"; fi

step "5. Crontab"
old_cron="$HOME/src/ApplyPilot/scripts/agent/run.sh"
new_cron="$HOME/src/Strelitzia/scripts/agent/run.sh"
current=$($CRONTAB -l 2>/dev/null || true)
if printf '%s\n' "$current" | grep -qF "$old_cron"; then
    act "crontab: $old_cron -> $new_cron" \
        bash -c 'printf "%s\n" "${1//"$2"/"$3"}" | $4 -' _ "$current" "$old_cron" "$new_cron" "$CRONTAB"
else
    skip "no crontab line uses $old_cron"
fi

step "6. Dashboard credentials file"
old_cred=$HOME/applypilot-dashboard-credentials.txt
new_cred=$HOME/strelitzia-dashboard-credentials.txt
if [ -e "$old_cred" ] && [ ! -e "$new_cred" ]; then act "mv $old_cred $new_cred" mv "$old_cred" "$new_cred"
elif [ -e "$old_cred" ]; then echo "   WARN: both $old_cred and $new_cred exist; left alone"
else skip "$new_cred"; fi

step "7. Root-owned paths (run these yourself; this script never uses sudo)"
root_lines=()
for pair in "$SRV/ApplyPilot-prod:$SRV/Strelitzia-prod" "$SRV/ApplyPilot-preprod:$SRV/Strelitzia-preprod" \
            "$VARLOG/applypilot-agent:$VARLOG/strelitzia-agent"; do
    old=${pair%%:*} new=${pair#*:}
    if [ -e "$old" ] && [ ! -L "$old" ] && [ ! -e "$new" ]; then
        root_lines+=("sudo mv $old $new && sudo ln -s $new $old")
    fi
done
if [ ${#root_lines[@]} -eq 0 ]; then skip "nothing to move"; else printf '   %s\n' "${root_lines[@]}"; fi

echo
if [ "$MODE" = dry-run ]; then
    echo "Dry run: $changes change(s) planned. Rerun with --apply to make them."
else
    echo "Done: $changes change(s). Later (M16): point the runner env file and the engineerfamily compose at the new paths,"
    echo "then the old-path symlinks can go."
fi
