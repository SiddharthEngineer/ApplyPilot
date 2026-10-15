"""Contacts and interviews (strelitzia.workspace) on both backends."""

from datetime import UTC, datetime

import pytest

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
    ana = ws.create_contact(conn, name="Ana Ruiz", company="Acme", email="ana@acme.com", role="Recruiter")
    ws.create_contact(conn, name="Bo Chen", company="Globex")
    assert len(ana["id"]) == 32 and ana["created_at"]

    assert [c["name"] for c in ws.search_contacts(conn, "ACME")] == ["Ana Ruiz"]
    assert [c["name"] for c in ws.search_contacts(conn, "chen")] == ["Bo Chen"]
    assert [c["name"] for c in ws.search_contacts(conn, "ana@")] == ["Ana Ruiz"]
    assert [c["name"] for c in ws.search_contacts(conn, None)] == ["Ana Ruiz", "Bo Chen"]
    assert ws.search_contacts(conn, "", limit=1)[0]["job_count"] == 0

    updated = ws.update_contact(conn, ana["id"], phone="+1 555 0100")
    assert updated["phone"] == "+1 555 0100" and updated["email"] == "ana@acme.com"

    with pytest.raises(ValueError):
        ws.create_contact(conn, name="  ")
    with pytest.raises(ValueError):
        ws.update_contact(conn, ana["id"], created_at="2000")
    with pytest.raises(ValueError):
        ws.create_contact(conn, name="X", created_at="2000")
    with pytest.raises(ws.NotFound):
        ws.update_contact(conn, "missing", name="X")


@BACKENDS
def test_links_are_shared_and_deleted_with_the_contact(db_target):
    conn = init_db(db_target)
    j1 = _job(conn, "https://example.com/1", title="Data Scientist")
    j2 = _job(conn, "https://example.com/2", title="ML Engineer")
    ana = ws.create_contact(conn, name="Ana")
    ws.link_contact(conn, j1, ana["id"], "recruiter")
    ws.link_contact(conn, j2, ana["id"], "referral")
    ws.link_contact(conn, j2, ana["id"], "hiring_manager")  # relinking changes the relation

    assert [(c["name"], c["relation"]) for c in ws.job_contacts(conn, j2)] == [("Ana", "hiring_manager")]
    jobs = ws.contact_jobs(conn, ana["id"])
    assert [(j["key"], j["title"]) for j in jobs] == [(job_key(j1), "Data Scientist"), (job_key(j2), "ML Engineer")]
    assert ws.search_contacts(conn, "ana")[0]["job_count"] == 2

    with pytest.raises(ValueError):
        ws.link_contact(conn, j1, ana["id"], "boss")

    ws.unlink_contact(conn, j1, ana["id"])
    assert ws.job_contacts(conn, j1) == []

    ws.delete_contact(conn, ana["id"])
    assert ws.job_contacts(conn, j2) == []
    assert conn.execute("SELECT COUNT(*) FROM job_contacts").fetchone()[0] == 0
    with pytest.raises(ws.NotFound):
        ws.get_contact(conn, ana["id"])


@BACKENDS
def test_interviews_and_next_interview(db_target):
    conn = init_db(db_target)
    j1 = _job(conn, "https://example.com/1")
    j2 = _job(conn, "https://example.com/2")
    _job(conn, "https://example.com/3")
    later = ws.create_interview(conn, j1, scheduled_at="2026-10-20T15:00:00.000Z", stage="onsite")
    ws.create_interview(conn, j1, scheduled_at="2026-10-17T09:30:00.000Z", stage="phone", format="video")
    ws.create_interview(conn, j1, scheduled_at="2026-10-01T09:00:00.000Z", stage="screen")  # past
    ws.create_interview(conn, j2, scheduled_at="2026-10-02T09:00:00.000Z")  # only past ones

    assert later["outcome"] == "pending"
    assert [i["stage"] for i in ws.job_interviews(conn, j1)] == ["screen", "phone", "onsite"]

    edited = ws.update_interview(conn, later["id"], prep_notes="Read the paper\nAsk about the team", outcome="passed")
    assert edited["prep_notes"] == "Read the paper\nAsk about the team" and edited["stage"] == "onsite"
    with pytest.raises(ValueError):
        ws.update_interview(conn, later["id"], outcome="maybe")
    with pytest.raises(ValueError):
        ws.update_interview(conn, later["id"], job_url="elsewhere")

    now = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
    rows = conn.execute(f"SELECT url, {ws.next_interview_sql(now)} AS nxt FROM jobs ORDER BY url").fetchall()
    assert [r["nxt"] for r in rows] == ["2026-10-17T09:30:00.000Z", None, None]

    ws.delete_interview(conn, later["id"])
    assert len(ws.job_interviews(conn, j1)) == 2
    with pytest.raises(ws.NotFound):
        ws.delete_interview(conn, later["id"])
