# ApplyPilot Roadmap: single source of truth for planned work

**Last updated:** 2026-10-01

This is the one place to see every planned change, its order, and its status. Each initiative links to
a plan in `agents/plans/`, which holds the task-level detail and acceptance criteria. Build agents update
the **Tasks** column and **Status** here whenever they finish a task (see `agents/BUILD_AGENT.md`).
`agents/plan_queue.json` holds the execution order for `scripts/plan_worker.py` and mirrors the
**Order** column below.

Status key: ⏳ queued · 🔄 in progress · ✅ done · ⏸ blocked (see Blockers) · ❌ dropped

## Active initiatives

| Order | ID | Initiative | Plan | Tasks | Status | Depends on |
|---|---|---|---|---|---|---|
| 1 | R1 | Run the plan worker (a coding agent) on Claude Code instead of OpenCode | [claude-code-plan-worker](plans/claude-code-plan-worker.md) | 0/2 | ⏳ | M1 |
| 2 | R2 | Repair job-board discovery (Indeed/LinkedIn reliable, blocked boards gated, add Greenhouse/Lever/Ashby, fix SmartExtract) | [job-board-discovery-repair](plans/job-board-discovery-repair.md) | 0/7 | ⏳ | none |
| 3 | R3 | Stop saving LLM errors as `fit_score = 0`; make them retryable | [scoring-error-recovery](plans/scoring-error-recovery.md) | 0/2 | ⏳ | none |
| 4 | R4 | Run the pipeline within Gemini's free tier (per-stage models, daily-quota stop, structured JSON, scoring pre-filter) | [gemini-free-tier-llm](plans/gemini-free-tier-llm.md) | 0/5 | ⏳ | R3 (Task 2 only) |
| 5 | R5 | Tailored resumes rendered from your resume template and content library | [resume-template-tailoring](plans/resume-template-tailoring.md) | 0/7 | ⏳ | R4 |

## Manual steps (you)

| ID | Step | When | Done |
|---|---|---|---|
| M1 | Install the Claude Code CLI and log in with your subscription account. Needed by R1 (plan worker). | Before R1 | ☑ 2026-10-01 (v2.1.287) |
| M2 | Decide whether to pay for a residential proxy. Without one, Glassdoor and ZipRecruiter stay disabled (Cloudflare 403). | Any time; R2 works without it | ☐ |
| M3 | After R3 + R4 land, run `applypilot run score --reset-errors`, then `applypilot run score` to re-score the 326 jobs that were stuck at 0. With a free-tier daily quota this may take more than one day; the stage stops cleanly and resumes. | After R4 | ☐ |
| M5 | Decide which agent backend `applypilot apply` should use. It defaults to `claude -p` (your subscription) driving the browser; the alternative is `--backend opencode`. | Before first real apply run | ☐ |
| M4 | Review `template_preview.pdf` side by side with your resume and sign off (R5 Task 3). Confirm the "Mathenatics" typo fix. | During R5 | ☐ |

## Decisions log

- 2026-10-01: Pipeline LLM calls (discovery judge, scoring, tailoring, cover letters) stay on the **Gemini free tier**. The Claude subscription is **not** used for them (`claude-llm-provider` plan dropped, replaced by `gemini-free-tier-llm`). Claude Code is still used as a coding agent for the plan worker (R1).

## Findings behind this roadmap (2026-10-01 audit)

- **Discovery:** a live probe returned rows from Indeed and LinkedIn. Glassdoor (403 + "location not parsed"), ZipRecruiter (403) and Google (0 rows) fail, and `python-jobspy` 1.1.82 is already the latest release. `~/.applypilot/searches.yaml` has `site_fail_threshold: 1`, which disables a board after one empty search. Workday has produced 1,195 jobs. SmartExtract has produced 0 ever.
- **Gemini today:** with your key, both the compat and native endpoints return 200 for `gemini-3.6-flash` and `gemini-3.1-flash-lite`. The key also lists `gemini-3.5-flash-lite`, `gemini-3.7-flash` and `gemini-3.8-flash`.
- **Scoring:** all 326 scored jobs have `fit_score = 0` from a Gemini 404 on 2026-08-27, before the fix in `e32a07b`. Zeros are never retried, so 0 jobs have ever been tailored or applied.
- **Where agents actually run:** only stage 6 (`applypilot apply`) is agentic. It launches `claude -p` (default) or `opencode run`, which drives Chrome through the Playwright MCP over CDP, plus the Gmail MCP and the credential server MCP. Stages 1–5 are plain Python, with single-shot LLM calls through `applypilot/llm.py`.
- **OpenCode is used in 3 places:** (1) `scripts/plan_worker.py` (dev tooling, which R1 replaces), (2) `applypilot apply --backend opencode` (optional; Claude is already the default, kept as-is), and (3) the OpenCode Zen LLM provider (`OPENCODE_API_KEY`, which you haven't configured; kept as-is).

## Backlog (not yet planned)

- Run apply-stage dry runs (`applypilot apply --dry-run`) end to end once R5 produces resumes.
- Re-evaluate the OpenCode Zen provider and `--backend opencode` for removal once Claude paths are proven.
- Commit-hygiene: `plan_worker` produced repeated "move X to completed" commits. Consider having the worker own all `plan_queue.json` writes.

## Done

| ID | Initiative | Plan | Completed |
|---|---|---|---|
| (earlier plans) | See `agents/plan_queue.json` → `completed` and `agents/CHANGELOG.md` | | ≤ 2026-09-01 |
