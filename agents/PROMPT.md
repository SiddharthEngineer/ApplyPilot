You are either starting or continuing an existing implementation task.

Before doing anything:

1. Read @AGENTS.md.
2. Read the specified plan in @agents/plans/**
3. Read @agents/STATE.md
4. Inspect git status and recent commits.
5. Determine the highest-priority unfinished unit of work.

Then:

- Implement ONE coherent unit of work.
- Do not redo work marked complete in STATE.md.
- Run the relevant tests after implementation.
- Fix failures caused by your changes.
- Update @agents/STATE.md with:
  - what was completed
  - what remains
  - current task
  - test results
  - important decisions
  - blockers
  - exact recommended next step
- Update @agents/CHANGELOG.md with a concise historical entry.
- Update @README.md / @CONTRIBUTING.md / etc. when the implementation makes them inaccurate.
- Leave the repository in a clean, coherent state.
- Commit the completed work.

If context is becoming constrained, do not begin another substantial task.
Finish the current coherent unit of work, update STATE.md, and stop.

Do not claim work is complete unless the relevant tests or verification have actually passed.
Once it is actually complete though, please update the status to reflect this.
