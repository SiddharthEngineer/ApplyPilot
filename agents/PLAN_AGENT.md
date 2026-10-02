# Planning Agent — ApplyPilot

Produce `agents/plans/<slug>.md` (kebab-case), then add a row to `agents/ROADMAP.md` (Order, ID, initiative,
plan link, `0/N` tasks, ⏳, Depends on). Plans are implemented one task per session by the nightly cloud
routine (see `agents/BUILD_AGENT.md`), so every task must be self-contained.

## Task Segmentation
One task = one Claude Code session. Pick ONE:
- single file (clear responsibility) | single function + tests | one-file modification | CLI flag + wiring
Don't bundle unrelated changes. 12 files → ~6-8 tasks.

Every task declares where it runs:
- `cloud`: verifiable offline with `pytest tests/` (no network, LLM, browser, or `~/.applypilot`).
- `local`: needs network, Gemini, the user's data, or the user's judgment.
- Mixed tasks say both, e.g. `cloud (code + unit tests); live check: local`. Prefer splitting the work so the cloud part is complete on its own.

## Plan Format (exact structure)
```markdown
# Plan: <Title>
**Started:** YYYY-MM-DD
**Status:** 🔄 In Progress | ✅ Complete | ❌ Abandoned
## Goal — 1 paragraph, user-facing outcome
## Success Criteria — numbered, verifiable (testable command/latency)
## Task Chain
### Task N: <Title>
**Runs:** cloud | local | cloud (…); local (…)
**Files:** path (new|modify)
**What:** 1 paragraph, specific approach
**Acceptance:** verifiable bullets
**Status:** ❌ Not started | 🟡 Cloud part done (date), local steps pending | ⏸ Blocked (date): reason | ✅ Complete (date)
## Implementation Order — ASCII graph + numbered list
## Key Design Decisions — numbered, 1 sentence each (why)
## Historical Record — append date+note per completion, never delete
```

## Rules (11)
1. Success criteria before tasks. 2. Every task has `Runs:` and `Status:` lines. 3. Files explicit with (new)/(modify).
4. Acceptance = verifiable commands, no "looks good". 5. Graph + list shows dependencies. 6. Document non-obvious choices.
7. Don't read existing plans for examples — template is self-contained. 8. Match codebase patterns (search before writing).
9. Data models: include `@dataclass`/typed signatures, match style. 10. Be specific.
11. The fork is public: never paste personal data (resume text, contact details, DB contents) into plans. Reference the file instead.
