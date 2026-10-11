# Plan: Hide Passwords from the LLM

**Started:** 2026-08-26
**Status:** ✅ Complete

---

## Goal

The LLM should never see, read, or output any password or secret. It should still be able to log in to ATS platforms by delegating credential entry to a trusted component.

---

## Success Criteria

1. The LLM prompt contains NO passwords, API keys, or secrets.
2. `ats_login` tool successfully logs into Workday, Greenhouse, Lever, and Ashby.
3. The LLM can still complete the full apply flow (navigate → detect ATS → login → fill form → submit).
4. Passwords are only present in: `profile.json` (at rest) and MCP server subprocess env vars (in memory).
5. For OpenCode, the prompt is not visible in `ps aux` output.
6. CapSolver API key is not in the prompt text.
7. All existing tests pass.
8. New tests verify: no passwords in prompt, cred_server env var reading, MCP config includes cred-server.

---

## Problem

Passwords are embedded directly into the LLM prompt as a Markdown table (`src/applypilot/apply/prompt.py:580-585`). The LLM (Claude Code or OpenCode) sees plaintext credentials for every ATS platform, then uses Playwright MCP tools to type them into login forms. This is dangerous because:

- Passwords are in the LLM's context window (sent to Anthropic/OpenAI API providers)
- For OpenCode, the prompt (with passwords) is visible in the OS process table as a CLI argument
- Passwords appear in worker log files via the agent's output
- The LLM could theoretically output or log the passwords

The CapSolver API key (`prompt.py:229`) has the same problem — it's embedded in the prompt text.

## Feasibility Assessment

**Is this possible?** Yes, but not trivially. The LLM currently needs to: (1) identify which ATS it's on, (2) know the password, (3) type it into the form. We can offload step (2) and (3) to a custom MCP server, leaving the LLM to only identify the ATS.

**Is it feasible given the current setup?** Mostly. Both Claude Code and OpenCode support custom MCP servers via config. The main complexity is building and maintaining the custom MCP server.

**Can we use environment variables alone?** No. Env vars solve storage-at-rest, but the LLM would still need to read the env var value to type it into the form, which means the password enters the context. The only way to keep secrets out of the LLM's context is to have a trusted component handle the credential entry.

## Architecture: Custom Credential MCP Server

### How it works

```
┌─────────────────────────────────────────────────┐
│  ApplyPilot (launcher.py)                        │
│                                                  │
│  1. Reads passwords from profile.json            │
│  2. Passes them as ENV VARS to MCP server process│
│  3. Generates MCP config with cred-server        │
│  4. Sends prompt to LLM (NO passwords in it)     │
└──────────┬──────────────────────┬────────────────┘
           │                      │
           ▼                      ▼
┌──────────────────┐   ┌──────────────────────────┐
│  LLM Agent       │   │  cred-server (MCP)       │
│  (Claude/OC)     │   │  - Reads env vars        │
│                  │   │  - Connects to Chrome CDP │
│  Prompt says:    │   │  - Finds password field   │
│  "Call           │──▶│  - Types password         │
│   ats_login      │   │  - Clicks submit          │
│   tool with      │◀──│  - Returns success/fail   │
│   ats=workday"   │   │                          │
│                  │   │  LLM NEVER sees password  │
└──────────────────┘   └──────────────────────────┘
```

### Key insight

The custom MCP server connects to the **same Chrome instance** via CDP (Chrome DevTools Protocol) on the same port the Playwright MCP server uses. It uses Playwright's `chromium.connectOverCDP()` to find and fill the password field, then returns a success/failure message. The LLM never handles the password.

### Why not just env vars?

Environment variables solve storage-at-rest, but the LLM still needs to read the env var value to type it into the form. Any path where the LLM reads the password (via `browser_evaluate` accessing `process.env`, via a bash command, etc.) puts the password into the LLM's context. The only safe approach is a trusted intermediary that the LLM calls by name (e.g., `ats_login(ats="workday")`) without ever receiving the credential value.

---

## Task Chain

### Task 1: Create `cred_server.py` — MCP Credential Server

**Files:** `src/applypilot/apply/cred_server.py` (new)

**What:** Build a standalone Python MCP server over stdio that handles credential entry. The LLM calls this server's tool instead of typing passwords itself.

**Tool definition:**
```
Tool: ats_login
Description: Log in to an ATS platform using stored credentials
Parameters:
  - ats: string (one of: workday, greenhouse, lever, ashby)
  - email: string (the user's email)
  - cdp_port: integer (Chrome CDP port, default 9222)
Returns: { success: bool, message: string }
```

**Server logic:**
1. Read the password from env var `APPLYPILOT_PW_{ATS_KEY}` (e.g., `APPLYPILOT_PW_WORKDAY`).
2. Connect to Chrome via CDP on the specified port using `playwright.chromium.connectOverCDP()`.
3. Find the password input field on the current page (`input[type="password"]`).
4. Find the email input field (`input[type="email"]`, `input[name*="email"]`, etc.).
5. Fill both fields and click the submit button.
6. Wait briefly for navigation/response.
7. Return success or failure.

**Error cases:**
- No password env var set for the requested ATS → return `{success: false, message: "no_password_configured"}`.
- CDP connection failure → return `{success: false, message: "cdp_connection_failed"}`.
- Form fields not found → return `{success: false, message: "form_not_found"}`.

**Shared interface contract (for Tasks 2-3):**
- MCP server name in config: `"cred"`
- Env var pattern: `APPLYPILOT_PW_{ATS_UPPER}` (e.g., `APPLYPILOT_PW_WORKDAY`, `APPLYPILOT_PW_GREENHOUSE`)
- Also reads `CAPSOLVER_API_KEY` from env (for future `captcha_solve` tool)

**Acceptance criteria:**
- Server starts and responds to MCP `tools/list` with `ats_login`.
- Unit tests pass for env var reading, tool dispatch, and all error paths (mock Playwright/CDP).
- No real Chrome needed for unit tests.

**Status:** ✅ Complete

---

### Task 2: Refactor `launcher.py` — Env Vars + OpenCode Security

**Files:** `src/applypilot/apply/launcher.py` (modify)

**What:** Wire the cred-server into MCP config, inject passwords as env vars, and fix the OpenCode prompt-in-CLI security issue.

**Changes:**

1. **Add `site_passwords` parameter** to `_make_mcp_config()` and `_make_opencode_config()`:
   ```python
   def _make_mcp_config(cdp_port: int, site_passwords: dict | None = None) -> dict:
   ```

2. **Add `"cred"` server entry** to both config builders:
   ```python
   "cred": {
       "command": sys.executable,
       "args": [str(Path(__file__).parent / "cred_server.py")],
       "env": {
           "APPLYPILOT_PW_WORKDAY": (site_passwords or {}).get("workday", ""),
           "APPLYPILOT_PW_GREENHOUSE": (site_passwords or {}).get("greenhouse", ""),
           "APPLYPILOT_PW_LEVER": (site_passwords or {}).get("lever", ""),
           "APPLYPILOT_PW_ASHBY": (site_passwords or {}).get("ashby", ""),
           "CAPSOLVER_API_KEY": os.environ.get("CAPSOLVER_API_KEY", ""),
       }
   }
   ```

3. **Update `_build_opencode_cmd()`** to write prompt to a file instead of passing as CLI arg:
   - Write prompt content to `{worker_dir}/prompt.txt`
   - Return `["opencode", "run", "--model", model, "--auto", "--format", "json", "--dir", str(worker_dir), "--prompt-file", str(worker_dir / "prompt.txt")]`
   - (Verify OpenCode supports `--prompt-file` or equivalent; if not, use stdin piping like Claude)

4. **Update `run_job()`** to load profile and extract `site_passwords`:
   ```python
   profile = config.load_profile()
   site_passwords = profile.get("site_passwords", {})
   ```
   Pass `site_passwords` to `_make_mcp_config()` / `_make_opencode_config()`.

5. **Update OpenCode permission rules** to allow the cred-server tools:
   ```python
   "permission": {
       "playwright_*": "allow",
       "gmail_*": "deny",
       "ats_login": "allow",
       "captcha_solve": "allow",
   }
   ```

**Acceptance criteria:**
- `_make_mcp_config(port, site_passwords={"workday": "secret"})` produces config with `"cred"` server containing `APPLYPILOT_PW_WORKDAY` env var.
- `_build_opencode_cmd()` no longer includes prompt text in returned args.
- `run_job()` loads profile and passes passwords to config builders.
- Existing MCP config for playwright/gmail is unchanged.

**Status:** ✅ Complete

---

### Task 3: Update `prompt.py` — Remove Passwords, Add Tool Instructions

**Files:** `src/applypilot/apply/prompt.py` (modify), `src/applypilot/apply/launcher.py` (minor — pass `cdp_port` to `build_prompt`)

**What:** Remove all secrets from the prompt text and replace with `ats_login` tool call instructions.

**Changes:**

1. **Remove password table** from step 5c (lines 580-585). Replace with:
   ```
   5c. Regular login form? Identify the ATS from the URL, then call the ats_login tool:

       | ATS | URL pattern |
       |-----|-------------|
       | Workday | *.myworkdayjobs.com |
       | Greenhouse | boards.greenhouse.io |
       | Lever | jobs.lever.co |
       | Ashby | jobs.ashbyhq.com |

       Call: ats_login(ats="<platform>", email="{personal['email']}", cdp_port={cdp_port})
       The tool handles filling email, password, and clicking Sign In.

       If ats_login returns success=false with "no_password_configured", output RESULT:FAILED:no_password_configured.
       If ats_login returns success=false with other reason, try sign up with same email (use a new random password via browser_evaluate), then retry ats_login.
   ```

2. **Remove `site_passwords` variable** from `build_prompt()` (lines 444-447) — it's no longer needed.

3. **Remove CapSolver API key** from `_captcha_section()` (line 229). Replace `API key: {capsolver_key}` with `API key: Read from CAPSOLVER_API_KEY env var (set on cred-server)`.

4. **Add `cdp_port` parameter** to `build_prompt()` so the tool call includes it:
   ```python
   def build_prompt(job: dict, tailored_resume: str, ..., cdp_port: int = 9222) -> str:
   ```

5. **Update `run_job()`** in launcher.py to pass `cdp_port` to `build_prompt()`.

**Acceptance criteria:**
- `build_prompt()` output contains zero passwords or API keys.
- Prompt contains `ats_login` tool call instructions with correct signature.
- `cdp_port` is interpolated into the tool call.
- URL pattern table is preserved (ATS identification still works).

**Status:** ✅ Complete

---

### Task 4: Tests, Verification, and Documentation

**Files:** `tests/test_cred_server.py` (new), `tests/test_launcher.py` (new), `tests/test_prompt.py` (new), `agents/STATE.md`, `agents/CHANGELOG.md`

**What:** Comprehensive test coverage for the new credential flow, verify no regressions, update project docs.

**Test cases:**

1. **`tests/test_cred_server.py`** (may partially exist from Task 1):
   - Env var reading: `APPLYPILOT_PW_WORKDAY` → correct password.
   - Missing env var → `no_password_configured` error.
   - Tool dispatch: valid `ats_login` call → correct handler invoked.
   - Error paths: CDP failure, form not found.

2. **`tests/test_launcher.py`**:
   - MCP config includes `"cred"` server with env vars when `site_passwords` provided.
   - MCP config works when `site_passwords` is None/empty (backward compat).
   - OpenCode cmd does not contain prompt text.
   - `run_job()` loads profile and passes passwords through.

3. **`tests/test_prompt.py`**:
   - Prompt output contains no passwords (grep for known password values).
   - Prompt output contains no API keys (grep for capsolver key patterns).
   - Prompt output contains `ats_login` tool call.
   - Prompt output contains URL pattern table.
   - `cdp_port` is interpolated correctly.

4. **Regression verification**:
   - All 117+ existing tests pass.
   - `ruff check src/` clean (no new issues).

5. **Documentation updates**:
   - `agents/STATE.md` — update with progress and current task.
   - `agents/CHANGELOG.md` — add historical entry for this feature.

**Acceptance criteria:**
- All new + existing tests pass.
- `ruff check` clean (no new issues).
- STATE.md and CHANGELOG.md updated.
- Clean commit with all changes.

**Status:** ✅ Complete

---

## Implementation Order

```
Task 1 (cred_server.py)    ──┐
                              ├──▶ Task 3 (prompt.py) ──▶ Task 4 (tests + docs)
Task 2 (launcher.py)       ──┘
```

Tasks 1 and 2 are **fully independent** and can be implemented in parallel by separate agents. Task 3 depends on both (references the tool name from Task 1 and the env var pattern from Task 2). Task 4 depends on all previous tasks.

Each task is a coherent unit of work that can be implemented and verified independently. Tasks 1-2 are the core new code. Task 3 removes secrets from the prompt. Task 4 verifies everything works together.

---

## Key Design Decisions

1. **MCP server, not env vars alone** — Env vars solve storage-at-rest but the LLM would still read the password to type it. A trusted MCP server keeps secrets out of the context entirely.
2. **Same Chrome instance via CDP** — The cred-server connects to the same Chrome the Playwright MCP server uses. Both can coexist; only the cred-server writes to form fields.
3. **Per-server `env` blocks** — MCP spec supports per-server env vars. Passwords are passed to the cred-server subprocess only, never to the LLM process.
4. **`site_passwords` parameter, not global state** — Config builders accept `site_passwords` as an argument (explicit dependency), not via module-level import.
5. **OpenCode prompt via file** — Prevents passwords from appearing in `ps aux`. Matches Claude's stdin-based approach conceptually.
6. **Backward compatible** — Old profiles still work. Passwords in `profile.json` are read and passed to MCP server. No user-facing changes to wizard or profile format.
7. **CapSolver key same treatment** — Passed as env var to cred-server. The `browser_evaluate` CAPTCHA scripts remain but the key is no longer in the prompt text.

---

## Risk & Mitigation

| Risk | Mitigation |
|---|---|
| CDP connection conflicts with Playwright MCP | Both servers connect read-only to CDP; only the cred-server writes to form fields. Test concurrent access. |
| Form field selectors vary across ATS platforms | Use robust selectors: `input[type="password"]`, `input[name*="email"]`, `input[name*="user"]`. Add ATS-specific fallbacks if needed. |
| MCP server process lifecycle | Server runs as long as the LLM agent session. Launcher starts it via MCP config; agent runtime manages lifecycle. |
| OpenCode MCP config format differences | OpenCode uses `mcpServers` format already; verify `env` block is supported. If not, pass env vars via a wrapper script. |
| OpenCode `--prompt-file` flag existence | Verify OpenCode CLI supports file-based prompt input. Fallback: pipe via stdin like Claude. |
| Backward compatibility | Old profiles still work — passwords in `profile.json` are read and passed to MCP server. No user-facing changes. |

---

## Estimated LOE

| Task | Hours | Notes |
|---|---|---|
| Task 1: cred_server.py | 3-4h | MCP protocol over stdio, CDP connection, form filling logic |
| Task 2: launcher.py refactor | 2-3h | Config builders, env var injection, OpenCode file-based prompt |
| Task 3: prompt.py update | 1-2h | Remove password table, add ats_login instructions |
| Task 4: Tests + verification | 2-3h | cred_server tests, launcher config tests, prompt content tests |
| **Total** | **8-12h** | Tasks 1-2 parallelizable |

---

## Historical Record

**Phase 1 (Completed 2026-08-26):** All 4 tasks implemented. Created `cred_server.py` (MCP credential server over stdio). Refactored `launcher.py` (cred-server MCP config, env var injection, OpenCode stdin piping). Updated `prompt.py` (removed passwords from prompt, added `ats_login` tool instructions, removed CapSolver API key). Created 42 new tests across 3 test files. All 182 tests pass, lint clean.

The previous plan (Site-Specific Passwords, completed 2026-08-26) added `site_passwords` dict to profiles — this plan builds on that by removing the passwords from the LLM prompt entirely.
