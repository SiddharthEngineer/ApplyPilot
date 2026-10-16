# Plan: User accounts, part 7: sign-in screens and the Users admin page
**Status:** ❌ Not started
**Risk:** review (new pages and owner-only admin API)
**Issue:** #47
**Depends on:** user-auth-6-drive

## Goal
The dashboard shows a "Sign in with Google" screen when login is on and there's no session, a "not invited" message for
uninvited accounts, and the signed-in user's name with a Sign out button in the top bar. The Claude setup page has
Connect/Disconnect Google Drive. The owner gets a Users page to invite or remove emails, see each user's spend this month,
and change their cap.

## Context
- `dashboard/src/App.tsx:1-55`: routes and the top bar (`header.topbar`, `nav`).
- `dashboard/src/api.ts:18-45`: `request<T>()` (adds `X-Strelitzia: 1`). A 401 should flip the app to the sign-in screen.
- `dashboard/src/pages/ClaudeSetupPage.tsx`: today shows Drive status from `GET /claude/setup` (`drive`, `drive_detail`; part 6 adds `connect_url`).
- `src/strelitzia/web/auth.py` (part 5): `GET /auth/me`, login and logout routes. `src/strelitzia/users.py`: invite, set_status, set_cap.
- `src/strelitzia/llm.py` (part 3): `user_month_spend(conn, user_id)`.
- Theme tokens: `dashboard/src/styles.css`. Rebuild the bundle into `src/strelitzia/web/static` (`npm run build`, committed).

## Tasks
### Task 1: admin API
**Runs:** unattended
**Files:** src/strelitzia/web/admin.py (new), src/strelitzia/web/app.py (modify), tests/web/test_admin_api.py (new)
**What:** Owner-only router (403 for others): `GET /app/api/admin/users` (email, name, role, status, cap, month spend,
last login), `POST /app/api/admin/users` `{email, monthly_cap_usd?}` (invite), `PATCH /app/api/admin/users/{id}`
`{status?, monthly_cap_usd?}`, `DELETE` = set status `disabled` (never deletes data). The owner can't disable themself.
**Validate:** `pytest tests/web/test_admin_api.py -q` → passes.
**Status:** ✅ 2026-10-16 (1fa72db)

### Task 2: sign-in and the user menu
**Runs:** unattended
**Files:** dashboard/src/App.tsx (modify), dashboard/src/api.ts (modify), dashboard/src/pages/SignInPage.tsx (new), dashboard/src/types.ts (modify)
**What:** On load, `GET /auth/me`. If enabled and no user: render `SignInPage` (Flower mark, "Sign in with Google" link to
`/app/api/auth/login`, and the not-invited message when `?auth=not_invited`). A 401 from any request shows the same page.
The top bar shows the name and Sign out (POST logout, then reload). When auth is off, nothing changes.
**Validate:** `cd dashboard && npm test -- --run` → passes, with a new test for the sign-in screen and 401 handling.
**Status:** ✅ 2026-10-16 (8baf875)

### Task 3: Drive connect and the Users page
**Runs:** unattended
**Files:** dashboard/src/pages/ClaudeSetupPage.tsx (modify), dashboard/src/pages/UsersPage.tsx (new), dashboard/src/__tests__/UsersPage.test.tsx (new), src/strelitzia/web/static (rebuild)
**What:** Setup page: "Connect Google Drive" (link to `connect_url`) or "Connected · Disconnect". Users page at `/users`,
in the nav only for the owner: a table of users with spend and cap (editable inline), an invite form, Disable/Enable.
**Validate:** `cd dashboard && npm test -- --run && npm run build` → passes and writes the bundle.
**Status:** ✅ 2026-10-16 (Task 3 commit)

## Validation
- `scripts/validate.sh --full` passes.

## Human test steps (preprod)
1. Sign out, then sign in with Google: you land on Jobs, and your name is in the top bar.
2. Users page: invite a second Google account, sign in with it in a private window, and check it sees no jobs.
3. Claude setup: Disconnect, then Connect Google Drive again.

## Decisions
1. Removing a user disables them and keeps their data, so a mistaken removal can be undone.
