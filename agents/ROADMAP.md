# Strelitzia Roadmap

The single tracker for planned work. Each row links to a plan in `agents/plans/`. Finished plans move to `agents/plans/done/`,
and their history is in git and PRs. Workflow: see the PIV loop in `AGENTS.md`.

Status: ⏳ queued · 🔄 in progress · 🟡 code done, user steps pending · ⏸ blocked · ✅ done (then archived)

## Active

| ID | Initiative | Plan | Risk | Status | Depends on |
|---|---|---|---|---|---|
| R18 | Strelitzia pivot: rename, drop auto-apply, bird-of-paradise theme (v0.4.0) | [strelitzia-pivot](plans/strelitzia-pivot.md) | review | 🟡 merged (#33); live on strelitzia.engineerfamily.net; you: M17 | M14 |
| R20 | Claude API as the only LLM provider (Batch API, spend ledger, monthly budget); Gemini removed | [claude-llm-provider](plans/claude-llm-provider.md) | review | 🟡 Tasks 1-6 done, PR open; you: Task 7 live run (M19) | M18 |
| R21 | Preferences page + two-layer fit score (code-scored preferences, Claude rubric, rescore cost confirm, Haiku/Sonnet eval) | [fit-preferences](plans/fit-preferences.md) | review | 🟡 Tasks 0-4 done, PR open; you: Task 5 eval (M20) | R20 |

## Manual steps (you)

| ID | Step | Done |
|---|---|---|
| M2 | Decide whether to pay for a residential proxy. Without one, Glassdoor and ZipRecruiter stay disabled (Cloudflare 403). | ☐ |
| M3 | `strelitzia run score --reset-errors`, then `strelitzia run score`, to re-score jobs stuck at 0 (may take more than one day on the free tier). | ☐ |
| M5 | Obsolete: R18 removed auto-apply (applications are tracked by hand in the dashboard). | ☑ |
| M8 | Google Cloud OAuth desktop client with the Drive API, saved to `~/.applypilot/google_client_secret.json`, then `strelitzia drive auth --no-browser`, `strelitzia drive sync` and `DRIVE_SYNC=1` (R6 code is done and archived; checklist in `plans/done/google-drive-file-library.md` Task 8). | ☐ |
| M11 | Rerun `strelitzia run extract` daily until the backfill count reaches 0 (until a pipeline cron exists). | ☐ |
| M12 | Agent runner setup: inbox repo, ntfy topic, `~/.config/applypilot-agent/env`, crontab line (`scripts/agent/README.md`). | ☐ |
| M14 | Before R18 runs: GitHub Settings → Danger Zone → **Leave fork network** on SiddharthEngineer/ApplyPilot, then rename it to **Strelitzia**. In Cloudflare, add `strelitzia` and `strelitzia-preprod` DNS records (proxied, copy of the `applypilot*` ones). | ☑ |
| M15 | After R18 merges: `scripts/migrate_to_strelitzia.sh` (dry run), then `--apply`, then run the `sudo` lines it prints for `/srv` and `/var/log`. Done 2026-10-13 (plus `safe.directory` for the new `/srv` paths, now step 8 of the script). | ☑ |
| M16 | After M15: engineerfamily PR (nginx `strelitzia*` server_names + 301 from `applypilot*`, compose service/context/mounts/`STRELITZIA_DATABASE_URL`, Makefile `strelitzia-*` targets, restyled homepage/privacy page), then `make deploy-prod`, then `make strelitzia-deploy`. Done 2026-10-13: engineerfamily v1.1.0 (#26) on preprod and prod, runner env updated, prod dashboard at d05aecb. | ☑ |
| M17 | After M16: Google Cloud OAuth consent screen: app name "Strelitzia", homepage and privacy URLs on `strelitzia.engineerfamily.net`. Before commercializing: USPTO search for "Strelitzia" (classes 9/35/42). | ☐ |
| M13 | After R16 merges: VPS `.env` cleanup and prod redeploy (checklist in [llm-limits-config](plans/done/llm-limits-config.md), "After merge"). Done 2026-10-12: engineerfamily v1.0.3 (PR #25) dropped the `/app/.env` mount, the OAuth token left `~/.applypilot/.env`, ApplyPilot prod at f5fae69. | ☑ |
| M18 | Before R20 runs: Anthropic Console account + API key, set a monthly spend limit in the Console, put `ANTHROPIC_API_KEY` and `LLM_MONTHLY_BUDGET_USD` in `~/.strelitzia/.env` on the VPS. **Never** in `~/.config/strelitzia-agent/env`: `claude -p` would bill the API instead of the subscription. 2026-10-13: key added (Default Workspace); still to do: Console spend limit, `LLM_MONTHLY_BUDGET_USD`. | ☐ |
| M19 | After R20 merges: delete `GEMINI_API_KEY` and the `LLM_*_MODEL` lines from `~/.strelitzia/.env`. Run the skipped interactive Task 7 (`pytest -m llm --run-llm tests/test_live_scoring_tailoring_cover.py`, ~$0.05), then `strelitzia doctor` and `strelitzia run extract` to clear the backlog. Deploy prod when happy. Replaces M3 and M11. | ☐ |
| M20 | After R21 merges: R21 Task 5 eval (skipped by the runner). Label ~30 jobs with `scripts/eval_fit.py sample`, run `scripts/eval_fit.py run`, and pick the scoring model. | ☐ |

## Decisions (current)
- 2026-10-13 (user): LLM calls move from the Gemini free tier to the Claude API with an API key (never the Pro subscription; Anthropic's docs point products to API keys). Gemini/OpenAI/OpenCode/local providers are deleted. Haiku 5.5 + Batch for bulk stages, Sonnet 5.5 for writing. One owner key with per-user spend tracking; per-user caps when other users exist. Supersedes 2026-10-01 and 2026-10-02 LLM decisions.
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
- Resume and cover letter quality pass on Claude (Sonnet 5.5; Opus 5.5 per document as an option): prompts, evidence use, an eval like R21 Task 5.
- Split the god-files (`smartextract.py`, `scoring/tailor.py`, `cli.py`); review risk.
- Runner: let the agent address review comments on its own PRs (re-queue on a "changes requested" review).
