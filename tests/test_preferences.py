"""Preference layer of the fit score: criteria compared to extracted posting fields in code (synthetic data only)."""

from types import SimpleNamespace

import pytest

from strelitzia.database import init_db
from strelitzia.enrichment.posting_model import JobPosting, Location
from strelitzia.scoring.preferences import (
    Component,
    Preferences,
    combine,
    load_preferences,
    save_preferences,
    score_preferences,
    score_qualifications,
    travel_percent,
)


def _prefs(**criteria) -> Preferences:
    """Everything Don't care except the given {key: (importance, value)}."""
    data = {k: {"importance": "dont_care"} for k in Preferences().criteria()}
    data.update({k: {"importance": imp, "value": val} for k, (imp, val) in criteria.items()})
    return Preferences.from_dict(data)


def _component(prefs, posting, key):
    return next(c for c in score_preferences(prefs, posting) if c.key == key)


SALARY_FLOOR = {"min": 100000, "currency": "USD", "period": "year"}


@pytest.mark.parametrize(("importance", "posting", "score", "hit"), [
    ("important", JobPosting(salary_min=80000, salary_max=90000, salary_period="year"), 0.0, False),
    ("dealbreaker", JobPosting(salary_min=80000, salary_max=90000, salary_period="year"), 0.0, True),
    ("dealbreaker", JobPosting(salary_min=90000, salary_max=120000, salary_period="year"), 1.0, False),
    ("dealbreaker", JobPosting(salary_min=50, salary_period="hour"), 1.0, False),  # 50 x 2080 = 104,000
    ("dealbreaker", JobPosting(salary_min=40, salary_period="hour"), 0.0, True),  # 83,200
    ("dealbreaker", JobPosting(salary_min=9000, salary_period="month"), 1.0, False),
    ("dealbreaker", JobPosting(), 0.5, False),  # not listed
    ("dealbreaker", JobPosting(salary_min=4500), 0.5, False),  # no period: monthly or yearly? unknown
    ("dealbreaker", JobPosting(salary_min=85000), 0.0, True),  # no period, clearly yearly
    ("dealbreaker", JobPosting(salary_min=55), 1.0, False),  # no period, clearly hourly (114,400)
    ("dealbreaker", JobPosting(salary_min=50000, salary_currency="EUR", salary_period="year"), 0.5, False),
])
def test_salary(importance, posting, score, hit):
    c = _component(_prefs(salary=(importance, SALARY_FLOOR)), posting, "salary")
    assert (c.score, c.dealbreaker_hit) == (score, hit)
    if hit:
        assert "salary below minimum" in c.note


def test_hourly_salary_is_converted_to_yearly():
    c = _component(_prefs(salary=("important", SALARY_FLOOR)), JobPosting(salary_max=48.5, salary_period="hour"),
                   "salary")
    assert c.score == 1.0 and "$100,880/year" in c.note


@pytest.mark.parametrize(("work_mode", "score"), [("remote", 1.0), ("hybrid", 1.0), ("onsite", 0.0),
                                                  ("unknown", 0.5)])
def test_work_mode_unknown_scores_half_and_never_breaks(work_mode, score):
    c = _component(_prefs(work_mode=("dealbreaker", ["remote", "hybrid"])), JobPosting(work_mode=work_mode),
                   "work_mode")
    assert c.score == score
    assert c.dealbreaker_hit is (score == 0.0)


@pytest.mark.parametrize(("places", "posting", "score"), [
    (["Chicago, IL"], JobPosting(work_mode="remote"), 1.0),
    (["Chicago, IL"], JobPosting(work_mode="onsite", locations=[Location("Chicago", "IL", "US")]), 1.0),
    (["Chicago, IL"], JobPosting(work_mode="hybrid", locations=[Location("Austin", "TX", "US")]), 0.0),
    (["US"], JobPosting(work_mode="onsite", locations=[Location("Austin", "TX", "United States")]), 1.0),
    (["Chicago, IL"], JobPosting(work_mode="onsite"), 0.5),
    ([], JobPosting(work_mode="onsite", locations=[Location("Austin")]), None),
])
def test_locations(places, posting, score):
    prefs = _prefs(locations=("important", {"places": places, "remote_ok": True}))
    assert _component(prefs, posting, "locations").score == score


def test_remote_not_ok_needs_a_place():
    prefs = _prefs(locations=("important", {"places": ["Denver"], "remote_ok": False}))
    assert _component(prefs, JobPosting(work_mode="remote", locations=[Location("Boston")]), "locations").score == 0


@pytest.mark.parametrize(("key", "value", "posting", "score"), [
    ("visa", True, JobPosting(visa_sponsorship="no"), 0.0),
    ("visa", True, JobPosting(visa_sponsorship="unknown"), 0.5),
    ("visa", False, JobPosting(visa_sponsorship="no"), None),
    ("clearance", False, JobPosting(security_clearance="required"), 0.0),
    ("travel", 10, JobPosting(travel="Up to 25% travel"), 0.0),
    ("travel", 30, JobPosting(travel="Up to 25% travel"), 1.0),
    ("travel", 10, JobPosting(travel="Occasional"), 0.5),
    ("seniority", ["senior"], JobPosting(seniority="unknown"), 0.5),
])
def test_other_criteria(key, value, posting, score):
    assert _component(_prefs(**{key: ("important", value)}), posting, key).score == score


@pytest.mark.parametrize(("key", "have", "posting", "score", "hit"), [
    ("education", "high_school", JobPosting(education_level="bachelors"), 0.0, True),
    ("education", "associate", JobPosting(education_level="bachelors"), 0.4, False),
    ("education", "masters", JobPosting(education_level="phd"), 0.4, False),
    ("education", "masters", JobPosting(education_level="bachelors"), 1.0, False),
    ("education", "bachelors", JobPosting(education_level="bachelors"), 1.0, False),
    ("education", "none", JobPosting(education_level="none"), 1.0, False),
    ("education", "bachelors", JobPosting(education_level="unknown"), 0.5, False),
    ("education", None, JobPosting(education_level="phd"), 0.5, False),
    ("experience", 3, JobPosting(years_experience_min=4), 0.4, False),
    ("experience", 6, JobPosting(years_experience_min=8), 0.4, False),
    ("experience", 2, JobPosting(years_experience_min=5), 0.0, True),
    ("experience", 5, JobPosting(years_experience_min=5), 1.0, False),
    ("experience", 0, JobPosting(years_experience_min=1), 0.4, False),
    ("experience", 3, JobPosting(), 0.5, False),
    ("experience", None, JobPosting(years_experience_min=10), 0.5, False),
])
def test_qualifications(key, have, posting, score, hit):
    profile = SimpleNamespace(education_level=have if key == "education" else None,
                              years_experience=have if key == "experience" else None)
    c = next(c for c in score_qualifications(profile, posting) if c.key == key)
    assert (c.score, c.dealbreaker_hit, c.importance) == (score, hit, "very_important")


def test_qualifications_without_a_profile_are_unknown():
    comps = score_qualifications(None, JobPosting(education_level="phd", years_experience_min=10))
    assert [(c.key, c.score, c.dealbreaker_hit) for c in comps] == [("education", 0.5, False),
                                                                   ("experience", 0.5, False)]


def test_travel_percent():
    assert travel_percent("10-20% domestic") == 20
    assert travel_percent("None") == 0
    assert travel_percent(None) is None


def test_dont_care_is_left_out_of_the_mean():
    comps = [Component("salary", "Salary", 0.0, "dont_care"), Component("work_mode", "Work mode", 1.0, "important"),
             Component("seniority", "Seniority", 0.5, "nice")]
    assert combine(comps) == (round(100 * (2 * 1.0 + 1 * 0.5) / 3), [])


def test_weights_and_dealbreakers():
    comps = [Component("salary", "Salary", 0.0, "dealbreaker", dealbreaker_hit=True),
             Component("skills_match", "Skills", 1.0, "very_important")]
    assert combine(comps) == (50, ["salary"])


def test_unset_criteria_are_left_out():
    comps = [Component("salary", "Salary", None, "important"), Component("skills_match", "Skills", 0.75, "nice")]
    assert combine(comps) == (75, [])


def test_all_dont_care_leaves_only_the_llm_layer():
    prefs = _prefs()
    posting = JobPosting(salary_min=1, salary_period="year", work_mode="onsite")
    pref_comps = score_preferences(prefs, posting)
    llm = Component("skills_match", "Skills", 0.75, "very_important")
    assert combine([*pref_comps, llm]) == (75, [])
    assert combine(pref_comps) == (None, [])


def test_defaults():
    prefs = Preferences()
    important = {k for k, c in prefs.criteria().items() if c.importance != "dont_care"}
    assert important == {"salary", "locations", "role_category"}
    # Nothing is set yet, so the defaults alone give no score.
    assert combine(score_preferences(prefs, JobPosting(work_mode="onsite", salary_min=1))) == (None, [])


def test_from_dict_is_lenient():
    prefs = Preferences.from_dict({
        "salary": {"importance": "huge", "value": {"min": "120,000", "period": "fortnight"}},
        "work_mode": {"importance": "nice", "value": ["remote", "moon"]},
        "experience": {"importance": "nice", "value": 3},
        "education": {"value": "masters"},
        "skills_want": ["Python", "", None, "Python", 3],
        "nonsense": 1,
        "locations": "Chicago",
    })
    assert prefs.salary.importance == "important"
    assert prefs.salary.value == {"min": 120000.0, "currency": "USD", "period": "year"}
    assert prefs.work_mode.value == ["remote"] and prefs.work_mode.importance == "nice"
    assert not hasattr(prefs, "experience") and not hasattr(prefs, "education")
    assert prefs.skills_want == ["Python", "3"]
    assert prefs.locations.value == {"places": [], "remote_ok": True}
    assert Preferences.from_dict(None) == Preferences()
    assert Preferences.from_dict(prefs.to_dict()) == prefs


def test_save_and_load(tmp_path):
    conn = init_db(tmp_path / "p.db")
    assert load_preferences(conn) == Preferences()
    prefs = _prefs(salary=("dealbreaker", SALARY_FLOOR))
    save_preferences(conn, prefs)
    save_preferences(conn, prefs)  # updates the one row
    assert load_preferences(conn) == prefs
    assert conn.execute("SELECT COUNT(*) FROM user_preferences").fetchone()[0] == 1


def test_preferences_are_per_user(tmp_path):
    from strelitzia import users

    conn = init_db(tmp_path / "p.db")
    owner = users.owner(conn)
    alice = users.invite(conn, "alice@example.com")
    owner_prefs = _prefs(salary=("dealbreaker", SALARY_FLOOR))
    alice_prefs = _prefs(work_mode=("nice", ["remote"]))
    save_preferences(conn, owner_prefs, user_id=owner.id)
    save_preferences(conn, alice_prefs, user_id=alice.id)
    save_preferences(conn, alice_prefs, user_id=alice.id)  # updates alice's row only
    assert load_preferences(conn, owner.id) == owner_prefs
    assert load_preferences(conn) == owner_prefs  # the owner by default
    assert load_preferences(conn, alice.id) == alice_prefs
    assert load_preferences(conn, users.invite(conn, "bob@example.com").id) == Preferences()
    assert conn.execute("SELECT COUNT(*) FROM user_preferences").fetchone()[0] == 2
