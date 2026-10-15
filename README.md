# Strelitzia

**Bring your search into the light.**

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-green.svg)](LICENSE)

Strelitzia is the workspace between finding a job (LinkedIn, Indeed, a company's site, anywhere) and applying for it.
Paste a job's link into the dashboard: Strelitzia reads the posting, scores it against your resume with AI, tailors your
resume and writes a cover letter on request, and keeps your notes, contacts and interviews for each job. It can also
crawl job boards for you (optional). **It never submits applications for you.** You decide where to apply, and you
click submit.

### The name

*Strelitzia reginae*, the bird-of-paradise flower, stands for freedom, joy, royalty and new beginnings. It only blooms
in bright light, the way a job search only works with exposure: the right roles in front of you, and you in front of
the right people. The flame-orange and royal-blue flower is the dashboard's logo and theme ("Sunrise").

---

## Quick start

```bash
git clone https://github.com/SiddharthEngineer/Strelitzia.git
cd Strelitzia
uv pip install -e ".[web,jobspy]"
playwright install chromium
strelitzia init          # one-time setup: resume, profile, searches, API key
strelitzia doctor        # check your setup
strelitzia serve         # the dashboard at http://127.0.0.1:8765/app/
```

In the dashboard, click **Add job** and paste a posting's link. The job appears at once; within about a minute it has
its title, company, salary and fit score. If the page can't be read (LinkedIn and Indeed often show a sign-in page), the
job page asks you to paste the description instead.

`strelitzia run` processes the jobs in bulk (enrich > score > tailor > cover letters > extract). It no longer crawls job
boards unless you ask: `strelitzia run --with-discover` (or `strelitzia run discover`).

For development: `uv pip install -e ".[dev,web,jobspy]"`, then `scripts/validate.sh`.

---

## Requirements

| Component | Required for | Details |
|-----------|-------------|---------|
| Python 3.11+ | Everything | Core runtime |
| Chromium (Playwright) | Discovery, enrichment, PDFs | `playwright install chromium` |
| Claude API key | Scoring, tailoring, cover letters, extraction | Pay as you go, capped by a monthly budget (see [Cost and budget](#cost-and-budget)). Create one at [console.anthropic.com](https://console.anthropic.com) |
| Node.js 22 | Only to change the dashboard UI | The built UI ships in the package |

---

## Pipeline

| Stage | What happens |
|-------|-------------|
| **1. Discover** (optional) | Off by default: jobs come from the dashboard's **Add job**. With `--with-discover` (or `strelitzia run discover`), searches Indeed and LinkedIn (via JobSpy), Greenhouse/Lever/Ashby company boards, 48 Workday portals, and 30 direct career sites. Glassdoor and ZipRecruiter are optional and need a proxy (see [Job sources](#job-sources)) |
| **2. Enrich** | Fetches full job descriptions via JSON-LD, CSS selectors, or AI-powered extraction |
| **3. Score** | Every job gets a 0-100 fit score: your Preferences page (salary, location, work mode, ...) scored in code, plus Claude's assessment of skills, experience and domain fit against your resume |
| **4. Tailor** | AI writes per-job bullets from your content library and renders them through your own resume template (or rewrites `resume.txt` without a library). Every number is checked against the project facts it cites. Never fabricates |
| **5. Cover letter** | AI writes a targeted cover letter per job, citing the same projects as that job's resume |
| **6. Track** | In the dashboard you open the posting, apply yourself, and mark the job In progress, Submitted, Rejected or Heard back. Each job keeps notes, contacts (shared across jobs, on the Contacts page) and interviews with prep and debrief notes |

**Extract** (`strelitzia run extract`) parses each enriched posting into structured fields: role category, seniority,
employment type, work mode, locations, salary range, posted date and deadline, responsibilities, required and preferred
qualifications, skills, education, years of experience, visa sponsorship and more. It sends one posting per request,
all in one Message Batch (half price), and saves each result as it arrives. The full record is stored in `details_json`,
and the filter fields get their own columns. Until a job has been extracted, `strelitzia classify` fills its role
category, work mode and location from keyword rules (no LLM). In a full `strelitzia run`, extract runs after the cover
stage, so scoring gets the monthly budget first.

Each stage is independent. Run them all or pick what you need.

---

## Configuration

Everything lives in the data directory, `~/.strelitzia` (created by `strelitzia init`; set `STRELITZIA_DIR` to move it).
`.env` holds only secrets and per-host settings (API key, monthly budget, model overrides, database URL); anything
that isn't secret lives in a YAML file:

| File | Purpose |
|------|---------|
| `profile.json` | Contact info, work authorization, compensation, experience, skills, resume facts |
| `searches.yaml` | Job search queries, target titles, locations, boards |
| `.env` | Secrets and per-host settings: `ANTHROPIC_API_KEY`, `LLM_MONTHLY_BUDGET_USD`, `STRELITZIA_DATABASE_URL` |
| `config/employers.yaml` | Workday employer registry (48 preconfigured) |
| `config/ats_boards.yaml` | Greenhouse / Lever / Ashby company boards (23 preconfigured) |
| `config/sites.yaml` | Direct career sites (30+), blocked sites, base URLs |
| `content_library.md` | Optional: raw facts per project, grouped by role. When present, tailoring uses it by default |
| `resume_template.html` | Optional: your resume layout (Jinja2), from `strelitzia template init` |
| `resume_fixed.yaml` | Optional: header, education and per-role title/company/dates, copied verbatim into every tailored resume |

### Resume template (content-library tailoring)

With a `content_library.md`, each tailored resume keeps your own resume's look and shape. Only the bullets and the skill order change per job.

1. `strelitzia template init` writes `resume_fixed.yaml` from `resume.txt` (header, education, and each role's title, company, dates and tagline, verbatim) and a starter `resume_template.html`.
2. Edit `resume_template.html` to match your resume (fonts, spacing, section rules). It's Jinja2 with `name`, `contact`, `roles`, `education` and `skills`. See `src/strelitzia/config/resume_template.default.html` for the variables.
3. `strelitzia template preview` renders your base resume through the template to `template_preview.pdf`. Compare it with your resume, and iterate until they match.

When tailoring, the LLM picks bullets per role (between your base resume's bullet count and two fewer, so the shape stays the same), and each bullet cites the library projects it draws on. Every number in a bullet must appear in those projects' facts. Output per job: `<job>.pdf` (always one page; bullets above a role's minimum are dropped if it overflows), `<job>.json` (bullets with their `project_ids`), and `<job>.txt`. On Linux, install `fonts-liberation` so Chromium has a Times New Roman–compatible font.

### Cost and budget

Every LLM call goes to the Claude API with your own `ANTHROPIC_API_KEY`, through the official `anthropic` SDK. Each
stage has its own model and effort ([`config/llm_models.yaml`](src/strelitzia/config/llm_models.yaml), which also
holds the prices used for spend tracking):

| Stage | Default | Override |
|---|---|---|
| SmartExtract strategy + API judge, enrichment fallback | `claude-haiku-5-5`, effort `low` | `LLM_DISCOVERY_MODEL`, `LLM_DISCOVERY_EFFORT` |
| Posting extraction | `claude-haiku-5-5`, effort `low` | `LLM_EXTRACT_MODEL`, `LLM_EXTRACT_EFFORT` |
| Scoring | `claude-haiku-5-5`, effort `low` | `LLM_SCORING_MODEL`, `LLM_SCORING_EFFORT` |
| Resume tailoring | `claude-sonnet-5-5`, effort `medium` | `LLM_TAILOR_MODEL`, `LLM_TAILOR_EFFORT` |
| Cover letters | `claude-sonnet-5-5`, effort `medium` | `LLM_COVER_MODEL`, `LLM_COVER_EFFORT` |

An override that isn't a `claude-*` model id (say, a leftover Gemini setting) is ignored with a warning, and
`strelitzia init` removes old-provider settings from `.env`.

- **Batches**: `run score` and `run extract` send their jobs as one Message Batch at 50% off, and print the estimated
  cost first. Most batches finish within an hour (24 hours at most). The batch id is stored in the database, so if a
  run is killed while waiting, the next batch run of that stage collects the results instead of paying again. Runs of
  5 jobs or fewer, and runs with `--sync`, send one request at a time at full price.
- **Prompt caching**: the system prompt (the extraction rules; the scoring rubric plus your resume) is cached, so each
  request after the first pays about a tenth for it.
- **Monthly budget**: `LLM_MONTHLY_BUDGET_USD` (default 20 in `.env.example`) stops a run before month-to-date spend
  would pass it: a batch whose estimate doesn't fit isn't submitted, and sync requests stop at the limit. The stage
  ends with `stopped: monthly LLM budget`, charges no job an attempt, and leaves the rest for later. A batch counts at
  its estimate until its results are collected. Unset or 0 means no limit.
- **Spend tracking**: every response is recorded in the `llm_usage` table with its tokens and dollar cost.
  `strelitzia doctor` shows month-to-date spend against the budget and any batches still open.
- **Scoring pre-filter**: jobs whose title shares no word with your search queries or `target_role` get `fit_score` 1
  without an LLM call. Use `--no-prefilter` to score them anyway.
- **Structured output**: scoring, extraction and tailoring ask for schema-constrained JSON, so malformed replies don't
  cost retries.
- **`--validation lenient`** skips the LLM judge, saving one call per attempt. It's the default for content-library
  tailoring, whose bullets are already checked mechanically (project ids, numbers, bullet ranges). Pass
  `--validation normal` to bring the judge back.
- **Errors**: the SDK retries rate limits and server errors with backoff. A bad key or an API that stays overloaded
  stops the stage (`stopped: Claude API key missing or rejected` / `stopped: Claude API overloaded`) without charging
  the job an attempt.

**Running Strelitzia next to Claude Code:** keep `ANTHROPIC_API_KEY` in `~/.strelitzia/.env` only, never in your
shell profile or in the environment of an unattended `claude -p` runner (such as `scripts/agent/run.sh`). In `-p`
mode Claude Code prefers `ANTHROPIC_API_KEY` over its subscription login, so the runner would bill the API instead.

### Job sources

| Source | Default | Notes |
|---|---|---|
| Indeed, LinkedIn | on | Via JobSpy, one request per board per search |
| Greenhouse, Lever, Ashby | on | Public JSON job-board APIs. Add companies to `config/ats_boards.yaml` |
| Workday | on | `config/employers.yaml` |
| Direct career sites | on | `config/sites.yaml` (SmartExtract) |
| Glassdoor, ZipRecruiter | off | Cloudflare returns HTTP 403 without a residential proxy. Set `PROXY` (or `proxy:` in `searches.yaml`) to enable them |
| Google Jobs | off | JobSpy currently returns no Google results. `defaults.allow_unsupported: true` tries it anyway |

`strelitzia discover --probe` checks every source with a tiny search (no DB writes) and prints `ok`, `empty`, `blocked` or `error` for each. During a crawl, a board that answers HTTP 403/429 is skipped for the rest of the run, and a board that returns 0 results is skipped after `site_fail_threshold` searches in a row (default 3, minimum 2).

### Google Drive file library

Strelitzia can move tailored resume and cover letter PDFs to your Google Drive, filed as
`Strelitzia/<Company>/<Role>/<YYYY-MM-DD>/` (set `DRIVE_ROOT_FOLDER` to use another top-level folder). This is useful
when you run Strelitzia on more than one machine. Each job's Drive links are saved in the database (`resume_drive_url`,
`cover_letter_drive_url`). A local PDF is deleted only after Drive confirms the same checksum. Re-syncing, or
re-tailoring the same job on the same day, updates the existing Drive file (Drive keeps the old version in its history)
instead of adding a copy. Strelitzia asks for the `drive.file` permission, so it can only see the files it created.

One-time Google setup (about 5 minutes, free):

1. Go to [console.cloud.google.com](https://console.cloud.google.com), create a project (e.g. "Strelitzia"), then open
   **APIs & Services → Library**, search for **Google Drive API** and click **Enable**.
2. Open **Google Auth Platform** (called **OAuth consent screen** in older consoles) and click **Get started**. Use any
   app name, your email as the support and contact email, and **External** as the audience.
3. Under **Audience**, click **Publish app** and confirm. Without this, Google treats the app as "Testing" and your
   sign-in expires every 7 days. `drive.file` isn't a sensitive permission, so no Google review is needed. You'll see an
   "unverified app" warning when you sign in; click **Advanced → Go to (your app name)**.
4. Under **Clients**, click **Create client**, choose **Desktop app**, then **Download JSON**. Save it as
   `~/.strelitzia/google_client_secret.json` on every machine that runs Strelitzia (use the same client everywhere so
   all machines see the same files). Treat this file like a password: don't commit it.

Then, on each machine:

```bash
pip install -e ".[drive]"
strelitzia drive auth                    # opens a browser to sign in
strelitzia drive sync                    # move existing PDFs to Drive
strelitzia drive links --csv links.csv   # list the saved links
```

On a headless server, run `strelitzia drive auth --no-browser`. From your laptop, open a tunnel first with
`ssh -N -L 8765:localhost:8765 <user>@<server>`, then open the printed URL in your laptop's browser. To move new PDFs
automatically after the tailor and cover stages, set `DRIVE_SYNC=1` in `.env`. `strelitzia doctor` shows whether Drive
is authorized.

### Database

Jobs are stored in a SQLite file, `~/.strelitzia/strelitzia.db`, by default. To share one job store between
machines or processes (the CLI, cron jobs, the dashboard), point Strelitzia at Postgres instead:

```bash
pip install -e ".[postgres]"
strelitzia db migrate --from ~/.strelitzia/strelitzia.db --to postgresql://user:pw@host:5432/strelitzia
strelitzia db verify  --from ~/.strelitzia/strelitzia.db --to postgresql://user:pw@host:5432/strelitzia
echo 'STRELITZIA_DATABASE_URL=postgresql://user:pw@host:5432/strelitzia' >> .env
strelitzia doctor   # Database: postgresql (user@host), N jobs
```

`db migrate` can be rerun safely: it copies only the jobs that aren't in Postgres yet, and it backs the SQLite file up
to `<name>.db.bak-<date>` first, without changing it. To switch back to SQLite, unset `STRELITZIA_DATABASE_URL`.

### Dashboard

`strelitzia serve` runs the job dashboard at `http://127.0.0.1:8765/app/`: a filterable, sortable jobs table and a detail
page per job, with buttons to generate the resume and cover letter and to track the application's status.
The built UI ships in the package (`src/strelitzia/web/static/`), so Node is only needed to change it:

```bash
pip install -e ".[web]"
strelitzia serve --port 8765 [--web-dir path/to/built/ui]
curl localhost:8765/app/api/health          # {"ok": true, "db": "postgresql", ...}
```

- `GET /app/api/jobs` lists jobs. Filters: `status` (repeatable: active, inactive, in_progress, submitted, rejected,
  heard_back, no_response), `role`, `work_mode`, `location`, `found_from`/`found_to`, `due_from`/`due_to`,
  `score_min`/`score_max`, `source` (manual, discovered) and `q`. Sort with `sort=-fit_score` (or `discovered_at`,
  `deadline`, `next_interview`). Page with `page` and `page_size` (at most 200).
  `GET /app/api/facets` returns the counts for the filter dropdowns and the hero banner.
- `GET /app/api/jobs/{key}` returns one job with its extracted details, status history, links, contacts and interviews.
- Add job: `POST /app/api/jobs {"url": "..."}` (or `{"title", "company", "location", "description"}` without a link)
  queues an `intake` task that reads, extracts and scores the posting; a link you already have returns `"created": false`
  and the same key. `PUT /app/api/jobs/{key}/description` saves a pasted description when the page couldn't be read.
- Contacts: `GET/POST /app/api/contacts`, `GET/PATCH/DELETE /app/api/contacts/{id}`, `POST /app/api/jobs/{key}/contacts
  {"contact_id" | "contact", "relation"}`, `DELETE /app/api/jobs/{key}/contacts/{id}`. Interviews:
  `POST /app/api/jobs/{key}/interviews`, `PATCH/DELETE /app/api/interviews/{id}`.
- Status: `POST /app/api/jobs/{key}/status {"status": "submitted"}` (or `rejected`, `heard_back`, `in_progress`, `null`
  to reset). Active and Inactive are derived from the deadline. `PATCH /app/api/jobs/{key}` edits `submitted_at` and `notes`.
- `POST /app/api/jobs/{key}/generate {"resume": true, "cover": true}` queues a tailored resume and/or cover letter
  for that job, then moves the PDFs to Drive when it's authorized. A cover letter is written from the tailored
  resume's content-library evidence, so asking for only a cover letter tailors the resume first if there isn't one.
  Poll `GET /app/api/tasks/{id}`. Tasks run one at a time on a background thread.

Every POST/PATCH needs the header `X-Strelitzia: 1`. Put the server behind a reverse proxy with authentication;
it has no login of its own.

**UI development** (React + TypeScript + Vite, in `dashboard/`):

```bash
cd dashboard && npm ci
npm run dev      # http://localhost:5173/app/ , proxies /app/api to strelitzia serve on :8765
npm test         # Vitest (including the theme contrast and reduced-motion checks)
npm run build    # writes src/strelitzia/web/static/ (commit it)
```

The filters and sort live in the URL, so a view like "Submitted, no response, Data Science" can be bookmarked.
The theme tokens are at the top of `dashboard/src/styles.css` (light, and indigo night under
`prefers-color-scheme: dark`). Every animation sits inside `@media (prefers-reduced-motion: no-preference)`.

**Docker:** the `Dockerfile` builds the dashboard image (Playwright's Python image + Chromium, fonts-liberation,
`.[web,postgres,drive]`). It contains no secrets or user data. At runtime, mount the data dir at the same path as on
the host, run as the user that owns it, and mount the `.env` with the Claude API key at `/app/.env`:

```bash
docker build -t strelitzia-dashboard .
docker run --rm --user "$(id -u):$(id -g)" -e HOME=$HOME -v $HOME/.strelitzia:$HOME/.strelitzia \
  -v $PWD/.env:/app/.env:ro -p 127.0.0.1:8000:8000 strelitzia-dashboard
```

---

## CLI reference

```
strelitzia init                          # First-time setup wizard
strelitzia doctor                        # Verify setup, diagnose missing requirements
strelitzia run [stages...]               # Run pipeline stages (or 'all', which skips discover)
strelitzia run --with-discover           # Also crawl the job boards (discovery is off by default)
strelitzia run --workers 4               # Parallel discovery/enrichment
strelitzia run --stream                  # Concurrent stages (streaming mode)
strelitzia run --min-score 80            # Override the fit score threshold (0-100, default 70)
strelitzia run --dry-run                 # Preview without executing
strelitzia run --source content-library  # Tailor from content_library.md (default when it exists)
strelitzia run --source resume           # Tailor by rewriting resume.txt
strelitzia run tailor --limit 3          # Cap the tailor/cover stages at N jobs
strelitzia run score --limit 20          # Score at most N jobs (a Message Batch; each score is saved as it arrives)
strelitzia run score --sync              # One request per job: full price, no batch wait
strelitzia run extract --limit 100       # Parse N postings into structured fields (a Message Batch; stops at the budget)
strelitzia run extract --limit 3 --sync  # Parse a few postings right away
strelitzia classify                      # Keyword role category / work mode / location for jobs not yet extracted (no LLM)
strelitzia template init                 # Create resume_fixed.yaml + resume_template.html from resume.txt
strelitzia template preview              # Render your base resume through the template (template_preview.pdf)
strelitzia run --validation normal       # Run the LLM judge (default: lenient for content-library tailoring, else normal)
strelitzia run score --reset-errors      # Re-queue jobs whose scoring failed with an LLM error
strelitzia run score --no-prefilter      # Also LLM-score jobs whose title shares no word with your queries/target role
strelitzia discover --probe              # Check each job board (ok/empty/blocked/error) in seconds, no DB writes
strelitzia drive auth [--no-browser]     # Sign in to Google Drive (see "Google Drive file library")
strelitzia drive sync [--keep-local]     # Move tailored resume/cover letter PDFs to Drive, save links in the DB
strelitzia drive links [--csv FILE]      # List each job's Drive links
strelitzia status                        # Pipeline statistics
strelitzia db migrate [--from F --to URL]  # Copy the SQLite jobs into Postgres (rerunnable)
strelitzia db verify  [--from F --to URL]  # Compare counts between the two
strelitzia dashboard                     # Open the static HTML report
strelitzia serve [--port 8765]           # Dashboard API + UI at /app/
```

---

## Migrating from ApplyPilot

Strelitzia 0.4 is the renamed ApplyPilot fork, with the auto-apply stage removed. Existing installs keep working with
no config change: every old name below is still read as a deprecated fallback through the 0.4.x releases.

1. Reinstall under the new name: `pip uninstall applypilot`, then `pip install -e ".[web,jobspy]"` (add your extras).
2. Use `strelitzia` instead of `applypilot`. The `applypilot` command still works and prints a deprecation notice.
3. Optional: move your data dir with `mv ~/.applypilot ~/.strelitzia`. Until you do, the old dir is used (Strelitzia
   logs that once). If database rows hold absolute paths under `~/.applypilot` (tailored resumes, cover letters),
   leave a symlink: `ln -s ~/.strelitzia ~/.applypilot`. An existing `applypilot.db` is used when there's no
   `strelitzia.db`.
4. Optional: rename the env vars. `STRELITZIA_<NAME>` wins; `APPLYPILOT_<NAME>` is read when it's unset. A variable
   set in the real process environment (shell, docker compose) beats one read from a `.env` file, whichever name it uses.

| Old (deprecated) | New |
|---|---|
| `applypilot` command | `strelitzia` |
| `APPLYPILOT_DIR` | `STRELITZIA_DIR` |
| `APPLYPILOT_DATABASE_URL` | `STRELITZIA_DATABASE_URL` |
| `APPLYPILOT_WEB_DIR` | `STRELITZIA_WEB_DIR` |
| `APPLYPILOT_TEST_DATABASE_URL` | `STRELITZIA_TEST_DATABASE_URL` |
| `~/.applypilot/`, `applypilot.db` | `~/.strelitzia/`, `strelitzia.db` |
| `X-ApplyPilot: 1` header | `X-Strelitzia: 1` |
| Drive folder `ApplyPilot/` | `Strelitzia/` (rename the folder in Drive, or set `DRIVE_ROOT_FOLDER=ApplyPilot`) |

Removed: `applypilot apply` and everything it needed (Claude Code/OpenCode CLI apply engines, Chrome detection,
CapSolver, site passwords, `CAPSOLVER_API_KEY`, `CHROME_PATH`). Jobs that auto-apply already marked as applied keep
that status, and you track new applications in the dashboard.

---

## Development

```
Strelitzia/
├── src/strelitzia/       # The package: cli.py, pipeline.py, discovery/, enrichment/, scoring/, storage/, web/, wizard/
├── dashboard/            # React + Vite dashboard (builds into src/strelitzia/web/static)
├── agents/               # AI-agent workflow: ROADMAP.md, DEPLOY.md, plans/ (done/ = archive)
├── .claude/commands/     # PIV slash commands: /plan /implement /validate /ship /rca /schedule-build
├── scripts/              # validate.sh, the unattended runner (agent/), fixture and seed scripts
└── tests/                # Hermetic by default (conftest.py blocks network)
```

### Testing

```bash
scripts/validate.sh                                         # ruff + hermetic pytest
scripts/validate.sh --full                                  # + dashboard tests/build + docker build
pytest -m live --run-live -v                                # Live network tests
pytest -m llm --run-llm -v                                  # Real Claude API calls (requires ANTHROPIC_API_KEY, ~$0.05)
STRELITZIA_TEST_DATABASE_URL=postgresql://…/strelitzia_test pytest -m pg -q  # Postgres backend (throwaway DB)
python scripts/qc.py <plan-slug>                            # QC for a plan's PR: all tiers + its success criteria
```

Unit tests are hermetic: `tests/conftest.py` points `STRELITZIA_DIR` at a temp dir, and any test not marked
`live` or `llm` that opens a non-localhost connection fails with `NetworkAccessBlocked`. Mock the call or mark the test.

Recorded responses in `tests/data/` are committed, scrubbed JSON samples (JobSpy rows, a Workday page, a Claude
scoring reply) that unit tests replay without network. Refresh them (needs network + `ANTHROPIC_API_KEY`), then check
for personal data before committing:
```bash
python scripts/capture_fixtures.py --out tests/data --scrub --sites indeed,linkedin
grep -ri "<your email or name>" tests/data          # expect no matches
```

### Contributing

1. Check [issues](https://github.com/SiddharthEngineer/Strelitzia/issues) to avoid duplicating work.
2. Create a feature branch from `trunk`, with tests.
3. Run `scripts/validate.sh --full`.
4. Open a PR: one change per PR, CI must pass.

**Adding a Workday employer:** add an entry to `config/employers.yaml`, test with `strelitzia discover --employer "Name"`.
**Adding a career site:** add an entry to `config/sites.yaml` with CSS selectors, test with `strelitzia discover --site "Name"`.

### Planned work and AI agents

Planned work is tracked in [`agents/ROADMAP.md`](agents/ROADMAP.md), with one plan per change in `agents/plans/`.
Changes follow a Plan → Implement → Validate loop driven by the Claude Code commands in `.claude/commands/`
(see [`AGENTS.md`](AGENTS.md)). Plans can also be queued for unattended runs on a server (`/schedule-build`); see
[`scripts/agent/README.md`](scripts/agent/README.md).

---

## Credits and license

Strelitzia is a fork of [ApplyPilot](https://github.com/Pickle-Pixel/ApplyPilot), originally created by
[Pickle-Pixel](https://github.com/Pickle-Pixel) (Henry Muhiar). Thank you for the original project.

Licensed under the [GNU Affero General Public License v3.0](LICENSE), like the original. You are free to use, modify
and distribute it. If you run a modified version as a network service, you must offer its users the source under the
same license (the dashboard footer links to it). See [`NOTICE`](NOTICE) for copyright and modification notices.
The Strelitzia flower artwork (`dashboard/src/components/Flower.tsx`, `dashboard/public/favicon.svg`) is original and
also available under the MIT license.
