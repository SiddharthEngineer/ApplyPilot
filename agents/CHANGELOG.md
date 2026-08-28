# Changelog

All notable changes to ApplyPilot will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Completed
- **Gemini 3.6-Flash Model Migration** (2026-08-27) — Migrated ApplyPilot's default Gemini model from `gemini-2.5-flash` (retired, returns 404 for new users) to `gemini-3.6-flash`. Updated `_detect_provider()` default in `llm.py`, `applypilot doctor` validation default in `cli.py`, setup wizard default prompt in `wizard/init.py`, scorer error hints, `.env.example`, and `test_llm.py` fixture. Verified live: `gemini-3.6-flash` returns HTTP 200 on both the OpenAI-compat and native `generateContent` endpoints; `LLMClient.chat()` returned "PONG" with the new default. Note: 2.5-flash returns 404 even on native API, so the existing compat→native fallback cannot rescue it. All non-network tests pass (258 total); `tests/test_pipeline.py` hangs due to unmocked live Workday/smart-extract scrapers (pre-existing, unrelated to this change).
- **Workday SSL Certificate Fix** (2026-08-27) — Fixed `SSL: CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate` error when scraping Workday employer portals on macOS. Added module-level `_ssl_context = ssl.create_default_context(cafile=certifi.where())` to `workday.py`, updated `setup_proxy()` to inject `HTTPSHandler(context=_ssl_context)` into the opener chain, and updated `_urlopen()` to pass `context=_ssl_context` when no proxy is configured. Uses `certifi` CA bundle (already a transitive dependency via `httpx`) — no new dependencies needed. Created `tests/test_workday_ssl.py` with 5 tests verifying SSL context existence, certifi CA bundle loading, CERT_REQUIRED verify mode, proxy setup preservation, and TLS protocol version. Integration test verified against 5 live employers (Manulife, TD, Sun Life, Desjardins, Intact Financial) — all return jobs without SSL errors. All tests pass, lint clean.
- **Gemini 404 Scoring Fix** (2026-08-27) — Fixed job scoring (`applypilot run score`) when using `GEMINI_API_KEY` so that LLM calls no longer 404 on the OpenAI-compat endpoint (`/v1beta/openai/chat/completions`). Extended `LLMClient._chat_compat()` fallback from 403-only to cover 400/403/404 for Gemini providers, reusing the existing `_GeminiCompatForbidden` sentinel and native `generateContent` path. Non-Gemini providers (OpenAI) still raise on 404/400 without fallback. Updated default model from `gemini-2.0-flash` to `gemini-2.5-flash` across `llm.py`, `cli.py`, `.env.example`, and wizard. Added doctor command model validation that queries Gemini API model list and warns if configured `LLM_MODEL` not found. Hardened scorer error logging with Gemini-specific hints (`check GEMINI_API_KEY, LLM_MODEL`). Created 15 mocked unit tests in `tests/test_llm.py` covering compat fallback (404/400/403→native), native failure, fallback persistence, 429 retry, provider detection, and OpenAI no-fallback. `ruff check src/` and `PYTHONPATH=src .venv/bin/python -m pytest tests/ -v` pass.
- **ZipRecruiter 403 Handling** (2026-08-27) — Boards that repeatedly return 0 results are auto-skipped for the rest of a crawl, preventing log spam from blocked boards. Added `_site_counts()` and `_SiteTracker` to `jobspy.py`: tracks per-crawl consecutive empty results and disables boards after `site_fail_threshold` (configurable, default 3) consecutive 0-result searches. Wired tracker through `_full_crawl` and `run_discovery` — `disabled_sites` and `site_stats` returned in all code paths. `_run_one_search` now returns per-site counts. Pipeline `discover` stage prints a yellow banner when sites are skipped and updates `stats["jobspy"]`. Added `site_fail_threshold` to `searches.example.yaml` defaults. Documented auto-skip behavior and ZipRecruiter Cloudflare 403 upstream block in README. 25 jobspy tests + 4 pipeline tests, 158 total pass, lint clean (pre-existing only).
- **CAPTCHA Solve via cred-server tool** (2026-08-27) — Moved CapSolver API key handling and HTTP calls from the LLM's browser context (broken) to a new `captcha_solve` MCP tool on the cred-server. `cred_server.py` now exposes `captcha_solve` which reads `CAPSOLVER_API_KEY` from its own env, performs createTask→poll→getTaskResult via `httpx.AsyncClient`, and returns `{"success", "token"|"message"}`. The prompt's CAPTCHA SOLVE section no longer instructs the LLM to read the key or call `api.capsolver.com` via `browser_evaluate`; it instructs calling `cred.captcha_solve` and injecting the returned token. Token injection (STEP 3) via `browser_evaluate` is preserved. Added `_get_capsolver_key()`, `CAPTCHA_TYPE_MAP`, `_solve_captcha()` in `cred_server.py`. Added 15 unit tests in `test_cred_server.py`, updated 7 prompt tests in `test_prompt.py`. All 212 tests pass, lint clean (pre-existing warnings only).
- **Secure Passwords at Rest** (2026-08-27) — Eliminated plaintext passwords from MCP config JSON files on disk. `cred_server.py` now reads passwords from `profile.json` via `APPLYPILOT_APP_DIR` env var (with env-var fallback for backward compatibility). `launcher.py` MCP configs contain only `APPLYPILOT_APP_DIR` and `CAPSOLVER_API_KEY` — zero `APPLYPILOT_PW_*` keys. Added `set_restricted_permissions()` in `config.py` to enforce 0o600 on `profile.json` and `.env`, 0o700 on `~/.applypilot` directory. Applied in `load_profile()`, wizard `_setup_profile()`, `_setup_ai_features()`, and `_setup_auto_apply()`. Created `tests/test_config.py` (5 tests), updated `tests/test_cred_server.py` (+6 tests) and `tests/test_launcher.py`. All 193 tests pass, lint clean.
- **Plan Queue Worker** (2026-08-27) — Created `scripts/plan_worker.py`: a reusable orchestrator that continuously implements plans from `agents/plan_queue.json`. Reads the top plan, launches opencode agent sessions with `--auto`, detects completion via STATE.md and plan file status markers, dequeues completed plans, and immediately starts the next. Supports max iterations per plan (20), retry on failure (2 retries), 30-min hard timeout, structured logging. CLI: `--enqueue`, `--dequeue`, `--status`, `--dry-run`. Added `plan_queue.json` to `agents/`, `plan_worker.log` to `.gitignore`.
- **Hide Passwords from LLM** (2026-08-26) — Created `src/applypilot/apply/cred_server.py`: standalone MCP credential server over stdio that reads passwords from env vars, connects to Chrome via CDP, and fills login forms. The LLM calls `ats_login(ats="workday", email="...", cdp_port=9222)` and never sees the password. Refactored `launcher.py`: added `site_passwords` parameter to MCP config builders, added `"cred"` MCP server entry with per-ATS env vars, updated OpenCode to pipe prompt via stdin (not CLI arg) to prevent `ps aux` leakage. Updated `prompt.py`: removed password table, replaced with `ats_login` tool instructions, removed CapSolver API key from prompt text, added `cdp_port` parameter. Created 42 new tests across 3 test files. All 182 tests pass, lint clean.
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
