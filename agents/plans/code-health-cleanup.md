# Plan: Code health cleanup (from the 2026-10-11 repo audit)
**Status:** ❌ Not started
**Risk:** low (each task is a small, behavior-preserving change; the gate escalates anything bigger)

## Goal
Make the codebase cheaper for agents to work in: less dead code, a faster test suite, consistent config, and the dashboard covered by CI.
This plan isn't refined yet. Run `/plan code-health-cleanup` with the user to finalize the tasks before scheduling it.

## Context (audit findings)
- 37 pyflakes findings (`ruff check --select F src tests scripts`), all unused imports and variables, e.g. `apply/launcher.py:29`, `enrichment/detail.py:24-26`, `smartextract.py:22-33`.
- Dead functions (no references found by grep): `smartextract.clean_card_html` (:773), `enrichment/detail.stream_detail` (:787).
- Duplicated helpers: `extract_json` in `smartextract.py:879` and `scoring/tailor.py:397`; `_now()` in `tracking.py:27` and `web/tasks.py:36`.
- Slow tests: 3 tests in `tests/test_cred_server.py::TestCaptchaSolve` each sleep 3s (about 20% of the suite).
- Env var drift: `APPLYPILOT_DIR` (config.py:10) and `APPLYPILOT_APP_DIR` (cred_server.py:144, launcher.py:74,108). Missing from `.env.example`: `EXTRACT_BATCH_SIZE`, `LLM_RPD_LIMIT`, `LLM_API_KEY`, `APPLYPILOT_WEB_DIR`, `APPLYPILOT_DIR`, `CHROME_PATH`.
- `python-jobspy` isn't a declared dependency or extra; it's installed by hand in CI, `cloud_setup.sh` and the README.
- CI doesn't run the dashboard tests or check the committed bundle (`scripts/validate.sh --full` does this locally).
- Three different things are called "dashboard": `apply/dashboard.py` (Rich TUI), `view.py` (static HTML), `web/` + `dashboard/` (React app).
- God-files: `smartextract.py` (1444 lines), `scoring/tailor.py` (1231), `cli.py` (1019). Splitting them is a separate, review-risk plan.

## Tasks
To be written with the user (suggested order: unused imports + dead code → duplicated helpers → test sleep →
env vars + `.env.example` → jobspy as an extra → CI dashboard job → widen the pinned ruff rule set to include `F401`).

## Decisions
1. Splitting the god-files is out of scope here. It changes many lines and needs review.
