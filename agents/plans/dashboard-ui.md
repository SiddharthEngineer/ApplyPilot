# Plan: Dashboard UI (React + Vite)
**Started:** 2026-10-03
**Status:** 🔄 In Progress

## Goal
This is the browser front end for the job dashboard, served by `applypilot serve` at `/app/`. A filterable, sortable jobs table is
the home page, and clicking a row opens that job's detail page. The detail page shows every extracted field and the links
(posting, application, Google Drive folder, resume and cover letter files), and it has the action buttons: Generate resume, Generate cover letter,
Generate both, Mark submitted (with an editable date), Rejected, Heard back, and Reset status.

## Success Criteria
1. `cd dashboard && npm ci && npm run build` writes the bundle to `src/applypilot/web/static/`, and `npm test` (Vitest) passes.
2. With `applypilot serve` running, `/app/` lists jobs with colored status badges. Every filter in the brief works: All / Submitted / Rejected
   / No response / Active / Inactive / In progress / Heard back, location (including Remote), role category, date found range, due date range
   and fit score range. Sorting works on date found, due date and fit score.
3. Filters and sort live in the URL query string, so a reload or a shared link keeps them.
4. On a detail page, "Generate both" shows progress until the task finishes, then shows the Drive links. "Submitted" records today, and the date can be edited.
5. The page works at 375 px width: the table scrolls horizontally inside its container, not the whole page.

## Task Chain

### Task 1: Scaffold the Vite app and API client
**Runs:** cloud
**Files:** `dashboard/package.json`, `dashboard/vite.config.ts`, `dashboard/tsconfig.json`, `dashboard/index.html`,
`dashboard/src/main.tsx`, `dashboard/src/api.ts`, `dashboard/src/types.ts` (new), `.gitignore` (modify: `dashboard/node_modules`;
the built `src/applypilot/web/static/` IS committed so `pip install` works without Node), `pyproject.toml` (modify: package data includes `web/static/**`)
**What:** React 18 + TypeScript + Vite (the same stack as engineerfamily's `services/app/vite`) and `react-router-dom`, with `base: "/app/"`
and `build.outDir: "../src/applypilot/web/static"`. The dev server proxies `/app/api` to `127.0.0.1:8765`. `api.ts` holds typed fetch
helpers mirroring the `dashboard-api` responses. They send `X-ApplyPilot: 1` on every mutating call and surface errors as thrown
`ApiError`s. Keep dependencies minimal: no UI kit, plain CSS with tokens, and a dark mode via `prefers-color-scheme`.
**Acceptance:** `npm ci && npm run build && npm test` pass in `dashboard/`, and `applypilot serve` serves the built index at `/app/`.
**Status:** ✅ Complete (2026-10-05)

### Task 2: Jobs table and sorting
**Runs:** cloud
**Files:** `dashboard/src/pages/JobsPage.tsx`, `dashboard/src/components/StatusBadge.tsx`, `dashboard/src/components/JobsTable.tsx`,
`dashboard/src/styles.css` (new), `dashboard/src/__tests__/JobsTable.test.tsx` (new)
**What:** The columns are Status (a badge colored by the `status_color` from the API: grey, orange, yellow, blue, red or green), Title, Company,
Role, Location (with a Remote/Hybrid tag), Fit score, Found (date), Due (date or "—"), and Days since submitted (Submitted rows only).
Clicking the Found, Due or Fit headers toggles the sort (asc/desc), and the indicator shows the active sort. Rows link to `/app/jobs/:key`.
Pagination is server-side (50 per page) and shows the total count. Loading, empty and error states each get their own message.
**Acceptance:** Vitest checks the rendering of each status color, that a sort-header click updates the query, and that row links work.
**Status:** ✅ Complete (2026-10-05)

### Task 3: Filter bar with URL state
**Runs:** cloud
**Files:** `dashboard/src/components/Filters.tsx`, `dashboard/src/useQueryState.ts` (new), `dashboard/src/pages/JobsPage.tsx` (modify),
`dashboard/src/__tests__/Filters.test.tsx` (new)
**What:**
- Status segmented control: All, Active, Inactive, In progress, Submitted, No response, Rejected, Heard back. Each shows a count from `/api/facets`.
- Role category multi-select, with readable labels: Data Science, Data Engineering, ML/AI Engineering, Data/BI Analytics,
  Software Engineering, Research Science, Product/Program Mgmt, Quant/Finance, Hardware/EE, IT/Infra/DevOps, Other.
- Location: a text field with suggestions from the facets, plus "Remote" / "Hybrid" / "On-site" toggles.
- Date found from/to, due date from/to, fit score min/max (1–10), and a title/company search with a 300 ms debounce.
- A "Clear filters" button.

All state is synced to the URL query string through `useQueryState`, and the page refetches when it changes.
**Acceptance:** Vitest checks that each control writes the expected query param and that loading a URL with params restores the controls.
**Status:** ❌ Not started

### Task 4: Job detail page and actions
**Runs:** cloud (code + tests); local (click-through on the VPS)
**Files:** `dashboard/src/pages/JobPage.tsx`, `dashboard/src/components/ActionBar.tsx`, `dashboard/src/components/TaskProgress.tsx` (new),
`dashboard/src/__tests__/JobPage.test.tsx` (new)
**What:**
- Header: the title, company, status badge, fit score with `score_reasoning` (expandable), and a link row: Posting, Apply, Drive folder, Resume, Cover letter. Missing links are hidden.
- Sections, from `details`: Summary; Responsibilities; Required and Preferred qualifications; Skills (chips); Compensation; Location and work mode;
  Seniority and type; Education and experience; Sponsorship and clearance; Benefits; Team. When `details` is null, show the raw description with a
  "not extracted yet" note.
- Action bar: Generate resume, Generate cover letter, Generate both. These are disabled while a task for this job is queued or running, and progress
  is polled every 3 s with `TaskProgress`. Next to them: Mark submitted (opens a date input defaulting to today), Rejected, Heard back, Reset.
- The submitted date can be edited inline after submission. A notes textarea saves on blur. Status history is listed at the bottom.
**Acceptance:**
- Vitest, with mocked `api.ts`, checks section rendering with and without `details`, the submit flow with the default and an edited date, and
  that the buttons are disabled while a task runs.
- On the VPS: a manual click-through on one job (generate → in progress → submit → rejected) is recorded in the Historical Record.
**Status:** ❌ Not started

## Implementation Order
```
T1 scaffold ─→ T2 table ─→ T3 filters
           └─→ T4 detail page
```
1. Task 1  2. Task 2  3. Task 3  4. Task 4

## Key Design Decisions
1. React + Vite rather than Streamlit or server-rendered pages: the brief needs row click-through, per-row colored status, inline date editing and background-task progress, all of which Streamlit handles poorly. It also matches the stack engineerfamily already runs.
2. The UI is built into the Python package and committed, so the Docker image and `pip install` need no Node at runtime. Node is needed only to rebuild.
3. Filtering and sorting are server-side, so the table stays fast as nightly discovery grows the DB.
4. Filter state lives in the URL, so views are bookmarkable ("Submitted, no response, Data Science").

## Historical Record
- 2026-10-03: Plan created.
- 2026-10-05: Task 1 done (VPS session 8). React 18 + TS + Vite 8, Vitest 5 + Testing Library; react-router-dom 7 (6.x has open advisories, GHSA-wrjc-x8rr-h8h6). `serve` now defaults to the packaged bundle in `web/static` when `APPLYPILOT_WEB_DIR` is unset. Checked live: `/app/`, `/app/jobs/x` (SPA fallback) and the JS asset return 200.
- 2026-10-05: Task 2 done. `useQueryState.ts` (URL ⇄ query parse/serialize) landed here rather than in Task 3, because sort and page already live in the URL. Sorting: the first click on a header sorts descending, the second ascending. The table has `min-width: 900px` inside an `overflow-x: auto` container. Vitest: 12 passed.
