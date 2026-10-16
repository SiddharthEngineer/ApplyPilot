# Strelitzia Roadmap

The single tracker for planned work. Each row links to a plan in `agents/plans/`. Finished plans move to `agents/plans/done/`,
and their history is in git and PRs. Workflow: see the PIV loop in `AGENTS.md`.

Status: ⏳ queued · 🔄 in progress · 🟡 code done, user steps pending · ⏸ blocked · ✅ done (then archived)

## Active

R25-R39 (issues #47, #49, #45, #48, #46) run as one unattended chain, one at a time, merging after green CI (`AGENT_MERGE_POLICY=all`, user 2026-10-16). #19 is the user's, after the chain.

| ID | Initiative | Plan | Risk | Status | Depends on |
|---|---|---|---|---|---|
| R25 | Users 1: users table, per-user rows (#47) | [user-auth-1-schema](plans/user-auth-1-schema.md) | review | ✅ PR open | - |
| R26 | Users 2: intake and tasks per user (#47) | [user-auth-2-intake](plans/user-auth-2-intake.md) | review | ✅ PR open | R25 |
| R27 | Users 3: preferences, scoring, spend caps (#47) | [user-auth-3-scoring](plans/user-auth-3-scoring.md) | review | ✅ PR open | R26 |
| R28 | Users 4: contacts, tracking, workbench, panel (#47) | [user-auth-4-workspace](plans/user-auth-4-workspace.md) | review | ✅ PR open | R27 |
| R29 | Users 5: Google sign-in, allowlist (#47) | [user-auth-5-login](plans/user-auth-5-login.md) | review | ✅ PR open | R28 |
| R30 | Users 6: connect Drive per user (#47) | [user-auth-6-drive](plans/user-auth-6-drive.md) | review | ✅ PR open | R29 |
| R31 | Users 7: sign-in screens, Users admin page (#47) | [user-auth-7-dashboard](plans/user-auth-7-dashboard.md) | review | ✅ PR open | R30 |
| R32 | Users 8: admin-only CLI (#47) | [user-auth-8-cli](plans/user-auth-8-cli.md) | review | ⏳ | R31 |
| R33 | Contact form with separate fields (#49) | [contact-form](plans/contact-form.md) | review | ⏳ | R32 |
| R34 | Rescore all jobs (#45) | [rescore-all](plans/rescore-all.md) | review | ⏳ | R33 |
| R35 | Profile 1: per-user profile in the DB (#48) | [profile-1-store](plans/profile-1-store.md) | review | ⏳ | R34 |
| R36 | Profile 2: Profile page, resume parsing (#48) | [profile-2-page](plans/profile-2-page.md) | review | ⏳ | R35 |
| R37 | Prefs cleanup 1: qualification rules, fixed weights (#46) | [prefs-cleanup-1-scoring](plans/prefs-cleanup-1-scoring.md) | review | ⏳ | R36 |
| R38 | Prefs cleanup 2: job fields and role map (#46) | [prefs-cleanup-2-roles](plans/prefs-cleanup-2-roles.md) | review | ⏳ | R37 |
| R39 | Prefs cleanup 3: regrouped Preferences page (#46) | [prefs-cleanup-3-page](plans/prefs-cleanup-3-page.md) | review | ⏳ | R38 |

## Manual steps (you)

| ID | Step | Done |
|---|---|---|
| M25 | For R29/R30 (Google sign-in, Drive per user): in the **same** Google Cloud project as the Drive desktop client, publish the consent screen (scopes openid, email, profile, drive.file; no logo), create a Web OAuth client with redirect URIs `/app/api/auth/callback` and `/app/api/drive/callback` on `strelitzia` and `strelitzia-preprod.engineerfamily.net`, then put `STRELITZIA_GOOGLE_CLIENT_ID`, `_GOOGLE_CLIENT_SECRET`, `_OWNER_EMAIL`, `_PUBLIC_URL`, `_SESSION_SECRET`, `_TOKEN_KEY` in `~/.strelitzia/.env` (and auth-only keys with their own secrets in `~/.strelitzia-preprod/.env`). Login stays off until the client id is set. Covers M17's consent-screen part. | ☐ |
| M26 | After R31: engineerfamily PR to drop nginx basic auth on prod `strelitzia` (keep it on preprod), deploy via the VPS make targets. | ☐ |
| M27 | After R39: deploy prod (`make -C ~/src/engineerfamily strelitzia-deploy`). The R25 migration rewrites the jobs primary key on first start: take a `pg_dump` of `applypilot` first. Then sign in, invite a test account, and run the human test steps of R29-R39 on prod. | ☐ |
| M2 | Obsolete (2026-10-16): discovery is off by default (R22) and jobs come in by manual upload. Decide whether to pay for a residential proxy. Without one, Glassdoor and ZipRecruiter stay disabled (Cloudflare 403). | ☑ |
| M3 | Obsolete: replaced by M19, and the backlog was pruned on 2026-10-15. `strelitzia run score --reset-errors`, then `strelitzia run score`, to re-score jobs stuck at 0 (may take more than one day on the free tier). | ☑ |
| M5 | Obsolete: R18 removed auto-apply (applications are tracked by hand in the dashboard). | ☑ |
| M8 | Google Cloud OAuth desktop client with the Drive API, saved to `~/.applypilot/google_client_secret.json`, then `strelitzia drive auth --no-browser`, `strelitzia drive sync` and `DRIVE_SYNC=1` (R6 code is done and archived; checklist in `plans/done/google-drive-file-library.md` Task 8). | ☐ |
| M11 | Obsolete: replaced by M19, and the backlog was pruned on 2026-10-15. Rerun `strelitzia run extract` daily until the backfill count reaches 0 (until a pipeline cron exists). | ☑ |
| M12 | Agent runner setup: inbox repo, ntfy topic, `~/.config/strelitzia-agent/env`, crontab line (`scripts/agent/README.md`). | ☑ |
| M14 | Before R18 runs: GitHub Settings → Danger Zone → **Leave fork network** on SiddharthEngineer/ApplyPilot, then rename it to **Strelitzia**. In Cloudflare, add `strelitzia` and `strelitzia-preprod` DNS records (proxied, copy of the `applypilot*` ones). | ☑ |
| M15 | After R18 merges: `scripts/migrate_to_strelitzia.sh` (dry run), then `--apply`, then run the `sudo` lines it prints for `/srv` and `/var/log`. Done 2026-10-13 (plus `safe.directory` for the new `/srv` paths, now step 8 of the script). | ☑ |
| M16 | After M15: engineerfamily PR (nginx `strelitzia*` server_names + 301 from `applypilot*`, compose service/context/mounts/`STRELITZIA_DATABASE_URL`, Makefile `strelitzia-*` targets, restyled homepage/privacy page), then `make deploy-prod`, then `make strelitzia-deploy`. Done 2026-10-13: engineerfamily v1.1.0 (#26) on preprod and prod, runner env updated, prod dashboard at d05aecb. | ☑ |
| M17 | After M16: Google Cloud OAuth consent screen: app name "Strelitzia", homepage and privacy URLs on `strelitzia.engineerfamily.net`. Before commercializing: USPTO search for "Strelitzia" (classes 9/35/42). | ☐ |
| M13 | After R16 merges: VPS `.env` cleanup and prod redeploy (checklist in [llm-limits-config](plans/done/llm-limits-config.md), "After merge"). Done 2026-10-12: engineerfamily v1.0.3 (PR #25) dropped the `/app/.env` mount, the OAuth token left `~/.applypilot/.env`, ApplyPilot prod at f5fae69. | ☑ |
| M18 | Before R20 runs: Anthropic Console account + API key, set a monthly spend limit in the Console, put `ANTHROPIC_API_KEY` and `LLM_MONTHLY_BUDGET_USD` in `~/.strelitzia/.env` on the VPS. **Never** in `~/.config/strelitzia-agent/env`: `claude -p` would bill the API instead of the subscription. 2026-10-13: key added (Default Workspace); still to do: Console spend limit, `LLM_MONTHLY_BUDGET_USD`. | ☐ |
| M19 | After R20 merges: delete `GEMINI_API_KEY` and the `LLM_*_MODEL` lines from `~/.strelitzia/.env`. Run the skipped interactive Task 7 (`pytest -m llm --run-llm tests/test_live_scoring_tailoring_cover.py`, ~$0.05), then `strelitzia doctor` and `strelitzia run extract` to clear the backlog. Deploy prod when happy. Replaces M3 and M11. Done 2026-10-15: `.env` cleaned (backup `.env.bak-20261015-m19`). Task 7: 6/7 passed; extraction hit the 16-union schema limit, fixed in #43 and passed on rerun. `doctor` OK. Instead of a full backlog extract, 3,126 discovered jobs were deleted (dump in `~/backups/applypilot-before-prune-20261015.dump`) and 10 of the 21 kept jobs extracted as a sample ($0.005). Prod not deployed. | ☑ |
| M20 | Deferred until about 30 extracted jobs exist (the 2026-10-15 prune left about 10; `sample` only picks extracted jobs). Scoring stays on Haiku until then. R21 Task 5 merged (#52): label ~30 jobs with `scripts/eval_fit.py sample`, run `scripts/eval_fit.py run` (~$1 of API), pick the scoring model in `llm_models.yaml`, and record the numbers in the plan's Decisions. | ☐ |
| M21 | R22 merged (#44). Live check on real postings. Add one link each from Greenhouse, Lever, Ashby, a Workday portal, a company careers page, LinkedIn and Indeed; note `intake_status`, seconds until scored, and whether the paste fallback appeared. Greenhouse/Lever/Ashby should reach `ok`; LinkedIn/Indeed reach `ok` or show the paste panel, never a stuck spinner. | ☐ |
| M22 | R23 merged (#50). With M8 done for Drive (without it the packet goes to the clipboard): open `/claude-setup`, create the "Strelitzia Resumes" Claude Project and attach the Docs. On three jobs: Open in Claude, iterate once, paste the result, paste one bad result to see the fix message, then check the PDF, DOCX and cover PDF in Drive. | ☐ |
| M23 | R24 merged (#51). After M22, once 5 jobs have two drafts each: create the "Strelitzia Panel" project with the rubric Doc, run one 5-item panel on Sonnet and Opus, make your picks, and add a ROADMAP decision line with the verdict. | ☐ |
| M24 | Deploy prod: `make -C ~/src/engineerfamily strelitzia-deploy`, then the prod checks in `DEPLOY.md`. On 2026-10-16 prod was still at d05aecb (R18), 49 commits behind trunk, so R20-R24 were on preprod only. Prod reads `~/.strelitzia/.env`, which M19 already moved to Claude, so (per M19) the old Gemini code on prod has no LLM key. Do M18 first. | ☐ |

## Decisions (current)
- 2026-10-15 (user): Jobs come in by manual upload (R22 Add job) from now on, not bulk discovery. The discovered backlog was pruned to the 21 jobs with activity or the best fit scores; don't run full-backlog extract or score jobs.
- 2026-10-14 (user): R21 Task 5, R22, R23 and R24 run overnight unattended and merge after green CI (`AGENT_MERGE_POLICY=all`). Each plan's live check moved to Manual steps (M20 to M23), per the 2026-10-12 rule. Chained plans are queued with `buildqueue.py add --after` and also check their predecessor in a Task 0.
- 2026-10-14 (user): Resume, cover-letter, judge and panel work moves off the API into the user's Claude Pro chats (copy-paste packets, Google Docs in a Claude Project, JSON pasted back and checked by the app). The API keeps only Haiku for scraping, extraction and scoring. The API tailor/cover/judge paths are deleted (R23). Supersedes the writing-model parts of 2026-10-13.
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
- "Posting closed" liveness check, so jobs without a stated deadline can turn Inactive.
- Nightly `pg_dump` of the `applypilot` DB to `/srv/backups`.
- Split the god-files (`smartextract.py`, `cli.py`; `scoring/tailor.py` shrank to 69 lines in R23); review risk.
- Runner: let the agent address review comments on its own PRs (re-queue on a "changes requested" review).
