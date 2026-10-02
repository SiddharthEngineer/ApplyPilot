# Morning Review

Newest session first. Each section: what landed, how to verify it yourself, what's waiting on you.
Sessions run on the VPS in "VPS mode" (see `agents/BUILD_AGENT.md` §0) and merge to `trunk` themselves.

## Next session

Paste into a new session connected to the VPS:

> Build agent, VPS mode: follow `agents/BUILD_AGENT.md` and do the next session in `agents/MORNING.md`'s plan.

Session plan (remaining):
1. ~~Session 1: setup, R0, R3~~ ✅ 2026-10-02
2. **Session 2:** R2 `job-board-discovery-repair` ← next
3. **Session 3:** R4 `gemini-free-tier-llm`
4. **Session 4:** R5 `resume-template-tailoring` (stops at your M4 sign-off)

## Session 1 (2026-10-02): setup, R0 test tiers, R3 scoring errors

**Landed on trunk**
- **R0 `test-tiers-and-qc`** ([PR #3](https://github.com/SiddharthEngineer/ApplyPilot/pull/3), 4/5)
  - CI now runs on every PR and push to `trunk`. Lint gates on real errors only; ruff 0.16's defaults flag about 150 old style issues in `src/`.
  - `scripts/qc.py <slug>`: morning QC (unit/live/LLM tiers, then the plan's checklist).
  - `capture_fixtures.py --out tests/data --scrub` and `tests/test_recorded_data.py`: scrubbed recorded samples. The tests skip until data is committed (M6).
- **R3 `scoring-error-recovery`** (PR #4, 2/2 ✅)
  - LLM errors and unparseable replies leave `fit_score` NULL and are retried up to 3 times (`score_attempts`).
  - `applypilot run score --reset-errors` re-queues them.
  - Finding: your 326 bad rows are stored as `"\nLLM error…"`, so the plan's SQL would have matched none. The reset handles that. On a scratch copy of your DB it reset exactly 326. Your real DB is untouched.
- Agent setup: `.venv` on the VPS, `BUILD_AGENT.md` §0 "VPS mode", and this file.

**Verify (≈2 min)**
```bash
cd /srv/ApplyPilot && git pull && . .venv/bin/activate
python scripts/qc.py scoring-error-recovery --skip-live --skip-llm
applypilot run --help | grep -A1 reset-errors
```

**Waiting on you**
- **Permissions for live checks:** the session's auto-mode classifier blocked the live capture, which scrapes Indeed/LinkedIn/Workday and makes one Gemini call. R2 is mostly live checks. Either allow them (a Bash allow rule for `python scripts/capture_fixtures.py*` and `applypilot *` in `.claude/settings.local.json`), or I'll leave each one as a command for you.
- **M6:** `python scripts/capture_fixtures.py --out tests/data --scrub --sites indeed,linkedin`, then `grep -ri "<your email or name>" tests/data` (expect nothing), then commit `tests/data/`.
- **M3:** still wait until R4 lands before running `--reset-errors` on the real DB.
- **Your laptop's `~/.applypilot/resume.txt`** probably has the same `fake pdf content` junk the VPS copy had.
