# Current State

**Last updated:** 2026-08-25

## No Active Implementation Task

The "Add OpenCode as an Alternative Agent Backend" plan has been fully implemented and verified. There is currently no active plan or in-progress work.

## Project Overview

ApplyPilot v0.3.0 is a 6-stage autonomous job application pipeline. Stage 6 (auto-apply) now supports two agent backends:

| Backend | Default | Cost | CLI |
|---------|---------|------|-----|
| `claude` | Yes | Anthropic API | `claude -p` |
| `opencode` | No | Free (own API keys) | `opencode run` |

Users choose via `applypilot apply --backend <claude|opencode>`.

## Architecture: Backend Abstraction (Stage 6)

```
cli.py (--backend claude|opencode)
  └─> launcher.py
        ├─ _build_claude_cmd()      # Claude Code command builder
        ├─ _build_opencode_cmd()    # OpenCode command builder
        ├─ _make_mcp_config()       # Claude MCP config (JSON via --mcp-config)
        ├─ _make_opencode_config()  # OpenCode MCP config (opencode.json per-worker)
        ├─ _parse_claude_output()   # Claude stream-json parser
        └─ _parse_opencode_output() # OpenCode format-json parser
```

Each backend has its own command builder, MCP config generator, output parser, and tool name prefix mapping. The `run_job()` function dispatches based on the `backend` parameter.

## Key Files

| File | Role |
|------|------|
| `src/applypilot/apply/launcher.py` | Backend dispatch, command building, MCP config, output parsing |
| `src/applypilot/cli.py` | CLI entry points, `--backend` flag, `doctor()` |
| `src/applypilot/config.py` | Tier detection (Tier 3 accepts either backend) |
| `src/applypilot/apply/prompt.py` | Agent prompt (backend-agnostic tool names) |
| `src/applypilot/wizard/init.py` | Setup wizard (detects both CLIs) |

## Known Limitations

1. **Prompt size (OpenCode)** — OpenCode takes prompts as CLI positional arguments. OS arg limits (~200KB Linux, ~256KB macOS) may constrain very large prompts with full resume text.
2. **Session accumulation (OpenCode)** — Non-interactive `run` mode may accumulate sessions in OpenCode's DB over time. Not a functional issue.
3. **Parallel worker stagger (OpenCode)** — OpenCode may need stagger delays between parallel worker starts to avoid race conditions. Not yet implemented.
4. **Pre-existing linting errors** — Unrelated to the OpenCode feature; present before and after implementation.

## Testing

- Run `pytest tests/ -v` for unit tests
- Run `ruff check src/` for linting
- No end-to-end tests for the OpenCode backend yet (requires running OpenCode CLI)
