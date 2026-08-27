# Contributing to ApplyPilot

Thank you for your interest in contributing to ApplyPilot. This guide covers everything you need to get started.

## Development Setup

### Prerequisites

- Python 3.11 or higher
- Git

### Clone and Install

```bash
git clone https://github.com/Pickle-Pixel/ApplyPilot.git
cd ApplyPilot
uv pip install -e ".[dev]"
playwright install chromium
```

This installs ApplyPilot in editable mode with all development dependencies (pytest, ruff, etc.) and downloads the Chromium browser binary for Playwright.

### Verify Installation

```bash
applypilot --version
pytest tests/ -v
ruff check src/
```

## How to Contribute

### Adding New Workday Employers

Workday employer portals are configured in `config/employers.yaml`. To add a new employer:

1. Find the company's Workday career portal URL (usually `https://company.wd5.myworkdaysite.com/`)
2. Identify the Workday instance number (wd1, wd3, wd5, etc.) and the tenant ID
3. Add an entry to `config/employers.yaml`:

```yaml
- name: "Company Name"
  tenant: "company_tenant_id"
  instance: "wd5"
  url: "https://company.wd5.myworkdaysite.com/en-US/recruiting"
```

4. Test discovery: `applypilot discover --employer "Company Name"`
5. Submit a PR with the new entry

### Adding New Career Sites

Direct career site scrapers are configured in `config/sites.yaml`. To add a new site:

1. Inspect the company's careers page and identify the job listing structure
2. Add an entry to `config/sites.yaml` with CSS selectors:

```yaml
- name: "Company Name"
  url: "https://company.com/careers"
  selectors:
    job_list: ".job-listing"
    title: ".job-title"
    location: ".job-location"
    link: "a.job-link"
    description: ".job-description"
```

3. Test: `applypilot discover --site "Company Name"`
4. Submit a PR

### Bug Fixes and Features

1. Check existing [issues](https://github.com/Pickle-Pixel/ApplyPilot/issues) to avoid duplicating work
2. For new features, open an issue first to discuss the approach
3. Fork the repo and create a feature branch from `main`
4. Write your code with type hints and docstrings
5. Add tests for new functionality
6. Update the CHANGELOG.md under an `[Unreleased]` section
7. Submit a PR

## Running Tests

```bash
# Run all tests
pytest tests/ -v

# Run a specific test file
pytest tests/test_scoring.py -v

# Run with coverage
pytest tests/ --cov=src/applypilot --cov-report=term-missing
```

## Linting and Code Style

ApplyPilot uses [Ruff](https://docs.astral.sh/ruff/) for linting and formatting.

```bash
# Check for issues
ruff check src/

# Auto-fix what can be fixed
ruff check src/ --fix

# Format code
ruff format src/
```

### Code Style Guidelines

- **Type hints**: All function signatures must have type annotations
- **Docstrings**: All public functions and classes must have docstrings (Google style)
- **Naming**: snake_case for functions and variables, PascalCase for classes
- **Imports**: Sorted by Ruff (isort-compatible)
- **Line length**: 100 characters maximum

## PR Guidelines

- **One feature per PR.** Keep changes focused and reviewable.
- **Include tests.** New features need test coverage. Bug fixes need a regression test.
- **Update CHANGELOG.md.** Add your changes under `[Unreleased]`.
- **Write a clear PR description.** Explain what changed and why.
- **Keep commits clean.** Squash fixup commits before requesting review.
- **CI must pass.** All linting and tests must be green.

## Project Structure

```
ApplyPilot/
├── src/applypilot/       # Main package
│   ├── __init__.py
│   ├── cli.py            # CLI entry points
│   ├── discover/         # Stage 1: job discovery scrapers
│   ├── enrich/           # Stage 2: description extraction
│   ├── score/            # Stage 3: AI scoring
│   ├── tailor/           # Stage 4: resume tailoring
│   ├── cover/            # Stage 5: cover letter generation
│   ├── apply/            # Stage 6: browser automation
│   └── utils/            # Shared utilities
├── agents/                 # Agent plans, state, and queue
│   ├── BUILD_PROMPT.md     # Prompt template for build agents
│   ├── STATE.md            # Current implementation state
│   ├── CHANGELOG.md        # Agent-maintained changelog
│   ├── plan_queue.json     # Plan queue for continuous implementation
│   └── plans/              # Individual plan files
├── scripts/                # Automation scripts
│   └── plan_worker.py      # Plan queue worker (continuous agent loop)
├── config/               # Default configuration files
├── tests/                # Test suite
├── docs/                 # Documentation
└── pyproject.toml        # Package configuration
```

## Apply Backends (Stage 6)

The auto-apply stage supports two agent backends via the `--backend` flag:

| Backend | CLI | Config format | Cost |
|---------|-----|---------------|------|
| `claude` (default) | `claude -p` | MCP config JSON passed via `--mcp-config` | Anthropic API |
| `opencode` | `opencode run` | `opencode.json` in worker directory | Free (own API keys) |

Each backend has its own command builder (`_build_claude_cmd` / `_build_opencode_cmd`), MCP config generator (`_make_mcp_config` / `_make_opencode_config`), and output parser (`_parse_claude_output` / `_parse_opencode_output`) in `apply/launcher.py`.

When adding new MCP tools or changing tool permissions, update **both** backend paths. The prompt in `apply/prompt.py` uses backend-agnostic tool names (e.g. `browser_navigate`) and works with both.

## Plan Queue Worker (Automated Implementation)

The plan queue worker continuously implements plans using agentic sessions. It reads plans from `agents/plan_queue.json`, launches opencode agents to implement them, and loops until the queue is empty.

### How It Works

1. Picks the top plan from the queue
2. Launches `opencode run --auto` with `agents/BUILD_PROMPT.md` + plan path
3. After the agent exits, checks completion via `agents/STATE.md` and the plan file's status field
4. If done: dequeues the plan, immediately starts the next one
5. If not done: next iteration continues (agent reads STATE.md to resume)
6. Retries up to 2x on failure, skips after 20 iterations per plan

### Usage

```bash
# Start the worker (runs continuously until queue is empty)
./scripts/plan_worker.py

# Background it
nohup ./scripts/plan_worker.py >> plan_worker.log 2>&1 &

# Add a plan to the queue
./scripts/plan_worker.py --enqueue agents/plans/my_new_plan.md

# Remove a plan from the queue
./scripts/plan_worker.py --dequeue agents/plans/my_new_plan.md

# Check queue status
./scripts/plan_worker.py --status

# Dry run (see what would happen without executing)
./scripts/plan_worker.py --dry-run
```

### Queue Format (`agents/plan_queue.json`)

```json
{
  "queue": ["agents/plans/captcha-solve-tool.md"],
  "completed": [],
  "model": "opencode/big-pickle",
  "max_iterations": 20,
  "iteration_counts": {},
  "retry_counts": {}
}
```

### Adding a New Plan

1. Create a plan file in `agents/plans/` following the template in `agents/PLAN_PROMPT.md`
2. Enqueue it: `./scripts/plan_worker.py --enqueue agents/plans/your_plan.md`
3. The worker will pick it up on the next iteration

### Completion Detection

A plan is considered done when either:
- `agents/STATE.md` contains "No remaining work" or "All tasks complete"
- The plan file's status field reads `✅ Completed`

The agent is responsible for updating these markers via the instructions in `agents/BUILD_PROMPT.md`.

## License

By contributing to ApplyPilot, you agree that your contributions will be licensed under the [GNU Affero General Public License v3.0](LICENSE).
