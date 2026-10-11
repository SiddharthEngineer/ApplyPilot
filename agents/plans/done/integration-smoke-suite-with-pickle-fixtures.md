# Plan: Integration Smoke Suite with Pickle Fixtures + Live LLM Marks
**Started:** 2026-08-31
**Status:** ✅ Complete

## Goal
Add an opt-in smoke/integration suite that exercises all 6 stages of `applypilot run` (`discover -> enrich -> score -> tailor -> cover -> pdf`) with `n=1` / small payloads, keeps the default `pytest tests/ -v` fast/no-keys/no-network, and provides a local capture script that pickles real crawl objects so filtering/scoring/tailoring unit tests stay independent in the future. Use `gemini-2.0-flash-lite` for all LLM smoke tests; fixtures are gitignored and generated locally via `scripts/capture_fixtures.py`. Separate `live` (network/Playwright) and `llm` (Gemini API) marks allow `pytest --run-live --run-llm -v` to run everything at once.

## Success Criteria
1. `pytest tests/ -v` passes with no API keys (new `live`/`llm` tests auto-skipped; `pyproject.toml` registers marks, `tests/conftest.py` adds `--run-live`, `--run-llm` opts via `pytest_collection_modifyitems`).
2. `pytest -m live --run-live -v` hits each JobSpy board once (`indeed,linkedin,zip_recruiter,glassdoor,google`) via `search_jobs(results_wanted=1)` (`src/applypilot/discovery/jobspy.py:338`) and asserts DB insert via `store_jobspy_results` (`src/applypilot/database.py:329`).
3. `pytest -m llm --run-llm -v` (sets `LLM_DISCOVERY_MODEL=gemini-2.0-flash-lite` + `LLM_MODEL=gemini-2.0-flash-lite`) calls `score_job` (`src/applypilot/scoring/scorer.py:75`), `tailor_resume` (`src/applypilot/scoring/tailor.py:663`), and `generate_cover_letter` (`src/applypilot/scoring/cover_letter.py:120`) against 2-3 captured jobs and asserts score 1-10, valid JSON, and validator pass (`src/applypilot/scoring/validator.py:99`).
4. Location/title filtering tested locally without LLM/network: assertions against `tests/fixtures/jobs_raw.pkl` + `jobs_enriched.json` using `_location_ok` from `jobspy.py:130`, `smartextract.py:104`, `workday.py:55` (covers drift).
5. `python scripts/capture_fixtures.py --n 1` (full `run_discovery()` path `src/applypilot/pipeline.py:68`) creates `tests/fixtures/*.pkl` + `*.json` <1MB and is idempotent; `tests/fixtures/.gitignore` keeps them out of repo; `CONTRIBUTING.md:82` documents regeneration.
6. Enrich detail cascade (`src/applypilot/enrichment/detail.py:531` Tier 1-3) covered: cheap unit (Tier1/2 + `clean_description:488` + `resolve_url:58`) in default suite, plus live 1-URL `scrape_detail_page` with `playwright` gated by `live`.
7. Workday (`workday.py:501`) + SmartExtract (`smartextract.py:49`) discovery live smokes (n=1 site each) included as `live`, PDF conversion (`scoring/pdf.py:batch_convert`) covered as cheap non-llm.
8. `CONTRIBUTING.md:82` updated with separate `live`/`llm` invocations and a single "run everything" command; no `.github/workflows/ci.yml` change (fork has none configured).

## Task Chain

### Task 1: Mark infrastructure + conftest
**Files:** `pyproject.toml` (modify), `tests/conftest.py` (new)
**What:** Register marks in `[tool.pytest.ini_options]` as `markers = ["live: real network/Playwright", "llm: real Gemini calls", "expensive: live or llm"]` and implement `pytest_addoption(--run-live, --run-llm, --run-expensive)` plus `pytest_collection_modifyitems` that skips `live` unless `--run-live` or `--run-expensive`, skips `llm` unless `--run-llm` or `--run-expensive`, skips `expensive` unless any flag. Add helper `requires_api_key()` that checks `GEMINI_API_KEY|OPENAI_API_KEY|OPENCODE_API_KEY|LLM_URL` (mirrors `src/applypilot/config.py:263` `get_tier`) and skips with `pytest.skip("No LLM provider")` if missing; for `llm` tests force `os.environ["LLM_MODEL"]="gemini-2.0-flash-lite"` and `LLM_DISCOVERY_MODEL` unless already set, and reset `applypilot.llm._instance/_discovery_instance` per session. Ensure default `pytest tests/ -v` excludes expensive/live/llm without flags.
**Acceptance:**
- `pytest --markers` lists `live`, `llm`, `expensive`
- `pytest tests/ -v` skips new tests (collection shows `SKIPPED` or `deselect` when no flag)
- `pytest -m live --run-live -v` runs live; `pytest -m llm --run-llm -v` runs llm; `pytest --run-live --run-llm -v` runs both
- `ruff check src/` clean on changed files
**Status:** ✅ Complete

### Task 2: Fixture capture script (full discover path, pickle + JSON)
**Files:** `scripts/capture_fixtures.py` (new), `tests/fixtures/.gitignore` (new), `.gitignore` (modify), `tests/fixtures/README.md` (new)
**What:** Script that follows the former (full `run_discovery()` wiring `src/applypilot/pipeline.py:68` -> `jobspy.py:584` + `workday.py:run_workday_discovery` + `smartextract.py:run_smart_extract`) but with `results_per_site=1`, `hours_old=24`, single query `software engineer`, single location `San Francisco, CA`, `workers=1`, isolated `APPLYPILOT_DIR` tmp or read-only `~/.applypilot`. Captures: `jobs_raw.pkl` (pickle of DataFrame rows + `_site_counts` dict `jobspy.py:515` + DB rows via `get_jobs_by_stage`), `jobs_enriched.json` (3 jobs where `full_description` populated via enrich Tier 1/2, truncated 6000 chars like `scorer.py:89`), `profile_anonymized.json` (from `profile.example.json:1`, no secrets), `resume_sample.txt`, `smartextract_intel_sample.pkl` (1 site `collect_intelligence` output). Saves both pickle + JSON (pickle fidelity, JSON diffable). Creates `tests/fixtures/.gitignore` with `*.pkl`, `*.json`, `!README.md` and root `.gitignore` entry for `tests/fixtures/*.pkl` if needed. Script is idempotent and never writes `~/.applypilot/.env` secrets; re-running overwrites deterministically.
**Acceptance:**
- `python scripts/capture_fixtures.py --n 1 --sites indeed,linkedin` creates 5 files <200KB in `tests/fixtures/`
- `python -c "import pickle; pickle.load(open('tests/fixtures/jobs_raw.pkl','rb'))"` succeeds; JSON loads valid
- No secrets committed; re-run overwrites without error
- `ruff check scripts/capture_fixtures.py` clean
**Status:** ✅ Complete

### Task 3: Live JobSpy per-site (n=1)
**Files:** `tests/test_live_jobspy.py` (new)
**What:** `@pytest.mark.live @pytest.mark.expensive` parametrized over `["indeed","linkedin","zip_recruiter","glassdoor","google"]` calling `search_jobs(query="software engineer", location="San Francisco, CA", sites=[site], results_per_site=1, hours_old=72)` `jobspy.py:338`. Uses `tmp_path` `APPLYPILOT_DIR` monkeypatch + `init_db` isolation (`database.py:62`) to avoid polluting `~/.applypilot/applypilot.db`. Asserts no exception, `result` has `total/new/existing`, if `total>0` then DB `get_stats()["total"]>=1` (`database.py:222`). `glassdoor/google/zip_recruiter` expected `xfail` (blocked `config/sites.yaml:12`, 403 `README.md:153`) — `pytest.xfail` if 0 results, `strict=False`.
**Acceptance:**
- `pytest tests/test_live_jobspy.py -m live --run-live -v` runs 5 cases <120s total; without flag 0 run (skipped)
- At least `indeed`/`linkedin` return >=1 on stable network; flaky boards xfail not fail
**Status:** ✅ Complete

### Task 4: Filtering independence (no LLM, pickle fixtures)
**Files:** `tests/test_filtering_smoke.py` (new)
**What:** Loads `tests/fixtures/jobs_enriched.json` (or fallback synthetic if missing → `pytest.skip("Run python scripts/capture_fixtures.py --n 1")`). Tests `_location_ok` against all 3 impls (`jobspy.py:130`, `smartextract.py:104`, `workday.py:55`) with table: remote→pass, `San Francisco`→pass, `New York only`→reject, `None`→pass; tests `exclude_titles` (`src/applypilot/config/searches.example.yaml:104`) case-insensitive; tests `location_accept`/`reject` wiring via `_load_location_filter` `workday.py:45` and `smartextract.py:95`. Uses pickle `jobs_raw.pkl` `filtered` count (`jobspy.py:322`). No LLM/network.
**Acceptance:**
- `pytest tests/test_filtering_smoke.py -v` 10-12 tests pass without API keys/network
- Covers all 3 `_location_ok` implementations to catch drift
**Status:** ✅ Complete

### Task 5: Scoring / Tailoring / Cover live LLM (gemini-2.0-flash-lite)
**Files:** `tests/test_live_scoring_tailoring_cover.py` (new)
**What:** `@pytest.mark.llm @pytest.mark.expensive` (each also `expensive`). Loads `profile_anonymized.json` + `resume_sample.txt` + 2 enriched jobs from fixtures (or synthetic fallback). Sets `LLM_MODEL=gemini-2.0-flash-lite` and `LLM_DISCOVERY_MODEL=gemini-2.0-flash-lite` via `conftest` env fixture. Calls `score_job` `scorer.py:75` with `client.model == gemini-2.0-flash-lite` assert, checks `1<=score<=10`, `keywords` str, `reasoning` len>20. Calls `tailor_resume(..., validation_mode="lenient", max_retries=1)` `tailor.py:663` + `validate_json_fields(..., mode="lenient")` `validator.py:99` assert `passed`. Calls `generate_cover_letter(..., validation_mode="lenient")` `cover_letter.py:120` assert starts `Dear`, <275 words in normal mode, no `LLM_LEAK_PHRASES`. Caps: 2 jobs, scoring 512 tokens, tailoring 2048 tokens. Skips if `llm._detect_provider` raises `RuntimeError` (no key). Resets `llm._instance/_discovery_instance` per test.
**Acceptance:**
- `pytest -m llm --run-llm -v` 6-8 tests make 4-6 LLM calls <90s; without flag skipped
- Asserts schema not exact wording; cost < $0.05 at flash-lite pricing
**Status:** ✅ Complete

### Task 6: Enrich / Workday / SmartExtract / PDF (all 5 extras)
**Files:** `tests/test_enrich_smoke.py` (new), `tests/test_workday_smartextract_live.py` (new), `tests/test_pdf_smoke.py` (new)
**What:** Cheap (default, no marks): `extract_from_json_ld` `detail.py:227` with synthetic `intel` dict (`{"json_ld":[{"@type":"JobPosting","description":"<p>Engineer..."}]}`), `clean_description:488` HTML→text, `resolve_url:58` absolute/relative/`;jsessionid=` stripping, `store_jobs` `database.py:329` dedup, `batch_convert`/`convert_to_pdf` `scoring/pdf.py` text→pdf (skip if `playwright` missing). Live (marked `live`+`expensive`): one `scrape_detail_page` `detail.py:531` against first fixture URL (real HTTP, `sync_playwright`, `headless=True`), assert `tier_used in {1,2,3}`; one Workday site `run_workday_discovery(workers=1)` limited to 1 employer (`config/employers.yaml`) with `max_per_site=1` (may xfail if CAPTCHA); one SmartExtract `build_scrape_targets` `smartextract.py:49` + `_run_one_site` on `Hacker News Jobs` (static, reliable) with `no_cache=True`.
**Acceptance:**
- Default `pytest tests/test_enrich_smoke.py tests/test_pdf_smoke.py -v` 8 tests pass without network/keys
- `pytest -m live --run-live -v` adds 3 live detail/workday/smartextract (xfail allowed on CAPTCHA/403)
**Status:** ✅ Complete

### Task 7: Docs (CONTRIBUTING.md separate live/llm)
**Files:** `CONTRIBUTING.md` (modify)
**What:** Replace `## Running Tests` `CONTRIBUTING.md:82` block with: default quick, live, llm, everything at once. Add `tests/fixtures` note (gitignored, generate via script). Document `gemini-2.0-flash-lite` cost/rate-limit (`LLM_RPM_LIMIT=12` `README.md:133`). Keep no workflow edits per user answer #5. Update `Project Structure` to list `tests/fixtures/`, `scripts/capture_fixtures.py`, `tests/conftest.py` if present.
**Acceptance:**
- `CONTRIBUTING.md` shows exact commands: `pytest tests/ -v`, `pytest -m live --run-live -v`, `pytest -m llm --run-llm -v`, `pytest --run-live --run-llm -v` (or `pytest -m "live or llm or expensive" --run-live --run-llm -v`)
- `python -m pytest tests/ -v` in CI matrix still passes (expensive excluded by default)
**Status:** ✅ Complete

## Implementation Order
```
Task1 (marks/conftest)
        |
        v
Task2 (capture script + gitignore)
        |
        +---> Task3 (JobSpy live)
        +---> Task4 (filtering cheap)
        +---> Task5 (LLM live, gemini-2.0-flash-lite)
        +---> Task6 (enrich/workday/smartextract/pdf)
                |
                v
            Task7 (CONTRIBUTING docs)
```

1. Task 1 → Task 2 → Tasks 3,4,5,6 in parallel (5 after 2) → Task 7 last (docs after code stabilizes).

## Key Design Decisions
1. **Former path + pickle:** `scripts/capture_fixtures.py` uses full `run_discovery` wiring (captures `_site_counts`, `tracker.disabled`, `store_jobspy_results` shape) not just `search_jobs`, then pickles raw objects for fixture independence.
2. **Gitignored fixtures:** `tests/fixtures/.gitignore` ignores `*.pkl/*.json`; unit tests `pytest.skip` with clear message if fixtures missing, prompting `python scripts/capture_fixtures.py --n 1`.
3. **Gemini-2.0-flash-lite default:** `conftest.py` sets `LLM_MODEL=gemini-2.0-flash-lite` and `LLM_DISCOVERY_MODEL=gemini-2.0-flash-lite` for `llm` tests; cost ~5x cheaper than `gemini-3.6-flash` (`llm.py:77`, `README.md:135`).
4. **Separate live/llm:** `live` = network/Playwright (`jobspy`, `workday`, `smartextract`, `detail`), `llm` = Gemini calls (`score`, `tailor`, `cover`). Each also marks `expensive`; running everything is `pytest --run-live --run-llm -v` (or `pytest -m "live or llm or expensive" --run-live --run-llm -v`).
5. **All 5 extras:** Enrich, Workday, SmartExtract, Tailor/Cover, PDF are separate sub-suites (Task 6) not bundled, matching `agents/PLAN_AGENT.md:5` one-task-one-session.
6. **No CI change:** Leave `.github/workflows/ci.yml:28` `pytest tests/ -v` untouched (already excludes expensive via `conftest`); fork has none configured.

## Historical Record
- 2026-08-31: Plan drafted from `AGENTS.md:1`, `pipeline.py:35`, `llm.py:122`, `jobspy.py:130,338,515`, `detail.py:58,227,488,531`, `scorer.py:75`, `tailor.py:663`, `cover_letter.py:120`, `validator.py:99`, `database.py:62,222,329`. User clarified 6 points (all 5 stages, flash-lite, local fixtures, full discover capture, no workflow, separate marks). Plan confirmed by user; queued for build agent.
- 2026-08-31: All 7 tasks complete. Created `tests/conftest.py` (marks + CLI options), `tests/fixtures/` (gitignored), `scripts/capture_fixtures.py`, `tests/test_live_jobspy.py`, `tests/test_filtering_smoke.py`, `tests/test_live_scoring_tailoring_cover.py`, `tests/test_enrich_smoke.py`. Updated `pyproject.toml` (markers), `.gitignore`, `CONTRIBUTING.md` (test commands + project structure).
