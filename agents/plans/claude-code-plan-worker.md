# Plan: Switch Plan Worker from OpenCode to Claude Code
**Started:** 2026-10-01
**Status:** 🔄 In Progress

## Goal
The plan queue worker (`scripts/plan_worker.py`) currently shells out to `opencode run` with free
`opencode/*` models. Those models repeatedly hit "Rate limit exceeded" and exhausted iteration caps
(`llm-rate-limit-mitigation`, `integration-smoke-suite…`, `fix-live-test-failures` all ended
`max_iterations_exceeded`; `gemini-2-5-flash-lite-migration` ended `error_exit_1`). The user now has a
Claude subscription, which covers headless Claude Code (`claude -p`). After this plan, the worker
runs build sessions with Claude Code by default, OpenCode stays available behind a flag, and the
agent docs no longer assume a small free model.

## Success Criteria
1. `./scripts/plan_worker.py --dry-run` prints a `claude -p …` command by default.
2. `./scripts/plan_worker.py --agent opencode --dry-run` prints the previous `opencode run …` command.
3. `pytest tests/test_plan_worker.py -v` passes (new file).
4. `grep -n "Nemotron" agents/*.md` returns nothing.
5. README "Plan Queue Worker" section documents `--agent` and the Claude Code prerequisite.

## Task Chain

### Task 1: Add Claude Code agent runner to plan_worker
**Files:** `scripts/plan_worker.py` (modify), `tests/test_plan_worker.py` (new)
**What:** Split `run_agent()` into `_build_opencode_cmd(model) -> list[str]` (existing behavior) and
`_build_claude_cmd(model) -> list[str]`, which returns
`["claude", "-p", "--model", model, "--permission-mode", "bypassPermissions", "--output-format", "text"]`
(verify each flag against `claude --help` before writing; drop any that don't exist). The prompt
is still piped on stdin. Add `--agent {claude,opencode}` (default `claude`) to argparse and store it in
the queue state as `"agent"`. When agent is `claude`, the default model is `claude-opus-5-5` and
`MODEL_FALLBACKS` is not used (keep it only for opencode). Detect the subscription usage-limit
message in stderr/stdout (case-insensitive `"usage limit"` or `"rate limit"`). On a match, log it,
**don't** increment `retry_counts`, and exit the loop cleanly so you can resume later. On
`FileNotFoundError`, the error message names the binary that was used.
**Acceptance:**
- `python scripts/plan_worker.py --dry-run` output contains `claude -p`.
- `python scripts/plan_worker.py --agent opencode --dry-run` output contains `opencode run`.
- `pytest tests/test_plan_worker.py -v` covers both command builders and usage-limit detection (no subprocess spawned; pass a fake `subprocess.run`).
**Status:** ❌ Not started

### Task 2: Update agent docs for Claude Code
**Files:** `agents/PLAN_AGENT.md` (modify), `agents/BUILD_AGENT.md` (modify), `AGENTS.md` (modify), `README.md` (modify)
**What:** In PLAN_AGENT.md, replace "One task = one session (Nemotron 3.5 Lightning)" with "One
task = one Claude Code session". Keep the sizing guidance. In AGENTS.md and the PLAN_AGENT
"Plan Worker Invocation" section, change `opencode run --auto` to `claude -p` (with
`--agent opencode` noted as an alternative). Add a pre-flight line to BUILD_AGENT.md: "Read
`agents/ROADMAP.md` and update the plan's row when a task completes." In README's Plan Queue Worker
section, document `--agent`, the prerequisite (`claude` CLI installed and logged in to the
subscription), and that the worker stops cleanly on usage limits.
**Acceptance:**
- `grep -rn "Nemotron" agents/ AGENTS.md` returns nothing.
- `grep -n "\-\-agent" README.md` matches.
- `grep -n "ROADMAP.md" agents/BUILD_AGENT.md` matches.
**Status:** ❌ Not started

## Implementation Order
```
Task 1 ──► Task 2
```
1. Task 1: the code change, so the docs describe real behavior.
2. Task 2: the docs.

## Key Design Decisions
1. Keep OpenCode as an `--agent` option instead of deleting it, so the worker still runs without a Claude login.
2. Hitting the usage limit stops the worker instead of counting as a failure, because subscription limits reset over time and burning retries on them produced the false `max_iterations_exceeded` records seen in `plan_queue.json`.
3. Use `bypassPermissions` to match the old `opencode --auto` full-permission behavior. Build agents need to run tests and commit.

## Historical Record
- 2026-10-01: Plan created (roadmap initiative R1).
