"""Structured job-posting model: what the `extract` stage pulls out of each posting.

Dataclasses plus a hand-written JSON Schema for Claude structured output (same approach as
`scoring/resume_model.py`; no pydantic). `JobPosting.from_dict` is lenient: it drops
unknown keys, coerces bad enum values to "unknown" and bad numbers to None, so one odd
field never costs a whole extraction.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass, field, fields

from strelitzia.llm import json_object, nullable

# Bump whenever the schema or the extraction prompt changes: rows with an older
# extract_version are picked up again by the extract stage.
EXTRACT_VERSION = 4  # 2: title-first categories, no inferred skills/work mode, exact pay figures; 3: "Remote" is not a
# city; 4: Claude, one posting per request

ROLE_CATEGORIES = (
    "data_science", "data_engineering", "ml_ai_engineering", "data_analytics_bi",
    "software_engineering", "research_science", "product_program_management", "quant_finance",
    "hardware_electrical", "it_infra_devops", "other",
)
SENIORITIES = ("intern", "entry", "mid", "senior", "staff_principal", "manager", "director_plus", "unknown")
EMPLOYMENT_TYPES = ("full_time", "part_time", "contract", "internship", "co_op", "temporary", "unknown")
WORK_MODES = ("remote", "hybrid", "onsite", "unknown")
SALARY_PERIODS = ("year", "month", "hour", "unknown")
EDUCATION_LEVELS = ("none", "high_school", "associate", "bachelors", "masters", "phd", "unknown")
VISA_SPONSORSHIP = ("yes", "no", "unknown")
SECURITY_CLEARANCE = ("required", "preferred", "none", "unknown")

# Enum field -> allowed values. role_category falls back to "other", the rest to "unknown".
ENUMS: dict[str, tuple[str, ...]] = {
    "role_category": ROLE_CATEGORIES,
    "seniority": SENIORITIES,
    "employment_type": EMPLOYMENT_TYPES,
    "work_mode": WORK_MODES,
    "salary_period": SALARY_PERIODS,
    "education_level": EDUCATION_LEVELS,
    "visa_sponsorship": VISA_SPONSORSHIP,
    "security_clearance": SECURITY_CLEARANCE,
}

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass
class Location:
    city: str | None = None
    state: str | None = None
    country: str | None = None


@dataclass
class JobPosting:
    company: str | None = None
    title_normalized: str | None = None
    role_category: str = "other"
    seniority: str = "unknown"
    employment_type: str = "unknown"
    work_mode: str = "unknown"
    locations: list[Location] = field(default_factory=list)
    remote_region: str | None = None
    salary_min: float | None = None
    salary_max: float | None = None
    salary_currency: str | None = None
    salary_period: str | None = None
    posted_date: str | None = None  # YYYY-MM-DD
    application_deadline: str | None = None  # YYYY-MM-DD
    start_date: str | None = None  # YYYY-MM-DD
    summary: str = ""
    team: str | None = None
    responsibilities: list[str] = field(default_factory=list)
    required_qualifications: list[str] = field(default_factory=list)
    preferred_qualifications: list[str] = field(default_factory=list)
    skills_required: list[str] = field(default_factory=list)
    skills_preferred: list[str] = field(default_factory=list)
    education_level: str = "unknown"
    education_fields: list[str] = field(default_factory=list)
    years_experience_min: float | None = None
    years_experience_max: float | None = None
    visa_sponsorship: str = "unknown"
    security_clearance: str = "unknown"
    travel: str | None = None
    benefits: list[str] = field(default_factory=list)
    company_blurb: str | None = None

    @classmethod
    def from_dict(cls, data: dict) -> JobPosting:
        """Build from LLM JSON, dropping unknown keys and coercing bad values (never raises on content).

        Raises:
            ValueError: `data` is not a JSON object.
        """
        if not isinstance(data, dict):
            raise ValueError("posting JSON must be an object")  # noqa: TRY004 — ValueError = retryable validation
        out = cls()
        for f in fields(cls):
            if f.name not in data:
                continue
            raw = data[f.name]
            if f.name == "locations":
                locs = [_location(x) for x in raw or [] if isinstance(x, dict)]
                out.locations = [loc for loc in locs if loc.city or loc.state or loc.country]
            elif f.name in ENUMS:
                value = _enum(f.name, raw)
                # salary_period is optional: keep None when the posting has no salary.
                setattr(out, f.name, None if f.name == "salary_period" and raw is None else value)
            elif f.name in ("salary_min", "salary_max", "years_experience_min", "years_experience_max"):
                setattr(out, f.name, _number(raw))
            elif f.name in ("posted_date", "application_deadline", "start_date"):
                setattr(out, f.name, _date(raw))
            elif f.default_factory is list:  # list[str]
                setattr(out, f.name, _str_list(raw))
            elif f.name == "summary":
                out.summary = _str(raw) or ""
            else:
                setattr(out, f.name, _str(raw))
        return out

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


def _str(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _str_list(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [s for s in (_str(v) for v in value) if s]


def _number(value) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        num = float(str(value).replace(",", "").strip())
    except ValueError:
        return None
    return num if math.isfinite(num) and num >= 0 else None


def _date(value) -> str | None:
    text = _str(value)
    return text if text and _DATE_RE.match(text) else None


def _enum(name: str, value) -> str:
    allowed = ENUMS[name]
    text = (_str(value) or "").lower().replace("-", "_").replace(" ", "_")
    if text in allowed:
        return text
    return "other" if name == "role_category" else "unknown"


_NOT_A_PLACE = re.compile(r"^(?:remote|virtual|anywhere|hybrid|on-?site|n/?a|various|multiple locations?)$", re.IGNORECASE)


def _place(value) -> str | None:
    text = _str(value)
    return None if text is None or _NOT_A_PLACE.match(text) else text


def _location(data: dict) -> Location:
    """A Location, with work-mode words ("Remote") dropped: they aren't places."""
    return Location(city=_place(data.get("city")), state=_place(data.get("state")), country=_place(data.get("country")))


# ── Structured-output schema (JSON Schema) ────────────────────────────────

_NULL_STR = nullable({"type": "string"})
_NULL_NUM = nullable({"type": "number"})
_NULL_DATE = nullable({"type": "string", "format": "date"})
_STR_LIST = {"type": "array", "items": {"type": "string"}}
# Free-text fields where "" means unstated (`_str` reads it as None). Structured outputs allow at most 16
# union-typed parameters per schema, so only fields where null carries meaning stay nullable.
_TEXT = {"type": "string"}


def _enum_schema(values: tuple[str, ...]) -> dict:
    return {"type": "string", "enum": list(values)}


def posting_schema() -> dict:
    """JSON Schema for one JobPosting (property order = JobPosting field order)."""
    props = {
        "company": _NULL_STR,
        "title_normalized": _NULL_STR,
        "role_category": _enum_schema(ROLE_CATEGORIES),
        "seniority": _enum_schema(SENIORITIES),
        "employment_type": _enum_schema(EMPLOYMENT_TYPES),
        "work_mode": _enum_schema(WORK_MODES),
        "locations": {"type": "array", "items": json_object(
            {"city": _TEXT, "state": _TEXT, "country": _TEXT})},
        "remote_region": _NULL_STR,
        "salary_min": _NULL_NUM,
        "salary_max": _NULL_NUM,
        "salary_currency": _NULL_STR,
        "salary_period": nullable(_enum_schema(SALARY_PERIODS)),
        "posted_date": _NULL_DATE,
        "application_deadline": _NULL_DATE,
        "start_date": _NULL_DATE,
        "summary": {"type": "string"},
        "team": _TEXT,
        "responsibilities": _STR_LIST,
        "required_qualifications": _STR_LIST,
        "preferred_qualifications": _STR_LIST,
        "skills_required": _STR_LIST,
        "skills_preferred": _STR_LIST,
        "education_level": _enum_schema(EDUCATION_LEVELS),
        "education_fields": _STR_LIST,
        "years_experience_min": _NULL_NUM,
        "years_experience_max": _NULL_NUM,
        "visa_sponsorship": _enum_schema(VISA_SPONSORSHIP),
        "security_clearance": _enum_schema(SECURITY_CLEARANCE),
        "travel": _TEXT,
        "benefits": _STR_LIST,
        "company_blurb": _TEXT,
    }
    return json_object(props)
