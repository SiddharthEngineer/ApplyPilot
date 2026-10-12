# Plan: Remove dead discovery/enrichment code (PIV smoke test)
**Status:** ✅ Complete (2026-10-12): PR #22 merged; deployed to prod at 7c08613. The gate returned `review` because option A touched `web/tasks.py`.
**Risk:** low (deletes unreferenced code, behavior unchanged)

## Goal
Remove two functions nothing calls and the unused imports in their modules, so agents reading these large files load
less noise. This is also the first end-to-end test of the unattended PIV loop, including a question to the user.

## Context
- `src/applypilot/discovery/smartextract.py:773` `clean_card_html` and `src/applypilot/enrichment/detail.py:787` `stream_detail`:
  `grep -rn` across `src tests scripts` finds only their definitions.
- `ruff check --select F401 <file> --output-format concise` lists the unused imports: 4 in smartextract.py (`Path`, `httpx`,
  `get_connection`, `store_jobs`) and 4 in detail.py (`config`, `DB_PATH`, `get_connection`, `ensure_columns`).
- Gotcha: a test may monkeypatch a module attribute that only exists because of an unused import
  (e.g. `monkeypatch.setattr(detail, "get_connection", …)`). If removing an import breaks a test, keep that import and
  say so in the PR. Don't change the test.
- `_now()` is defined identically in `src/applypilot/tracking.py:27` and `src/applypilot/web/tasks.py:36`
  (`datetime.now(UTC).isoformat()`).

## Tasks
### Task 1: Delete dead functions and unused imports
**Runs:** unattended
**Files:** src/applypilot/discovery/smartextract.py (modify), src/applypilot/enrichment/detail.py (modify)
**What:** Delete `clean_card_html` and `stream_detail`, plus any private helpers that only they used (check with grep). Then
remove the F401 imports listed above, except the ones the gotcha says to keep.
**Validate:** `.venv/bin/ruff check --select F401 src/applypilot/discovery/smartextract.py src/applypilot/enrichment/detail.py` → no findings
(or only the imports kept on purpose); `scripts/validate.sh` → ALL PASS.
**Status:** ✅ 2026-10-11 (commit `refactor(remove-dead-enrichment-code): Task 1`). No imports needed keeping. `detail.set_proxy` is now uncalled but kept: it's a public hook, and removing it would also strand the `_PROXY_CONFIG` path.

### Task 2: Ask the user about the duplicate `_now()` helpers
**Runs:** unattended (requires one inbox question; this is deliberate, to test the reply path)
**Files:** depends on the answer: src/applypilot/tracking.py, src/applypilot/web/tasks.py (modify)
**What:** Before changing anything, ask the user via `scripts/agent/inbox.py ask` with these options: (A) `web/tasks.py` imports `_now`
from `applypilot.tracking`; (B) leave both as they are. Recommend A. Once the answer arrives in the queue notes, do what it says.
If the answer is A, rename nothing else and keep the patch to a few lines.
**Validate:** `grep -n "def _now" src/applypilot/tracking.py src/applypilot/web/tasks.py` matches the chosen option; `scripts/validate.sh` → ALL PASS.
**Status:** ✅ 2026-10-11 (commit `refactor(remove-dead-enrichment-code): Task 2`). User chose A: `web/tasks.py` now imports `_now` from `applypilot.tracking`.

## Validation
- `scripts/validate.sh --full` → ALL PASS.
- `python3 scripts/agent/gate.py agents/plans/remove-dead-enrichment-code.md` → `"tier": "low"`.

## Human test steps (preprod)
None (low risk). Afterwards, check that the PR auto-merged and that ntfy pinged you for the question and the merge.

## Decisions
1. Unused imports are removed by hand, not with `ruff --fix` across the repo, so the diff stays within the two files the plan names.
