# Plan: Structured Job Posting Extraction
**Started:** 2026-10-03
**Status:** 🔄 In Progress

## Goal
Every enriched job gets parsed by Gemini into one structured record: company, normalized title, role category,
seniority, employment type, work mode and locations, salary range, posted date, application deadline,
summary, responsibilities, required and preferred qualifications, skills, education, years of experience,
sponsorship, clearance, benefits and team. The record goes into the database, so the dashboard can show it and filter on it.
A new pipeline stage, `extract`, runs after `enrich`. It's batched to fit the free-tier quota and is resumable, so the
existing ~2,900 described jobs are backfilled over a few days. A keyword fallback fills role category and work mode
for jobs that haven't been extracted yet, so dashboard filters work on day one.

## Success Criteria
1. `applypilot run extract --limit 10` fills `details_json` and the promoted columns for 10 jobs, using ≤ 2 LLM requests.
2. `pytest tests/test_posting_extract.py tests/test_posting_classify.py -q` passes offline (fake LLM).
3. In a hand check of 20 extracted jobs across Workday, Greenhouse/Lever/Ashby, Indeed and LinkedIn, role_category, work_mode
   and the salary min/max are right in ≥ 18 of 20, and no extracted field contains text that isn't in the posting.
4. `select count(*) from jobs where full_description is not null and role_category is null` is 0 right after
   `applypilot classify` runs (heuristics cover everything not yet extracted).
5. The backfill finishes: `select count(*) from jobs where full_description is not null and extracted_at is null` reaches 0
   within about 3 days of free-tier quota, without starving `score`/`tailor`/`cover` (the daily-quota stop still works).

## Task Chain

### Task 1: Posting model and JSON schema
**Runs:** cloud
**Files:** `src/applypilot/enrichment/posting_model.py` (new), `tests/test_posting_model.py` (new)
**What:** Follow the pattern of `scoring/resume_model.py` and `tailor.tailored_resume_schema()`: use dataclasses plus a hand-written
JSON schema dict for Gemini structured output (the repo doesn't use pydantic). Include enums and a `from_dict` that drops unknown keys and
coerces bad enum values to `"unknown"`. Bump `EXTRACT_VERSION` whenever the schema or prompt changes, so stale rows can be re-extracted.
```python
EXTRACT_VERSION = 1
ROLE_CATEGORIES = ("data_science", "data_engineering", "ml_ai_engineering", "data_analytics_bi",
    "software_engineering", "research_science", "product_program_management", "quant_finance",
    "hardware_electrical", "it_infra_devops", "other")
@dataclass
class Location: city: str | None; state: str | None; country: str | None
@dataclass
class JobPosting:
    company: str | None; title_normalized: str | None; role_category: str
    seniority: str            # intern|entry|mid|senior|staff_principal|manager|director_plus|unknown
    employment_type: str      # full_time|part_time|contract|internship|co_op|temporary|unknown
    work_mode: str            # remote|hybrid|onsite|unknown
    locations: list[Location]; remote_region: str | None
    salary_min: float | None; salary_max: float | None; salary_currency: str | None
    salary_period: str | None # year|month|hour|unknown
    posted_date: str | None; application_deadline: str | None; start_date: str | None   # YYYY-MM-DD
    summary: str; team: str | None
    responsibilities: list[str]; required_qualifications: list[str]; preferred_qualifications: list[str]
    skills_required: list[str]; skills_preferred: list[str]
    education_level: str      # none|high_school|associate|bachelors|masters|phd|unknown
    education_fields: list[str]; years_experience_min: float | None; years_experience_max: float | None
    visa_sponsorship: str     # yes|no|unknown
    security_clearance: str   # required|preferred|none|unknown
    travel: str | None; benefits: list[str]; company_blurb: str | None
def posting_schema() -> dict: ...
def batch_schema() -> dict: ...  # {"jobs": [{"job_id": str, ...JobPosting}]}
```
**Acceptance:** `pytest tests/test_posting_model.py -q` passes (round-trip, enum coercion, unknown keys dropped, schema has no unsupported keywords).
**Status:** ✅ Complete (2026-10-05)

### Task 2: Extraction prompt built from real postings
**Runs:** cloud (prompt + offline tests); local (sample review on the VPS)
**Files:** `src/applypilot/enrichment/posting_prompt.py` (new), `tests/data/postings/*.txt` (new: 6 public postings with
expected `*.json`), `tests/test_posting_prompt.py` (new)
**What:** First (local, on the VPS), sample about 15 descriptions from the DB, spread across `site` values: Workday tenants,
Greenhouse/Lever/Ashby, Indeed and LinkedIn. Note the section headers each source uses ("What you'll do", "Basic
Qualifications", "Preferred", "The Work", "Here's What You'll Need"), the salary formats (`USD87,400-USD266,300/yearly`,
pay-transparency paragraphs, hourly co-op pay) and the boilerplate (EEO, company "about us"). Commit 6 representative
postings as fixtures. Job ads are public; strip any recruiter names or emails. Then write
`build_extract_prompt(today: str) -> str` (the system prompt) and `format_batch(jobs: list[dict]) -> str` (the user message).
The rules:
- Extract only, never infer: a field the text doesn't state is `null`, `"unknown"` or `[]`.
- Use the posting's metadata block (title, company/site, location string, salary column) as well as the description.
- Map "preferred / nice to have / bonus" to `preferred_qualifications`, and "basic / minimum / required / must have" to `required_qualifications`.
- Write each list item as a short phrase, one requirement per item, with no EEO or legal boilerplate.
- Pick the role category from the title first and the responsibilities second. Pick work mode from explicit remote/hybrid/onsite wording or the location string.
- Normalize salaries to numbers plus a period. Use dates as YYYY-MM-DD. Resolve relative dates ("posted 3 days ago") against `today`.
- Keep the summary to at most 2 sentences, in your own words, with no marketing language.
Truncate each description to 12,000 characters, cutting boilerplate-heavy tails first.
**Acceptance:**
- `pytest tests/test_posting_prompt.py -q` passes: the prompt lists every enum value from `posting_model`, and `format_batch` labels each job with its `job_id` and truncates long text.
- The sampled sources and their header styles are listed in this plan's Historical Record (no posting text).
**Status:** ❌ Not started

### Task 3: DB columns for extracted fields
**Runs:** cloud
**Files:** `src/applypilot/database.py` (modify: `_ALL_COLUMNS` and a `PENDING_EXTRACT_WHERE` constant), `tests/test_database.py` (modify)
**What:** Add these columns: `details_json TEXT`, `role_category TEXT`, `seniority TEXT`, `employment_type TEXT`, `work_mode TEXT`,
`location_city TEXT`, `location_state TEXT`, `location_country TEXT`, `salary_min REAL`, `salary_max REAL`,
`salary_currency TEXT`, `salary_period TEXT`, `posted_date TEXT`, `deadline TEXT`, `extracted_at TEXT`,
`extract_attempts INTEGER DEFAULT 0`, `extract_error TEXT`, `extract_version INTEGER`, `category_source TEXT` (`llm` or `heuristic`).
`company` already exists: extraction fills it only when it's NULL. Set `PENDING_EXTRACT_WHERE` to "full_description IS NOT NULL AND
(extracted_at IS NULL OR extract_version < EXTRACT_VERSION) AND COALESCE(extract_attempts,0) < 3". `ensure_columns()` adds the columns on both backends.
`REAL` works on both SQLite and Postgres.
**Acceptance:** `pytest tests/test_database.py -q` passes, and `ensure_columns` on an old DB adds all 19 columns.
**Status:** ❌ Not started

### Task 4: Heuristic classifier (day-one filters)
**Runs:** cloud
**Files:** `src/applypilot/enrichment/classify.py` (new), `src/applypilot/cli.py` (modify: `applypilot classify`), `tests/test_posting_classify.py` (new)
**What:** `classify_title(title: str) -> str` uses ordered, whole-word keyword rules into `ROLE_CATEGORIES`. For example,
"data engineer|analytics engineer|etl" → data_engineering, "machine learning|ml engineer|ai engineer|llm" →
ml_ai_engineering, "data scien" → data_science, "analyst|business intelligence|bi " → data_analytics_bi,
"research scientist" → research_science, and so on, with `other` as the fallback. `classify_work_mode(location: str, description: str)` returns remote,
hybrid, onsite or unknown. `parse_location(location: str) -> Location` handles the formats in the DB: `"US, CA, Santa Clara"`,
`"USA.VA.Reston"`, `"Chicago, IL, US"`, `"Remote, US (Remote)"` and `"San Francisco (Remote)"`. `applypilot classify` fills
role_category, work_mode and location_* with `category_source='heuristic'` only where `extracted_at IS NULL`. It never
overwrites LLM output.
**Acceptance:**
- `pytest tests/test_posting_classify.py -q` passes, with ≥ 25 table-driven cases covering every location format above.
- Success criterion 4.
**Status:** ❌ Not started

### Task 5: Batched `extract` stage
**Runs:** cloud (code + fake-LLM tests); local (live run of 10 jobs)
**Files:** `src/applypilot/enrichment/extract.py` (new), `src/applypilot/pipeline.py` (modify: add `extract` to
`STAGE_ORDER`/`STAGE_META`/dependencies/`_STAGE_RUNNERS` after `enrich`; `score` still depends on `enrich`, so a
quota-exhausted extract never blocks scoring), `src/applypilot/config.py` (modify: `LLM_EXTRACT_MODEL` default
`gemini-3.1-flash-lite`, `EXTRACT_BATCH_SIZE` default 5), `tests/test_posting_extract.py` (new)
**What:** `run_extraction(limit: int | None = None, batch_size: int = 5, conn=None) -> dict` selects with
`PENDING_EXTRACT_WHERE`, ordered by `fit_score DESC NULLS LAST, discovered_at DESC` (SQLite ≥3.30 supports NULLS LAST, as does
Postgres). Then it sends `batch_size` jobs per request through `applypilot.llm` with `batch_schema()` structured output, at
`temperature=0`. It matches results back by `job_id` (from `drive_layout.job_key(url)`), and writes `details_json` plus the
promoted columns (first location → location_*), `category_source='llm'`, `extract_version`, `extracted_at`. A job missing from the
response, or whose JSON is invalid, gets `extract_attempts += 1` and `extract_error` set, and is retried on its own in the next batch.
`LLMQuotaExhausted` stops the stage cleanly (the R4 behavior). Use the per-stage model setting the same way scoring does.
**Acceptance:**
- `pytest tests/test_posting_extract.py -q` passes. It covers: batch round-trip, a missing job in the response, invalid enum
  coercion, quota stop, ordering and the version bump re-selection.
- On the VPS: `applypilot run extract --limit 10` makes ≤ 2 requests (check `llm_usage.json` before and after), and 10 rows have `extracted_at`.
**Status:** ❌ Not started

### Task 6: Quality check and backfill
**Runs:** local (VPS)
**Files:** this plan (Historical Record), `agents/MORNING.md`
**What:** Run `applypilot run extract --limit 40` and hand-check 20 rows against their postings (success criterion 3).
Fix the prompt for any systematic misses: bump `EXTRACT_VERSION` and add a fixture. Then start the backfill with
`applypilot run extract` (it stops at the daily quota). In MORNING.md, record the remaining count and the one-line command to resume it.
Until the cron plan exists, the user (or the next session) reruns it daily.
**Acceptance:** Success criterion 3 is recorded with numbers. The backfill command and remaining count are in MORNING.md.
**Status:** ❌ Not started

## Implementation Order
```
T1 model ─→ T2 prompt ─→ T5 extract stage ─→ T6 QC + backfill
T3 columns ─→ T4 heuristics      ↗
          └──────────────────────┘
```
1. Task 1  2. Task 3  3. Task 4  4. Task 2  5. Task 5  6. Task 6

## Key Design Decisions
1. Dataclasses plus a hand-written JSON schema match `resume_model.py`, with no new pydantic dependency.
2. The full JSON goes in `details_json`, and only filter/sort fields are promoted to columns: the detail page shows everything, while filters stay plain indexed SQL on both backends.
3. Five jobs per request on flash-lite (500 RPD) makes the ~2,900-job backfill cost about 600 requests, so it fits in about 2 days next to scoring.
4. Heuristic classification ships before LLM extraction is done, so filters are useful immediately. LLM output always wins.
5. `extract` is not a prerequisite for `score`, so quota trouble in one stage never stalls the others.
6. Most postings state no deadline, so `deadline` is often NULL. The dashboard treats NULL as "no deadline" (see `dashboard-api`).

## Historical Record
- 2026-10-03: Plan created. At planning time: 3,147 jobs, 2,940 with full descriptions (avg ~6.9k chars, max ~30k).
- 2026-10-05: The user requires LLM results to be saved after each call. Task 5 must commit each batch's rows as soon as the response returns (as `run score` now does), so a stopped run keeps everything already paid for. The jobs DB is now Postgres (R7).
