# Plan: Resume SEO/GEO, phase 1: eval baseline, ATS-safe output, qualification targeting
**Status:** ❌ Not started
**Risk:** review (changes the tailoring prompts, the judge, the resume template, the models and the job page; adds deps in `pyproject.toml`)

## Goal
Tailored resumes (and their cover letters) score better with both ATS keyword search and AI screeners (Workday HiredScore,
Greenhouse Talent Matching, LinkedIn Hiring Assistant, Ashby criteria), **without** claiming anything the content library
or base resume doesn't back up. Phase 1 builds the measuring stick first, then makes the changes and measures them:
1. an offline eval with a baseline (deterministic metrics, an order-swapped Claude screener panel, your blind A/B labels);
2. resume PDFs that parse cleanly (round-trip test), an optional DOCX, a truthful headline, and a working cover-letter PDF;
3. tailoring driven by the posting's structured required/preferred qualifications, covering every evidenced one and listing the unmet ones for you;
4. resumes and letters written by Opus 5.5 at high effort, checked by an Opus judge;
5. each submitted job records which tailoring version it used, so callback rates per version build up over months.
Later plans (screener-panel iteration, cover letter quality) are written after this plan's numbers are in.

## Context
Research basis (summarized from a 2026-10-13 research pass; URLs in the PR body when this ships):
- AI screeners check **each qualification** (HiredScore: LLM checks every basic/preferred qualification against parsed experience,
  education, skills, certifications and summary, then grades A to D; Greenhouse: LLM-extracted profile vs recruiter-weighted criteria;
  LinkedIn Hiring Assistant: required/preferred qualifications per candidate; Eightfold weights skills used in recent roles).
  The honest lever: every evidenced required qualification is stated explicitly, in the right section, preferably in the most recent role.
- Keyword-density targets (e.g. "75% match") are vendor marketing with no outcome evidence: coverage is a diagnostic, never a gate.
- ATS parse failures come from columns, tables, text boxes, header/footer regions, images and odd fonts. Some Workday users report DOCX parses better.
- LLM screeners favor LLM-written text (self-preference) and the first item shown (position bias). So Claude screener scores are
  order-swapped and treated as secondary to your blind A/B labels.
- Hidden text (white or tiny keywords) and prompt injection are detected in production and lead to rejection. They are **forbidden** and tested against (Task 4).

Code:
- `src/strelitzia/scoring/tailor.py:180-298`: `tailored_resume_schema`, content-library system prompt (cached; keep the system block job-independent).
  `:286` is the only keyword instruction today. `:502-628` `tailor_from_content_library` (retry loop with "AVOID" notes, `:556-560`).
  `:534-539` sends only `full_description[:6000]`: the structured posting is unused.
- `src/strelitzia/scoring/tailor.py:436-499,631-676`: the LLM judge; its "TOLERANCE RULE" (`:493-497`) allows "Adding any LEARNABLE skill": remove it.
  The judge calls `get_client("tailor")` at `:662`.
- `src/strelitzia/scoring/validator.py:66-74` `FABRICATION_WATCHLIST` (skills section only), `:274-295` `validate_provenance` (numbers only), `:418-467` cover-letter checks.
- `src/strelitzia/enrichment/posting_model.py:59-90`: `JobPosting` fields `title_normalized`, `skills_required`, `skills_preferred`,
  `required_qualifications`, `preferred_qualifications`, `responsibilities`, `education_*`, `years_experience_*`; stored in `jobs.details_json`
  (read via `assessment._posting`, `assessment.py:184-188`). `assessment.py:83` `matched_keywords` is a second source of candidate terms.
- `src/strelitzia/scoring/template.py:257-347` render + `resume_to_text`; `src/strelitzia/config/resume_template.default.html` (single column,
  right-aligned flex date spans, Calibri); `src/strelitzia/scoring/pdf.py:361-403` HTML → PDF via Playwright; `tailor.py:850-884` `fit_to_one_page`;
  `tailor.py:924-967` writes `.pdf`, `.json` sidecar, `.txt`, `_JOB.txt`, `_REPORT.json`.
- **Bug:** `cover_letter.py` sends the letter through `convert_to_pdf` (resume parser): the name becomes "Dear Hiring Manager," and the body is lost;
  `cover_letter.py:311` stores `str(dict)` as `pdf_path`.
- `src/strelitzia/llm.py:23` `PURPOSES`, `:78` `resolve_model`; `src/strelitzia/config/llm_models.yaml` per-purpose model/effort/output_tokens and prices.
- Pattern for the eval script: `agents/plans/fit-preferences.md` Task 5 (`scripts/eval_fit.py`: labels as CSV under `~/.strelitzia/eval`, cost from `llm_usage`).
- Tests: `tests/test_content_library_tailor*.py`, `tests/test_validator_source.py`, `tests/test_pdf_overflow.py` (already renders PDFs with Chromium),
  live tier `tests/test_live_scoring_tailoring_cover.py` (`-m llm --run-llm`). Hermetic tests mock every Claude call.
- Only the content-library path changes; legacy `--source resume` stays as is (Decision 3).

## Tasks
### Task 1: Models: Opus writes, Opus judges, Haiku scrapes
**Runs:** unattended
**Files:** `src/strelitzia/config/llm_models.yaml`, `src/strelitzia/llm.py`, `src/strelitzia/scoring/tailor.py`, `README.md` (Cost section), tests for `resolve_model`
**What:** `tailor` and `cover` → `claude-opus-5-5`, effort `high` (raise `output_tokens` estimates to 5000 / 2000). New purposes
`judge` (`claude-opus-5-5`, effort `medium`, 800) and `screener` (`claude-sonnet-5-5`, effort `medium`, 1200) added to `PURPOSES`;
the tailor judge (`tailor.py:662` and any cover judge) uses `get_client("judge")`. `discovery`, `extract`, `scoring` stay Haiku.
`LLM_<PURPOSE>_MODEL` / `_EFFORT` env overrides keep working for the new purposes.
**Validate:** `pytest -q -k "llm or resolve_model or tailor"` → new test asserts the purpose → model map and that the judge resolves `judge`; `scripts/validate.sh` green.
**Status:** ❌

### Task 2: Eval harness (`scripts/eval_tailor.py`) and metrics module
**Runs:** unattended (code); the baseline run is Task 9
**Files:** `src/strelitzia/scoring/tailor_eval.py` (new), `scripts/eval_tailor.py` (new), `tests/test_tailor_eval.py` (new)
**What:**
- `tailor_eval.py` (pure functions, hermetic-testable):
  `targets(posting: dict) -> list[Target]` (`Target(text, kind: "required"|"preferred", source_field)`) from required/preferred
  qualifications and skills; `term_variants(term) -> set[str]` (case-folded, acronym ↔ expansion from a small built-in table plus
  "Full Name (ACR)" patterns found in the posting); `coverage(resume_text, targets) -> CoverageReport` (`covered`, `missing`, `pct_required`, `pct_preferred`);
  `roundtrip(pdf_path, expected_text) -> RoundTrip` (`token_recall`, `section_order_ok`, `extracted_text`) using pypdf;
  `screener_grade(client, posting, resume_text) -> dict` (per-qualification met/not-met with quoted evidence + overall A-D, HiredScore style);
  `pairwise(client, posting, a_text, b_text) -> str` that asks twice with A/B swapped and returns `a | b | tie` (tie on disagreement).
- `scripts/eval_tailor.py` subcommands (data stays in `~/.strelitzia/eval/tailor/`, never in the repo):
  `sample --n 20` (jobs with `details_json`, spread over role categories, prefer ones you rated relevant);
  `run --variant <name>` (tailor + cover each sampled job into `variants/<name>/`, with `--reuse` to score existing outputs);
  `score --variant <name>` (coverage, round-trip, provenance/judge failures, screener grades; writes `scores_<name>.csv`);
  `compare A B` (pairwise panel on Sonnet + Opus via `screener` with a model override, plus the metric deltas);
  `label A B` (blind CLI: shows the two resumes for each job in random order as text, records your pick in `labels.csv`);
  `report` (table of every metric per variant, your label win rate, panel win rate and agreement with your labels, and $ spent from `llm_usage`).
  `--max-usd` (default 3 for `run`, 6 for `run`+`compare`) aborts before spending past it (uses `estimate_cost`).
**Validate:** `pytest tests/test_tailor_eval.py -q` → coverage counts acronym/expansion matches, round-trip on a fixture PDF, pairwise
returns `tie` when the swapped answers disagree, budget guard aborts. `python scripts/eval_tailor.py --help` lists the subcommands.
**Status:** ❌

### Task 3: Parse-safe PDF and round-trip test
**Runs:** unattended
**Files:** `pyproject.toml` (add `pypdf>=4`, BSD), `src/strelitzia/config/resume_template.default.html`, `src/strelitzia/scoring/template.py`,
`tests/test_resume_roundtrip.py` (new), `tests/fixtures/` (synthetic resume model only: no personal data)
**What:** Render a synthetic resume through the default template to PDF; extract its text with pypdf; assert name → contact → each role
(title, company, dates) → bullets → education → skills appear in order and token recall vs `resume_to_text` ≥ 0.98. Fix whatever fails:
likely the flex right-aligned dates (emit them inline after the company, e.g. "Company · City · Jan 2024 – Present"), the "▫" contact separator
(use " | "), and the font stack (Calibri, Carlito, Arial, sans-serif, with Carlito/Liberation in the Docker image only if text extraction needs it).
No header/footer regions, columns, tables or icons-as-text in the default template.
**Validate:** `pytest tests/test_resume_roundtrip.py -q` → passes; `scripts/validate.sh` green.
**Status:** ❌

### Task 4: No hidden text, ever
**Runs:** unattended
**Files:** `src/strelitzia/scoring/validator.py`, `src/strelitzia/scoring/tailor.py`, `tests/test_hidden_text.py` (new)
**What:** `validate_visible(html: str) -> list[str]`: errors for inline styles or classes that hide text (`color` equal or near the background,
`font-size` < 6pt, `display:none`, `visibility:hidden`, `opacity` < 0.5, off-page positioning) and for prompt-injection phrases in any
text ("ignore previous", "AI screener", "rank this candidate", "system prompt"). Run it on the rendered HTML before PDF; an error fails the resume.
It also checks the PDF: the extracted text must equal the visible text (round-trip from Task 3), with no extra tokens.
**Validate:** `pytest tests/test_hidden_text.py -q` → white-on-white, 1pt and injection fixtures fail; the default template passes.
**Status:** ❌

### Task 5: Qualification targeting from the structured posting
**Runs:** unattended
**Files:** `src/strelitzia/scoring/tailor.py`, `src/strelitzia/scoring/validator.py`, `src/strelitzia/scoring/tailor_eval.py`,
`tests/test_content_library_tailor_prompt.py`, `tests/test_content_library_tailor.py`, `tests/test_validator_source.py`
**What:**
- `evidence_map(posting, library, base_resume_text) -> EvidenceMap`: each target from `targets()` is `evidenced` (with the project slugs or
  base-resume role that back it, via `term_variants` over library facts/skills and the base resume) or `unevidenced`.
  Honestly claimable = content library facts + base resume only (Decision 1); `skills_boundary` no longer licenses new skills.
- User message (not the cached system block) gains a `TARGETS` section: evidenced required, evidenced preferred (with slugs), and the title.
  System prompt rules replace `:286`: cover every evidenced required qualification at least once, in a bullet citing a backing project,
  in the most recent role it applies to; use the posting's exact term, and add the acronym or expansion in parentheses the first time when
  both are common; put evidenced tools in Skills in the posting's spelling; never mention an unevidenced target; no keyword lists inside bullets.
- Judge prompt: delete the TOLERANCE RULE's learnable-skill clause; any tool, skill or credential not in the cited facts or the base resume is a FAIL.
- `validate_claims(resume, evidence_map) -> list[str]`: deterministic check that every posting target appearing in bullets or Skills is evidenced
  (extends `FABRICATION_WATCHLIST` logic to bullets). Error in all validation modes.
- The tailor report (`_REPORT.json`) gains `coverage` (from `tailor_eval.coverage`) and `unmet` (unevidenced required/preferred targets).
  Coverage is reported only, never a failure (Decision 5).
- Add `TAILOR_VERSION = 2` in `tailor.py` (1 = before this plan) and include it in the report.
**Validate:** updated prompt tests assert the TARGETS section and rules; tailor tests with a mocked client show an unevidenced target in a bullet
→ validation error + retry, and the report carries coverage/unmet; judge prompt test asserts the learnable clause is gone. `scripts/validate.sh` green.
**Status:** ❌

### Task 6: Headline, DOCX, and the cover-letter PDF fix
**Runs:** unattended
**Files:** `src/strelitzia/scoring/tailor.py` (schema + prompt), `src/strelitzia/scoring/template.py`, `resume_template.default.html`,
`src/strelitzia/scoring/docx_export.py` (new), `pyproject.toml` (add `python-docx>=1.1`, MIT), `src/strelitzia/scoring/cover_letter.py`,
`src/strelitzia/config/cover_letter_template.default.html` (new), tests (new: `test_docx_export.py`, `test_cover_letter_pdf.py`)
**What:**
- Headline: the schema gains `headline` (≤ 110 chars): a real title from the base resume/library plus 2-3 evidenced strengths matching the posting.
  Rendered under the name in the default template when the template uses `{{ headline }}`; custom templates without it are unaffected.
  `validate_claims` covers it.
- `docx_export.to_docx(resume_model, path)`: single-column Word file with real heading styles (no tables, text boxes or header/footer content),
  written next to the PDF as `<prefix>.docx` on every tailor. The job page links it (Task 7).
- Cover letter: render from its own simple HTML template (date, greeting, paragraphs, sign-off with profile name) through the existing
  Playwright HTML → PDF path; store the real path string. Add number provenance: every number in the letter must appear in the cited evidence (mirror `validate_provenance`).
**Validate:** DOCX test reopens the file with python-docx and finds headings + all bullets in order; cover PDF test extracts text with pypdf
and finds the greeting, all paragraphs and the sign-off; `pdf_path` is a str ending `.pdf`. `scripts/validate.sh` green.
**Status:** ❌

### Task 7: Version tracking and the job page
**Runs:** unattended
**Files:** `src/strelitzia/database.py` (columns), `src/strelitzia/scoring/tailor.py`, `src/strelitzia/tracking.py`, `src/strelitzia/web/app.py`,
`dashboard/src/pages/JobPage.tsx`, `dashboard/src/types.ts`, a component `dashboard/src/components/CoveragePanel.tsx` (new), dashboard tests, rebuilt static bundle,
`tests/web/test_job_detail.py`, `tests/web/test_tracking.py`
**What:** Columns `tailor_version INTEGER`, `tailor_report TEXT` (JSON: coverage + unmet + model), `resume_docx_path TEXT`, `submitted_tailor_version INTEGER`.
Tailor saves them; `tracking.set_status(..., "submitted")` snapshots `tailor_version` into `submitted_tailor_version`. `job_detail` returns
`coverage`, `unmet`, `tailor_version` and a DOCX link. Job page: a "Resume targeting" panel under the resume actions: required coverage %,
the covered targets, and "Not on your resume" (unmet requirements, as prompts to add evidence to your library or address in the letter). Report-only, no warnings.
`scripts/eval_tailor.py report` adds a section: heard-back rate by `submitted_tailor_version` (with counts, and "too few to compare" under 10 per version).
**Validate:** web tests for the new detail fields and the submit snapshot; dashboard test renders the panel from a fixture; `scripts/validate.sh --full` green.
**Status:** ❌

### Task 8: Live smoke test
**Runs:** interactive (Claude API spend, about $0.30)
**Files:** `tests/test_live_scoring_tailoring_cover.py` (modify)
**What:** Extend the live test: tailor one fixture job on Opus, assert the report has `coverage` and `unmet`, the PDF round-trips, the DOCX
exists, no hidden-text errors, and the cover-letter PDF contains its paragraphs.
**Validate:** `pytest -m llm --run-llm tests/test_live_scoring_tailoring_cover.py -q` → passes.
**Status:** ❌

### Task 9: Baseline vs phase-1 eval (you)
**Runs:** interactive (needs your jobs, library and judgment; about $6)
**Files:** none in the repo (results in the PR body and a ROADMAP decision line)
**What:** `eval_tailor.py sample --n 20`; check out the commit before Task 5 and `run --variant v1` (Sonnet, old prompt), then this branch
`run --variant v2`; `score` both; `compare v1 v2`; `label v1 v2` (you, blind); `report`.
**Validate:** The report prints, for v1 and v2: required coverage %, round-trip pass rate, provenance/judge failures, screener A/B grades,
panel win rate, your label win rate and panel–label agreement. Ship criteria: v2 wins ≥ 60% of your labels, provenance failures do not rise, round-trip 100%.
**Status:** ❌

## Validation
- `scripts/validate.sh --full` passes.
- `pytest tests/test_resume_roundtrip.py tests/test_hidden_text.py tests/test_tailor_eval.py tests/test_docx_export.py tests/test_cover_letter_pdf.py -q` → all pass.
- `python -c "from strelitzia.llm import resolve_model; print([resolve_model(p) for p in ('extract','scoring','tailor','cover','judge','screener')])"`
  → Haiku for extract/scoring, Opus high for tailor/cover, Opus for judge, Sonnet for screener.
- Task 9's report meets the ship criteria.

## Human test steps (preprod)
1. On `https://strelitzia-preprod.engineerfamily.net/app/`, open a scored job with a description and click **Generate resume + cover letter**.
2. When it finishes, the job page shows "Resume targeting" with a required-coverage % and a "Not on your resume" list. Nothing on that list appears in the resume.
3. Open the resume PDF: a headline under your name, dates inline with each company, nothing you haven't done. Copy-paste all text from the PDF into a text editor: it reads top to bottom in order.
4. Download the DOCX and open it in Word or Google Docs: same content, one column.
5. Open the cover letter PDF: greeting, three paragraphs and sign-off are all there.
6. Mark the job Submitted; `GET /app/api/jobs/<key>` shows `submitted_tailor_version: 2`.
7. In `/app/api/...` or the LLM spend view, the tailor and cover calls show `claude-opus-5-5`, and extract/scoring still show Haiku.

## Decisions
1. Honestly claimable means backed by the content library or base resume; the skills boundary no longer licenses new skills; unmet requirements are shown to you, never written in.
2. Eval comes first and phase 1 is one plan; screener-panel iteration and cover-letter quality get their own plans after Task 9's numbers.
3. Only the content-library tailoring path changes; the legacy `--source resume` path is left as is.
4. PDF stays the default; a DOCX is written alongside for Workday-style portals.
5. Coverage is a report, never a gate; there is no evidence for a magic threshold.
6. Ground truth is your blind A/B labels plus heard-back rates per tailoring version; the Claude screener panel is secondary because of self-preference and position bias.
7. Opus 5.5 (high effort) writes resumes and letters, Opus judges facts, the eval panel uses Sonnet + Opus with order swapping, and every scraping and extraction step stays on Haiku.
8. A default eval run is capped at about $3 (generate) / $6 (with the panel) via `--max-usd`, and never runs in `validate.sh`.
9. Hidden text and prompt injection are forbidden and enforced by a validator, because screeners detect them and they are deceptive.
10. A truthful one-line headline is added to the default template; custom templates opt in via `{{ headline }}`.
