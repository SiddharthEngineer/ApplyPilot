# Plan: ZipRecruiter 403 Handling for JobSpy Crawl

**Started:** 2026-08-27
**Status:** ✅ Completed

---

## Goal

The JobSpy crawl repeatedly logs `JobSpy:ZipRecruiter - ZipRecruiter response status code 403 with response: {"error_code":"forbidden aa",...}` on every query×location combination (up to ~46 times per crawl). This is a server-side anti-bot block from ZipRecruiter's Cloudflare/WAF (`forbidden aa` / `forbidden cf-waf`); the installed `python-jobspy` (1.1.82) is already the latest version and upstream issue [JobSpy#302](https://github.com/speedyapply/JobSpy/issues/302) has been open since Sept 2025, so no upgrade or ApplyPilot header tweak can reliably restore the board.

JobSpy swallows the 403 internally (logs it via its own `JobSpy:*` logger and returns 0 ZipRecruiter rows) while other boards still succeed, so the crawl "fails open" but ApplyPilot never learns that ZipRecruiter is dead — it just hammers it on every search and floods the log.

The plan makes the crawl resilient and observable: detect a board that is requested but returns zero results on `N` consecutive searches, stop calling it for the rest of the crawl, report it in crawl stats and in the `run discover` console output, and let the user tune `N` or permanently remove the board via config.

**Follow-up (2026-08-27):** The original plan caps the 403 log at `site_fail_threshold` (default 3), but the init wizard (and thus the user's live `searches.yaml`) emits neither a `sites:` list nor `site_fail_threshold`, so every wizard-generated config silently inherits `sites = ["indeed","linkedin","zip_recruiter"]` and threshold 3. As a result, ZipRecruiter is still called on the first 3 searches of every crawl (up to 3 `JobSpy:ZipRecruiter` 403 log lines per run) and there is no config-level way to opt out without hand-editing. This follow-up makes the wizard and the user's live config exclude the currently-blocked board by default and clarifies the expected auto-skip log behavior.

## Success Criteria

1. During one full crawl where ZipRecruiter returns 0 results, the JobSpy 403 error is emitted at most `site_fail_threshold` times (default 3) instead of once per query×location; once the threshold is hit, ZipRecruiter is excluded from every subsequent `scrape_jobs` call of that crawl.
2. Other boards are unaffected: results from Indeed/LinkedIn/Glassdoor are still scraped and stored to the DB exactly as before (fail-open behavior preserved, no per-site serialization that slows the crawl).
3. `run_discovery()` returns `site_stats` and `disabled_sites` keys in every code path (including the empty-config early return).
4. `applypilot run discover` prints a clear, actionable message when a site is skipped, e.g. "zip_recruiter returned 0 results on 3 consecutive searches — likely blocked; remove it from `sites` in `searches.yaml` to permanently disable". With no disabled sites, no such banner is printed.
5. The threshold is configurable: setting `defaults.site_fail_threshold: 1` disables a board after a single empty result; omitting it defaults to 3.
6. New tests pass: `tests/test_jobspy.py` and `tests/test_pipeline.py` green, full suite `python -m pytest -q` green, `ruff check` clean.
7. A wizard-generated `searches.yaml` contains an explicit `sites:` list (no `zip_recruiter`) and `defaults.site_fail_threshold: 1`.
8. The user's live `~/.applypilot/searches.yaml` has `sites` excluding `zip_recruiter`; a crawl never passes `zip_recruiter` to `scrape_jobs` and logs zero `JobSpy:ZipRecruiter` 403 lines.
9. README states that a board kept in `sites` can still log up to `site_fail_threshold` ERROR lines per crawl (before it is auto-skipped), so removing it from `sites` is the only way to get zero lines.

## Task Chain

### Task 1: Add per-site result counting and the blocked-site tracker

**Files:**
- `src/applypilot/discovery/jobspy.py` (modify)
- `tests/test_jobspy.py` (new)

**What:** Add two pure, dependency-free helpers to `jobspy.py` that will be used to detect boards that are requested but return no jobs. `_site_counts(df, requested_sites)` counts rows grouped by the DataFrame's `site` column for the given site names (jobspy's `Site` enum values are exactly the config names: `indeed`, `linkedin`, `zip_recruiter`, `glassdoor`, `google`); any requested site missing from the DataFrame reports 0. `_SiteTracker` keeps per-crawl state — total counts, request counts, consecutive empty-result counters, and a disabled set — and exposes:

```python
@dataclass
class _SiteTracker:
    """Tracks per-site results across a crawl and disables boards that keep returning 0 jobs."""
    threshold: int = 3  # consecutive 0-result searches before a site is disabled
    counts: dict[str, int] = field(default_factory=dict)          # total rows per site
    requests: dict[str, int] = field(default_factory=dict)        # times each site was requested
    consecutive_empty: dict[str, int] = field(default_factory=dict)
    disabled: set[str] = field(default_factory=set)

    def active_sites(self, sites: list[str]) -> list[str]:
        """Return `sites` with disabled boards removed, preserving order."""
        ...

    def note(self, requested: list[str], counts: dict[str, int]) -> list[str]:
        """Record results for one search; disable boards that reached `threshold`
        consecutive 0-result searches. Returns the list of boards newly disabled."""
        ...

    def report(self) -> dict:
        """Return {"counts": dict, "requests": dict, "disabled": list} for crawl stats."""
        ...
```

`note` increments `requests`, adds to `counts`, bumps `consecutive_empty[site]` for a requested site with 0 rows (resetting to 0 when a site yields ≥1 row), and moves a site to `disabled` the moment its counter reaches `threshold`.

**Acceptance criteria:**
- `_site_counts` returns `{"zip_recruiter": 3, "indeed": 1, "linkedin": 0}` for a DataFrame with 3 `zip_recruiter` + 1 `indeed` rows when all three sites are requested, and returns 0 for a requested site that has no rows at all.
- `_SiteTracker.note` disables a site only after `threshold` consecutive 0-result calls and returns it in the newly-disabled list; a site that produces ≥1 row in between resets its counter and is never disabled.
- `active_sites(["indeed", "linkedin", "zip_recruiter"])` drops disabled entries and preserves the order of the remaining ones.
- `report()` returns keys `counts`, `requests`, `disabled`.
- `python -m pytest tests/test_jobspy.py -q` passes.

**Status:** ✅ Complete

---

### Task 2: Wire the tracker through the crawl and expose per-site stats + disabled_sites

**Files:**
- `src/applypilot/discovery/jobspy.py` (modify)
- `tests/test_jobspy.py` (modify)

**What:** Thread the tracker through `_run_one_search`, `_full_crawl`, and `run_discovery` so a disabled board is dropped from subsequent searches and surfaced in crawl results.

- `_run_one_search` computes `_site_counts(combined_df, sites)` from the concatenated per-search DataFrame and adds a `"sites"` key to its return dict.
- `_full_crawl` creates `tracker = _SiteTracker(threshold=search_cfg.get("defaults", {}).get("site_fail_threshold", 3))`. For each search it computes `active = tracker.active_sites(sites)` and passes `active` to `_run_one_search` instead of the raw `sites` list (the existing Glassdoor-split logic then naturally excludes disabled boards). If `active` is empty, it logs a warning and stops iterating (nothing left to scrape). After each search it calls `tracker.note(active, result["sites"])` and logs a `WARNING` for each newly disabled board, e.g.:
  `"zip_recruiter returned 0 results on 3 consecutive searches — likely blocked. Skipping for the rest of the crawl. Remove it from 'sites' in searches.yaml to permanently disable."`
- `_full_crawl` returns `"disabled_sites": sorted(tracker.disabled)` and `"site_stats": tracker.report()`; `run_discovery` passes both through, and also adds them to its "No search configuration found" early-return dict for dict-shape consistency.

**Acceptance criteria** (integration tests that monkeypatch `applypilot.discovery.jobspy.scrape_jobs` to return controlled DataFrames and point `applypilot.discovery.jobspy.get_connection` at a tmp `init_db` SQLite file):
- With `scrape_jobs` returning only `indeed`/`linkedin` rows across all searches, `run_discovery(cfg)` returns `disabled_sites == ["zip_recruiter"]`, and after the threshold is reached, later `scrape_jobs` calls receive a `site_name` that excludes `zip_recruiter`.
- Boards that yield rows are never disabled.
- `defaults.site_fail_threshold: 1` in the search config disables after a single empty result.
- `run_discovery` returns `site_stats` and `disabled_sites` in all code paths including the empty-config early return.
- `python -m pytest tests/test_jobspy.py -q` and `python -m pytest -q` pass.

**Status:** ✅ Complete

---

### Task 3: Surface disabled/blocked sites in the pipeline discover output

**Files:**
- `src/applypilot/pipeline.py` (modify)
- `tests/test_pipeline.py` (new)

**What:** In `_run_discover`, capture the dict returned by `run_discovery()` instead of discarding it. When `disabled_sites` is non-empty, print a yellow banner via the module's `console`, e.g.:
`[yellow]JobSpy skipped site(s): zip_recruiter (0 results across N searches — likely blocked). Remove from 'sites' in searches.yaml to permanently disable.[/yellow]`
and set `stats["jobspy"]` to `"ok (disabled: zip_recruiter)"` (else keep the current `"ok"`). Preserve the existing try/except and `[red]JobSpy error[/red]` behavior unchanged.

**Acceptance criteria:**
- With `applypilot.pipeline`'s call to `run_discovery` monkeypatched to return `{"disabled_sites": ["zip_recruiter"], "site_stats": {}}`, the discover stage prints a message containing `zip_recruiter` and either `blocked` or `skipped`.
- With no disabled sites, no banner is printed and `stats["jobspy"] == "ok"`.
- `python -m pytest tests/test_pipeline.py -q` passes.

**Status:** ✅ Complete

---

### Task 4: Document the auto-skip behavior and the configurable threshold in the example config

**Files:**
- `src/applypilot/config/searches.example.yaml` (modify)
- `README.md` (modify)

**What:** Add an optional, commented key to the `defaults` block of the example search config:

```yaml
  site_fail_threshold: 3  # Auto-skip a board (e.g. ZipRecruiter) after N consecutive searches return 0 results
```

Add a short note to the README discovery section (around the JobSpy lines, README.md:~124 and ~140) explaining that boards which repeatedly return 0 results are auto-skipped for the rest of a crawl, that a board can be permanently disabled by removing it from the `sites:` list in `searches.yaml`, and that ZipRecruiter is currently subject to a known Cloudflare 403 anti-bot block upstream (`python-jobspy` latest version, upstream issue open, discussed in the plan).

**Acceptance criteria:**
- `yaml.safe_load` on `searches.example.yaml` succeeds and `defaults.site_fail_threshold == 3`.
- README contains a phrase noting boards that repeatedly return 0 results are auto-skipped, and a mention of the ZipRecruiter 403 block.
- `python -m pytest tests/test_config.py -q` and the full suite still pass.

**Status:** ✅ Complete

---

### Task 5: Wizard writes explicit `sites` + `site_fail_threshold`

**Files:**
- `src/applypilot/wizard/init.py` (modify)
- `tests/test_init_wizard.py` (modify)

**What:** In `_setup_searches`, add a `sites:` key (`indeed`, `linkedin`, `glassdoor`, `google` — excludes the currently-blocked `zip_recruiter`) and `site_fail_threshold: 1` to the generated `defaults:` block, preserving the existing manual line-builder and all pre-fill behavior. New users' configs will no longer silently inherit `zip_recruiter` and threshold 3.

**Acceptance criteria:**
- Pumping `mock_ask` through `_setup_searches` produces YAML where `yaml.safe_load` yields `defaults.site_fail_threshold == 1` and a `sites` list not containing `zip_recruiter`.
- Existing `TestSetupSearchesPrefill` tests continue to pass unchanged.
- `python -m pytest tests/test_init_wizard.py -q` passes.

**Status:** ✅ Complete

---

### Task 6: Migrate the user's live search config

**Files:**
- `~/.applypilot/searches.yaml` (modify — user data, outside repo)

**What:** Update the user's real search config to add `sites: [indeed, linkedin, glassdoor, google]` and `defaults.site_fail_threshold: 1`, keeping all existing queries, locations, and accept patterns intact. This eliminates the remaining ZipRecruiter 403 requests for the user's own installs.

**Acceptance criteria:**
- `config.load_search_config()` returns `sites` without `zip_recruiter` and `site_fail_threshold == 1`.
- Simulated crawl (test_jobspy-style monkeypatch of `scrape_jobs`) never receives `zip_recruiter` in `site_name`; `disabled_sites == []`; zero `JobSpy:ZipRecruiter` log lines.
- Optional manual check: a real `applypilot run discover` log contains 0 `JobSpy:ZipRecruiter` matches.

**Status:** ✅ Complete

---

### Task 7: Clarify auto-skip log behavior in docs

**Files:**
- `README.md` (modify)

**What:** Add one line to the README discovery section: a board kept in `sites` can still emit up to `site_fail_threshold` ERROR lines per crawl (before it is auto-skipped), so removing it from `sites` is the only way to get zero such lines.

**Acceptance criteria:**
- README contains the above phrasing.
- Full suite `python -m pytest -q` and `ruff check` still pass.

**Status:** ✅ Complete

---

## Implementation Order

```
Task 1 (Tracker/helper + tests)
        ↓
Task 2 (Crawl wiring + stats + tests)
        ↓
Task 3 (Pipeline banner + tests)   Task 4 (Docs + example yaml)

Task 5 (Wizard sites/threshold)    Task 6 (Live config migration)
        ↓
Task 7 (Docs clarification)
```

Implementation order:
1. Task 1 — add `_site_counts` + `_SiteTracker` and their unit tests.
2. Task 2 — wire the tracker through `_run_one_search`/`_full_crawl`/`run_discovery` and add integration tests.
3. Task 3 — surface disabled sites in `_run_discover` (depends on Task 2's return dict).
4. Task 4 — update `searches.example.yaml` and README (independent; can land with or after Task 3).
5. Task 5 — make the wizard write an explicit `sites` list + `site_fail_threshold: 1`; then Task 6 — migrate the user's live `searches.yaml` (Tasks 5 and 6 are independent; either can run first).
6. Task 7 — clarify the auto-skip log behavior in the README (final; verify full suite + `ruff check` after).

## Key Design Decisions

1. **Detect failures via the DataFrame `site` column, not JobSpy log parsing.** JobSpy swallows the 403 internally and logs it through its own `JobSpy:*` logger with `propagate=False` and a direct stderr handler, so its message text bypasses ApplyPilot's logging and is an unversioned implementation detail. Comparing requested boards against boards present in the returned DataFrame is stable, documented library API.
2. **Disable on "N consecutive 0-result searches" rather than a single empty search.** One query can legitimately return nothing; requiring `site_fail_threshold` (default 3) consecutive empties makes false positives very unlikely while still capping wasted ZipRecruiter calls at 3 per crawl (down from ~46 error lines).
3. **Keep the combined `scrape_jobs` call; do not split per-site.** JobSpy already fans boards out across threads inside one call and fails open (a blocked board yields rows only for the others), so splitting would serialize ~46 searches × 3–5 boards and slow the crawl for no correctness gain. The tracker only filters the requested site list at the crawl level.
4. **Skip blocked boards rather than retry them.** ZipRecruiter's Cloudflare `forbidden aa`/`forbidden cf-waf` 403 blocks JobSpy's hardcoded iOS UA + static tokens; `python-jobspy` 1.1.82 is the latest and upstream issue #302 is unresolved, so no retry or header tweak here can reliably restore the board. `_scrape_with_retry` continues to cover transient 429/timeout/proxy/connection cases for all boards.
5. **Threshold is config-driven via `defaults.site_fail_threshold`** (default 3), following the existing pattern of pulling tunables from `searches.yaml`'s `defaults` block.
6. **Disabled state is crawl-scoped, not persisted.** Each `applypilot run discover` starts fresh, so a board that recovers (block lifted) is automatically retried on the next run with no manual config change.
7. **Wizard and live config exclude `zip_recruiter` rather than lean on the threshold.** Zero requests = zero log noise; `site_fail_threshold: 1` remains as a safety net for any other board that starts returning 0 results.
8. **Keep a positive threshold (1) instead of 0.** A board can't be evaluated without at least one request, so a fully quiet crawl requires removing the board from `sites` (the only way to get zero requests/noise), which the example config, wizard, and docs now make explicit.

## Historical Record

- 2026-08-27 — Plan created. Root cause confirmed as a server-side ZipRecruiter Cloudflare 403 block of JobSpy's requests (upstream issue #302 open, `python-jobspy` 1.1.82 already latest); plan scopes ApplyPilot's fix to detect-and-skip + observability rather than attempting to unblock the board.
- 2026-08-27 — Plan completed. All 4 tasks implemented and verified: `_site_counts`/`_SiteTracker` helpers, tracker wired through `_full_crawl`/`run_discovery`, pipeline yellow banner for disabled sites, `site_fail_threshold` in example config and README. 25 jobspy tests + 4 pipeline tests added, 158 total tests pass.
- 2026-08-27 — Plan reopened. Root cause of lingering 403 logs: the init wizard generates `searches.yaml` without a `sites:` list or `site_fail_threshold`, so configs silently inherit `zip_recruiter` + threshold 3 and still call the blocked board up to 3 times per crawl. Plan extended with Tasks 5–7: wizard writes explicit `sites` (no `zip_recruiter`) + `site_fail_threshold: 1`, migrate the user's live `~/.applypilot/searches.yaml`, and clarify auto-skip log behavior in README.
- 2026-08-28 — Reopened plan fully completed. Task 5 (wizard) and its 3 tests implemented. Task 6: migrated the user's live `~/.applypilot/searches.yaml` — added top-level `sites` (`indeed`, `linkedin`, `glassdoor`, `google`) and `defaults.site_fail_threshold: 1`, preserving all queries/locations; verified `load` returns no `zip_recruiter` and threshold 1. Task 7: added the `site_fail_threshold` ERROR-line clarification to the README discover section. Also fixed a plan-worker false-completion bug: `check_plan_completed()` no longer treats the global STATE.md "No remaining work" phrase as completion (it is shared across plans), relying solely on the plan file's own status line being marked completed.