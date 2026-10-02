# Build Agent — ApplyPilot

You implement **exactly one task** from `agents/plans/` per session. Usually you're running as the
nightly Claude Code cloud routine, and the user reviews your work the next morning. Work only in this
repo. Never call job boards, LLM APIs, or other external services, and never read `~/.applypilot`.
Cloud sessions can't reach them, and unit tests must not need them.

## 1. Setup
1. `bash scripts/cloud_setup.sh` (skip if running locally with `.venv`).
2. `git fetch origin`. Read `agents/ROADMAP.md` **on `origin/trunk`**, then `agents/STATE.md`.

## 2. Pick the plan (one plan branch at a time)
- If a branch `origin/claude/plan-<slug>` exists **and is not merged into trunk**, check it out and continue that plan.
- Otherwise, take the first initiative in ROADMAP's **Order** column whose status isn't ✅ or ❌, whose
  **Depends on** initiatives are ✅, and that has a runnable task (below). Create `claude/plan-<slug>` from `origin/trunk`.

## 3. Pick the task
A task is runnable if:
- its `**Status:**` is ❌ Not started,
- its `**Runs:**` line includes `cloud`, and
- every task before it in the plan's *Implementation Order* graph is ✅ (or 🟡 when only its local part is pending and your task doesn't need that part).

Pick the first runnable task in Implementation Order. If none is runnable, don't start another plan.
Update the PR description's **Waiting on you** section (local tasks, merge) and stop.

## 4. Do the task
- Implement only that task's cloud part. Match surrounding code style. Add the tests its Acceptance asks for.
- Gate: `ruff check <changed files>` (no new errors) and `pytest tests/ -q` must pass. Until
  `test-tiers-and-qc` Task 1 is ✅, add `--ignore=tests/test_pipeline.py` (it makes live network calls). Until its Task 2 is ✅, also add
  `--deselect tests/test_content_library_e2e.py::TestRunTailoringContentLibrary::test_content_library_not_found`
  `--deselect tests/test_init_wizard.py::TestSetupTraditionalResume::test_pdf_file_copied` (they need a real `~/.applypilot`). Note any exclusions in the PR.
- Acceptance items that need network, an LLM, or user files are **not** yours. Copy them verbatim into the PR's **Morning QC** section.
- If you can't make the gate pass: don't push broken code. Reset to the last good commit, set the task to
  `⏸ Blocked (YYYY-MM-DD): <reason>`, record the blocker in STATE.md and the PR, push that, and stop.

## 5. Record
- Plan file: the task's `**Status:**` becomes `✅ Complete (YYYY-MM-DD)`, or `🟡 Cloud part done (YYYY-MM-DD), local steps pending`
  when its `Runs:` includes `local`. Append a dated line to the plan's Historical Record.
- `agents/ROADMAP.md`: update that initiative's **Tasks** count and **Status** (🔄, or ✅ when every task is ✅).
- `agents/STATE.md` (active plan, done, next step, blockers) and `agents/CHANGELOG.md` under `[Unreleased]`.
- `README.md` if user-facing behavior changed.

## 6. Ship
- Commit with a conventional message (`feat:`/`fix:`/`test:`/`docs:`) naming the plan and task.
- `git push -u origin claude/plan-<slug>`.
- Open a **draft** PR if none exists: `gh pr create --repo SiddharthEngineer/ApplyPilot --base trunk --draft`.
  Always pass `--repo`, because `upstream` points at Pickle-Pixel/ApplyPilot and must never receive PRs. If `gh` is
  unavailable, put the PR title and body in your final message instead.
- The PR body has these sections, and each run updates them:
  - **Tonight**: task done, files changed
  - **Gate**: the pytest summary line and ruff result
  - **Morning QC**: `python scripts/qc.py <slug>` (once it exists), plus live acceptance commands and local tasks, as checkboxes
  - **Waiting on you**: anything blocking the next task
- Stop. One task per session.
