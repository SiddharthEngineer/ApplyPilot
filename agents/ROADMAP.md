# ApplyPilot Roadmap: single source of truth for planned work

**Last updated:** 2026-10-03 (job dashboard planned: R7–R11)

This is the one place to see every planned change, its order, and its status. Each initiative links to a
plan in `agents/plans/` with task-level detail. Every task is tagged `Runs: cloud` (done by the nightly
routine) or `Runs: local` (done by you). The build agent updates **Tasks** and **Status** here as it finishes
tasks (see `agents/BUILD_AGENT.md`).

Status key: ⏳ queued · 🔄 in progress · 🟡 cloud work done, local steps pending · ✅ done · ⏸ blocked · ❌ dropped

## How work runs

1. **Nightly (cloud):** the Claude Code routine **"ApplyPilot nightly build"** ([claude.ai/code/routines](https://claude.ai/code/routines)) works through runnable `cloud` tasks in the order below, one after another, until none are left or the session hits its usage limit. Each task is tested, committed and pushed on its own. Each plan has its own branch `claude/plan-<slug>` and draft PR on `SiddharthEngineer/ApplyPilot`, and later plans are **stacked** on earlier unmerged ones, so work never waits on a merge.
2. **Morning (you):** open the PR, then:
   ```bash
   git fetch origin && git switch claude/plan-<slug>
   python scripts/qc.py <slug>
   ```
   (Before `scripts/qc.py` exists, run `pytest tests/ -q --ignore=tests/test_pipeline.py` and the PR's Morning QC commands.)
   Do any `local` tasks on that branch, then merge PRs **bottom of the stack first**, using **"Create a merge commit"** (squash or rebase merges break the stacked PRs above it).
3. **Planning:** new work gets a plan via `agents/PLAN_AGENT.md` and a row in the table below.

## Active initiatives

| Order | ID | Initiative | Plan | Tasks | Status | Depends on |
|---|---|---|---|---|---|---|
| 0 | R0 | Test tiers + morning QC (hermetic unit tests, CI on PRs, committed recorded data, `scripts/qc.py`) | [test-tiers-and-qc](plans/test-tiers-and-qc.md) | 4/5 | 🟡 | none |
| 1 | R2 | Repair job-board discovery (Indeed/LinkedIn reliable, blocked boards gated, add Greenhouse/Lever/Ashby, fix SmartExtract) | [job-board-discovery-repair](plans/job-board-discovery-repair.md) | 8/8 | ✅ | R0 |
| 2 | R3 | Stop saving LLM errors as `fit_score = 0`; make them retryable | [scoring-error-recovery](plans/scoring-error-recovery.md) | 2/2 | ✅ | R0 |
| 3 | R4 | Run the pipeline within Gemini's free tier (per-stage models, daily-quota stop, structured JSON, scoring pre-filter) | [gemini-free-tier-llm](plans/gemini-free-tier-llm.md) | 5/5 | ✅ | R3 |
| 4 | R5 | Tailored resumes rendered from your resume template and content library | [resume-template-tailoring](plans/resume-template-tailoring.md) | 7/7 | ✅ | R4 |
| 5 | R6 | Google Drive file library: tailored resumes and cover letters moved to Drive as company/role/date, links saved in the DB | [google-drive-file-library](plans/google-drive-file-library.md) | 7/8 | 🔄 | R5 |
| 6 | R7 | Job store on Postgres: new `applypilot` DB on engineerfamily's Postgres server, `?`→`%s` adapter, `applypilot db migrate/verify`, VPS cutover | [job-store-postgres](plans/job-store-postgres.md) | 0/7 | ⏳ | R0 |
| 7 | R8 | Structured job-posting extraction: Gemini parses each posting into JSON (qualifications, salary, deadline, role category, work mode…), batched `extract` stage + heuristic fallback, backfill | [job-posting-extraction](plans/job-posting-extraction.md) | 0/6 | ⏳ | R7 |
| 8 | R9 | Dashboard API (FastAPI, `applypilot serve`): filters/sort, job detail, status tracking (Active/Inactive/In progress/Submitted/Rejected/Heard back), background resume/cover generation | [dashboard-api](plans/dashboard-api.md) | 0/6 | ⏳ | R7, R8 Task 3 |
| 9 | R10 | Dashboard UI (React + Vite at `/app/`): jobs table, filter bar, detail page with action buttons | [dashboard-ui](plans/dashboard-ui.md) | 0/4 | ⏳ | R9 |
| 10 | R11 | Serve the dashboard from engineerfamily: Docker image, compose service, nginx `/app/` + basic auth, prod deploy | [dashboard-deploy](plans/dashboard-deploy.md) | 0/4 | ⏳ | R10 |

## Manual steps (you)

| ID | Step | When | Done |
|---|---|---|---|
| M1 | Install the Claude Code CLI and log in with your subscription account. | Before the first routine run | ☑ 2026-10-01 (v2.1.287) |
| M2 | Decide whether to pay for a residential proxy. Without one, Glassdoor and ZipRecruiter stay disabled (Cloudflare 403). | Any time; R2 works without it | ☐ |
| M3 | After R3 + R4 land, run `applypilot run score --reset-errors`, then `applypilot run score` to re-score the 326 jobs stuck at 0. With a free-tier daily quota this may take more than one day; the stage stops cleanly and resumes. | After R4 | ☐ |
| M4 | Review `template_preview.pdf` side by side with your resume and sign off (R5 Task 3). | During R5 | ☑ 2026-10-02 |
| M5 | `applypilot apply`: keep a human in the loop long-term (agent fills forms, you submit, no automated CAPTCHA solving). Needs its own plan later; see Backlog. | Later | ☐ |
| M6 | Capture and commit scrubbed recorded-response data (R0 Task 4): `python scripts/capture_fixtures.py --out tests/data --scrub --sites indeed,linkedin`, check with `grep -ri "<your email or name>" tests/data`, commit. | During R0 | ☐ |
| M7 | Connect GitHub to your Claude account at https://claude.ai/connect-github (grant access to `SiddharthEngineer/ApplyPilot`), then create the routine "ApplyPilot nightly build" (daily 2:07 AM CT, Default environment, model `claude-opus-5-5`, no connectors). | Before the first nightly run | ☑ 2026-10-02: routine `trig_01LsAQHDWu531mcmvkqSspVB`, cron `7 7 * * *` UTC |
| M8 | Google Drive setup (R6 Task 8): create a Google Cloud OAuth "Desktop app" client with the Drive API enabled, save the client secret to `~/.applypilot/google_client_secret.json`, run `applypilot drive auth --no-browser` over an SSH tunnel, then `applypilot drive sync`. | After R6 Tasks 1–7 | ☐ |
| M9 | ~~Optional: MySQL instead of Postgres for R7~~ Postgres accepted. engineerfamily runs no MySQL today, so R7 targets a new `applypilot` DB on the existing Postgres 16 (`analytics-db`). | Before R7 starts | ☑ 2026-10-03 |
| M10 | After R11: log in at `https://applypilot.engineerfamily.net/app/` with the credentials in `/root/applypilot-dashboard-credentials.txt`. Optionally put Cloudflare Access in front of `/app/*`. | After R11 | ☐ |
| M12 | Switch `/srv/engineerfamily` from `prod` to `main` (as `deploy`), so `make up` deploys `main`. | Before R7 | ☐ |
| M11 | During R8 Task 6 → done: rerun `applypilot run extract` daily until the backfill count reaches 0 (until a cron plan exists). | After R8 | ☐ |

## Decisions log

- 2026-10-03 (user): Job dashboard (R7–R11). Data moves to a new `applypilot` DB on the engineerfamily DB server. The user said MySQL, but that server is Postgres 16, so it's Postgres (M9 to override). Dashboard: FastAPI in ApplyPilot plus a React/Vite UI, served by the engineerfamily stack at `applypilot.engineerfamily.net/app/` behind basic auth. For these plans the agent commits straight to `trunk` (no PRs), commits engineerfamily changes on `main` (never the `prod` branch), and deploys to prod itself with `make up` (no tags). The user fixes breakage in the morning.
- 2026-10-01: Pipeline LLM calls (discovery judge, scoring, tailoring, cover letters) stay on the **Gemini free tier**. The Claude subscription is not used for them (`claude-llm-provider` plan dropped, replaced by `gemini-free-tier-llm`).
- 2026-10-01: Plans are implemented by a nightly **Claude Code cloud routine**, reviewed each morning. `scripts/plan_worker.py` and `agents/plan_queue.json` were retired (completion history moved to **Done** below). The never-started `claude-code-plan-worker` plan (R1) was dropped.
- 2026-10-02: The nightly routine keeps completing tasks until nothing is runnable or its usage limit ends the session (was: one task per night), with stacked per-plan branches and PRs.
- 2026-10-02: Plans now run in **VPS mode** (`agents/BUILD_AGENT.md` §0): sessions on the user's VPS, one plan at a time, rebase-merged to `trunk` by the agent, summarized in `agents/MORNING.md`. The cloud routine is disabled. CI lint gates on real errors only (`--select E9,F63,F7,F82`).
- 2026-10-01: `applypilot apply` will keep the user in the loop long-term. Deferred behind R0–R5.
- 2026-10-02 (user): Gemini 503s fail over immediately to `LLM_FALLBACK_MODEL` (default `gemini-3.1-flash-lite`, with a 5-minute cooldown) instead of 5 backoff retries, because Google counts 503s against the free-tier daily quota. Content-library tailoring defaults to `--validation lenient` (no LLM judge).
- 2026-10-02 (R5): Tailored resumes take the header from `resume_fixed.yaml` (copied verbatim from resume.txt), not profile.json, because profile.json's `full_name` is just "Siddharth" and its phone and state are formatted differently from the resume. `tailored_resume_path` now stores the template PDF. Role dates print inline after `Title at Company`, as on resume.pdf (the plan said right-aligned). Overflow is detected from the PDF page count.

- 2026-10-02 (user): Tailored PDFs are moved to Google Drive (R6), in folders by company → role → date, because ApplyPilot runs on both the laptop and the VPS. The source board stays in the DB `site` column. `apply` downloads a moved PDF when it needs one. OAuth uses the `drive.file` scope.

## Findings behind this roadmap (2026-10-01 audit)

- **Tests:** `pytest tests/` hangs for 15+ min because `tests/test_pipeline.py` runs the real Workday crawl. Without that file: 366 passed, 15 skipped in 16s. CI (`.github/workflows/ci.yml`) is manual-only and doesn't install `python-jobspy`.
- **Discovery:** a live probe returned rows from Indeed and LinkedIn. Glassdoor (403 + "location not parsed"), ZipRecruiter (403) and Google (0 rows) fail, and `python-jobspy` 1.1.82 is already the latest release. The user's search config has `site_fail_threshold: 1`, which disables a board after one empty search. Workday has produced 1,195 jobs. SmartExtract has produced 0 ever.
- **Gemini:** the compat and native endpoints both return 200 for `gemini-3.6-flash` and `gemini-3.1-flash-lite`. The key also lists `gemini-3.5-flash-lite`, `gemini-3.7-flash` and `gemini-3.8-flash`.
- **Scoring:** all 326 scored jobs have `fit_score = 0` from a Gemini 404 on 2026-08-27, before the fix in `e32a07b`. Zeros are never retried, so no job has ever been tailored or applied.
- **Where agents run in the product:** only stage 6 (`applypilot apply`) is agentic: `claude -p` (default) or `opencode run` driving Chrome via the Playwright MCP, plus the Gmail and credential-server MCPs. Stages 1–5 are plain Python with single-shot LLM calls through `applypilot/llm.py`.

## Backlog (not yet planned)

- Cron: scheduled `discover → enrich → extract → score` on the VPS (after R8; the user plans this).
- "Posting closed" liveness check, so jobs without a stated deadline can turn Inactive.
- Nightly `pg_dump` of the `applypilot` DB to `/srv/backups` (after R7).
- `apply` human-in-the-loop mode: fill forms, pause for the user to review and submit, disable CapSolver by default (M5).
- Run apply-stage dry runs (`applypilot apply --dry-run`) end to end once R5 produces resumes.
- Re-evaluate the OpenCode Zen LLM provider and `--backend opencode` for removal.

## Done

| Plan | File | Completed | Note |
|---|---|---|---|
| gemini-3.6-flash-migration | [gemini-3.6-flash-migration.md](plans/gemini-3.6-flash-migration.md) | 2026-08-27 | |
| secure-password-at-rest | [secure-password-at-rest.md](plans/secure-password-at-rest.md) | 2026-08-27 | |
| captcha-solve-tool | [captcha-solve-tool.md](plans/captcha-solve-tool.md) | 2026-08-27 | |
| fix-workday-ssl-cert | [fix-workday-ssl-cert.md](plans/fix-workday-ssl-cert.md) | 2026-08-27 | |
| ziprecruiter-403-handling | [ziprecruiter-403-handling.md](plans/ziprecruiter-403-handling.md) | 2026-08-28 | |
| opencode-model-selection | [opencode-model-selection.md](plans/opencode-model-selection.md) | 2026-08-28 | |
| restore-auto-flag | [restore-auto-flag.md](plans/restore-auto-flag.md) | 2026-08-28 | |
| fix-opencode-plan-permissions | [fix-opencode-plan-permissions.md](plans/fix-opencode-plan-permissions.md) | 2026-08-28 | |
| discover-crawl-resilience | [discover-crawl-resilience.md](plans/discover-crawl-resilience.md) | 2026-08-28 | |
| gemini-404-scoring-fix | [gemini-404-scoring-fix.md](plans/gemini-404-scoring-fix.md) | 2026-08-28 | worker exited `error_exit_1`; fix landed in `e32a07b` |
| llm-rate-limit-mitigation | [llm-rate-limit-mitigation.md](plans/llm-rate-limit-mitigation.md) | 2026-08-31 | worker hit `max_iterations_exceeded`; finished in follow-up commits |
| integration-smoke-suite-with-pickle-fixtures | [integration-smoke-suite-with-pickle-fixtures.md](plans/integration-smoke-suite-with-pickle-fixtures.md) | 2026-08-31 | worker hit `max_iterations_exceeded`; finished in follow-up commits |
| fix-live-test-failures | [fix-live-test-failures.md](plans/fix-live-test-failures.md) | 2026-09-01 | worker hit `max_iterations_exceeded`; finished in follow-up commits |
| cap-live-test-scope | [cap-live-test-scope.md](plans/cap-live-test-scope.md) | 2026-09-01 | |
| gemini-2-5-flash-lite-migration | [gemini-2-5-flash-lite-migration.md](plans/gemini-2-5-flash-lite-migration.md) | 2026-09-01 | target pivoted to `gemini-3.1-flash-lite` |
| Earlier (init wizard, content library, passwords, OpenCode engine) | see `agents/CHANGELOG.md` | ≤ 2026-08-26 | |
