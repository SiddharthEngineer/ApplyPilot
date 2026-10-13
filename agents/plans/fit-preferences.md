# Plan: Preferences page and two-layer fit score (code-scored preferences + LLM rubric)
**Status:** 🟡 In progress
**Risk:** review (new DB tables, web API and dashboard page; changes what `fit_score` means)
**Depends on:** [claude-llm-provider](claude-llm-provider.md) (Claude client, batch runner, `estimate_cost`, `llm_usage`)

## Goal
The user sets job preferences on a new **Preferences** page. Each one has a value and an importance:
**Don't care / Nice to have / Important / Very important / Dealbreaker**. Every job gets one overall **fit score
(0-100)** for sorting and filtering, and a breakdown on the job page ("Skills 4/5 · Salary ✓ · Hybrid ✗ · ⚠ mostly
sales"). The score has two layers:
1. **Preference layer (code, free).** Salary, location, work mode, job field, seniority, employment type, visa,
   clearance, travel, education, experience years and company blocklist are compared to the fields that extraction
   already stores. Changing any of these re-ranks every job instantly at no cost.
2. **Assessment layer (Claude).** A fixed rubric, which users can't edit as a prompt, scores skills match, experience
   match and domain interest on anchored 1-5 scales, and flags red flags. It only re-runs when its inputs change
   (resume, skills, target roles, industries). Before it does, the user sees "Re-assess 412 jobs, about $0.40?" and
   must confirm.

Dealbreakers flag a job rather than delete it. The jobs list hides flagged jobs by default and a filter can show them.
Users never see or edit the prompt.

## Context
- Extracted fields (`enrichment/posting_model.py:JobPosting`) are the preference layer's inputs: `role_category`,
  `seniority`, `employment_type`, `work_mode`, `locations`, `remote_region`, `salary_min/max/currency/period`,
  `visa_sponsorship`, `security_clearance`, `travel`, `education_level`, `years_experience_min`,
  `skills_required/preferred`, `company`. Many are `"unknown"`. See Decision 3.
- Current scoring: `scoring/scorer.py` (after plan A, Claude with a 1-10 `SCORE_SCHEMA`), saved to `jobs.fit_score`
  (INTEGER 1-10), `score_reasoning`, `scored_at`.
- `fit_score` consumers: `database.py:463` (`>= 7` for tailoring), `database.py:540-590` (`min_score`),
  `web/queries.py:18,97-100,127`, `dashboard/src/components/{Filters,JobsTable}.tsx`, `pages/JobPage.tsx:209-212`
  ("/10"), `view.py`, and the prefilter in `scorer.py:170-210`.
- Profile today is `profile.json` (`config.load_profile()`, see `profile.example.json`) plus the resume file
  (`RESUME_PATH`). Preferences go in the DB, not `profile.json`, so they're editable from the dashboard and per-user
  later.
- Web patterns: FastAPI routes in `web/app.py` (CSRF guard at 245), query helpers in `web/queries.py`, and background
  work through `web/tasks.py` (`dashboard_tasks` table, `KINDS`). Dashboard routes are in `dashboard/src/App.tsx`.
  Use the Sunrise theme tokens in `dashboard/src/styles.css`.
- The built bundle is committed in `src/strelitzia/web/static`. CI checks it, so rebuild after dashboard changes.
- Never put the user's real preferences or resume in tests or fixtures. Use synthetic ones.

## Tasks
### Task 0: Precondition (R20 merged)
**Runs:** unattended
**Files:** none
**What:** The build queue has no dependencies, so check first: `git fetch -q origin && git cat-file -e origin/trunk:src/strelitzia/llm_batch.py`.
If that fails, plan `claude-llm-provider` hasn't merged yet. Change nothing, and finish with result status `blocked` and summary
"waiting for claude-llm-provider to merge; requeue with buildqueue.py add fit-preferences".
**Validate:** the command exits 0.
**Status:** ✅ 2026-10-13 (R20 is on trunk, PR #39)

### Task 1: Preference model and code scorer
**Runs:** unattended
**Files:** src/strelitzia/scoring/preferences.py (new), src/strelitzia/database.py (modify), tests/test_preferences.py (new)
**What:**
- Dataclasses:
  - `Importance = Literal["dont_care", "nice", "important", "very_important", "dealbreaker"]`, with weights 0/1/2/4 and
    dealbreaker = 4 plus a flag.
  - `Criterion(importance, value)`.
  - `Preferences`, one `Criterion` per item:
    - `salary`: min, currency, period
    - `locations`: places plus `remote_ok`
    - `work_mode`: accepted set
    - `role_category`: accepted set
    - `seniority`: accepted set
    - `employment_type`: accepted set
    - `visa`: needs sponsorship
    - `clearance`: holds one
    - `travel`: max %
    - `education`: my level
    - `experience`: my years
    - `company_blocklist`
    - LLM-layer importances: `skills_match`, `experience_match`, `domain_interest`, `red_flags`
    - LLM-layer inputs: `skills_want`, `skills_avoid`, `industries_favor`, `industries_avoid`, `target_roles`
- Defaults: everything Don't care except salary, locations and role_category (Important) and skills_match (Very
  important).
- `Preferences.from_dict` is lenient, like `JobPosting.from_dict`.
- `score_preferences(prefs, posting) -> list[Component]`. `Component(key, label, score: float | None, importance,
  dealbreaker_hit: bool, note: str)`.
- `combine(components) -> (overall_0_100: int, dealbreakers: list[str])`: the weighted mean of the components whose
  importance isn't dont_care.
- Table `user_preferences(user_id TEXT NULL UNIQUE, prefs_json TEXT, updated_at)`.
**Validate:** `pytest tests/test_preferences.py -q` → table-driven cases: salary below floor (score 0, dealbreaker when
set), hourly salary converted to yearly (×2080), unknown work mode → 0.5 and no dealbreaker, dont_care excluded from
the mean, all-dont_care → overall from the LLM layer only.
**Status:** ✅ 2026-10-13 (this commit)

### Task 2: Assessment rubric (LLM layer)
**Runs:** unattended
**Files:** src/strelitzia/scoring/assessment.py (new), src/strelitzia/scoring/scorer.py (modify), src/strelitzia/database.py (modify), tests/test_assessment.py (new)
**What:**
- Prompt structure:
  - The system prompt is fixed. It holds the rubric with 1-5 anchors per dimension and a red-flag enum:
    `mostly_sales`, `commission_heavy`, `title_mismatch`, `unrealistic_requirements`, `contract_disguised`,
    `relocation_required`, `unpaid_or_trial`, `vague_posting`, `other`.
  - A second cached system block holds the candidate's resume text plus the LLM-layer inputs, rendered as a
    deterministic JSON document (sorted keys).
  - The user turn holds the **extracted posting** as JSON plus the description, truncated to 8k characters, never the
    metadata noise.
- Output schema `{skills_match: {score 1-5, evidence}, experience_match: {...}, domain_interest: {...}, red_flags:
  [{code, note}], matched_keywords: [str], summary: str}`.
- `assessment_input_hash(prefs, resume_text) -> str` (sha256 of the second block) and `ASSESS_VERSION = 1`.
- New columns: `fit_assessment TEXT` (JSON), `assessment_hash TEXT`, `assessed_at TEXT`, `fit_breakdown TEXT`
  (JSON of all components), and `fit_flags TEXT` (JSON list of dealbreaker and red-flag codes).
- `fit_score` is 0-100. The migration multiplies existing 1-10 values by 10 so the order holds until re-assessment.
- A job needs assessment when it's extracted and `assessment_hash` is not the current hash or `ASSESS_VERSION`
  changed.
- `run_scoring` assesses pending jobs (batch/sync from plan A), then calls `recompute_fit(conn)`.
- `recompute_fit(conn, urls=None)` recomputes `fit_score`, `fit_breakdown` and `fit_flags` from the stored assessment
  and the current preferences in pure Python, with no LLM calls.
- Red flags count as one component: 1.0 with no flags, minus 0.34 per flag, floored at 0.
- Delete the old `SCORE_PROMPT`. Keep the title prefilter; it now sets `fit_flags=["prefilter"]` instead of
  `fit_score=1`.
- Update the `>= 7` thresholds (`database.py:463` and the others listed in Context) to `>= 70`.
**Validate:** `pytest tests/test_assessment.py tests/test_scoring_errors.py tests/test_scoring_prefilter.py -q` → pass;
the hash is stable across dict orderings; changing only salary prefs leaves the hash unchanged.
**Status:** ✅ 2026-10-13 (this commit). A pipeline run only assesses never-assessed jobs; stale ones wait for a confirmed `reassess` (Decision 5). `fit_flags` holds failed dealbreakers (red flags count when Red flags is a dealbreaker) and `prefilter`; flagged jobs are not tailored.

### Task 3: Preferences and rescore API
**Runs:** unattended
**Files:** src/strelitzia/web/app.py (modify), src/strelitzia/web/queries.py (modify), src/strelitzia/web/tasks.py (modify), tests/web/test_preferences_api.py (new)
**What:**
- `GET /api/preferences` returns the prefs plus the option lists (enums from `posting_model.ENUMS`).
- `POST /api/preferences/preview` takes a body of prefs and returns
  `{reassess_needed: bool, jobs_to_reassess: int, estimated_usd: float}`, using `llm.estimate_cost` with batch pricing.
- `PUT /api/preferences` takes `{prefs, confirm_reassess: bool}`:
  - It saves, then runs `recompute_fit` synchronously.
  - If the assessment hash changed and `confirm_reassess` is set, it enqueues a `reassess_all` task (new `KINDS`
    entry, which runs `run_scoring(mode="batch")`).
  - Without confirmation, jobs keep their old assessment and show a "stale" badge.
- `POST /api/jobs/{key}/rescore` enqueues a sync single-job assessment.
- Job list and detail return `fit_score`, `fit_breakdown`, `fit_flags` and `assessment_stale`.
- List filters: `hide_flagged` (default true) and `score_min/max` on 0-100.
- Every LLM-triggering endpoint checks `check_budget`. A budget stop returns 402 with the month spend.
**Validate:** `pytest tests/web -q` → preview reports 0 reassess for a salary-only change and N for a skills change;
PUT without confirm leaves `assessment_hash` untouched; the budget stop returns 402.
**Status:** ❌

### Task 4: Preferences page and fit breakdown UI
**Runs:** unattended
**Files:** dashboard/src/pages/PreferencesPage.tsx (new), dashboard/src/App.tsx (modify), dashboard/src/api.ts (modify), dashboard/src/types.ts (modify),
dashboard/src/components/Filters.tsx (modify), dashboard/src/components/JobsTable.tsx (modify), dashboard/src/pages/JobPage.tsx (modify), dashboard/src/__tests__/PreferencesPage.test.tsx (new), src/strelitzia/web/static (rebuild)
**What:**
- `/preferences` route and nav link.
- Page layout:
  - One row per criterion: label, value control (number, multi-select chips, toggle) and an importance segmented
    control with 5 levels. Dealbreaker is styled as a warning.
  - Grouped into "Basics" (salary, location, job field), "Role" (seniority, type, work mode, experience, education),
    "Eligibility" (visa, clearance, travel), "What the AI judges" (skills want/avoid, industries, target roles,
    importance of skills/experience/domain/red flags) and "Companies to skip".
  - "Advanced" groups are collapsed by default. Don't-care rows are visually quiet.
- Save flow:
  - Save calls preview first.
  - If reassessment is needed, a confirm dialog shows "Re-assess N jobs with Claude, about $X (takes up to an hour).
    Preference-only changes apply now for free." with **Re-assess** and **Save without re-assessing** buttons.
  - Otherwise it saves directly and shows "Re-ranked N jobs".
- Jobs table: fit is 0-100 with a small colour bar; flagged rows show a ⚠ with a tooltip listing the reasons.
- Filters: "Show flagged jobs" checkbox; score min/max become 0-100 steps of 10.
- Job page: a breakdown list (component, score, note, importance), red flags, the AI summary, a "Rescore" button that
  polls the task, and a "stale assessment" note.
**Validate:** `cd dashboard && npm test -- --run && npm run build` → pass; `scripts/validate.sh --full` → green.
**Status:** ❌

### Task 5: Haiku vs Sonnet eval for the assessment
**Runs:** interactive (the user labels jobs; spends ~$1 of API)
**Files:** scripts/eval_fit.py (new), agents/plans/fit-preferences.md (record result)
**What:**
- `python scripts/eval_fit.py sample --n 30 > ~/.strelitzia/eval/fit_labels.csv` picks 30 extracted jobs spread
  across role categories and current scores. The CSV has columns `job_key,title,company,label`. It lives outside the
  repo and is never committed.
- The user fills in `label` with good, ok or bad.
- `python scripts/eval_fit.py run --models claude-haiku-5-5,claude-sonnet-5-5 --efforts low,medium` runs the
  assessment synchronously for each combination. For each one it reports:
  - Spearman correlation of the overall score with the labels (bad=0, ok=1, good=2)
  - how many good jobs land in the top 10
  - cost per 1,000 jobs from `llm_usage`
- Pick the cheapest combination within 0.05 correlation of the best, set it in `llm_models.yaml` (`scoring`), and
  record the numbers in this plan's Decisions.
**Validate:** the script prints a table with one row per model/effort; the chosen default is committed.
**Status:** ❌

## Validation
- `scripts/validate.sh --full` passes.
- `strelitzia run score --sync --limit 3` on fixtures → `fit_breakdown` has every non-dont_care component and `fit_score` is in 0-100.
- Changing only salary prefs through `PUT /api/preferences` changes `fit_score` values with zero new `llm_usage` rows.

## Human test steps (preprod)
1. Open `/app/preferences`. Defaults: salary, location and job field are Important; everything else is Don't care.
2. Raise the salary minimum and mark it Dealbreaker, then Save. There's no cost dialog, and the jobs list re-sorts with
   low-paying jobs hidden. Tick "Show flagged jobs" and they reappear with ⚠ "salary below minimum".
3. Add a skill to "Skills I want to use" and Save. A dialog shows the job count and a dollar estimate. Choose Re-assess
   and the task finishes. Job pages show updated skills evidence, and `strelitzia doctor` spend rises by about the
   estimate.
4. On one job page, click Rescore. Within about 20s the breakdown updates.
5. Set the monthly budget to just above the current spend and try a re-assess. You get a clear "budget reached" message
   and nothing is charged.

## Decisions
1. Importance has five levels; Dealbreaker flags and hides rather than deletes (user, 2026-10-13: weight + dealbreaker,
   hiding via frontend filters).
2. Defaults are Don't care except salary, location and job field. Visa, clearance and the rest exist for future users
   (user, 2026-10-13).
3. Unknown job values score 0.5 and never trigger a dealbreaker. Postings often omit salary or work mode, and hiding
   them all would hide most jobs.
4. One overall 0-100 score for sorting plus a stored breakdown (user, 2026-10-13: breakdown and one number).
5. Only the assessment layer calls Claude. Preference-only changes are free and instant; assessment-input changes need
   an explicit confirm with a cost estimate (user, 2026-10-13).
6. The rubric is fixed in code, and users steer it only through structured inputs (skills, industries, target roles,
   importances), never free-text prompt (user, 2026-10-13).
7. The model is picked by the Task 5 eval, with Haiku 5.5 as the starting default (user, 2026-10-13).
