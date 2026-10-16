"""Google sign-in (strelitzia.web.auth) with Google's token endpoint and ID-token check mocked."""

from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from strelitzia import users
from strelitzia.database import close_connection, get_connection, init_db
from strelitzia.web import auth

OWNER = "owner@example.com"


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "auth.db"
    init_db(path)
    yield path
    close_connection(path)


@pytest.fixture
def conn(db):
    return get_connection(db)


@pytest.fixture
def auth_on(monkeypatch):
    monkeypatch.setenv("STRELITZIA_GOOGLE_CLIENT_ID", "client-123")
    monkeypatch.setenv("STRELITZIA_GOOGLE_CLIENT_SECRET", "shh")
    monkeypatch.setenv("STRELITZIA_OWNER_EMAIL", OWNER)
    monkeypatch.setenv("STRELITZIA_PUBLIC_URL", "https://strelitzia.example.net")


@pytest.fixture
def google(monkeypatch):
    """Mocks the token exchange and the ID-token check; set `google.claims` to what Google vouches for."""

    class Google:
        claims = {"email": OWNER, "email_verified": True, "name": "Owner"}
        token_requests: list[dict] = []

    def fake_post(url, data=None, timeout=None):
        assert url == auth.GOOGLE_TOKEN_URL
        Google.token_requests.append(data)
        return httpx.Response(200, json={"id_token": "signed.jwt", "access_token": "at"})

    def fake_verify(token, request, audience, clock_skew_in_seconds=0):
        assert token == "signed.jwt" and audience == "client-123" and clock_skew_in_seconds > 0
        return dict(Google.claims)

    monkeypatch.setattr(auth.httpx, "post", fake_post)
    monkeypatch.setattr(auth.id_token, "verify_oauth2_token", fake_verify)
    return Google


@pytest.fixture
def client(db):
    app = FastAPI()
    app.state.db = db
    app.include_router(auth.router)
    app.include_router(auth.drive_router)

    @app.get("/app/api/whoami")
    def whoami(user: users.User = Depends(auth.current_user)):
        return {"email": user.email}

    app.add_middleware(SessionMiddleware, secret_key="test-secret", session_cookie=auth.SESSION_COOKIE)
    with TestClient(app, follow_redirects=False) as c:
        yield c


def sign_in(client, state=None):
    login = client.get("/app/api/auth/login")
    assert login.status_code == 303
    query = parse_qs(urlparse(login.headers["location"]).query)
    return client.get("/app/api/auth/callback", params={"code": "c0de", "state": state or query["state"][0]})


def test_login_redirects_to_google_with_state(auth_on, client):
    resp = client.get("/app/api/auth/login")
    url = urlparse(resp.headers["location"])
    assert f"{url.scheme}://{url.netloc}{url.path}" == auth.GOOGLE_AUTH_URL
    query = parse_qs(url.query)
    assert query["client_id"] == ["client-123"]
    assert query["scope"] == ["openid email profile"]
    assert query["redirect_uri"] == ["https://strelitzia.example.net/app/api/auth/callback"]
    assert len(query["state"][0]) >= 32


def test_owner_signs_in(auth_on, client, conn, google):
    owner = users.owner(conn)
    resp = sign_in(client)
    assert resp.status_code == 303 and resp.headers["location"] == "/app/"
    assert google.token_requests[-1]["code"] == "c0de"
    assert google.token_requests[-1]["redirect_uri"] == "https://strelitzia.example.net/app/api/auth/callback"
    me = client.get("/app/api/auth/me").json()
    assert me == {"enabled": True, "user": {"email": OWNER, "name": None, "role": "owner"}}
    assert client.get("/app/api/whoami").json() == {"email": owner.email}


def test_owner_row_from_before_owner_email_is_rekeyed(client, conn, auth_on, google):
    # The database (and its owner row) was created before STRELITZIA_OWNER_EMAIL was set.
    placeholder = users.owner(conn)
    assert placeholder.email == users.FALLBACK_OWNER_EMAIL
    google.claims = {"email": "Owner@Example.com", "email_verified": True, "name": "Owner"}
    assert sign_in(client).headers["location"] == "/app/"
    rekeyed = users.owner(conn)
    assert (rekeyed.id, rekeyed.email, rekeyed.name) == (placeholder.id, OWNER, "Owner")
    assert client.get("/app/api/auth/me").json()["user"]["role"] == "owner"


def test_invited_user_signs_in_and_is_activated(auth_on, client, conn, google):
    invited = users.invite(conn, "friend@example.com")
    google.claims = {"email": "friend@example.com", "email_verified": True, "name": "Friend"}
    assert sign_in(client).headers["location"] == "/app/"
    assert users.get(conn, invited.id).status == "active"
    assert client.get("/app/api/whoami").json() == {"email": "friend@example.com"}
    assert client.get("/app/api/auth/me").json()["user"] == {"email": "friend@example.com", "name": "Friend",
                                                              "role": "user"}


def test_uninvited_email_gets_no_session(auth_on, client, conn, google):
    google.claims = {"email": "stranger@example.com", "email_verified": True}
    resp = sign_in(client)
    assert resp.headers["location"] == "/app/?auth=not_invited"
    assert users.by_email(conn, "stranger@example.com") is None
    assert client.get("/app/api/auth/me").json() == {"enabled": True, "user": None}
    assert client.get("/app/api/whoami").status_code == 401


def test_unverified_email_is_refused(auth_on, client, conn, google):
    users.invite(conn, "friend@example.com")
    google.claims = {"email": "friend@example.com", "email_verified": False}
    assert sign_in(client).headers["location"] == "/app/?auth=unverified"
    assert client.get("/app/api/whoami").status_code == 401


def test_bad_state_is_rejected(auth_on, client, google):
    resp = sign_in(client, state="forged")
    assert resp.headers["location"] == "/app/?auth=error"
    assert google.token_requests == []
    assert client.get("/app/api/whoami").status_code == 401


def test_callback_without_a_login_is_rejected(auth_on, client, google):
    for state in ("anything", "\u00e9t\u00e9"):  # a non-ASCII state is refused too, not a 500
        resp = client.get("/app/api/auth/callback", params={"code": "c0de", "state": state})
        assert resp.headers["location"] == "/app/?auth=error"
    assert google.token_requests == []


def test_state_is_single_use(auth_on, client, google):
    login = client.get("/app/api/auth/login")
    state = parse_qs(urlparse(login.headers["location"]).query)["state"][0]
    assert client.get("/app/api/auth/callback", params={"code": "c0de", "state": state}).status_code == 303
    client.post("/app/api/auth/logout")
    assert client.get("/app/api/auth/callback", params={"code": "c0de", "state": state}).headers["location"] \
        == "/app/?auth=error"
    assert len(google.token_requests) == 1


def test_rejected_id_token_signs_nobody_in(auth_on, client, google, monkeypatch):
    def reject(token, request, audience, clock_skew_in_seconds=0):
        raise ValueError("Token expired")

    monkeypatch.setattr(auth.id_token, "verify_oauth2_token", reject)
    assert sign_in(client).headers["location"] == "/app/?auth=error"
    assert client.get("/app/api/whoami").status_code == 401


def test_token_endpoint_trouble_signs_nobody_in(auth_on, client, google, monkeypatch):
    responses = iter([httpx.Response(200, text="<html>captive portal</html>"), httpx.Response(400, json={}),
                      httpx.Response(200, json={"access_token": "no id token"})])
    monkeypatch.setattr(auth.httpx, "post", lambda url, data=None, timeout=None: next(responses))
    for _ in range(3):
        assert sign_in(client).headers["location"] == "/app/?auth=error"
    assert client.get("/app/api/whoami").status_code == 401


def test_google_cert_fetch_failure_signs_nobody_in(auth_on, client, google, monkeypatch):
    from google.auth.exceptions import TransportError

    def unreachable(token, request, audience, clock_skew_in_seconds=0):
        raise TransportError("Could not fetch certificates")

    monkeypatch.setattr(auth.id_token, "verify_oauth2_token", unreachable)
    assert sign_in(client).headers["location"] == "/app/?auth=error"


def test_sign_in_records_last_login(auth_on, client, conn, google):
    sign_in(client)
    first = conn.execute("SELECT last_login_at FROM users WHERE role = 'owner'").fetchone()[0]
    assert first
    conn.execute("UPDATE users SET last_login_at = '2000-01-01' WHERE role = 'owner'")
    conn.commit()
    sign_in(client)
    assert conn.execute("SELECT last_login_at FROM users WHERE role = 'owner'").fetchone()[0] > "2000-01-01"


def test_login_on_needs_the_other_keys(monkeypatch):
    monkeypatch.setenv("STRELITZIA_GOOGLE_CLIENT_ID", "client-123")
    for name in ("GOOGLE_CLIENT_SECRET", "SESSION_SECRET", "OWNER_EMAIL"):
        monkeypatch.delenv(f"STRELITZIA_{name}", raising=False)
    with pytest.raises(RuntimeError, match="OWNER_EMAIL"):
        auth.check_config()
    monkeypatch.setenv("STRELITZIA_GOOGLE_CLIENT_SECRET", "s")
    monkeypatch.setenv("STRELITZIA_SESSION_SECRET", "s")
    monkeypatch.setenv("STRELITZIA_OWNER_EMAIL", OWNER)
    auth.check_config()


def test_disabled_user_gets_401(auth_on, client, conn, google):
    friend = users.invite(conn, "friend@example.com")
    google.claims = {"email": "friend@example.com", "email_verified": True}
    sign_in(client)
    assert client.get("/app/api/whoami").status_code == 200
    users.set_status(conn, friend.id, "disabled")
    assert client.get("/app/api/whoami").status_code == 401
    assert client.get("/app/api/auth/me").json()["user"] is None
    # and they can't sign back in
    assert sign_in(client).headers["location"] == "/app/?auth=not_invited"


def test_logout_ends_the_session(auth_on, client, google):
    sign_in(client)
    assert client.get("/app/api/whoami").status_code == 200
    assert client.post("/app/api/auth/logout").json() == {"ok": True}
    assert client.get("/app/api/whoami").status_code == 401


def test_login_off_acts_as_owner(client, conn, monkeypatch):
    monkeypatch.delenv("STRELITZIA_GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("APPLYPILOT_GOOGLE_CLIENT_ID", raising=False)
    assert not auth.auth_enabled()
    owner = users.owner(conn)
    assert client.get("/app/api/whoami").json() == {"email": owner.email}
    assert client.get("/app/api/auth/me").json() == {
        "enabled": False, "user": {"email": owner.email, "name": None, "role": "owner"}}
    assert client.get("/app/api/auth/login").headers["location"] == "/app/"
    assert client.get("/app/api/auth/callback", params={"code": "c", "state": "s"}).status_code == 404


# --- Connect Google Drive (user-auth-6-drive) ---

DRIVE_CALLBACK = "https://strelitzia.example.net/app/api/drive/callback"


@pytest.fixture
def token_key(monkeypatch):
    from cryptography.fernet import Fernet
    monkeypatch.setenv("STRELITZIA_TOKEN_KEY", Fernet.generate_key().decode())


@pytest.fixture
def drive_google(google, monkeypatch):
    """The sign-in mock, plus Drive's token exchange: set `drive_google.drive_token` to Google's reply."""
    sign_in_post = auth.httpx.post

    class DriveGoogle:
        drive_token = {"access_token": "drive-access", "refresh_token": "drive-refresh", "expires_in": 3599,
                       "scope": f"openid {auth.DRIVE_SCOPE}", "token_type": "Bearer"}
        requests: list[dict] = []
        revoked: list[str] = []

    def fake_post(url, data=None, timeout=None):
        if url == auth.GOOGLE_REVOKE_URL:
            DriveGoogle.revoked.append(data["token"])
            return httpx.Response(200)
        if data and data.get("redirect_uri") == DRIVE_CALLBACK:
            DriveGoogle.requests.append(data)
            return httpx.Response(200, json=DriveGoogle.drive_token)
        return sign_in_post(url, data=data, timeout=timeout)

    monkeypatch.setattr(auth.httpx, "post", fake_post)
    return DriveGoogle


def connect_drive(client, state=None):
    start = client.get("/app/api/drive/connect")
    assert start.status_code == 303
    query = parse_qs(urlparse(start.headers["location"]).query)
    return client.get("/app/api/drive/callback", params={"code": "drive-c0de", "state": state or query["state"][0]})


def _token_rows(conn):
    return dict(conn.execute("SELECT user_id, token_enc FROM user_drive").fetchall())


def test_drive_connect_asks_only_for_drive_file_offline(auth_on, token_key, client, google):
    sign_in(client)
    start = client.get("/app/api/drive/connect")
    assert start.status_code == 303
    url = urlparse(start.headers["location"])
    query = {k: v[0] for k, v in parse_qs(url.query).items()}
    assert f"{url.scheme}://{url.netloc}{url.path}" == auth.GOOGLE_AUTH_URL
    assert query["scope"] == "https://www.googleapis.com/auth/drive.file"
    assert query["access_type"] == "offline" and query["prompt"] == "consent"
    assert query["include_granted_scopes"] == "true"
    assert query["redirect_uri"] == DRIVE_CALLBACK and query["client_id"] == "client-123" and query["state"]
    assert query["login_hint"] == OWNER


def test_drive_connect_stores_a_token_for_the_session_user_only(auth_on, token_key, client, conn, google,
                                                                drive_google, monkeypatch):
    from strelitzia.storage import drive
    monkeypatch.setattr(drive, "drive_libraries_installed", lambda: True)
    friend = users.invite(conn, "friend@example.com")
    google.claims = {"email": "friend@example.com", "email_verified": True}
    sign_in(client)
    done = connect_drive(client)
    assert done.headers["location"] == "/app/claude-setup?drive=connected"
    assert drive_google.requests[-1]["code"] == "drive-c0de"
    rows = _token_rows(conn)
    assert list(rows) == [friend.id]
    assert "drive-refresh" not in rows[friend.id] and "refresh_token" not in rows[friend.id]
    creds = drive.load_user_credentials(conn, friend.id)
    assert creds.refresh_token == "drive-refresh" and creds.token == "drive-access" and creds.client_id == "client-123"
    assert drive.user_drive_status(conn, users.owner(conn).id)[0] == "not_authorized"


def test_drive_callback_with_a_wrong_state_stores_nothing(auth_on, token_key, client, conn, google, drive_google):
    sign_in(client)
    assert connect_drive(client, state="forged").headers["location"] == "/app/claude-setup?drive=error"
    assert client.get("/app/api/drive/callback", params={"code": "c", "state": "s"}).headers["location"] == (
        "/app/claude-setup?drive=error")
    assert _token_rows(conn) == {}


def test_drive_consent_without_the_drive_scope_is_denied(auth_on, token_key, client, conn, google, drive_google):
    sign_in(client)
    drive_google.drive_token = dict(drive_google.drive_token, scope="openid email")
    assert connect_drive(client).headers["location"] == "/app/claude-setup?drive=denied"
    start = client.get("/app/api/drive/connect")
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    denied = client.get("/app/api/drive/callback", params={"error": "access_denied", "state": state})
    assert denied.headers["location"] == "/app/claude-setup?drive=denied"
    assert _token_rows(conn) == {}


def test_drive_token_without_a_refresh_token_is_an_error(auth_on, token_key, client, conn, google, drive_google):
    sign_in(client)
    drive_google.drive_token = {k: v for k, v in drive_google.drive_token.items() if k != "refresh_token"}
    assert connect_drive(client).headers["location"] == "/app/claude-setup?drive=error"
    assert _token_rows(conn) == {}


def test_drive_connect_needs_a_session_and_the_token_key(auth_on, client, google, monkeypatch):
    assert client.get("/app/api/drive/connect").status_code == 401
    monkeypatch.delenv("STRELITZIA_TOKEN_KEY", raising=False)
    monkeypatch.delenv("APPLYPILOT_TOKEN_KEY", raising=False)
    sign_in(client)
    assert client.get("/app/api/drive/connect").headers["location"] == "/app/claude-setup?drive=unavailable"


def test_drive_disconnect(auth_on, token_key, client, conn, google, drive_google):
    sign_in(client)
    connect_drive(client)
    assert list(_token_rows(conn)) == [users.owner(conn).id]
    assert client.post("/app/api/drive/disconnect").json() == {"ok": True}
    assert _token_rows(conn) == {}
    assert drive_google.revoked == ["drive-refresh"]


def test_drive_disconnect_forgets_the_token_when_google_is_unreachable(auth_on, token_key, client, conn, google,
                                                                       drive_google, monkeypatch):
    sign_in(client)
    connect_drive(client)
    post = auth.httpx.post

    def unreachable(url, data=None, timeout=None):
        if url == auth.GOOGLE_REVOKE_URL:
            raise httpx.ConnectError("no route")
        return post(url, data=data, timeout=timeout)

    monkeypatch.setattr(auth.httpx, "post", unreachable)
    assert client.post("/app/api/drive/disconnect").json() == {"ok": True}
    assert _token_rows(conn) == {}
