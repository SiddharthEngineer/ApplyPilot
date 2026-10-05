# Current State

**Last updated:** 2026-10-05 (R9 dashboard-api ✅ 6/6)

## Active Plan

**Next: R10 `dashboard-ui`** (session 8, new interactive VPS session), then R11 `dashboard-deploy`.

**R9 `dashboard-api`** ✅ 6/6 (2026-10-05, interactive VPS session 7, commits on `trunk`). New: `tracking.py` (status
model, `status_sql`, `set_status`, `status_events` table), `web/app.py` (`create_app(static_dir, db, generate, run_tasks)`,
everything under `/app`, CSRF header `X-ApplyPilot: 1`, SPA fallback), `web/queries.py` (list filters/sort/paging, facets),
`web/tasks.py` (`dashboard_tasks` queue + `TaskRunner` thread, `generate_for_job`), CLI `applypilot serve`, `web` extra.
DB: `jobs` gains `job_key` (indexed; backfilled by `init_db` and list requests), `user_status`, `status_updated_at`,
`submitted_at`, `responded_at`, `notes`, `drive_folder_id`, `drive_folder_url`. `run_tailoring`/`run_cover_letters` take
`urls=`; `sync_job` saves the job's Drive folder. `job_key()` now lives in `database.py`. Live: health → postgresql;
one generation (Supabase job) → done in 29 s with all three Drive links. Gate: 891 passed, 29 skipped.

**R8 `job-posting-extraction`** ✅ 6/6 (2026-10-05, interactive VPS session, commits on `trunk`). New: `enrichment/posting_model.py`
(`JobPosting`, enums, `posting_schema()`/`batch_schema()`, `EXTRACT_VERSION = 3`), `enrichment/posting_prompt.py`
(`build_extract_prompt`, `format_batch`, boilerplate-first truncation), `enrichment/classify.py` (`classify_title`,
`classify_work_mode`, `parse_location`, `run_classify`; CLI `applypilot classify`), `enrichment/extract.py` (`run_extraction`,
5 jobs/request, per-batch commit, single-job retry, quota stop), 19 `jobs` columns + `PENDING_EXTRACT_WHERE`, `extract`
pipeline stage (after `cover`; upstream `enrich`), `LLM_EXTRACT_MODEL` purpose (default flash-lite), `EXTRACT_BATCH_SIZE`.
Live: classify filled all 3,147 jobs; `run extract --limit 10` = 2 requests; hand QC 18/20 (v2 prompt). Backfill started
with `--limit 1500` (log `~/.applypilot/logs/extract-backfill-2026-10-05.log`); M11 = rerun `applypilot run extract` daily.
Gate: 787 passed, 29 skipped.

**R7 `job-store-postgres`** ✅ (2026-10-05, interactive VPS session). Task 1: engineerfamily b09a98a deployed;
`applypilot`/`applypilot_test` DBs on analytics-db at `127.0.0.1:5432`. Tasks 5–7: the user's `pytest -m pg` run passed (9),
3,147 jobs migrated and verified, and `APPLYPILOT_DATABASE_URL` is in `~/.applypilot/.env`. The agent can't read the prod `.env`,
but ApplyPilot loads the URL itself. New: `db_pg.py` (PgConnection adapter),
`migrate.py` + `applypilot db migrate|verify`, backend dispatch in `database.py` (`APPLYPILOT_DATABASE_URL`), `doctor`
Database row, `pg` test tier (`APPLYPILOT_TEST_DATABASE_URL`). Gate: 677 passed, 29 skipped (SQLite); 686 passed
with a throwaway Postgres 16. Committed on `trunk` as 84c9e92 (2026-10-05). Next: the Task 5/6 runs and Task 7.


**R6 `google-drive-file-library`** (2026-10-02, local session on the laptop, branch `claude/plan-google-drive-file-library`):
Tasks 1–5 and 7 ✅, Task 6 🟡 (live check pending), Task 8 = the user's Google Cloud setup (M8). New modules: `storage/drive_layout.py`,
`storage/drive.py` (OAuth `drive.file`, find-or-create folders, upsert by `appProperties` job key), `storage/sync.py`
(`run_drive_sync`, `ensure_local_pdf`, `moved_pdf_paths`). `tests/fake_drive.py` is the in-memory Drive fake.
Gate on the laptop: `pytest tests/ -q` → 633 passed, 20 skipped. Next step: the user does M8 on the laptop and the VPS, then the live checks in Task 8.

Sessions run on the VPS in VPS mode (`agents/BUILD_AGENT.md` §0); session plan and morning summary in `agents/MORNING.md`.
Session 1 done: R0 `test-tiers-and-qc` (4/5, Task 4 capture = M6) and R3 `scoring-error-recovery` (2/2 ✅).
Session 2 done: R2 `job-board-discovery-repair` 8/8 ✅ (PR #5).
Session 3 done: R4 `gemini-free-tier-llm` 5/5 ✅ (PR #6), plus per-model `LLM_RPM_LIMITS` / `LLM_RPD_LIMITS` (PR #7).
Session 4 done: R5 `resume-template-tailoring` 7/7 ✅ (PR #8). The user signed off the template (M4).
After session 4 (user request, PR #9): 503 failover to `LLM_FALLBACK_MODEL` (default flash-lite, 5-min cooldown, 503 counted
in the local daily tally), `--validation` defaults to lenient for content-library tailoring (`pipeline.tailor_validation_mode`),
plus data fixes outside the repo: typo, full_name, GitHub link (see the plan's Historical Record).
- New modules: `scoring/resume_model.py` (TailoredResume), `scoring/template.py` (Jinja renderer, base-resume parser,
  fixed blocks, text export), `config/resume_template.default.html`. CLI: `applypilot template init|preview`, `run --limit`.
- Content-library tailoring: slug-citing structured output (enums), slot ranges from the base resume, `validate_resume_model`
  + `validate_provenance`, judge sees cited facts + base skills + date, one-page fit loop, `.pdf/.json/.txt` outputs,
  `tailored_resume_path` = PDF. `--source` defaults to content-library. Cover letters use the job's sidecar as evidence.
- User data on the VPS (not in git): `~/.applypilot/resume_template.html` (hand-authored), `resume_fixed.yaml`,
  `template_preview.pdf/.html`, `template_side_by_side.png`; 3 live tailored resumes in `tailored_resumes/` (DB rows set).
Next step: all ROADMAP initiatives are ✅ except R0 (4/5, Task 4 = the user's M6). No plan is queued, so a new plan is needed (see MORNING.md).

Gate on the VPS: `. .venv/bin/activate && pytest tests/ -q` → 579 passed, 20 skipped in ~17s. LLM tier: export `GEMINI_API_KEY`
from the repo-root `.env`, then `pytest -m llm --run-llm tests/test_live_scoring_tailoring_cover.py`.
The user's LLM env lives in `/srv/ApplyPilot/.env` (`~/.applypilot/.env` doesn't exist; `load_env` falls back to CWD).
System packages added on the VPS: `fonts-liberation` (session 4). PDF inspection in sessions: install PyMuPDF to a scratch dir
(`uv pip install --target <scratch>/pylib pymupdf`), not into `.venv`.

### Blockers

None for code. Known gaps:
- The content-library judge is noisy on flash-lite; it's off by default now (lenient) for content-library tailoring.
- `run_scoring` writes results only at the end of a run (unchanged from session 3).

### Previous plans
### Progress — Cap Live Test Scope

| Task | Status |
|------|--------|
| Task 1: Extend Workday API for bounded runs (no monkeypatch) | ✅ Complete |
| Task 2: Cap TestWorkdayLive to 2 employers × 2 queries (no monkeypatch) | ✅ Complete |
| Task 3: Audit and remove monkeypatch from remaining live/llm suites | ✅ Complete |

### Progress — Fix Live Test Failures

| Task | Status |
|------|--------|
| Fix 1: test_smartextract_hackernews — correct API usage | ✅ Complete |
| Fix 2: test_jobspy_single_site — add indeed/linkedin to xfail, thread conn param | ✅ Complete |

### Current Task

None — plan complete.

### Progress — Gemini 2.5 Flash Lite Migration

| Task | Status |
|------|--------|
| Task 1: Update core LLM client discovery default (llm.py, config.py) | ✅ Complete |
| Task 2: Update CLI doctor and setup wizard defaults (cli.py, wizard/init.py) | ✅ Complete |
| Task 3: Update env example and docs (.env.example, README.md) | ✅ Complete |
| Task 4: Update LLM test infrastructure (conftest.py, test_llm.py, test_live_scoring_tailoring_cover.py) | ✅ Complete |
| Task 5: Update remaining wizard/doctor test fixtures (test_init_wizard.py, test_doctor_content_library.py) | ✅ Complete |
| Task 6: Live integration verification | ✅ Complete (target pivoted to gemini-3.1-flash-lite — 2.5-flash-lite deprecated for new users) |

### Progress — Discover Crawl Resilience

| Task | Status |
|------|--------|
| Task 1: Normalize & validate `country_indeed` before JobSpy call | ✅ Complete |
| Task 3: Exclude hard scrape errors from consecutive empty counting | ✅ Complete |
| Task 2: Revert wizard default `site_fail_threshold` to 3 | ✅ Complete |
| Task 4: Document board flakiness and auto-skip in README | ✅ Complete |

### Current Task

None — plan complete.

### Progress — ZipRecruiter 403 follow-up (Tasks 5–7)

| Task | Status |
|------|--------|
| Task 5: Wizard writes explicit `sites` + `site_fail_threshold` | ✅ Complete |
| Task 6: Migrate user's live search config (`~/.applypilot/searches.yaml`) | ✅ Complete |
| Task 7: Clarify auto-skip log behavior in README | ✅ Complete |

### Progress — LLM Rate-Limit Mitigation

| Task | Status |
|------|--------|
| Task 1: Add client-side RPM limiter to LLMClient | ✅ Complete |
| Task 2: Heuristic pre-filter for Judge API responses | ✅ Complete |
| Task 3: Batch Judge API responses into a single LLM call | ✅ Complete |
| Task 4: Per-domain strategy cache and target deduplication | ✅ Complete |
| Task 5: Tiered model configuration and cheaper defaults | ✅ Complete |
| Task 6: Integrate OpenCode free models as an LLM provider | ✅ Complete |
| Task 7: Wire new env vars through wizard, doctor, and docs | ✅ Complete |

### Current Task

None — both plans complete.

### Progress — Integration Smoke Suite with Pickle Fixtures

| Task | Status |
|------|--------|
| Task 1: Mark infrastructure + conftest | ✅ Complete |
| Task 2: Fixture capture script | ✅ Complete |
| Task 3: Live JobSpy per-site (n=1) | ✅ Complete |
| Task 4: Filtering independence (no LLM, pickle fixtures) | ✅ Complete |
| Task 5: Scoring/Tailoring/Cover live LLM | ✅ Complete |
| Task 6: Enrich/Workday/SmartExtract/PDF | ✅ Complete (gap fix: added scrape_detail_page test) |
| Task 7: Docs (CONTRIBUTING.md) | ✅ Complete |

### Completed This Session

- **Integration Smoke Suite — Task 6 gap fix** — Added missing `scrape_detail_page` live test to `tests/test_enrich_smoke.py`. The plan listed3 files for Task 6 but only `test_enrich_smoke.py` was created; the `scrape_detail_page` test (Tier 1-3 cascade) was omitted. Added `TestDetailPageLive::test_scrape_detail_page` with `@pytest.mark.live @pytest.mark.expensive` that uses Playwright to exercise `enrichment/detail.py:531` against a real URL. Fixed ruff `BLE001` (narrowed `except Exception` to `except (TimeoutError, OSError, RuntimeError)`). All 13 enrich smoke tests pass (9 cheap, 4 skipped); ruff clean.
- **LLM Rate-Limit Mitigation — lint cleanup (post-Task 7)** — Verified all 7 tasks' acceptance criteria against `trunk`. Fixed the only two net-new ruff errors the plan introduced in `src/applypilot/discovery/smartextract.py`: removed the now-unused `get_client` import (route is fully on `get_discovery_client()`) and changed an invalid-type guard from `raise ValueError` to `raise TypeError` in `judge_api_responses()`. Baseline ruff on the 5 plan files (43) vs `trunk` (48) → net-new reduced to 0. Plan's 133 targeted tests still pass (`test_llm` 28, heuristic 17, batch_judge 16, cache 18, config 5, init_wizard 41, doctor 9 — counts include pre-existing suites). See `agents/CHANGELOG.md`.
- **LLM Rate-Limit Mitigation — Task 7: Wire new env vars through wizard, doctor, and docs** — Surface `LLM_DISCOVERY_MODEL`/`LLM_RPM_LIMIT`/`OPENCODE_API_KEY` across the user-facing surface:
  - `src/applypilot/wizard/init.py:_setup_ai_features()`: after the provider block, prompts `LLM_DISCOVERY_MODEL` (default `gemini-2.0-flash-lite` when provider=gemini, else falls back to `LLM_MODEL`) and `LLM_RPM_LIMIT` (default `12`), appending both to `~/.applypilot/.env`.
  - `src/applypilot/cli.py:doctor()`: `Gemini` branch now also validates the discovery model against the Gemini model list (`Available:` list on miss); after the LLM key block, prints `Discovery model: <...>` and `RPM limit: <...> (window ...s)` lines whenever any LLM provider is configured.
  - `.env.example`: added commented `OPENCODE_API_KEY` and `LLM_URL` (OpenCode gateway) entries.
  - `README.md`: new `### Cost & Rate Limits` subsection covering `LLM_RPM_LIMIT=12`, `LLM_DISCOVERY_MODEL=gemini-2.0-flash-lite` vs `gemini-3.6-flash`, `--validation lenient`, `--no-cache`, and `opencode/*` free models.
  - `tests/test_init_wizard.py`: 2 new tests (writes `LLM_DISCOVERY_MODEL`+`LLM_RPM_LIMIT` on defaults; non-gemini default falls back to `LLM_MODEL`); updated 3 existing AI-feature tests for the 2 new prompts.
  - `tests/test_doctor_content_library.py`: new `TestDoctorRateLimitTuning` (3 tests) — discovery/RPM lines present, bad discovery model warns with `Available:`, OpenCode provider doesn't report Gemini missing.
  - All 85 targeted tests pass (`test_init_wizard` 41, `test_llm` 28, `test_config` 5, `test_doctor_content_library` 9 — note wizard/doctor/llm/config counts include pre-existing suites); ruff: no new errors on changed files (17 pre-existing, unrelated).

- **LLM Rate-Limit Mitigation — Task 6: Integrate OpenCode free models as an LLM provider** — First-class OpenCode Zen gateway provider reusing the OpenAI-compatible transport:
  - `src/applypilot/llm.py`: `_detect_provider()` now checks `OPENCODE_API_KEY` before Gemini/OpenAI; returns `https://opencode.ai/zen/v1` + `opencode/nemotron-3-nano-free` (or `LLM_MODEL` override). `LLM_URL` containing `opencode.ai` also routes there. An explicit local URL (`127.0.0.1`/`localhost`) keeps priority and is NOT hijacked by `OPENCODE_API_KEY`.
  - `src/applypilot/cli.py:doctor()`: `has_opencode` branch before Gemini/OpenAI prints `OpenCode (model)` and never prints `MISSING` for Gemini when `OPENCODE_API_KEY` is set.
  - `src/applypilot/wizard/init.py:_setup_ai_features()`: `choices` gains `opencode`; prompts `OPENCODE_API_KEY` (with `opencode auth` hint) + `LLM_MODEL` default `opencode/nemotron-3-nano-free`, optional `LLM_URL` gateway override. `detected_provider`/`has_existing_llm` include `OPENCODE_API_KEY`.
  - `tests/test_llm.py:TestDetectProvider`: added 4 tests (opencode default, respects `LLM_MODEL`, via `LLM_URL`, local `127.0.0.1` not hijacked). All 28 `test_llm.py` tests pass; `test_init_wizard.py` (41) and `test_config.py` (5) pass; ruff: no new errors (17 pre-existing, unrelated to this change).

- **LLM Rate-Limit Mitigation — Task 5: Tiered model configuration and cheaper defaults** — Per-stage models so discovery uses a cheaper model than tailoring:
  - `src/applypilot/llm.py`: `_detect_provider(purpose)` now accepts `purpose="discovery"`; on Gemini with no `LLM_MODEL`/`LLM_DISCOVERY_MODEL` set it defaults to `gemini-2.0-flash-lite` (vs `gemini-3.6-flash` for all other stages). Added `get_discovery_client()` singleton (independently memoized from `get_client()`), reading `LLM_RPM_LIMIT`/`LLM_RPM_WINDOW` like `get_client()`. `LLM_DISCOVERY_MODEL`/`LLM_SCORING_MODEL`/`LLM_TAILOR_MODEL` honored.
  - `src/applypilot/discovery/smartextract.py`: `ask_llm()` now routes through `get_discovery_client()`; `judge_api_responses()` (batch + sequential fallback) and strategy selection consume the cheaper discovery model (`grep -c get_discovery_client == 2`, `scorer.py` untouched → `== 0`).
  - `src/applypilot/config.py:DEFAULTS`: added `llm_rpm_limit: 12`, `llm_discovery_model: gemini-2.0-flash-lite`.
  - `.env.example`: documented `LLM_RPM_LIMIT`, `LLM_RPM_WINDOW`, `LLM_DISCOVERY_MODEL`, `LLM_SCORING_MODEL`, `LLM_TAILOR_MODEL`.
  - `tests/test_llm.py`: added `TestDiscoveryClient` (2) + 3 `_detect_provider` purpose tests; updated `test_smartextract_heuristic.py`/`test_smartextract_batch_judge.py` patches to `get_discovery_client`. 77 relevant tests pass; ruff: no new errors on changed files.
- **LLM Rate-Limit Mitigation — Plan correction + Task 4 reconciliation** — Audited `llm-rate-limit-mitigation.md` against `HEAD` and `git log`; rewrote plan to unblock BigPickle:
  - Task 1: corrected defaults (`LLM_RPM_LIMIT=0` disabled vs plan's stale `12`), pinned line refs `src/applypilot/llm.py:88-110,342`.
  - Task 2/3: pinned exact refs `smartextract.py:385-425,449-614` and test paths.
  - Task 4: marked ✅ Complete — code already in `d8ee86b` (`smartextract.py:49-91` cache, `cli.py:112` --no-cache, `pipeline.py:62,294`, 18 tests at `tests/test_smartextract_cache.py`) but plan still said ❌ Not started; corrected cache path `CONFIG_DIR/.smartextract_cache.json`, noted `api_response` not cached.
  - Tasks 5-7: expanded to single-responsibility steps with explicit `DEFAULTS` keys, `get_discovery_client()` singleton, `OPENCODE_API_KEY` priority, and doctor/wizard wiring to prevent implementation guesswork.
- **LLM Rate-Limit Mitigation — Task 4: Per-domain strategy cache and target deduplication** — (landed in `d8ee86b` as "Clean up plan queue"):
  - `src/applypilot/discovery/smartextract.py`: `_strategy_cache`, `_CACHE_FILE`, `_get_cache_key()`, `_load/_save_strategy_cache()`, `_run_one_site()` cache hit/miss + CAPTCHA/shape invalidation, `build_scrape_targets()` `seen` dedup. `api_response` intentionally not cached.
  - `src/applypilot/cli.py` + `src/applypilot/pipeline.py`: `--no-cache` flag plumbing.
  - `tests/test_smartextract_cache.py`: 18 tests (5 dedup, 2 key, 3 persistence, 8 cache-hit/miss/no-cache/captcha).
- **LLM Rate-Limit Mitigation — Task 3: Batch judge** — Collapsed per-response judge loop into a single batched LLM call with fallback:
  - `src/applypilot/discovery/smartextract.py`: Added `JUDGE_BATCH_PROMPT` that lists all candidates numbered `[1]..[N]` and asks the LLM to return a JSON array of verdicts. Added `_format_response_summary()` helper that formats each response (url, status, size, type, fields, sample truncated to 300 chars). Extracted `_judge_sequential()` for fallback. Refactored `judge_api_responses()`: with >1 candidate, builds a batched prompt and makes 1 LLM call; parses the array response and maps index→verdict; falls back to sequential if batch parsing fails (invalid JSON, non-list response, missing verdicts). Single candidate still uses sequential path directly. 16 new tests in `tests/test_smartextract_batch_judge.py`: 4 summary formatter tests, 5 happy-path batch tests (5 responses→1 call, prompt content, all-relevant, all-irrelevant, single-candidate sequential), 4 fallback tests (invalid JSON, missing verdicts, non-list response, error handling), 3 integration tests (heuristic+batch combined, prompt size budget, empty-after-heuristic). All 33 tests (17 heuristic + 16 batch) pass, ruff clean.

### Test Results (verified 2026-08-28)

```
tests/test_smartextract_heuristic.py: 17 passed ✅
tests/test_smartextract_batch_judge.py: 16 passed ✅
tests/test_smartextract_cache.py: 18 passed ✅ (Task 4 — previously uncounted)
tests/test_llm.py: 24 passed ✅ (Task 5 added 5: TestDiscoveryClient x2 + 3 purpose tests)
tests/test_config.py: 5 passed ✅
ruff: no new errors on changed files ✅
```

| Task | Status |
|------|--------|
| Task 1: Change plan worker default model to Nemotron 3.5 Lightning | ✅ Complete |
| Task 2: Fix auto-apply OpenCode default model | ✅ Complete |
| Task 3: Add model fallback list to plan worker | ✅ Complete |
| Task 4: Document model selection | ✅ Complete |

### Current Task

None — both plans complete.

### Completed This Session

- **Discover Crawl Resilience (Tasks 1–4)** — finished the plan:
  - `src/applypilot/discovery/jobspy.py`: Added `_normalize_country()` helper that validates against JobSpy's supported country allowlist; falls back to `"usa"` with a warning for unknown values. Wired into `_run_one_search` and `search_jobs`. Modified `_full_crawl` to skip `tracker.note()` when `result["errors"] > 0` so hard errors don't penalize boards.
  - `src/applypilot/wizard/init.py`: Changed `site_fail_threshold` default from `1` to `3` in generated `searches.yaml`.
  - `tests/test_jobspy.py`: Added 6 `_normalize_country` unit tests + 1 integration test for error-excluded tracker.
  - `tests/test_init_wizard.py`: Updated threshold assertion to `== 3`.
  - `README.md`: Documented board flakiness, auto-skip behavior, and country validation fallback.

### Test Results (verified 2026-08-28)

```
tests/test_init_wizard.py: 41 passed ✅
tests/test_jobspy.py: 32 passed ✅
tests/test_pipeline.py: 9 passed ✅
tests/test_launcher.py: 13 passed ✅
ruff: clean on changed files ✅
```

### Recommended Next Step

Task 2: Heuristic pre-filter for Judge API responses (zero-LLM skip) in `smartextract.py`.

- **Plan completion must be plan-specific.** The global STATE.md "no remaining work" phrase is not a safe completion signal because it is shared across plans. The reliable signal is the plan file's own `Status: ✅ Completed` line, which the implementing agent updates.
- **Wizard and live config exclude `zip_recruiter` rather than lean on the threshold.** Zero requests = zero log noise; `site_fail_threshold: 1` remains as a safety net for any other board that starts returning 0 results.

### Blockers

None.

### Recommended Next Step

No queued plans. Next session can enqueue a new plan, or run the full test suite to reconfirm counts.

### Historical Context — OpenCode Model Selection (prior session)

- **OpenCode Model Selection** — Selected optimal OpenCode models for the two agent use cases and added fallback handling:
  - `scripts/plan_worker.py`: Default model changed from `opencode/mimo-v2.5-free` to `opencode/nemotron-3.5-lightning-free` (NVIDIA execution tier) in the `load_queue()` fallback, `worker_loop()` model lookup, and `show_status()` display. Added a module-level `MODEL_FALLBACKS` ordered list (`[nemotron-3.5-lightning-free, nemotron-3-ultra-free, big-pickle, mimo-v2.5-free]`); on a non-zero `run_agent()` exit the worker retries the same iteration with the next model before counting it as a retry/failure.
  - `agents/plan_queue.json`: `model` field migrated to `opencode/nemotron-3.5-lightning-free`.
  - `src/applypilot/cli.py`: `apply --model` is now backend-aware. `--backend opencode` resolves the default to the valid `opencode/nemotron-3-ultra-free` (single-pass patch rewrite, not Lightning); `--backend claude` keeps `haiku`. An explicit `--model` always wins.
  - `README.md` / `CONTRIBUTING.md`: Documented the OpenCode auto-apply default + override and the plan worker's model default + fallback list.

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
| `src/applypilot/llm.py` | LLM client with Gemini compat/native fallback |
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
| `src/applypilot/discovery/jobspy.py` | JobSpy job discovery + site fail tracking |
| `tests/test_llm.py` | LLMClient Gemini fallback and provider detection tests |
| `tests/test_jobspy.py` | JobSpy site counting and tracker tests |
| `tests/test_pipeline.py` | Pipeline discover banner tests |
| `tests/test_content_library_e2e.py` | End-to-end integration tests for content-library tailoring |
| `tests/test_init_wizard.py` | Init wizard tests for content library support |
| `tests/test_doctor_content_library.py` | Doctor command content library validation tests |

## Testing

- Run `PYTHONPATH=src .venv/bin/python -m pytest tests/ -v` for unit tests
- Run `ruff check src/` for linting

### Historical Context — --auto Flag Restoration (2026-08-28)

- **Restore --auto flag to plan_worker.py** — The `--auto` flag was removed from `scripts/plan_worker.py` by a previous agent, preventing build agents from having all permissions enabled. Restored the flag in `run_agent()` at line 156. This flag is the OpenCode equivalent of Claude Code's `--permission-mode bypassPermissions`, allowing agents to run autonomously without user confirmation. Command structure now matches `launcher.py:_build_opencode_cmd()`.
