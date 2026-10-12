---
description: Root-cause a bug with the user, then turn it into a fix plan
argument-hint: <GitHub issue # | bug description>
---

# Root cause: $ARGUMENTS

1. **Gather:** read the issue (`gh issue view <n> --repo SiddharthEngineer/Strelitzia`) or ask the user for symptoms,
   when it started, and the inputs that trigger it. Check `git log -S`/`git log -- <file>` for recent changes nearby.
2. **Reproduce:** write the smallest failing test you can in `tests/` (hermetic: mock the network, or use `tests/data/`).
   Run it and show that it fails for the reason you expect. If you can't reproduce it, say so and ask the user for more
   before going on.
3. **Explain:** trace the cause to specific lines (`path:line`). Separate the root cause from the symptoms.
   Check for the same bug pattern elsewhere (`grep`).
4. **Hand off to planning:** run `/plan` with what you found. Task 1 of the plan is the failing test (it should already
   exist on the branch or be written out in full), and the fix tasks follow it. Assign risk with the user as usual.

Keep the RCA in the plan's **Context** section. Don't write separate RCA files.
