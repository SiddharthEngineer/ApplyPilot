# Plan: Fix CI (test reads an archived plan)
**Status:** ✅ Done 2026-10-12 (fix landed on trunk in 2ef2c7b; trunk CI green, run 38318674104)
**Risk:** low (one-line test path fix, `tests/` only)

## Goal
The "CI" workflow on `trunk` is green again. It has been red since `f5fae69` ("archive R0, R13, R15, R16"), which moved
`agents/plans/test-tiers-and-qc.md` to `agents/plans/done/`.

## Context
- `tests/test_qc_script.py:64-66`: `test_real_plan_has_five_criteria` reads `agents/plans/test-tiers-and-qc.md`, which no longer
  exists, so CI fails with `FileNotFoundError` (run 38313689567). The other 961 tests pass.
- The archived copy `agents/plans/done/test-tiers-and-qc.md` still parses to exactly 5 success criteria with `scripts/qc.py`.
- Gotcha: don't delete the test or relax the `== 5` assertion. That would weaken tests, and `gate.py` checks for it.

## Tasks
### Task 1: Point the test at the archived plan
**Runs:** unattended
**Files:** tests/test_qc_script.py (modify)
**What:** In `test_real_plan_has_five_criteria`, change the path to `... / "agents" / "plans" / "done" / "test-tiers-and-qc.md"`.
Keep the assertion as it is.
**Validate:** `pytest tests/test_qc_script.py -q` → all pass; `scripts/validate.sh` → passes.
**Status:** ✅ 2026-10-12 (already on trunk via 2ef2c7b; verified 7/7 pass, `--full` passes)

## Validation
- `scripts/validate.sh` passes (ruff + full pytest).
- After merge, `gh run list --workflow CI --branch trunk -L 1 --repo SiddharthEngineer/Strelitzia` shows `success`.

## Human test steps (preprod)
None (low risk).

## Decisions
1. Use the archived path rather than an inline fixture. `done/` is the permanent archive, and the test keeps checking a real plan.
