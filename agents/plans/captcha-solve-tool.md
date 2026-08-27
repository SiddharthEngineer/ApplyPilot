# Plan: CAPTCHA Solve via cred-server Tool

**Started:** 2026-08-26
**Status:** ✅ Complete

---

## Goal

The apply agent's prompt instructs the LLM to read the CapSolver API key and call the CapSolver REST API via `browser_evaluate` (JavaScript running in the *browser* page context). This is functionally broken: the key lives in the `cred-server` process env, and `browser_evaluate` cannot reach it, so CAPTCHA solving silently fails. We fix this by moving the key-handling and HTTP calls into a new `captcha_solve` MCP tool on the `cred` server (exactly mirroring the existing `ats_login` pattern). The LLM calls `cred.captcha_solve` with the detected captcha type/URL/sitekey, the cred-server reads `CAPSOLVER_API_KEY` from its own env and runs the createTask→poll→getTaskResult flow, returning the token. The LLM still injects the returned token via `browser_evaluate` (the only part that legitimately runs in the browser). This also keeps the secret out of the LLM's context (no security regression).

## Success Criteria

1. `cred_server.py` exposes a `captcha_solve` MCP tool that reads `CAPSOLVER_API_KEY` from the process env and never returns the key in its output.
2. Calling `captcha_solve` performs the full CapSolver createTask→poll→getTaskResult flow (via `httpx`, the project's existing HTTP dependency) and returns `{"success": true, "token": "<...>"}` or a structured error.
3. The prompt's `CAPTCHA SOLVE` section no longer instructs the LLM to read the key or call CapSolver via `browser_evaluate`; it instructs calling the `cred.captcha_solve` tool and then injecting the returned token.
4. `test_cred_server.py` has unit tests for `captcha_solve` (definition present, missing-key error, mocked successful solve, error from CapSolver).
5. `test_prompt.py` verifies the CAPTCHA section references `captcha_solve`, never contains a leaked real key, and no longer tells the LLM to read `CAPSOLVER_API_KEY` via `browser_evaluate`.
6. `pytest` passes for both modified test files.

## Task Chain

### Task 1: Add `captcha_solve` tool to cred_server.py

**Files:** `src/applypilot/apply/cred_server.py` (modify)

**What:** Add a `captcha_solve` tool definition to `TOOLS`, a `_get_capsolver_key()` env reader, an async `_solve_captcha(captcha_type, website_url, website_key, page_action=None, metadata=None) -> dict` function, and dispatch in `_handle_tool_call`. Map captcha types to CapSolver task types (`hcaptcha→HCaptchaTaskProxyLess`, `recaptchav2→ReCaptchaV2TaskProxyLess`, `recaptchav3→ReCaptchaV3TaskProxyLess`, `turnstile→AntiTurnstileTaskProxyLess`, `funcaptcha→FunCaptchaTaskProxyLess`); for `recaptchav3` include `pageAction`, for `turnstile` include `metadata`. Use `httpx.AsyncClient` (wrapped in try/except → `"httpx_not_installed"`), POST to `https://api.capsolver.com/createTask`, then poll `https://api.capsolver.com/getTaskResult` every 3s up to 10 times. Extract token per type (`gRecaptchaResponse` for recaptcha/hcaptcha, `token` for turnstile/funcaptcha). Return `{"success": bool, "token"|"message": ...}`. Never include `clientKey` in the JSON result text.

**Acceptance criteria:**
- `TOOLS` contains an entry named `captcha_solve` with an `inputSchema` requiring `captcha_type`, `website_url`, `website_key` and an enum listing the 5 captcha types.
- `_get_capsolver_key()` returns the env value when `CAPSOLVER_API_KEY` is set, `None` when unset/empty.
- `_handle_tool_call("captcha_solve", {...})` returns `success:false, message:"no_capsolver_key_configured"` when the key is absent.
- A mocked httpx call returning a valid task + `ready` result yields `success:true` and a non-empty `token`; the returned JSON text contains no `clientKey`.

**Status:** ✅ Complete

---

### Task 2: Unit tests for `captcha_solve`

**Files:** `tests/test_cred_server.py` (modify)

**What:** Add a `TestCaptchaSolve` class. Test (a) tool definition present and schema correct, (b) missing key returns `no_capsolver_key_configured`, (c) successful solve via `httpx.AsyncClient` mocked with `AsyncMock` returning a fake `createTask` (`errorId:0, taskId:"t1"`) then `getTaskResult` (`status:"ready", solution:{gRecaptchaResponse:"TOK"}`) yields `success:true, token:"TOK"`, (d) CapSolver `errorId>0` yields `success:false`, (e) result text never leaks the key. Mirror the existing `test_cdp_connection_failure` patching style.

**Acceptance criteria:**
- `pytest tests/test_cred_server.py -k captcha` runs and all tests pass.
- At least one test asserts the returned JSON text does not contain a fake key value.

**Status:** ✅ Complete

---

### Task 3: Rewrite CAPTCHA SOLVE prompt section

**Files:** `src/applypilot/apply/prompt.py` (modify)

**What:** In `_build_captcha_section()`, replace the `FIRST` (lines 300–302: read key via `browser_evaluate`) and `STEP 1`/`STEP 2` (createTask/poll via `browser_evaluate` fetch) with instructions to call the `captcha_solve` tool on the `cred` server. Keep `STEP 3` (token injection via `browser_evaluate`, lines 356–405) but relabel `THE_TOKEN` as "the token returned by `captcha_solve`". Preserve the TASK_TYPE mapping and the MANUAL FALLBACK section. The docstring already states the key is on the cred-server env — keep/adjust it to mention the tool.

**Acceptance criteria:**
- `_build_captcha_section()` no longer contains `browser_evaluate` calls that reference `api.capsolver.com/createTask` or `getTaskResult`.
- The section instructs calling `captcha_solve` and references the returned token for injection.
- `STEP 3` injection JS is unchanged in behavior.

**Status:** ✅ Complete

---

### Task 4: Update prompt tests for new solve flow

**Files:** `tests/test_prompt.py` (modify)

**What:** Update `TestCaptchaSection` to match the new design. Replace `test_captcha_section_mentions_env_var` (which asserts `"CAPSOLVER_API_KEY"` and `"env var"`) with assertions that the section contains `captcha_solve` and instructs the LLM to call it, while still asserting a real key does not appear. Add a test asserting the section no longer tells the LLM to read the key via `browser_evaluate` (e.g. `"api.capsolver.com/createTask"` not in section). Keep `test_no_capsolver_key_in_captcha_section`.

**Acceptance criteria:**
- `pytest tests/test_prompt.py` passes.
- A test fails if the section contains `api.capsolver.com/createTask` (proving the broken instruction is gone).
- A test confirms `captcha_solve` appears in the section.

**Status:** ✅ Complete

---

## Implementation Order

```
Task 1 (cred_server tool) → Task 2 (cred_server tests)
                                      ↓
Task 3 (prompt rewrite) → Task 4 (prompt tests)
```

1. Task 1 — implement the `captcha_solve` tool.
2. Task 2 — test the tool (depends on Task 1).
3. Task 3 — rewrite the prompt to call the tool (depends on Task 1 existing).
4. Task 4 — update prompt tests (depends on Task 3).

## Key Design Decisions

1. The CapSolver HTTP call lives in the cred-server (not the browser), mirroring `ats_login` — this is the only process that has `CAPSOLVER_API_KEY` in its env, fixing the bug and preserving secret isolation.
2. Use `httpx.AsyncClient` (already a hard project dependency, used in `llm.py`/`smartextract.py`) rather than `urllib`, keeping cred-server consistent with the rest of the codebase.
3. Token injection (STEP 3) correctly runs in the browser via `browser_evaluate` and is left intact; only the key-reading and CapSolver HTTP steps move server-side.
4. `captcha_solve` returns a structured `{"success", "token"|"message"}` JSON identical in shape to `ats_login`, so the LLM's MANUAL FALLBACK logic (errorId > 0) maps cleanly to `success:false`.

## Historical Record

1. **Task 1** — Added `captcha_solve` tool to `cred_server.py`: tool definition with input schema (captcha_type enum, website_url, website_key, page_action, metadata), `_get_capsolver_key()` env reader, `_solve_captcha()` async function with createTask→poll→getTaskResult flow via `_httpx.AsyncClient`, and dispatch in `_handle_tool_call`. Maps 5 captcha types to CapSolver task types. Returns `{"success": bool, "token"|"message": ...}`. Never returns the API key in output. Imported `httpx` as module-level `_httpx` with try/except ImportError for clean testing.

2. **Task 2** — Added 15 unit tests in `TestCaptchaSolve` class in `tests/test_cred_server.py`: tool definition present, schema correct, enum values, missing key error, unsupported type error, mocked successful hcaptcha solve, mocked successful turnstile solve, CapSolver error returns failure, result never leaks key, `_get_capsolver_key` reads/returns none/empty, CAPTCHA_TYPE_MAP values, dispatch test, unsupported type dispatch. Updated `test_tools_list_returns_tools` to expect 2 tools.

3. **Task 3** — Rewrote `_build_captcha_section()` in `prompt.py`: removed FIRST/STEP 1/STEP 2 (browser_evaluate createTask/poll flow with `api.capsolver.com`), replaced with instructions to call `captcha_solve` tool on the cred server. Kept STEP 3 token injection JS (browser_evaluate). Updated MANUAL FALLBACK to reference `captcha_solve` returns. No more `CAPSOLVER_API_KEY` or `api.capsolver.com` in prompt text.

4. **Task 4** — Updated `TestCaptchaSection` in `tests/test_prompt.py`: replaced `test_captcha_section_mentions_env_var` with 5 new tests verifying `captcha_solve` appears, no `api.capsolver.com` in section, no `CAPSOLVER_API_KEY` in section, token injection still present, manual fallback references tool. All 15 prompt tests pass.
