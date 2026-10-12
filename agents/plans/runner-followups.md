# Plan: Runner follow-ups from the first PIV smoke test
**Status:** ❌ Not started
**Risk:** review (both tasks touch `scripts/agent/*`, which `gate.py` treats as high-risk)

## Goal
Two gaps the R14 smoke test exposed in the unattended runner:
- An agent that asked a question resumes its own session after the user answers, instead of rebuilding about 23k tokens of context.
- `/plan` catches a `low` plan that lists high-risk files while the user is still planning.

PR #24 (2026-10-12) already fixed the preprod health check under cron, replaced the daily run cap with a session-usage gate,
and changed the docs to the 5-minute cron, so those are out of scope here.

## Context
- **Run logs (Task 1).** `run.sh:71-77` writes `claude -p --output-format json` to `$log`, and the final JSON object carries `session_id`.
  `run.sh:92` then *appends* `validate.sh --full` output to the same `$log`, so the JSON is not always the last line.
  Read `session_id` right after `claude` exits (line 78), before anything else is appended.
- **Session storage.** Claude stores sessions per cwd (`~/.claude/projects/-home-dev--local-state-applypilot-agent-worktrees-<slug>/`).
  `--resume` only finds the session when run from the same worktree path. The worktree survives `waiting-answer`, because
  `run.sh:124-129` removes it only on `done` or `awaiting-preprod`, so the path matches.
- **Queue.** `buildqueue.py` `cmd_answer` re-queues an item with `attempts=0`. Tests live in `tests/test_agent_runner.py` (fixture `q` runs the script with a temp `APPLYPILOT_AGENT_STATE`). Re-read `run.sh` and `buildqueue.py` first; PR #24 changed both.
- **Gate (Task 2).** `scripts/agent/gate.py`: `HIGH_RISK` patterns and `matches()`. Plans list files as
  `**Files:** path (new|modify), path (modify)` (see `.claude/commands/plan.md` step 3). Mirror the `repo` fixture tests at `tests/test_agent_runner.py:76-130`.
## Tasks
### Task 1: Resume the agent's session after an answer
**Runs:** unattended
**Files:** scripts/agent/run.sh (modify), scripts/agent/buildqueue.py (modify), tests/test_agent_runner.py (modify)
**What:**
- After `claude` exits, `run.sh` reads `session_id` from the JSON in `$log` (the last line that parses as JSON with that key)
  and stores it with `buildqueue.py set <slug> session_id=<id>`.
- `cmd_answer` sets `resume=1` on the item. Every other path that re-queues an item (`finish` after a failed attempt, `add`) clears `resume` and `session_id`.
- When `resume=1` and `session_id` is set, `run.sh` runs `claude -p --resume <id> "<prompt>"`, keeping the same flags. The
  prompt is short: the user's latest answer, the result-file path, and "continue the plan from where you stopped".
- If the resumed run exits non-zero without writing a result file, and the log shows the session wasn't found, `run.sh` immediately
  re-runs fresh with the normal prompt, in the same tick and attempt, and logs `resume failed, fresh run`.
- `run.sh` clears `resume` after either path.
**Validate:** queue tests: `answer` sets `resume=1`; a failed-attempt `finish` clears it and `session_id`. `bash -n scripts/agent/run.sh` passes.
`scripts/validate.sh` → ALL PASS. The live check is the human step below.
**Status:** ❌

### Task 2: `gate.py --plan` and a check in `/plan`
**Runs:** unattended
**Files:** scripts/agent/gate.py (modify), .claude/commands/plan.md (modify), tests/test_agent_runner.py (modify)
**What:** Add `planned_files(plan: Path) -> list[str]`, which collects the paths from every `**Files:**` line (strip the `(new|modify)` suffixes and
split on commas). Add `evaluate_plan(plan: Path) -> dict`, which returns `{"tier", "reasons"}` from the declared risk plus `touches high-risk path X` for each planned
file that matches `HIGH_RISK`. `gate.py <plan> --plan` prints that JSON and uses the same exit codes, without git. In `.claude/commands/plan.md` step 2, add:
after writing the plan, run `python3 scripts/agent/gate.py agents/plans/<slug>.md --plan`; if a `low` plan comes back `review`, tell the
user which files caused it and either change the risk to `review` or narrow the files.
**Validate:** tests: a `low` plan listing `src/applypilot/web/tasks.py (modify)` → `review` with that reason; a `low` plan listing only
`tests/` and `*.md` → `low`. `python3 scripts/agent/gate.py agents/plans/runner-followups.md --plan` → `review`. `scripts/validate.sh` → ALL PASS.
**Status:** ❌

## Validation
- `scripts/validate.sh --full` → ALL PASS.
- `.venv/bin/pytest tests/test_agent_runner.py -q` → all pass, including the new tests from Tasks 1 and 2.
- `python3 scripts/agent/gate.py agents/plans/runner-followups.md --plan` → `"tier": "review"`.

## Human test steps (preprod)
Preprod doesn't run the runner, so these happen on the VPS after merge:
1. Pull trunk in `~/src/ApplyPilot` (the crontab runs `run.sh` from there).
2. Write a tiny `low` plan with `/plan` that asks one inbox question, then `/schedule-build` it.
3. When the question arrives, reply on the issue. Within 5 minutes, `runs.log` shows the run starting, and that run's log shows
   `--resume` with the same `session_id` as the first run (`grep session_id ~/.local/state/applypilot-agent/logs/*-<slug>.log`).

## Decisions
1. Resume only after an answer. A failed attempt starts fresh, because its context is the thing that went wrong.
2. A resume that can't find its session falls back to a fresh run in the same tick, rather than failing the attempt.
