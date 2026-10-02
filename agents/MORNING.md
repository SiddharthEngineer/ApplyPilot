# Morning Review

Newest session first. Each section: what landed, how to verify it yourself, what's waiting on you.
Sessions run on the VPS in "VPS mode" (see `agents/BUILD_AGENT.md` §0) and merge to `trunk` themselves.

## Next session

Paste into a new session connected to the VPS:

> Build agent, VPS mode: follow `agents/BUILD_AGENT.md` and do the next session in `agents/MORNING.md`'s plan.

Session plan (remaining):
1. ~~Session 1: setup, R0, R3~~ ✅ 2026-10-02
2. ~~Session 2: R2 `job-board-discovery-repair`~~ ✅ 2026-10-02
3. **Session 3:** R4 `gemini-free-tier-llm` ← next
4. **Session 4:** R5 `resume-template-tailoring` (stops at your M4 sign-off)

## Session 2 (2026-10-02): R2 job-board discovery repair

**Landed on trunk:** R2 `job-board-discovery-repair` ([PR #5](https://github.com/SiddharthEngineer/ApplyPilot/pull/5), 8/8 ✅, all 6 success criteria verified live)
- **`applypilot discover --probe`**: checks each source in about 10s with no DB writes. Today's result: indeed/linkedin/ats:greenhouse/lever/ashby `ok`, glassdoor `blocked` (403), google `empty`.
- **JobSpy**:
  - Upgraded to 1.2.0, which no longer needs `--no-deps`.
  - One request per board, so a blocked board (403/429) is dropped immediately and the banner says why.
  - Glassdoor/ZipRecruiter only run when `PROXY` is set, and Google only with `defaults.allow_unsupported: true`.
  - `site_fail_threshold` is at least 2.
  - LinkedIn no longer fetches each description during discovery.
- **New source: Greenhouse/Lever/Ashby company boards.**
  - 23 data/ML/infra employers (Anthropic, OpenAI, Databricks, Stripe, Snowflake, …) are in `config/ats_boards.yaml`.
  - Titles are matched to your queries as whole words.
  - Add companies by slug.
- **SmartExtract had never stored a job.** One site timing out or showing a Cloudflare page crashed the whole run, and the 4th site in the list always did. Now errors are per site, 8 broken sites are disabled with reasons, and 2 URLs are fixed.
- **Live results in your DB this session:** JobSpy 200 (Indeed 173, LinkedIn 27), ATS boards 432, SmartExtract 173 from 7 sites. That's about 800 new unscored jobs.

**Changes on the VPS outside the repo**
- `~/.applypilot/searches.yaml`: `site_fail_threshold` 1 → 3. Backup: `searches.yaml.bak-2026-10-02`.
- `playwright install chromium`: it was missing, so SmartExtract and enrich's browser step could never run here.
- `.venv`: `python-jobspy` 1.2.0 (+ `curl_cffi`).

**Verify (≈2 min)**
```bash
cd /srv/ApplyPilot && git pull && . .venv/bin/activate && uv pip install "python-jobspy==1.2.0"
python scripts/qc.py job-board-discovery-repair --skip-llm
applypilot discover --probe
```

**Waiting on you**
- **Location filter:** accept patterns match as substrings, so your `US` also accepts "Australia", "Austin", "Houston", "Russia" (JobSpy, ATS and SmartExtract). Remote jobs anywhere are always accepted. Should I switch to whole-word matching? Then `US` would no longer match "Austin, TX" unless the location says US. Or do you want to add explicit cities/states instead?
- **Scoring load:** about 800 new jobs plus the 326 stuck ones is more than one free-tier day. R4 (next session) adds the daily-quota stop and a pre-filter, so don't run `score` on everything before then.
- **Your laptop:** run `uv pip install "python-jobspy>=1.2.0"` and `playwright install chromium` there too. Its `searches.yaml` probably still has `site_fail_threshold: 1` (now raised to 2 automatically, but 3 is the intended value).
- **M2 (proxy):** still optional. Glassdoor/ZipRecruiter 403 even on jobspy 1.2.0 with `curl_cffi`.
- SmartExtract's 8 search-type sites (×6 queries = 48 targets) weren't run live because of LLM quota. They'll run on your next `applypilot run discover`.

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
