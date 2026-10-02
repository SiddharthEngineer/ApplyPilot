# ApplyPilot Roadmap: single source of truth for planned work

**Last updated:** 2026-10-01

This is the one place to see every planned change, its order, and its status. Each initiative links to a
plan in `agents/plans/` with task-level detail. Every task is tagged `Runs: cloud` (done by the nightly
routine) or `Runs: local` (done by you). The build agent updates **Tasks** and **Status** here as it finishes
tasks (see `agents/BUILD_AGENT.md`).

Status key: ⏳ queued · 🔄 in progress · 🟡 cloud work done, local steps pending · ✅ done · ⏸ blocked · ❌ dropped

## How work runs

1. **Nightly (cloud):** the Claude Code routine **"ApplyPilot nightly build"** ([claude.ai/code/routines](https://claude.ai/code/routines)) picks the next runnable `cloud` task in the order below, implements it, runs the unit tests, pushes to `claude/plan-<slug>`, and updates a draft PR on `SiddharthEngineer/ApplyPilot`. One task per night, one plan branch at a time.
2. **Morning (you):** open the PR, then:
   ```bash
   git fetch origin && git switch claude/plan-<slug>
   python scripts/qc.py <slug>
   ```
   (Before `scripts/qc.py` exists, run `pytest tests/ -q --ignore=tests/test_pipeline.py` and the PR's Morning QC commands.)
   Do any `local` tasks on that branch, then merge the PR to let the routine move on to the next plan.
3. **Planning:** new work gets a plan via `agents/PLAN_AGENT.md` and a row in the table below.

## Active initiatives

| Order | ID | Initiative | Plan | Tasks | Status | Depends on |
|---|---|---|---|---|---|---|
| 0 | R0 | Test tiers + morning QC (hermetic unit tests, CI on PRs, committed recorded data, `scripts/qc.py`) | [test-tiers-and-qc](plans/test-tiers-and-qc.md) | 0/5 | ⏳ | none |
| 1 | R2 | Repair job-board discovery (Indeed/LinkedIn reliable, blocked boards gated, add Greenhouse/Lever/Ashby, fix SmartExtract) | [job-board-discovery-repair](plans/job-board-discovery-repair.md) | 0/7 | ⏳ | R0 |
| 2 | R3 | Stop saving LLM errors as `fit_score = 0`; make them retryable | [scoring-error-recovery](plans/scoring-error-recovery.md) | 0/2 | ⏳ | R0 |
| 3 | R4 | Run the pipeline within Gemini's free tier (per-stage models, daily-quota stop, structured JSON, scoring pre-filter) | [gemini-free-tier-llm](plans/gemini-free-tier-llm.md) | 0/5 | ⏳ | R3 |
| 4 | R5 | Tailored resumes rendered from your resume template and content library | [resume-template-tailoring](plans/resume-template-tailoring.md) | 0/7 | ⏳ | R4 |

## Manual steps (you)

| ID | Step | When | Done |
|---|---|---|---|
| M1 | Install the Claude Code CLI and log in with your subscription account. | Before the first routine run | ☑ 2026-10-01 (v2.1.287) |
| M2 | Decide whether to pay for a residential proxy. Without one, Glassdoor and ZipRecruiter stay disabled (Cloudflare 403). | Any time; R2 works without it | ☐ |
| M3 | After R3 + R4 land, run `applypilot run score --reset-errors`, then `applypilot run score` to re-score the 326 jobs stuck at 0. With a free-tier daily quota this may take more than one day; the stage stops cleanly and resumes. | After R4 | ☐ |
| M4 | Review `template_preview.pdf` side by side with your resume and sign off (R5 Task 3). Confirm any wording or typo fixes in the education block. | During R5 | ☐ |
| M5 | `applypilot apply`: keep a human in the loop long-term (agent fills forms, you submit, no automated CAPTCHA solving). Needs its own plan later; see Backlog. | Later | ☐ |
| M6 | Capture and commit scrubbed recorded-response data (R0 Task 4). | During R0 | ☐ |
| M7 | Connect GitHub to your Claude account at https://claude.ai/connect-github (grant access to `SiddharthEngineer/ApplyPilot`), then create the routine "ApplyPilot nightly build" (daily 2:07 AM CT, Default environment, model `claude-opus-5-5`, no connectors). | Before the first nightly run | ☐ |

## Decisions log

- 2026-10-01: Pipeline LLM calls (discovery judge, scoring, tailoring, cover letters) stay on the **Gemini free tier**. The Claude subscription is not used for them (`claude-llm-provider` plan dropped, replaced by `gemini-free-tier-llm`).
- 2026-10-01: Plans are implemented by a nightly **Claude Code cloud routine**, one task per night, reviewed each morning. `scripts/plan_worker.py` and `agents/plan_queue.json` were retired (completion history moved to **Done** below). The never-started `claude-code-plan-worker` plan (R1) was dropped.
- 2026-10-01: `applypilot apply` will keep the user in the loop long-term. Deferred behind R0–R5.

## Findings behind this roadmap (2026-10-01 audit)

- **Tests:** `pytest tests/` hangs for 15+ min because `tests/test_pipeline.py` runs the real Workday crawl. Without that file: 366 passed, 15 skipped in 16s. CI (`.github/workflows/ci.yml`) is manual-only and doesn't install `python-jobspy`.
- **Discovery:** a live probe returned rows from Indeed and LinkedIn. Glassdoor (403 + "location not parsed"), ZipRecruiter (403) and Google (0 rows) fail, and `python-jobspy` 1.1.82 is already the latest release. The user's search config has `site_fail_threshold: 1`, which disables a board after one empty search. Workday has produced 1,195 jobs. SmartExtract has produced 0 ever.
- **Gemini:** the compat and native endpoints both return 200 for `gemini-3.6-flash` and `gemini-3.1-flash-lite`. The key also lists `gemini-3.5-flash-lite`, `gemini-3.7-flash` and `gemini-3.8-flash`.
- **Scoring:** all 326 scored jobs have `fit_score = 0` from a Gemini 404 on 2026-08-27, before the fix in `e32a07b`. Zeros are never retried, so no job has ever been tailored or applied.
- **Where agents run in the product:** only stage 6 (`applypilot apply`) is agentic: `claude -p` (default) or `opencode run` driving Chrome via the Playwright MCP, plus the Gmail and credential-server MCPs. Stages 1–5 are plain Python with single-shot LLM calls through `applypilot/llm.py`.

## Backlog (not yet planned)

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
