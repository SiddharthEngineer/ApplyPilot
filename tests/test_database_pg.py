"""Core database behavior on both backends: SQLite (always) and Postgres (with STRELITZIA_TEST_DATABASE_URL).

Run the Postgres half with:
    STRELITZIA_TEST_DATABASE_URL=postgresql://applypilot:$PW@127.0.0.1:5432/applypilot_test pytest -m pg -q
"""

import pytest

from strelitzia.database import (
    IntegrityError,
    backend_name,
    USER_TABLES,
    _ensure_tables,
    ensure_columns,
    get_connection,
    get_jobs_by_stage,
    get_jobs_by_urls,
    get_stats,
    init_db,
    reset_score_errors,
    store_jobs,
    table_columns,
)
from strelitzia.users import owner

BACKENDS = pytest.mark.parametrize(
    "db_target", ["sqlite", pytest.param("pg", marks=pytest.mark.pg)], indirect=True
)


def _jobs(n: int, prefix: str = "https://example.com/job/") -> list[dict]:
    return [
        {"url": f"{prefix}{i}", "title": f"Engineer {i}", "salary": "100% remote, $150k",
         "description": "short", "location": "Remote"}
        for i in range(n)
    ]


@BACKENDS
def test_init_db_is_idempotent(db_target):
    conn = init_db(db_target)
    init_db(db_target)
    assert {"url", "title", "fit_score", "company", "resume_drive_url"} <= table_columns(conn)
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
    assert backend_name(conn) == ("postgresql" if str(db_target).startswith("postgresql") else "sqlite")


@BACKENDS
def test_ensure_columns_migrates_an_old_table(db_target):
    conn = get_connection(db_target)
    conn.execute("CREATE TABLE jobs (url TEXT PRIMARY KEY, title TEXT, site TEXT, strategy TEXT)")
    conn.executemany("INSERT INTO jobs VALUES (?, ?, ?, ?)", [
        ("u1", "Engineer", "NVIDIA", "workday_api"),
        ("u2", "Engineer", "Acme", "ats_greenhouse"),
        ("u3", "Engineer", "indeed", "jobspy"),
    ])
    conn.commit()

    added = ensure_columns(conn)

    assert "company" in added and "fit_score" in added
    assert ensure_columns(conn) == []
    rows = {r["url"]: r["company"] for r in conn.execute("SELECT url, company FROM jobs").fetchall()}
    assert rows == {"u1": "NVIDIA", "u2": "Acme", "u3": None}


@BACKENDS
def test_store_jobs_dedupes_by_url(db_target):
    conn = init_db(db_target)
    assert store_jobs(conn, _jobs(3), site="RemoteOK", strategy="json_ld") == (3, 0)
    # Duplicates in the middle of a batch must not lose the new rows around them.
    batch = [_jobs(5)[3], _jobs(5)[0], _jobs(5)[4]]
    assert store_jobs(conn, batch, site="RemoteOK", strategy="json_ld") == (2, 1)
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 5
    row = conn.execute("SELECT * FROM jobs WHERE url = ?", ("https://example.com/job/0",)).fetchone()
    assert row["salary"] == "100% remote, $150k"
    assert dict(row)["site"] == "RemoteOK"


@BACKENDS
def test_job_helpers_filter_by_user(db_target):
    conn = init_db(db_target)
    owner_id = owner(conn).id
    assert store_jobs(conn, _jobs(3), site="RemoteOK", strategy="json_ld") == (3, 0)  # no user = the owner
    # Another user tracks one of the same postings plus a new one, as separate rows.
    assert store_jobs(conn, _jobs(4)[2:], site="Dice", strategy="api", user_id="other") == (2, 0)
    assert store_jobs(conn, _jobs(4)[2:], site="Dice", strategy="api", user_id="other") == (0, 2)
    conn.execute("UPDATE jobs SET full_description = 'jd'")
    conn.commit()

    assert get_stats(conn)["total"] == 5
    assert get_stats(conn, user_id=owner_id)["by_site"] == [("RemoteOK", 3)]
    assert get_stats(conn, user_id="other")["with_description"] == 2
    assert len(get_jobs_by_stage(conn, "enriched", user_id="other")) == 2
    assert len(get_jobs_by_stage(conn, "enriched")) == 5
    u = "https://example.com/job/"
    picked = get_jobs_by_urls(conn, [f"{u}2", f"{u}0"], user_id="other")
    assert [(j["url"], j["site"]) for j in picked] == [(f"{u}2", "Dice")]
    assert [j["url"] for j in get_jobs_by_urls(conn, [f"{u}2", f"{u}0"])] == [f"{u}2", f"{u}0"]


@BACKENDS
def test_integrity_error_is_catchable(db_target):
    conn = init_db(db_target)
    conn.execute("INSERT INTO jobs (url, title) VALUES (?, ?)", ("u1", "a"))
    with pytest.raises(IntegrityError):
        conn.execute("INSERT INTO jobs (url, title) VALUES (?, ?)", ("u1", "b"))
    conn.execute("INSERT INTO jobs (url, title) VALUES (?, ?)", ("u2", "c"))
    conn.commit()
    assert [r[0] for r in conn.execute("SELECT url FROM jobs ORDER BY url").fetchall()] == ["u1", "u2"]


def _old_shape_db(conn) -> None:
    """A database from before users: jobs keyed on url, side rows without user_id, NULL-user preferences."""
    _ensure_tables(conn)
    conn.execute("CREATE TABLE jobs (url TEXT PRIMARY KEY, title TEXT, site TEXT, strategy TEXT)")
    ensure_columns(conn)
    conn.executemany("INSERT INTO jobs (url, title, job_key, tailor_attempts) VALUES (?, ?, ?, ?)",
                     [(f"https://example.com/job/{i}", f"Engineer {i}", f"key{i}", i) for i in range(4)])
    conn.execute("INSERT INTO status_events (id, url, to_status) VALUES ('e1', 'https://example.com/job/0', 'x')")
    conn.execute("INSERT INTO contacts (id, name) VALUES ('c1', 'Ada')")
    conn.execute("INSERT INTO job_contacts (job_url, contact_id) VALUES ('https://example.com/job/0', 'c1')")
    conn.execute("INSERT INTO interviews (id, job_url) VALUES ('i1', 'https://example.com/job/1')")
    conn.execute("INSERT INTO drafts (id, job_url, version) VALUES ('d1', 'https://example.com/job/1', 1)")
    conn.execute("INSERT INTO panel_runs (id, status) VALUES ('p1', 'open')")
    conn.execute("INSERT INTO dashboard_tasks (id, url, state) VALUES ('t1', 'https://example.com/job/2', 'done')")
    conn.execute("INSERT INTO llm_usage (id, purpose, cost_usd) VALUES ('l1', 'score', 0.01)")
    conn.execute("INSERT INTO user_preferences (user_id, prefs_json, updated_at) VALUES (NULL, '{\"a\": 1}', '1')")
    conn.execute("INSERT INTO user_preferences (user_id, prefs_json, updated_at) VALUES (NULL, '{\"a\": 2}', '2')")
    conn.commit()


@BACKENDS
def test_init_db_migrates_an_old_db_to_users(db_target):
    conn = get_connection(db_target)
    _old_shape_db(conn)

    init_db(db_target)
    conn = init_db(db_target)

    owner_id = owner(conn).id
    assert conn.execute("SELECT COUNT(*), COUNT(DISTINCT user_id) FROM jobs").fetchone()[:] == (4, 1)
    assert conn.execute("SELECT DISTINCT user_id FROM jobs").fetchone()[0] == owner_id
    # Columns and their values survive the rebuild.
    row = conn.execute("SELECT * FROM jobs WHERE url = ?", ("https://example.com/job/3",)).fetchone()
    assert (row["title"], row["job_key"], row["tailor_attempts"]) == ("Engineer 3", "key3", 3)
    for table in ("llm_usage", *USER_TABLES):
        assert conn.execute(f"SELECT COUNT(*), COUNT(DISTINCT user_id) FROM {table}").fetchone()[:] == (1, 1), table
        assert conn.execute(f"SELECT user_id FROM {table}").fetchone()[0] == owner_id, table
    # The newest of the NULL preference rows becomes the owner's.
    assert conn.execute("SELECT prefs_json FROM user_preferences WHERE user_id = ?",
                        (owner_id,)).fetchone()[0] == '{"a": 2}'

    # Two users can track the same posting; one user can't hold it twice.
    conn.execute("INSERT INTO jobs (user_id, url, job_key) VALUES ('other', 'https://example.com/job/0', 'key0')")
    with pytest.raises(IntegrityError):
        conn.execute("INSERT INTO jobs (url, title) VALUES ('https://example.com/job/0', 'dup')")
    with pytest.raises(IntegrityError):
        conn.execute("INSERT INTO jobs (user_id, url, job_key) VALUES ('other', 'https://example.com/job/x', 'key0')")
    # Inserts that don't name a user (callers not threaded yet) belong to the owner.
    conn.execute("INSERT INTO jobs (url) VALUES ('https://example.com/job/new')")
    conn.execute("INSERT INTO contacts (id, name) VALUES ('c2', 'Bob')")
    conn.commit()
    assert conn.execute("SELECT user_id FROM jobs WHERE url = 'https://example.com/job/new'").fetchone()[0] == owner_id
    assert conn.execute("SELECT user_id FROM contacts WHERE id = 'c2'").fetchone()[0] == owner_id
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 6


@BACKENDS
def test_fresh_db_is_keyed_per_user(db_target):
    conn = init_db(db_target)
    init_db(db_target)
    assert {"user_id", "url"} <= table_columns(conn)
    for table in USER_TABLES:
        assert "user_id" in table_columns(conn, table), table
    conn.execute("INSERT INTO jobs (user_id, url) VALUES ('a', 'u'), ('b', 'u')")
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 2


def _seed_stages(conn) -> None:
    store_jobs(conn, _jobs(6), site="RemoteOK", strategy="json_ld")
    u = "https://example.com/job/"
    conn.execute("UPDATE jobs SET full_description = 'jd', detail_scraped_at = 'x', details_json = '{}' "
                 "WHERE url != ?", (f"{u}5",))
    scored = "fit_score = ?, scored_at = '2026-10-05', assessment_hash = '1:h'"
    conn.execute(f"UPDATE jobs SET {scored} WHERE url = ?", (90, f"{u}0"))
    conn.execute(f"UPDATE jobs SET {scored} WHERE url = ?", (70, f"{u}1"))
    conn.execute(f"UPDATE jobs SET {scored} WHERE url = ?", (30, f"{u}2"))
    conn.execute("UPDATE jobs SET tailored_resume_path = '/r.pdf', application_url = 'https://apply' WHERE url = ?",
                 (f"{u}0",))
    conn.commit()


@BACKENDS
def test_get_jobs_by_stage(db_target):
    conn = init_db(db_target)
    _seed_stages(conn)
    u = "https://example.com/job/"

    assert [j["url"] for j in get_jobs_by_stage(conn, "scored")] == [f"{u}0", f"{u}1", f"{u}2"]
    assert [j["url"] for j in get_jobs_by_stage(conn, "pending_tailor")] == [f"{u}1"]
    assert [j["url"] for j in get_jobs_by_stage(conn, "pending_apply")] == [f"{u}0"]
    assert {j["url"] for j in get_jobs_by_stage(conn, "pending_score")} == {f"{u}3", f"{u}4"}
    assert len(get_jobs_by_stage(conn, "discovered", limit=2)) == 2
    # Unscored jobs sort after scored ones (NULLS LAST) on both backends.
    assert get_jobs_by_stage(conn, "discovered", limit=0)[-1]["fit_score"] is None


@BACKENDS
def test_get_stats(db_target):
    conn = init_db(db_target)
    _seed_stages(conn)
    stats = get_stats(conn)
    assert stats["total"] == 6
    assert stats["by_site"] == [("RemoteOK", 6)]
    assert stats["pending_detail"] == 1
    assert stats["with_description"] == 5
    assert stats["scored"] == 3
    assert stats["unscored"] == 2
    assert stats["score_distribution"] == [(90, 1), (70, 1), (30, 1)]
    assert stats["tailored"] == 1
    assert stats["untailored_eligible"] == 1
    assert stats["ready_to_apply"] == 1
    assert stats["applied"] == 0
    # A job submitted in the dashboard counts as applied and is no longer ready to apply.
    conn.execute("UPDATE jobs SET user_status = 'submitted' WHERE url = ?", ("https://example.com/job/0",))
    conn.commit()
    stats = get_stats(conn)
    assert (stats["ready_to_apply"], stats["applied"]) == (0, 1)


@BACKENDS
def test_reset_score_errors(db_target):
    conn = init_db(db_target)
    store_jobs(conn, _jobs(3), site="RemoteOK", strategy="json_ld")
    u = "https://example.com/job/"
    conn.execute("UPDATE jobs SET fit_score = 0, score_reasoning = ? WHERE url = ?", ("\nLLM error: 500", f"{u}0"))
    conn.execute("UPDATE jobs SET score_reasoning = ?, score_attempts = 2 WHERE url = ?", ("LLM error: 429", f"{u}1"))
    conn.execute("UPDATE jobs SET fit_score = 6, score_reasoning = ? WHERE url = ?", ("good fit", f"{u}2"))
    conn.commit()

    assert reset_score_errors(conn) == 2
    rows = {r["url"]: (r["fit_score"], r["score_attempts"])
            for r in conn.execute("SELECT url, fit_score, score_attempts FROM jobs").fetchall()}
    assert rows == {f"{u}0": (None, 0), f"{u}1": (None, 0), f"{u}2": (6, 0)}


@pytest.mark.pg
def test_reads_leave_no_open_transaction(pg_db):
    """A read must not hold a transaction open (it would block another process's ALTER TABLE)."""
    conn = init_db(pg_db)
    conn.execute("SELECT COUNT(*) FROM jobs").fetchone()
    assert not conn.in_transaction
    status = conn.execute(
        "SELECT state FROM pg_stat_activity WHERE pid = pg_backend_pid()"
    ).fetchone()[0]
    assert status == "active"  # this query itself; not "idle in transaction"
