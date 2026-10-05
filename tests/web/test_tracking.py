"""Status model for the dashboard (dashboard-api Task 1)."""

from datetime import date

import pytest

from applypilot.database import init_db
from applypilot.tracking import (
    COLORS,
    STATUSES,
    days_since_submitted,
    effective_status,
    no_response,
    set_status,
    set_submitted_at,
    status_events,
    status_sql,
)

TODAY = date(2026, 10, 5)
URL = "https://example.com/jobs/1"

# (user_status, apply_status, deadline, expected)
CASES = [
    (None, None, None, "active"),
    (None, None, "", "active"),
    (None, None, "2026-10-05", "active"),  # due today is still active
    (None, None, "2026-12-01", "active"),
    (None, None, "2026-10-04", "inactive"),
    (None, None, "2025-01-01", "inactive"),
    ("in_progress", None, None, "in_progress"),
    ("in_progress", None, "2026-01-01", "in_progress"),  # user status beats the deadline
    ("submitted", None, "2026-01-01", "submitted"),
    (None, "applied", None, "submitted"),
    (None, "applied", "2026-01-01", "submitted"),
    ("in_progress", "applied", None, "submitted"),  # apply recorded it, so submitted beats in_progress
    (None, "failed", "2026-01-01", "inactive"),
    ("rejected", None, None, "rejected"),
    ("rejected", "applied", "2026-01-01", "rejected"),
    ("heard_back", None, "2026-01-01", "heard_back"),
    ("heard_back", "applied", None, "heard_back"),
]


@pytest.fixture
def conn(tmp_path):
    return init_db(tmp_path / "jobs.db")


def _insert(conn, url=URL, **cols):
    cols = {"url": url, "title": "Data Scientist", **cols}
    names = ", ".join(cols)
    conn.execute(f"INSERT INTO jobs ({names}) VALUES ({', '.join('?' * len(cols))})", tuple(cols.values()))
    conn.commit()


def _row(conn, url=URL):
    return dict(conn.execute("SELECT * FROM jobs WHERE url = ?", (url,)).fetchone())


def test_every_status_has_a_color():
    assert set(COLORS) == set(STATUSES)


@pytest.mark.parametrize("user,apply,deadline,expected", CASES)
def test_effective_status(user, apply, deadline, expected):
    job = {"user_status": user, "apply_status": apply, "deadline": deadline}
    assert effective_status(job, TODAY) == expected


def test_status_sql_agrees_with_python(conn):
    assert len(CASES) >= 12
    for i, (user, apply, deadline, _) in enumerate(CASES):
        _insert(conn, url=f"https://example.com/{i}", user_status=user, apply_status=apply, deadline=deadline)
    rows = conn.execute(f"SELECT url, {status_sql(TODAY.isoformat())} AS st FROM jobs").fetchall()
    got = {r["url"]: r["st"] for r in rows}
    for i, (user, apply, deadline, expected) in enumerate(CASES):
        job = {"user_status": user, "apply_status": apply, "deadline": deadline}
        assert got[f"https://example.com/{i}"] == effective_status(job, TODAY) == expected


def test_status_sql_rejects_a_non_date():
    with pytest.raises(ValueError):
        status_sql("2026-10-05' OR 1=1 --")


def test_days_since_submitted():
    job = {"user_status": "submitted", "submitted_at": "2026-09-28T15:00:00+00:00"}
    assert days_since_submitted(job, TODAY) == 7
    assert days_since_submitted({"user_status": "submitted", "submitted_at": "2026-10-05"}, TODAY) == 0
    # Only while Submitted.
    assert days_since_submitted({**job, "user_status": "rejected"}, TODAY) is None
    assert days_since_submitted({"submitted_at": "2026-09-28"}, TODAY) is None
    # `applypilot apply` submissions count from applied_at.
    assert days_since_submitted({"apply_status": "applied", "applied_at": "2026-10-01T09:00:00"}, TODAY) == 4


def test_no_response():
    assert no_response({"user_status": "submitted"}, TODAY)
    assert not no_response({"user_status": "submitted", "responded_at": "2026-10-02"}, TODAY)
    assert not no_response({"user_status": "heard_back", "responded_at": "2026-10-02"}, TODAY)


def test_set_submitted_defaults_to_now_and_clears_response(conn):
    _insert(conn, user_status="rejected", responded_at="2026-10-01T00:00:00")
    result = set_status(conn, URL, "submitted")
    row = _row(conn)
    assert result["from_status"] == "rejected" and result["to_status"] == "submitted"
    assert row["user_status"] == "submitted"
    assert row["submitted_at"][:10] == result["at"][:10]
    assert row["responded_at"] is None
    assert row["status_updated_at"] == result["at"]


def test_set_submitted_with_a_date(conn):
    _insert(conn)
    set_status(conn, URL, "submitted", submitted_at="2026-09-30")
    assert _row(conn)["submitted_at"] == "2026-09-30"
    with pytest.raises(ValueError):
        set_status(conn, URL, "submitted", submitted_at="last tuesday")


@pytest.mark.parametrize("status", ["rejected", "heard_back"])
def test_response_sets_responded_at(conn, status):
    _insert(conn)
    set_status(conn, URL, "submitted", submitted_at="2026-09-30")
    set_status(conn, URL, status)
    row = _row(conn)
    assert row["user_status"] == status
    assert row["responded_at"]
    assert row["submitted_at"] == "2026-09-30"


def test_reset_to_derived_status(conn):
    _insert(conn, deadline="2020-01-01")
    set_status(conn, URL, "in_progress")
    result = set_status(conn, URL, None)
    assert _row(conn)["user_status"] is None
    assert result["to_status"] == "inactive"


def test_every_change_is_logged_newest_first(conn):
    _insert(conn)
    set_status(conn, URL, "in_progress", source="generate")
    set_status(conn, URL, "submitted")
    set_status(conn, URL, "heard_back")
    events = status_events(conn, URL)
    assert [e["to_status"] for e in events] == ["heard_back", "submitted", "in_progress"]
    assert [e["from_status"] for e in events] == ["submitted", "in_progress", "active"]
    assert events[-1]["source"] == "generate"


def test_unknown_job_and_status(conn):
    with pytest.raises(KeyError):
        set_status(conn, "https://nope", "submitted")
    _insert(conn)
    with pytest.raises(ValueError):
        set_status(conn, URL, "active")  # derived statuses can't be set


def test_set_submitted_at_edits_without_an_event(conn):
    _insert(conn)
    set_status(conn, URL, "submitted")
    assert set_submitted_at(conn, URL, "2026-09-01") == "2026-09-01"
    assert _row(conn)["submitted_at"] == "2026-09-01"
    assert len(status_events(conn, URL)) == 1


def test_init_db_is_idempotent(tmp_path):
    init_db(tmp_path / "jobs.db")
    conn = init_db(tmp_path / "jobs.db")
    assert conn.execute("SELECT COUNT(*) FROM status_events").fetchone()[0] == 0
