"""Job preferences and the code-scored preference layer of the fit score.

Each preference is a `Criterion`: a value (salary floor, accepted work modes, ...) and an importance from Don't care
to Dealbreaker. `score_preferences` compares the criteria to the fields extraction stored for a posting (no LLM
call), and `combine` folds those components plus the assessment layer's (`scoring/assessment.py`) into one 0-100
fit score. A posting value that is unknown scores 0.5 and never triggers a dealbreaker.

Preferences live in the `user_preferences` table (one row per user; the owner's until the other parts thread a user).
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field, fields
from datetime import UTC, datetime
from typing import Any, Literal

from strelitzia import users
from strelitzia.enrichment.posting_model import (
    EDUCATION_LEVELS,
    EMPLOYMENT_TYPES,
    ROLE_CATEGORIES,
    SENIORITIES,
    WORK_MODES,
    JobPosting,
)

Importance = Literal["dont_care", "nice", "important", "very_important", "dealbreaker"]
IMPORTANCES: tuple[str, ...] = ("dont_care", "nice", "important", "very_important", "dealbreaker")
# Weight in the overall mean. A dealbreaker weighs like Very important and also flags the job when it fails.
WEIGHTS: dict[str, int] = {"dont_care": 0, "nice": 1, "important": 2, "very_important": 4, "dealbreaker": 4}

UNKNOWN = 0.5  # score of a criterion the posting doesn't say anything about
QUALIFICATION_CLOSE = 0.4  # education or experience just short of the requirement
QUALIFICATION_IMPORTANCE = "very_important"
SALARY_PERIODS_PER_YEAR = {"year": 1, "month": 12, "hour": 2080}
# Required education, lowest first ("unknown" is not ranked).
_EDUCATION_RANK = {level: i for i, level in enumerate(EDUCATION_LEVELS) if level != "unknown"}


@dataclass
class Criterion:
    importance: str = "dont_care"
    value: Any = None


def _criterion(importance: str, value: Any) -> Any:
    return field(default_factory=lambda: Criterion(importance, value() if callable(value) else value))


@dataclass
class Preferences:
    # Preference layer (scored in code)
    salary: Criterion = _criterion("important", lambda: {"min": None, "currency": "USD", "period": "year"})
    locations: Criterion = _criterion("important", lambda: {"places": [], "remote_ok": True})
    work_mode: Criterion = _criterion("dont_care", list)
    role_category: Criterion = _criterion("important", list)
    seniority: Criterion = _criterion("dont_care", list)
    employment_type: Criterion = _criterion("dont_care", list)
    visa: Criterion = _criterion("dont_care", False)  # value: I need sponsorship
    clearance: Criterion = _criterion("dont_care", False)  # value: I hold a clearance
    travel: Criterion = _criterion("dont_care", None)  # value: max travel %
    company_blocklist: Criterion = _criterion("dont_care", list)
    # Assessment layer importances (scored by Claude, see scoring/assessment.py); their value is unused.
    skills_match: Criterion = _criterion("very_important", None)
    experience_match: Criterion = _criterion("dont_care", None)
    domain_interest: Criterion = _criterion("dont_care", None)
    red_flags: Criterion = _criterion("dont_care", None)
    # Assessment layer inputs: changing these makes every assessment stale.
    skills_want: list[str] = field(default_factory=list)
    skills_avoid: list[str] = field(default_factory=list)
    industries_favor: list[str] = field(default_factory=list)
    industries_avoid: list[str] = field(default_factory=list)
    target_roles: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict | None) -> Preferences:
        """Build from stored or posted JSON. Unknown keys are dropped and bad values fall back to the default."""
        out = cls()
        if not isinstance(data, dict):
            return out
        for f in fields(cls):
            if f.name not in data:
                continue
            raw = data[f.name]
            if f.name in LLM_INPUTS:
                setattr(out, f.name, _str_list(raw))
                continue
            default: Criterion = getattr(out, f.name)
            if not isinstance(raw, dict):
                continue
            importance = raw.get("importance")
            if importance in IMPORTANCES:
                default.importance = importance
            if "value" in raw:
                value = _VALUE_COERCE[f.name](raw["value"], default.value)
                default.value = value
        return out

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)

    def criteria(self) -> dict[str, Criterion]:
        return {f.name: getattr(self, f.name) for f in fields(self) if f.name not in LLM_INPUTS}


LLM_INPUTS = ("skills_want", "skills_avoid", "industries_favor", "industries_avoid", "target_roles")
LLM_CRITERIA = ("skills_match", "experience_match", "domain_interest", "red_flags")
LABELS = {
    "salary": "Salary", "locations": "Location", "work_mode": "Work mode", "role_category": "Job field",
    "seniority": "Seniority", "employment_type": "Employment type", "visa": "Visa sponsorship",
    "clearance": "Security clearance", "travel": "Travel", "education": "Education", "experience": "Experience",
    "company_blocklist": "Company", "skills_match": "Skills", "experience_match": "Experience match",
    "domain_interest": "Domain interest", "red_flags": "Red flags",
}
# The value lists the dashboard offers for set-valued criteria.
OPTIONS = {
    "work_mode": [m for m in WORK_MODES if m != "unknown"],
    "role_category": list(ROLE_CATEGORIES),
    "seniority": [s for s in SENIORITIES if s != "unknown"],
    "employment_type": [t for t in EMPLOYMENT_TYPES if t != "unknown"],
    "salary_period": list(SALARY_PERIODS_PER_YEAR),
    "importance": list(IMPORTANCES),
}


# ── Lenient value coercion ────────────────────────────────────────────────

def _str(value) -> str | None:
    if value is None or isinstance(value, (dict, list)):
        return None
    text = str(value).strip()
    return text or None


def _str_list(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(s for s in (_str(v) for v in value) if s))


def _number(value) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        num = float(str(value).replace(",", "").strip())
    except ValueError:
        return None
    return num if num >= 0 and num == num and num != float("inf") else None


def _choices(allowed: list[str]):
    def coerce(value, _default):
        return [v for v in _str_list(value) if v in allowed]
    return coerce


def _salary(value, default):
    if not isinstance(value, dict):
        return default
    period = value.get("period")
    return {
        "min": _number(value.get("min")),
        "currency": (_str(value.get("currency")) or "USD").upper(),
        "period": period if period in SALARY_PERIODS_PER_YEAR else "year",
    }


def _locations(value, default):
    if not isinstance(value, dict):
        return default
    return {"places": _str_list(value.get("places")), "remote_ok": bool(value.get("remote_ok", True))}


_VALUE_COERCE = {
    "salary": _salary,
    "locations": _locations,
    "work_mode": _choices(OPTIONS["work_mode"]),
    "role_category": _choices(OPTIONS["role_category"]),
    "seniority": _choices(OPTIONS["seniority"]),
    "employment_type": _choices(OPTIONS["employment_type"]),
    "visa": lambda v, _d: bool(v),
    "clearance": lambda v, _d: bool(v),
    "travel": lambda v, _d: _number(v),
    "company_blocklist": lambda v, _d: _str_list(v),
    **{name: (lambda v, _d: None) for name in LLM_CRITERIA},
}


# ── Scoring ───────────────────────────────────────────────────────────────

@dataclass
class Component:
    """One criterion's result. `score` is 0-1, or None when the criterion isn't set (left out of the mean)."""

    key: str
    label: str
    score: float | None
    importance: str
    dealbreaker_hit: bool = False
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _component(key: str, crit: Criterion, score: float | None, note: str = "") -> Component:
    hit = crit.importance == "dealbreaker" and score is not None and score <= 0
    return Component(key, LABELS[key], score, crit.importance, hit, note)


def _money(amount: float, currency: str) -> str:
    return f"${amount:,.0f}" if currency == "USD" else f"{amount:,.0f} {currency}"


# A posting's pay with no stated period is read as hourly below this and yearly at or above the next; anything in
# between (a monthly figure? a low yearly one?) is too ambiguous to compare and scores as unknown.
_HOURLY_BELOW, _YEARLY_FROM = 1000, 20000


def _yearly(amount: float, period: str | None) -> float | None:
    if period not in SALARY_PERIODS_PER_YEAR:
        if amount < _HOURLY_BELOW:
            period = "hour"
        elif amount >= _YEARLY_FROM:
            period = "year"
        else:
            return None
    return amount * SALARY_PERIODS_PER_YEAR[period]


def _score_salary(crit: Criterion, p: JobPosting) -> tuple[float | None, str]:
    want = crit.value
    if not want.get("min"):
        return None, ""
    floor = _yearly(want["min"], want.get("period") or "year")
    top = p.salary_max if p.salary_max is not None else p.salary_min
    if top is None:
        return UNKNOWN, "salary not listed"
    currency = (p.salary_currency or want["currency"]).upper()
    if currency != want["currency"]:
        return UNKNOWN, f"salary in {currency}, not {want['currency']}"
    yearly = _yearly(top, p.salary_period)
    if yearly is None:
        return UNKNOWN, "salary period not stated"
    if yearly >= floor:
        return 1.0, f"up to {_money(yearly, currency)}/year meets your {_money(floor, currency)} minimum"
    return 0.0, f"salary below minimum ({_money(yearly, currency)}/year < {_money(floor, currency)})"


_COUNTRY_ALIASES = {"united states": "us", "usa": "us", "united states of america": "us", "u.s.": "us",
                    "united kingdom": "uk", "great britain": "uk", "gb": "uk"}


def _norm_place(text: str) -> str:
    text = text.strip().lower()
    return _COUNTRY_ALIASES.get(text, text)


def _place_matches(place: str, loc) -> bool:
    """Every comma part of `place` ("Chicago, IL") names the job location's city, state or country."""
    parts = [_norm_place(x) for x in place.split(",") if x.strip()]
    have = {_norm_place(x) for x in (loc.city, loc.state, loc.country) if x}
    return bool(parts) and all(part in have for part in parts)


def _score_locations(crit: Criterion, p: JobPosting) -> tuple[float | None, str]:
    places, remote_ok = crit.value["places"], crit.value["remote_ok"]
    if not places:
        return None, ""
    if p.work_mode == "remote" and remote_ok:
        return 1.0, "remote"
    if not p.locations:
        return UNKNOWN, "location not listed"
    for loc in p.locations:
        for place in places:
            if _place_matches(place, loc):
                return 1.0, f"in {place}"
    where = ", ".join(x for x in (p.locations[0].city, p.locations[0].state, p.locations[0].country) if x)
    return 0.0, f"location not in your list ({where})"


def _score_choice(key: str, crit: Criterion, actual: str) -> tuple[float | None, str]:
    accepted = crit.value
    if not accepted:
        return None, ""
    if actual in (None, "unknown"):
        return UNKNOWN, f"{LABELS[key].lower()} not stated"
    shown = actual.replace("_", " ")
    return (1.0, shown) if actual in accepted else (0.0, f"{shown} is not one you want")


def _score_visa(crit: Criterion, p: JobPosting) -> tuple[float | None, str]:
    if not crit.value:
        return None, ""
    return {"yes": (1.0, "sponsors visas"), "no": (0.0, "no visa sponsorship")}.get(
        p.visa_sponsorship, (UNKNOWN, "visa sponsorship not stated"))


def _score_clearance(crit: Criterion, p: JobPosting) -> tuple[float | None, str]:
    if crit.value:  # holding a clearance rules nothing out
        return None, ""
    return {"required": (0.0, "security clearance required"), "preferred": (0.75, "clearance preferred"),
            "none": (1.0, "no clearance needed")}.get(p.security_clearance, (UNKNOWN, "clearance not stated"))


_PERCENT = re.compile(r"(\d{1,3})\s*%")
_NO_TRAVEL = re.compile(r"^\s*(?:none|no|no travel|0|n/a|not required)\b", re.IGNORECASE)


def travel_percent(text: str | None) -> float | None:
    """The highest percentage in the posting's travel text ("up to 25%" -> 25), 0 for "none", else None."""
    if not text:
        return None
    found = [int(x) for x in _PERCENT.findall(text)]
    if found:
        return float(max(found))
    return 0.0 if _NO_TRAVEL.match(text) else None


def _score_travel(crit: Criterion, p: JobPosting) -> tuple[float | None, str]:
    if crit.value is None:
        return None, ""
    pct = travel_percent(p.travel)
    if pct is None:
        return UNKNOWN, "travel not stated"
    if pct <= crit.value:
        return 1.0, f"{pct:.0f}% travel"
    return 0.0, f"{pct:.0f}% travel is above your {crit.value:.0f}%"


def _score_education(have: str | None, p: JobPosting) -> tuple[float, bool, str]:
    needed = _EDUCATION_RANK.get(p.education_level)
    if needed is None:
        return UNKNOWN, False, "education not stated"
    shown = p.education_level.replace("_", " ")
    mine = _EDUCATION_RANK.get(have)
    if mine is None:
        return UNKNOWN, False, f"asks for {shown}; add your education to your Profile"
    if mine >= needed:
        return 1.0, False, f"asks for {shown}"
    if needed - mine == 1:
        return QUALIFICATION_CLOSE, False, f"asks for {shown}, one step above yours"
    return 0.0, True, f"asks for {shown}"


def _score_experience(have: float | None, p: JobPosting) -> tuple[float, bool, str]:
    need = p.years_experience_min
    if need is None:
        return UNKNOWN, False, "years of experience not stated"
    note = f"asks for {need:g}+ years"
    if have is None:
        return UNKNOWN, False, f"{note}; add your years of experience to your Profile"
    if have >= need:
        return 1.0, False, note
    if need - have <= 1 or have >= 0.75 * need:
        return QUALIFICATION_CLOSE, False, f"{note}, you have {have:g}"
    return 0.0, True, f"{note}, you have {have:g}"


def score_qualifications(profile, posting: JobPosting) -> list[Component]:
    """Education and experience: the user's Profile (`education_level`, `years_experience`; None for no profile)
    against what the posting asks for, by fixed rules at Very important weight.

    Meeting the requirement scores 1.0, falling just short (one education step, or within a year or 75% of the
    years) 0.4, and anything further a dealbreaker. Unknown on either side scores 0.5 and never breaks.
    """
    out = []
    for key, scorer, have in (
        ("education", _score_education, getattr(profile, "education_level", None)),
        ("experience", _score_experience, getattr(profile, "years_experience", None)),
    ):
        score, hit, note = scorer(have, posting)
        out.append(Component(key, LABELS[key], score, QUALIFICATION_IMPORTANCE, hit, note))
    return out


def _score_company(crit: Criterion, company: str | None) -> tuple[float | None, str]:
    blocked = crit.value
    if not blocked:
        return None, ""
    name = (company or "").strip().lower()
    if not name:
        return UNKNOWN, "company unknown"
    for b in blocked:
        if b.strip().lower() == name:
            return 0.0, f"{company} is on your skip list"
    return 1.0, ""


def score_preferences(prefs: Preferences, posting: JobPosting, company: str | None = None) -> list[Component]:
    """The preference-layer components for one posting (every criterion, set or not; see `combine`).

    `company` is the job row's company, used when the posting has none.
    """
    scorers = {
        "salary": lambda c: _score_salary(c, posting),
        "locations": lambda c: _score_locations(c, posting),
        "work_mode": lambda c: _score_choice("work_mode", c, posting.work_mode),
        "role_category": lambda c: _score_choice("role_category", c, posting.role_category),
        "seniority": lambda c: _score_choice("seniority", c, posting.seniority),
        "employment_type": lambda c: _score_choice("employment_type", c, posting.employment_type),
        "visa": lambda c: _score_visa(c, posting),
        "clearance": lambda c: _score_clearance(c, posting),
        "travel": lambda c: _score_travel(c, posting),
        "company_blocklist": lambda c: _score_company(c, posting.company or company),
    }
    out = []
    for key, score in scorers.items():
        crit = getattr(prefs, key)
        value, note = score(crit)
        out.append(_component(key, crit, value, note))
    return out


def combine(components: list[Component]) -> tuple[int | None, list[str]]:
    """(overall 0-100, keys of the failed dealbreakers).

    The overall is the weighted mean of the components that are set (score not None) and not Don't care; None when
    there are none.
    """
    total = weight = 0.0
    for c in components:
        w = WEIGHTS.get(c.importance, 0)
        if w and c.score is not None:
            total += w * c.score
            weight += w
    overall = round(100 * total / weight) if weight else None
    return overall, [c.key for c in components if c.dealbreaker_hit]


# ── Storage (user_preferences) ────────────────────────────────────────────

def _user(conn, user_id: str | None) -> str:
    return user_id or users.owner(conn).id


def load_preferences(conn, user_id: str | None = None) -> Preferences:
    """The user's saved preferences (the owner's by default), or the defaults."""
    row = conn.execute("SELECT prefs_json FROM user_preferences WHERE user_id = ?",
                       (_user(conn, user_id),)).fetchone()
    if row is None or not row[0]:
        return Preferences()
    try:
        return Preferences.from_dict(json.loads(row[0]))
    except ValueError:
        return Preferences()


def save_preferences(conn, prefs: Preferences, user_id: str | None = None) -> None:
    now = datetime.now(UTC).isoformat(timespec="seconds")
    conn.execute(
        "INSERT INTO user_preferences (user_id, prefs_json, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT (user_id) DO UPDATE SET prefs_json = excluded.prefs_json, updated_at = excluded.updated_at",
        (_user(conn, user_id), prefs.to_json(), now),
    )
    conn.commit()
