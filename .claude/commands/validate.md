---
description: Run the validation gate and report evidence
argument-hint: [--full | --preprod]
---

Run `scripts/validate.sh $ARGUMENTS` from the repo root (add `--setup` once in a fresh worktree).

Report each step's PASS/FAIL line. On failure, show the relevant output, find the root cause, and either fix it
(if it's in scope of the current work) or say exactly what's broken. Never mark work done on a FAIL.

For behaviour the gate can't see (UI, CLI output, an API response), also run the specific check yourself and paste its
output, e.g. `strelitzia serve --port 8765` plus `curl -s localhost:8765/app/api/health`.
