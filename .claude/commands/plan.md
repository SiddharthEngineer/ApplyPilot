---
description: Plan a feature or fix with the user and write agents/plans/<slug>.md (PIV step 1)
argument-hint: <feature description | GitHub issue # | path to an RCA note>
---

# Plan: $ARGUMENTS

Planning is always done **with the user**. Your output is one plan file that a fresh agent with no memory of this
conversation can implement and validate on its own, unattended.

## 1. Understand (ask, don't assume)
- If `$ARGUMENTS` is an issue number, read it: `gh issue view <n> --repo SiddharthEngineer/ApplyPilot`.
- Search the code for the patterns you'll mirror and read only the files you need. Don't read old plans
  (`agents/plans/done/`) unless the user points you at one.
- Ask the user questions (AskUserQuestion) until the goal, scope, edge cases and "done" are unambiguous.
  Give each question a recommended option. Several short rounds are better than one guess.

## 2. Decide risk with the user
- `low`: may auto-merge to trunk after green validation. Fits docs, tests, small refactors and lint fixes, and scraper or prompt tweaks.
- `review`: a PR that's deployed to preprod and tested by the user. Use it for everything else, and whenever you're unsure.
- `scripts/agent/gate.py` re-checks a `low` plan after the code is written and downgrades it to `review` if the diff
  touches high-risk paths (see `HIGH_RISK` there), goes over the size budget, or weakens tests. Tell the user this.
- After writing the plan (step 3), run `python3 scripts/agent/gate.py agents/plans/<slug>.md --plan`. If a `low` plan
  comes back `review`, tell the user which files caused it (the `touches high-risk path` reasons), then either change
  the risk to `review` or narrow the files.
- Mark a task `Runs: interactive` if it needs the user, prod, engineerfamily, secrets, or live job boards or Gemini.
  Unattended runs skip those tasks and flag them in the PR.

## 3. Write `agents/plans/<slug>.md` (kebab-case)

```markdown
# Plan: <Title>
**Status:** ❌ Not started | 🔄 In progress | ✅ Complete (YYYY-MM-DD)
**Risk:** low | review (one-line reason)
**Issue:** #n (optional)

## Goal
One paragraph: the user-facing outcome.

## Context
- `path/file.py:120-160`: pattern to mirror / code to change (why)
- Gotchas: library quirks, data shapes, things that look wrong but are intentional.

## Tasks
### Task N: <Title>
**Runs:** unattended | interactive (why)
**Files:** path (new|modify)
**What:** the specific approach, with signatures for any new functions or dataclasses
**Validate:** `exact command` → expected result (a failing-first test for bug fixes)
**Status:** ❌ | ✅ (YYYY-MM-DD, commit) | ⏸ (reason)

## Validation
- `scripts/validate.sh --full` passes.
- Feature-specific commands with expected output (CLI runs against fixtures, `curl` against `applypilot serve`, …).

## Human test steps (preprod)
Numbered steps the user follows on preprod: URL or command, what to click or run, what they should see. Write "None (low risk)" if not applicable.

## Decisions
Numbered, one sentence each: the choice and why.
```

Rules: success means a command printed something checkable, never "looks good". Each task is one committed,
testable unit. Never put personal data (resume text, contact details, DB rows) in a plan. Point at the file instead.

## 4. Register and hand off
1. Add a row to the **Active** table in `agents/ROADMAP.md` (ID, initiative, plan link, risk, status ⏳, depends on).
2. Show the user the plan summary, then commit both files on `trunk` and push (`git push origin trunk`), because the unattended
   runner only builds plans that are on `origin/trunk`.
3. Ask the user: implement now in this session (`/implement agents/plans/<slug>.md`), or schedule it
   (`/schedule-build <slug> [time]`)?
