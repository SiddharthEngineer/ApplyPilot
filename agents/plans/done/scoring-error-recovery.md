# Plan: Scoring Error Recovery
**Started:** 2026-10-01
**Status:** ✅ Complete (2026-10-02)

## Goal
All 326 scored jobs in `~/.applypilot/applypilot.db` have `fit_score = 0` with reasoning
`LLM error: Client error '404 Not Found' … /v1beta/openai/chat/completions`. They were scored on
2026-08-27 14:46 UTC, before commit `e32a07b` fixed the Gemini 404. `scorer.score_job()` returns
`{"score": 0}` on any LLM error, and `run_scoring` saves it. `pending_score` selects
`fit_score IS NULL`, so these jobs are never retried, and nothing has ever reached tailoring
(0 tailored resumes). After this plan, LLM failures leave a job unscored and retryable, and the
existing 326 rows are reset.

## Success Criteria
1. After a forced LLM error, `sqlite3 ~/.applypilot/applypilot.db "select count(*) from jobs where score_reasoning like 'LLM error%' and fit_score is not null"` returns 0.
2. `applypilot run score --reset-errors` resets the 326 historical rows to `fit_score IS NULL` and prints the count.
3. A job that fails scoring 3 times stops being retried (`score_attempts >= 3`).
4. `pytest tests/test_scoring_errors.py -v` passes.

## Task Chain

### Task 1: Don't persist LLM errors as scores
**Runs:** cloud
**Files:** `src/applypilot/scoring/scorer.py` (modify), `src/applypilot/database.py` (modify), `tests/test_scoring_errors.py` (new)
**What:** Change `score_job()` to return `{"score": None, "error": "<msg>", …}` on an exception instead
of `score: 0`. In `run_scoring`, when `score is None`, write `score_reasoning = 'LLM error: …'` and
increment a new `score_attempts INTEGER DEFAULT 0` column, leaving `fit_score` NULL. Add the column
using the same idempotent `ALTER TABLE … ADD COLUMN` migration pattern `database.py` uses for other
columns (search for it first). Change `pending_score` to
`full_description IS NOT NULL AND fit_score IS NULL AND COALESCE(score_attempts,0) < 3`, and update the
duplicate query at `database.py:271` the same way.
**Acceptance:**
- Tests: error → `fit_score` NULL and `score_attempts == 1`; third failure excludes the job from `pending_score`; success still stores an integer score.
- `pytest tests/ -q` passes.
**Status:** ✅ Complete (2026-10-02)

### Task 2: `--reset-errors` flag
**Runs:** cloud
**Files:** `src/applypilot/cli.py` (modify), `src/applypilot/database.py` (modify), `tests/test_scoring_errors.py` (modify)
**What:** Add `reset_score_errors(conn) -> int`, which runs
`UPDATE jobs SET fit_score=NULL, score_reasoning=NULL, scored_at=NULL, score_attempts=0 WHERE score_reasoning LIKE 'LLM error%'`
and returns the row count. Expose it as `applypilot run score --reset-errors` (or a standalone flag on
`run`, matching existing typer option style). Don't run it on the user DB in this plan. The reset is
run after gemini-free-tier-llm lands (see ROADMAP manual steps).
**Acceptance:**
- Test with an in-memory DB seeded with 2 error rows + 1 real score → returns 2, real score untouched.
**Status:** ✅ Complete (2026-10-02)

## Implementation Order
```
Task 1 ──► Task 2
```
1. Task 1: stop the bleeding
2. Task 2: recovery tool

## Key Design Decisions
1. NULL + attempt counter instead of score 0, because 0 is a valid "bad fit" score and must not be confused with "never evaluated".
2. Cap retries at 3 so a job whose description consistently breaks the model doesn't burn quota on every run.
3. Reset is a manual step, so the 326 jobs are re-scored after per-stage models, quota-aware stopping and the pre-filter are in place (gemini-free-tier-llm).

## Historical Record
- 2026-10-01: Plan created after DB audit (roadmap initiative R3).
- 2026-10-02: Tasks 1+2 done (VPS session 1, one commit). `score_job()` returns `score: None` + `error` on an LLM exception **or an unparseable reply** (no SCORE line; previously also saved as 0). `run_scoring` leaves `fit_score`/`scored_at` NULL, stores `LLM error: …` and increments new column `score_attempts` (schema + `_ALL_COLUMNS` migration). `database.PENDING_SCORE_WHERE` (`… AND COALESCE(score_attempts,0) < MAX_SCORE_ATTEMPTS=3`) is shared by `pending_score` and `get_stats()["unscored"]`. `reset_score_errors(conn)` + `applypilot run score --reset-errors` (resets, prints the count, exits). Finding: the 326 historical rows are stored as `"\nLLM error: …"` (empty keywords + newline), so the plan's `LIKE 'LLM error%'` matched 0; the reset uses `LTRIM(score_reasoning, char(10))`. Verified on a scratch copy of the user DB: migration OK, reset → 326, pending_score 2009 → 2335, zero scores left 0. The user DB was not modified (M3). `tests/test_scoring_errors.py`: 8 tests. Gate: 394 passed, 20 skipped.
