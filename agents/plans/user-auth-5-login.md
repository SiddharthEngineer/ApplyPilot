# Plan: User accounts, part 5: Google sign-in and the invite allowlist
**Status:** ❌ Not started
**Risk:** review (authentication)
**Issue:** #47
**Depends on:** user-auth-4-workspace

## Goal
Users sign in with Google. Only invited emails (and the owner) get in; anyone else sees a "not invited" message. Every API
request acts as the signed-in user. While `STRELITZIA_GOOGLE_CLIENT_ID` is unset, login is off and the app acts as the
owner, exactly as today, so merging this never locks anyone out of preprod or prod.

## Context
- `src/strelitzia/web/app.py:1-8, 40-45`: module docstring (CSRF header rationale) and `db_conn`. `:739-770`: `create_app`
  and the `csrf_guard` middleware. `current_user(request)` exists since part 2 and returns the owner; this part replaces its body.
- `src/strelitzia/users.py` (part 1): `owner`, `by_email`, `activate`.
- `src/strelitzia/config.py`: `env(name)` reads `STRELITZIA_<name>` (with the APPLYPILOT_ fallback).
- Env (set by the user, see ROADMAP manual step): `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `SESSION_SECRET`,
  `OWNER_EMAIL`, `PUBLIC_URL` (e.g. `https://strelitzia.engineerfamily.net`), all `STRELITZIA_`-prefixed.
  Redirect URI: `{PUBLIC_URL}/app/api/auth/callback`.
- Libraries: add `itsdangerous>=2.1` (Starlette `SessionMiddleware`) and `google-auth>=2.0` to the `web` extra in
  `pyproject.toml`. Use the plain OAuth 2.0 authorization-code flow with `httpx` for the token exchange, and
  `google.oauth2.id_token.verify_oauth2_token` to verify the ID token. Scopes: `openid email profile` only (Drive is part 6).
- Gotchas: keep the `X-Strelitzia: 1` CSRF check. The session cookie is `HttpOnly`, `Secure` when PUBLIC_URL is https,
  `SameSite=Lax`, 30-day max age. Store a random `state` in the session and check it on callback. `/app/api/health` stays public.
  nginx basic auth stays in front for now; the user drops it on prod later (manual step).
- Owner re-key: when the owner signs in and the owner row's email is `owner@localhost`, set it to the Google email.

## Tasks
### Task 1: auth module
**Runs:** unattended
**Files:** src/strelitzia/web/auth.py (new), pyproject.toml (modify), tests/web/test_auth.py (new)
**What:** `auth_enabled() -> bool`; `router` with `GET /app/api/auth/login` (redirects to Google with state),
`GET /app/api/auth/callback` (exchange code, verify id_token, `email_verified` must be true, then owner email → owner;
invited → activate; else redirect to `/app/?auth=not_invited`), `POST /app/api/auth/logout`, `GET /app/api/auth/me`
(`{enabled, user: {email, name, role} | null}`). `current_user(request) -> User` reads `request.session["uid"]`; when
auth is off it returns `users.owner(conn)`; when on and there's no valid active user it raises 401.
**Validate:** `pytest tests/web/test_auth.py -q` → passes with Google's token endpoint and `verify_oauth2_token` mocked:
the owner and invited users get a session, an uninvited email doesn't, a bad state is rejected, disabled users get 401.
**Status:** ✅ 2026-10-16 (this commit)

### Task 2: wire it into the app
**Runs:** unattended
**Files:** src/strelitzia/web/app.py (modify), tests/web/conftest.py (modify), tests/web/test_app.py (modify)
**What:** Add `SessionMiddleware` (secret from `SESSION_SECRET`; refuse to start with auth on and no secret).
Include `auth.router`. Replace the part-2 `current_user` with `auth.current_user`. With auth on, every `/app/api/*` route
except `health`, `auth/*` returns 401 without a session. The UI routes stay public (the SPA handles the login screen).
Test fixtures run with auth off, plus one fixture that turns it on with a fake session.
**Validate:** `pytest tests/web -q` → passes, including 401 on `/app/api/jobs` with auth on and no session.
**Status:** ✅ 2026-10-16 (this commit)

### Task 3: docs
**Runs:** unattended
**Files:** .env.example (modify), agents/DEPLOY.md (modify)
**What:** Document the env keys above, the redirect URIs, and that login is off until the client id is set.
**Validate:** `grep -c STRELITZIA_GOOGLE_CLIENT_ID .env.example` → 1.
**Status:** ❌

## Validation
- `scripts/validate.sh --full` passes.
- `STRELITZIA_GOOGLE_CLIENT_ID= strelitzia serve` then `curl -s localhost:8000/app/api/auth/me` → `{"enabled": false, ...}`.

## Human test steps (preprod)
1. With the auth keys in `~/.strelitzia-preprod/.env`: open preprod, sign in with Google, see your jobs.
2. Sign in with a Google account that isn't invited: you see "not invited".

## Decisions
1. Login is off when no client id is set (user, 2026-10-16), so unattended merges never lock out preprod.
2. Invite allowlist only; there is no open sign-up.
3. No auth library dependency beyond google-auth: the flow is ~60 lines and easy to test with mocks.
