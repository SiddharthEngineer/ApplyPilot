# Morning Review

Newest session first. Each section: what landed, how to verify it yourself, what's waiting on you.
Sessions run on the VPS in "VPS mode" (see `agents/BUILD_AGENT.md` §0) and merge to `trunk` themselves.

## Next session

Paste into a new session connected to the VPS:

> Build agent, VPS mode: follow `agents/BUILD_AGENT.md` and do the next session in `agents/MORNING.md`'s plan.

Session plan (remaining):
1. ~~Session 1: setup, R0, R3~~ ✅ 2026-10-02
2. ~~Session 2: R2 `job-board-discovery-repair`~~ ✅ 2026-10-02
3. ~~Session 3: R4 `gemini-free-tier-llm`~~ ✅ 2026-10-02
4. **Session 4:** R5 `resume-template-tailoring` (stops at your M4 sign-off) ← next

## Session 3 (2026-10-02): R4 Gemini free tier

**Landed on trunk:** R4 `gemini-free-tier-llm` ([PR #6](https://github.com/SiddharthEngineer/ApplyPilot/pull/6), 5/5 ✅, all 6 success criteria verified)
- **A model per stage.** `LLM_DISCOVERY_MODEL`, `LLM_SCORING_MODEL`, `LLM_TAILOR_MODEL` and `LLM_COVER_MODEL` each fall back to `LLM_MODEL`, then the default. Before this, the scoring and tailor vars were documented but never read. Stages on the same model share one RPM limiter.
- **Daily quota = clean stop.** A per-day 429 now stops the stage after one request with `Gemini daily quota exhausted for <model>`. Before, it was retried 5× with backoff. The job in progress gets no attempt counted, and the remaining jobs wait for the next run. The pipeline shows `stopped: daily quota` in yellow, and `--stream` no longer re-runs the stopped stage (which would have spent a request on every poll). Per-minute 429s still retry, now waiting Gemini's suggested `retryDelay`.
- **Structured JSON.** Scoring and tailoring ask Gemini for schema-constrained JSON. Live: **20 of your jobs scored, 0 parse retries, 0 errors** (scores 3–8, mostly Snowflake roles).
- **Pre-filter before scoring.** A title that shares no word with your search queries or `target_role` gets `fit_score 1` (`prefilter: title not relevant`) with no LLM call. Seniority words like Senior or Staff don't count as matches. A read-only dry run on your DB would skip **674 of 2,594** pending jobs, e.g. "HVAC Application Specialist", "VP Sales Enablement", "Client Care Representative". `--no-prefilter` turns it off.
- **`applypilot doctor`** shows each stage's model, the RPM limit, and the AI Studio limits link.
- **Security fix:** your Gemini key was sent as `?key=` in the URL. httpx logs URLs at INFO, so `doctor` printed it, and so did every native-API call when INFO logging was on. It now goes in a header.

**Changes on the VPS outside the repo**
- `/srv/ApplyPilot/.env` is the file the app actually loads, because `~/.applypilot/.env` doesn't exist. I added `LLM_DISCOVERY_MODEL`/`LLM_SCORING_MODEL=gemini-3.1-flash-lite`, `LLM_TAILOR_MODEL`/`LLM_COVER_MODEL=gemini-3.6-flash` and `LLM_RPM_LIMIT=10`. Backup: `.env.bak-2026-10-02`.
- Your DB: 20 jobs scored live (the Task 3 check). Nothing else was written.

**Verify (≈3 min)**
```bash
cd /srv/ApplyPilot && git pull && . .venv/bin/activate
python scripts/qc.py gemini-free-tier-llm --skip-live --skip-llm
applypilot doctor | grep -E "model|RPM"
applypilot run --help | grep -A1 no-prefilter
```

**Waiting on you**
- **Rotate your Gemini key (recommended).** Before the fix, the full key appeared once in this session's terminal output, from the old `doctor` log line. It hasn't been committed or sent anywhere else. Create a new key in AI Studio and put it in `/srv/ApplyPilot/.env` (and on your laptop).
- **Check your real free-tier limits.** Google's docs no longer publish numbers; they're shown per project at https://aistudio.google.com/rate-limit (needs your login). Unverified third-party figures are about 15 RPM / 1,500 requests per day for Flash models. If your scoring model's RPM differs, set `LLM_RPM_LIMIT` a little below it.
- **Tailoring model:** your key also lists `gemini-3.8-flash` (newest). At 14:20 UTC, 3.6, 3.7 and 3.8 flash all returned 503 "overloaded", so I kept the previously verified `gemini-3.6-flash`. To try 3.8 later, set `LLM_TAILOR_MODEL=gemini-3.8-flash` and `LLM_COVER_MODEL=gemini-3.8-flash`.
- **M3 is unblocked:** `applypilot run score --reset-errors`, then `applypilot run score`. That's about 2,900 unscored jobs; about 700 are pre-filtered, and the rest take roughly 2 free-tier days at about 1,500 requests per day. The stage stops by itself when the quota runs out, and you rerun it the next day. Caveat: scores are saved at the **end** of each run, so don't Ctrl-C a long run. Try `applypilot run score` in tmux/screen. Should I make it save per job? It's a small change.
- **Undoing the pre-filter** for jobs it skipped: `sqlite3 ~/.applypilot/applypilot.db "UPDATE jobs SET fit_score=NULL, score_reasoning=NULL, scored_at=NULL WHERE score_reasoning='prefilter: title not relevant'"`, then `applypilot run score --no-prefilter`.
- **Still open from session 2:** location-filter substring matching (`US` matches "Austin"), M2 proxy, M6 fixtures.

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
