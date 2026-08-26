# Plan: Add OpenCode as an Alternative Agent Backend

> **Status: IMPLEMENTED** (2026-08-25)
> All code changes from this plan are complete. See CHANGELOG.md for details.
> 
> **Implementation Summary:**
> - ✅ OpenCode command builder (`_build_opencode_cmd`)
> - ✅ OpenCode MCP config generator (`_make_opencode_config`)
> - ✅ OpenCode output parser (`_parse_opencode_output`)
> - ✅ `--backend` CLI flag with validation
> - ✅ Backend dispatch in `run_job()`, `worker_loop()`, `main()`
> - ✅ Dual backend detection in `config.py` tier system
> - ✅ Dual backend detection in `doctor()` command
> - ✅ Dual backend detection in `init` wizard
> - ✅ Prompt docstring updated
> - ✅ README.md updated with both backends
> - ✅ CONTRIBUTING.md updated with "Apply Backends" section
> - ✅ `.gitignore` updated with `.opencode/`
> - ✅ CHANGELOG.md updated with all changes
> 
> **Note:** All pre-existing linting errors are unrelated to these changes.

## Goal

Add OpenCode CLI as an alternative browser agent backend for Tier 3 auto-apply, alongside
the existing Claude Code backend. Users choose which backend to use via a `--backend` CLI
flag. The default remains Claude Code to avoid breaking existing users.

**Key constraint: All existing Claude Code functionality must remain intact and unmodified
in its behavior. OpenCode is an additive alternative, not a replacement.**

## Feasibility: YES

The core architecture (subprocess invocation of an AI coding agent with MCP tools) is
the same for both backends — only the CLI interface, MCP config format, and output
format differ. A backend abstraction layer keeps the two cleanly separated.

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

## Architecture: Backend Abstraction

The plan introduces a thin backend abstraction so both Claude Code and OpenCode coexist:

```
cli.py (--backend claude|opencode)
  └─> launcher.py
        ├─ _build_claude_cmd()    # existing Claude Code command builder
        ├─ _build_opencode_cmd()  # new OpenCode command builder
        ├─ _make_mcp_config()     # existing (Claude format JSON)
        ├─ _make_opencode_config() # new (OpenCode format JSON)
        ├─ _parse_claude_output() # existing output parser
        └─ _parse_opencode_output() # new output parser
```

Each backend has its own:
- Command builder function
- MCP config generator
- Output parser
- Tool name prefix mapping

The `run_job()` function dispatches to the correct backend based on the `backend` parameter.

---

## Changes Required

### 1. `src/applypilot/apply/launcher.py` — Add OpenCode Backend

**New CLI flag plumbing:**
- Add `backend: str = "claude"` parameter to `run_job()`, `worker_loop()`, and `main()`
- Default is `"claude"` to preserve existing behavior

**New: `_build_opencode_cmd()` function:**
- Builds the OpenCode CLI command:
  ```python
  ["opencode", "run", "--model", model, "--auto", "--format", "json",
   "--dir", worker_dir, prompt]
  ```
- Handles prompt as positional argument (not stdin)

**New: `_make_opencode_config()` function:**
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

**New: `_parse_opencode_output()` function:**
- Parses OpenCode's `--format json` event stream
- Handles OpenCode's tool naming: `playwright_browser_navigate` (not `mcp__playwright__browser_navigate`)
- Returns same structured result as Claude parser for downstream compatibility

**Modified: `run_job()`:**
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

**Environment handling (line 346-348):**
- Claude backend: keep `env.pop("CLAUDECODE", None)` / `env.pop("CLAUDE_CODE_ENTRYPOINT", None)`
- OpenCode backend: no env cleanup needed

**Process tracking (line 52-54):**
- Keep `_claude_procs` name (it tracks subprocess objects regardless of backend)

### 2. `src/applypilot/cli.py` — Add `--backend` Flag

**New `--backend` option on `apply` command (line 145-156):**
```python
backend: str = typer.Option(
    "claude", "--backend", "-b",
    help="Agent backend: 'claude' (Claude Code CLI) or 'opencode' (OpenCode CLI)."
)
```

**Update `apply` command (line 146-256):**
- Pass `backend` through to `apply_main()`
- Validate backend value (`claude` or `opencode`)
- If `backend == "opencode"`: check `shutil.which("opencode")` instead of `shutil.which("claude")`
- If `backend == "claude"`: existing behavior unchanged

**Update `doctor()` (lines 399-405):**
- Check for BOTH `claude` and `opencode` on PATH
- Report both:
  ```
  Claude Code CLI    OK    /usr/local/bin/claude
  OpenCode CLI       OK    /usr/local/bin/opencode
  ```
- Tier 3 is satisfied if EITHER is available

**Update `gen` debug hint (line 226-230):**
- Show command for the selected backend:
  - Claude: `claude --model ... -p --mcp-config ... < prompt_file`
  - OpenCode: `opencode run --model ... --auto --dir ... "$(cat prompt_file)"`

### 3. `src/applypilot/config.py` — Dual Backend Tier Detection

**Update `get_tier()` (lines 200-223):**
- Tier 3 requires an LLM API key + Chrome + **at least one** agent CLI
- Change:
  ```python
  has_claude = shutil.which("claude") is not None
  has_opencode = shutil.which("opencode") is not None
  has_agent = has_claude or has_opencode
  ```
- Tier 3 condition: `has_agent and has_chrome`

**Update `check_tier()` (lines 226-260):**
- When reporting missing Tier 3 deps, list both CLIs and say "install one of:"
  ```
  Agent CLI (one required) — install Claude Code from https://claude.ai/code
                           — or install OpenCode from https://opencode.ai
  ```

**No changes to `has_claude` usage in other modules** — they only check for Tier gating,
which now accepts either backend.

### 4. `src/applypilot/apply/prompt.py` — Tool Name Abstraction

The prompt references MCP tools by their **user-facing names** (e.g., `browser_navigate`,
`browser_snapshot`, `browser_fill_form`). These names are backend-agnostic because:

- Claude Code: tools are `mcp__playwright__browser_navigate` → LLM sees them as `browser_navigate`
- OpenCode: tools are `playwright_browser_navigate` → LLM sees them as `browser_navigate`

The prompt body does NOT use the full prefixed tool names. The prompt uses bare names
like `browser_navigate`, `browser_snapshot`, etc. This should work with both backends
as-is.

**Only change needed:**
- Update module docstring (line 3): "Claude Code / the AI agent" → "the AI agent (Claude Code or OpenCode)"
- No tool name changes in the prompt body

### 5. `src/applypilot/wizard/init.py` — Dual Detection

**Update `_setup_auto_apply()` (lines 282-301):**
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

**Update tier unlock hint (line 383):**
- "To unlock Tier 3: install Claude Code or OpenCode + Chrome."

### 6. `README.md` — Documentation Updates

- Line 48: "Claude Code CLI" → "Claude Code CLI or OpenCode CLI"
- Line 68: Pipeline table — mention both backends
- Line 95: Dependency table — add OpenCode row alongside Claude Code
- Lines 147, 152: Feature descriptions — mention both backends
- Add a section explaining `--backend` flag and how to choose

### 7. `.gitignore`

- Add `.opencode/` alongside existing `.claude/` entry (line 40-41)

---

## Risks & Unknowns

1. **Prompt size as CLI argument (OpenCode only)**
   Claude Code accepts prompts via stdin (unlimited). OpenCode's `run` command
   takes prompts as positional arguments. OS argument limits (~200KB on Linux,
   ~256KB on macOS) should handle typical prompts (10-50KB), but very large
   prompts with full resume text could be an issue.
   **Mitigation**: Use `--file` flag to attach prompt as a file, or verify
   if OpenCode supports stdin piping. Claude Code path is unaffected.

2. **Output format differences**
   Need to inspect actual `opencode run --format json` output to understand
   the exact event schema. The OpenCode parser will need to be built from scratch.
   Claude Code parser is unchanged.
   **Action**: Run a test `opencode run --format json "hello"` to capture output format.

3. **MCP tool naming**
   Need to verify that OpenCode names Playwright tools as
   `playwright_browser_navigate` (not `mcp__playwright__browser_navigate`).
   The output parser depends on this naming for action logging.
   **Action**: Configure a test MCP server and verify tool name format.

4. **Model availability**
   OpenCode supports 75+ providers, but the user needs to configure auth.
   Gemini free tier works well for the "free" goal.
   Claude Code users continue using Anthropic models as before.
   **Action**: Set up `opencode auth login` with Gemini or another free provider.

5. **No `--no-session-persistence` equivalent (OpenCode only)**
   In non-interactive `run` mode, sessions may accumulate in OpenCode's DB.
   Not a functional issue but could consume disk over time.
   **Mitigation**: Periodic `opencode session delete` cleanup, or ignore.
   Claude Code path is unaffected.

6. **Parallel worker stagger (OpenCode only)**
   OpenCode may need stagger delays (5-10s) between parallel worker starts
   to avoid race conditions. Claude Code doesn't have this issue.
   **Mitigation**: Add stagger logic in `worker_loop` only for OpenCode backend.
   Claude Code path is unaffected.

---

## Implementation Order

1. **Verify OpenCode setup** — Install OpenCode, configure a free provider (Gemini),
   test `opencode run --format json "hello"` to capture output format
2. **Verify MCP tool naming** — Configure Playwright MCP in OpenCode, verify tool names
3. **Implement `_make_opencode_config()`** — New helper to generate per-worker config
4. **Implement `_build_opencode_cmd()`** — New command builder function
5. **Implement `_parse_opencode_output()`** — New output parser
6. **Add `--backend` flag to `cli.py`** — Plumbing through to launcher
7. **Update `run_job()` / `worker_loop()` / `main()`** — Backend dispatch
8. **Update `config.py`** — Dual backend tier detection
9. **Update `doctor()`** — Check both CLIs
10. **Update `wizard/init.py`** — Detect both CLIs
11. **Update `prompt.py` docstring** — Minor text change
12. **Update `README.md` and `.gitignore`** — Documentation
13. **Test both backends end-to-end** — Claude Code regression test + OpenCode test

---

## Estimated Effort

| Component | Effort |
|---|---|
| OpenCode setup & verification | 30 min |
| OpenCode config generator (`_make_opencode_config`) | 1 hour |
| OpenCode command builder (`_build_opencode_cmd`) | 30 min |
| OpenCode output parser (`_parse_opencode_output`) | 1-2 hours |
| `launcher.py` backend dispatch (`run_job`, etc.) | 1 hour |
| `cli.py` `--backend` flag + plumbing | 30 min |
| `config.py` dual tier detection | 20 min |
| `doctor()` dual CLI checks | 15 min |
| `wizard/init.py` dual detection | 15 min |
| `prompt.py` + `README.md` + `.gitignore` | 20 min |
| Testing & debugging | 1-2 hours |
| **Total** | **~7-9 hours** |
