# Plan: Google Drive File Library
**Started:** 2026-10-02
**Status:** 🔄 In Progress

## Goal
Tailored resume and cover letter PDFs are moved to the user's Google Drive, filed as
`ApplyPilot/<Company>/<Role>/<YYYY-MM-DD>/`. ApplyPilot runs on both a laptop and a VPS, so Drive becomes the one place
the files live. A file is deleted locally only after its upload is verified (the MD5 checksums match). Re-running a
sync, or re-tailoring the same job on the same day from either machine, updates the existing Drive file instead of
creating a duplicate. Each job's Drive links are saved in the ApplyPilot database (next to `site`, which already records
the source board) and can be listed with `applypilot drive links`. When `applypilot apply` needs a PDF that is no longer
on disk, it downloads it from Drive.

## Success Criteria
1. `pytest tests/ -q` passes offline. All Drive code is tested against an in-memory fake Drive service, with no network.
2. A fresh DB and a migrated DB both have the columns `company`, `resume_drive_id`, `resume_drive_url`,
   `cover_letter_drive_id`, `cover_letter_drive_url` and `drive_synced_at` (`tests/test_database.py`).
3. Jobs found on Indeed and LinkedIn store the JobSpy `company` value. Workday and ATS-board jobs store their employer name.
4. `folder_path(job, kind)` returns `["ApplyPilot", <company>, <role>, <YYYY-MM-DD>]` with names cleaned for Drive,
   and is deterministic (unit tests).
5. Running `applypilot drive sync` twice in a row creates no new folders or files the second time (the fake service
   counts `create` calls).
6. Two different jobs with the same company, role and date get two separate files (unit test).
7. After a sync, the local PDFs are deleted, and only when the Drive `md5Checksum` matches. `--keep-local` keeps them.
   Every synced job has `resume_drive_url` set, and `applypilot drive links` prints the links (or writes CSV with `--csv`).
8. `build_prompt` for a job whose PDF was moved downloads it from Drive and attaches it (unit test with the fake).
9. Local: after `applypilot drive auth` and `applypilot drive sync`, on both the laptop and the VPS, the Drive folders
   show in drive.google.com and every link opens the right PDF.

## Task Chain

### Task 1: Store the company name for every job
**Runs:** cloud
**Files:** src/applypilot/database.py (modify), src/applypilot/discovery/jobspy.py (modify),
src/applypilot/discovery/workday.py (modify), src/applypilot/discovery/ats_boards.py (modify), tests/test_database.py (new),
tests/test_ats_boards.py (modify)
**What:** Add `"company": "TEXT"` to `_ALL_COLUMNS`. JobSpy inserts store `row["company"]`. Workday and ATS-board inserts
store their employer name (the value they already put in `site`). `ensure_columns` backfills `company = site` for
existing `workday_api` and `ats_*` rows when it adds the column. `job_company(job) -> str` returns `company`, else `site`
unless it is a job-board name, else `"Unknown company"`. Existing Indeed/LinkedIn rows stay without a company; the user
will reprocess old data.
**Acceptance:**
- `pytest tests/test_database.py tests/test_ats_boards.py -q` passes.
- `job_company({"site": "indeed", "company": None}) == "Unknown company"` and `job_company({"site": "NVIDIA"}) == "NVIDIA"`.
**Status:** ✅ Complete (2026-10-02)

### Task 2: Drive folder layout (pure functions)
**Runs:** cloud
**Files:** src/applypilot/storage/__init__.py (new), src/applypilot/storage/drive_layout.py (new), tests/test_drive_layout.py (new)
**What:** These functions have no Google imports:
- `clean_name(s, max_len=80) -> str` strips `/\:*?"<>|` and control characters, collapses whitespace, and returns `"Untitled"` if empty.
- `job_date(job, kind) -> str` returns the local date (`YYYY-MM-DD`) of `tailored_at` (for a resume) or `cover_letter_at`
  (for a cover letter). It falls back to `tailored_at`, then to today.
- `folder_path(job, kind) -> list[str]` returns `[root, clean_name(job_company(job)), clean_name(job["title"]), job_date(job, kind)]`.
  `root` defaults to `"ApplyPilot"` (env var `DRIVE_ROOT_FOLDER`).
- `job_key(url) -> str` returns `sha1(url)[:12]`.
- `file_name(job, kind, suffix="") -> str` returns `"<Company> - <Role> - Resume.pdf"` or `"... - Cover Letter.pdf"`.
- `@dataclass(frozen=True) class DriveTarget: folders: tuple[str, ...]; name: str; job_key: str; kind: str; site: str`
  and `drive_target(job, kind) -> DriveTarget`.
**Acceptance:**
- `pytest tests/test_drive_layout.py -q` passes, with cases for slashes in titles, empty company, very long titles,
  the date fallback chain, the `DRIVE_ROOT_FOLDER` override, and the same job giving the same target every time.
**Status:** ✅ Complete (2026-10-02)

### Task 3: Drive client with find-or-create, upsert and download
**Runs:** cloud
**Files:** src/applypilot/storage/drive.py (new), pyproject.toml (modify), tests/fake_drive.py (new), tests/test_drive_client.py (new)
**What:** Add the optional extra `drive = ["google-api-python-client>=2.0", "google-auth-oauthlib>=1.0"]`. Google imports
happen inside functions. `DriveClient(service)` provides:
- `ensure_folder_path(folders) -> str` finds or creates each folder (by name, parent, folder MIME type, not trashed),
  caches IDs, and returns the deepest folder's ID.
- `upsert_file(target, local_path, known_id=None) -> DriveFile(id, url, md5, created: bool)`. It reuses `known_id` if that
  file still exists and isn't trashed, else a file in the folder whose `appProperties` match `job_key` and `kind`. If one
  is found, it replaces the content with `files().update`. Otherwise it creates a new file with `appProperties`
  `{applypilot_job, applypilot_kind, applypilot_site}`. If a different job's file in the folder already has
  `target.name`, the new file gets ` (<job_key[:6]>)` added to its name.
- `download(file_id, dest)` writes the file's content to `dest`.
- `load_credentials()` reads `APP_DIR/google_token.json` and refreshes it if expired. If the token is missing, it
  raises `DriveNotConfigured` with a hint to run `applypilot drive auth`. `DriveClient.from_credentials()` builds the service.
- The OAuth scope is `drive.file`, so the app only sees files it created. Files created from the laptop and the VPS
  share one OAuth client, so both machines see each other's files.
`tests/fake_drive.py` is an in-memory fake of `files().list/create/update/get/get_media` that understands the `q`
filters used above and counts calls.
**Acceptance:**
- `pytest tests/test_drive_client.py -q` passes. Tests cover: folders are created once; a second upsert calls `update`;
  a stale or trashed `known_id` falls back to the `appProperties` lookup; a name collision gets the suffix; `download`
  round-trips the bytes.
- `python -c "import applypilot.storage.drive"` works without the `drive` extra installed.
**Status:** ✅ Complete (2026-10-02)

### Task 4: Move job files to Drive and save the links
**Runs:** cloud
**Files:** src/applypilot/database.py (modify), src/applypilot/storage/sync.py (new), tests/test_drive_sync.py (new)
**What:** Add `resume_drive_id`, `resume_drive_url`, `cover_letter_drive_id`, `cover_letter_drive_url` and `drive_synced_at`
(all TEXT) to `_ALL_COLUMNS`. `local_pdf(job, kind) -> Path | None` returns the expected local PDF path
(`tailored_resume_path` or `cover_letter_path` with a `.pdf` suffix). `sync_job(conn, client, job, keep_local=False) -> dict`
uploads each PDF that exists locally, writes its id and url to the row right away, and deletes the local PDF once the
Drive `md5Checksum` matches the local MD5. The `.txt`/`.json` working files stay, because the cover-letter and apply
stages read them. `run_drive_sync(conn=None, client=None, limit=None, keep_local=False) -> dict` picks jobs with a local
PDF on disk and returns `{"uploaded", "updated", "moved", "skipped", "errors"}`. An error on one job is logged and the
run continues. The DB keeps `tailored_resume_path`/`cover_letter_path`, so later stages know where to put a download.
**Acceptance:**
- `pytest tests/test_drive_sync.py tests/test_database.py -q` passes with the fake service and `tmp_path` PDFs.
- After a sync, the local PDF is gone and the row has its Drive id and url. A second run makes no `create` calls.
- A checksum mismatch keeps the local file and counts it as an error. `keep_local=True` keeps the file.
- A re-tailored PDF (new content) synced again calls `update` on the same file ID.
**Status:** ✅ Complete (2026-10-02)

### Task 5: Download moved PDFs when applying
**Runs:** cloud
**Files:** src/applypilot/storage/sync.py (modify), src/applypilot/apply/prompt.py (modify), tests/test_drive_sync.py (modify)
**What:** `ensure_local_pdf(job, kind, client=None) -> Path | None` returns the local PDF if it exists. Otherwise, if
the row has a Drive id and Drive is configured, it downloads the PDF to the local path and returns it. Otherwise it
returns None. `build_prompt` calls it for the resume and the cover letter before checking the files exist.
**Acceptance:**
- A test deletes the local PDF, sets `resume_drive_id` on a fake-backed file, and checks `ensure_local_pdf` restores the bytes.
- `pytest tests/test_prompt.py -q` still passes.
**Status:** ✅ Complete (2026-10-02)

### Task 6: `applypilot drive` CLI (auth, sync, links) and doctor check
**Runs:** cloud (code + CLI tests); live check: local (Task 8)
**Files:** src/applypilot/cli.py (modify), tests/test_cli_drive.py (new)
**What:** Add a `drive_app` sub-app, following the `template_app` pattern:
- `drive auth [--client-secret PATH] [--port 8765] [--no-browser]` runs `InstalledAppFlow.run_local_server` with the
  client secret (default `APP_DIR/google_client_secret.json`). It saves the token to `APP_DIR/google_token.json` with
  restricted permissions. `--no-browser` prints the URL and the SSH port-forward command for a headless VPS.
- `drive sync [--limit N] [--keep-local]` calls `run_drive_sync` and prints a summary table.
- `drive links [--company TEXT] [--csv PATH]` lists company, role, date, site, resume URL and cover letter URL.
- `doctor` gets a row saying one of: Drive extra not installed / not authorized / authorized.
**Acceptance:**
- `pytest tests/test_cli_drive.py -q` passes using `CliRunner` and a fake client: the `sync` summary counts, the
  `links --csv` columns, and the `doctor`-style status when the token is missing.
- `applypilot drive --help` lists `auth`, `sync` and `links`.
**Status:** 🟡 Cloud part done (2026-10-02), local steps pending

### Task 7: Move files automatically after the tailor and cover stages
**Runs:** cloud
**Files:** src/applypilot/config.py (modify), src/applypilot/pipeline.py (modify), tests/test_drive_sync.py (modify)
**What:** `drive_sync_enabled() -> bool` is true when `DRIVE_SYNC=1` and `google_token.json` exists. After the `tailor`
and `cover` stages, `pipeline.py` calls `run_drive_sync()` if it's enabled. Drive failures are logged as warnings and
never fail the stage.
**Acceptance:**
- With `DRIVE_SYNC=1` and a fake client, the sync runs after tailoring. If the fake raises an error, the stage still succeeds.
- With `DRIVE_SYNC` unset, `run_drive_sync` is never called.
**Status:** ❌ Not started

### Task 8: Google setup, first move of existing files, live check
**Runs:** local
**Files:** README.md (modify), .env.example (modify)
**What:** The README documents the Google Cloud setup: create a project, enable the Drive API, set up the OAuth consent
screen (External, add yourself as a test user, then publish to "In production" so refresh tokens don't expire after
7 days), and create an OAuth client of type "Desktop app". Download its JSON to `~/.applypilot/google_client_secret.json`
on each machine. Then run `applypilot drive auth` on each machine (on the VPS with `--no-browser` and an SSH tunnel),
then `applypilot drive sync`, then set `DRIVE_SYNC=1`.
**Acceptance:**
- `applypilot doctor` shows Drive as authorized on both machines.
- Drive shows `ApplyPilot/<Company>/<Role>/<date>/` for each tailored job, and a second `drive sync` reports `uploaded 0`.
- `sqlite3 ~/.applypilot/applypilot.db "select count(*) from jobs where tailored_resume_path is not null and resume_drive_url is null"` → 0.
**Status:** ❌ Not started

## Implementation Order
```
T1 (company) ──► T2 (layout) ──► T3 (client) ──► T4 (move + DB) ──┬─► T5 (apply download)
                                                                 ├─► T6 (CLI) ──► T8 (local setup)
                                                                 └─► T7 (pipeline hook)
```
1. Task 1: company column.
2. Task 2: folder layout and naming.
3. Task 3: Drive client and fake service.
4. Task 4: move and DB link columns.
5. Task 5: apply-stage download.
6. Task 6: CLI and doctor.
7. Task 7: pipeline hook.
8. Task 8: the user's Google setup, first move and live check.

## Key Design Decisions
1. OAuth user credentials with the `drive.file` scope, not a service account: service accounts have no storage quota
   in personal Drives, and `drive.file` limits the app to the files it created.
2. Drive is a move, not a mirror (user decision, 2026-10-02), because ApplyPilot runs on two machines and the files
   need one home. The local PDF is deleted only after the checksum matches, and `apply` downloads it back when needed.
3. Only PDFs move. The `.txt`/`.json`/`_JOB.txt`/`_REPORT.json` working files stay local, because the cover-letter and
   apply stages read them.
4. Folders are company → role → date (user decision, 2026-10-02). The source board stays in the DB `site` column and in
   the file's `applypilot_site` property.
5. Duplicates are avoided by identity, not just by name. Each file carries `appProperties` (a job key from the URL hash,
   plus the kind), and the DB stores the file ID, so re-syncs from either machine update the same file. Drive keeps
   earlier versions in the file's history.
6. Two different jobs with the same company, role and date share a folder, and the second file gets a 6-character suffix.
7. Drive is an optional extra (`.[drive]`) with lazy imports, so CI and users who don't use Drive install nothing new.
8. Drive errors never fail a pipeline stage. The next `drive sync` picks up anything that was missed.

## Historical Record
- 2026-10-02: Plan created (user request: move the tailored file library to Google Drive, organized by company/role/date, with links stored in the DB).
- 2026-10-02: The user chose company/role/date folders (site stays in the DB), a move instead of a mirror (laptop and VPS
  both run ApplyPilot), and asked for the work to be implemented in this session.
- 2026-10-02: Task 1 done. `company` column, `job_company()`, inserts from JobSpy/Workday/ATS, backfill for employer rows.
- 2026-10-02: Task 2 done. `storage/drive_layout.py`: company/role/date folders, file names, job key.
- 2026-10-02: Task 3 done. `storage/drive.py` (OAuth token, find-or-create folders, upsert by job key, download), `tests/fake_drive.py`, `[drive]` extra.
- 2026-10-02: Task 4 done. `storage/sync.py`: `run_drive_sync` moves PDFs (deletes local copies after the MD5 matches), Drive id/url columns.
- 2026-10-02: Task 5 done. `ensure_local_pdf`; `build_prompt` downloads moved resume and cover letter PDFs; launcher selects the Drive ids.
- 2026-10-02: Task 6 done. `applypilot drive auth|sync|links`, doctor row. Fixed `_jobs_with_files` resetting the shared connection's row_factory. Live check is Task 8.
