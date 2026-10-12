# ApplyPilot Roadmap

The single tracker for planned work. Each row links to a plan in `agents/plans/`. Finished plans move to `agents/plans/done/`,
and their history is in git and PRs. Workflow: see the PIV loop in `AGENTS.md`.

Status: ⏳ queued · 🔄 in progress · 🟡 code done, user steps pending · ⏸ blocked · ✅ done (then archived)

## Active

| ID | Initiative | Plan | Risk | Status | Depends on |
|---|---|---|---|---|---|
| R0 | Test tiers: commit scrubbed recorded-response data (M6) | [test-tiers-and-qc](plans/test-tiers-and-qc.md) | low | 🟡 | none |
| R6 | Google Drive file library: Google setup and the first move of existing files (M8) | [google-drive-file-library](plans/google-drive-file-library.md) | review | 🟡 | none |
| R12 | VPS layout migration: dev clones in `~/src`, pinned prod/preprod checkouts, preprod ApplyPilot service | [vps-layout-migration](plans/vps-layout-migration.md) | review | ⏳ | none |
| R13 | Code health cleanup from the 2026-10-11 audit (needs `/plan` refinement first) | [code-health-cleanup](plans/code-health-cleanup.md) | low | ⏳ | none |
| R15 | Runner follow-ups: resume the agent session after an answer, `gate.py --plan` check in `/plan` | [runner-followups](plans/runner-followups.md) | review | ⏳ | none |
| R16 | LLM rate limits out of `.env` (packaged defaults + `~/.applypilot/llm_limits.yaml`), one `.env` per environment | [llm-limits-config](plans/llm-limits-config.md) | review | ⏳ | none |
| R17 | Seed preprod with fake jobs on every deploy (replaces R12 Task 4) | [seed-preprod-data](plans/seed-preprod-data.md) | review | ⏳ | none (R12 Task 3 done) |

## Manual steps (you)

| ID | Step | Done |
|---|---|---|
| M2 | Decide whether to pay for a residential proxy. Without one, Glassdoor and ZipRecruiter stay disabled (Cloudflare 403). | ☐ |
| M3 | `applypilot run score --reset-errors`, then `applypilot run score`, to re-score jobs stuck at 0 (may take more than one day on the free tier). | ☐ |
| M5 | `applypilot apply` keeps a human in the loop long-term (agent fills, you submit). Needs its own plan. | ☐ |
| M6 | `python scripts/capture_fixtures.py --out tests/data --scrub --sites indeed,linkedin`, then `grep -ri "<your name>" tests/data`, then commit (R0). | ☐ |
| M8 | Google Cloud OAuth desktop client with the Drive API, saved to `~/.applypilot/google_client_secret.json`, then `applypilot drive auth --no-browser` and `applypilot drive sync` (R6). | ☐ |
| M11 | Rerun `applypilot run extract` daily until the backfill count reaches 0 (until a pipeline cron exists). | ☐ |
| M12 | Agent runner setup: inbox repo, ntfy topic, `~/.config/applypilot-agent/env`, crontab line (`scripts/agent/README.md`). | ☐ |

## Decisions (current)
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
