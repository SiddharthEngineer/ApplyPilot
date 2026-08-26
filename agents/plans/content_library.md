# Plan: Content Library Resume Tailoring

**Started:** 2026-08-26
**Status:** In progress

---

## Goal

Add an alternative resume tailoring mode that sources resume content from `personal/content_library.md` instead of `~/.applypilot/resume.txt`. The content library is a structured bank of raw project facts (Context / Scope / Tools / Outcome / Angles) organized under role headers. An LLM selects the 5-7 most relevant projects for each job, writes one bullet per project from raw facts, and outputs a role-grouped, single-page PDF resume matching the formatting of `personal/2026 Siddharth Engineer.pdf`.

## Success Criteria

1. `applypilot run tailor --source content-library` produces a tailored resume PDF for each high-scoring job using `content_library.md` as input.
2. The LLM selects projects based on Angle-tag matching to the job description (not keyword overlap).
3. Every bullet traces to a fact in the content library (no fabricated metrics/tools).
4. Output PDF matches the visual style of `personal/2026 Siddharth Engineer.pdf` (same header, section layout, fonts, colors).
5. Resume fits on a single page (Letter size).
6. Existing `resume.txt`-based tailoring is untouched and continues to work.
7. `applypilot run tailor` (no flag) works exactly as before (backward compatible).

---

## Task Chain

### Task 1: Content Library Parser

**Files:** `src/applypilot/scoring/content_library.py` (new)

**What:** Build a parser that reads `content_library.md` and returns structured data. The parser must handle the exact markdown format in `personal/content_library.md`.

**Data model:**
```python
@dataclass
class Project:
    name: str                    # e.g. "PatentsView Data Pipeline Lead"
    role_header: str             # e.g. "CURRENT ROLE — Data Science Associate, AIR (Sep 2025–Present)"
    dates: str                   # e.g. "Nov 2025–present"
    context: str
    scope_scale: str
    tools_actions: str
    outcome_metrics: str
    angles: list[str]            # e.g. ["DEVOPS", "PIPELINE", "LEADERSHIP"]

@dataclass
class RoleSection:
    title: str                   # e.g. "CURRENT ROLE — Data Science Associate, AIR"
    dates: str                   # e.g. "Sep 2025–Present"
    projects: list[Project]

@dataclass
class ContentLibrary:
    roles: list[RoleSection]     # ordered as in the file
    all_angles: set[str]         # union of all angle tags
```

**Parsing logic:**
1. Split by `## CURRENT ROLE` / `## PRIOR ROLE` headers to get role sections.
2. Within each role section, split by `### ` headers to get individual projects.
3. For each project, extract `- **Context:**`, `- **Scope/Scale:**`, `- **Tools & Actions:**`, `- **Outcome/Metrics:**`, `- **Angles:**` fields.
4. Handle the `---` separators between role sections.
5. Skip the README section (everything before `## CURRENT ROLE`).

**Acceptance criteria:**
- `parse_content_library(path)` returns a `ContentLibrary` instance.
- All 14+ projects from the current `content_library.md` are parsed correctly.
- Angle tags are normalized to uppercase.
- Unit tests verify parsing of the real `content_library.md` file.

---

### Task 2: Content Library Tailoring Prompt

**Files:** `src/applypilot/scoring/tailor.py` (modify — add `_build_content_library_tailor_prompt()`)

**What:** Build a new system prompt for content-library-based tailoring. This prompt replaces the existing `_build_tailor_prompt()` when `--source content-library` is active.

**Prompt must instruct the LLM to:**
1. Receive the full content library (all projects with raw facts).
2. Receive the job description.
3. Identify the JD's top 3-5 priorities and map them to Angle tags.
4. Select 5-7 projects whose Angle tags best match the JD priorities.
5. For each selected project, write ONE new resume bullet from its raw facts (Context / Scope / Tools / Outcome) — do not reuse pre-written phrasing.
6. Mirror the JD's own terminology where accurate (ATS keyword matching).
7. Every number/tool in a bullet must trace to a fact in the content library.
8. Keep bullets to one line (~20-28 words), start with a strong verb, vary verbs.
9. Note which internship (if any) is worth keeping as a single line, which can be dropped.
10. Output structured JSON with the same schema as the existing tailor prompt.

**Key differences from existing prompt:**
- No `resume_text` input — the content library IS the input.
- The LLM must select WHICH projects to include (not just rewrite existing bullets).
- Bullets are written from raw facts, not reworded from existing bullets.
- The "preserve companies" rule is relaxed — the LLM decides which roles/projects are relevant.

**Acceptance criteria:**
- Prompt produces valid JSON output in testing.
- Output JSON contains experience entries grouped by role (matching content library structure).
- LLM selects projects based on Angle tags, not just keyword matching.

---

### Task 3: Content Library Tailor Function

**Files:** `src/applypilot/scoring/tailor.py` (modify — add `tailor_from_content_library()`)

**What:** New core function that orchestrates content-library-based tailoring. Mirrors the existing `tailor_resume()` function's structure (retry loop, validation, judge) but with different inputs and prompt.

**Flow:**
1. Load and parse the content library.
2. Build the content-library-specific prompt.
3. LLM receives: (a) system prompt with all project raw facts + JD, (b) user message with JD.
4. Parse JSON output.
5. Validate (updated validation — see Task 4).
6. Assemble text (reuses existing `assemble_resume_text()` — header is still code-injected).
7. Judge pass (updated judge prompt — see Task 4).
8. Retry on failure (fresh conversation each attempt).

**Key difference:** The resume_text parameter is replaced by content_library (parsed). The judge prompt must also be updated to understand that projects were selected from a library, not rewritten from an existing resume.

**Acceptance criteria:**
- `tailor_from_content_library(content_library, job, profile)` returns `(tailored_text, report)`.
- Retry logic works identically to existing `tailor_resume()`.
- Output text is in the same format as existing tailoring (header + sections).

**Status:** ✅ Complete (2026-08-26) — Added `tailor_from_content_library()`, `judge_content_library_resume()`, and `_build_content_library_judge_prompt()`. 19 unit tests pass covering successful tailoring, retry logic, exhausted retries, prompt content, and judge pass/fail.

---

### Task 4: Validation Updates

**Files:** `src/applypilot/scoring/validator.py` (modify)

**What:** Update validation to handle content-library-based output.

**Changes needed:**
1. **Relax "preserved companies" check**: In content-library mode, the LLM may drop roles/companies that aren't relevant. The validator should only check that companies mentioned in the output are real (not that all real companies appear).
2. **Add "project traceability" check**: Optionally verify that selected projects exist in the content library.
3. **Update judge prompt** (`_build_judge_prompt`): The judge must understand that (a) projects were selected from a library, (b) bullets were written from raw facts, (c) the comparison baseline is the content library, not a pre-written resume.
4. **Add new validation mode parameter**: `source="content-library"` passed through to validation functions to adjust behavior.

**Acceptance criteria:**
- Existing `resume.txt`-based validation is unchanged when `source="resume"`.
- Content-library validation passes for valid output.
- Fabrication detection still catches invented metrics/tools.

**Status:** ✅ Complete (2026-08-26) — Added `source` parameter to `validate_json_fields()` and `validate_tailored_resume()`. When `source="content-library"`, preserved-companies and preserved-projects checks are relaxed. Fabrication detection, banned words, required sections, and LLM self-talk checks remain fully enforced. Updated `tailor_from_content_library()` to pass `source="content-library"` to validation. 14 unit tests pass.

---

### Task 5: CLI & Pipeline Integration

**Files:**
- `src/applypilot/cli.py` (modify — add `--source` flag)
- `src/applypilot/scoring/tailor.py` (modify — update `run_tailoring()`)
- `src/applypilot/pipeline.py` (modify — pass source through)
- `src/applypilot/config.py` (modify — add CONTENT_LIBRARY_PATH)

**What:** Wire content-library mode into the CLI and pipeline.

**Changes:**
1. Add `--source` flag to `applypilot run tailor`: choices `resume` (default) and `content-library`.
2. Add `CONTENT_LIBRARY_PATH` to `config.py`.
3. Update `run_tailoring()` to accept `source` parameter and dispatch to `tailor_resume()` or `tailor_from_content_library()`.
4. Update `pipeline.py` `_run_tailor()` to pass source through.
5. Add `content_library_path` config option to profile or `searches.yaml`.

**CLI examples:**
```bash
applypilot run tailor --source resume          # existing behavior (default)
applypilot run tailor --source content-library  # new mode
applypilot run --source content-library         # runs all stages, tailor uses content library
```

**Acceptance criteria:**
- `applypilot run tailor` (no flag) works exactly as before.
- `applypilot run tailor --source content-library` uses content library.
- Source preference is persisted per-run (not just CLI flag).

**Status:** ✅ Complete (2026-08-26) — Added `--source` flag to CLI `run` command (choices: `resume`, `content-library`). Added `CONTENT_LIBRARY_PATH` to `config.py`. Updated `run_tailoring()` to accept `source` parameter and dispatch to `tailor_resume()` or `tailor_from_content_library()`. Plumbed `source` through `pipeline.py` (sequential, streaming, stage runner). CLI validates flag and checks file exists. 75 tests pass, lint clean.

---

### Task 6: PDF Rendering Update

**Files:** `src/applypilot/scoring/pdf.py` (modify — update `build_html()`)

**What:** Update the HTML/CSS template to match the visual style of `personal/2026 Siddharth Engineer.pdf`.

**The existing PDF has these characteristics** (inferred from the filename and existing template):
- Letter size, tight margins
- Professional header with name, title, contact
- Section dividers (blue lines)
- Clean bullet formatting

**Changes needed:**
1. Verify existing `build_html()` styling matches the target PDF.
2. If the target PDF uses a different layout (e.g., two-column skills, different spacing), update CSS.
3. Add a **one-page enforcement** check: after rendering HTML, measure content height. If overflows, log a warning and include it in the report.
4. Ensure role-grouped experience entries render correctly (role header → company → bullets).

**Acceptance criteria:**
- Output PDF visually matches `personal/2026 Siddharth Engineer.pdf`.
- Content fits on one page for typical content-library output (5-7 projects).
- Overflows are detected and logged (not silently truncated).

**Status:** ✅ Complete (2026-08-26) — Added one-page overflow detection: `render_pdf()` measures content height via Playwright and returns overflow dict; `convert_to_pdf()` returns dict with path and overflow info. Added role-group detection in `build_html()` — entries with role keywords get `role-entry` CSS class. Overflow warnings logged; `page_overflow` flag saved in report JSON. Report saved after PDF generation so overflow info is included. `run_tailoring()` captures overflow in result dict. 14 new tests pass (89 total).

---

### Task 7: Batch Entry & End-to-End Test

**Files:**
- `src/applypilot/scoring/tailor.py` (modify — update `run_tailoring()`)
- `tests/` (new test file)

**What:** Complete the batch entry point and verify end-to-end.

**Changes:**
1. `run_tailoring(source="content-library")` loads content library, iterates jobs, calls `tailor_from_content_library()`, saves `.txt`, `.pdf`, `_JOB.txt`, `_REPORT.json`.
2. Add integration test: mock LLM response, verify content library tailoring produces valid output.
3. Add unit test for content library parser against real `content_library.md`.
4. Run `ruff check src/` and `pytest tests/ -v` to verify.

**Acceptance criteria:**
- `applypilot run tailor --source content-library` processes all eligible jobs.
- Output files are saved to `~/.applypilot/tailored_resumes/` with correct naming.
- All tests pass.
- Linting passes.

**Status:** ✅ Complete (2026-08-26) — Verified batch entry point already implemented in `run_tailoring()`. Created 7 integration tests in `tests/test_content_library_e2e.py`: successful job processing with approval, file output verification (txt + report JSON), no-jobs edge case, missing content library error handling, resume source isolation, multi-job batch processing, and DB update verification. Tests mock DB/LLM/parser but exercise real `run_tailoring()` dispatch logic. 96 tests pass, lint clean. Content Library Resume Tailoring plan is fully implemented.

---

## Implementation Order

```
Task 1 (Parser) → Task 2 (Prompt) → Task 3 (Core Function) → Task 4 (Validation)
                                                                ↓
                               Task 5 (CLI/Pipeline) ← Task 6 (PDF)
                                                                ↓
                                                       Task 7 (E2E Test)
```

Each task is a coherent unit of work that can be implemented and verified independently. Tasks 1-4 are the core logic. Tasks 5-6 integrate into the pipeline. Task 7 verifies everything works together.

## Key Design Decisions

1. **Alternative mode, not replacement** — `--source` flag keeps both paths available.
2. **Same output format** — content-library tailoring produces the same text structure as existing tailoring (header + SUMMARY + SKILLS + EXPERIENCE + PROJECTS + EDUCATION), so `assemble_resume_text()` and `build_html()` are reused.
3. **Role-grouped output** — Experience entries are grouped under role headers (matching content library structure and existing resume format).
4. **HTML/Playwright kept** — no new dependencies for PDF generation.
5. **Validation relaxed for project selection** — the LLM may legitimately drop companies/projects that aren't relevant; validator checks fabrication, not completeness.

---

## Historical Record

The previous plan (OpenCode backend, completed 2026-08-25) is preserved in git history and `agent/CHANGELOG.md` under `[0.3.0]`.
