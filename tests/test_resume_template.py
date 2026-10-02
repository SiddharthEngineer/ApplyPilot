"""Tests for the template-driven tailored resume (resume-template-tailoring plan).

All tests use the synthetic library in tests/fixtures/content_library_sample.md, never the user's file.
"""

import json
from pathlib import Path

import pytest

from applypilot.scoring.content_library import parse_content_library, slugify
from applypilot.scoring.resume_model import Bullet, RoleEntry, TailoredResume

SAMPLE_LIBRARY = Path(__file__).resolve().parent / "fixtures" / "content_library_sample.md"


@pytest.fixture
def library():
    return parse_content_library(SAMPLE_LIBRARY)


def _llm_json() -> dict:
    return {
        "roles": [
            {
                "role_key": "data-engineer-acme",
                "title": "Data Engineer",
                "company": "Acme Corp",
                "dates": "Jan 2024 – present",
                "tagline": "Retail company.",
                "bullets": [
                    {"text": "Led the orders pipeline (Airflow, Spark), cutting runtime from 6 hours to 45 minutes.",
                     "project_ids": ["orders-pipeline-lead"]},
                    {"text": "Built a Superset quality dashboard for 3 teams.",
                     "project_ids": ["quality-dashboard"]},
                ],
            },
            {
                "role_key": "data-analyst-intern-globex",
                "title": "Data Analyst Intern",
                "company": "Globex",
                "dates": "Jun 2022 – Aug 2022",
                "tagline": None,
                "bullets": [{"text": "Built a churn model (AUC 0.83).", "project_ids": ["churn-model"]}],
            },
        ],
        "skills": [
            {"category": "Data Engineering", "items": "Airflow, Spark, dbt"},
            {"category": "Languages", "items": "Python, SQL"},
        ],
        "dropped_roles": [],
    }


# ── Task 1: model ─────────────────────────────────────────────────────────


class TestSlugs:
    def test_slugify(self):
        assert slugify("PatentsView Data Quality Lead — earlier phase") == "patentsview-data-quality-lead-earlier-phase"
        assert slugify("Data Analytics Intern, CapConnect+") == "data-analytics-intern-capconnect"

    def test_role_keys_and_project_slugs(self, library):
        assert [r.key for r in library.roles] == ["data-engineer-acme", "data-analyst-intern-globex"]
        assert [p.slug for p in library.roles[0].projects] == ["orders-pipeline-lead", "quality-dashboard"]

    def test_duplicate_project_name_gets_role_suffix(self, library):
        slugs = [p.slug for r in library.roles for p in r.projects]
        assert "orders-pipeline-lead-data-analyst-intern-globex" in slugs
        assert len(slugs) == len(set(slugs))

    def test_lookup(self, library):
        assert library.project_by_slug("churn-model").name == "Churn Model"
        assert library.role_by_key("nope") is None
        assert "1.2M" in library.project_by_slug("churn-model").facts()


class TestTailoredResumeModel:
    def test_from_llm_json(self, library):
        r = TailoredResume.from_llm_json(_llm_json(), library)
        assert [e.role_key for e in r.roles] == ["data-engineer-acme", "data-analyst-intern-globex"]
        assert r.roles[0].bullets[0].project_ids == ["orders-pipeline-lead"]
        assert list(r.skills) == ["Data Engineering", "Languages"]
        assert r.roles[1].tagline is None

    def test_round_trip(self, library):
        r = TailoredResume.from_llm_json(_llm_json(), library)
        again = TailoredResume.from_llm_json(json.loads(r.to_json()), library)
        assert again == r

    def test_to_json_extra_keys(self, library):
        r = TailoredResume.from_llm_json(_llm_json(), library)
        assert json.loads(r.to_json(job_url="https://x"))["job_url"] == "https://x"

    def test_unknown_project_id_raises(self, library):
        data = _llm_json()
        data["roles"][0]["bullets"][0]["project_ids"] = ["made-up-project"]
        with pytest.raises(ValueError, match="made-up-project"):
            TailoredResume.from_llm_json(data, library)

    def test_unknown_role_key_raises(self, library):
        data = _llm_json()
        data["roles"][0]["role_key"] = "ceo-acme"
        with pytest.raises(ValueError, match="ceo-acme"):
            TailoredResume.from_llm_json(data, library)

    def test_project_from_other_role_raises(self, library):
        data = _llm_json()
        data["roles"][1]["bullets"][0]["project_ids"] = ["orders-pipeline-lead"]
        with pytest.raises(ValueError, match="does not belong"):
            TailoredResume.from_llm_json(data, library)

    def test_fixed_roles_override_llm_fields(self, library):
        fixed = {"data-engineer-acme": {"title": "Data Engineer", "company": "Acme Corporation",
                                        "dates": "Jan 2024 – present", "tagline": "Fixed tagline."}}
        data = _llm_json()
        data["roles"][0]["company"] = "Hallucinated Inc"
        r = TailoredResume.from_llm_json(data, library, fixed_roles=fixed)
        assert r.roles[0].company == "Acme Corporation"
        assert r.roles[0].tagline == "Fixed tagline."

    def test_skills_as_dict_accepted(self, library):
        data = _llm_json()
        data["skills"] = {"B": "x", "A": "y"}
        assert list(TailoredResume.from_llm_json(data, library).skills) == ["B", "A"]

    def test_dataclasses_construct_directly(self):
        r = TailoredResume(roles=[RoleEntry("k", "T", "C", "D", None, [Bullet("b", [])])])
        assert json.loads(r.to_json())["roles"][0]["bullets"][0] == {"text": "b", "project_ids": []}
