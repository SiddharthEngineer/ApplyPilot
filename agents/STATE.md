# Current State

**Last updated:** 2026-08-27

## Active Plan: Secure Passwords at Rest

Plan file: `agents/plans/secure-password-at-rest.md`

### Progress

| Task | Status |
|------|--------|
| Task 1: Update cred_server.py — Read passwords from profile.json | ✅ Complete |
| Task 2: Update launcher.py — Remove passwords from MCP configs | ✅ Complete |
| Task 3: Set restrictive file permissions on sensitive files | ✅ Complete |
| Task 4: Tests and verification | ✅ Complete |

### Current Task

Completed. Secure Passwords at Rest plan is fully implemented.

### Completed This Session

- **Secure Passwords at Rest** — Eliminated plaintext passwords from MCP config JSON files on disk and hardened file permissions on sensitive config files. Key changes:
  - `cred_server.py`: Added `_get_password_from_profile()` that reads passwords from `profile.json` via `APPLYPILOT_APP_DIR` env var. `_get_password()` now tries profile.json first, then falls back to env vars for backward compatibility.
  - `launcher.py`: Removed `site_passwords` parameter from `_make_mcp_config()` and `_make_opencode_config()`. MCP configs now contain only `APPLYPILOT_APP_DIR` (non-secret path) and `CAPSOLVER_API_KEY` — zero `APPLYPILOT_PW_*` keys. Removed profile loading from `run_job()` and `gen_prompt()` for the purpose of extracting site passwords.
  - `config.py`: Added `set_restricted_permissions()` helper (0o600, best-effort). Applied in `load_profile()` after migration write. Added 0o700 permissions on `APP_DIR` in `ensure_dirs()`.
  - `wizard/init.py`: Applied `set_restricted_permissions()` after all `profile.json` and `.env` writes.
  - Created `tests/test_config.py` with 5 tests for file permission enforcement.
  - Updated `tests/test_cred_server.py` with 6 new tests for profile.json reading.
  - Updated `tests/test_launcher.py` — removed obsolete `site_passwords` tests, added `test_no_passwords_in_mcp_config` and `test_app_dir_points_to_config_app_dir`.

### Test Results

```
42 targeted tests passed (test_launcher, test_cred_server, test_config)
193 total tests passed — zero failures
ruff check: clean on all modified files
```

### Key Decisions

- **Profile-first with env-var fallback** — `_get_password()` tries profile.json first, then falls back to env vars. This provides backward compatibility for any existing MCP configs that still have `APPLYPILOT_PW_*` env vars.
- **`APPLYPILOT_APP_DIR` env var, not hardcoded path** — The MCP config passes the app directory path as a non-secret env var to cred-server, keeping it self-contained.
- **`0o600` permissions, not encryption** — File permissions are the simplest defense that works cross-platform without new dependencies.
- **`0o700` on APP_DIR** — The `~/.applypilot` directory gets owner-only permissions.
- **`CAPSOLVER_API_KEY` remains in MCP config** — This key is not a user password and is already in the parent process env.
- **`set_restricted_permissions` is best-effort** — On Windows or when permissions cannot be set, the function silently succeeds.

### Blockers

None.

### Recommended Next Step

All tasks in the Secure Passwords at Rest plan are complete. No remaining work.

## Project Overview

ApplyPilot v0.3.0 is a 6-stage autonomous job application pipeline. Stage 6 (auto-apply) supports two agent backends:

| Backend | Default | Cost | CLI |
|---------|---------|------|-----|
| `claude` | Yes | Anthropic API | `claude -p` |
| `opencode` | No | Free (own API keys) | `opencode run` |

Users choose via `applypilot apply --backend <claude|opencode>`.

## Key Files

| File | Role |
|------|------|
| `src/applypilot/scoring/content_library.py` | Content library parser |
| `src/applypilot/scoring/tailor.py` | Resume tailoring with LLM + validation + judge |
| `src/applypilot/scoring/validator.py` | Banned words, fabrication detection, structural checks |
| `src/applypilot/scoring/pdf.py` | Text-to-PDF via Playwright |
| `src/applypilot/cli.py` | CLI entry points |
| `src/applypilot/config.py` | Paths, tier system, profile/config loaders |
| `src/applypilot/pipeline.py` | 6-stage pipeline orchestrator |
| `src/applypilot/wizard/init.py` | Interactive setup wizard |
| `src/applypilot/apply/cred_server.py` | MCP credential server (hides passwords from LLM) |
| `src/applypilot/apply/launcher.py` | Apply orchestration: config builders, job execution |
| `src/applypilot/apply/prompt.py` | Prompt builder for the apply agent |
| `tests/test_content_library_e2e.py` | End-to-end integration tests for content-library tailoring |
| `tests/test_init_wizard.py` | Init wizard tests for content library support |
| `tests/test_doctor_content_library.py` | Doctor command content library validation tests |

## Testing

- Run `PYTHONPATH=src .venv/bin/python -m pytest tests/ -v` for unit tests
- Run `ruff check src/` for linting
