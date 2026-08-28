# Planning Agent — ApplyPilot

Produce `agents/plans/<slug>.md` (kebab-case). Never write to `.opencode/plans/`.

## Task Segmentation
One task = one session (Nemotron 3.5 Lightning). Pick ONE:
- single file (clear responsibility) | single function + tests | one-file modification | CLI flag + wiring
Don't bundle unrelated changes. 12 files → ~6-8 tasks.

## Plan Format (exact structure)
```markdown
# Plan: <Title>
**Started:** YYYY-MM-DD
**Status:** 🔄 In Progress | ✅ Complete | ❌ Abandoned
## Goal — 1 paragraph, user-facing outcome
## Success Criteria — numbered, verifiable (testable command/latency)
## Task Chain
### Task N: <Title>
**Files:** path (new|modify)
**What:** 1 paragraph, specific approach
**Acceptance:** verifiable bullets
**Status:** ✅ Complete (date) | ❌ Not started
## Implementation Order — ASCII graph + numbered list
## Key Design Decisions — numbered, 1 sentence each (why)
## Historical Record — append date+note per completion, never delete
```

## Rules (10)
1. Success criteria before tasks. 2. Every task has `Status:` line. 3. Files explicit with (new)/(modify). 4. Acceptance = verifiable commands, no "looks good". 5. Graph + list shows dependencies. 6. Document non-obvious choices. 7. Don't read existing plans for examples — template is self-contained. 8. Match codebase patterns (search before writing). 9. Data models: include `@dataclass`/typed signatures, match style. 10. Be specific.


## Important: Plan Worker Invocation
- **Never** `@`-mention `scripts/plan_worker.py` from within a plan-mode session.
- The worker is a standalone script to run in a terminal: `./scripts/plan_worker.py`
- It spawns build agents via `opencode run --auto` — those agents have full permissions.
