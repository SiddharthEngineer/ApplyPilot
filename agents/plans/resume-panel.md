# Plan: Resume panel: blind A/B grading in Claude Pro with a rubric, plus your own picks
**Status:** ❌ Not started
**Risk:** review (new table, web routes and dashboard page)

## Goal
Every so often (after the rules, template or library change, or monthly) you check that the resume process is working.
- **Export:** the dashboard's **Panel** page builds a blind panel packet: N jobs (default 5), each with two resume versions labelled A/B in random
  order (e.g. an older draft vs the newest, or two rules versions). The app keeps the key.
- **Grade:** you run the packet in a **fresh** claude.ai chat in a "Strelitzia Panel" project that holds the Panel rubric Doc, once on Sonnet and once on Opus.
- **Import:** you paste each reply's JSON into the import form. You also make your own blind A/B pick per job on the same page.
- **Report:** the app un-blinds and reports per-qualification grades, win rates per model, agreement between the models, and agreement with **your** picks.
  Your picks are the ground truth; Claude grading Claude-written text is biased toward it.
- **Reminder:** "Panel due" appears when a panel is due.

## Context
- Depends on R23 (`claude-workbench`): drafts are stored per version (`draft_version`, `draft_rules_version`, `tailor_report`), `targets.py`
  (`targets`, `coverage`), `workbench.extract_result`-style JSON extraction, `DriveClient.upsert_doc` and `publish_static_docs` (add the rubric Doc there).
- Research basis: HiredScore grades each basic/preferred qualification as met/not met with evidence, then gives an overall A–D (all basic met → A/B,
  preferred separate A from B). LLM screeners show position bias (favor the first item) and self-preference (favor LLM-written text), hence
  order-swapping, two models, and your labels as ground truth.
- Older drafts: R23 keeps each accepted draft's JSON under the job's output folder; this plan needs them addressable by `(job_url, draft_version)`.
  If R23 overwrote earlier versions, Task 1 adds a `drafts` table first.
- Pattern for reports and labels: CSV-free here, everything in the DB; cost is $0 (Pro chats).

## Tasks
### Task 0: Precondition (R23 merged)
**Runs:** unattended
**Files:** none
**What:** Check first: `git fetch -q origin && git cat-file -e origin/trunk:src/strelitzia/scoring/workbench.py`.
If that fails, plan `claude-workbench` hasn't merged yet. Change nothing, and finish with result status `blocked` and summary
"waiting for claude-workbench to merge; requeue with buildqueue.py add resume-panel".
**Validate:** the command exits 0.
**Status:** ❌

### Task 1: Drafts history and panel tables
**Runs:** unattended
**Files:** `src/strelitzia/database.py`, `src/strelitzia/scoring/workbench.py`, `src/strelitzia/scoring/panel.py` (new), `tests/test_panel.py` (new)
**What:** If not already present: `drafts (id, job_url, version, rules_version, resume_json, cover_json, coverage_json, created_at)`, written by `import_result`.
`panel_runs (id, created_at, rules_version_a, rules_version_b, status, packet_text)`, `panel_items (run_id, item_no, job_url, draft_a_id, draft_b_id, shown_first: "a"|"b")`,
`panel_grades (run_id, item_no, grader: "sonnet"|"opus"|"you", winner: "first"|"second"|"tie", detail_json, created_at)`.
`panel.select_items(conn, n=5, mode: "latest_vs_previous" | "rules_a_vs_b") -> list[Item]`: jobs with two comparable drafts, newest first.
**Validate:** `pytest tests/test_panel.py -q` (selection rules, random but stored order). `scripts/validate.sh` green.
**Status:** ❌

### Task 2: Rubric, packet and import
**Runs:** unattended
**Files:** `src/strelitzia/scoring/panel.py`, `src/strelitzia/scoring/workbench.py` (`publish_static_docs` adds "Strelitzia panel rubric"), `tests/test_panel.py`
**What:**
- `rubric_doc() -> str` (HTML). For each item, grade each version separately and then compare:
  1. Per required and preferred qualification: `met | partial | not_met`, with a verbatim quote from the resume as evidence (empty when not met).
  2. An overall grade A–D (A: all required met plus most preferred; B: all required met; C: most required met; D: otherwise).
  3. Clarity 1–5 (can a recruiter see the fit in 10 seconds).
  4. Red flags: unsupported-looking claims, keyword stuffing, vague bullets.
  5. Winner: `first | second | tie`, with one sentence on why.
  Output is one ```json block: `{"panel": 1, "items": [{"item": 1, "first": {...}, "second": {...}, "winner": "first", "why": "..."}]}`.
  Versions are called "First" and "Second" (never A/B or old/new) and nothing in the packet reveals which is newer.
- `panel_packet(conn, run) -> Packet(text, prompt)`: per item the job title, company, required/preferred qualifications, then the two resumes
  as plain text (`resume_to_text`) in the stored order. Each run also stores a second packet with the order swapped, for the second model.
- `import_grades(conn, run_id, grader, text) -> ImportOutcome`: parse the JSON, map first/second back to the drafts, store. Wrong item count or bad JSON → errors.
**Validate:** `pytest tests/test_panel.py -q` (packet never contains version numbers or dates of the drafts; swap mapping; import round trip). `scripts/validate.sh` green.
**Status:** ❌

### Task 3: Report and "panel due"
**Runs:** unattended
**Files:** `src/strelitzia/scoring/panel.py`, `tests/test_panel.py`
**What:** `panel_report(conn, run_id) -> dict`: per grader, the win rate of the newer version (ties as half), the mean overall grade per version,
the per-qualification met rate per version, and the red-flag counts. Agreement Sonnet vs Opus and each model vs you (share of items with the same winner).
Verdict: "newer is better" when your picks favor it ≥ 60% and no model reports more red flags for it; otherwise "no clear gain" or "worse".
Across runs: the heard-back rate by `submitted_rules_version`, showing "too few to compare" under 10 submissions per version.
`panel_due(conn) -> str | None`: a reason when the rules version changed since the last completed panel, or 30+ days have passed, **and** at least 5 jobs have two comparable drafts.
**Validate:** `pytest tests/test_panel.py -q` (report math on fixture grades, due reasons). `scripts/validate.sh` green.
**Status:** ❌

### Task 4: Panel page in the dashboard
**Runs:** unattended
**Files:** `src/strelitzia/web/app.py`, `tests/web/test_panel_api.py` (new), `dashboard/src/pages/PanelPage.tsx` (new), `dashboard/src/App.tsx`, `dashboard/src/api.ts`,
`dashboard/src/types.ts`, the nav (Panel with a "due" dot), dashboard tests, rebuilt static bundle
**What:** Routes: `GET /panel` (runs, due reason), `POST /panel/runs` `{n, mode}`, `GET /panel/runs/{id}` (packets, prompts, status, report), `POST /panel/runs/{id}/grades` `{grader, text}`.
Page steps: 1. Create run. 2. **Copy packet for model 1** (with a "use Sonnet, new chat in the Panel project" hint) and a paste box. 3. The same for model 2
(swapped packet, Opus). 4. **Your picks**: each item shows First/Second side by side as text, and you click First, Second or Tie. 5. The report.
The jobs list header shows "Panel due: <reason>" with a link.
**Validate:** `pytest tests/web -q`; `cd dashboard && npm test -- --run`; `scripts/validate.sh --full` green.
**Status:** ❌

The first real panel (your Claude Pro chats and your picks) is ROADMAP manual step M23, after merge.

## Validation
- `scripts/validate.sh --full` passes.
- `pytest tests/test_panel.py tests/web/test_panel_api.py -q` passes.

## Human test steps (preprod)
1. Open `/app/panel`: with fewer than 5 comparable jobs it says how many more drafts are needed; otherwise click **Create run**.
2. Copy packet 1, paste it into a new Sonnet chat in the Panel project, and paste the JSON reply back: "5 items graded".
3. The same with packet 2 on Opus.
4. Make your 5 picks; the report shows the win rates, the agreements and the verdict.
5. Paste a reply with 4 items: you get an error naming the count mismatch.

## Decisions
1. The panel runs in Claude Pro chats from a copy-paste packet and a rubric Doc in a separate Claude Project; it costs $0 in API.
2. It runs on change (a new rules version) or monthly, with at least 5 comparable jobs, not on every resume.
3. Two models see opposite orders, and versions are only "First" and "Second", to cancel position bias and hide which is newer.
4. Your blind picks are the ground truth; model grades are a second opinion because of self-preference bias.
5. The rubric mirrors HiredScore-style per-qualification grading with quoted evidence, plus clarity and red flags.
