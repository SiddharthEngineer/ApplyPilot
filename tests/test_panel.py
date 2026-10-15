"""Resume panel (strelitzia.scoring.panel): draft history, item selection, packets, import and the report."""

import json
import random

import pytest

from strelitzia.database import init_db
from strelitzia.scoring import panel
from strelitzia.scoring import pdf as pdf_mod
from strelitzia.scoring import workbench as wb
from workbench_helpers import add_job_row, fake_render_pdf, good_result, inputs, reply


@pytest.fixture
def conn(tmp_path):
    return init_db(tmp_path / "panel.db")


def add_draft(conn, url, version, rules="r1", headline=None, at=None, text=None):
    """A drafts row as import_result writes it (synthetic resume content)."""
    headline = headline or f"Headline {url[-2:]} {version}"
    resume = {"headline": headline, "roles": [], "skills": [], "dropped_roles": []}
    draft_id = f"d-{url[-2:]}-{version}-{rules}"
    conn.execute(
        "INSERT INTO drafts (id, job_url, version, rules_version, resume_json, resume_text, cover_json, "
        "coverage_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (draft_id, url, version, rules, json.dumps(resume), text or f"Alex Tester\n{headline}\n", "null",
         json.dumps({"covered": [], "missing": []}), at or f"2026-0{min(version, 9)}-01T00:00:{url[-2:]}+00:00"),
    )
    conn.commit()
    return draft_id


def add_job(conn, no: int, **kw):
    return add_job_row(conn, url=f"https://example.com/jobs/{no:02d}", job_key=f"key{no:02d}",
                       title=f"Data Engineer {no}", **kw)


def seed_jobs(conn, count: int, versions: int = 2, rules=("r1", "r2")):
    for no in range(1, count + 1):
        job = add_job(conn, no)
        for v in range(1, versions + 1):
            add_draft(conn, job["url"], v, rules=rules[min(v - 1, len(rules) - 1)])


# ── Task 1: draft history and selection ──

def test_import_result_records_every_accepted_draft(conn, tmp_path, monkeypatch):
    monkeypatch.setattr(pdf_mod, "render_pdf", fake_render_pdf)
    job = add_job_row(conn)
    assert wb.import_result(conn, job, reply(good_result()), inputs=inputs(), out_dir=tmp_path / "out").ok
    second = good_result(headline="Data engineer for Airflow pipelines")
    assert wb.import_result(conn, job, reply(second), inputs=inputs(), out_dir=tmp_path / "out").ok
    rows = [dict(r) for r in conn.execute("SELECT * FROM drafts ORDER BY version").fetchall()]
    assert [r["version"] for r in rows] == [1, 2]
    assert all(r["job_url"] == job["url"] and r["rules_version"] == wb.RULES_VERSION for r in rows)
    assert json.loads(rows[1]["resume_json"])["headline"] == "Data engineer for Airflow pipelines"
    assert "Data engineer for Airflow pipelines" in rows[1]["resume_text"]
    assert json.loads(rows[0]["cover_json"])["evidence_slugs"]
    assert "pct_required" in json.loads(rows[0]["coverage_json"])
    assert len({r["id"] for r in rows}) == 2


def test_latest_vs_previous_takes_the_last_two_drafts_newest_first(conn):
    a, b, c = add_job(conn, 1), add_job(conn, 2), add_job(conn, 3)
    add_draft(conn, a["url"], 1, at="2026-01-01T00:00:00+00:00")
    add_draft(conn, a["url"], 2, at="2026-01-05T00:00:00+00:00")
    add_draft(conn, a["url"], 3, at="2026-03-01T00:00:00+00:00")
    add_draft(conn, b["url"], 1, at="2026-02-01T00:00:00+00:00")
    add_draft(conn, b["url"], 2, at="2026-04-01T00:00:00+00:00")
    add_draft(conn, c["url"], 1, at="2026-05-01T00:00:00+00:00")  # one draft: not comparable
    items = panel.select_items(conn, n=5)
    assert [i.job_url for i in items] == [b["url"], a["url"]]
    assert (items[1].draft_a["version"], items[1].draft_b["version"]) == (2, 3)
    assert items[0].title == "Data Engineer 2" and items[0].company == "Initech"
    assert len(panel.select_items(conn, n=1)) == 1


def test_identical_drafts_are_not_comparable(conn):
    job = add_job(conn, 1)
    add_draft(conn, job["url"], 1, headline="Same")
    add_draft(conn, job["url"], 2, headline="Same")
    assert panel.select_items(conn) == []


def test_rules_a_vs_b_compares_the_latest_draft_under_each_rules_version(conn):
    a, b = add_job(conn, 1), add_job(conn, 2)
    add_draft(conn, a["url"], 1, rules="r1", at="2026-01-01T00:00:00+00:00")
    add_draft(conn, a["url"], 2, rules="r1", at="2026-01-02T00:00:00+00:00")
    add_draft(conn, a["url"], 3, rules="r2", at="2026-02-01T00:00:00+00:00")
    add_draft(conn, b["url"], 1, rules="r2", at="2026-02-02T00:00:00+00:00")
    add_draft(conn, b["url"], 2, rules="r2", at="2026-02-03T00:00:00+00:00")
    items = panel.select_items(conn, mode="rules_a_vs_b")
    assert [i.job_url for i in items] == [a["url"]]
    assert (items[0].draft_a["version"], items[0].draft_b["version"]) == (2, 3)
    assert panel.select_items(conn, mode="rules_a_vs_b", rules_a="r2", rules_b="r1")[0].draft_a["version"] == 3
    with pytest.raises(ValueError, match="Unknown panel mode"):
        panel.select_items(conn, mode="nope")


def test_create_run_stores_a_random_order_per_item(conn):
    seed_jobs(conn, 6)
    run_id = panel.create_run(conn, n=5, rng=random.Random(3))
    run = panel.get_run(conn, run_id)
    assert run["status"] == "open" and run["mode"] == "latest_vs_previous"
    assert (run["rules_version_a"], run["rules_version_b"]) == ("r1", "r2")
    items = panel.run_items(conn, run_id)
    assert [i["item_no"] for i in items] == [1, 2, 3, 4, 5]
    orders = [i["shown_first"] for i in items]
    assert set(orders) <= {"a", "b"} and len(set(orders)) == 2  # random, and both orders occur with this seed
    assert all(i["draft_a"]["version"] == 1 and i["draft_b"]["version"] == 2 for i in items)
    # The order is stored, not recomputed.
    assert [i["shown_first"] for i in panel.run_items(conn, run_id)] == orders
    assert panel.list_runs(conn)[0]["id"] == run_id


def test_create_run_without_comparable_jobs_is_an_error(conn):
    add_draft(conn, add_job(conn, 1)["url"], 1)
    with pytest.raises(ValueError, match="two comparable drafts"):
        panel.create_run(conn)
