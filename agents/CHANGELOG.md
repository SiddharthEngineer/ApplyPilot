# Changelog

All notable changes to ApplyPilot will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Completed
- **Init Wizard Pre-fill Defaults** (2026-08-26) — `applypilot init` now pre-fills every prompt with previously saved values from `profile.json`, `searches.yaml`, and `.env` on re-run. Added helpers `_load_existing_profile()`, `_load_existing_env()`, `_str_to_bool()`, `_join_list()`. Modified `_setup_profile()`, `_setup_site_passwords()`, `_setup_searches()`, `_setup_ai_features()` to accept existing config dicts and pass saved values as `default=` to each prompt. `run_wizard()` loads all existing configs at startup and shows "ApplyPilot Reconfigure" banner. 23 new tests added (38 total in `test_init_wizard.py`). All 117 tests pass, lint clean.
- **Site-Specific Passwords** (2026-08-26) — Replaced single `personal.password` field with `site_passwords` dict mapping ATS platform names (workday, greenhouse, lever, ashby) to credentials. Added `SITE_PASSWORDS` registry in `config.py`. Added backward-compat migration in `load_profile()`. Updated wizard to prompt per-ATS with descriptive labels. Updated prompt builder with site-specific password lookup table. 6 new tests added. All 117 tests pass.
- **OpenCode backend plan fully implemented** (2026-08-25) — All items from the "Add OpenCode as an Alternative Agent Backend" plan are complete and verified. The plan is preserved in git history; see `agent/PLAN.md` for the cleared version.
- **Content Library Parser (Task 1)** (2026-08-26) — Created `src/applypilot/scoring/content_library.py` with `Project`, `RoleSection`, `ContentLibrary` dataclasses and `parse_content_library()` function. Parses all 19 projects from `personal/content_library.md` with correct angle tag extraction. 26 unit tests pass.
- **Content Library Tailoring Prompt (Task 2)** (2026-08-26) — Added `_build_content_library_tailor_prompt()` to `src/applypilot/scoring/tailor.py`. Prompt formats all content library projects grouped by role, includes angle tags for selection, skills boundary, banned words, and a 5-step project selection process. 16 unit tests pass.
- **Content Library Tailor Function (Task 3)** (2026-08-26) — Added `tailor_from_content_library()` and `judge_content_library_resume()` to `src/applypilot/scoring/tailor.py`. Mirrors `tailor_resume()` structure (retry loop, validation, judge) but uses content library as input. Judge uses content-library-aware prompt that understands projects were selected from a library. 19 unit tests pass.
- **Validation Updates (Task 4)** (2026-08-26) — Added `source` parameter to `validate_json_fields()` and `validate_tailored_resume()` in `src/applypilot/scoring/validator.py`. When `source="content-library"`, preserved-companies and preserved-projects checks are relaxed (the LLM may legitimately drop irrelevant roles/companies). Fabrication detection, banned words, required sections, and LLM self-talk checks remain fully enforced. 14 unit tests pass.
- **CLI & Pipeline Integration (Task 5)** (2026-08-26) — Added `--source` flag to `applypilot run` CLI (choices: `resume`, `content-library`, default `resume`). Added `CONTENT_LIBRARY_PATH` to `config.py`. Updated `run_tailoring()` to accept `source` parameter and dispatch to `tailor_resume()` or `tailor_from_content_library()`. Plumbed `source` through `pipeline.py` (sequential, streaming, and stage runner paths). CLI validates source flag and checks content library file exists before running. 75 tests pass, lint clean.
- **PDF Rendering Update (Task 6)** (2026-08-26) — Updated `src/applypilot/scoring/pdf.py` with one-page overflow detection: `render_pdf()` now measures content height via Playwright and returns overflow info; `convert_to_pdf()` returns a dict with path and overflow details. Added role-group detection in `build_html()` — experience entries with role keywords (associate, engineer, intern, lead, etc.) get a distinct `role-entry` CSS class. Overflow warnings logged and `page_overflow` flag saved in the report JSON. Moved report save after PDF generation so overflow info is included. Updated `run_tailoring()` to capture overflow in result dict. 14 new tests pass (89 total).
- **Batch Entry & End-to-End Test (Task 7)** (2026-08-26) — Verified batch entry point (`run_tailoring(source='content-library')`) works end-to-end. Created 7 integration tests in `tests/test_content_library_e2e.py` covering: successful job processing, file output verification (txt + report JSON), no-jobs edge case, missing content library error, resume source isolation, multi-job batch, and DB update flow. Tests mock external dependencies (DB, LLM, parser) while exercising real `run_tailoring()` dispatch logic. 96 tests pass total. Content Library Resume Tailoring plan is fully implemented.
- **README updated** (2026-08-26) — Documented content-library tailoring mode in the Tailor section and added `--source` flag to CLI reference.
- **Init Wizard Content Library Support (Task 8)** (2026-08-26) — Updated `src/applypilot/wizard/init.py` to present workflow choice (traditional resume vs content library) during `applypilot init`. Added `_setup_content_library()` and `_setup_pdf_reference()` functions. Content library mode copies file to `~/.applypilot/content_library.md` and skips resume prompts. Optional PDF formatting reference support added. Added `RESUME_REFERENCE_PATH` to `config.py`. Created 9 unit tests in `tests/test_init_wizard.py`. All 105 tests pass.
- **Doctor Command Content Library Check (Task 9)** (2026-08-26) — Added content library validation to `applypilot doctor` in `src/applypilot/cli.py`. Three checks: file existence (OK/WARN), parse validation with role/project/angle counts or ERROR on failure, and tier summary hint. Created 6 unit tests in `tests/test_doctor_content_library.py`. All 111 tests pass.
- **Init Wizard Tests (Task 10)** (2026-08-26) — Verified init wizard correctly handles both traditional and content library workflows. Created 9 unit tests in `tests/test_init_wizard.py` covering: traditional mode preserved, content library mode setup, file validation, optional PDF reference, and content library skipping resume prompts. All existing tests unchanged.

## [0.3.0] - 2026-08-25

### Added
- **OpenCode as alternative agent backend** — `applypilot apply --backend opencode` uses
  OpenCode CLI instead of Claude Code for autonomous browser-based job applications.
  OpenCode is free (bring your own models/API keys) vs Claude Code (Anthropic API).
- **`--backend` CLI flag** — choose between `claude` (default) and `opencode` backends
  on `applypilot apply`. Passes through to launcher, worker loop, and job execution.
- **OpenCode command builder** (`_build_opencode_cmd`) — constructs the `opencode run`
  command with `--model`, `--auto`, `--format json`, and `--dir` flags. Prompt is passed
  as a positional argument (not stdin).
- **OpenCode MCP config generator** (`_make_opencode_config`) — generates per-worker
  `opencode.json` with Playwright and Gmail MCP servers, plus permission rules to
  block Gmail tools and allow Playwright tools.
- **OpenCode output parser** (`_parse_opencode_output`) — parses OpenCode's `--format json`
  event stream (assistant messages, tool usage, usage stats) into the same structured
  format as the Claude parser for downstream compatibility.
- **Dual backend detection in `doctor()`** — checks for both Claude Code and OpenCode
  CLI on PATH, reports availability of each, and shows combined Tier 3 status.
- **Dual backend detection in `init` wizard** — detects both CLIs during auto-apply
  setup, reports which are found, and suggests the alternative when only one is present.

### Changed
- **Tier 3 now accepts either backend** — `get_tier()` returns Tier 3 when Chrome +
  LLM key + at least one agent CLI (Claude Code or OpenCode) is available.
- **`check_tier()` error messages** — when Tier 3 is missing, lists both CLIs and
  says "install one of" with links to both.
- **`run_job()` refactored** — command building extracted into `_build_claude_cmd()`
  and `_build_opencode_cmd()`. Backend dispatch selects the correct builder, config
  generator, and output parser based on the `backend` parameter.
- **`worker_loop()` and `main()` accept `backend` parameter** — plumbed through from
  `cli.py` `--backend` flag to job execution.
- **Log filenames include backend name** — job logs are now prefixed with `{backend}_`
  instead of hardcoded `claude_`.
- **Documentation updated** — README, CONTRIBUTING (added "Apply Backends" section), PLAN reflect both backends.
- **`.gitignore`** — added `.opencode/` alongside `.claude/`.

## [0.2.0] - 2026-02-17

### Added
- **Parallel workers for discovery/enrichment** - `applypilot run --workers N` enables
  ThreadPoolExecutor-based parallelism for Workday scraping, smart extract, and detail
  enrichment. Default is sequential (1); power users can scale up.
- **Apply utility modes** - `--gen` (generate prompt for manual debugging), `--mark-applied`,
  `--mark-failed`, `--reset-failed` flags on `applypilot apply`
- **Dry-run mode** - `applypilot apply --dry-run` fills forms without clicking Submit
- **5 new tracking columns** - `agent_id`, `last_attempted_at`, `apply_duration_ms`,
  `apply_task_id`, `verification_confidence` for better apply-stage observability
- **Manual ATS detection** - `manual_ats` list in `config/sites.yaml` skips sites with
  unsolvable CAPTCHAs (e.g. TCS iBegin)
- **Qwen3 `/no_think` optimization** - automatically saves tokens when using Qwen models
- **`config.DEFAULTS`** - centralized dict for magic numbers (`min_score`, `max_apply_attempts`,
  `poll_interval`, `apply_timeout`, `viewport`)

### Fixed
- **Config YAML not found after install** - moved `config/` into the package at
  `src/applypilot/config/` so YAML files (employers, sites, searches) ship with `pip install`
- **Search config format mismatch** - wizard wrote `searches:` key but discovery code
  expected `queries:` with tier support. Aligned wizard output and example config
- **JobSpy install isolation** - removed python-jobspy from package dependencies due to
  broken numpy==1.26.3 exact pin in jobspy metadata. Installed separately with `--no-deps`
- **Scoring batch limit** - default limit of 50 silently left jobs unscored across runs.
  Changed to no limit (scores all pending jobs in one pass)
- **Missing logging output** - added `logging.basicConfig(INFO)` so per-job progress for
  scoring, tailoring, and cover letters is visible during pipeline runs

### Changed
- **Blocked sites externalized** - moved from hardcoded sets in launcher.py to
  `config/sites.yaml` under `blocked:` key
- **Site base URLs externalized** - moved from hardcoded dict in detail.py to
  `config/sites.yaml` under `base_urls:` key
- **SSO domains externalized** - moved from hardcoded list in prompt.py to
  `config/sites.yaml` under `blocked_sso:` key
- **Prompt improvements** - screening context uses `target_role` from profile,
  salary section includes `currency_conversion_note` and dynamic hourly rate examples
- **`acquire_job()` fixed** - writes `agent_id` and `last_attempted_at` to proper columns
  instead of misusing `apply_error`
- **`profile.example.json`** - added `currency_conversion_note` and `target_role` fields

## [0.1.0] - 2026-02-17

### Added
- 6-stage pipeline: discover, enrich, score, tailor, cover letter, apply
- Multi-source job discovery: Indeed, LinkedIn, Glassdoor, ZipRecruiter, Google Jobs
- Workday employer portal support (46 preconfigured employers)
- Direct career site scraping (28 preconfigured sites)
- 3-tier job description extraction cascade (JSON-LD, CSS selectors, AI fallback)
- AI-powered job scoring (1-10 fit scale with rationale)
- Resume tailoring with factual preservation (no fabrication)
- Cover letter generation per job
- Autonomous browser-based application submission via Playwright
- Interactive setup wizard (`applypilot init`)
- Cross-platform Chrome/Chromium detection (Windows, macOS, Linux)
- Multi-provider LLM support (Gemini, OpenAI, local models via OpenAI-compatible endpoints)
- Pipeline stats and HTML results dashboard
- YAML-based configuration for employers, career sites, and search queries
- Job deduplication across sources
- Configurable score threshold filtering
- Safety limits for maximum applications per run
- Detailed application results logging
