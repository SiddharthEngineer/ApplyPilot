# Plan: Cap Live Test Scope (Workday 2x2 + Suite Audit)
**Started:** 2026-09-01
**Status:** ✅ Completed

## Goal
`TestWorkdayLive::test_workday_smoke` hits real Workday CXS for exactly 2 employers × 2 queries (`max_results=5`, 1 page each, `workers=1`) via a real `run_workday_discovery(...)` signature with no `monkeypatch`, finishes `<30s` in `tmp_path` isolation, and all other `@live`/`@llm` suites are audited to stay `n=1` / 1-URL / 1-site with zero `monkeypatch` in live classes.

## Success Criteria
1. `timeout 45 pytest tests/test_enrich_smoke.py::TestWorkdayLive::test_workday_smoke --run-live -v --durations=0` completes `<30s`, log shows `Workday crawl: 2 queries x 2 employers`, `workday_search` called ≤4 times (1 page each, `max_results=5`, `page_size=20` at `src/applypilot/discovery/workday.py:209`), `fetch_details` ≤10 GETs, `tmp_path/applypilot.db` exists and `~/.applypilot/applypilot.db` mtime unchanged.
2. `timeout 120 pytest -m live --run-live -v --durations=0` total `<120s`; `rg -n "monkeypatch" tests/test_enrich_smoke.py tests/test_live_jobspy.py tests/test_live_scoring_tailoring_cover.py` shows zero hits inside any `@pytest.mark.live` or `@pytest.mark.llm` class (only `conftest.py` helpers allowed).
3. `pytest tests/ -v` without flags still skips all `live`/`expensive`/`llm` via `tests/conftest.py:33` (`SKIPPED` reason `need --run-live`).
4. `ruff check src/applypilot/discovery/workday.py tests/test_enrich_smoke.py tests/test_live_jobspy.py tests/test_live_scoring_tailoring_cover.py` clean and `pytest tests/test_enrich_smoke.py::TestEnrichmentCheap -v` 9/9 pass.

## Task Chain
### Task 1: Extend Workday API for bounded runs (no monkeypatch)
**Files:** `src/applypilot/discovery/workday.py` (modify)
**What:** Extend `run_workday_discovery` at `workday.py:478` from `def run_workday_discovery(employers=None, workers=1)` to `def run_workday_discovery(employers=None, workers=1, employer_keys=None, queries=None, max_queries=0, max_results=0, db_path=None)`; when `queries` is not `None` use it verbatim else load from `config.load_search_config()` at `workday.py:499` and slice `queries[:max_queries]` if `max_queries>0`; when `employer_keys` is not `None` filter `employers` dict before loop at `workday.py:521`; thread `max_results` through `scrape_employers:391` → `_process_one:349` → `search_employer:195` (already has `max_results` + `max_pages=25` at `workday.py:210`) and cap `fetch_details:282` to `jobs[:max_results]`; thread `db_path` to `init_db(db_path)` at `workday.py:415` and `get_connection(db_path)` at `workday.py:381` / `store_results:309` so callers can pass `tmp_path/"applypilot.db"` without touching `config.py:10` `APP_DIR`. Keep defaults `None/0` for backward compat with `src/applypilot/pipeline.py:91`.
**Acceptance:**
- `rg -n "def run_workday_discovery" src/applypilot/discovery/workday.py` shows new signature with `employer_keys, queries, max_queries, max_results, db_path`
- `python -c "import inspect; from applypilot.discovery.workday import run_workday_discovery; print(inspect.signature(run_workday_discovery))"` lists the five new params
- `ruff check src/applypilot/discovery/workday.py` clean (pre-existing only)
**Status:** ✅ Complete

### Task 2: Cap `TestWorkdayLive` to 2 employers × 2 queries (real network, tmp isolation)
**Files:** `tests/test_enrich_smoke.py` (modify)
**What:** Rewrite `TestWorkdayLive::test_workday_smoke` at `test_enrich_smoke.py:184-200` to call the new bounded API without `monkeypatch`: `run_workday_discovery(employer_keys=["nvidia","salesforce"], queries=["software engineer","backend engineer"], max_results=5, workers=1, db_path=tmp_path/"applypilot.db")` using pinned employers `nvidia` (`nvidia.wd5.myworkdayjobs.com` at `config/employers.yaml:164`) and `salesforce` (`salesforce.wd12.myworkdayjobs.com` at `config/employers.yaml:158`); remove `monkeypatch` arg and `(tmp_path/"tailored_resumes").mkdir` scaffolding; add `@pytest.mark.timeout(30)` (or `pytest.fail` guard) and assert `result["queries"]==2` and `result["found"]>=0`; verify `tmp_path/"applypilot.db"` exists and no write to `~/.applypilot`.
**Acceptance:**
- `grep -n "monkeypatch" tests/test_enrich_smoke.py` zero hits in `TestWorkdayLive` class
- `timeout 45 pytest tests/test_enrich_smoke.py::TestWorkdayLive::test_workday_smoke --run-live -v --durations=0` passes or `xfail` on CAPTCHA only, `<30s`, `tmp_path/applypilot.db` exists, `ls -la ~/.applypilot/applypilot.db` mtime unchanged after run
- `rg -n "employer_keys.*nvidia.*salesforce" tests/test_enrich_smoke.py` matches new call
**Status:** ✅ Complete

### Task 3: Audit and remove monkeypatch from remaining live/llm suites
**Files:** `tests/test_enrich_smoke.py` (modify), `tests/test_live_jobspy.py` (modify), `tests/test_live_scoring_tailoring_cover.py` (modify), `tests/conftest.py` (modify)
**What:** Audit `TestDetailPageLive` at `test_enrich_smoke.py:158` (already 1 URL `scrape_detail_page(page, "https://example.com/jobs/1")` at `test_enrich_smoke.py:170`, `collect_detail_intelligence:208`, `extract_from_json_ld:227`) and `TestSmartExtractLive` at `test_enrich_smoke.py:205` (already `build_scrape_targets(sites=[{"name":"Hacker News Jobs",...}], search_cfg={"queries":[{"query":"engineer","tier":1}]})` → `_run_one_site:1058` single target, `smartextract.py:49`); remove `monkeypatch` arg from both (currently `def test_...(tmp_path, monkeypatch)` at `test_enrich_smoke.py:161,208` but unused), add `assert len(targets)==1` at `test_enrich_smoke.py:220` and `@pytest.mark.timeout(30)`; replace `tests/test_live_jobspy.py:14` `_isolated_db` fixture's `monkeypatch.setenv("APPLYPILOT_DIR", str(tmp_path))` with direct `init_db(tmp_path/"applypilot.db")` (already at `test_live_jobspy.py:21`) — drop `APPLYPILOT_DIR` env patch, keep `results_per_site=1` at `test_live_jobspy.py:48` parametrized over 5 sites; for `tests/test_live_scoring_tailoring_cover.py:18` `_setup_llm` `monkeypatch.setenv("LLM_MODEL","gemini-2.0-flash-lite")` at `test_live_scoring_tailoring_cover.py:29` replace with per-call model arg or keep as sole documented exception — prefer `get_client(model="gemini-2.0-flash-lite")` / `get_discovery_client` at `src/applypilot/llm.py:88` if available else leave env patch with comment `# live/llm env exception — model pin`; add guard in `tests/conftest.py` (e.g., `pytest_collection_modifyitems` adds `timeout` marker to `@live` or `pytest_runtest_setup` fails if `monkeypatch` in `item.fixturenames` for `@live`).
**Acceptance:**
- `rg "monkeypatch" tests/test_enrich_smoke.py tests/test_live_jobspy.py` zero for `@live` classes; `rg "def test_smartextract_hackernews\(self, tmp_path"` shows no `monkeypatch` param
- `pytest tests/test_enrich_smoke.py::TestDetailPageLive --run-live -v --durations=0` and `pytest tests/test_enrich_smoke.py::TestSmartExtractLive --run-live -v --durations=0` each `<30s` and `len(targets)==1` assert passes
- `timeout 120 pytest -m live --run-live -v --durations=0` total `<120s`, `ruff` clean
**Status:** ✅ Complete

## Implementation Order
```
Task 1 (workday.py API extension)
        |
        v
Task 2 (TestWorkdayLive 2x2, no patch)
        |
        v
Task 3 (audit remaining lives, remove patch, guards)
```
1. Task 1 → Task 2 → Task 3 (Task 2 depends on Task 1's new signature; Task 3 depends on Task 2's pattern for no-monkeypatch isolation).

## Key Design Decisions
1. Pin `nvidia` + `salesforce` (`wd5` + `wd12` shards) — covers two Workday shards with stable tech hiring and low CAPTCHA vs random 49-employer set at `config/employers.yaml:5`.
2. Extend `run_workday_discovery` with `employer_keys`/`queries`/`max_queries`/`max_results`/`db_path` — bounds `workday.py:527` `for query in queries` loop and `scrape_employers:391` per-employer pagination at `workday.py:210` without `monkeypatch.setattr(config.load_search_config, ...)`.
3. Thread `db_path` to `get_connection(db_path)`/`init_db(db_path)` instead of fixing `config.py:10` `APP_DIR = Path(os.environ.get("APPLYPILOT_DIR"))` import-time capture — avoids global `APP_DIR` laziness change and keeps `src/applypilot/pipeline.py:91` backward compat.
4. Keep live tests real (`workday_search:162` `timeout=30` / `workday_detail:181` over `urllib`) — cap via `max_results=5` and single page (`page_size=20`) not mocks, per user request for actual 1-2 searches.
5. Zero `monkeypatch` in `@live`/`@llm` — live tests pass explicit `employer_keys`/`queries`/`db_path` rather than `monkeypatch.setenv("APPLYPILOT_DIR")` which is broken by `config.py:10` import-time eval; `conftest` guard prevents future regression.

## Historical Record
- 2026-09-01: Plan drafted after diagnosing `test_workday_smoke` at `tests/test_enrich_smoke.py:197` `run_workday_discovery(workers=1)` unbounded `49×13=637` searches each `timeout=30` at `workday.py:177` plus sequential `fetch_details:282` with `workers=1`. Audited other lives: `test_live_jobspy.py:48` capped (`results_per_site=1`), `TestSmartExtractLive:212` capped (1 site `Hacker News Jobs`), `TestDetailPageLive:170` capped (1 URL), `test_live_scoring_tailoring_cover.py:107` capped (2 jobs `max_retries=1`). User confirmed 2 employers + API extension + no monkeypatch for live/llm.
