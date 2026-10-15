#!/usr/bin/env bash
# One validation gate for humans, agents, hooks and CI. Run from the repo root (or any worktree).
#
#   scripts/validate.sh            # fast: ruff + hermetic pytest (+ dashboard tests if dashboard/ changed vs trunk)
#   scripts/validate.sh --full     # fast + dashboard tests/build (bundle must be committed) + docker build
#   scripts/validate.sh --preprod  # health check of the deployed preprod service (after scripts/agent/preprod.sh deploy)
#   scripts/validate.sh --setup    # create .venv with uv if missing (worktrees start without one), then run fast
#
# Prints one PASS/FAIL line per step and exits non-zero on the first failure.
set -uo pipefail

MODE=fast
SETUP=0
for arg in "$@"; do
    case "$arg" in
        --full) MODE=full ;;
        --preprod) MODE=preprod ;;
        --setup) SETUP=1 ;;
        -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
        *) echo "unknown option: $arg" >&2; exit 2 ;;
    esac
done

step() {  # step <name> <command...>
    local name=$1; shift
    local out
    if out=$("$@" 2>&1); then
        echo "PASS  $name"
    else
        echo "FAIL  $name"
        echo "$out" | tail -n 60
        exit 1
    fi
}

if [ "$MODE" = preprod ]; then
    # Only curls the deployed service, so no checkout is needed (preprod.sh runs this from cron, whose cwd is $HOME).
    # Settings come from ~/.config/strelitzia-agent/env (the same file as scripts/agent/run.sh).
    [ -f "$HOME/.config/strelitzia-agent/env" ] && . "$HOME/.config/strelitzia-agent/env"
    : "${PREPROD_HEALTH_URL:?PREPROD_HEALTH_URL is not set; preprod is not configured yet (agents/DEPLOY.md, Preprod section)}"
    netrc=()
    [ -n "${PREPROD_NETRC:-}" ] && netrc=(--netrc-file "$PREPROD_NETRC")
    step "preprod health ($PREPROD_HEALTH_URL)" bash -c "curl -fsS --max-time 20 ${netrc[*]} '$PREPROD_HEALTH_URL' | grep -q '\"ok\": *true'"
    exit 0
fi

ROOT=$(git rev-parse --show-toplevel) || exit 2
cd "$ROOT" || exit 2

if [ ! -x .venv/bin/python ]; then
    if [ "$SETUP" = 1 ]; then
        step "venv setup" bash -c 'uv venv -q .venv && uv pip install -q --python .venv/bin/python -e ".[dev,web,jobspy]"'
        # The PDF tests render through headless Chromium (a no-op when this Playwright's build is already cached).
        step "chromium for playwright" .venv/bin/playwright install chromium-headless-shell
    else
        echo "FAIL  no .venv here; rerun with --setup"; exit 1
    fi
fi
PY=.venv/bin/python

step "ruff (pyproject rule set)" "$PY" -m ruff check src tests scripts
step "pytest (hermetic tier)" "$PY" -m pytest tests -q -x -p no:cacheprovider

dashboard_changed() {
    git fetch -q origin trunk 2>/dev/null
    ! git diff --quiet "$(git merge-base HEAD origin/trunk)" -- dashboard/ 2>/dev/null || ! git diff --quiet HEAD -- dashboard/
}

if [ "$MODE" = full ] || dashboard_changed; then
    [ -d dashboard/node_modules ] || step "npm ci" npm ci --prefix dashboard --silent
    step "dashboard tests" npm test --prefix dashboard --silent
fi

if [ "$MODE" = full ]; then
    step "dashboard build" npm run build --prefix dashboard --silent
    # The built bundle is committed under src/strelitzia/web/static; a stale one ships old UI.
    step "dashboard bundle committed" bash -c '[ -z "$(git status --porcelain -- src/strelitzia/web/static)" ]'
    step "docker build" docker build -q -t strelitzia-validate:latest .
fi

echo "ALL PASS ($MODE)"
