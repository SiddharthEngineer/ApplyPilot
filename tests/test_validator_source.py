"""Tests for validator source parameter (content-library mode relaxation)."""

from applypilot.scoring.validator import (
    validate_json_fields,
    validate_tailored_resume,
)

import pytest


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def profile():
    return {
        "personal": {
            "full_name": "Siddharth Engineer",
            "email": "test@example.com",
            "phone": "555-1234",
        },
        "skills_boundary": {
            "languages": ["Python", "SQL"],
            "frameworks": ["Django", "React"],
        },
        "resume_facts": {
            "preserved_companies": ["AIR", "Capgemini"],
            "preserved_projects": ["PatentsView", "CAFE"],
            "preserved_school": "Carnegie Mellon University",
            "real_metrics": ["14M rows", "30,000 researchers"],
        },
        "experience": {
            "education_level": "B.S. in Computer Science",
        },
    }


@pytest.fixture
def valid_json_data():
    return {
        "title": "Data Scientist",
        "summary": "Experienced data scientist with expertise in Python and SQL.",
        "skills": {
            "Languages": "Python, SQL",
            "Frameworks": "Django, React",
        },
        "experience": [
            {
                "header": "Data Science Associate at AIR",
                "subtitle": "Sep 2025-Present",
                "bullets": [
                    "Built data pipeline processing 14M rows using Python and Airflow",
                ],
            }
        ],
        "projects": [
            {
                "header": "PatentsView Pipeline",
                "subtitle": "Python | 2025",
                "bullets": ["Automated patent data extraction"],
            }
        ],
        "education": "Carnegie Mellon University | B.S. in Computer Science",
    }


# ── validate_json_fields: source parameter ────────────────────────────────


class TestValidateJsonFieldsSource:
    def test_resume_mode_enforces_preserved_companies(self, profile, valid_json_data):
        """In resume mode, missing preserved companies must cause an error."""
        valid_json_data["experience"] = [
            {"header": "Engineer at OtherCo", "subtitle": "2024-Present", "bullets": ["Did stuff"]}
        ]
        result = validate_json_fields(valid_json_data, profile, mode="normal", source="resume")
        assert not result["passed"]
        assert any("Company" in e and "missing" in e for e in result["errors"])

    def test_content_library_mode_skips_preserved_companies(self, profile, valid_json_data):
        """In content-library mode, missing preserved companies should NOT cause an error."""
        valid_json_data["experience"] = [
            {"header": "Engineer at OtherCo", "subtitle": "2024-Present", "bullets": ["Did stuff"]}
        ]
        result = validate_json_fields(valid_json_data, profile, mode="normal", source="content-library")
        # Should pass (no company error) — other checks may still apply
        company_errors = [e for e in result["errors"] if "Company" in e and "missing" in e]
        assert company_errors == []

    def test_content_library_mode_still_checks_fabrication(self, profile, valid_json_data):
        """In content-library mode, fabrication detection still works."""
        valid_json_data["skills"]["Languages"] = "Python, SQL, golang"
        result = validate_json_fields(valid_json_data, profile, mode="normal", source="content-library")
        assert not result["passed"]
        assert any("Fabricated skill" in e for e in result["errors"])

    def test_content_library_mode_still_checks_banned_words(self, profile, valid_json_data):
        """In content-library mode, banned words are still checked (mode-dependent)."""
        valid_json_data["summary"] = "I am passionate about building scalable solutions."
        result = validate_json_fields(valid_json_data, profile, mode="normal", source="content-library")
        assert any("Banned words" in w for w in result["warnings"])

    def test_content_library_mode_still_checks_required_fields(self, profile):
        """In content-library mode, missing required fields still cause errors."""
        incomplete = {"title": "Engineer"}  # missing summary, skills, etc.
        result = validate_json_fields(incomplete, profile, mode="normal", source="content-library")
        assert not result["passed"]
        assert any("Missing required field" in e for e in result["errors"])

    def test_default_source_is_resume(self, profile, valid_json_data):
        """Default source should be 'resume' (backward compatible)."""
        valid_json_data["experience"] = [
            {"header": "Engineer at OtherCo", "subtitle": "2024-Present", "bullets": ["Did stuff"]}
        ]
        result = validate_json_fields(valid_json_data, profile, mode="normal")
        company_errors = [e for e in result["errors"] if "Company" in e and "missing" in e]
        assert len(company_errors) == 2  # Both AIR and Capgemini missing


# ── validate_tailored_resume: source parameter ────────────────────────────


class TestValidateTailoredResumeSource:
    def _make_resume_text(self, companies=None, projects=None, school="Carnegie Mellon University"):
        """Build a minimal resume text for testing."""
        lines = [
            "Siddharth Engineer",
            "Data Scientist",
            "test@example.com | 555-1234",
            "",
            "SUMMARY",
            "Experienced data scientist.",
            "",
            "TECHNICAL SKILLS",
            "Languages: Python, SQL",
            "",
            "EXPERIENCE",
        ]
        if companies:
            for company in companies:
                lines.append(f"Data Scientist at {company}")
                lines.append("2024-Present")
                lines.append("- Built cool stuff")
                lines.append("")
        lines.extend([
            "",
            "PROJECTS",
            "PatentsView Pipeline",
            "Python | 2025",
            "- Automated data extraction",
            "",
            "EDUCATION",
            school,
            "| B.S. in Computer Science",
        ])
        return "\n".join(lines)

    def test_resume_mode_enforces_preserved_companies(self, profile):
        """In resume mode, missing preserved companies must cause an error."""
        text = self._make_resume_text(companies=["OtherCo"])
        result = validate_tailored_resume(text, profile, source="resume")
        company_errors = [e for e in result["errors"] if "missing" in e and "Company" in e]
        assert len(company_errors) == 2  # Both AIR and Capgemini missing

    def test_content_library_mode_skips_preserved_companies(self, profile):
        """In content-library mode, missing preserved companies should NOT cause an error."""
        text = self._make_resume_text(companies=["OtherCo"])
        result = validate_tailored_resume(text, profile, source="content-library")
        company_errors = [e for e in result["errors"] if "missing" in e and "Company" in e]
        assert company_errors == []

    def test_content_library_mode_skips_preserved_projects(self, profile):
        """In content-library mode, missing preserved projects should NOT cause warnings."""
        text = self._make_resume_text()
        result = validate_tailored_resume(text, profile, source="content-library")
        project_warnings = [w for w in result["warnings"] if "Project" in w and "not found" in w]
        assert project_warnings == []

    def test_resume_mode_enforces_preserved_projects(self, profile):
        """In resume mode, missing preserved projects should cause warnings."""
        text = self._make_resume_text()
        # Remove PatentsView and CAFE from the text
        text = text.replace("PatentsView Pipeline", "Other Project")
        result = validate_tailored_resume(text, profile, source="resume")
        # Projects are warnings, not errors, so they won't block passing
        # But they should appear in warnings
        project_warnings = [w for w in result["warnings"] if "Project" in w and "not found" in w]
        assert len(project_warnings) >= 1

    def test_content_library_mode_still_checks_sections(self, profile):
        """In content-library mode, required section checks still apply."""
        text = "Just some random text without sections."
        result = validate_tailored_resume(text, profile, source="content-library")
        assert not result["passed"]
        assert any("Missing required section" in e for e in result["errors"])

    def test_content_library_mode_still_checks_fabrication(self, profile):
        """In content-library mode, fabrication watchlist still applies."""
        text = self._make_resume_text()
        # Add a fabricated skill (use golang which is length > 2)
        text = text.replace(
            "Languages: Python, SQL",
            "Languages: Python, SQL, golang",
        )
        result = validate_tailored_resume(text, profile, source="content-library")
        assert not result["passed"]
        assert any("FABRICATED SKILL" in e for e in result["errors"])

    def test_content_library_mode_still_checks_banned_words(self, profile):
        """In content-library mode, banned words are still checked."""
        text = self._make_resume_text()
        # Inject a banned word
        text = text.replace(
            "Experienced data scientist.",
            "Experienced and passionate data scientist.",
        )
        result = validate_tailored_resume(text, profile, source="content-library")
        assert any("Banned words" in e for e in result["errors"])

    def test_default_source_is_resume(self, profile):
        """Default source should be 'resume' (backward compatible)."""
        text = self._make_resume_text(companies=["OtherCo"])
        result = validate_tailored_resume(text, profile)
        company_errors = [e for e in result["errors"] if "missing" in e and "Company" in e]
        assert len(company_errors) == 2
