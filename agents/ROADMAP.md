# ApplyPilot Roadmap

The single tracker for planned work. Each row links to a plan in `agents/plans/`. Finished plans move to `agents/plans/done/`,
and their history is in git and PRs. Workflow: see the PIV loop in `AGENTS.md`.

Status: ⏳ queued · 🔄 in progress · 🟡 code done, user steps pending · ⏸ blocked · ✅ done (then archived)

## Active

| ID | Initiative | Plan | Risk | Status | Depends on |
|---|---|---|---|---|---|
| — | Nothing active. R0, R13, R15, R16 merged 2026-10-12 (PRs #31, #32, #30, #28) and archived in `plans/done/`. | | | | |

## Manual steps (you)

| ID | Step | Done |
|---|---|---|
| M2 | Decide whether to pay for a residential proxy. Without one, Glassdoor and ZipRecruiter stay disabled (Cloudflare 403). | ☐ |
| M3 | `applypilot run score --reset-errors`, then `applypilot run score`, to re-score jobs stuck at 0 (may take more than one day on the free tier). | ☐ |
| M5 | `applypilot apply` keeps a human in the loop long-term (agent fills, you submit). Needs its own plan. | ☐ |
| M8 | Google Cloud OAuth desktop client with the Drive API, saved to `~/.applypilot/google_client_secret.json`, then `applypilot drive auth --no-browser`, `applypilot drive sync` and `DRIVE_SYNC=1` (R6 code is done and archived; checklist in `plans/done/google-drive-file-library.md` Task 8). | ☐ |
| M11 | Rerun `applypilot run extract` daily until the backfill count reaches 0 (until a pipeline cron exists). | ☐ |
| M12 | Agent runner setup: inbox repo, ntfy topic, `~/.config/applypilot-agent/env`, crontab line (`scripts/agent/README.md`). | ☐ |
| M13 | After R16 merges: VPS `.env` cleanup and prod redeploy (checklist in [llm-limits-config](plans/done/llm-limits-config.md), "After merge"). Done 2026-10-12: engineerfamily v1.0.3 (PR #25) dropped the `/app/.env` mount, the OAuth token left `~/.applypilot/.env`, ApplyPilot prod at f5fae69. | ☑ |

## Decisions (current)
- 2026-10-12 (user): Every active plan runs unattended. Work only a human can do (Google setup, VPS `.env`, prod deploys) lives in Manual steps, not in plan tasks.
  R0 uses synthetic samples instead of a live capture. The runner's session-usage gate defaults to 70%.
- 2026-10-11 (user): PIV loop. Planning is always interactive (`/plan`). Implementation runs either interactively or unattended from an hourly VPS
  cron (`scripts/agent/run.sh`, at most 4 runs a day and 2 attempts per plan). The plan declares risk and `gate.py` verifies it: `low` auto-merges after green
  validation and CI, `review` becomes a PR deployed to the single preprod, one PR at a time (lock). Agents ask questions and send notices as issues in a
  private inbox repo, with an ntfy push. STATE, CHANGELOG and MORNING were retired in favor of git history and PR bodies. This replaces the 2026-10-03 "commit straight to trunk" rule.
- 2026-10-03 (user): Job data is in the `applypilot` Postgres DB on engineerfamily's server. The dashboard is served at `applypilot.engineerfamily.net/app/` behind basic auth.
- 2026-10-02 (user): Gemini 503s fail over to `LLM_FALLBACK_MODEL`. Tailoring defaults to `--validation lenient`. Tailored PDFs move to Google Drive (company → role → date).
- 2026-10-01: Pipeline LLM calls stay on the Gemini free tier. `applypilot apply` keeps the user in the loop.

## Backlog (not yet planned)
- Pipeline cron on the VPS (`discover → enrich → extract → score`).
- "Posting closed" liveness check, so jobs without a stated deadline can turn Inactive.
- Nightly `pg_dump` of the `applypilot` DB to `/srv/backups`.
- `apply` human-in-the-loop mode (M5), and dry runs of `applypilot apply --dry-run` end to end.
- Re-evaluate the OpenCode Zen provider and `--backend opencode` for removal.
- Split the god-files (`smartextract.py`, `scoring/tailor.py`, `cli.py`); review risk.
- Runner: let the agent address review comments on its own PRs (re-queue on a "changes requested" review).
