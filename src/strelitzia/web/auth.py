"""Google sign-in for the dashboard: the OAuth 2.0 authorization-code flow and the invite allowlist.

Login is on only when STRELITZIA_GOOGLE_CLIENT_ID is set. While it's unset every request acts as the owner, as before
accounts existed. With it on, the owner (STRELITZIA_OWNER_EMAIL) and invited emails get a session cookie holding their
user id; anyone else is sent back to /app/?auth=not_invited.
"""

from __future__ import annotations

import logging
import secrets
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token

from strelitzia import users
from strelitzia.config import env
from strelitzia.database import Connection, get_connection

PREFIX = "/app/api/auth"
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPES = "openid email profile"
SESSION_COOKIE = "strelitzia_session"
SESSION_MAX_AGE = 30 * 24 * 3600
PUBLIC_API_PATHS = ("/app/api/health",)

router = APIRouter(prefix=PREFIX)
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


def _sign_in(conn: Connection, email: str, name: str | None) -> users.User | None:
    """The account a verified Google email signs in as: the owner, an invited (or active) user, else None."""
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
    if not state or not isinstance(expected, str) or not secrets.compare_digest(state, expected):
        raise HTTPException(400, "Bad sign-in state; start again from the login page")
    if error or not code:
        return _back("error")
    client_id = env("GOOGLE_CLIENT_ID")
    try:
        resp = httpx.post(GOOGLE_TOKEN_URL, timeout=15, data={
            "code": code,
            "client_id": client_id,
            "client_secret": env("GOOGLE_CLIENT_SECRET") or "",
            "redirect_uri": _redirect_uri(request),
            "grant_type": "authorization_code",
        })
    except httpx.HTTPError as exc:
        log.warning("Google token exchange failed: %s", exc)
        return _back("error")
    token = resp.json().get("id_token") if resp.status_code == 200 else None
    if not token:
        log.warning("Google token exchange returned HTTP %s without an id_token", resp.status_code)
        return _back("error")
    try:
        info = id_token.verify_oauth2_token(token, google_requests.Request(), client_id)
    except ValueError as exc:
        log.warning("Google ID token rejected: %s", exc)
        return _back("error")
    if not info.get("email_verified") or not info.get("email"):
        return _back("unverified")
    user = _sign_in(_conn(request), info["email"], info.get("name"))
    request.session.clear()
    if user is None:
        return _back("not_invited")
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
