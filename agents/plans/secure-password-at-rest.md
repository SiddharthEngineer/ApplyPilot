# Plan: Secure Passwords at Rest

**Started:** 2026-08-27
**Status:** 🔄 In Progress

---

## Goal

Eliminate plaintext passwords from MCP config JSON files on disk and harden file permissions on sensitive config files (`profile.json`, `.env`). After this plan, the only file containing plaintext passwords will be `profile.json`, protected by `0o600` permissions (owner-only read/write). MCP config files will contain zero secrets.

## Success Criteria

1. `.mcp-apply-{id}.json` files contain NO `APPLYPILOT_PW_*` environment variables — only `CAPSOLVER_API_KEY` and `APPLYPILOT_APP_DIR` (non-secret path).
2. `opencode.json` worker configs contain NO `APPLYPILOT_PW_*` environment variables.
3. `cred_server.py` reads passwords from `profile.json` (via `APPLYPILOT_APP_DIR` env var) instead of env vars.
4. `profile.json` is created with `0o600` permissions (owner-only) on all write paths (wizard, migration, `applypilot init`).
5. `.env` is created with `0o600` permissions on all write paths.
6. Existing `_get_password()` env-var path works as fallback when `APPLYPILOT_APP_DIR` is not set (backward compat).
7. All existing tests pass; new tests verify: no plaintext in MCP configs, profile.json reading, file permissions.
8. `ruff check src/` is clean.

## Task Chain

### Task 1: Update `cred_server.py` — Read Passwords from `profile.json`

**Files:** `src/applypilot/apply/cred_server.py` (modify)

**What:** Add a `_get_password_from_profile(ats)` function that reads passwords from `profile.json` instead of environment variables. The function resolves the profile path via the `APPLYPILOT_APP_DIR` env var (e.g., `~/.applypilot`), reads and parses `profile.json`, and returns the password for the requested ATS. Update `_get_password(ats)` to try profile.json first, then fall back to env vars for backward compatibility.

**Changes:**

1. Add `_get_password_from_profile(ats: str) -> str | None`:
   ```python
   def _get_password_from_profile(ats: str) -> str | None:
       """Read password from profile.json via APPLYPILOT_APP_DIR env var."""
       app_dir = os.environ.get("APPLYPILOT_APP_DIR")
       if not app_dir:
           return None
       profile_path = Path(app_dir) / "profile.json"
       if not profile_path.exists():
           return None
       try:
           profile = json.loads(profile_path.read_text(encoding="utf-8"))
           return profile.get("site_passwords", {}).get(ats) or None
       except (json.JSONDecodeError, OSError):
           return None
   ```

2. Update `_get_password(ats)` to try profile first, env var fallback:
   ```python
   def _get_password(ats: str) -> str | None:
       password = _get_password_from_profile(ats)
       if password:
           return password
       env_var = ATS_PW_ENV.get(ats)
       if not env_var:
           return None
       return os.environ.get(env_var) or None
   ```

3. Add `from pathlib import Path` to imports.

**Acceptance criteria:**
- `_get_password_from_profile("workday")` returns the password when `APPLYPILOT_APP_DIR` is set and `profile.json` exists.
- `_get_password_from_profile("workday")` returns `None` when `APPLYPILOT_APP_DIR` is not set.
- `_get_password_from_profile("workday")` returns `None` when `profile.json` is malformed.
- `_get_password("workday")` returns profile.json password when `APPLYPILOT_APP_DIR` is set (even if env var is also set).
- `_get_password("workday")` falls back to env var when `APPLYPILOT_APP_DIR` is not set.

**Status:** ❌ Not started

---

### Task 2: Update `launcher.py` — Remove Passwords from MCP Configs

**Files:** `src/applypilot/apply/launcher.py` (modify)

**What:** Remove `site_passwords` parameter and `APPLYPILOT_PW_*` env vars from `_make_mcp_config()` and `_make_opencode_config()`. Replace with `APPLYPILOT_APP_DIR` env var (non-secret path) and `CAPSOLVER_API_KEY`. Update `run_job()` and `gen_prompt()` to stop loading `site_passwords` from profile.

**Changes:**

1. **Update `_make_mcp_config(cdp_port, site_passwords=None)`** signature and body:
   - Remove `site_passwords` parameter
   - Replace `pw_env` with:
     ```python
     cred_env = {
         "APPLYPILOT_APP_DIR": str(config.APP_DIR),
         "CAPSOLVER_API_KEY": os.environ.get("CAPSOLVER_API_KEY", ""),
     }
     ```
   - Keep `"env": cred_env` in the cred server entry

2. **Update `_make_opencode_config(cdp_port, site_passwords=None)`** — same changes as above.

3. **Update `run_job()`** (lines 513-528):
   - Remove lines 514-515 (`profile = config.load_profile()` / `site_passwords = profile.get(...)`)
   - Update calls: `_make_opencode_config(port)` and `_make_mcp_config(port)` (no `site_passwords` arg)

4. **Update `gen_prompt()`** (lines 304-309):
   - Remove lines 305-306 (`profile = config.load_profile()` / `site_passwords = ...`)
   - Update call: `_make_mcp_config(port)` (no `site_passwords` arg)

**Acceptance criteria:**
- `_make_mcp_config(9222)` returns config where `cred.env` contains `APPLYPILOT_APP_DIR` and `CAPSOLVER_API_KEY` but NO `APPLYPILOT_PW_*` keys.
- `_make_opencode_config(9222)` returns config where `cred.env` contains `APPLYPILOT_APP_DIR` and `CAPSOLVER_API_KEY` but NO `APPLYPILOT_PW_*` keys.
- `run_job()` no longer calls `config.load_profile()` for the purpose of extracting `site_passwords`.
- `gen_prompt()` no longer calls `config.load_profile()` for the purpose of extracting `site_passwords`.
- Existing playwright/gmail MCP server entries are unchanged.

**Status:** ❌ Not started

---

### Task 3: Set Restrictive File Permissions on Sensitive Files

**Files:** `src/applypilot/config.py` (modify), `src/applypilot/wizard/init.py` (modify)

**What:** Add a `set_restricted_permissions(path)` helper in `config.py` that sets `0o600` permissions on a file (owner-only read/write). Apply it everywhere `profile.json` and `.env` are written to disk.

**Changes:**

1. **Add to `config.py`:**
   ```python
   import stat

   def set_restricted_permissions(path: Path) -> None:
       """Set file permissions to owner-only read/write (0o600).

       No-op on platforms that don't support Unix permissions (e.g., Windows
       without NTFS ACLs). Errors are logged but not raised.
       """
       try:
           path.chmod(stat.S_IRUSR | stat.S_IWUSR)
       except (OSError, AttributeError):
           pass  # Windows or permission error — best effort
   ```

2. **Apply in `config.py:load_profile()`** — after the migration write (line 139-141), call `set_restricted_permissions(PROFILE_PATH)`.

3. **Apply in `wizard/init.py:_setup_profile()`** — after `PROFILE_PATH.write_text(...)` (line 403), call `config.set_restricted_permissions(PROFILE_PATH)`.

4. **Apply in `wizard/init.py:_setup_ai_features()`** — after `ENV_PATH.write_text(...)` (the `.env` write), call `config.set_restricted_permissions(ENV_PATH)`.

5. **Apply in `config.py:ensure_dirs()`** — after creating `APP_DIR`, set `0o700` on the directory itself (owner-only traversal).

**Acceptance criteria:**
- After `applypilot init`, `ls -la ~/.applypilot/profile.json` shows `-rw-------` (0600).
- After `applypilot init`, `ls -la ~/.applypilot/.env` shows `-rw-------` (0600).
- After `applypilot init`, `ls -ld ~/.applypilot` shows `drwx------` (0700).
- On macOS/Linux, `stat -f "%Lp" ~/.applypilot/profile.json` returns `600`.
- `set_restricted_permissions()` does not raise on Windows or when permissions cannot be set.

**Status:** ❌ Not started

---

### Task 4: Tests and Verification

**Files:** `tests/test_cred_server.py` (modify), `tests/test_launcher.py` (modify), `tests/test_config.py` (new or modify)

**What:** Update existing tests to reflect the new MCP config structure (no `APPLYPILOT_PW_*`), add tests for profile.json reading in cred_server, and add tests for file permission enforcement.

**Changes:**

1. **Update `tests/test_launcher.py`:**
   - Remove/update `test_site_passwords_passed_to_env` — MCP config no longer has `APPLYPILOT_PW_*` keys
   - Remove/update `test_empty_site_passwords` and `test_none_site_passwords` — no longer applicable
   - Update `test_cred_server_has_env_vars` — assert `APPLYPILOT_APP_DIR` is in env, `APPLYPILOT_PW_*` are NOT
   - Add `test_no_passwords_in_mcp_config` — verify no `APPLYPILOT_PW_*` in any MCP config output
   - Add `test_capsolver_key_from_env` — still works (unchanged)
   - Update `test_site_passwords_passed` for OpenCode config — same changes

2. **Update `tests/test_cred_server.py`:**
   - Add `TestGetPasswordFromProfile` class:
     - `test_reads_from_profile_json` — mock `APPLYPILOT_APP_DIR` + temp `profile.json`, verify password returned
     - `test_returns_none_when_app_dir_not_set` — no `APPLYPILOT_APP_DIR` env var
     - `test_returns_none_when_profile_missing` — `APPLYPILOT_APP_DIR` set but no `profile.json`
     - `test_returns_none_when_malformed_json` — `profile.json` contains invalid JSON
     - `test_returns_none_when_ats_not_in_profile` — `profile.json` exists but requested ATS has no password
   - Update `TestGetPassword` — add test that profile.json takes precedence over env var

3. **Add file permission tests** (in `tests/test_config.py` or new file):
   - `test_set_restricted_permissions_sets_0o600` — create temp file, call `set_restricted_permissions`, verify mode
   - `test_set_restricted_permissions_no_error_on_missing_file` — verify no exception
   - `test_load_profile_migration_sets_permissions` — after migration write, file has 0o600

4. **Regression:**
   - All 205+ existing tests pass
   - `ruff check src/` clean

**Acceptance criteria:**
- `test_no_passwords_in_mcp_config` passes — grep for `APPLYPILOT_PW_` in MCP config output returns zero matches.
- `test_reads_from_profile_json` passes — cred_server correctly reads from profile.json.
- `test_set_restricted_permissions_sets_0o600` passes.
- All existing tests updated and passing (no broken assertions from removed `site_passwords` parameter).
- `ruff check src/` returns zero new issues.

**Status:** ❌ Not started

---

## Implementation Order

```
Task 1 (cred_server.py)  ──┐
                            ├──▶ Task 4 (tests)
Task 2 (launcher.py)     ──┤
                            │
Task 3 (file permissions) ──┘
```

**Execution order:**
1. Task 1 (cred_server.py) — independent, can start immediately
2. Task 2 (launcher.py) — independent of Task 1, can start in parallel
3. Task 3 (file permissions) — independent of Tasks 1-2, can start in parallel
4. Task 4 (tests) — depends on Tasks 1-3 being complete

Tasks 1, 2, and 3 are **fully independent** and can be implemented in parallel by separate agents. Task 4 depends on all three.

## Key Design Decisions

1. **`APPLYPILOT_APP_DIR` env var, not hardcoded path** — The MCP config passes the app directory path (e.g., `~/.applypilot`) as a non-secret env var. This avoids importing `config.py` into `cred_server.py` and keeps the MCP server self-contained.

2. **Profile-first with env-var fallback** — `_get_password()` tries `profile.json` first, then falls back to env vars. This provides backward compatibility for any existing MCP configs that still have `APPLYPILOT_PW_*` env vars, and allows gradual migration.

3. **`0o600` permissions, not encryption** — File permissions are the simplest defense that works cross-platform without new dependencies. They prevent other users on a shared system from reading passwords, but do not protect against root access or physical disk access. This is the same protection level as SSH keys and other standard Unix credential files.

4. **`0o700` on APP_DIR** — The `~/.applypilot` directory itself gets owner-only permissions, preventing other users from listing its contents.

5. **No changes to profile.json schema** — Passwords remain in plaintext JSON within `profile.json`. The protection is at the file permission level. This avoids a migration step for existing users.

6. **`CAPSOLVER_API_KEY` remains in MCP config** — This key is not a user password (it's a third-party API key) and is already in the parent process env. It is passed through to the cred-server for future `captcha_solve` tool use.

7. **`set_restricted_permissions` is best-effort** — On Windows or when permissions cannot be set (e.g., running as root), the function silently succeeds. This prevents crashes on unsupported platforms while still hardening on Unix.

---

## Historical Record

- **2026-08-27:** Plan created. Approach A selected (file permissions + no plaintext MCP configs). Four tasks defined: cred_server.py profile reading, launcher.py password removal, file permission hardening, tests.
