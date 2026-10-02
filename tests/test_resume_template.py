"""Tests for the template-driven tailored resume (resume-template-tailoring plan).

All tests use the synthetic library in tests/fixtures/content_library_sample.md, never the user's file.
"""

import json
import re
from pathlib import Path

import pytest

from applypilot.scoring.content_library import parse_content_library, slugify
from applypilot.scoring.resume_model import Bullet, RoleEntry, TailoredResume
from applypilot.scoring.template import (
    DEFAULT_TEMPLATE_PATH,
    build_contact,
    education_from_resume_text,
    load_fixed,
    render_resume,
    split_trailing_dates,
)

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


# ── Task 2: template renderer ─────────────────────────────────────────────

PROFILE = {
    "personal": {
        "full_name": "Alex Q. Tester",
        "email": "alex@example.com",
        "phone": "(555) 010-0000",
        "city": "Springfield",
        "province_state": "IL",
        "linkedin_url": "https://linkedin.com/in/alex",
        "github_url": "https://github.com/alex",
    }
}
FIXED = {
    "education": [{"heading": "State University, B.S. Statistics", "dates": "May 2021",
                   "bullets": ["Minor in Mathematics", "Coursework: Data Mining"]}],
    "roles": {},
}


def _block(html: str, block_id: str) -> str:
    """The element with this id, up to the next top-level block."""
    m = re.search(rf'<div class="[^"]*" id="{block_id}">.*?(?=\n<div class="[^"]*" id="resume-|\n</body>)', html, re.DOTALL)
    assert m, f"{block_id} not found"
    return m.group(0)


class TestRenderResume:
    def _other(self, library) -> TailoredResume:
        data = _llm_json()
        data["roles"] = data["roles"][:1]
        data["roles"][0]["bullets"] = [{"text": "Something else entirely <b>bold</b> & co.",
                                        "project_ids": ["quality-dashboard"]}]
        data["skills"] = [{"category": "Other", "items": "Excel"}]
        return TailoredResume.from_llm_json(data, library)

    def test_fixed_blocks_identical_across_resumes(self, library):
        a = render_resume(TailoredResume.from_llm_json(_llm_json(), library), PROFILE,
                          template_path=DEFAULT_TEMPLATE_PATH, fixed=FIXED)
        b = render_resume(self._other(library), PROFILE, template_path=DEFAULT_TEMPLATE_PATH, fixed=FIXED)
        assert a != b
        for block in ("resume-header", "resume-education"):
            assert _block(a, block) == _block(b, block)
        assert "Alex Q. Tester" in _block(a, "resume-header")
        assert "Minor in Mathematics" in _block(a, "resume-education")

    def test_content_rendered_and_escaped(self, library):
        html = render_resume(self._other(library), PROFILE, template_path=DEFAULT_TEMPLATE_PATH, fixed=FIXED)
        assert "&lt;b&gt;bold&lt;/b&gt; &amp; co." in html
        assert "<b>bold</b>" not in html
        assert "Data Engineer at Acme Corp" in html
        assert "Excel" in html

    def test_user_template_preferred(self, library, tmp_path, monkeypatch):
        import applypilot.scoring.template as t
        user = tmp_path / "resume_template.html"
        user.write_text("<p>{{ name }}|{% for r in roles %}{{ r.company }}{% endfor %}</p>", encoding="utf-8")
        monkeypatch.setattr(t, "RESUME_TEMPLATE_PATH", user)
        html = render_resume(self._other(library), PROFILE, fixed=FIXED)
        assert html == "<p>Alex Q. Tester|Acme Corp</p>"

    def test_default_template_without_user_template(self, library, tmp_path, monkeypatch):
        import applypilot.scoring.template as t
        monkeypatch.setattr(t, "RESUME_TEMPLATE_PATH", tmp_path / "missing.html")
        html = render_resume(self._other(library), PROFILE, fixed=FIXED)
        assert 'id="resume-experience"' in html and "@page" in html

    def test_contact_from_profile(self):
        items = build_contact(PROFILE)
        assert [c["text"] for c in items] == ["Springfield, IL", "alex@example.com", "(555) 010-0000",
                                              "LinkedIn", "GitHub"]
        assert items[3]["href"] == "https://linkedin.com/in/alex"


class TestFixedBlocks:
    RESUME_TXT = (
        "ALEX Q. TESTER\n\nWORK EXPERIENCE\n\nAnalyst at Foo Jan 2020 – present\n• Did things\n\n"
        "EDUCATION\n\nState University, B.S. Statistics, Honors (GPA: 3.9) May 2021\n"
        "• Minor in Mathematics\n• Relevant coursework: Data Mining, Machine Learning,\nA.I., Optimization\n\n"
        "SKILLS\n\nLanguages: Python\n"
    )

    def test_split_trailing_dates(self):
        assert split_trailing_dates("Analyst at Foo June 2023 – Sep 2025") == ("Analyst at Foo", "June 2023 – Sep 2025")
        assert split_trailing_dates("Analyst at Foo Sep 2025 – present") == ("Analyst at Foo", "Sep 2025 – present")
        assert split_trailing_dates("No dates here") == ("No dates here", "")

    def test_education_from_resume_text(self):
        edu = education_from_resume_text(self.RESUME_TXT)
        assert edu == [{
            "heading": "State University, B.S. Statistics, Honors (GPA: 3.9)",
            "dates": "May 2021",
            "bullets": ["Minor in Mathematics",
                        "Relevant coursework: Data Mining, Machine Learning, A.I., Optimization"],
        }]

    def test_load_fixed_yaml(self, tmp_path):
        y = tmp_path / "resume_fixed.yaml"
        y.write_text("education:\n  - heading: X Univ\n    dates: 2020\n    bullets: [a]\n"
                     "roles:\n  k:\n    company: Foo\n", encoding="utf-8")
        assert load_fixed(y) == {"education": [{"heading": "X Univ", "dates": "2020", "bullets": ["a"]}],
                                 "roles": {"k": {"company": "Foo"}}}

    def test_load_fixed_falls_back_to_resume_txt(self, tmp_path):
        txt = tmp_path / "resume.txt"
        txt.write_text(self.RESUME_TXT, encoding="utf-8")
        fixed = load_fixed(tmp_path / "missing.yaml", resume_text_path=txt)
        assert fixed["education"][0]["dates"] == "May 2021"
        assert fixed["roles"] == {}
