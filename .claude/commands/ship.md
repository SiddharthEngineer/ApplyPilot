---
description: Ship finished work in an interactive session, through the risk gate (merge or PR plus preprod)
argument-hint: [agents/plans/<slug>.md]
---

Use this when you've finished a plan interactively. Unattended runs ship through `scripts/agent/run.sh` instead.

1. Make sure the work is on a branch (not `trunk`): `git switch -c agent/<slug>` if needed.
2. `scripts/validate.sh --full`. Stop if anything fails.
3. Push the branch and open a PR to `trunk` on `SiddharthEngineer/ApplyPilot`, following `.github/pull_request_template.md`.
   Copy the plan's **Human test steps** verbatim, and include the real validation output.
4. `python3 scripts/agent/gate.py <plan>`. Show the user the JSON verdict.
   - `low`: ask the user whether to merge now: wait for green checks (`gh pr checks <n> --watch`), then
     `gh pr merge <n> --rebase --delete-branch`.
   - `review`: `scripts/agent/preprod.sh deploy <n>`. If preprod is busy, tell the user who holds it and add the
     `awaiting-preprod` label. The cron tick deploys it when the lock is free.
5. Tell the user the PR link and exactly what to test.
