# Plan: Test Tiers + Morning QC
**Started:** 2026-10-01
**Status:** 🔄 In Progress

## Goal
Plans are implemented unattended by a nightly Claude Code cloud routine (see `agents/BUILD_AGENT.md`) and
reviewed by the user the next morning. That only works if the automated gate is trustworthy. Today:
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
**Status:** ❌ Not started

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
**Status:** ❌ Not started

### Task 4: Committed recorded-response data
**Runs:** local (capture needs network), then cloud for the tests
**Files:** `scripts/capture_fixtures.py` (modify), `tests/data/` (new dir), `tests/test_filtering_smoke.py` (modify)
**What:** Add `--out tests/data --scrub` to `capture_fixtures.py`. It writes small JSON (not pickle)
samples: 5 rows per JobSpy board, one Workday listing page, and one Gemini scoring response. Scrubbing
removes anything from `profile.json`/`resume.txt` (name, email, phone, URLs) and caps descriptions at
2,000 characters. Tests that currently skip when gitignored pickles are missing use `tests/data/` instead.
The user runs the capture locally and commits the output after checking it with
`grep -ri "<own email>" tests/data` (expect no matches).
**Acceptance:**
- `pytest tests/test_filtering_smoke.py -v` runs (not skips) in a fresh clone.
- `grep -rli "applypilot" tests/data/*.json` shows no personal data. The user confirms in Historical Record.
**Status:** ❌ Not started

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
**Status:** ❌ Not started

## Implementation Order
```
Task 1 ──► Task 2 ──► Task 3
              └─────► Task 5
Task 4 (local capture; any time after Task 2)
```
1. Task 1: unblock the suite (the nightly routine can't run tests until this lands)
2. Task 2: hermetic guard
3. Task 3: CI on PRs
4. Task 5: QC script
5. Task 4: committed recorded data (needs the user)

## Key Design Decisions
1. This is roadmap item 0, because an unattended builder is only as trustworthy as the tests it runs.
2. Block the network in unit tests instead of trusting authors to mock. The Workday leak survived because nothing enforced it.
3. Use GitHub Actions as a second gate, so a PR's status doesn't rely only on the routine reporting its own results.
4. Live and LLM tiers stay local, because cloud environments only reach an allowlist (no job boards, no Gemini) and shouldn't hold API keys.
5. Committed data is JSON and scrubbed because the fork is public.

## Historical Record
- 2026-10-01: Plan created (roadmap initiative R0). Suite hang diagnosed with `-o faulthandler_timeout=60`: `workday.py:158 _urlopen` reached from `tests/test_pipeline.py:16`.
- 2026-10-02: Task 1 done (nightly routine). `tests/test_pipeline.py` gets an autouse fixture patching `applypilot.discovery.workday.run_workday_discovery` and `applypilot.discovery.smartextract.run_smart_extract` (covers all 4 `_run_discover()` tests), plus a test asserting the stubs ran. `pytest tests/test_pipeline.py -q`: 5 passed in 1.0s. `pytest tests/ -q` (2 deselects pending Task 2): 359 passed, 25 skipped, 2 deselected in 14.6s.
