# Build Agent — ApplyPilot

Starting/continuing a queued task.

## Pre-flight
1. Read `agents/plans/<plan>.md`, `agents/STATE.md`, `git status`/`log`.
2. Pick highest-priority unfinished task.

## Do
- Implement ONE unit. Don't redo `STATE.md` complete.
- Run relevant tests; fix failures you caused.
- Update `agents/STATE.md` (done/remaining/current/tests/decisions/blockers/next step), `agents/CHANGELOG.md`, docs (`README`/`CONTRIBUTING` if inaccurate).
- Leave repo clean; commit.

If context constrained: finish current unit, update `STATE.md`, stop.
Don't claim complete until tests/verification pass; then update status.
