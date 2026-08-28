# Plan: Restore --auto Flag to plan_worker.py

**Started:** 2026-08-28
**Status:** ✅ Complete

## Goal
Restore the `--auto` flag to the opencode command built by `scripts/plan_worker.py` so build agents spawned by the plan worker run autonomously with all permissions enabled, without prompting the user for tool permission.

## Success Criteria
1. The `--auto` flag is present in the command list in `run_agent()` in `scripts/plan_worker.py`
2. The command structure matches the pattern in `src/applypilot/apply/launcher.py:_build_opencode_cmd()`
3. Documentation in `CONTRIBUTING.md` is consistent with the `--auto` flag usage
4. `./scripts/plan_worker.py --dry-run` shows the `--auto` flag in the logged command
5. Existing tests pass (if any exist for `plan_worker.py`)

## Task Chain

### Task 1: Restore --auto flag to plan_worker.py
**Files:** `scripts/plan_worker.py` (modify)
**What:** Add `"--auto"` to the command list in `run_agent()` (`scripts/plan_worker.py:151`), matching the pattern already used by `launcher.py`. The flag is the OpenCode equivalent of Claude Code's `--permission-mode bypassPermissions` and is essential for continuous autonomous operation of the plan worker; without it, spawned agents prompt for permissions and break the loop.
**Acceptance:**
- `"--auto"` appears in the `cmd` list at `scripts/plan_worker.py:156`
- Python syntax check (`python3 -m py_compile scripts/plan_worker.py`) exits 0
- Command structure matches `launcher.py:_build_opencode_cmd()` (`--model`, `--auto`, `--dir`)
**Status:** ✅ Complete (2026-08-28)

### Task 2: Verify consistency with launcher.py
**Files:** `src/applypilot/apply/launcher.py` (read-only verification)
**What:** Verify that the command structure in `plan_worker.py` matches the pattern used in `launcher.py:_build_opencode_cmd()`: both include `--auto`, both use `--model`, and both include `--dir`.
**Acceptance:**
- `launcher.py` includes `--auto` in its opencode command
- `--model` and `--dir` flags match between the two files
**Status:** ✅ Complete (2026-08-28)

### Task 3: Verify documentation in CONTRIBUTING.md
**Files:** `CONTRIBUTING.md` (read-only verification)
**What:** Confirm the documentation already reflects `--auto` flag usage, specifically the line: "Launches `opencode run --auto` with `agents/BUILD_PROMPT.md` + plan path".
**Acceptance:**
- `CONTRIBUTING.md` documents the `--auto` flag in the plan worker command
**Status:** ✅ Complete (2026-08-28)

### Task 4: Test the change
**Files:** (none — verification only)
**What:** Run `./scripts/plan_worker.py --dry-run` to verify the logged command includes `--auto`, and run any existing tests for `plan_worker.py`.
**Acceptance:**
- `./scripts/plan_worker.py --dry-run` exits 0 and logs `--auto` in the command preview (`scripts/plan_worker.py:223`)
- Existing tests pass (if any exist for `plan_worker.py`)
**Status:** ✅ Complete (2026-08-28)

## Implementation Order
```
Task 1 (restore flag) → Task 2 (verify launcher) → Task 3 (verify docs) → Task 4 (test)
```
Task 2 and Task 3 are independent verifications; Task 1 must complete before Task 4.

## Key Design Decisions
1. **Place `--auto` after `--model`** — Matches the existing command pattern used in `launcher.py:_build_opencode_cmd()`.
2. **Single-line change in `cmd` list** — Keeps the change minimal and reviewable rather than refactoring the command builder.
3. **`--auto` is required for autonomous operation** — Without it, spawned agents prompt for permissions, breaking the plan worker's continuous operation.

## Historical Record
- **2026-08-28:** Task 1 completed. Added `"--auto"` to the command list in `scripts/plan_worker.py:run_agent()` at line 156. Python syntax check passed. Tasks 2–4 verified command structure matches `launcher.py:_build_opencode_cmd()`, `CONTRIBUTING.md` already documents `opencode run --auto`, and dry-run mode logs the `--auto` flag.