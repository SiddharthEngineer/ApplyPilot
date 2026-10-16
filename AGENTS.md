# AGENTS.md (auto-loaded every session; keep it short)

Strelitzia (formerly ApplyPilot): a Python 3.11+ job-discovery pipeline (discover → enrich → extract → score → tailor → cover),
with a multi-user FastAPI + React dashboard where each signed-in user tracks applications by hand (`strelitzia serve`, `dashboard/`, built bundle
committed in `src/strelitzia/web/static`). There is no auto-apply. Data lives in Postgres (`STRELITZIA_DATABASE_URL`) or SQLite
under `~/.strelitzia`. The old `applypilot` CLI, `APPLYPILOT_*` env vars and `~/.applypilot` still work as deprecated fallbacks
(`config.env()`, `config.resolve_app_dir()`). LLM calls use the Claude API (`llm.py`, `llm_batch.py`; spend in `llm_usage`).

## Map
- `src/strelitzia/`: `cli.py` (Typer entry, admin-only: serve, init, doctor, db, users), `pipeline.py`, stage packages
  (`discovery/`, `enrichment/`, `scoring/`), `database.py`/`db_pg.py`, `web/` (API), `wizard/` (init).
- Accounts: `users.py` (owner + invited emails, caps; every per-user row has `user_id`), `web/auth.py` (Google sign-in,
  off until `STRELITZIA_GOOGLE_CLIENT_ID` is set; then every request has a signed-in user), `web/admin.py` (Users page).
- The dashboard is `web/` + `dashboard/`: the React app at `/app/` (Sunrise theme tokens in `dashboard/src/styles.css`). Per-user work happens only there.
- `tests/`: hermetic by default (`conftest.py` blocks network). Opt-in tiers: `-m live --run-live`, `-m llm --run-llm`, `pg`.
- `agents/ROADMAP.md`: the only status tracker. `agents/plans/<slug>.md`: one spec per change (`done/` holds the archive).
- `agents/DEPLOY.md`: the prod/preprod layout and how deploys work. `scripts/agent/`: the unattended runner.

## Validate (run before claiming anything works)
`scripts/validate.sh` (ruff + pytest, ~45s) · `--full` (+ dashboard tests/build + docker build) · `--setup` in a new worktree.

## PIV loop (commands in `.claude/commands/`)
`/plan` (always with the user; sets risk) → `/implement <plan>` or `/schedule-build <slug>` (unattended on the VPS)
→ `/validate` → `/ship` (risk gate: auto-merge low, else PR on preprod). Bugs start with `/rca`.

## Rules
- Public repo (`origin` = SiddharthEngineer/Strelitzia, AGPL-3.0, keep the upstream credit in `NOTICE`). PRs go to `origin` only (`--repo SiddharthEngineer/Strelitzia`), never `upstream`.
- Never commit personal data (`personal/`, `~/.strelitzia` or `~/.applypilot`, `.env`, resume or profile content, DB rows). Never print secrets; hand secret steps to the user.
- Work on a branch, never directly on `trunk`, except for plan/ROADMAP commits made in `/plan`.
- Never weaken tests to get to green. Never add code that submits applications: Strelitzia only discovers, tailors and tracks.
- Prod deploys are the user's call (`agents/DEPLOY.md`). Never `chown`, force-push, drop volumes or databases, or run as root.
- When a plan task finishes: set its Status in the plan and the ROADMAP row. Git history and PRs are the changelog.
