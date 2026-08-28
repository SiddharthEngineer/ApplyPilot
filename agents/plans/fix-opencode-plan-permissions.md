# Plan: Fix OpenCode Plan Agent Permissions

**Started:** 2026-08-28
**Status:** ✅ Complete

## Goal
Fix the `opencode.json` permission configuration so the plan agent can read files outside `agents/plans/`, the build agent has full permissions, and documentation clarifies that `scripts/plan_worker.py` must not be `@`-mentioned from within a plan-mode session.

## Success Criteria
1. Plan agent can read any file in the repo (not just `agents/plans/**`)
2. Build agent has unrestricted permissions for autonomous operation
3. `@scripts/plan_worker.py` no longer triggers a deny in plan mode
4. Documentation clarifies the correct way to invoke the plan worker
5. `./scripts/plan_worker.py --dry-run` shows the `--auto` flag and correct command

## Task Chain

### Task 1: Fix opencode.json permission config
**Files:** `opencode.json` (modify)
**What:** Add `read: "allow"` to the plan agent's permission block and add `build` agent with `permission: "allow"`. The current config only defines `edit` rules for the plan agent, which causes the Read tool to be evaluated against the edit-deny rule. Explicitly allowing `read` ensures the plan agent can inspect any file. The build agent (used by `plan_worker.py:147` `run_agent()`) needs unrestricted permissions for autonomous operation.
**Acceptance:**
- `opencode.json` contains `"read": "allow"` under `agent.plan.permission`
- `opencode.json` contains `"build": { "permission": "allow" }` under `agent`
- File validates against `https://opencode.ai/config.json` schema
- `$schema` key preserved
**Status:** ✅ Complete (2026-08-28)

### Task 2: Document plan worker invocation rules
**Files:** `agents/PLAN_AGENT.md` (modify), `AGENTS.md` (modify)
**What:** Add a note that `scripts/plan_worker.py` must never be `@`-mentioned from within a plan-mode session. The worker is a standalone script to be run in a terminal; it spawns build agents via `opencode run --auto`. Including it via `@` in plan mode triggers the edit-deny rule because the plan agent evaluates file access against its sandbox.
**Acceptance:**
- `agents/PLAN_AGENT.md` contains a warning about not `@`-mentioning the worker
- `AGENTS.md` contains a note under the Build Agent section about correct invocation
**Status:** ✅ Complete (2026-08-28)

### Task 3: Verify with dry-run
**Files:** (none — verification only)
**What:** Run `./scripts/plan_worker.py --dry-run` to confirm the logged command includes `--auto` and the plan worker can read its queue. Also verify that `opencode.json` is valid JSON.
**Acceptance:**
- `./scripts/plan_worker.py --dry-run` exits 0 and logs `--auto` in the command
- `python3 -c "import json; json.load(open('opencode.json'))"` exits 0
**Status:** ✅ Complete (2026-08-28)

## Implementation Order
```
Task 1 (opencode.json) → Task 2 (docs) → Task 3 (verify)
```
No dependencies between Task 1 and Task 2; both must complete before Task 3.

## Key Design Decisions
1. **Explicit `read: "allow"` for plan agent** — The plan agent's `edit: "*": "deny"` sandbox was inadvertently blocking Read because some opencode builds evaluate Read against the edit ruleset. Making `read` explicit eliminates ambiguity.
2. **`build: permission: "allow"`** — Build agents spawned by `plan_worker.py:147` need full file access to implement plans. The `--auto` flag (`plan_worker.py:156`) already bypasses interactive prompts, but the permission config must also be permissive.
3. **Plan worker is external** — The worker (`plan_worker.py:199` `worker_loop()`) is a standalone process, not an opencode agent. It should never be `@`-mentioned inside a session.

## Historical Record
- **2026-08-28:** Tasks 1–3 completed. Added `read: "allow"` to plan agent, `build: permission: "allow"` to build agent in `opencode.json`. Added invocation warnings to `PLAN_AGENT.md` and `AGENTS.md`. Dry-run verified `--auto` flag present.
