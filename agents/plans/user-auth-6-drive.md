# Plan: User accounts, part 6: connect Google Drive per user
**Status:** ❌ Not started
**Risk:** review (OAuth tokens at rest)
**Issue:** #47
**Depends on:** user-auth-5-login

## Goal
Each user connects their own Google Drive from the dashboard (a "Connect Drive" button that asks only for `drive.file`).
Their token is stored encrypted in the database, and their files go to a "Strelitzia" folder in their own Drive. A user
who hasn't connected Drive still works: the workbench falls back to the clipboard as it does today.

## Context
- `src/strelitzia/storage/drive.py:72-120`: `load_credentials()` reads `config.GOOGLE_TOKEN_PATH`; `_save_token`;
  `authorize(client_secret)` (desktop loopback flow, CLI only). `:137-150` `DriveClient.__init__`/`from_credentials`.
  `:267-273` `drive_status()`.
- `src/strelitzia/storage/drive_layout.py:32-75`: `root_folder()` and the folder paths.
- `src/strelitzia/web/app.py:230-330`: `_drive_client()`, packet/result routes, `/claude/setup`, `/claude/docs`.
- `src/strelitzia/web/auth.py` (part 5): the OAuth helpers to reuse for a second flow.
- Env: `STRELITZIA_TOKEN_KEY` (a Fernet key). Add `cryptography>=42` to the `web` extra. Redirect URI
  `{PUBLIC_URL}/app/api/drive/callback`. The web client must be in the same Google Cloud project as the old desktop
  client, because `drive.file` only sees files created by that project (the user already set this up that way).
- Gotchas: request `access_type=offline&prompt=consent&include_granted_scopes=true` so Google returns a refresh token.
  Never log tokens. Migrate the owner: on start, if the owner has no stored token and `GOOGLE_TOKEN_PATH` exists,
  import it into the owner's row (the file stays; the CLI is admin-only after part 8).

## Tasks
### Task 1: per-user token store
**Runs:** unattended
**Files:** src/strelitzia/storage/drive.py (modify), src/strelitzia/database.py (modify), tests/test_drive_client.py (modify)
**What:** Table `user_drive (user_id TEXT PRIMARY KEY, token_enc TEXT, root_folder_id TEXT, connected_at TEXT)`.
`save_user_token(conn, user_id, creds)`, `load_user_credentials(conn, user_id)` (refreshes and re-saves; raises
`DriveNotConfigured` when missing), `disconnect(conn, user_id)`, `user_drive_status(conn, user_id) -> tuple[str, str]`,
`DriveClient.for_user(conn, user_id)`. Encrypt with Fernet from `TOKEN_KEY`; without the key, the store refuses to save.
The owner import of the legacy token file runs once.
**Validate:** `pytest tests/test_drive_client.py -q` → passes; the stored column never contains `refresh_token` in clear.
**Status:** ✅ 2026-10-16 (this commit)

### Task 2: connect flow and per-user client
**Runs:** unattended
**Files:** src/strelitzia/web/auth.py (modify), src/strelitzia/web/app.py (modify), tests/web/test_auth.py (modify), tests/web/test_workbench_api.py (modify)
**What:** `GET /app/api/drive/connect` (redirect with `drive.file`), `GET /app/api/drive/callback` (save, redirect to
`/app/claude-setup?drive=connected`), `POST /app/api/drive/disconnect`. `_drive_client(request)` uses the current
user's credentials; `/claude/setup` reports that user's status and a `connect_url`. The 409 message says
"Connect Google Drive on the Claude setup page" instead of the CLI command.
**Validate:** `pytest tests/web -q` → passes with Google mocked: connect stores a token for the session's user only.
**Status:** ✅ 2026-10-16 (this commit; also the setup page's Connect/Disconnect UI and DEPLOY.md/.env.example)

### Task 3: per-user root folder
**Runs:** unattended
**Files:** src/strelitzia/storage/drive_layout.py (modify), tests/test_drive_layout.py (modify)
**What:** Folder paths are unchanged (each user's Drive is separate); cache the root folder id per user in
`user_drive.root_folder_id` so a lookup by name happens once.
**Validate:** `pytest tests/test_drive_layout.py tests/test_drive_docs.py -q` → passes.
**Status:** ✅ 2026-10-16 (this commit; the cache helpers are in drive_layout, DriveClient uses them)

## Validation
- `scripts/validate.sh --full` passes.

## Human test steps (preprod)
1. Claude setup page on preprod: Connect Google Drive, approve; the page shows Drive connected. Open in Claude on a job
   creates the packet Doc in your Drive.

## Decisions
1. Keep Google Drive rather than object storage (user, 2026-10-16): files stay in the user's Drive, and claude.ai Projects can attach the Docs.
2. Drive is a separate, optional consent after sign-in.
