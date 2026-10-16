"""The owner's Users API (strelitzia.web.admin): owner-only, invite, caps, disable/enable, never deletes."""

from datetime import datetime, timezone

from strelitzia import users
from web_helpers import add_job, second_user, sign_in_as

URL = "/app/api/admin/users"


def _owner(conn):
    return users.owner(conn)


def test_list_shows_spend_cap_and_last_login(client, conn):
    friend = second_user(conn)
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("INSERT INTO llm_usage (id, ts, cost_usd, user_id) VALUES ('u1', ?, 1.25, ?)", (now, friend.id))
    conn.execute("INSERT INTO llm_usage (id, ts, cost_usd, user_id) VALUES ('u2', '2000-01-01T00:00:00+00:00', 9, ?)",
                 (friend.id,))
    conn.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (now, friend.id))
    conn.commit()
    resp = client.get(URL)
    assert resp.status_code == 200
    rows = resp.json()["users"]
    assert [r["role"] for r in rows] == ["owner", "user"]  # the owner first
    row = rows[1]
    assert row["email"] == "b@example.com" and row["status"] == "invited"
    assert row["monthly_cap_usd"] == users.DEFAULT_CAP_USD
    assert row["month_spend_usd"] == 1.25  # last month's spend doesn't count
    assert row["last_login_at"] == now
    assert rows[0]["monthly_cap_usd"] is None


def test_non_owner_gets_403(auth_client, conn):
    friend = users.activate(conn, second_user(conn).email, "Friend")
    sign_in_as(auth_client, friend)
    assert auth_client.get(URL).status_code == 403
    assert auth_client.post(URL, json={"email": "c@example.com"}).status_code == 403
    assert auth_client.patch(f"{URL}/{friend.id}", json={"monthly_cap_usd": 100}).status_code == 403
    assert auth_client.delete(f"{URL}/{_owner(conn).id}").status_code == 403
    assert users.by_email(conn, "c@example.com") is None
    assert users.get(conn, friend.id).monthly_cap_usd == users.DEFAULT_CAP_USD


def test_no_session_gets_401(auth_client):
    assert auth_client.get(URL).status_code == 401


def test_signed_in_owner_can_list(auth_client, conn):
    sign_in_as(auth_client, _owner(conn))
    assert auth_client.get(URL).status_code == 200


def test_invite(client, conn):
    resp = client.post(URL, json={"email": " New@Example.com ", "monthly_cap_usd": 12.5})
    assert resp.status_code == 201
    body = resp.json()
    assert body["email"] == "new@example.com" and body["status"] == "invited" and body["monthly_cap_usd"] == 12.5
    assert users.by_email(conn, "new@example.com").monthly_cap_usd == 12.5


def test_invite_defaults_the_cap(client, conn):
    assert client.post(URL, json={"email": "d@example.com"}).json()["monthly_cap_usd"] == users.DEFAULT_CAP_USD


def test_invite_rejects_bad_input_and_duplicates(client, conn):
    assert client.post(URL, json={"email": "not-an-email"}).status_code == 422
    assert client.post(URL, json={"email": "e@example.com", "monthly_cap_usd": -1}).status_code == 422
    assert client.post(URL, json={"email": "e@example.com"}).status_code == 201
    assert client.post(URL, json={"email": "E@example.com"}).status_code == 409


def test_patch_cap(client, conn):
    friend = second_user(conn)
    resp = client.patch(f"{URL}/{friend.id}", json={"monthly_cap_usd": 20})
    assert resp.status_code == 200 and resp.json()["monthly_cap_usd"] == 20
    assert client.patch(f"{URL}/{friend.id}", json={"monthly_cap_usd": None}).json()["monthly_cap_usd"] is None
    assert users.get(conn, friend.id).monthly_cap_usd is None
    # A patch without the cap leaves it alone.
    client.patch(f"{URL}/{friend.id}", json={"monthly_cap_usd": 3})
    client.patch(f"{URL}/{friend.id}", json={"status": "disabled"})
    assert users.get(conn, friend.id).monthly_cap_usd == 3
    assert client.patch(f"{URL}/{friend.id}", json={"monthly_cap_usd": -2}).status_code == 422


def test_delete_disables_and_keeps_data(client, conn):
    friend = users.activate(conn, second_user(conn).email, "Friend")
    add_job(conn, user_id=friend.id)
    resp = client.delete(f"{URL}/{friend.id}")
    assert resp.status_code == 200 and resp.json()["status"] == "disabled"
    assert users.get(conn, friend.id).status == "disabled"
    assert conn.execute("SELECT COUNT(*) FROM jobs WHERE user_id = ?", (friend.id,)).fetchone()[0] == 1


def test_enable_restores_active_or_invited(client, conn):
    signed_in = users.activate(conn, second_user(conn).email, "Friend")
    conn.execute("UPDATE users SET last_login_at = '2026-10-01T00:00:00+00:00' WHERE id = ?", (signed_in.id,))
    conn.commit()
    never = second_user(conn, "never@example.com")
    for u in (signed_in, never):
        client.delete(f"{URL}/{u.id}")
    assert client.patch(f"{URL}/{signed_in.id}", json={"status": "active"}).json()["status"] == "active"
    assert client.patch(f"{URL}/{never.id}", json={"status": "active"}).json()["status"] == "invited"


def test_owner_cant_disable_themself(client, conn):
    owner = _owner(conn)
    assert client.delete(f"{URL}/{owner.id}").status_code == 400
    assert client.patch(f"{URL}/{owner.id}", json={"status": "disabled"}).status_code == 400
    assert users.get(conn, owner.id).status == "active"


def test_unknown_user_and_bad_status(client, conn):
    assert client.delete(f"{URL}/nope").status_code == 404
    assert client.patch(f"{URL}/nope", json={"monthly_cap_usd": 1}).status_code == 404
    friend = second_user(conn)
    assert client.patch(f"{URL}/{friend.id}", json={"status": "owner"}).status_code == 422


def test_mutations_need_the_csrf_header(app, conn):
    from fastapi.testclient import TestClient

    with TestClient(app) as bare:
        assert bare.post(URL, json={"email": "f@example.com"}).status_code == 403
    assert users.by_email(conn, "f@example.com") is None


def test_owner_has_no_cap(client, conn):
    owner = _owner(conn)
    assert client.patch(f"{URL}/{owner.id}", json={"monthly_cap_usd": 5}).status_code == 400
    assert users.get(conn, owner.id).monthly_cap_usd is None


def test_invite_twice_is_one_account(conn):
    first = users.invite(conn, "g@example.com")
    assert users.invite(conn, "G@example.com").id == first.id
    assert len([u for u in users.list_users(conn) if u.email == "g@example.com"]) == 1
