# AGENTS.md — Dispatcher (auto-loaded every session)

## Global (all agents)
- `agents/ROADMAP.md` is the single tracker for planned work: order, status, dependencies, manual steps.
- Don't mark `agents/plans/*.md` tasks complete until acceptance criteria are verified.
- End of session: update plan → `agents/ROADMAP.md` → `agents/STATE.md` → `agents/CHANGELOG.md` → run tests → commit.
- This repo is a public fork (`origin` = SiddharthEngineer/ApplyPilot). PRs go to `origin` with `--repo SiddharthEngineer/ApplyPilot`, never to `upstream`. Never commit personal data.

## Planning Agent → `agents/PLAN_AGENT.md`
Produce `agents/plans/<slug>.md` (kebab-case) and add it to `agents/ROADMAP.md`. Follow its template + rules.

## Build Agent → `agents/BUILD_AGENT.md`
Implement runnable tasks from `agents/plans/**` one at a time, testing and pushing after each. Normally this runs as the
nightly Claude Code cloud routine "ApplyPilot nightly build" (claude.ai/code/routines), which keeps going until nothing is
runnable or its usage limit ends the session, using stacked `claude/plan-<slug>` branches and draft PRs for morning review. Follow its checklist.

## New role → `agents/<ROLE>_AGENT.md`
Add `## <Role> Agent → agents/<ROLE>_AGENT.md` above. Don't duplicate global rules.
