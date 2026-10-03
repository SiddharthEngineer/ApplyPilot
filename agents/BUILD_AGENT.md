# Build Agent — ApplyPilot

You implement tasks from `agents/plans/` **one at a time, repeatedly, until nothing is runnable or the session's usage limit ends the run**. Usually you're running as the
nightly Claude Code cloud routine, and the user reviews your work the next morning. Work only in this
repo. Never call job boards, LLM APIs, or other external services, and never read `~/.applypilot`.
Cloud sessions can't reach them, and unit tests must not need them.

## 0. VPS mode (overrides §1–§7 where they differ)
Used when a session runs on the user's VPS (`/srv/ApplyPilot`) instead of the cloud routine. The session plan is the
**Next session** block at the top of `agents/MORNING.md`.
- Setup: `. .venv/bin/activate` (create with `uv venv .venv` + the installs in `scripts/cloud_setup.sh`, via `uv pip`).
- Network is allowed: `local` tasks that need job boards or Gemini may be done here, using the real `~/.applypilot`.
  Keep live LLM runs small (free-tier quota). Never commit anything from `~/.applypilot` or `personal/`.
  Tasks needing the user's judgment (sign-offs, proxy purchase, `M*` steps marked for them) stay theirs.
- No stacking: one plan at a time on `claude/plan-<slug>` from current `trunk`, one commit per task.
  When the plan's runnable tasks are done and the gate passes, open a PR (not draft), then
  `gh pr merge <n> --repo SiddharthEngineer/ApplyPilot --rebase --delete-branch` and `git switch trunk && git pull`.
- **No PRs (user, 2026-10-03; applies to R7–R11 and later unless the user says otherwise):** work on `trunk` directly, with one commit per
  task, and push after each task (`git push origin trunk`).
- **engineerfamily (cross-repo tasks; user, 2026-10-03):** commit on `main` in the working clone `/home/dev/src/engineerfamily`
  (`git clone https://github.com/SiddharthEngineer/engineerfamily.git` if it's missing; `git pull` first), then `git push origin main`.
  Never commit directly on `prod`. To deploy (pre-approved, including for testing): in the live checkout `/srv/engineerfamily` (branch `prod`),
  `git -c safe.directory=/srv/engineerfamily fetch origin && git … merge --ff-only origin/main`, then
  `git … push https://github.com/SiddharthEngineer/engineerfamily.git prod`, then `chown -R deploy:deploy /srv/engineerfamily` and `make up`.
  Untracked server files (`.env`, `services/nginx/ssl/`, `.htpasswd*`) live only in `/srv/engineerfamily`: edit them there, never commit them.
  No tags, no `make tag-prod`. Afterwards, check that `https://engineerfamily.net`, `umami.engineerfamily.net` and `applypilot.engineerfamily.net`
  return 200. If they don't, revert on `main`, redeploy the same way, and note it in MORNING.md.
- **Unattended runs:** the `dev` user's cron runs `scripts/agent_tick.sh` at 01:07, 07:07, 13:07 and 19:07 UTC. Each run completes one plan (`agents/TICK_PROMPT.md`), with tools
  from `scripts/agent/settings.json` and a headless Playwright browser (`scripts/agent/mcp.json`). Logs are in `/var/log/applypilot-agent/`.
- End of session: prepend a dated section to `agents/MORNING.md` (what landed + PR links, how to verify,
  blocked/waiting on user) and update its **Next session** block. Commit it to trunk via the plan PR.

## 1. Setup
1. `bash scripts/cloud_setup.sh` (skip if running locally with `.venv`).
2. `git fetch origin`. Read `agents/ROADMAP.md` **on `origin/trunk`**, then `agents/STATE.md`.

## 2. Pick the plan (stacked branches)
Plans are worked in ROADMAP **Order**. Each plan gets its own branch `claude/plan-<slug>` and its own draft PR. Plans
can be **stacked**: a later plan's branch is based on the previous unmerged plan branch, so work never waits on a merge.

1. List open draft PRs from `claude/plan-*` branches (`gh pr list --repo SiddharthEngineer/ApplyPilot`). The **stack** is
   those branches in ROADMAP order. The **top** is the last one. If an open PR's base branch has already been merged into
   trunk, retarget it: `gh pr edit <n> --repo SiddharthEngineer/ApplyPilot --base trunk`.
2. Walk the ROADMAP initiatives in order. Skip ✅/❌ ones. For each, find a runnable task (§3), reading the plan file from
   the top of the stack (or `origin/trunk` if the stack is empty). Skip an initiative whose **Depends on** initiatives
   are neither ✅ on trunk nor in the stack with all their `cloud` tasks done.
3. The first initiative with a runnable task is your plan:
   - Its branch is already in the stack: check it out and work there.
   - It isn't: create `claude/plan-<slug>` from the **top of the stack** (or `origin/trunk` if empty). If a remote
     branch with that name exists and is fully merged into trunk (a previous PR for the same plan), delete it first
     (`git push origin --delete claude/plan-<slug>`). Its PR's base is the parent branch, not trunk.
4. If a plan lower in the stack gets new commits after a branch above it was created, merge the lower branch
   into the upper branch before continuing (`git merge --no-edit claude/plan-<lower>`), so the stack stays linear.

## 3. Pick the task
A task is runnable if:
- its `**Status:**` is ❌ Not started,
- its `**Runs:**` line includes `cloud`, and
- every task before it in the plan's *Implementation Order* graph is ✅ (or 🟡 when only its local part is pending and your task doesn't need that part).

Pick the first runnable task in Implementation Order. When a plan has no runnable task left, update its PR's
**Waiting on you** section (local tasks, merge) and go back to §2 for the next plan.

## 4. Do the task
- Implement only that task's cloud part. Match surrounding code style. Add the tests its Acceptance asks for.
- Gate: `ruff check <changed files>` (no new errors) and `pytest tests/ -q` must pass. Until `test-tiers-and-qc` Task 2 is ✅, add
  `--deselect tests/test_content_library_e2e.py::TestRunTailoringContentLibrary::test_content_library_not_found`
  `--deselect tests/test_init_wizard.py::TestSetupTraditionalResume::test_pdf_file_copied` (they need a real `~/.applypilot`). Note any exclusions in the PR.
- Acceptance items that need network, an LLM, or user files are **not** yours. Copy them verbatim into the PR's **Morning QC** section.
- If you can't make the gate pass: don't push broken code. Reset to the last good commit, set the task to
  `⏸ Blocked (YYYY-MM-DD): <reason>`, record the blocker in STATE.md and the PR, push that, and continue with the next runnable task (§7).
  Later tasks that depend on the blocked one aren't runnable.

## 5. Record
- Plan file: the task's `**Status:**` becomes `✅ Complete (YYYY-MM-DD)`, or `🟡 Cloud part done (YYYY-MM-DD), local steps pending`
  when its `Runs:` includes `local`. Append a dated line to the plan's Historical Record.
- `agents/ROADMAP.md`: update that initiative's **Tasks** count and **Status** (🔄, or ✅ when every task is ✅).
- `agents/STATE.md` (active plan, done, next step, blockers) and `agents/CHANGELOG.md` under `[Unreleased]`.
- `README.md` if user-facing behavior changed.

## 6. Ship (after every task, before starting the next)
- Commit with a conventional message (`feat:`/`fix:`/`test:`/`docs:`) naming the plan and task.
- `git push -u origin claude/plan-<slug>`.
- Open a **draft** PR if none exists, with base = parent branch in the stack (or `trunk`):
  `gh pr create --repo SiddharthEngineer/ApplyPilot --base <parent> --draft`, or the GitHub tool if `gh` is unavailable.
  Always target `SiddharthEngineer/ApplyPilot`, because `upstream` points at Pickle-Pixel/ApplyPilot and must never receive PRs.
- Update the PR body **now**, not at the end of the session, since the session can be cut off by its usage limit at any point.
  Sections:
  - **Stack**: `Stacked on #<n>`, or `Base: trunk`. Merge order is bottom first.
  - **Done**: one line per completed task (date), files changed
  - **Gate**: latest pytest summary line and ruff result
  - **Morning QC**: `python scripts/qc.py <slug>` (once it exists), plus live acceptance commands and local tasks, as checkboxes
  - **Waiting on you**: anything blocking the next task in this plan

## 7. Loop
After §6, go back to §2 and take the next runnable task. Context is compacted automatically, and everything you need is in
the repo files and PR bodies. Re-read the plan file before each task instead of relying on memory.

Stop only when one of these happens:
- no initiative has a runnable `cloud` task,
- two tasks in a row end ⏸ Blocked (something systemic is wrong; say so in STATE.md and the top PR),
- the session is ended by its usage limit (nothing to do; everything is already pushed).

Before stopping on your own, post a final summary of all tasks done this session, open PRs in merge order, and what's waiting on the user.
