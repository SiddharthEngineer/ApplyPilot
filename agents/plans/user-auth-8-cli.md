# Plan: User accounts, part 8: admin-only CLI
**Status:** 🚧 In progress
**Risk:** review (removes CLI commands)
**Issue:** #47
**Depends on:** user-auth-7-dashboard

## Goal
The CLI becomes an admin tool: `serve`, `init` (server setup), `doctor`, `db migrate|verify`, and new
`users list|invite|disable|cap` commands. Commands that did per-user work from local files (pipeline runs, tailoring,
workbench, Drive sync, the static HTML view) are removed or refuse with a pointer to the dashboard, since every
per-user action now happens there under a signed-in user.

## Context
- `src/strelitzia/cli.py`: the Typer app. Read its command list first (`grep -n "@app.command\|add_typer\|@.*\.command" src/strelitzia/cli.py`).
  `:366-374` loads profile/resume/library from files; `:700-710` doctor checks profile.json.
- `src/strelitzia/view.py`: static HTML report (single-user). `src/strelitzia/storage/sync.py`: CLI Drive sync.
  `src/strelitzia/pipeline.py`: discover→score pipeline (discovery is off by default since R22).
- `src/strelitzia/users.py` (part 1).
- Tests that call removed commands: `tests/test_cli_drive.py`, `tests/test_cli_workbench.py`, `tests/test_pipeline.py`.
  Delete tests only together with the code they cover; don't weaken tests for code that stays.
- `README.md`: the CLI section.

## Tasks
### Task 1: users commands
**Runs:** unattended
**Files:** src/strelitzia/cli.py (modify), tests/test_cli_users.py (new)
**What:** `strelitzia users list`, `users invite <email> [--cap 5]`, `users disable <email>`, `users cap <email> <usd>`,
using `users.py`. Output is a Rich table.
**Validate:** `pytest tests/test_cli_users.py -q` → passes.
**Status:** ✅ 2026-10-16 (Task 1 commit)

### Task 2: remove per-user commands
**Runs:** unattended
**Files:** src/strelitzia/cli.py (modify), src/strelitzia/view.py (delete), src/strelitzia/storage/sync.py (delete), tests/test_cli_drive.py (modify), tests/test_cli_workbench.py (delete), tests/test_drive_sync.py (delete)
**What:** Remove `run`, `tailor`/`cover`/`workbench`, `drive sync`/`drive auth`, `view`/`dashboard` (static report).
Keep library modules the dashboard uses (pipeline stage functions, scorer, intake). `doctor` checks the DB, the env keys
for auth/Drive/Claude, and drops the profile.json check.
**Validate:** `scripts/validate.sh` → green; `strelitzia --help` lists only serve, init, doctor, db, users.
**Status:** ❌

### Task 3: docs
**Runs:** unattended
**Files:** README.md (modify), AGENTS.md (modify)
**What:** Describe the multi-user dashboard and the admin CLI; AGENTS.md map line for `users.py` and `web/auth.py`.
**Validate:** `grep -n "strelitzia run" README.md` → no output.
**Status:** ❌

## Validation
- `scripts/validate.sh --full` passes.

## Human test steps (preprod)
None beyond the dashboard (the CLI isn't on preprod).

## Decisions
1. The CLI is admin-only (user, 2026-10-16): per-user work happens in the dashboard so it always has a user.
