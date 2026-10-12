# Plan: Test Tiers + Morning QC
**Started:** 2026-10-01
**Status:** ✅ Complete (2026-10-12)
**Risk:** low (test data and docs only)

## Goal
Plans are implemented by unattended agents and reviewed by the user afterwards. That only works if the automated gate is trustworthy. Today:
- `pytest tests/` **hangs for 15+ minutes**. `tests/test_pipeline.py` calls `_run_discover()` with only JobSpy mocked, so it runs the real Workday crawl (48 employers) and SmartExtract over the network. Without that file the suite takes 16s (366 passed, 15 skipped).
- Nothing stops a unit test from touching the network or the real `~/.applypilot` directory.
- `.github/workflows/ci.yml` only runs on `workflow_dispatch` and doesn't install `python-jobspy`, so it can't pass.
- Recorded fixtures (`tests/fixtures/`) are gitignored, so cloud runs can't use them.
- Live and LLM checks have no single entry point for the morning review.

After this plan there are three tiers:

| Tier | Command | Where |
|---|---|---|
| Unit (hermetic) | `pytest tests/` | Nightly cloud routine + GitHub Actions on every PR |
| Recorded responses | part of `pytest tests/` (committed data in `tests/data/`) | Same |
| Live + LLM | `python scripts/qc.py <plan-slug>` | User's machine, during morning review |

## Success Criteria
1. `pytest tests/ -q` completes in under 3 minutes with no network access (`time pytest tests/ -q`).
2. A unit test that opens a socket to a non-localhost address fails with a clear `NetworkAccessBlocked` error, and a `@pytest.mark.live` test doesn't (`pytest tests/test_hermetic.py -v`).
3. No unit test reads or writes the real `~/.applypilot` (`tests/test_hermetic.py::test_app_dir_isolated`).
4. GitHub Actions runs lint + unit tests on every pull request to `trunk` and passes on a no-op PR.
5. `python scripts/qc.py job-board-discovery-repair` runs the live and LLM tiers and prints that plan's Success Criteria as a checklist.

## Task Chain

### Task 1: Fix network-bound tests in test_pipeline.py
**Runs:** cloud
**Files:** `tests/test_pipeline.py` (modify)
**What:** In every test that calls `_run_discover()`, also patch
`applypilot.discovery.workday.run_workday_discovery` and `applypilot.discovery.smartextract.run_smart_extract`
with `unittest.mock.patch` returning `None`, alongside the existing JobSpy patch. That covers
`test_banner_printed_when_sites_disabled` and `test_stats_show_disabled_when_sites_disabled`. Patch at
the import location `_run_discover` uses (it imports inside the function, so patch the source module attribute).
**Acceptance:**
- `time pytest tests/test_pipeline.py -q` passes in under 10s.
- `time pytest tests/ -q` passes in under 3 minutes.
**Status:** ✅ Complete (2026-10-02)

### Task 2: Hermetic guard (network + app dir)
**Runs:** cloud
**Files:** `tests/conftest.py` (modify), `tests/test_hermetic.py` (new)
**What:** Add an autouse fixture. For items **not** marked `live` or `llm`, it patches `socket.socket.connect`
to raise `NetworkAccessBlocked(RuntimeError)` for any address other than `127.0.0.1`, `::1`, `localhost`, or
`AF_UNIX`. Use `unittest.mock.patch.object`, since `pytest_runtest_setup` forbids `monkeypatch` only on live/llm
tests. Separately, in `pytest_configure`, set `APPLYPILOT_DIR` to a session temp dir **before**
`applypilot.config` is imported (`APP_DIR` is computed at import time in `src/applypilot/config.py:10`).
Fix any test that then fails because it depended on network or real user files: add the missing mock, or mark it `live`.
Known cases (found 2026-10-02 by running the suite with an empty `HOME`):
`tests/test_content_library_e2e.py::TestRunTailoringContentLibrary::test_content_library_not_found` (`sqlite3.OperationalError: unable to open database file`)
and `tests/test_init_wizard.py::TestSetupTraditionalResume::test_pdf_file_copied` (`FileNotFoundError: ~/.applypilot/resume.txt`).
**Acceptance:**
- Success Criteria 2 and 3.
- `HOME=$(mktemp -d) pytest tests/ -q` passes (simulates a cloud session with no user data).
**Status:** ✅ Complete (2026-10-02)

### Task 3: CI on pull requests
**Runs:** cloud
**Files:** `.github/workflows/ci.yml` (modify)
**What:** Trigger on `pull_request` (branches: `trunk`) and `push` (branches: `trunk`), keeping
`workflow_dispatch`. Install with `pip install -e ".[dev]" && pip install python-jobspy --no-deps` plus
jobspy's runtime deps (mirror the README "Local Install" step), or switch to `uv` as the README does.
Run `ruff check src/` and `pytest tests/ -q`. Drop the matrix to `3.13` only to save minutes. Use the
version the local `.venv` reports.
**Acceptance:**
- Success Criterion 4: the workflow run on the PR that adds this change is green.
**Status:** ✅ Complete (2026-10-02)

### Task 4: Committed recorded-response data (synthetic samples)
**Runs:** unattended
**Files:** tests/data/jobspy_indeed.json (new), tests/data/jobspy_linkedin.json (new), tests/data/workday_search.json (new),
tests/data/gemini_score_response.json (new), tests/data/README.md (new), tests/test_recorded_data.py (modify: module docstring only)
**What:** The capture code from 2026-10-02 stays, but the committed samples are now **hand-written synthetic data** (user decision,
2026-10-12) instead of a live capture (old M6). No network, no `~/.applypilot`, no personal data. Write the four files so that the
replay tests in `tests/test_recorded_data.py` run instead of skipping:
- `jobspy_indeed.json`, `jobspy_linkedin.json`: a JSON list of 5 rows each, shaped like `scrape_jobs(...).to_dict("records")` after
  `capture_fixtures._json_safe` (see `_capture_jobspy_samples`). Keys `store_jobspy_results` reads (`src/applypilot/discovery/jobspy.py`,
  the `row.get(...)` calls in that function): `job_url`, `job_url_direct`, `title`, `company`, `location`, `min_amount`, `max_amount`,
  `interval`, `currency`, `description`, `site`, `is_remote`, plus `date_posted`. Use distinct `https://www.indeed.com/viewjob?jk=synthetic<n>`
  / `https://www.linkedin.com/jobs/view/90000000<n>` URLs, fictional companies (`Example Robotics Co`, …), a mix of US cities and
  `Remote`, null salary on some rows, and 300-1,500 character markdown descriptions. `site` is `indeed` / `linkedin`.
  Include at least one location that `TestLocationOkJobspy` and friends would reject (e.g. `London, UK`), since `tests/test_filtering_smoke.py`
  switches from its 3-row fallback to these rows once they exist.
- `workday_search.json`: `{"employer_key": "examplecorp", "response": {"total": 3, "jobPostings": [...]}}` with 3 postings that have
  `title`, `externalPath` (`/job/Remote-USA/Software-Engineer_R1000<n>`), `locationsText`, `postedOn` ("Posted 3 Days Ago"),
  `bulletFields`. `total` must equal the number of postings so `search_employer`'s paging loop stops after one page.
- `gemini_score_response.json`: `{"model": "synthetic", "job_url": <the first indeed row's job_url>, "response": "<text>"}` where the
  response is what Gemini returns under `SCORE_SCHEMA` (`src/applypilot/scoring/scorer.py`): a JSON string
  `{"score": 7, "keywords": "Python, distributed systems, ...", "reasoning": "<2 sentences>"}`, which `_parse_score_json` handles.
- No email addresses or phone numbers anywhere (`test_committed_data_has_no_personal_markers` checks emails).
- `tests/data/README.md`: 3-5 lines: these samples are synthetic, written to match the live shapes on 2026-10-12; `postings/` holds
  real public postings; a live refresh is `python scripts/capture_fixtures.py --out tests/data --scrub` run by the user, followed by a
  personal-data grep before committing.
- Update the module docstring of `tests/test_recorded_data.py` to say the samples are synthetic (and how to refresh them live). Don't change any test.
If a replay test fails because the synthetic shape is wrong, fix the data, not the test. If it fails because of a real parser bug,
stop and ask through the inbox (don't fix `src/` in this low-risk plan).
**Validate:** `.venv/bin/pytest tests/test_recorded_data.py tests/test_filtering_smoke.py -v -rs` → every test passes and **none is
skipped** with "not captured yet"; `grep -rEn "[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[a-z]{2,}" tests/data/*.json` → no output;
`scripts/validate.sh` → ALL PASS.
**Status:** ✅ Complete (2026-10-12, synthetic samples)

### Task 5: Morning QC script
**Runs:** cloud (write), local (use)
**Files:** `scripts/qc.py` (new), `README.md` (modify)
**What:** `python scripts/qc.py <plan-slug> [--skip-live] [--skip-llm]`:
(1) runs `pytest tests/ -q`;
(2) runs `pytest -m live --run-live -q` and `pytest -m llm --run-llm -q` unless skipped;
(3) parses `agents/plans/<slug>.md` and prints `## Success Criteria` as a checkbox list, plus every task whose
`**Runs:**` line contains `local` and whose status isn't ✅ ("tasks for you");
(4) exits non-zero if any pytest tier failed. Use only the standard library. Document it in the README
under Testing, as the first thing to run when reviewing a nightly PR.
**Acceptance:**
- `python scripts/qc.py test-tiers-and-qc --skip-live --skip-llm` prints 5 criteria and exits 0.
- Unit test for the plan parser (`tests/test_qc_script.py`) using a small inline plan string.
**Status:** ✅ Complete (2026-10-02)

## Implementation Order
```
Task 1 ──► Task 2 ──► Task 3
              └─────► Task 5
Task 4 (synthetic data; any time after Task 2)
```
1. Task 1: unblock the suite (the nightly routine can't run tests until this lands)
2. Task 2: hermetic guard
3. Task 3: CI on PRs
4. Task 5: QC script
5. Task 4: committed synthetic recorded data (unattended)

## Key Design Decisions
1. This is roadmap item 0, because an unattended builder is only as trustworthy as the tests it runs.
2. Block the network in unit tests instead of trusting authors to mock. The Workday leak survived because nothing enforced it.
3. Use GitHub Actions as a second gate, so a PR's status doesn't rely only on the routine reporting its own results.
4. Live and LLM tiers stay local, because cloud environments only reach an allowlist (no job boards, no Gemini) and shouldn't hold API keys.
5. Committed data is JSON and scrubbed because the fork is public.
6. The committed JobSpy/Workday/Gemini samples are synthetic, not captured (user, 2026-10-12): it lets an unattended agent finish R0, and synthetic data can't leak anything. `capture_fixtures.py` stays for an optional live refresh.

## Historical Record
- 2026-10-01: Plan created (roadmap initiative R0). Suite hang diagnosed with `-o faulthandler_timeout=60`: `workday.py:158 _urlopen` reached from `tests/test_pipeline.py:16`.
- 2026-10-02: Task 1 done (nightly routine). `tests/test_pipeline.py` gets an autouse fixture patching `applypilot.discovery.workday.run_workday_discovery` and `applypilot.discovery.smartextract.run_smart_extract` (covers all 4 `_run_discover()` tests), plus a test asserting the stubs ran. `pytest tests/test_pipeline.py -q`: 5 passed in 1.0s. `pytest tests/ -q` (2 deselects pending Task 2): 359 passed, 25 skipped, 2 deselected in 14.6s.
- 2026-10-02: Task 2 done (nightly routine). `tests/conftest.py`: `pytest_configure` sets `APPLYPILOT_DIR` to a `mkdtemp` session dir (removed in `pytest_unconfigure`); autouse `_block_network` patches `socket.socket.connect`/`connect_ex` via `patch.object` to raise `NetworkAccessBlocked` for non-localhost, non-`AF_UNIX` addresses, skipped for `live`/`llm` items. New `tests/test_hermetic.py` (7 tests). The two tests that needed a real `~/.applypilot` now pass with no changes, because the isolated APP_DIR exists; the gate no longer needs any `--deselect`. `HOME=$(mktemp -d) pytest tests/ -q`: 367 passed, 26 skipped in 13.4s (in the cloud image this also needs `PYTHONUSERBASE=/root/.local`, since pip installs to the user site under HOME). No other test touched the network.
- 2026-10-02: Task 3 done (VPS session). `.github/workflows/ci.yml` runs on `pull_request`/`push` to `trunk` + `workflow_dispatch`, Python 3.12 via `astral-sh/setup-uv@v6` (matches the local `.venv`), installs jobspy `--no-deps` + its runtime deps like `scripts/cloud_setup.sh`. Deviation: lint is `ruff check src/ tests/ scripts/ --select E9,F63,F7,F82` (real errors only), because ruff 0.16's default rule set reports ~150 pre-existing style findings in `src/`; full-lint cleanup is not planned yet. CI green on PR #3.
- 2026-10-02: Task 5 done (VPS session). `scripts/qc.py` (stdlib only) + `tests/test_qc_script.py` (5 tests). `python scripts/qc.py test-tiers-and-qc --skip-live --skip-llm` prints 5 criteria and exits 0. README Testing section starts with it.
- 2026-10-02: Task 4 code done (VPS session). `scripts/capture_fixtures.py --out <dir> --scrub` writes `jobspy_<site>.json` (5 raw rows), `workday_search.json` (one CXS page) and `gemini_score_response.json` (scored against the script's fake `SAMPLE_RESUME`, so the real resume never enters the prompt). Scrubbing removes profile `personal.*` identifiers, name parts, `site_passwords`, `resume_facts` companies/projects/school, emails/phones/URLs from `resume.txt`, and every email address; description fields capped at 2,000 chars. New `tests/test_recorded_data.py`: scrubber tests always run; replay tests (JobSpy rows → `store_jobspy_results`, Workday page → `search_employer`, Gemini reply → `_parse_score_response`) skip until the samples are committed. `tests/test_filtering_smoke.py` prefers `tests/data/jobspy_*.json`. The live capture itself was blocked by this session's auto-mode permission classifier, so it stays with the user (M6).
- 2026-10-12: Task 4 rescoped to synthetic samples written by an unattended agent; ROADMAP M6 dropped.
- 2026-10-12: Task 4 done (unattended run). Synthetic `tests/data/jobspy_{indeed,linkedin}.json` (5 rows each, fictional companies, includes `London, UK` and `Toronto, ON, Canada`), `workday_search.json` (3 postings, `total` 3), `gemini_score_response.json` (score 7) and `tests/data/README.md`. `pytest tests/test_recorded_data.py tests/test_filtering_smoke.py -v -rs`: 32 passed, 0 skipped. No `src/` changes were needed.

## Validation
- `scripts/validate.sh --full` → ALL PASS.
- `.venv/bin/pytest tests/test_recorded_data.py -v -rs` → 0 skipped.
- `python scripts/qc.py test-tiers-and-qc --skip-live --skip-llm` → exits 0.

## Human test steps (preprod)
None (low risk; test data only).
