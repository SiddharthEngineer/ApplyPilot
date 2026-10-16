"""Google sign-in for the dashboard: the OAuth 2.0 authorization-code flow and the invite allowlist.

Login is on only when STRELITZIA_GOOGLE_CLIENT_ID is set. While it's unset every request acts as the owner, as before
accounts existed. With it on, the owner (STRELITZIA_OWNER_EMAIL) and invited emails get a session cookie holding their
user id; anyone else is sent back to /app/?auth=not_invited (a disabled account to ?auth=disabled).
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from google.auth.exceptions import GoogleAuthError
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token
from google.oauth2.credentials import Credentials

from strelitzia import users
from strelitzia.config import env
from strelitzia.database import Connection, get_connection
from strelitzia.storage import drive

PREFIX = "/app/api/auth"
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"
SCOPES = "openid email profile"
SESSION_COOKIE = "strelitzia_session"
SESSION_MAX_AGE = 30 * 24 * 3600
CLOCK_SKEW_SECONDS = 30  # tolerated drift between this server's clock and Google's when checking the ID token
PUBLIC_API_PATHS = ("/app/api/health",)

DRIVE_PREFIX = "/app/api/drive"
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.file"
SETUP_PAGE = "/app/claude-setup"

router = APIRouter(prefix=PREFIX)
drive_router = APIRouter(prefix=DRIVE_PREFIX)
log = logging.getLogger(__name__)


def auth_enabled() -> bool:
    return bool(env("GOOGLE_CLIENT_ID"))


def public_url() -> str:
    return (env("PUBLIC_URL") or "").rstrip("/")


def secure_cookie() -> bool:
    return public_url().startswith("https://")


def is_public_path(path: str) -> bool:
    """API paths that answer without a session: the health check and the sign-in flow itself."""
    return path in PUBLIC_API_PATHS or path.startswith(PREFIX + "/")


def _conn(request: Request) -> Connection:
    return get_connection(request.app.state.db)


def _redirect_uri(request: Request) -> str:
    base = public_url()
    return f"{base}{PREFIX}/callback" if base else str(request.url_for("auth_callback"))


def current_user(request: Request) -> users.User:
    """The signed-in user: the owner while login is off, else the session's active account (401 without one)."""
    cached = getattr(request.state, "user", None)
    if cached is not None:
        return cached
    conn = _conn(request)
    if not auth_enabled():
        user = users.owner(conn)
    else:
        uid = request.session.get("uid")
        user = users.get(conn, uid) if isinstance(uid, str) else None
        if user is None or user.status != "active":
            raise HTTPException(401, "Not signed in")
    request.state.user = user
    return user


def check_config() -> None:
    """With login on, refuse to start without the keys sign-in can't work without."""
    if not auth_enabled():
        return
    missing = [f"STRELITZIA_{name}" for name in ("GOOGLE_CLIENT_SECRET", "SESSION_SECRET", "OWNER_EMAIL")
               if not env(name)]
    if missing:
        raise RuntimeError(f"STRELITZIA_GOOGLE_CLIENT_ID is set but {', '.join(missing)} is not "
                           "(see agents/DEPLOY.md, Google sign-in)")


def _sign_in(conn: Connection, email: str, name: str | None) -> users.User | None:
    """The account a verified Google email signs in as: the owner, an invited (or active) user, else None."""
    user = _account_for(conn, email, name)
    if user is not None:
        conn.execute("UPDATE users SET last_login_at = ? WHERE id = ?",
                     (datetime.now(timezone.utc).isoformat(), user.id))
        conn.commit()
    return user


def _account_for(conn: Connection, email: str, name: str | None) -> users.User | None:
    owner = users.owner(conn)
    email = email.strip().lower()
    if email == owner.email:
        return owner
    owner_email = (env("OWNER_EMAIL") or "").strip().lower()
    if email == owner_email and owner.email == users.FALLBACK_OWNER_EMAIL:
        # The owner row was created before OWNER_EMAIL was set: re-key it to the real address.
        if users.by_email(conn, email) is not None:
            log.warning("owner email %s is already taken by another account; not re-keying the owner", email)
            return owner
        conn.execute("UPDATE users SET email = ?, name = COALESCE(name, ?) WHERE id = ?", (email, name, owner.id))
        conn.commit()
        return users.get(conn, owner.id)
    user = users.by_email(conn, email)
    if user is not None and user.role == "owner":
        return user
    return users.activate(conn, email, name)


def _state_matches(state: str | None, expected: object) -> bool:
    return bool(state) and isinstance(expected, str) and secrets.compare_digest(state.encode(), expected.encode())


def _exchange_code(code: str, redirect_uri: str) -> dict | None:
    """Trade an authorization code for Google's token response, or None (logged, without the tokens)."""
    try:
        resp = httpx.post(GOOGLE_TOKEN_URL, timeout=15, data={
            "code": code,
            "client_id": env("GOOGLE_CLIENT_ID"),
            "client_secret": env("GOOGLE_CLIENT_SECRET") or "",
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        })
        body = resp.json() if resp.status_code == 200 else None
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("Google token exchange failed: %s", exc)
        return None
    if not isinstance(body, dict):
        log.warning("Google token exchange returned HTTP %s", resp.status_code)
        return None
    return body


def _back(outcome: str | None = None) -> RedirectResponse:
    return RedirectResponse("/app/" + (f"?auth={outcome}" if outcome else ""), status_code=303)


@router.get("/login")
def login(request: Request):
    if not auth_enabled():
        return _back()
    state = secrets.token_urlsafe(32)
    request.session["oauth_state"] = state
    params = {
        "client_id": env("GOOGLE_CLIENT_ID"),
        "redirect_uri": _redirect_uri(request),
        "response_type": "code",
        "scope": SCOPES,
        "state": state,
        "prompt": "select_account",
    }
    return RedirectResponse(f"{GOOGLE_AUTH_URL}?{urlencode(params)}", status_code=303)


@router.get("/callback", name="auth_callback")
def callback(request: Request, code: str | None = None, state: str | None = None, error: str | None = None):
    if not auth_enabled():
        raise HTTPException(404, "Login is off")
    expected = request.session.pop("oauth_state", None)
    if not _state_matches(state, expected):
        # A forged callback, or a stale one (a second login tab, Back into the callback): nobody is signed in.
        log.warning("sign-in callback with a missing or wrong state")
        return _back("error")
    if error or not code:
        return _back("error")
    client_id = env("GOOGLE_CLIENT_ID")
    body = _exchange_code(code, _redirect_uri(request))
    token = body.get("id_token") if body else None
    if not isinstance(token, str) or not token:
        if body is not None:
            log.warning("Google token exchange returned no id_token")
        return _back("error")
    try:
        info = id_token.verify_oauth2_token(token, google_requests.Request(), client_id,
                                            clock_skew_in_seconds=CLOCK_SKEW_SECONDS)
    except (ValueError, GoogleAuthError) as exc:
        log.warning("Google ID token rejected: %s", exc)
        return _back("error")
    if not info.get("email_verified") or not info.get("email"):
        return _back("unverified")
    user = _sign_in(_conn(request), info["email"], info.get("name"))
    request.session.clear()
    if user is None:
        known = users.by_email(_conn(request), info["email"])
        return _back("disabled" if known and known.status == "disabled" else "not_invited")
    request.session["uid"] = user.id
    return _back()


@router.post("/logout")
def logout(request: Request) -> dict:
    request.session.clear()
    return {"ok": True}


@router.get("/me")
def me(request: Request) -> dict:
    try:
        user = current_user(request)
    except HTTPException:
        user = None
    return {
        "enabled": auth_enabled(),
        "user": {"email": user.email, "name": user.name, "role": user.role} if user else None,
    }


# --- Connect Google Drive (a second, optional consent after sign-in; strelitzia.storage.drive stores the token) ---

def drive_connect_available() -> bool:
    """The web OAuth client and the token key are both set, so a user can connect their own Drive."""
    return auth_enabled() and bool(env("TOKEN_KEY"))


def _drive_redirect_uri(request: Request) -> str:
    base = public_url()
    return f"{base}{DRIVE_PREFIX}/callback" if base else str(request.url_for("drive_callback"))


def _setup_page(outcome: str) -> RedirectResponse:
    return RedirectResponse(f"{SETUP_PAGE}?drive={outcome}", status_code=303)


@drive_router.get("/connect")
def drive_connect(request: Request):
    user = current_user(request)
    if not drive_connect_available():
        return _setup_page("unavailable")
    state = secrets.token_urlsafe(32)
    request.session["drive_state"] = state
    params = {
        "client_id": env("GOOGLE_CLIENT_ID"),
        "redirect_uri": _drive_redirect_uri(request),
        "response_type": "code",
        "scope": DRIVE_SCOPE,
        "state": state,
        # offline + consent: Google only returns a refresh token on a fresh consent.
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "login_hint": user.email,  # the account picker starts on the signed-in account
    }
    return RedirectResponse(f"{GOOGLE_AUTH_URL}?{urlencode(params)}", status_code=303)


@drive_router.get("/callback", name="drive_callback")
def drive_callback(request: Request, code: str | None = None, state: str | None = None, error: str | None = None,
                   scope: str | None = None):
    user = current_user(request)
    if not drive_connect_available():
        return _setup_page("unavailable")
    if not _state_matches(state, request.session.pop("drive_state", None)):
        log.warning("Drive connect callback with a missing or wrong state")
        return _setup_page("error")
    if error or not code:
        return _setup_page("denied" if error == "access_denied" else "error")
    body = _exchange_code(code, _drive_redirect_uri(request))
    if body is None:
        return _setup_page("error")
    granted = str(body.get("scope") or scope or "").split()
    access, refresh = body.get("access_token"), body.get("refresh_token")
    if DRIVE_SCOPE not in granted:
        return _setup_page("denied")  # the user unticked the Drive box on the consent screen
    if not isinstance(access, str) or not isinstance(refresh, str) or not refresh:
        log.warning("Drive token exchange returned no refresh token")
        return _setup_page("error")
    expires_in = body.get("expires_in")
    expiry = (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=int(expires_in))
              if isinstance(expires_in, int | float) else None)  # google-auth wants naive UTC
    creds = Credentials(token=access, refresh_token=refresh, token_uri=GOOGLE_TOKEN_URL,
                        client_id=env("GOOGLE_CLIENT_ID"), client_secret=env("GOOGLE_CLIENT_SECRET"),
                        scopes=[DRIVE_SCOPE], expiry=expiry)
    try:
        drive.save_user_token(_conn(request), user.id, creds)
    except drive.DriveNotConfigured as exc:
        log.warning("Couldn't store the Drive token: %s", exc)
        return _setup_page("error")
    return _setup_page("connected")


@drive_router.post("/disconnect")
def drive_disconnect(request: Request) -> dict:
    """Forget the user's Drive token, revoking it at Google first (best effort: it's forgotten either way)."""
    conn, user = _conn(request), current_user(request)
    refresh = drive.stored_refresh_token(conn, user.id)
    if refresh:
        try:
            resp = httpx.post(GOOGLE_REVOKE_URL, timeout=15, data={"token": refresh})
            if resp.status_code != 200:
                log.warning("Google didn't revoke the Drive token (HTTP %s)", resp.status_code)
        except httpx.HTTPError as exc:
            log.warning("Couldn't revoke the Drive token at Google: %s", type(exc).__name__)
    drive.disconnect(conn, user.id)
    return {"ok": True}
