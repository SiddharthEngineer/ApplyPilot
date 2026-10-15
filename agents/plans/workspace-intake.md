# Plan: Workspace intake: Add job by link, contacts, interviews
**Status:** ❌ Not started
**Risk:** review (new tables, new API routes and dashboard pages; `database.py`, `web/*` and `dashboard/*` are high-risk paths)

## Goal
Strelitzia becomes the workspace between finding a job (LinkedIn, Indeed, anywhere) and applying on the company's site.
In the dashboard you click **Add job** and paste a link. The row appears at once. A background task scrapes the posting,
extracts its fields and scores it, so within about a minute the row shows title, company, salary and fit score. If the
page can't be read (login wall, block, thin text), the job page says so and offers a paste form prefilled with whatever
was found. Each job also gets structured **contacts** (shared across jobs, with a Contacts page) and **interviews**
(date, stage, prep and debrief notes). The jobs list shows the next interview, and can sort and filter by it and by
source. Discovery scraping stays in the code but is off by default.

## Context
- `src/strelitzia/web/app.py:147-176`: `post_status` / `patch_job` (`JobPatch.notes` already exists): the pattern for
  new routes (Pydantic body, `job_by_key`, return `job_detail`). Mutating routes need the `X-Strelitzia: 1` header (middleware in the same file).
- `src/strelitzia/web/app.py:181-222`: `generate` / `rescore` enqueue a `dashboard_tasks` row and `_notify` the runner;
  `budget_stop` returns 402 over the monthly budget. Intake is a new task kind on the same queue.
- `src/strelitzia/web/tasks.py:36-170`: `KINDS`, `enqueue` (dedupes queued tasks), `generate_for_job` dispatch, `TaskRunner`.
  Add an `intake` kind here.
- `src/strelitzia/enrichment/detail.py:528-603`: `scrape_detail_page(page, url)`: the JSON-LD → CSS → LLM cascade for one
  page, returns `full_description`, `application_url`, `status`, `error`. Reuse it with a single Playwright page;
  the prod image already ships Chromium (`Dockerfile:5`).
- `src/strelitzia/enrichment/extract.py:57-90`: `extract_one(job)` + `_save_posting(conn, url, posting)`: structured fields for one job (sync call).
- `src/strelitzia/scoring/scorer.py:352` `run_scoring(..., urls=[...])` / `tasks.assess_task(url, "rescore")`: score one job.
- `src/strelitzia/database.py:111-240,278-370,545-575`: `init_db`, `_ensure_tables` (side tables, uuid4-hex ids, works on
  SQLite and Postgres), `_ALL_COLUMNS` (add columns here; `ensure_columns` migrates), `store_jobs`, `job_key(url)`.
  Every SQL must run on both backends (`?` placeholders are translated for Postgres in `db_pg.py`).
- `src/strelitzia/web/queries.py:17-60`: `SORT_COLUMNS`, `_LIST_COLUMNS`, `JobFilters`: add `source` filter and `next_interview` sort.
- `src/strelitzia/pipeline.py:45-130`: `STAGE_ORDER` and `_run_discover`; `cli.py` `run` resolves "all" via `_resolve_stages`.
- `dashboard/src/pages/JobPage.tsx:130-260` (current Notes textarea saves on blur), `dashboard/src/api.ts`, `App.tsx` routes,
  tests in `dashboard/src/__tests__/` with `fixtures.ts`. Rebuild the committed bundle in `src/strelitzia/web/static` (`scripts/validate.sh --full` builds it).
- Web tests: `tests/web/` (`conftest.py`, `web_helpers.py` give a TestClient on a temp SQLite DB). Tests are hermetic:
  mock the Playwright scrape and the Claude calls, never hit the network.
- Gotchas: `url` is the jobs primary key, so a pasted job without a link needs a synthetic url (Decision 3). LinkedIn
  and Indeed often return a login wall with HTTP 200, so "ok" status alone isn't success (Task 2's thin/blocked check).

## Tasks
### Task 1: Schema for source, contacts and interviews
**Runs:** unattended
**Files:** `src/strelitzia/database.py` (modify), `src/strelitzia/workspace.py` (new), `tests/test_workspace.py` (new)
**What:**
- `_ALL_COLUMNS`: `source TEXT` (`'manual'` | `'discovered'`; NULL means discovered, for old rows), `added_at TEXT`,
  `intake_status TEXT` (`queued` | `scraping` | `ok` | `needs_paste` | `error`), `intake_error TEXT`.
- `_ensure_tables`: `contacts (id TEXT PK, name TEXT NOT NULL, company TEXT, role TEXT, email TEXT, phone TEXT, linkedin_url TEXT,
  notes TEXT, created_at TEXT, updated_at TEXT)`; `job_contacts (job_url TEXT, contact_id TEXT, relation TEXT, PRIMARY KEY (job_url, contact_id))`
  where relation is `recruiter | hiring_manager | referral | interviewer | other`; `interviews (id TEXT PK, job_url TEXT, scheduled_at TEXT,
  stage TEXT, format TEXT, interviewers TEXT, prep_notes TEXT, debrief_notes TEXT, outcome TEXT, created_at TEXT, updated_at TEXT)`
  with an index on `(job_url, scheduled_at)`.
- `workspace.py`: plain functions over a `Connection`, returning dicts:
  `create_contact(conn, **fields) -> dict`, `update_contact(conn, id, **fields) -> dict`, `delete_contact(conn, id)`,
  `search_contacts(conn, q: str | None, limit=20) -> list[dict]` (name/company/email LIKE, case-insensitive),
  `link_contact(conn, job_url, contact_id, relation)`, `unlink_contact(conn, job_url, contact_id)`,
  `job_contacts(conn, job_url) -> list[dict]`, `contact_jobs(conn, contact_id) -> list[dict]` (key, title, company),
  `create_interview(conn, job_url, **fields)`, `update_interview(conn, id, **fields)`, `delete_interview(conn, id)`,
  `job_interviews(conn, job_url) -> list[dict]` (by `scheduled_at`), `next_interview_sql() -> str` (correlated subquery: earliest
  `scheduled_at >= now` for the row). Deleting a contact deletes its links; field names are allow-listed.
**Validate:** `pytest tests/test_workspace.py -q` → passes on SQLite (and with `-m pg` when a PG URL is set); `scripts/validate.sh` green.
**Status:** ✅ 2026-10-15 (Task 1 commit)

### Task 2: Intake backend: add a job by link or by paste
**Runs:** unattended
**Files:** `src/strelitzia/intake.py` (new), `src/strelitzia/web/tasks.py` (modify), `tests/test_intake.py` (new)
**What:**
- `normalize_url(raw: str) -> str`: strip whitespace, tracking params (`utm_*`, `refId`, `trackingId`, `trk`, `src`), fragments;
  LinkedIn `/jobs/view/<id>` and `currentJobId=<id>` both normalize to `https://www.linkedin.com/jobs/view/<id>/`.
- `add_job(conn, url: str | None, manual: ManualFields | None = None) -> tuple[dict, bool]` returns `(row, created)`.
  `ManualFields` is a dataclass: `title, company, location, description` (all optional). Rules:
  existing url → no new row; set `source='manual'` and `added_at` if not already manual; return `(row, False)`.
  New url → insert with `source='manual'`, `site` = hostname, `strategy='manual'`, `discovered_at = added_at = now`, `job_key`.
  No url → require `manual.description`; url = `manual://<uuid4 hex>` (Decision 3).
  If `manual.description` is given, it's saved as `full_description` (`detail_scraped_at = now`) and the scrape is skipped.
- `run_intake(url: str) -> None`, the work for the `intake` task kind:
  1. If there is no `full_description` yet and the url is http(s): open one Chromium page (reuse detail.py's browser
     setup), call `scrape_detail_page`, save `full_description` / `application_url` / `detail_error` like `_run_detail_scraper` does.
  2. `is_unreadable(result, url) -> str | None`: returns a reason when the scrape failed, the text is under 400 chars,
     or it looks like a login wall (LinkedIn/Indeed host plus phrases like "Sign in", "Join LinkedIn", "authwall" in
     the url or text). Then set `intake_status='needs_paste'`, `intake_error=<reason>`, and stop (Decision 2).
  3. `extract_one` + `_save_posting`; fill `title`/`company`/`location` from the extracted posting when they're empty.
  4. Score it via the same path as `assess_task(url, "rescore")`. Then `intake_status='ok'`.
  Any exception → `intake_status='error'`, `intake_error`, re-raise so the task shows failed. `LLMStopRun` (budget) after
  a successful scrape leaves the description saved and sets `intake_error` to the budget message.
- `save_pasted(conn, url, manual: ManualFields) -> None`: write the pasted description (and any non-empty title/company/location),
  clear `intake_error`, set `intake_status='queued'`; the caller enqueues `intake`, which now skips the scrape.
- `tasks.py`: add `intake` to `KINDS`, dispatch it to `intake.run_intake` in `generate_for_job`.
**Validate:** `pytest tests/test_intake.py -q` with a fake `scrape_detail_page` and fake `extract_one`/scoring: covers dedupe,
tracking-param stripping, LinkedIn id normalization, paste-only synthetic url, login-wall → `needs_paste`, thin text → `needs_paste`,
paste then re-intake → `ok` without scraping, budget stop keeps the description. `scripts/validate.sh` green.
**Status:** ❌

### Task 3: API routes for intake, contacts and interviews
**Runs:** unattended
**Files:** `src/strelitzia/web/app.py` (modify), `src/strelitzia/web/queries.py` (modify), `tests/web/test_workspace_api.py` (new)
**What:**
- `POST /app/api/jobs` body `{url?: str, title?, company?, location?, description?}` → 422 if neither url nor description.
  Runs `budget_stop(conn)` only when a new LLM step will run. Returns `{key, created: bool, task?: Task}`: 201 for new, 200 for an existing job (no task queued for existing jobs that are already scored).
- `PUT /app/api/jobs/{key}/description` body `{title?, company?, location?, description: str (min 200 chars)}` → `save_pasted` + enqueue `intake`; returns `{task}`.
- `job_detail` adds `source`, `added_at`, `intake_status`, `intake_error`, `contacts` (each with `relation`), `interviews`.
- Contacts: `GET /contacts?q=` (search, each with `job_count`), `POST /contacts`, `GET /contacts/{id}` (with linked jobs),
  `PATCH /contacts/{id}`, `DELETE /contacts/{id}`, `POST /jobs/{key}/contacts` `{contact_id, relation}` (or `{contact: {...new}, relation}`
  to create and link in one step), `DELETE /jobs/{key}/contacts/{contact_id}`.
- Interviews: `POST /jobs/{key}/interviews`, `PATCH /interviews/{id}`, `DELETE /interviews/{id}`. `scheduled_at` is an ISO datetime string, stored as given (UTC from the browser).
- `queries.py`: `JobFilters.source: list[Literal["manual","discovered"]]` (`discovered` matches NULL too); `_LIST_COLUMNS` gains `source`, `added_at`,
  `intake_status` and a computed `next_interview`; `SORT_COLUMNS["next_interview"]` (NULLs last both ways); manual rows are never hidden by
  `hide_flagged` (prefilter/dealbreaker flags apply to discovered rows only). `facets` adds source counts.
**Validate:** `pytest tests/web -q` → new tests cover each route, CSRF header required on writes, 404s, dedupe returning 200 with the same key,
source filter, `next_interview` sort with NULLs last, manual rows visible with `hide_flagged=true`. `scripts/validate.sh` green.
**Status:** ❌

### Task 4: Dashboard: Add job, paste fallback, contacts, interviews, Contacts page
**Runs:** unattended
**Files:** `dashboard/src/api.ts`, `dashboard/src/types.ts`, `dashboard/src/App.tsx`, `dashboard/src/pages/JobPage.tsx`,
`dashboard/src/pages/JobsPage.tsx` (or wherever the list lives), `dashboard/src/pages/ContactsPage.tsx` (new),
`dashboard/src/components/AddJobDialog.tsx` (new), `dashboard/src/components/ContactsPanel.tsx` (new),
`dashboard/src/components/InterviewsPanel.tsx` (new), `dashboard/src/components/PastePanel.tsx` (new), `dashboard/src/styles.css`,
tests under `dashboard/src/__tests__/` (new + `fixtures.ts`), rebuilt `src/strelitzia/web/static/**`
**What:**
- Header **Add job** button opens a dialog: URL field (focused) plus a "No link? Paste the description" disclosure with
  title/company/location/description fields. Submit → `POST /jobs` → navigate to `/jobs/<key>` (also for duplicates, with a
  "You already had this job" toast).
- Job page: while `intake_status` is `queued`/`scraping`, show "Reading the posting…" and poll the task like `generate` does.
  On `needs_paste`/`error`, show `PastePanel`: the reason in plain words, an **Open posting** link, and title/company/location/description
  fields prefilled from the row → `PUT /description`.
- Job page sections, in order under the fit card: **Notes** (existing textarea), **Contacts**, **Interviews**. All text saves on blur
  (plain text, line breaks kept, Sunrise tokens from `styles.css`).
  - Contacts panel: linked contacts as cards (name, relation chip, role, email `mailto:`, phone `tel:`, LinkedIn link, notes);
    **Add contact** opens a search-as-you-type picker over `GET /contacts?q=` with "Create '<text>'" at the bottom and a relation select; unlink with ×.
  - Interviews panel: list by date (stage, format, local date/time, interviewers); **Add interview** inline form; each has prep and
    debrief textareas and an outcome select (`pending | passed | rejected | withdrew`). Past interviews collapse.
- Jobs list: a "Next interview" column (local date, badge when within 7 days), sortable; a Source filter (Added by me / Discovered).
  Default the Source filter to "Added by me" when the facets show any manual job and the URL has no explicit source param.
- `/contacts` page (nav link next to Preferences): searchable table (name, company, role, email, # jobs); a row opens an edit
  panel with the linked jobs as links; Delete with confirm.
**Validate:** `cd dashboard && npm test -- --run` → new tests: AddJobDialog submit + duplicate navigation, PastePanel shown on `needs_paste`,
contact picker create-and-link, interview add/edit, Source filter and next-interview sort query strings. `scripts/validate.sh --full` green (bundle rebuilt and committed).
**Status:** ❌

### Task 5: Discovery off by default
**Runs:** unattended
**Files:** `src/strelitzia/pipeline.py` (modify), `src/strelitzia/cli.py` (modify), `README.md` (modify), tests next to the existing pipeline/CLI tests
**What:** `strelitzia run` (and `run all`) no longer includes `discover`; `--with-discover` adds it back; `strelitzia run discover`
still runs it explicitly. `enrich` keeps working on any rows without a description. `strelitzia doctor` and the `run` header say
"discovery: off (use --with-discover)". README Quick start leads with `strelitzia serve` + Add job; the Pipeline table marks Discover as optional.
**Validate:** `pytest -q -k "pipeline or cli"` → new test: `_resolve_stages(["all"])` excludes discover, with the flag includes it,
explicit `discover` still runs; `scripts/validate.sh` green.
**Status:** ❌

The live check on real postings (Greenhouse, Lever, Ashby, Workday, LinkedIn, Indeed) is ROADMAP manual step M21, after merge.

## Validation
- `scripts/validate.sh --full` passes.
- `strelitzia serve` on a temp DB, then
  `curl -s -X POST -H 'X-Strelitzia: 1' -H 'content-type: application/json' localhost:8765/app/api/jobs -d '{"title":"T","company":"C","description":"<250+ chars>"}'`
  → JSON with `"created": true` and a `key` starting the url `manual://`; a second POST with the same url returns `"created": false` and the same key.
- `curl -s 'localhost:8765/app/api/jobs?source=manual&sort=next_interview'` → only manual rows.

## Human test steps (preprod)
1. Open `https://strelitzia-preprod.engineerfamily.net/app/`, click **Add job**, paste a Greenhouse or Lever posting link. You land on the
   job page with "Reading the posting…", and within about a minute see the title, company, description and a fit score.
2. Add a LinkedIn job link. Either it fills in, or you see "We couldn't read this posting" with the paste form; paste the description,
   save, and the job gets extracted and scored.
3. Add the same link again: you land on the same job with "You already had this job".
4. On a job, **Add contact** → type a new name → Create, relation Recruiter, fill email and notes. Open a second job at the same
   company, **Add contact**, type the first letters: the same contact appears; link it.
5. Open **Contacts** in the nav: the contact shows 2 jobs; edit the phone number and see it on both job pages.
6. Add an interview 3 days from now with prep notes. Back on the jobs list, the job shows the next-interview badge; sort by Next interview.
7. Source filter: "Added by me" shows only the jobs you added.
8. On the VPS, `strelitzia run --dry-run` lists no discover stage.

## Decisions
1. After Add job, scrape + extract + score run automatically; resume and cover letter stay on-demand (about $0.01 per added job).
2. A failed, blocked or thin scrape still saves the row and shows a prefilled paste form; LinkedIn/Indeed are tried once, with no proxies or logins.
3. A job added without a link gets the synthetic url `manual://<uuid>` because `url` is the primary key everywhere.
4. Contacts are shared across jobs (many-to-many with a relation), managed from a job's picker and a Contacts page.
5. Interviews are structured rows; the jobs list shows and sorts by the next upcoming one; no calendar integration yet.
6. Re-adding an existing url opens that job and marks it `source='manual'`, so prefilter flags never hide it.
7. Notes stay plain text with autosave on blur; no markdown.
8. Discovery code stays but `strelitzia run` skips it unless `--with-discover`.
