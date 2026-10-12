# Plan: LLM rate limits out of .env (packaged defaults + override file), one .env per environment
**Status:** ❌ Not started
**Risk:** review (touches `config.py`, `cli.py` and the limit defaults; the VPS cleanup after merge is manual step M13)

## Goal
The per-model Gemini limits stop being multi-line JSON inside `.env` files. The free-tier limits ship with ApplyPilot as
defaults (`src/applypilot/config/llm_limits.yaml`), since they're public facts about the models, not secrets. An optional
`~/.applypilot/llm_limits.yaml` overrides them. After this, `.env` holds only secrets and per-host settings, and each
environment has exactly one: the host CLI and the prod dashboard read `~/.applypilot/.env`, and preprod has none, by design.

## Context
- `src/applypilot/llm.py:131-170`: `_parse_model_limits(var)` parses `LLM_RPM_LIMITS` / `LLM_RPD_LIMITS` (JSON, trailing
  commas allowed, warns once). `model_limits(model) -> (rpm, rpd)` falls back to `LLM_RPM_LIMIT` / `LLM_RPD_LIMIT`. Callers
  only use `model_limits`. Tests: `tests/test_llm.py:576-620`.
- Mirror the packaged-YAML pattern: `config.py:40` `CONFIG_DIR`, plus `sites.yaml` / `searches.example.yaml` loading at `config.py:183-200`.
  `APP_DIR` (`config.py:10`) honours `APPLYPILOT_DIR`, so tests can point the override file at `tmp_path`.
- Starting defaults: the values in `.env.example:14-21` (`gemini-3.1-flash-lite` rpm 15 / rpd 500, `gemini-3.6-flash` rpm 5 / rpd 20).
  Manual step M13 (after merge) reconciles them with the live values on the VPS. Unattended agents can't read `~/.applypilot`, so don't try.
- `cli.py:916-930` (`applypilot doctor`) warns "RPM limit off: set LLM_RPM_LIMITS (per model) in .env" when the env var is absent.
- **Env file duplication on the VPS (background for M13; not part of this plan's code).** There are three copies with the same keys: `~/.applypilot/.env` (read by `load_env()`, `config.py:263`),
  `~/src/ApplyPilot/.env` (only reached through the `load_dotenv()` CWD fallback), and `/srv/ApplyPilot-prod/.env`. That last one is mounted at
  `/app/.env` by engineerfamily `docker-compose.prod.yml` ("Gemini key + LLM limits (multi-line JSON values, so not env_file)").
  The prod container also mounts `/home/dev/.applypilot`, and its HOME is `/home/dev` (checked 2026-10-12), so `load_env()` already reads
  `~/.applypilot/.env` there and the `/app/.env` mount is redundant. `/app/.env` is loaded second and never overrides a value.
- The env files also hold `CLAUDE_CODE_OAUTH_TOKEN`. The dashboard doesn't use it (`grep -rn CLAUDE_CODE_OAUTH_TOKEN src` finds nothing), but it ends up
  in the dashboard container's environment. The runner's copy lives in `~/.config/applypilot-agent/env`.
- Preprod has no `.env` and mounts no LLM keys on purpose (user decision 2026-10-12). Score and tailor actions fail there with a missing-key error.

## Tasks
### Task 1: Packaged limits + override file
**Runs:** unattended
**Files:** src/applypilot/config/llm_limits.yaml (new), src/applypilot/llm.py (modify), src/applypilot/config.py (modify), src/applypilot/cli.py (modify), tests/test_llm.py (modify)
**What:** Add `LLM_LIMITS_PATH = APP_DIR / "llm_limits.yaml"` in `config.py`. In `llm.py`, add `load_model_limits() -> dict[str, dict[str, int]]`,
which returns `{"rpm": {...}, "rpd": {...}}`, merged per model from packaged defaults ← user file ← the legacy `LLM_RPM_LIMITS` / `LLM_RPD_LIMITS` env vars.
The env vars keep working, but the first use logs a one-time deprecation pointing at `llm_limits.yaml`. A malformed user file logs once and is ignored.
`model_limits()` uses it and keeps the `LLM_RPM_LIMIT` / `LLM_RPD_LIMIT` fallback for unlisted models. In `doctor`, report where the limits came from
(packaged / user file / env) instead of warning when the env var is absent.
YAML shape:
```yaml
# Requests per minute / per day, per model (Gemini free tier). Override in ~/.applypilot/llm_limits.yaml.
rpm: {gemini-3.1-flash-lite: 15, gemini-3.6-flash: 5}
rpd: {gemini-3.1-flash-lite: 500, gemini-3.6-flash: 20}
```
**Validate:** new tests: packaged defaults apply with no env and no user file; a user file overrides one model and leaves the rest;
the env var still wins and warns once; a malformed user file → defaults plus one warning. The existing `tests/test_llm.py` limit tests pass unchanged.
`.venv/bin/pytest tests/test_llm.py -q` → pass; `scripts/validate.sh` → ALL PASS.
**Status:** ✅ Complete (2026-10-12, this commit: "Task 1 Packaged limits + override file"). Two existing tests that relied on the global `LLM_RPM_LIMIT` reaching the default Gemini models now use an unlisted model (asserts unchanged).

### Task 2: Docs
**Runs:** unattended
**Files:** .env.example, README.md (modify)
**What:** Replace the `LLM_RPM_LIMITS` / `LLM_RPD_LIMITS` JSON block in `.env.example` with a two-line pointer to `src/applypilot/config/llm_limits.yaml` and
`~/.applypilot/llm_limits.yaml`. In the README configuration section, say that `.env` is for secrets and per-host settings only.
**Validate:** `grep -n "LLM_RPM_LIMITS\|LLM_RPD_LIMITS" .env.example README.md` → only the deprecation mention; `scripts/validate.sh` → ALL PASS.
**Status:** ❌

## Validation
- `scripts/validate.sh --full` → ALL PASS.
- `.venv/bin/pytest tests/test_llm.py -q` → pass, including the new limit-source tests.

## Human test steps (preprod)
1. On preprod (`https://applypilot-preprod.engineerfamily.net/app/`, once the Cloudflare route is fixed, or `http://127.0.0.1:8080/app/`), the dashboard
   loads and the job list renders. Preprod has no LLM key, so don't expect scoring to work there.
2. On the VPS, with the PR branch checked out in a scratch worktree: `APPLYPILOT_DIR=$(mktemp -d) .venv/bin/applypilot doctor` shows the
   rate limits as coming from the packaged defaults.
3. After merge, do manual step M13 in `agents/ROADMAP.md` (VPS `.env` cleanup and prod redeploy).

## After merge (you, ROADMAP M13; not for the agent)
Done by the user with an interactive Claude session once this plan's PR is merged:
1. Compare the live `LLM_RPM_LIMITS` / `LLM_RPD_LIMITS` in `~/.applypilot/.env` with the packaged defaults. Write `~/.applypilot/llm_limits.yaml` only for models that differ.
2. Remove both JSON vars from `~/.applypilot/.env`.
3. Confirm `~/src/ApplyPilot/.env` and `/srv/ApplyPilot-prod/.env` add nothing that `~/.applypilot/.env` lacks (compare key names; the prod copy's `APPLYPILOT_DATABASE_URL` is overridden by compose anyway), then delete both.
4. Drop the `/app/.env` mount from engineerfamily `docker-compose.prod.yml` (PR, deploy with `make tag-prod`).
5. Decide with the user where `CLAUDE_CODE_OAUTH_TOKEN` belongs on the host. If nothing outside the runner uses it, remove it from `~/.applypilot/.env`.
6. Run `make applypilot-deploy`.
**Validate:** `applypilot doctor` → limits from packaged defaults (or the user file); `curl -s -u … https://applypilot.engineerfamily.net/app/api/health` → `"ok": true`;
`ls /srv/ApplyPilot-prod/.env ~/src/ApplyPilot/.env` → no such file; `docker exec engineerfamily-applypilot-1 python -c 'from applypilot.config import load_env; import os; load_env(); print("CLAUDE_CODE_OAUTH_TOKEN" in os.environ)'` → False (if removed in step 5).

## Decisions
1. Limits are committed defaults, not secrets. `.env` is only for secrets and per-host settings (user, 2026-10-12).
2. The legacy env vars keep working with a deprecation warning, so an old `.env` doesn't silently lose its limits.
3. Preprod stays keyless (user, 2026-10-12).
4. The VPS `.env` cleanup and prod redeploy are manual step M13, not a task, so the whole plan runs unattended (user, 2026-10-12).
