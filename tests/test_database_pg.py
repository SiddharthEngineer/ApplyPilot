"""Core database behavior on both backends: SQLite (always) and Postgres (with APPLYPILOT_TEST_DATABASE_URL).

Run the Postgres half with:
    APPLYPILOT_TEST_DATABASE_URL=postgresql://applypilot:$PW@127.0.0.1:5432/applypilot_test pytest -m pg -q
"""

import pytest

from applypilot.database import (
    IntegrityError,
    backend_name,
    ensure_columns,
    get_connection,
    get_jobs_by_stage,
    get_stats,
    init_db,
    reset_score_errors,
    store_jobs,
    table_columns,
)

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
def test_integrity_error_is_catchable(db_target):
    conn = init_db(db_target)
    conn.execute("INSERT INTO jobs (url, title) VALUES (?, ?)", ("u1", "a"))
    with pytest.raises(IntegrityError):
        conn.execute("INSERT INTO jobs (url, title) VALUES (?, ?)", ("u1", "b"))
    conn.execute("INSERT INTO jobs (url, title) VALUES (?, ?)", ("u2", "c"))
    conn.commit()
    assert [r[0] for r in conn.execute("SELECT url FROM jobs ORDER BY url").fetchall()] == ["u1", "u2"]


def _seed_stages(conn) -> None:
    store_jobs(conn, _jobs(6), site="RemoteOK", strategy="json_ld")
    u = "https://example.com/job/"
    conn.execute("UPDATE jobs SET full_description = 'jd', detail_scraped_at = 'x' WHERE url != ?", (f"{u}5",))
    conn.execute("UPDATE jobs SET fit_score = 9, scored_at = '2026-10-05' WHERE url = ?", (f"{u}0",))
    conn.execute("UPDATE jobs SET fit_score = 7, scored_at = '2026-10-05' WHERE url = ?", (f"{u}1",))
    conn.execute("UPDATE jobs SET fit_score = 3, scored_at = '2026-10-05' WHERE url = ?", (f"{u}2",))
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
    assert stats["score_distribution"] == [(9, 1), (7, 1), (3, 1)]
    assert stats["tailored"] == 1
    assert stats["untailored_eligible"] == 1
    assert stats["ready_to_apply"] == 1


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
