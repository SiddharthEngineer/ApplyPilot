# Plan: Claude workbench: tailor resumes and cover letters in Claude Pro via Drive packets and paste-back
**Status:** 🔨 In progress
**Risk:** review (removes the API writer, changes the template, PDF output, Drive client, DB columns, web routes and the job page; adds deps)

## Goal
Resume and cover-letter writing moves from the Claude API to your Claude Pro plan (2026-10-14 decision).
- **One-time setup:** the app writes three Google Docs (Strelitzia rules, Content library, and later the Panel rubric) into a
  `Strelitzia/_Claude` Drive folder and keeps them current. You attach them to a "Strelitzia Resumes" Claude Project once.
- **Per job:** **Open in Claude** writes a job packet Google Doc into the job's Drive folder. It holds the posting, its
  required/preferred qualifications, the evidence you have for each, the unmet ones, and the current draft. It also copies
  a short prompt to your clipboard. Without Drive, the full packet is copied instead.
- **Iterate:** you work in one claude.ai chat, on Opus or Sonnet as you choose. Every answer ends in a fenced JSON block in a fixed format.
- **Paste back:** you paste that answer into **Paste result** on the job page. The app runs the deterministic checks (number provenance,
  no unsupported skills or tools, no hidden text or injection, banned words, cover-letter rules). On failure it shows the errors and a
  ready-to-paste "fix these" message for the same chat. On success it renders an ATS-safe PDF, a DOCX and the cover-letter PDF,
  uploads them to Drive, and records the draft version and coverage.

The Claude API stays only for Haiku scraping, extraction and scoring. The API tailor, cover, judge and legacy resume paths are deleted.

## Context
Research basis (2026-10-13 research pass; URLs go in the PR body):
- AI screeners (Workday HiredScore, Greenhouse Talent Matching, LinkedIn Hiring Assistant, Ashby) check **each required/preferred qualification**
  against the parsed resume. The honest lever is to state every evidenced qualification explicitly, preferably in the most recent role.
- Keyword-density targets are unvalidated vendor marketing, so coverage is reported, never a gate.
- ATS parse failures come from columns, tables, text boxes, header/footer regions, images and odd fonts. Some Workday users report DOCX parses better.
- Hidden text and prompt injection are detected in production and get candidates rejected. They are forbidden and validated against.

Code:
- `src/strelitzia/scoring/tailor.py`: `tailored_resume_schema` (`:180-195`, slug/role enums), the content-library prompt rules (`:198-298`;
  reuse the rules text for the rules Doc), `_role_slots` (`:157-177`), `fit_to_one_page` (`:850-884`), output writing (`:924-967`).
  API calls to remove: `get_client("tailor")` at `:548,:662,:719,:776`, the judge (`:436-499,:631-676`), the legacy `--source resume` path (`:738-845`), `run_tailoring`.
- `src/strelitzia/scoring/cover_letter.py`: prompt rules (`:33-110`) go into the rules Doc. `tailored_evidence` (`:130-163`) is reused.
  Bug: `convert_to_pdf` on the letter (resume parser) loses the body, and `:311` stores `str(dict)` as `pdf_path`. Remove `get_client("cover")` (`:196`) and `run_cover_letters`.
- `src/strelitzia/scoring/validator.py`: `FABRICATION_WATCHLIST` (`:66-74`), `validate_provenance` (`:274-295`, numbers only), the cover-letter checks (`:418-467`) are kept and reused on pasted results.
- `src/strelitzia/scoring/template.py:257-347` render + `resume_to_text`; `src/strelitzia/config/resume_template.default.html`; `src/strelitzia/scoring/pdf.py:361-403` HTML → PDF (Playwright).
- `src/strelitzia/enrichment/posting_model.py:59-90`: the `JobPosting` fields (`skills_required`, `skills_preferred`, `required_qualifications`,
  `preferred_qualifications`, `title_normalized`, …) in `jobs.details_json`; `assessment._posting` (`assessment.py:184-188`) reads them.
- `src/strelitzia/storage/drive.py:126-230`: `DriveClient` (`ensure_folder_path`, `find_file`, `upsert_file` for PDFs), scope `drive.file`
  (the app sees only files it created); `storage/drive_layout.py` (company → role → date folders); `storage/sync.py` `sync_job`.
  Drive needs ROADMAP M8 (OAuth). Everything must also work without Drive (clipboard fallback).
- `src/strelitzia/llm.py:23` `PURPOSES`; `config/llm_models.yaml`; `src/strelitzia/pipeline.py:45-66,219-261,326-383,475` (tailor/cover stages);
  `src/strelitzia/web/tasks.py` (`GENERATE_KINDS`, `generate_for_job`); `src/strelitzia/web/app.py:181-196` (`/generate`); dashboard `JobPage.tsx` / `ActionBar.tsx` generate buttons.
- Tests to update or delete with the code they cover: `tests/test_content_library_tailor*.py`, `tests/test_live_scoring_tailoring_cover.py`
  (tailor/cover parts), `tests/web/test_tasks.py` (generate kinds). Validator tests stay. Deleting tests for deleted code is not weakening tests;
  every kept behavior (validators, rendering, overflow) keeps its tests.
- R22 (`workspace-intake`) lands first; this plan's job-page work goes below R22's Notes / Contacts / Interviews sections.

## Tasks
### Task 0: Precondition (R22 merged)
**Runs:** unattended
**Files:** none
**What:** Check first: `git fetch -q origin && git cat-file -e origin/trunk:src/strelitzia/intake.py`.
If that fails, plan `workspace-intake` hasn't merged yet. Change nothing, and finish with result status `blocked` and summary
"waiting for workspace-intake to merge; requeue with buildqueue.py add claude-workbench".
**Validate:** the command exits 0.
**Status:** ✅ 2026-10-15 (R22 is on origin/trunk)

### Task 1: Output contract and packet builder
**Runs:** unattended
**Files:** `src/strelitzia/scoring/workbench.py` (new), `src/strelitzia/scoring/targets.py` (new), `tests/test_workbench_packet.py` (new), `tests/test_targets.py` (new)
**What:**
- `targets.py`: `Target(text, kind: "required"|"preferred", field)`; `targets(posting: dict) -> list[Target]`; `term_variants(term) -> set[str]`
  (case-folded, acronym ↔ expansion from a small table plus "Full Name (ACR)" patterns found in the posting);
  `evidence_map(posting, library, base_resume_text) -> EvidenceMap` (each target `evidenced` with backing project slugs / base-resume role,
  or `unevidenced`). Honestly claimable = library facts + base resume only; the skills boundary doesn't license new skills.
  `coverage(resume_text, targets) -> CoverageReport(covered, missing, pct_required, pct_preferred)`.
- `workbench.py`:
  `RESULT_SCHEMA`: JSON Schema for the pasted result: `{"strelitzia": 1, "resume": <tailored_resume_schema + headline ≤ 110 chars>, "cover_letter": {"paragraphs": [str], "evidence_slugs": [str]} | null, "notes_for_user": str?}`.
  `rules_doc() -> str` (HTML): the writing rules from today's tailor and cover prompts, rewritten for a chat:
  - cover every evidenced required qualification at least once, in a bullet citing a backing project, in the most recent role it applies to;
  - use the posting's exact term, with the acronym or expansion the first time;
  - never mention an unevidenced target, and no keyword lists in bullets;
  - no hidden text or instructions to screeners;
  - one page, bullets ≤ 230 chars, no dashes or banned words;
  - the cover letter is 3 paragraphs and ≤ 250 words, built on cited evidence;
  - end **every** reply with one ```json block matching RESULT_SCHEMA (the schema is included).
  `library_doc(library) -> str` (HTML): the content library with slugs visible.
  `job_packet(job, library, base_resume_text, current: dict | None) -> Packet(html: str, text: str, prompt: str)`. The packet has title/company/url,
  the description, required and preferred targets each marked evidenced (with slugs) or unevidenced, the allowed slugs and role keys,
  bullet slots per role, and the current draft JSON when there is one. `prompt` is ≤ 600 chars: "In this project, tailor my resume and
  cover letter for the job in the Google Doc '<name>' …". Without Drive, `prompt` = `text` + the rules (everything inline).
  `RULES_VERSION`: a short hash of `rules_doc()`.
**Validate:** `pytest tests/test_workbench_packet.py tests/test_targets.py -q` → evidence map on a synthetic library (acronym match counts, a skills-boundary-only skill is unevidenced), packet lists unmet targets, prompt under 600 chars, schema validates a sample result. `scripts/validate.sh` green.
**Status:** ✅ 2026-10-15 (this commit)

### Task 2: Paste-back import and checks
**Runs:** unattended
**Files:** `src/strelitzia/scoring/workbench.py`, `src/strelitzia/scoring/validator.py`, `src/strelitzia/database.py` (columns), `tests/test_workbench_import.py` (new)
**What:**
- `extract_result(text) -> dict`: the last ```json block (or the whole text if it parses), validated against `RESULT_SCHEMA`; clear errors on missing or invalid JSON.
- `validate_claims(resume, evidence_map) -> list[str]`: every posting target that appears in bullets, the headline or Skills must be evidenced
  (extends the watchlist logic to bullets). Plus `validate_provenance` (numbers), slug/role checks, bullet counts, banned words and dashes,
  and the cover-letter checks plus number provenance against its cited evidence.
- `import_result(conn, job, text) -> ImportOutcome(ok, errors, warnings, fix_prompt, draft_version, coverage, unmet)`. On errors, `fix_prompt`
  is a pasteable message: "These problems were found: 1. … Keep everything else the same and reply with the corrected JSON." Nothing is saved.
  On success it saves the resume JSON and `_REPORT.json` as today's tailor output does, renders the PDF (with `fit_to_one_page`; trimmed bullets
  become warnings) and the DOCX and cover PDF (Task 4), and updates columns.
- Columns: `draft_version INTEGER` (increments per accepted paste), `draft_rules_version TEXT`, `tailor_report TEXT` (JSON: coverage, unmet,
  warnings), `resume_docx_path TEXT`, `submitted_draft_version INTEGER`, `submitted_rules_version TEXT`, `packet_doc_id TEXT`, `packet_doc_url TEXT`.
  The existing `tailored_resume_path`, `tailored_at`, `cover_letter_path` and `cover_letter_at` keep their meaning.
**Validate:** `pytest tests/test_workbench_import.py -q` covers prose around the JSON, invalid JSON, an unevidenced skill in a bullet → error + fix
prompt, a number not in the facts → error, a valid result → files written, `draft_version` 1 then 2. `scripts/validate.sh` green.
**Status:** ❌

### Task 3: Parse-safe PDF, round-trip test, no hidden text
**Runs:** unattended
**Files:** `pyproject.toml` (add `pypdf>=4`, BSD), `src/strelitzia/config/resume_template.default.html`, `src/strelitzia/scoring/template.py`,
`src/strelitzia/scoring/validator.py` (`validate_visible`), `tests/test_resume_roundtrip.py` (new), `tests/test_hidden_text.py` (new), synthetic fixtures under `tests/fixtures/` (no personal data)
**What:**
- Round-trip: render a synthetic resume to PDF, extract the text with pypdf, and assert name → contact → each role (title, company, dates) → bullets → education → skills
  in order, with token recall ≥ 0.98 vs `resume_to_text`. Fix what fails: likely dates inline after the company ("Company · City · Jan 2024 – Present")
  instead of the right-aligned flex span, " | " instead of "▫", font stack `Calibri, Carlito, Arial, sans-serif`. No columns, tables,
  header/footer regions or icon text in the default template. Add `{{ headline }}` under the name (custom templates opt in).
- `validate_visible(html) -> list[str]`: errors for text hidden by color near the background, font-size < 6pt, `display:none`, `visibility:hidden`,
  opacity < 0.5 or off-page positioning, and for injection phrases ("ignore previous", "AI screener", "rank this candidate", "system prompt").
  The PDF's extracted text must contain no tokens outside the visible text. `import_result` runs both.
**Validate:** `pytest tests/test_resume_roundtrip.py tests/test_hidden_text.py -q` → the default template passes; white-on-white, 1pt and injection fixtures fail. `scripts/validate.sh` green.
**Status:** ❌

### Task 4: DOCX export and cover-letter PDF
**Runs:** unattended
**Files:** `pyproject.toml` (add `python-docx>=1.1`, MIT), `src/strelitzia/scoring/docx_export.py` (new), `src/strelitzia/scoring/cover_letter.py`,
`src/strelitzia/config/cover_letter_template.default.html` (new), `tests/test_docx_export.py` (new), `tests/test_cover_letter_pdf.py` (new)
**What:** `docx_export.to_docx(resume_model, path)`: a single-column Word file with real heading styles (no tables, text boxes or header/footer content),
written as `<prefix>.docx` next to the PDF. `cover_letter.render_pdf(paragraphs, profile, job, path) -> Path` through its own HTML template
(date, greeting, paragraphs, sign-off with the profile name) and the existing Playwright HTML → PDF path, storing the real path string.
**Validate:** the DOCX test reopens the file with python-docx and finds the headings and all bullets in order; the cover PDF test extracts the text with pypdf and finds the greeting, every paragraph and the sign-off. `scripts/validate.sh` green.
**Status:** ❌

### Task 5: Drive docs for the Claude Project and job packets
**Runs:** unattended
**Files:** `src/strelitzia/storage/drive.py`, `src/strelitzia/storage/drive_layout.py`, `src/strelitzia/scoring/workbench.py`, `tests/test_drive_docs.py` (new)
**What:** `DriveClient.upsert_doc(folder_id, name, html, known_id=None) -> DriveFile`: upload HTML with target mimeType
`application/vnd.google-apps.document` (Drive converts it to a Google Doc); update in place when `known_id` or a same-named Doc the app created exists.
`workbench.publish_static_docs(client, conn)` writes "Strelitzia rules" and "Strelitzia content library" into `Strelitzia/_Claude/`, only when their hash
changed (hashes kept in a small `app_state` key/value table or file), and returns their URLs. `workbench.publish_packet(client, conn, job) -> (doc_id, url)`
writes "<Company> – <Role> – Claude packet" into the job's folder (`drive_layout.folder_path`), saving `packet_doc_id` / `packet_doc_url`. No Drive configured → callers fall back to the clipboard text.
**Validate:** `pytest tests/test_drive_docs.py -q` with a fake Drive service: creates a Doc with the conversion mimeType, the second call updates instead of creating, static docs skip unchanged hashes. `scripts/validate.sh` green.
**Status:** ❌

### Task 6: API routes and job page workbench
**Runs:** unattended
**Files:** `src/strelitzia/web/app.py`, `tests/web/test_workbench_api.py` (new), `dashboard/src/api.ts`, `dashboard/src/types.ts`, `dashboard/src/App.tsx`,
`dashboard/src/pages/JobPage.tsx`, `dashboard/src/components/WorkbenchPanel.tsx` (new), `dashboard/src/components/CoveragePanel.tsx` (new),
`dashboard/src/pages/ClaudeSetupPage.tsx` (new), dashboard tests, rebuilt `src/strelitzia/web/static/**`
**What:**
- `POST /jobs/{key}/packet` → `{prompt, doc_url | null, inline: bool}` (publishes the static docs if stale, then the packet). `POST /jobs/{key}/result`
  `{text}` → the `ImportOutcome` (422 with errors + `fix_prompt` when it fails; 200 with the updated job detail when it passes).
  `GET /claude/setup` → static doc URLs, `RULES_VERSION`, whether Drive is configured, and the project instructions text.
- `job_detail` adds `draft_version`, `coverage`, `unmet`, `warnings`, `packet_doc_url`, and links to the PDF, DOCX and cover PDF.
- Job page **Resume & cover letter** section (replaces the Generate buttons): **Open in Claude** copies the prompt, shows "Copied" with
  links to the packet Doc and to claude.ai, and notes the rules version. **Paste result** is a textarea + Check & save: errors list with
  **Copy fix message**, or success with the files. A **Resume targeting** panel shows the required coverage %, the covered targets, and
  "Not on your resume" (unmet, report-only). Draft history: version, date, rules version.
- `/claude-setup` page (nav): step-by-step: create a Claude Project "Strelitzia Resumes", attach the two Docs (links), paste these project instructions (copy button). It also shows when the rules Doc last changed.
**Validate:** `pytest tests/web -q` (packet inline vs Drive, result 422 with fix_prompt, result 200 bumps the version); `cd dashboard && npm test -- --run` (copy flow, error list, coverage panel, setup page); `scripts/validate.sh --full` green.
**Status:** ❌

### Task 7: Remove the API writer
**Runs:** unattended
**Files:** `src/strelitzia/scoring/tailor.py`, `src/strelitzia/scoring/cover_letter.py`, `src/strelitzia/llm.py`, `src/strelitzia/config/llm_models.yaml`,
`src/strelitzia/pipeline.py`, `src/strelitzia/cli.py`, `src/strelitzia/web/tasks.py`, `src/strelitzia/web/app.py`, `README.md`, affected tests
**What:** Delete the API calls, the judge, the legacy `--source resume` path, `run_tailoring` and `run_cover_letters`. Keep the schema, slots,
rendering, `fit_to_one_page`, `tailored_evidence` and the validators (moved into or used by `workbench.py`). Remove `tailor` and `cover` from
`PURPOSES` and `llm_models.yaml` (drop the Opus price row only if nothing uses it). Remove the `tailor` and `cover` pipeline stages and CLI
validation options; `strelitzia run tailor` prints "Resumes are written in Claude now: open a job in the dashboard → Open in Claude". Remove the
`resume` / `cover` / `both` task kinds and `POST /generate`. Add `strelitzia workbench packet <key>` (prints the inline packet) and
`strelitzia workbench import <key> <file>` for terminal use. README: replace the Tailor/Cover pipeline rows and the Cost section with the workbench flow;
API cost is now Haiku only. Delete the tests that only covered the removed API paths; keep or port every validator and rendering test.
**Validate:** `grep -rn 'get_client("tailor")\|get_client("cover")' src` → nothing; `strelitzia run --dry-run` lists no tailor or cover stages; `scripts/validate.sh --full` green.
**Status:** ❌

The end-to-end run with a real Claude Project (Drive OAuth, Claude Pro, your library) is ROADMAP manual step M22, after merge.

## Validation
- `scripts/validate.sh --full` passes.
- `strelitzia workbench packet <key>` on a fixture DB prints a packet containing "Required qualifications" and the JSON schema;
  `strelitzia workbench import <key> tests/fixtures/workbench_result_ok.json` → "Saved draft v1" with the PDF/DOCX paths; with
  `workbench_result_bad.json` → exit code 1 and the fix message.
- `python -c "from strelitzia.llm import PURPOSES; print(PURPOSES)"` → `('discovery', 'extract', 'scoring')`.

## Human test steps (preprod)
1. `https://strelitzia-preprod.engineerfamily.net/app/claude-setup`: follow the steps; both Doc links open in Google Docs.
2. Open a scored job → **Open in Claude**: "Copied" appears with a packet Doc link; the Doc shows the posting, qualifications marked evidenced/unevidenced, and the allowed slugs.
3. In claude.ai, in the project, start a chat and paste the prompt; ask for one change; copy the final reply.
4. **Paste result** → Check & save: the PDF, DOCX and cover letter appear, and Resume targeting shows coverage and "Not on your resume".
5. Edit the JSON to add a skill you don't have, paste again: an error names it, and **Copy fix message** gives a message for the chat.
6. Mark the job Submitted: `GET /app/api/jobs/<key>` shows `submitted_draft_version` and `submitted_rules_version`.

## Decisions
1. Resume and cover-letter writing runs in your Claude Pro chats, not the API; the API keeps only Haiku scraping, extraction and scoring (2026-10-14).
2. Results come back by pasting a fixed JSON block into the app, because it works on any plan and doesn't rely on Claude writing to Drive.
3. Fixed inputs (rules, content library) live in app-made Google Docs attached to a Claude Project once; the per-job packet is its own Doc in the job's folder.
4. Without Drive, the full packet goes to the clipboard, so the flow never depends on OAuth.
5. The API tailor, cover, judge and legacy resume paths are deleted, not hidden behind a flag.
6. The deterministic checks (numbers, unsupported claims, hidden text, banned words) replace the API judge; quality judgment moves to the R24 panel.
7. Honestly claimable means the content library or base resume; unmet requirements are shown to you, never written in.
8. Coverage is report-only; PDF stays the default with a DOCX alongside; a truthful headline is added via `{{ headline }}`.
9. Each submitted job records its draft version and rules version, so callback rates can be compared over time.
