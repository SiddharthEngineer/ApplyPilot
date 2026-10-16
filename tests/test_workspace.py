"""Contacts and interviews (strelitzia.workspace) on both backends."""

from datetime import UTC, datetime

import pytest

from strelitzia import users
from strelitzia import workspace as ws
from strelitzia.database import init_db, job_key, table_columns

BACKENDS = pytest.mark.parametrize(
    "db_target", ["sqlite", pytest.param("pg", marks=pytest.mark.pg)], indirect=True
)


def _job(conn, url, title="Engineer", company="Acme"):
    conn.execute("INSERT INTO jobs (url, title, company, job_key) VALUES (?, ?, ?, ?)",
                 (url, title, company, job_key(url)))
    conn.commit()
    return url


@BACKENDS
def test_schema_has_workspace_columns_and_tables(db_target):
    conn = init_db(db_target)
    init_db(db_target)
    assert {"source", "added_at", "intake_status", "intake_error"} <= table_columns(conn)
    assert {"id", "name", "linkedin_url"} <= table_columns(conn, "contacts")
    assert {"job_url", "contact_id", "relation"} <= table_columns(conn, "job_contacts")
    assert {"id", "job_url", "scheduled_at", "debrief_notes", "outcome"} <= table_columns(conn, "interviews")


@BACKENDS
def test_contact_crud_and_search(db_target):
    conn = init_db(db_target)
    me = users.owner(conn).id
    ana = ws.create_contact(conn, me, name="Ana Ruiz", company="Acme", email="ana@acme.com", role="Recruiter")
    ws.create_contact(conn, me, name="Bo Chen", company="Globex")
    assert len(ana["id"]) == 32 and ana["created_at"]

    assert [c["name"] for c in ws.search_contacts(conn, me, "ACME")] == ["Ana Ruiz"]
    assert [c["name"] for c in ws.search_contacts(conn, me, "chen")] == ["Bo Chen"]
    assert [c["name"] for c in ws.search_contacts(conn, me, "ana@")] == ["Ana Ruiz"]
    assert [c["name"] for c in ws.search_contacts(conn, me, None)] == ["Ana Ruiz", "Bo Chen"]
    assert ws.search_contacts(conn, me, "", limit=1)[0]["job_count"] == 0
    assert ws.search_contacts(conn, me, "a_r") == [] and ws.search_contacts(conn, me, "%") == []  # no LIKE wildcards

    updated = ws.update_contact(conn, me, ana["id"], phone="+1 555 0100")
    assert updated["phone"] == "+1 555 0100" and updated["email"] == "ana@acme.com"

    with pytest.raises(ValueError):
        ws.create_contact(conn, me, name="  ")
    with pytest.raises(ValueError):
        ws.update_contact(conn, me, ana["id"], created_at="2000")
    with pytest.raises(ValueError):
        ws.create_contact(conn, me, name="X", created_at="2000")
    with pytest.raises(ws.NotFound):
        ws.update_contact(conn, me, "missing", name="X")


@BACKENDS
def test_links_are_shared_and_deleted_with_the_contact(db_target):
    conn = init_db(db_target)
    me = users.owner(conn).id
    j1 = _job(conn, "https://example.com/1", title="Data Scientist")
    j2 = _job(conn, "https://example.com/2", title="ML Engineer")
    ana = ws.create_contact(conn, me, name="Ana")
    ws.link_contact(conn, me, j1, ana["id"], "recruiter")
    ws.link_contact(conn, me, j2, ana["id"], "referral")
    ws.link_contact(conn, me, j2, ana["id"], "hiring_manager")  # relinking changes the relation

    assert [(c["name"], c["relation"]) for c in ws.job_contacts(conn, me, j2)] == [("Ana", "hiring_manager")]
    jobs = ws.contact_jobs(conn, me, ana["id"])
    assert [(j["key"], j["title"]) for j in jobs] == [(job_key(j1), "Data Scientist"), (job_key(j2), "ML Engineer")]
    assert ws.search_contacts(conn, me, "ana")[0]["job_count"] == 2

    with pytest.raises(ValueError):
        ws.link_contact(conn, me, j1, ana["id"], "boss")

    ws.unlink_contact(conn, me, j1, ana["id"])
    assert ws.job_contacts(conn, me, j1) == []

    ws.delete_contact(conn, me, ana["id"])
    assert ws.job_contacts(conn, me, j2) == []
    assert conn.execute("SELECT COUNT(*) FROM job_contacts").fetchone()[0] == 0
    with pytest.raises(ws.NotFound):
        ws.get_contact(conn, me, ana["id"])


@BACKENDS
def test_interviews_and_next_interview(db_target):
    conn = init_db(db_target)
    me = users.owner(conn).id
    j1 = _job(conn, "https://example.com/1")
    j2 = _job(conn, "https://example.com/2")
    _job(conn, "https://example.com/3")
    later = ws.create_interview(conn, me, j1, scheduled_at="2026-10-20T15:00:00.000Z", stage="onsite")
    ws.create_interview(conn, me, j1, scheduled_at="2026-10-17T09:30:00.000Z", stage="phone", format="video")
    ws.create_interview(conn, me, j1, scheduled_at="2026-10-01T09:00:00.000Z", stage="screen")  # past
    ws.create_interview(conn, me, j2, scheduled_at="2026-10-02T09:00:00.000Z")  # only past ones

    assert later["outcome"] == "pending"
    assert [i["stage"] for i in ws.job_interviews(conn, me, j1)] == ["screen", "phone", "onsite"]

    edited = ws.update_interview(conn, me, later["id"], prep_notes="Read the paper\nAsk about the team", outcome="passed")
    assert edited["prep_notes"] == "Read the paper\nAsk about the team" and edited["stage"] == "onsite"
    with pytest.raises(ValueError):
        ws.update_interview(conn, me, later["id"], outcome="maybe")
    with pytest.raises(ValueError):
        ws.update_interview(conn, me, later["id"], job_url="elsewhere")

    now = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
    rows = conn.execute(f"SELECT url, {ws.next_interview_sql(now)} AS nxt FROM jobs ORDER BY url").fetchall()
    assert [r["nxt"] for r in rows] == ["2026-10-17T09:30:00.000Z", None, None]

    ws.delete_interview(conn, me, later["id"])
    assert len(ws.job_interviews(conn, me, j1)) == 2
    with pytest.raises(ws.NotFound):
        ws.delete_interview(conn, me, later["id"])


@BACKENDS
def test_another_users_rows_are_not_found(db_target):
    conn = init_db(db_target)
    me = users.owner(conn).id
    other = users.invite(conn, "b@example.com").id
    job = _job(conn, "https://example.com/1")
    conn.execute("INSERT INTO jobs (user_id, url, title, job_key) VALUES (?, ?, ?, ?)",
                 (other, "https://example.com/theirs", "Theirs", job_key("theirs")))
    conn.commit()
    ana = ws.create_contact(conn, me, name="Ana")
    ws.link_contact(conn, me, job, ana["id"], "recruiter")
    interview = ws.create_interview(conn, me, job, stage="phone")

    # The other user sees none of it, and can't read, change, link or delete it.
    assert ws.search_contacts(conn, other, None) == []
    assert ws.job_contacts(conn, other, job) == [] and ws.job_interviews(conn, other, job) == []
    assert ws.contact_jobs(conn, other, ana["id"]) == []
    for call in (lambda: ws.get_contact(conn, other, ana["id"]),
                 lambda: ws.update_contact(conn, other, ana["id"], name="Mine"),
                 lambda: ws.delete_contact(conn, other, ana["id"]),
                 lambda: ws.link_contact(conn, other, job, ana["id"], "referral"),
                 lambda: ws.get_interview(conn, other, interview["id"]),
                 lambda: ws.update_interview(conn, other, interview["id"], outcome="rejected"),
                 lambda: ws.delete_interview(conn, other, interview["id"]),
                 lambda: ws.create_interview(conn, other, job)):
        with pytest.raises(ws.NotFound):
            call()
    ws.unlink_contact(conn, other, job, ana["id"])  # a no-op

    # Nor can a contact be linked to another user's job, or an interview added to it.
    with pytest.raises(ws.NotFound):
        ws.link_contact(conn, me, "https://example.com/theirs", ana["id"], "referral")
    with pytest.raises(ws.NotFound):
        ws.create_interview(conn, me, "https://example.com/theirs")
    theirs = ws.create_contact(conn, other, name="Bo")
    with pytest.raises(ws.NotFound):
        ws.link_contact(conn, other, job, theirs["id"], "referral")

    assert ws.get_contact(conn, me, ana["id"])["name"] == "Ana"
    assert [c["name"] for c in ws.job_contacts(conn, me, job)] == ["Ana"]
    assert ws.get_interview(conn, me, interview["id"])["outcome"] == "pending"
    assert [c["name"] for c in ws.search_contacts(conn, other, None)] == ["Bo"]
