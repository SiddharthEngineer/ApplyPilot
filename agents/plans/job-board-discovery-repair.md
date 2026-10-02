# Plan: Job Board Discovery Repair
**Started:** 2026-10-01
**Status:** 🔄 In Progress

## Goal
Make discovery reliable and honest about what works. Evidence gathered 2026-10-01:

| Source | Live probe (3 results, "Data Scientist", Remote) | DB rows ever |
|---|---|---|
| Indeed | ✅ 3 rows, 0.9s | 407 |
| LinkedIn | ✅ 3 rows, 0.6s | 249 |
| Glassdoor | ❌ HTTP 403 (Cloudflare) + "location not parsed" for `Remote` | 0 |
| Google Jobs | ❌ 0 rows, even with `google_search_term` | 0 |
| ZipRecruiter | ❌ HTTP 403 `forbidden aa` (Cloudflare) | 0 |
| Workday API (48 employers) | n/a | 1195 ✅ |
| SmartExtract (30 direct sites) | n/a | **0 ever** |

`python-jobspy` 1.1.82 is already the latest release, so upgrading won't help. Indeed and LinkedIn
work in isolation, but the user's `~/.applypilot/searches.yaml` sets `site_fail_threshold: 1`, so one
empty or throttled search disables a board for the rest of the crawl. Also, all non-Glassdoor boards
share a single `scrape_jobs()` call, and JobSpy only logs per-board 403s instead of raising them, so
the tracker can't tell "blocked" apart from "no jobs". The live test xfails every board on 0 results,
so the suite can never catch a regression. After this plan, Indeed and LinkedIn run reliably on
every crawl, blocked boards are gated off by default with a clear reason, a probe command reports
board health in seconds, and new block-free sources (Greenhouse, Lever, Ashby public JSON APIs) replace the lost coverage.

## Success Criteria
1. `applypilot discover --probe` prints one line per configured source with status `ok|empty|blocked|error`, row count, and latency, and finishes in under 60s.
2. A full `applypilot run discover` with default config stores ≥1 new job from both Indeed and LinkedIn (verified via `sqlite3 ~/.applypilot/applypilot.db "select site,count(*) from jobs where discovered_at > '<run start>' group by 1"`).
3. Glassdoor, ZipRecruiter, and Google are skipped by default with a single log line that names the reason. They're re-enabled only when `PROXY` is set (Glassdoor/Zip) or the user opts in explicitly.
4. `applypilot run discover` stores ≥1 job from at least one Greenhouse/Lever/Ashby board listed in `config/ats_boards.yaml`.
5. SmartExtract either stores jobs from ≥5 of the 30 sites or has each failing site annotated with a root cause in `config/sites.yaml`.
6. `pytest tests/ -v` passes, and `pytest -m live --run-live tests/test_live_jobspy.py` **fails** (not xfails) if Indeed or LinkedIn return 0.

## Task Chain

### Task 0: Evaluate python-jobspy 1.2.0
**Runs:** local (needs live board access)
**Files:** `pyproject.toml` / README install line (modify, only if adopted), `scripts/cloud_setup.sh` (modify, only if adopted)
**What:** `python-jobspy` 1.2.0 was released after the 2026-10-01 audit, which found 1.1.82 to be the latest. It adds a
`curl_cffi` dependency (browser TLS impersonation), which may get past the Cloudflare 403s on Glassdoor and ZipRecruiter.
In a scratch venv, install 1.2.0 + `curl_cffi`, rerun the 2026-10-01 probe (3 results per board, "Data Scientist", Remote)
for all five boards, and compare against the table in Goal. If it's at least as good on Indeed/LinkedIn, adopt it: bump the pin in
`scripts/cloud_setup.sh` (and add `curl_cffi`), update the README install line, and rerun `pytest tests/ -q`.
Record the probe table in Historical Record either way. If Glassdoor/Zip now work, Task 3's proxy gating changes from default-off to default-on.
**Acceptance:**
- Probe results for 1.2.0 recorded in Historical Record with the date.
- If adopted: `pytest tests/ -q` passes on 1.2.0 locally.
**Status:** ❌ Not started

### Task 1: Board probe command
**Runs:** cloud (code + unit tests); live `--probe` check: local
**Files:** `src/applypilot/discovery/jobspy.py` (modify), `src/applypilot/cli.py` (modify), `tests/test_jobspy.py` (modify)
**What:** Add `probe_boards(sites: list[str], query: str = "Software Engineer", location: str = "Remote") -> list[BoardHealth]`
with `@dataclass class BoardHealth: site: str; status: str; rows: int; latency_s: float; detail: str`.
Call `scrape_jobs(site_name=[site], results_wanted=3, …)` **once per site**. Attach a temporary
`logging.Handler` to the `JobSpy` logger to capture lines with `status code: 403`/`400`/`429`,
and map them to `blocked` with the message in `detail`. Exceptions map to `error`, and 0 rows with
no captured error maps to `empty`. No DB writes. Add `--probe` to the `discover` CLI path, which
prints a Rich table and exits.
**Acceptance:**
- `pytest tests/test_jobspy.py -k probe -v` passes, with `scrape_jobs` patched and the 403 log line simulated.
- `applypilot discover --probe` runs live and shows indeed/linkedin `ok` and zip_recruiter `blocked`.
**Status:** ❌ Not started

### Task 2: One scrape call per board + blocked-vs-empty tracking
**Runs:** cloud
**Files:** `src/applypilot/discovery/jobspy.py` (modify), `tests/test_jobspy.py` (modify)
**What:** In `_run_one_search()`, replace the combined `other_sites` call with one
`_scrape_with_retry` call per site (Glassdoor keeps its simplified location). Reuse the log-capture
handler from Task 1 so each site's result carries `blocked: bool`. Change `_SiteTracker.note()` to
accept `blocked: set[str]`: a blocked site is disabled immediately with reason `"blocked (HTTP 403)"`,
and an empty site follows the threshold rule. Store reasons in `_SiteTracker.reasons: dict[str, str]`
and include them in `report()`.
**Acceptance:**
- New tests: a blocked site is disabled after 1 search, an empty site only after `threshold` searches, and one site's exception doesn't zero the other sites' counts.
- Existing `tests/test_jobspy.py` still passes.
**Status:** ❌ Not started

### Task 3: Safe defaults + migrate user config
**Runs:** cloud (code + tests), then local (edit `~/.applypilot/searches.yaml`, live discover run)
**Files:** `src/applypilot/wizard/init.py` (modify), `src/applypilot/config/searches.example.yaml` (modify), `src/applypilot/discovery/jobspy.py` (modify), `~/.applypilot/searches.yaml` (modify, user data)
**What:** Add a module constant `_REQUIRES_PROXY = {"glassdoor", "zip_recruiter"}` and `_UNSUPPORTED = {"google"}`.
In `run_discovery()`, drop those sites unless `PROXY` is set (proxy sites) or `defaults.allow_unsupported: true`
(google), logging one warning per dropped site. Clamp `site_fail_threshold` to a minimum of 2,
with a warning if the config says 1. Add `defaults.linkedin_fetch_description` (default `false`).
Full descriptions are already fetched by the enrich stage, and the per-job LinkedIn fetch is what
triggers throttling at `results_per_site: 50`. Edit the user's `~/.applypilot/searches.yaml`: set
`site_fail_threshold: 3` and keep `sites` as-is so the new gating is visible in logs.
**Acceptance:**
- `grep -n "site_fail_threshold: 3" ~/.applypilot/searches.yaml` matches.
- Unit test: with no `PROXY`, `run_discovery` calls the crawl with `sites == ["indeed", "linkedin"]` for config `[indeed, linkedin, glassdoor, google, zip_recruiter]`.
- Success Criterion 2 verified with a live run.
**Status:** ❌ Not started

### Task 4: Greenhouse / Lever / Ashby source module
**Runs:** cloud (module + tests using hand-written samples shaped like each ATS's documented JSON); local (verify and seed `ats_boards.yaml` slugs, which needs network)
**Files:** `src/applypilot/discovery/ats_boards.py` (new), `src/applypilot/config/ats_boards.yaml` (new), `tests/test_ats_boards.py` (new)
**What:** These ATSes expose public, unauthenticated JSON job lists with no anti-bot protection:
Greenhouse `https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true`, Lever
`https://api.lever.co/v0/postings/{company}?mode=json`, and Ashby
`https://api.ashbyhq.com/posting-api/job-board/{org}?includeCompensation=true`. Implement
`fetch_board(kind: str, slug: str) -> list[dict]`, which normalizes each response to the `jobs` table
columns (`url, title, location, description/full_description, site=<company>, strategy="ats_<kind>"`).
Then implement `run_ats_discovery(queries: list[str], accept_locs, reject_locs) -> dict`, which filters
titles by case-insensitive query-term match and reuses `_location_ok` from `jobspy.py`. Seed
`ats_boards.yaml` with ~20 companies relevant to the user's queries (data/ML/infra employers), and
verify each slug returns HTTP 200 before adding it. Use `httpx` (already a dependency).
**Acceptance:**
- `pytest tests/test_ats_boards.py -v` passes using recorded JSON samples (one per ATS) in the test file.
- `python -c "from applypilot.discovery.ats_boards import fetch_board; print(len(fetch_board('greenhouse','<slug>')))"` prints >0 live.
**Status:** ❌ Not started

### Task 5: Wire ATS source into pipeline + probe
**Runs:** cloud; live check: local
**Files:** `src/applypilot/pipeline.py` (modify), `src/applypilot/discovery/jobspy.py` (modify), `tests/test_pipeline.py` (modify)
**What:** In the discover stage in `pipeline.py` (where `stats["jobspy"]`, `stats["workday"]`, and
`stats["smartextract"]` are set), add a `stats["ats"]` block that calls `run_ats_discovery` with the
search config's queries and location lists, using the same error-isolation pattern. Make
`probe_boards` also report one `ats:<kind>` row per ATS kind (first board in the yaml).
Patch `run_ats_discovery` in `tests/test_pipeline.py` wherever `_run_discover` is called, so the suite stays hermetic (test-tiers-and-qc Task 2 enforces this).
**Acceptance:**
- `pytest tests/test_pipeline.py -v` passes in under 10s, with a test asserting the `ats` key in discover stats.
- Success Criterion 4 verified with a live run.
**Status:** ❌ Not started

### Task 6: Diagnose SmartExtract zero output
**Runs:** local (needs network + Gemini)
**Files:** `src/applypilot/discovery/smartextract.py` (modify), `src/applypilot/config/sites.yaml` (modify)
**What:** SmartExtract has never stored a job (`select count(*) from jobs where strategy not in ('jobspy','workday_api')` = 0).
Run `applypilot discover` restricted to SmartExtract with `-v` against 5 sites from `sites.yaml`
and log, per site, the stage reached: page load → API capture → judge verdict → extraction → store.
Fix the first systemic cause found (for example, a judge LLM failure or filter rejecting everything).
For sites that are individually broken (dead URL, CAPTCHA), add `disabled: true` with a
`disabled_reason:` comment in `sites.yaml`, and have `load_sites()` skip disabled entries. Record the root cause in this plan's Historical Record.
**Acceptance:**
- Success Criterion 5.
- Root cause written in Historical Record.
**Status:** ❌ Not started

### Task 7: Live tests that actually catch regressions + docs
**Runs:** cloud; live test run: local
**Files:** `tests/test_live_jobspy.py` (modify), `README.md` (modify)
**What:** In `test_jobspy_single_site`, remove `indeed` and `linkedin` from the xfail tuple so 0 results
fails. Turn glassdoor/zip_recruiter/google into `pytest.mark.skip(reason="blocked without proxy / unsupported")`
unless `PROXY` is set. Update the README Pipeline table ("Scrapes 5 job boards") to list the real
sources: Indeed, LinkedIn, Workday, Greenhouse/Lever/Ashby, and optional proxied Glassdoor/ZipRecruiter.
Document `applypilot discover --probe`.
**Acceptance:**
- `pytest -m live --run-live tests/test_live_jobspy.py -v` shows indeed/linkedin PASSED and the others SKIPPED.
- `grep -n "\-\-probe" README.md` matches.
**Status:** ❌ Not started

## Implementation Order
```
Task 1 ──► Task 2 ──► Task 3 ──► Task 7
Task 4 ──► Task 5 ──┘
Task 0 (local, independent; may change Task 3 defaults)
Task 6 (independent)
```
1. Task 1: probe (diagnostic tool used by every later task)
2. Task 2: per-board calls + blocked detection
3. Task 3: defaults + user config migration
4. Task 4: ATS module
5. Task 5: ATS wiring
6. Task 6: SmartExtract diagnosis
7. Task 7: live tests + docs

## Key Design Decisions
1. Gate Glassdoor/ZipRecruiter on `PROXY` instead of trying to beat Cloudflare in-repo. The 403 happens at JobSpy's HTTP layer, and only a residential proxy reliably avoids it, which is a paid decision for the user.
2. Google is marked unsupported because JobSpy's Google scraper returns 0 rows even with `google_search_term`, so it's outside this repo's control.
3. Add Greenhouse/Lever/Ashby because they are public JSON APIs designed for job boards, with no bot detection, and many data/ML employers use them.
4. Make one `scrape_jobs` call per board so per-board errors can be attributed. JobSpy logs 403s per board instead of raising them.
5. Turn off `linkedin_fetch_description` by default because enrich already fetches full descriptions, and the per-job LinkedIn fetch is the main throttling trigger.

## Historical Record
- 2026-10-01: Plan created from live probe evidence (roadmap initiative R2).
