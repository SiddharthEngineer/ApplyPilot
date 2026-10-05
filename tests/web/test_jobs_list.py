"""GET /app/api/jobs filters, sorting, paging and /app/api/facets (dashboard-api Task 3)."""

from datetime import date

import pytest
from web_helpers import add_job

from applypilot.database import job_key
from applypilot.web import app as app_mod

TODAY = date(2026, 10, 5)


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setattr(app_mod, "today", lambda: TODAY)


@pytest.fixture
def jobs(conn):
    """Seven jobs covering every status, role, mode and date the filters look at."""
    add_job(conn, "https://x/ds-remote", title="Data Scientist", company="Acme", role_category="data_science",
            work_mode="remote", location="Remote, US", fit_score=9, discovered_at="2026-10-01T10:00:00+00:00",
            deadline="2026-10-20", user_status="submitted", submitted_at="2026-09-28T00:00:00+00:00")
    add_job(conn, "https://x/ds-chicago", title="Senior Data Scientist", company="Globex",
            role_category="data_science", work_mode="hybrid", location="Chicago, IL", location_city="Chicago",
            location_state="IL", fit_score=7, discovered_at="2026-10-03T10:00:00+00:00", deadline="2026-10-01")
    add_job(conn, "https://x/mle", title="ML Engineer", company="Initech", role_category="ml_ai", work_mode="onsite",
            location="New York, NY", fit_score=8, discovered_at="2026-09-20T10:00:00+00:00",
            user_status="rejected", submitted_at="2026-09-21", responded_at="2026-09-30T00:00:00+00:00")
    add_job(conn, "https://x/swe", title="Software Engineer", company="Hooli", role_category="software_engineering",
            work_mode="remote", location="Austin, TX", fit_score=None, discovered_at="2026-10-04T10:00:00+00:00",
            user_status="in_progress")
    add_job(conn, "https://x/de", title="Data Engineer", company=None, site="Umbrella", role_category="data_engineering",
            work_mode="onsite", location="Chicago, IL", fit_score=5, discovered_at="2026-10-05T01:00:00+00:00",
            deadline="2026-10-05", user_status="heard_back", responded_at="2026-10-04")
    add_job(conn, "https://x/applied", title="Analytics Engineer", company="Stark", role_category="data_engineering",
            work_mode="remote", location="Remote", fit_score=6, discovered_at="2026-09-01T10:00:00+00:00",
            apply_status="applied", applied_at="2026-10-02T09:00:00")
    add_job(conn, "https://x/no-desc", title="Data Analyst 100%_off", company="Wayne", full_description=None,
            discovered_at="2026-09-15T10:00:00+00:00", deadline="")
    conn.execute("UPDATE jobs SET job_key = NULL")  # as left by discovery paths that don't set it
    conn.commit()


def _urls(resp):
    assert resp.status_code == 200, resp.text
    return [item["key"] for item in resp.json()["items"]]


def _keys(*names):
    return [job_key(f"https://x/{n}") for n in names]


def get(client, **params):
    return client.get("/app/api/jobs", params=params)


def test_default_lists_everything_newest_first(client, jobs):
    r = get(client)
    body = r.json()
    assert body["total"] == 7 and body["page"] == 1
    assert _urls(r) == _keys("de", "swe", "ds-chicago", "ds-remote", "mle", "no-desc", "applied")


def test_item_shape(client, jobs):
    items = {i["key"]: i for i in get(client).json()["items"]}
    ds = items[job_key("https://x/ds-remote")]
    assert ds == {
        "key": job_key("https://x/ds-remote"), "title": "Data Scientist", "company": "Acme",
        "role_category": "data_science", "location": "Remote, US", "work_mode": "remote", "fit_score": 9,
        "discovered_at": "2026-10-01T10:00:00+00:00", "deadline": "2026-10-20", "status": "submitted",
        "status_color": "blue", "days_since_submitted": 7,
    }
    assert items[job_key("https://x/de")]["company"] == "Umbrella"  # site stands in for a missing company
    assert items[job_key("https://x/no-desc")]["deadline"] is None
    assert items[job_key("https://x/applied")]["days_since_submitted"] == 3


@pytest.mark.parametrize("status,expected", [
    ("active", ["no-desc"]),
    ("inactive", ["ds-chicago"]),
    ("in_progress", ["swe"]),
    ("submitted", ["ds-remote", "applied"]),
    ("rejected", ["mle"]),
    ("heard_back", ["de"]),
    ("no_response", ["ds-remote", "applied"]),
])
def test_status_filter(client, jobs, status, expected):
    assert sorted(_urls(get(client, status=status))) == sorted(_keys(*expected))


def test_status_filter_repeats_and_all(client, jobs):
    assert sorted(_urls(get(client, status=["rejected", "heard_back"]))) == sorted(_keys("mle", "de"))
    assert get(client, status=["all", "rejected"]).json()["total"] == 7
    assert get(client, status="bogus").status_code == 422


def test_role_and_work_mode(client, jobs):
    assert sorted(_urls(get(client, role="data_science"))) == sorted(_keys("ds-remote", "ds-chicago"))
    assert sorted(_urls(get(client, role=["ml_ai", "software_engineering"]))) == sorted(_keys("mle", "swe"))
    assert sorted(_urls(get(client, work_mode="remote"))) == sorted(_keys("ds-remote", "swe", "applied"))
    assert sorted(_urls(get(client, work_mode=["onsite", "hybrid"]))) == sorted(_keys("ds-chicago", "mle", "de"))


def test_combined_filters(client, jobs):
    r = get(client, status="submitted", role="data_science", work_mode="remote", sort="-fit_score", page=1)
    assert _urls(r) == _keys("ds-remote")


def test_location(client, jobs):
    assert sorted(_urls(get(client, location="chicago"))) == sorted(_keys("ds-chicago", "de"))
    assert sorted(_urls(get(client, location="IL"))) == sorted(_keys("ds-chicago", "de"))
    # "remote" also matches work_mode=remote (swe's location is Austin).
    assert sorted(_urls(get(client, location="Remote"))) == sorted(_keys("ds-remote", "swe", "applied"))


def test_found_dates_are_inclusive(client, jobs):
    assert sorted(_urls(get(client, found_from="2026-10-04"))) == sorted(_keys("swe", "de"))
    assert sorted(_urls(get(client, found_to="2026-09-15"))) == sorted(_keys("applied", "no-desc"))
    assert _urls(get(client, found_from="2026-10-03", found_to="2026-10-03")) == _keys("ds-chicago")
    assert get(client, found_from="10/03/2026").status_code == 422


def test_due_dates(client, jobs):
    assert sorted(_urls(get(client, due_from="2026-10-05"))) == sorted(_keys("ds-remote", "de"))
    assert _urls(get(client, due_to="2026-10-04")) == _keys("ds-chicago")  # '' deadlines never match


def test_score_range(client, jobs):
    assert sorted(_urls(get(client, score_min=8))) == sorted(_keys("ds-remote", "mle"))
    assert sorted(_urls(get(client, score_min=6, score_max=7))) == sorted(_keys("ds-chicago", "applied"))
    assert get(client, score_min=11).status_code == 422


def test_text_search(client, jobs):
    assert sorted(_urls(get(client, q="data sci"))) == sorted(_keys("ds-remote", "ds-chicago"))
    assert _urls(get(client, q="GLOBEX")) == _keys("ds-chicago")
    assert _urls(get(client, q="umbrella")) == _keys("de")  # site
    # LIKE wildcards are literal.
    assert _urls(get(client, q="100%_")) == _keys("no-desc")
    assert get(client, q="%").json()["total"] == 1


def test_sort_with_nulls_last(client, jobs):
    assert sorted(_urls(get(client, sort="-fit_score"))[-2:]) == sorted(_keys("swe", "no-desc"))
    assert _urls(get(client, sort="fit_score"))[:2] == _keys("de", "applied")
    assert sorted(_urls(get(client, sort="fit_score"))[-2:]) == sorted(_keys("swe", "no-desc"))
    by_deadline = _urls(get(client, sort="deadline"))
    assert by_deadline[:3] == _keys("ds-chicago", "de", "ds-remote")
    assert _urls(get(client, sort="-deadline"))[:3] == _keys("ds-remote", "de", "ds-chicago")
    assert _urls(get(client, sort="discovered_at"))[0] == _keys("applied")[0]


@pytest.mark.parametrize("sort", ["title", "fit_score; DROP TABLE jobs", "-url", "--fit_score", "fit_score DESC"])
def test_sort_injection_is_rejected(client, jobs, sort):
    assert get(client, sort=sort).status_code == 422
    assert get(client).json()["total"] == 7


def test_pagination(client, jobs):
    first = get(client, page_size=3, page=1).json()
    third = get(client, page_size=3, page=3).json()
    assert first["total"] == third["total"] == 7
    assert len(first["items"]) == 3 and len(third["items"]) == 1
    seen = [i["key"] for p in (1, 2, 3) for i in get(client, page_size=3, page=p).json()["items"]]
    assert len(set(seen)) == 7
    assert get(client, page=9).json()["items"] == []
    assert get(client, page_size=201).status_code == 422
    assert get(client, page=0).status_code == 422


def test_list_backfills_job_keys(client, jobs, conn):
    get(client)
    assert conn.execute("SELECT COUNT(*) FROM jobs WHERE job_key IS NULL").fetchone()[0] == 0


def test_facets(client, jobs):
    body = client.get("/app/api/facets").json()
    assert body["total"] == 7
    status = {s["value"]: s["count"] for s in body["status"]}
    assert status == {"active": 1, "inactive": 1, "in_progress": 1, "submitted": 2, "rejected": 1,
                      "heard_back": 1, "no_response": 2}
    assert {s["value"]: s["color"] for s in body["status"]}["inactive"] == "orange"
    roles = {r["value"]: r["count"] for r in body["role_category"]}
    assert roles == {"data_science": 2, "data_engineering": 2, "ml_ai": 1, "software_engineering": 1}
    assert body["work_mode"][0] == {"value": "remote", "count": 3}
    assert body["location"][0] == {"value": "Chicago, IL", "count": 2}
    assert len(body["location"]) == 5


def test_empty_db(client):
    assert get(client).json() == {"total": 0, "page": 1, "page_size": 50, "items": []}
    assert client.get("/app/api/facets").json()["total"] == 0
