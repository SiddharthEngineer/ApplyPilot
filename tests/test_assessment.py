"""Assessment layer: input hash, rubric request, red flags, recompute_fit and the 0-100 migration (synthetic data)."""

import json
import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from strelitzia import users
from strelitzia.database import get_jobs_by_stage, init_db
from strelitzia.profile import UserProfile, save_profile
from strelitzia.scoring import scorer
from strelitzia.scoring.assessment import (
    ASSESS_VERSION,
    ASSESS_PROMPT,
    RED_FLAGS,
    assess_messages,
    assessment_components,
    assessment_input_hash,
    assessment_key,
    candidate_block,
    parse_assessment,
    current_assessment_key,
    load_resume_text,
    recompute_fit,
)
from strelitzia.scoring.preferences import RED_FLAG_HELP, Preferences, save_preferences
from fit_helpers import POSTING, add_extracted_job, default_fit, reply

RESUME = "Synthetic resume: Python, SQL, five years building pipelines."


def test_hash_is_stable_across_dict_orderings():
    a = Preferences.from_dict({"skills_want": ["Python", "SQL"], "industries_avoid": ["Gambling"],
                               "target_roles": ["Data Engineer"]})
    b = Preferences.from_dict({"target_roles": ["Data Engineer"], "industries_avoid": ["Gambling"],
                               "skills_want": ["SQL", "Python"]})
    assert assessment_input_hash(a, RESUME) == assessment_input_hash(b, RESUME)
    assert candidate_block(a, RESUME) == candidate_block(b, RESUME)


def test_preference_only_changes_leave_the_hash_alone():
    base = Preferences(skills_want=["Python"])
    changed = Preferences.from_dict({**base.to_dict(),
                                     "salary": {"importance": "dealbreaker", "value": {"min": 150000}},
                                     "work_mode": {"importance": "important", "value": ["remote"]},
                                     "red_flags_counted": []})
    assert assessment_input_hash(changed, RESUME) == assessment_input_hash(base, RESUME)
    assert assessment_input_hash(Preferences(skills_want=["Python", "Go"]), RESUME) != \
        assessment_input_hash(base, RESUME)
    assert assessment_input_hash(base, RESUME + " Go.") != assessment_input_hash(base, RESUME)


def test_key_carries_the_rubric_version():
    assert assessment_key(Preferences(), RESUME).startswith(f"{ASSESS_VERSION}:")


def test_request_layout():
    job = {"url": "u", "title": "Data Engineer II", "site": "acme", "details_json": json.dumps(POSTING),
           "full_description": "x" * 9000, "strategy": "ats_greenhouse", "detail_error": "noise"}
    rubric, candidate, user = assess_messages(Preferences(skills_want=["Python"]), RESUME, job)
    assert rubric["role"] == candidate["role"] == "system"
    assert "mostly_sales" in rubric["content"] and RESUME not in rubric["content"]
    assert RESUME in candidate["content"] and '"skills_want": [\n  "Python"\n ]' in candidate["content"]
    posting = json.loads(user["content"].split("\n", 1)[1].split("\n\nDESCRIPTION:\n")[0])
    assert posting["skills_required"] == ["SQL", "Python"] and posting["listing_title"] == "Data Engineer II"
    assert "strategy" not in posting and "noise" not in user["content"]
    assert user["content"].endswith("x" * 8000) and "x" * 8001 not in user["content"]


def _comps(prefs, text):
    return {c.key: c for c in assessment_components(prefs, parse_assessment(text))}


def test_red_flags_component():
    comps = _comps(Preferences(), reply(flags=("mostly_sales", "commission_heavy")))
    assert (comps["red_flags"].score, comps["red_flags"].importance) == (0.0, "important")
    assert comps["red_flags"].note.startswith("mostly sales: ")
    assert not comps["red_flags"].dealbreaker_hit
    assert comps["skills_match"].score == 0.75
    assert _comps(Preferences(), reply())["red_flags"].score == 1.0
    assert _comps(Preferences(), reply(flags=RED_FLAGS[:4]))["red_flags"].score == 0.0


def test_only_counted_red_flags_count():
    assert "relocation_required" not in Preferences().red_flags_counted  # off by default
    comps = _comps(Preferences(), reply(flags=("relocation_required",)))
    assert comps["red_flags"].score == 1.0
    assert comps["red_flags"].note == "relocation required (not counted): seen in posting"  # still shown
    picky = Preferences.from_dict({"red_flags_counted": ["relocation_required"]})
    assert _comps(picky, reply(flags=("relocation_required",)))["red_flags"].score == 0.0
    assert _comps(picky, reply(flags=("vague_posting",)))["red_flags"].score == 1.0
    relaxed = Preferences.from_dict({"red_flags_counted": []})
    comps = _comps(relaxed, reply(flags=("mostly_sales", "vague_posting")))
    assert (comps["red_flags"].score, comps["red_flags"].importance) == (1.0, "dont_care")  # counts for nothing


def test_red_flag_help_is_the_rubrics_wording():
    for code, text in RED_FLAG_HELP.items():
        assert f"  {code}: {text}\n" in ASSESS_PROMPT


def test_red_flags_counted_is_lenient():
    prefs = Preferences.from_dict({"red_flags_counted": ["vague_posting", "other", "aliens", "mostly_sales"]})
    assert prefs.red_flags_counted == ["mostly_sales", "vague_posting"]  # "other" can't be picked
    assert Preferences.from_dict({"red_flags_counted": "all"}).red_flags_counted == Preferences().red_flags_counted
    assert "red_flags_counted" not in Preferences().criteria()


def test_red_flag_picks_leave_the_hash_alone():
    assert assessment_input_hash(Preferences(red_flags_counted=[]), RESUME) == \
        assessment_input_hash(Preferences(), RESUME)


def test_fixed_weights():
    comps = _comps(Preferences(), reply())
    assert {k: comps[k].importance for k in ("skills_match", "experience_match", "domain_interest")} == \
        {"skills_match": "very_important", "experience_match": "important", "domain_interest": "dont_care"}
    for industries in ({"industries_favor": ["Fintech"]}, {"industries_avoid": ["Gambling"]}):
        assert _comps(Preferences.from_dict(industries), reply())["domain_interest"].importance == "important"
    # The old importance rows are gone; saved ones are dropped.
    old = Preferences.from_dict({"skills_match": {"importance": "nice"}, "red_flags": {"importance": "dealbreaker"},
                                 "company_blocklist": {"importance": "dealbreaker", "value": ["Initech"]}})
    assert old == Preferences()
    assert _comps(old, reply(skills=1))["skills_match"].importance == "very_important"


def test_unknown_red_flag_code_becomes_other():
    data = json.loads(reply(flags=("mostly_sales",)))
    data["red_flags"][0]["code"] = "aliens"
    assert parse_assessment(json.dumps(data))["red_flags"][0]["code"] == "other"


# ── recompute_fit ─────────────────────────────────────────────────────────

@pytest.fixture
def conn(tmp_path):
    return init_db(tmp_path / "fit.db")


def _assess(conn, url, text, key="1:h"):
    conn.execute("UPDATE jobs SET fit_assessment = ?, assessment_hash = ? WHERE url = ?",
                 (json.dumps(parse_assessment(text)), key, url))
    conn.commit()


def _fit(conn, url):
    row = conn.execute("SELECT fit_score, fit_breakdown, fit_flags FROM jobs WHERE url = ?", (url,)).fetchone()
    return row[0], json.loads(row[1]) if row[1] else None, json.loads(row[2]) if row[2] else []


def test_recompute_combines_both_layers(conn):
    add_extracted_job(conn, "https://e.com/1")
    _assess(conn, "https://e.com/1", reply(skills=4))
    save_preferences(conn, Preferences.from_dict({
        "salary": {"importance": "important", "value": {"min": 100000}},
        "work_mode": {"importance": "important", "value": ["remote"]},
    }))
    assert recompute_fit(conn) == 1
    score, breakdown, flags = _fit(conn, "https://e.com/1")
    # salary 1.0 x2, work_mode 0.0 x2, education and experience unknown 0.5 x4 each (no profile), skills 0.75 x4,
    # experience match 0.5 x2, no red flags 1.0 x2 -> 12/20
    assert score == round(100 * 12 / 20) and flags == []
    keys = [c["key"] for c in breakdown]
    assert keys == ["salary", "locations", "work_mode", "role_category", "education", "experience", "skills_match",
                    "experience_match", "red_flags"]  # the non-dont_care ones


def test_salary_dealbreaker_flags_without_any_llm_call(conn):
    add_extracted_job(conn, "https://e.com/1")
    _assess(conn, "https://e.com/1", reply(skills=5))
    save_preferences(conn, Preferences.from_dict({"salary": {"importance": "dealbreaker", "value": {"min": 200000}}}))
    recompute_fit(conn)
    score, breakdown, flags = _fit(conn, "https://e.com/1")
    assert flags == ["salary"]
    salary = next(c for c in breakdown if c["key"] == "salary")
    assert "salary below minimum" in salary["note"]
    # salary 0 x4, education and experience 0.5 x4 each, skills 1.0 x4, experience match 0.5 x2, no red flags 1.0 x2
    assert score == round(100 * 11 / 20)
    assert get_jobs_by_stage(conn, "pending_tailor", min_score=0) == []  # flagged jobs aren't tailored


def test_unassessed_jobs_keep_their_score_but_get_flags(conn):
    add_extracted_job(conn, "https://e.com/old", fit_score=80)
    save_preferences(conn, Preferences.from_dict({"salary": {"importance": "dealbreaker", "value": {"min": 200000}}}))
    recompute_fit(conn)
    score, _, flags = _fit(conn, "https://e.com/old")
    assert (score, flags) == (80, ["salary"])


def test_recompute_only_the_given_urls(conn):
    add_extracted_job(conn, "https://e.com/1")
    add_extracted_job(conn, "https://e.com/2")
    _assess(conn, "https://e.com/1", reply(skills=5))
    _assess(conn, "https://e.com/2", reply(skills=5))
    assert recompute_fit(conn, urls=["https://e.com/2"]) == 1
    assert _fit(conn, "https://e.com/1")[0] is None and _fit(conn, "https://e.com/2")[0] == default_fit(5)
    assert recompute_fit(conn, urls=[]) == 0


def test_migration_scales_old_scores(tmp_path):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE jobs (url TEXT PRIMARY KEY, title TEXT, fit_score INTEGER, score_reasoning TEXT)")
    old.executemany("INSERT INTO jobs VALUES (?, 't', ?, ?)", [
        ("https://e.com/a", 7, "sql\nGood."), ("https://e.com/b", None, None),
        ("https://e.com/c", 1, "prefilter: title not relevant"),
    ])
    old.commit()
    old.close()
    conn = init_db(path)
    rows = {r[0]: tuple(r[1:]) for r in
            conn.execute("SELECT url, fit_score, fit_flags, assessment_hash FROM jobs").fetchall()}
    assert rows == {"https://e.com/a": (70, None, "0:legacy"), "https://e.com/b": (None, None, None),
                    "https://e.com/c": (0, '["prefilter"]', "0:legacy")}
    init_db(path)  # runs once: the columns exist now
    assert conn.execute("SELECT fit_score FROM jobs WHERE url = 'https://e.com/a'").fetchone()[0] == 70


# ── run_scoring: what gets assessed ───────────────────────────────────────

def _run(conn, tmp_path, replies, **kwargs):
    save_profile(conn, None, UserProfile(resume_text=RESUME))  # the owner's
    client = MagicMock()
    client.chat.side_effect = replies
    with patch.object(scorer, "get_connection", return_value=conn), \
            patch.object(scorer, "get_client", return_value=client), \
            patch.object(scorer, "resume_open_batches", return_value={"still_open": 0}):
        stats = scorer.run_scoring(prefilter=False, mode="sync", **kwargs)
    return stats, client


def test_the_resume_comes_from_each_users_profile(conn):
    other = users.invite(conn, "other@example.com").id
    save_profile(conn, None, UserProfile(resume_text=RESUME))
    assert load_resume_text(conn) == RESUME and load_resume_text(conn, other) == ""
    assert current_assessment_key(conn) == assessment_key(Preferences(), RESUME)
    assert current_assessment_key(conn, user_id=other) == assessment_key(Preferences(), "")


def test_no_resume_stops_before_any_request(conn, tmp_path):
    add_extracted_job(conn, "https://e.com/1")
    other = users.invite(conn, "other@example.com").id
    stats, client = _run(conn, tmp_path, lambda *a, **k: reply(skills=4), user_id=other)
    assert stats["stopped"] == "no_resume" and client.chat.call_count == 0


def test_stale_jobs_need_a_confirmed_reassess(conn, tmp_path):
    for i in range(3):
        add_extracted_job(conn, f"https://e.com/{i}")
    add_extracted_job(conn, "https://e.com/unextracted", details_json=None)
    stats, client = _run(conn, tmp_path, lambda *a, **k: reply(skills=4))
    assert stats["scored"] == 3 and client.chat.call_count == 3  # never the unextracted job
    assert get_jobs_by_stage(conn, "pending_score") == []

    save_preferences(conn, Preferences(skills_want=["Rust"]))  # every assessment is stale now
    stats, client = _run(conn, tmp_path, lambda *a, **k: reply(skills=2))
    assert client.chat.call_count == 0  # a pipeline run doesn't pay for re-assessment on its own
    assert {_fit(conn, f"https://e.com/{i}")[0] for i in range(3)} == {default_fit(4)}

    stats, client = _run(conn, tmp_path, lambda *a, **k: reply(skills=2), reassess=True)
    assert client.chat.call_count == 3 and {_fit(conn, f"https://e.com/{i}")[0] for i in range(3)} == {default_fit(2)}
    assert "Rust" in client.chat.call_args.args[0][1]["content"]


def test_rescore_one_job_by_url(conn, tmp_path):
    add_extracted_job(conn, "https://e.com/1", title="Registered Nurse")
    add_extracted_job(conn, "https://e.com/2")
    _run(conn, tmp_path, lambda *a, **k: reply(skills=3))
    stats, client = _run(conn, tmp_path, [reply(skills=5)], urls=["https://e.com/1"])
    assert client.chat.call_count == 1 and stats["scored"] == 1
    assert _fit(conn, "https://e.com/1")[0] == default_fit(5) and _fit(conn, "https://e.com/2")[0] == default_fit(3)


def test_breakdown_after_a_run(conn, tmp_path):
    add_extracted_job(conn, "https://e.com/1")
    save_preferences(conn, Preferences.from_dict({"industries_favor": ["Logistics"]}))
    _run(conn, tmp_path, [reply(skills=4, domain=5, flags=("vague_posting",))])
    score, breakdown, flags = _fit(conn, "https://e.com/1")
    comps = {c["key"]: c for c in breakdown}
    assert {"skills_match", "domain_interest", "red_flags"} <= set(comps)
    assert comps["skills_match"]["note"] == "Has SQL and Python."
    assert 0 <= score <= 100 and flags == []
    row = conn.execute("SELECT fit_assessment, assessment_hash, assessed_at FROM jobs").fetchone()
    assert json.loads(row[0])["red_flags"][0]["code"] == "vague_posting"
    assert row[1] == assessment_key(Preferences.from_dict({"industries_favor": ["Logistics"]}), RESUME) and row[2]


def test_old_scored_jobs_wait_for_a_confirmed_reassess(conn, tmp_path):
    add_extracted_job(conn, "https://e.com/legacy", fit_score=80, assessment_hash="0:legacy")
    add_extracted_job(conn, "https://e.com/new")
    _, client = _run(conn, tmp_path, lambda *a, **k: reply(skills=4))
    assert client.chat.call_count == 1  # only the new job
    assert _fit(conn, "https://e.com/legacy")[0] == 80
    _, client = _run(conn, tmp_path, lambda *a, **k: reply(skills=2), reassess=True)
    assert client.chat.call_count == 1 and _fit(conn, "https://e.com/legacy")[0] == default_fit(2)


def test_preferences_saved_during_a_run_win(conn, tmp_path):
    add_extracted_job(conn, "https://e.com/1")
    dealbreaker = Preferences.from_dict({"salary": {"importance": "dealbreaker", "value": {"min": 500000}}})

    def chat(*_a, **_k):  # the user saves new preferences while the run is going
        save_preferences(conn, dealbreaker)
        return reply(skills=4)

    _run(conn, tmp_path, chat)
    assert _fit(conn, "https://e.com/1")[2] == ["salary"]


def test_scoring_selects_and_ranks_only_the_users_jobs(conn):
    from strelitzia import users

    owner, alice = users.owner(conn).id, users.invite(conn, "alice@example.com").id
    add_extracted_job(conn, "https://e.com/1")
    add_extracted_job(conn, "https://e.com/1", user_id=alice)  # the same posting, tracked by both
    add_extracted_job(conn, "https://e.com/2", user_id=alice)
    for user in (owner, alice):
        conn.execute("UPDATE jobs SET fit_assessment = ?, assessment_hash = '1:h' WHERE user_id = ?",
                     (json.dumps(parse_assessment(reply(skills=5))), user))
    conn.commit()
    save_preferences(conn, Preferences.from_dict({"salary": {"importance": "dealbreaker", "value": {"min": 200000}}}),
                     user_id=alice)
    assert recompute_fit(conn, alice) == 2
    flags = dict(conn.execute("SELECT user_id, fit_flags FROM jobs WHERE url = 'https://e.com/1'").fetchall())
    assert flags == {owner: None, alice: '["salary"]'}  # alice's preferences, on alice's rows only

    assert {j["url"] for j in scorer._select_jobs(conn, alice, "1:new", 0, False, True, None)} == \
        {"https://e.com/1", "https://e.com/2"}
    assert [j["user_id"] for j in scorer._select_jobs(conn, owner, "1:new", 0, False, True, None)] == [owner]
    assert [j["user_id"] for j in scorer._select_jobs(conn, owner, "", 0, False, False, ["https://e.com/1"])] == \
        [owner]
    with patch.object(scorer, "estimate_cost", return_value=0.01):
        assert scorer.reassess_preview(conn, owner, Preferences(skills_want=["Rust"]))["jobs_to_reassess"] == 1


def test_active_scope_includes_current_assessments_and_excludes_rejected(conn, tmp_path):
    for i in range(3):
        add_extracted_job(conn, f"https://e.com/{i}")
    add_extracted_job(conn, "https://e.com/rejected", user_status="rejected")
    add_extracted_job(conn, "https://e.com/unextracted", details_json=None)
    add_extracted_job(conn, "https://e.com/unextracted-rejected", details_json=None, user_status="rejected")
    _, client = _run(conn, tmp_path, lambda *a, **k: reply(skills=4))
    assert client.chat.call_count == 4  # every assessment is current now

    with patch.object(scorer, "estimate_cost", return_value=0.01):
        owner = conn.execute("SELECT user_id FROM jobs LIMIT 1").fetchone()[0]
        active = scorer.rescore_preview(conn, owner, "active")
        stale = scorer.rescore_preview(conn, owner, "stale")
    assert active == {"jobs": 3, "skipped_unextracted": 1, "estimated_usd": 0.01}
    assert stale == {"jobs": 0, "skipped_unextracted": 1, "estimated_usd": 0.0}

    _, client = _run(conn, tmp_path, lambda *a, **k: reply(skills=2), reassess=True)
    assert client.chat.call_count == 0  # nothing is stale
    _, client = _run(conn, tmp_path, lambda *a, **k: reply(skills=2), scope="active")
    assert client.chat.call_count == 3
    assert {_fit(conn, f"https://e.com/{i}")[0] for i in range(3)} == {default_fit(2)}
    assert _fit(conn, "https://e.com/rejected")[0] == default_fit(4)  # left alone

    conn.execute("UPDATE jobs SET score_attempts = ? WHERE url = 'https://e.com/0'", (scorer.MAX_SCORE_ATTEMPTS,))
    conn.commit()
    assert "https://e.com/0" not in {j["url"] for j in scorer._select_jobs(conn, owner, "", 0, False, False, None,
                                                                           "active")}  # keeps failing: left out
