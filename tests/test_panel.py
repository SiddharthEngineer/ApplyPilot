"""Resume panel (strelitzia.scoring.panel): draft history, item selection, packets, import and the report."""

import json
import random
from datetime import datetime, timedelta

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


# ── Task 2: rubric, packets and import ──

def version_grade(overall="B", clarity=3, flags=(), quals=None):
    quals = quals if quals is not None else [
        {"text": "Airflow", "kind": "required", "status": "met", "evidence": "Airflow"},
        {"text": "dbt", "kind": "preferred", "status": "not_met", "evidence": ""},
    ]
    return {"qualifications": quals, "overall": overall, "clarity": clarity, "red_flags": list(flags)}


def model_reply(winners, first=None, second=None, count=None) -> str:
    """A panel reply with the given winner per item (first | second | tie), in the packet's order."""
    items = [{"item": no, "first": first or version_grade(), "second": second or version_grade(),
              "winner": w, "why": "Clearer fit."} for no, w in enumerate(winners, 1)]
    return reply({"panel": 1, "items": items[:count] if count is not None else items}, prose="My grades follow.")


@pytest.fixture
def run_id(conn):
    seed_jobs(conn, 5)
    return panel.create_run(conn, n=5, rng=random.Random(3))


def test_rubric_doc_has_the_grading_steps_and_format():
    html = panel.rubric_doc()
    for needle in ("met, partial or not_met", "Overall grade A-D", "Clarity 1-5", "Red flags", "Winner",
                   "&quot;panel&quot;: 1", "&quot;winner&quot;", "verbatim"):
        assert needle in html, needle
    text = panel.rubric_text()
    assert " A/B" not in text and "newer" not in text.replace("which version is newer", "")


def test_packets_are_blind_and_swapped(conn, run_id):
    run = panel.get_run(conn, run_id)
    p1, p2 = panel.panel_packet(conn, run), panel.panel_packet(conn, run, swapped=True)
    assert run["packet_text"] == p1.text and run["packet_text_swapped"] == p2.text
    assert p1.prompt.endswith(p1.text) and "Strelitzia panel rubric" in p1.prompt
    for text in (p1.text, p2.text):
        assert "Item 1: Data Engineer 5 at Initech" in text and "Item 5: Data Engineer 1 at Initech" in text
        assert "Airflow" in text and "Terraform" in text and "Required qualifications" in text
        for item in panel.run_items(conn, run_id):
            for side in "ab":
                draft = item[f"draft_{side}"]
                assert draft["created_at"][:10] not in text  # no draft dates
                assert draft["rules_version"] not in text and draft["id"] not in text
        lowered = text.lower()
        for word in ("version", "draft", "newer", "older", "latest", "previous", " v1", " v2"):
            assert word not in lowered, word
    for item in panel.run_items(conn, run_id):
        a, b = item["draft_a"]["resume_text"].strip(), item["draft_b"]["resume_text"].strip()
        first, second = (a, b) if item["shown_first"] == "a" else (b, a)
        assert p1.text.index(first) < p1.text.index(second)
        assert p2.text.index(second) < p2.text.index(first)


def test_import_maps_each_models_order_back_to_the_drafts(conn, run_id):
    items = panel.run_items(conn, run_id)
    # Sonnet sees packet 1 and always picks First; Opus sees packet 2 and always picks First too.
    out = panel.import_grades(conn, run_id, "sonnet", model_reply(["first"] * 5, first=version_grade("A")))
    assert out.ok and out.graded == 5
    assert panel.import_grades(conn, run_id, "opus", model_reply(["first"] * 5, first=version_grade("D"))).ok
    g = panel.grades(conn, run_id)
    for item in items:
        no, shown = item["item_no"], item["shown_first"]
        other = "b" if shown == "a" else "a"
        assert g["sonnet"][no]["winner"] == "first"
        assert g["opus"][no]["winner"] == "second"  # stored in packet 1's order
        assert g["sonnet"][no]["detail"][shown]["overall"] == "A"
        assert g["opus"][no]["detail"][other]["overall"] == "D"  # packet 2 showed the other side first
        assert g["sonnet"][no]["detail"]["why"] == "Clearer fit."


def test_reimport_replaces_and_ties_stay_ties(conn, run_id):
    assert panel.import_grades(conn, run_id, "opus", model_reply(["first"] * 5)).ok
    assert panel.import_grades(conn, run_id, "opus", model_reply(["tie"] * 5)).ok
    rows = conn.execute("SELECT winner FROM panel_grades WHERE grader = 'opus'").fetchall()
    assert [r[0] for r in rows] == ["tie"] * 5


def test_import_errors(conn, run_id):
    out = panel.import_grades(conn, run_id, "sonnet", model_reply(["first"] * 5, count=4))
    assert not out.ok and "grades 4 items, but this panel has 5" in out.errors[0]
    assert not panel.import_grades(conn, run_id, "sonnet", "no json here").ok
    bad = panel.import_grades(conn, run_id, "sonnet", "```json\n{\"panel\": 1, \"items\": [\n```")
    assert not bad.ok and "isn't valid JSON" in bad.errors[0]
    wrong = panel.import_grades(conn, run_id, "sonnet", model_reply(["first"] * 5, first=version_grade("E")))
    assert not wrong.ok and "doesn't match the panel format" in wrong.errors[0] and '"E"' in wrong.errors[0]
    data = json.loads(model_reply(["first"] * 5).split("```json\n")[1].split("```")[0])
    data["items"][4]["item"] = 9
    numbers = panel.import_grades(conn, run_id, "sonnet", json.dumps(data))
    assert not numbers.ok and "items [1, 2, 3, 4, 9]" in numbers.errors[0]
    assert not panel.import_grades(conn, run_id, "you", model_reply(["first"] * 5)).ok
    assert not panel.import_grades(conn, "nope", "sonnet", model_reply(["first"] * 5)).ok
    assert conn.execute("SELECT COUNT(*) FROM panel_grades").fetchone()[0] == 0


def test_picks_and_completion(conn, run_id):
    for no in range(1, 5):
        panel.record_pick(conn, run_id, no, "second")
    assert panel.get_run(conn, run_id)["status"] == "open"
    panel.record_pick(conn, run_id, 5, "tie")
    assert panel.get_run(conn, run_id)["status"] == "open"  # no model yet
    panel.import_grades(conn, run_id, "sonnet", model_reply(["first"] * 5))
    assert panel.get_run(conn, run_id)["status"] == "complete"
    panel.record_pick(conn, run_id, 5, "first")  # changing a pick replaces it
    assert panel.grades(conn, run_id)["you"][5]["winner"] == "first"
    with pytest.raises(ValueError):
        panel.record_pick(conn, run_id, 1, "A")
    with pytest.raises(KeyError):
        panel.record_pick(conn, run_id, 6, "first")


# ── Task 3: report and "panel due" ──

def picks_in_packet1(conn, run_id, newer_wins: list[str]) -> list[str]:
    """Packet-1 winners (first/second/tie) that make the newer draft (b) win, lose or tie per item."""
    out = []
    for item, want in zip(panel.run_items(conn, run_id), newer_wins, strict=True):
        if want == "tie":
            out.append("tie")
        else:
            newer_first = item["shown_first"] == "b"
            out.append("first" if (want == "newer") == newer_first else "second")
    return out


def packet2(winners: list[str]) -> list[str]:
    return [{"first": "second", "second": "first"}.get(w, w) for w in winners]


def test_report_math(conn, run_id):
    you = picks_in_packet1(conn, run_id, ["newer", "newer", "newer", "older", "tie"])
    sonnet = picks_in_packet1(conn, run_id, ["newer", "newer", "older", "older", "newer"])
    for no, w in enumerate(you, 1):
        panel.record_pick(conn, run_id, no, w)
    # Sonnet grades packet 1: both sides get the same grade except the flags, so per-side stats come from detail.
    good = version_grade("A", 5, quals=[
        {"text": "Airflow", "kind": "required", "status": "met", "evidence": "Airflow"},
        {"text": "dbt", "kind": "preferred", "status": "met", "evidence": "dbt"}])
    weak = version_grade("C", 2, flags=["vague"], quals=[
        {"text": "Airflow", "kind": "required", "status": "partial", "evidence": "pipelines"},
        {"text": "dbt", "kind": "preferred", "status": "not_met", "evidence": ""}])
    items = panel.run_items(conn, run_id)
    data = {"panel": 1, "items": []}
    for item, w in zip(items, sonnet, strict=True):
        newer_first = item["shown_first"] == "b"
        data["items"].append({"item": item["item_no"], "first": good if newer_first else weak,
                              "second": weak if newer_first else good, "winner": w, "why": "x"})
    assert panel.import_grades(conn, run_id, "sonnet", json.dumps(data)).ok
    # Opus (packet 2) agrees with you on every item.
    assert panel.import_grades(conn, run_id, "opus", model_reply(packet2(you))).ok

    r = panel.panel_report(conn, run_id)
    assert r["status"] == "complete" and r["items_total"] == 5
    assert r["graders"]["you"]["newer_win_rate"] == 0.7  # (3 + 0.5) / 5
    assert r["graders"]["sonnet"]["newer_win_rate"] == 0.6
    assert r["graders"]["opus"]["newer_win_rate"] == 0.7
    s = r["graders"]["sonnet"]
    assert s["b"]["mean_overall"] == 4.0 and s["a"]["mean_overall"] == 2.0
    assert s["b"]["mean_clarity"] == 5.0 and s["a"]["mean_clarity"] == 2.0
    assert s["b"]["met_rate"] == {"required": 1.0, "preferred": 1.0}
    assert s["a"]["met_rate"] == {"required": 0.0, "preferred": 0.0}  # partial isn't met
    assert (s["a"]["red_flags"], s["b"]["red_flags"]) == (5, 0)
    assert {"item": 1, "text": "Airflow", "kind": "required", "a": "partial", "b": "met"} in s["qualifications"]
    assert r["agreement"] == {"sonnet_vs_opus": 0.6, "sonnet_vs_you": 0.6, "opus_vs_you": 1.0}
    assert r["verdict"] == "newer is better"
    first = r["items"][0]
    assert first["winners"] == {"sonnet": "newer", "opus": "newer", "you": "newer"}
    assert (first["older_version"], first["newer_version"]) == (1, 2)


def test_verdict_needs_your_picks_and_no_extra_red_flags(conn, run_id):
    assert panel.panel_report(conn, run_id)["verdict"] is None
    for no, w in enumerate(picks_in_packet1(conn, run_id, ["newer"] * 5), 1):
        panel.record_pick(conn, run_id, no, w)
    assert panel.panel_report(conn, run_id)["verdict"] == "newer is better"
    # Opus flags the newer version (b) more often: no longer a clear gain.
    data = {"panel": 1, "items": []}
    for item in panel.run_items(conn, run_id):
        newer_first_in_p2 = item["shown_first"] == "a"
        flagged, clean = version_grade(flags=["stuffed"]), version_grade()
        data["items"].append({"item": item["item_no"], "first": flagged if newer_first_in_p2 else clean,
                              "second": clean if newer_first_in_p2 else flagged, "winner": "tie", "why": "x"})
    assert panel.import_grades(conn, run_id, "opus", json.dumps(data)).ok
    report = panel.panel_report(conn, run_id)
    assert report["graders"]["opus"]["b"]["red_flags"] == 5 and report["verdict"] == "no clear gain"


def test_verdict_worse(conn, run_id):
    for no, w in enumerate(picks_in_packet1(conn, run_id, ["older"] * 4 + ["newer"]), 1):
        panel.record_pick(conn, run_id, no, w)
    assert panel.panel_report(conn, run_id)["verdict"] == "worse"


def test_heard_back_by_rules_version(conn):
    for no in range(1, 13):
        add_job(conn, no)
        rules = "r1" if no <= 10 else "r2"
        status = "heard_back" if no in (1, 2, 11) else "submitted"
        conn.execute("UPDATE jobs SET submitted_rules_version = ?, user_status = ?, submitted_at = ? WHERE url = ?",
                     (rules, status, f"2026-01-{no:02d}", f"https://example.com/jobs/{no:02d}"))
    conn.commit()
    rows = panel.heard_back_by_rules(conn)
    assert rows[0] == {"rules_version": "r1", "submitted": 10, "heard_back": 2, "rate": 0.2, "note": None}
    assert rows[1] == {"rules_version": "r2", "submitted": 2, "heard_back": 1, "rate": None,
                       "note": "too few to compare"}


def _complete(conn, run_id):
    for no in range(1, 6):
        panel.record_pick(conn, run_id, no, "tie")
    panel.import_grades(conn, run_id, "sonnet", model_reply(["tie"] * 5))
    assert panel.get_run(conn, run_id)["status"] == "complete"


def test_panel_due_reasons(conn):
    seed_jobs(conn, 4)
    assert panel.panel_due(conn, rules_version="r2") is None  # 4 comparable jobs
    seed_jobs_from(conn, 5)
    assert panel.panel_due(conn, rules_version="r2") == "no panel has been run yet"
    run_id = panel.create_run(conn, rng=random.Random(1))
    assert panel.panel_due(conn, rules_version="r2") == "no panel has been run yet"  # not complete yet
    _complete(conn, run_id)
    assert panel.panel_due(conn, rules_version="r2") is None
    created = datetime.fromisoformat(panel.get_run(conn, run_id)["created_at"])
    assert panel.panel_due(conn, rules_version="r2", now=created + timedelta(days=31)) == "31 days since the last panel"
    # New rules, but no drafts under them yet: not due.
    assert panel.panel_due(conn, rules_version="r3") is None
    for no in range(1, 6):
        add_draft(conn, f"https://example.com/jobs/{no:02d}", 3, rules="r3", at=f"2026-09-01T00:00:0{no}+00:00")
    assert panel.panel_due(conn, rules_version="r3") == "the resume rules changed since the last panel"


def seed_jobs_from(conn, no: int):
    job = add_job(conn, no)
    add_draft(conn, job["url"], 1, rules="r1")
    add_draft(conn, job["url"], 2, rules="r2")
