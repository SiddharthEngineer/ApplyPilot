# Plan: Add OpenCode as an Alternative Agent Backend

**Started:** 2026-08-25
**Status:** ✅ Complete

---

## Goal

Add OpenCode CLI as an alternative browser agent backend for Tier 3 auto-apply, alongside
the existing Claude Code backend. Users choose which backend to use via a `--backend` CLI
flag. The default remains Claude Code to avoid breaking existing users.

**Key constraint: All existing Claude Code functionality must remain intact and unmodified
in its behavior. OpenCode is an additive alternative, not a replacement.**

## Success Criteria

1. `--backend claude` (default) works identically to pre-plan behavior — zero regressions.
2. `--backend opencode` invokes OpenCode CLI with correct command, MCP config, and output parsing.
3. `_build_opencode_cmd()` builds the correct OpenCode CLI command with model, auto, format, dir, and prompt.
4. `_make_opencode_config()` generates per-worker `opencode.json` with Playwright and Gmail MCP servers.
5. `_parse_opencode_output()` parses OpenCode's JSON event stream and returns structured results compatible with the Claude parser.
6. `get_tier()` accepts either Claude Code or OpenCode CLI for Tier 3 (dual backend detection).
7. `doctor()` reports both CLIs and shows Tier 3 satisfied if either is available.
8. `init` wizard detects both CLIs and provides appropriate guidance.
9. `prompt.py` docstring references both backends.
10. `README.md` and `CONTRIBUTING.md` document both backends.
11. All existing tests pass; no new linting errors introduced.

---

## Key Differences: Claude Code vs OpenCode

| Aspect | Claude Code | OpenCode |
|---|---|---|
| Headless command | `claude -p` (stdin) | `opencode run` (positional arg) |
| Prompt input | stdin pipe | Command-line argument |
| MCP config | `--mcp-config path` (per-run) | `opencode.json` config file (per-project) |
| Permissions | `--permission-mode bypassPermissions` | `--auto` flag |
| Model format | Short names (`haiku`) | `provider/model` (`anthropic/claude-haiku-4-5`) |
| Tool blocking | `--disallowedTools` (comma-separated) | Config-based (`permission` rules) |
| Output format | `--output-format stream-json` (line-delimited JSON) | `--format json` (raw JSON events) |
| MCP tool naming | `mcp__{server}__{tool}` | `{server}_{tool}` |
| Cost | $$$ (Anthropic API) | Free (own models/keys) |

---

## Task Chain

### Task 1: Add OpenCode Backend to `launcher.py`

**Files:** `src/applypilot/apply/launcher.py` (modify)

**What:** Add all OpenCode backend functions and backend dispatch logic.

**Changes:**

1. **Add `backend: str = "claude"` parameter** to `run_job()`, `worker_loop()`, and `main()`. Default is `"claude"` to preserve existing behavior.

2. **Add `_build_opencode_cmd()` function:**
   - Builds the OpenCode CLI command:
     ```python
     ["opencode", "run", "--model", model, "--auto", "--format", "json",
      "--dir", worker_dir, prompt]
     ```
   - Handles prompt as positional argument (not stdin)

3. **Add `_make_opencode_config()` function:**
   - Generates per-worker `opencode.json` into the worker directory
   - Configures Playwright MCP server with correct CDP port
   - Configures Gmail MCP server
   - Sets permission rules to block Gmail tools:
     ```json
     {
       "permission": {
         "playwright_*": "allow",
         "gmail_*": "deny"
       }
     }
     ```
   - OpenCode merges project-level config with global, so this works per-worker

4. **Add `_parse_opencode_output()` function:**
   - Parses OpenCode's `--format json` event stream
   - Handles OpenCode's tool naming: `playwright_browser_navigate` (not `mcp__playwright__browser_navigate`)
   - Returns same structured result as Claude parser for downstream compatibility

5. **Update `run_job()`:**
   - Dispatch to correct backend:
     ```python
     if backend == "opencode":
         cmd = _build_opencode_cmd(...)
         _make_opencode_config(worker_dir, port)
     else:
         cmd = _build_claude_cmd(...)
         _make_mcp_config(port)
     ```
   - Dispatch output parsing based on backend
   - Keep all existing Claude Code paths intact (rename current inline code to `_build_claude_cmd()`)

6. **Environment handling:**
   - Claude backend: keep `env.pop("CLAUDECODE", None)` / `env.pop("CLAUDE_CODE_ENTRYPOINT", None)`
   - OpenCode backend: no env cleanup needed

7. **Process tracking:**
   - Keep `_claude_procs` name (it tracks subprocess objects regardless of backend)

**Acceptance criteria:**
- `_build_opencode_cmd(...)` returns the correct command list with model, auto, format, dir, and prompt arguments.
- `_make_opencode_config(worker_dir, port)` creates `opencode.json` in the worker directory with Playwright and Gmail MCP servers and permission rules.
- `_parse_opencode_output(output, backend="opencode")` returns a structured dict compatible with the Claude parser output.
- `run_job(backend="opencode")` dispatches to OpenCode functions; `run_job(backend="claude")` uses existing Claude paths unchanged.
- Existing Claude Code behavior is completely unaffected.

**Status:** ✅ Complete

---

### Task 2: Add `--backend` Flag to `cli.py`

**Files:** `src/applypilot/cli.py` (modify)

**What:** Add CLI plumbing for the `--backend` flag and update `doctor()` and `gen` debug hint.

**Changes:**

1. **Add `--backend` option on `apply` command:**
   ```python
   backend: str = typer.Option(
       "claude", "--backend", "-b",
       help="Agent backend: 'claude' (Claude Code CLI) or 'opencode' (OpenCode CLI)."
   )
   ```

2. **Update `apply` command:**
   - Pass `backend` through to `apply_main()`
   - Validate backend value (`claude` or `opencode`)
   - If `backend == "opencode"`: check `shutil.which("opencode")` instead of `shutil.which("claude")`
   - If `backend == "claude"`: existing behavior unchanged

3. **Update `doctor()`:**
   - Check for BOTH `claude` and `opencode` on PATH
   - Report both:
     ```
     Claude Code CLI    OK    /usr/local/bin/claude
     OpenCode CLI       OK    /usr/local/bin/opencode
     ```
   - Tier 3 is satisfied if EITHER is available

4. **Update `gen` debug hint:**
   - Show command for the selected backend:
     - Claude: `claude --model ... -p --mcp-config ... < prompt_file`
     - OpenCode: `opencode run --model ... --auto --dir ... "$(cat prompt_file)"`

**Acceptance criteria:**
- `apply --backend claude` works identically to `apply` without the flag.
- `apply --backend opencode` invokes OpenCode backend when available.
- `apply --backend invalid` shows a validation error.
- `doctor()` shows both CLIs and Tier 3 satisfied if either is present.
- `gen` debug hint shows the correct command for the selected backend.

**Status:** ✅ Complete

---

### Task 3: Dual Backend Tier Detection in `config.py`

**Files:** `src/applypilot/config.py` (modify)

**What:** Update tier detection to accept either Claude Code or OpenCode for Tier 3.

**Changes:**

1. **Update `get_tier()`:**
   - Tier 3 requires an LLM API key + Chrome + **at least one** agent CLI
   - Change:
     ```python
     has_claude = shutil.which("claude") is not None
     has_opencode = shutil.which("opencode") is not None
     has_agent = has_claude or has_opencode
     ```
   - Tier 3 condition: `has_agent and has_chrome`

2. **Update `check_tier()`:**
   - When reporting missing Tier 3 deps, list both CLIs and say "install one of:"
     ```
     Agent CLI (one required) — install Claude Code from https://claude.ai/code
                              — or install OpenCode from https://opencode.ai
     ```

**Acceptance criteria:**
- Tier 3 is satisfied when either Claude Code or OpenCode is installed (with LLM key + Chrome).
- `check_tier()` reports both CLIs as options when neither is installed.
- Existing Claude Code-only setups still reach Tier 3.

**Status:** ✅ Complete

---

### Task 4: Update `prompt.py` Docstring

**Files:** `src/applypilot/apply/prompt.py` (modify)

**What:** Update module docstring to reference both backends.

**Change:**
- Line 3: "Claude Code / the AI agent" → "the AI agent (Claude Code or OpenCode)"
- No tool name changes in the prompt body — both backends expose the same bare tool names (`browser_navigate`, `browser_snapshot`, etc.) to the LLM.

**Acceptance criteria:**
- Module docstring mentions both Claude Code and OpenCode.
- Prompt body is unchanged (backend-agnostic tool names).

**Status:** ✅ Complete

---

### Task 5: Update `wizard/init.py` — Dual Detection

**Files:** `src/applypilot/wizard/init.py` (modify)

**What:** Update init wizard to detect both CLIs and provide appropriate guidance.

**Changes:**

1. **Update `_setup_auto_apply()`:**
   - Detect BOTH CLIs:
     ```python
     has_claude = shutil.which("claude") is not None
     has_opencode = shutil.which("opencode") is not None
     ```
   - Report which are found:
     - Both found: "Both Claude Code and OpenCode detected. You can choose with --backend."
     - Only Claude: "Claude Code detected. OpenCode also available as alternative."
     - Only OpenCode: "OpenCode detected."
     - Neither found: "Install Claude Code (https://claude.ai/code) or OpenCode (https://opencode.ai)"

2. **Update tier unlock hint:**
   - "To unlock Tier 3: install Claude Code or OpenCode + Chrome."

**Acceptance criteria:**
- `init` detects both CLIs and reports correctly for all 4 combinations (both, only Claude, only OpenCode, neither).
- Tier unlock hint mentions both backends.

**Status:** ✅ Complete

---

### Task 6: Documentation Updates

**Files:** `README.md`, `CONTRIBUTING.md`, `.gitignore`

**What:** Update documentation to reflect both backends.

**Changes:**

1. **README.md:**
   - "Claude Code CLI" → "Claude Code CLI or OpenCode CLI"
   - Pipeline table — mention both backends
   - Dependency table — add OpenCode row alongside Claude Code
   - Feature descriptions — mention both backends
   - Add section explaining `--backend` flag and how to choose

2. **CONTRIBUTING.md:**
   - Add "Apply Backends" section documenting both backends

3. **`.gitignore`:**
   - Add `.opencode/` alongside existing `.claude/` entry

**Acceptance criteria:**
- README documents both backends and the `--backend` flag.
- CONTRIBUTING has an "Apply Backends" section.
- `.gitignore` includes `.opencode/`.

**Status:** ✅ Complete

---

## Implementation Order

```
Task 1 (launcher.py backend) → Task 2 (cli.py flag) → Task 3 (config.py tier detection)
                                                          ↓
                                         Task 4 (prompt.py) + Task 5 (wizard) + Task 6 (docs)
```

1. Task 1 — core backend functions in launcher.py (foundation for everything else).
2. Task 2 — CLI flag and plumbing (depends on Task 1 functions existing).
3. Task 3 — tier detection (independent of Tasks 1-2, but logically follows).
4. Tasks 4-6 — docstring, wizard, and docs (independent, can be done in parallel).

## Key Design Decisions

1. **Backend abstraction, not inheritance** — Each backend has its own command builder, config generator, and output parser. No shared base class; the functions are simple enough that duplication is clearer than abstraction.
2. **Default to Claude Code** — The `--backend` default is `"claude"` so existing users see zero change.
3. **Per-worker config files** — OpenCode's `opencode.json` is written into each worker directory, leveraging OpenCode's project-level config merge behavior.
4. **Same output interface** — Both parsers return the same structured dict so downstream code (`run_job`, result handling) is backend-agnostic.
5. **Additive, not replacement** — All existing Claude Code paths are preserved verbatim. OpenCode is a new branch, not a refactor.

---

## Historical Record

**Phase 1 (Completed 2026-08-25):** All 6 tasks implemented. Added OpenCode backend functions (`_build_opencode_cmd`, `_make_opencode_config`, `_parse_opencode_output`) to `launcher.py`. Added `--backend` CLI flag with validation to `cli.py`. Updated `config.py` for dual backend tier detection. Updated `doctor()` to report both CLIs. Updated `wizard/init.py` for dual detection. Updated `prompt.py` docstring. Updated `README.md`, `CONTRIBUTING.md`, and `.gitignore`. All pre-existing tests pass; no new linting errors.
