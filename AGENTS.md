# AGENTS.md — Dispatcher (auto-loaded every session)

## Global (all agents)
- Don't mark `agents/plans/*.md` tasks complete until acceptance criteria verified.
- End of session: update plan → `agents/STATE.md` → `agents/CHANGELOG.md` → run tests → commit.

## Planning Agent → `agents/PLAN_AGENT.md`
Produce `agents/plans/<slug>.md` (kebab-case). Follow its template + 10 rules.

## Build Agent → `agents/BUILD_AGENT.md`
Implement ONE queued task from `agents/plans/**` per session. Follow its checklist. Worker: `scripts/plan_worker.py` (`opencode run --auto`).

**Never** `@`-mention `scripts/plan_worker.py` from within a plan-mode session. The worker is a standalone script to run in a terminal — it spawns build agents via `opencode run --auto`.

## New role → `agents/<ROLE>_AGENT.md`
Add `## If you are a <Role> Agent → read agents/<ROLE>_AGENT.md` above. Don't duplicate global rules.
