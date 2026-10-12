# AGENTS.md (auto-loaded every session; keep it short)

ApplyPilot: a Python 3.11+ job-application pipeline (discover → enrich → extract → score → tailor → cover → apply), with a
FastAPI + React dashboard (`applypilot serve`, `dashboard/`, built bundle committed in `src/applypilot/web/static`).
Data lives in Postgres (`APPLYPILOT_DATABASE_URL`) or SQLite under `~/.applypilot`. LLM calls use the Gemini free tier (`llm.py`).

## Map
- `src/applypilot/`: `cli.py` (Typer entry), `pipeline.py`, stage packages (`discovery/`, `enrichment/`, `scoring/`, `apply/`),
  `database.py`/`db_pg.py`, `web/` (API), `wizard/` (init).
- "Dashboard" is three things: `apply/dashboard.py` = Rich TUI during `apply`; `view.py` = static HTML report; `web/` + `dashboard/` = the React app at `/app/`.
- `tests/`: hermetic by default (`conftest.py` blocks network). Opt-in tiers: `-m live --run-live`, `-m llm --run-llm`, `pg`.
- `agents/ROADMAP.md`: the only status tracker. `agents/plans/<slug>.md`: one spec per change (`done/` holds the archive).
- `agents/DEPLOY.md`: the prod/preprod layout and how deploys work. `scripts/agent/`: the unattended runner.

## Validate (run before claiming anything works)
`scripts/validate.sh` (ruff + pytest, ~45s) · `--full` (+ dashboard tests/build + docker build) · `--setup` in a new worktree.

## PIV loop (commands in `.claude/commands/`)
`/plan` (always with the user; sets risk) → `/implement <plan>` or `/schedule-build <slug>` (unattended on the VPS)
→ `/validate` → `/ship` (risk gate: auto-merge low, else PR on preprod). Bugs start with `/rca`.

## Rules
- Public fork (`origin` = SiddharthEngineer/ApplyPilot). PRs go to `origin` only (`--repo SiddharthEngineer/ApplyPilot`), never `upstream`.
- Never commit personal data (`personal/`, `~/.applypilot`, `.env`, resume or profile content, DB rows). Never print secrets; hand secret steps to the user.
- Work on a branch, never directly on `trunk`, except for plan/ROADMAP commits made in `/plan`.
- Never weaken tests to get to green. Never run `applypilot apply` (it submits real applications) unless the user asks.
- Prod deploys are the user's call (`agents/DEPLOY.md`). Never `chown`, force-push, drop volumes or databases, or run as root.
- When a plan task finishes: set its Status in the plan and the ROADMAP row. Git history and PRs are the changelog.
