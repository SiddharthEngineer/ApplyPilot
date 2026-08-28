# Restore --auto Flag to plan_worker.py

## Context
The `--auto` flag was removed from `scripts/plan_worker.py` by a previous agent. This flag is essential for build agents spawned from the plan worker to have all permissions enabled. The `--auto` flag is the OpenCode equivalent of Claude Code's `--permission-mode bypassPermissions`, allowing agents to run autonomously without user confirmation for tool usage.

## Current Issue
The `run_agent()` function in `scripts/plan_worker.py` builds the opencode command without the `--auto` flag:
```python
cmd = [
    "opencode",
    "run",
    "--model",
    model,
    "--dir",
    str(REPO_ROOT),
]
```

This means spawned agents will prompt for permissions, breaking the autonomous operation of the plan worker.

## Plan

### Task 1: Restore --auto flag to plan_worker.py
**File:** `scripts/plan_worker.py` (modify)

**Changes:**
1. Add `"--auto"` to the command list in the `run_agent()` function
2. Ensure the command structure matches the launcher.py pattern

**Before:**
```python
cmd = [
    "opencode",
    "run",
    "--model",
    model,
    "--dir",
    str(REPO_ROOT),
]
```

**After:**
```python
cmd = [
    "opencode",
    "run",
    "--model",
    model,
    "--auto",
    "--dir",
    str(REPO_ROOT),
]
```

### Task 2: Verify consistency with launcher.py
**File:** `src/applypilot/apply/launcher.py` (read-only verification)

Verify that the command structure in `plan_worker.py` matches the pattern used in `launcher.py:_build_opencode_cmd()`:
- Both should include `--auto`
- Both should use `--model` flag
- Both should include `--dir` flag

### Task 3: Update documentation if needed
**File:** `CONTRIBUTING.md` (read-only verification)

Verify that the documentation in CONTRIBUTING.md already reflects the `--auto` flag usage:
- Line 175: "Launches `opencode run --auto` with `agents/BUILD_PROMPT.md` + plan path"

### Task 4: Test the change
**Verification:**
1. Run `./scripts/plan_worker.py --dry-run` to verify the command includes `--auto`
2. Check that the logged command includes the `--auto` flag
3. Run any existing tests for plan_worker.py

## Acceptance Criteria
1. The `--auto` flag is present in the `run_agent()` function command list
2. The command structure matches the pattern in `launcher.py`
3. Documentation in CONTRIBUTING.md is consistent
4. Dry-run mode shows the `--auto` flag in the logged command
5. Existing tests pass (if any exist for plan_worker.py)

## Testing
1. Run `./scripts/plan_worker.py --dry-run` and verify the command includes `--auto`
2. Check logs for the command structure
3. Run any unit tests that might exist for plan_worker.py

## Notes
- The `--auto` flag is critical for autonomous operation of the plan worker
- Without this flag, agents will prompt for permissions, breaking the continuous operation
- This is a simple one-line change but has significant impact on functionality

## Status: ✅ Completed (2026-08-28)

**Implementation:** Added `"--auto"` to the command list in `scripts/plan_worker.py:run_agent()` at line 156. Python syntax check passed. Command structure verified to match `launcher.py:_build_opencode_cmd()`.
