# Plan: Discover Crawl Resilience — Site Blocking, Country Validation & Early-Stop Fixes

**Started:** 2026-08-28
**Status:** ✅ Completed

---

## Goal

Make `applypilot run discover` (`pipeline.py:62-109` -> `discovery/jobspy.py:539-575`) resilient to transient 0-result boards, invalid `country_indeed`, and intermittent Glassdoor/Google blocks without aborting the crawl early. User sees: no `Invalid country` crash, no premature `All sites disabled` at 5/6, yellow banner only for boards that truly fail `N` consecutive searches.

## Success Criteria

1. `run_discovery({"defaults":{"country_indeed":"sri lanka"}})` does not raise `Invalid country string`; logs a `WARNING` with fallback (e.g. `usa`/`worldwide`) and completes crawl; `python -m pytest tests/test_jobspy.py -k country -q` passes.
2. With `site_fail_threshold: 3` (config + wizard), a board returning 0 on 1-2 consecutive searches is NOT disabled; disabled after 3 consecutive empties only; `tests/test_jobspy.py::TestSiteTracker` passes with threshold 3.
3. `applypilot run discover` with 6 queries × 2 sites where one search returns 0 does not emit `All sites disabled — stopping crawl early` nor disable `indeed`/`linkedin`; `progress 6/6` logged.
4. Glassdoor 403 (`jobspy.py:257`) and Google 0-result do not disable after 1 search; threshold 3 applies; or they are excluded from wizard defaults with doc explaining opt-in.
5. Wizard `_setup_searches` generates `sites: [indeed, linkedin, ...]` and `defaults.site_fail_threshold: 3` (no `zip_recruiter`); `yaml.safe_load` on generated YAML yields expected keys; `tests/test_init_wizard.py` passes.
6. `config/searches.example.yaml` `defaults.site_fail_threshold: 3` and README documents blocking/country behavior; `ruff check` and `python -m pytest -q` green.

## Task Chain

### Task 1: Normalize & validate `country_indeed` before JobSpy call

**Files:**
- `src/applypilot/discovery/jobspy.py` (modify)
- `tests/test_jobspy.py` (modify)

**What:** Add a robust `_normalize_country(raw: str | None) -> str` helper that validates against JobSpy's supported country allowlist (`usa`, `uk`, `canada`, `australia`, `germany`, `india`, `france`, `spain`, `italy`, `brazil`, `mexico`, `netherlands`, `switzerland`, `sweden`, `norway`, `denmark`, `finland`, `ireland`, `new zealand`, `singapore`, `south africa`, `poland`, `portugal`, `belgium`, `austria`, `argentina`, `chile`, `colombia`, `peru`, `japan`, `south korea`, `taiwan`, `hong kong`, `malaysia`, `indonesia`, `philippines`, `thailand`, `vietnam`, `turkey`, `united arab emirates`, `saudi arabia`, `israel`, `egypt`, `nigeria`, `pakistan`, `bangladesh`, `romania`, `czech republic`, `hungary`, `greece`, `ukraine`, `worldwide`). If unrecognized (e.g. `"sri lanka"`), logs a warning and falls back to `"usa"` (or `"worldwide"`). Call it in `_run_one_search` and `search_jobs`.

**Acceptance criteria:**
- `_normalize_country("sri lanka")` logs a warning and returns `"usa"` (or fallback).
- `_normalize_country("UK")` returns `"uk"`.
- `_normalize_country(None)` returns `"usa"`.
- `run_discovery` with an invalid `country_indeed` in `defaults` runs successfully without raising `ValueError`.
- `python -m pytest tests/test_jobspy.py -q` passes.

**Status:** ✅ Complete

---

### Task 2: Revert wizard default site_fail_threshold to 3 and clarify sites

**Files:**
- `src/applypilot/wizard/init.py` (modify)
- `tests/test_init_wizard.py` (modify)

**What:** Change `wizard/init.py:475` from `site_fail_threshold: 1` to `site_fail_threshold: 3` to match `searches.example.yaml` and prevent over-aggressive premature disabling on transient 0-result searches. Keep explicit `sites: [indeed, linkedin, glassdoor, google]` (no `zip_recruiter`).

**Acceptance criteria:**
- Wizard-generated `searches.yaml` has `defaults.site_fail_threshold: 3` and `sites` without `zip_recruiter`.
- `python -m pytest tests/test_init_wizard.py -q` passes.

**Status:** ✅ Complete

---

### Task 3: Exclude hard scrape errors from consecutive empty counting

**Files:**
- `src/applypilot/discovery/jobspy.py` (modify)
- `tests/test_jobspy.py` (modify)

**What:** In `_full_crawl`, if `result["errors"] > 0` (meaning `_run_one_search` caught an exception like connection failure or country error and returned `errors: 1`), do not pass its per-site counts to `tracker.note()` or skip calling `tracker.note` for that search so hard failures don't falsely increment `consecutive_empty` and trigger board blacklisting.

**Acceptance criteria:**
- When `_run_one_search` encounters an error, the error count is incremented but active site consecutive empty counters are not incremented.
- `python -m pytest tests/test_jobspy.py -q` passes.

**Status:** ✅ Complete

---

### Task 4: Document JobSpy board flakiness and auto-skip behavior in README

**Files:**
- `README.md` (modify)

**What:** Update README.md discovery section to explain that Glassdoor and Google can occasionally return 0 results or 403 blocks, and ApplyPilot's tracker auto-skips them after `site_fail_threshold` consecutive empty searches without failing the crawl.

**Acceptance criteria:**
- README contains notes on board flakiness and auto-skip.
- `ruff check` and `python -m pytest -q` pass.

**Status:** ✅ Complete

---

## Implementation Order

```
Task 1 (Country normalize + test) → Task 3 (Error vs empty tracker + test)
                                    ↓
Task 2 (Wizard threshold 3 + test) → Task 4 (README docs)
```

1. Task 1 — `_normalize_country` and unit test.
2. Task 3 — `_full_crawl` error handling for tracker and unit test.
3. Task 2 — Wizard threshold fix and test update.
4. Task 4 — README documentation.

## Key Design Decisions

1. **Normalize country at ApplyPilot boundary** — JobSpy allowlist is unversioned; fallback to `"usa"` keeps crawls fail-open.
2. **Revert threshold to 3** — Wizard threshold `1` caused collateral disable when Glassdoor/Google returned transient 0 results; `3` matches example config and balances resilience.
3. **Hard errors don't count as empty searches** — Network exceptions or invalid configs should not penalize job boards' availability.

## Historical Record

- 2026-08-28 — Plan created to fix invalid country validation crashes and over-aggressive site disabling during job discovery crawl.
- 2026-08-28 — All 4 tasks completed: `_normalize_country` helper, error-excluded tracker, wizard threshold 3, README docs.
