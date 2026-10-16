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

    def fake_verify(token, request, audience):
        assert token == "signed.jwt" and audience == "client-123"
        return dict(Google.claims)

    monkeypatch.setattr(auth.httpx, "post", fake_post)
    monkeypatch.setattr(auth.id_token, "verify_oauth2_token", fake_verify)
    return Google


@pytest.fixture
def client(db):
    app = FastAPI()
    app.state.db = db
    app.include_router(auth.router)

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
    assert resp.status_code == 400
    assert google.token_requests == []
    assert client.get("/app/api/whoami").status_code == 401


def test_callback_without_a_login_is_rejected(auth_on, client, google):
    resp = client.get("/app/api/auth/callback", params={"code": "c0de", "state": "anything"})
    assert resp.status_code == 400


def test_state_is_single_use(auth_on, client, google):
    login = client.get("/app/api/auth/login")
    state = parse_qs(urlparse(login.headers["location"]).query)["state"][0]
    assert client.get("/app/api/auth/callback", params={"code": "c0de", "state": state}).status_code == 303
    client.post("/app/api/auth/logout")
    assert client.get("/app/api/auth/callback", params={"code": "c0de", "state": state}).status_code == 400


def test_rejected_id_token_signs_nobody_in(auth_on, client, google, monkeypatch):
    def reject(token, request, audience):
        raise ValueError("Token expired")

    monkeypatch.setattr(auth.id_token, "verify_oauth2_token", reject)
    assert sign_in(client).headers["location"] == "/app/?auth=error"
    assert client.get("/app/api/whoami").status_code == 401


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
