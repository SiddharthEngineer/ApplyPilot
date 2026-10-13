"""Tests for the structured job-posting model and its structured-output schema."""

from dataclasses import fields

from strelitzia.enrichment.posting_model import (
    ENUMS,
    ROLE_CATEGORIES,
    JobPosting,
    Location,
    posting_schema,
)
from schema_rules import assert_strict_schema

SAMPLE = {
    "company": "Acme",
    "title_normalized": "Senior Data Engineer",
    "role_category": "data_engineering",
    "seniority": "senior",
    "employment_type": "full_time",
    "work_mode": "hybrid",
    "locations": [{"city": "Chicago", "state": "IL", "country": "US"}],
    "remote_region": None,
    "salary_min": 120000,
    "salary_max": 150000.5,
    "salary_currency": "USD",
    "salary_period": "year",
    "posted_date": "2026-09-30",
    "application_deadline": None,
    "start_date": None,
    "summary": "Builds pipelines.",
    "team": "Data Platform",
    "responsibilities": ["Build pipelines"],
    "required_qualifications": ["5+ years SQL"],
    "preferred_qualifications": ["Spark"],
    "skills_required": ["SQL", "Python"],
    "skills_preferred": ["Spark"],
    "education_level": "bachelors",
    "education_fields": ["Computer Science"],
    "years_experience_min": 5,
    "years_experience_max": None,
    "visa_sponsorship": "no",
    "security_clearance": "none",
    "travel": None,
    "benefits": ["401k"],
    "company_blurb": None,
}


class TestFromDict:
    def test_round_trip(self):
        p = JobPosting.from_dict(SAMPLE)
        assert p.locations == [Location("Chicago", "IL", "US")]
        assert p.salary_min == 120000.0 and p.years_experience_min == 5.0
        again = JobPosting.from_dict(p.to_dict())
        assert again == p
        assert JobPosting.from_dict(__import__("json").loads(p.to_json())) == p

    def test_enum_coercion(self):
        p = JobPosting.from_dict({
            "role_category": "astronaut", "seniority": "Senior", "employment_type": "Full-Time",
            "work_mode": "on the moon", "education_level": None, "salary_period": "fortnight",
        })
        assert p.role_category == "other"
        assert p.seniority == "senior"
        assert p.employment_type == "full_time"
        assert p.work_mode == "unknown"
        assert p.education_level == "unknown"
        assert p.salary_period == "unknown"

    def test_salary_period_null_stays_null(self):
        assert JobPosting.from_dict({"salary_period": None}).salary_period is None

    def test_unknown_keys_dropped(self):
        p = JobPosting.from_dict({**SAMPLE, "job_id": "abc", "favorite_color": "blue"})
        assert "favorite_color" not in p.to_dict() and "job_id" not in p.to_dict()

    def test_bad_values_coerced(self):
        p = JobPosting.from_dict({
            "salary_min": "120,000", "salary_max": "lots", "years_experience_min": -1,
            "posted_date": "Sept 30", "application_deadline": "2026-11-01",
            "responsibilities": "not a list", "skills_required": ["SQL", "", None, " Go "],
            "locations": [{"city": "X"}, "junk"], "summary": None, "company": "  ",
        })
        assert p.salary_min == 120000.0 and p.salary_max is None and p.years_experience_min is None
        assert p.posted_date is None and p.application_deadline == "2026-11-01"
        assert p.responsibilities == [] and p.skills_required == ["SQL", "Go"]
        assert p.locations == [Location("X", None, None)]
        assert p.summary == "" and p.company is None

    def test_remote_is_not_a_city(self):
        p = JobPosting.from_dict({"locations": [{"city": "Remote", "state": None, "country": "US"},
                                                {"city": "remote", "state": None, "country": None}]})
        assert p.locations == [Location(None, None, "US")]

    def test_non_object_raises(self):
        import pytest
        with pytest.raises(ValueError):
            JobPosting.from_dict(["nope"])


class TestSchema:
    def test_properties_match_dataclass(self):
        s = posting_schema()
        names = [f.name for f in fields(JobPosting)]
        assert list(s["properties"]) == names and s["required"] == names

    def test_enums_listed(self):
        props = posting_schema()["properties"]
        for name, values in ENUMS.items():
            schema = props[name]["anyOf"][0] if name == "salary_period" else props[name]
            assert schema["enum"] == list(values)
        assert props["role_category"]["enum"] == list(ROLE_CATEGORIES)

    def test_valid_for_structured_outputs(self):
        assert_strict_schema(posting_schema())

    def test_nullable_fields(self):
        props = posting_schema()["properties"]
        assert props["company"] == {"anyOf": [{"type": "string"}, {"type": "null"}]}
        assert props["salary_period"]["anyOf"][0]["enum"] == list(ENUMS["salary_period"])
        assert props["posted_date"]["anyOf"][0]["format"] == "date"
