Build agent, VPS mode, unattended run. No human is watching and nobody can answer questions. You have about 5.5 hours.

Follow `agents/BUILD_AGENT.md` §0 (VPS mode, no-PR rule, engineerfamily deploy flow). Your job this run: **complete the next plan.**

1. `cd /srv/ApplyPilot && git status`. If the tree is dirty, a previous run was cut off (usage limit or timeout). Read the diff and
   the plan, then finish that task or `git stash push -m "run-recovery <date>"` and note it in `agents/STATE.md`.
2. `git pull --ff-only`. Read `agents/ROADMAP.md`, `agents/STATE.md` and the top of `agents/MORNING.md`.
3. Your plan is the first R7–R11 initiative in ROADMAP order that isn't ✅ and has a runnable task. In VPS mode, `local` tasks
   count as runnable: you have network, Gemini (keep live LLM use small), Docker, Postgres and the live site. Skip only steps that need
   the user's own judgment.
4. Do that plan's tasks one after another, in its Implementation Order. After **each** task: run the gate (`ruff`, `pytest tests/ -q`, plus
   `npm test` for `dashboard/`), commit to `trunk`, push, and record it in the plan, ROADMAP, STATE and CHANGELOG. If the task touches
   engineerfamily or the dashboard, deploy (BUILD_AGENT §0) and verify it live.
5. Tools: the `playwright` MCP browser (headless) can open `https://applypilot.engineerfamily.net/…` and click through pages. Use it to check
   UI work after deploying. The dashboard's basic-auth credentials are in `/home/dev/applypilot-dashboard-credentials.txt` once R11 creates them.
   Web search and fetch are available for library docs.
6. When the plan is done (or nothing runnable is left in it), prepend a dated section to `agents/MORNING.md`: what landed, how to
   verify, and what's blocked or waiting on the user. Update its **Next session** block, commit and push, and stop. Don't start the next plan:
   the next scheduled run will.

If a task is blocked, mark it `⏸ Blocked (date): reason`, write it in MORNING.md, and continue with any task that doesn't depend on it.
Never print secrets into files, commits or logs. Never force-push, delete Docker volumes or drop databases other than `applypilot_test`.
