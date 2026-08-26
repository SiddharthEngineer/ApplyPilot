"""Tests for the content library tailor prompt builder."""

from pathlib import Path

import pytest

from applypilot.scoring.content_library import ContentLibrary, Project, RoleSection, parse_content_library
from applypilot.scoring.tailor import _build_content_library_tailor_prompt

REAL_LIBRARY = Path(__file__).resolve().parent.parent / "personal" / "content_library.md"


def _minimal_profile() -> dict:
    return {
        "skills_boundary": {
            "languages": ["Python", "R", "SQL"],
            "frameworks": ["Airflow", "Dagster", "Flask"],
            "devops_infra": ["Docker", "AWS", "Azure"],
        },
        "resume_facts": {
            "preserved_school": "University of Illinois Urbana-Champaign",
        },
        "experience": {
            "education_level": "B.S. Computer Science",
        },
    }


def _minimal_library() -> ContentLibrary:
    return ContentLibrary(
        roles=[
            RoleSection(
                title="Data Science Associate, AIR",
                dates="Sep 2025-Present",
                projects=[
                    Project(
                        name="PatentsView Pipeline",
                        role_header="## CURRENT ROLE",
                        dates="Nov 2025-present",
                        context="PatentsView is a federal patent database.",
                        scope_scale="Full data release.",
                        tools_actions="Airflow, Celery, RabbitMQ.",
                        outcome_metrics="Successful release on schedule.",
                        angles=["DEVOPS", "PIPELINE"],
                    ),
                    Project(
                        name="CAFE Pipeline",
                        role_header="## CURRENT ROLE",
                        dates="Aug 2025-Jan 2026",
                        context="Automated classroom feedback pipeline.",
                        scope_scale="Multi-container architecture.",
                        tools_actions="Dagster, Docker, AKS.",
                        outcome_metrics="Production pipeline handling multi-modal processing.",
                        angles=["PIPELINE", "CS/SWE"],
                    ),
                ],
            ),
            RoleSection(
                title="Data Science Assistant, AIR",
                dates="Jun 2023-Sep 2025",
                projects=[
                    Project(
                        name="Project Talent OCR",
                        role_header="## PRIOR ROLE",
                        dates="May 2024-Aug 2024",
                        context="PDF student outcome files need conversion.",
                        scope_scale="Thousands of files.",
                        tools_actions="OCR pipeline, JSON extraction.",
                        outcome_metrics="Structured data from scanned PDFs.",
                        angles=["PIPELINE", "CS/SWE"],
                    ),
                ],
            ),
        ],
        all_angles={"DEVOPS", "PIPELINE", "CS/SWE"},
    )


class TestBuildContentLibraryTailorPrompt:
    def test_returns_nonempty_string(self):
        prompt = _build_content_library_tailor_prompt(_minimal_profile(), _minimal_library())
        assert isinstance(prompt, str)
        assert len(prompt) > 200

    def test_includes_all_project_names(self):
        lib = _minimal_library()
        prompt = _build_content_library_tailor_prompt(_minimal_profile(), lib)
        for role in lib.roles:
            for proj in role.projects:
                assert proj.name in prompt

    def test_includes_role_titles(self):
        lib = _minimal_library()
        prompt = _build_content_library_tailor_prompt(_minimal_profile(), lib)
        assert "Data Science Associate, AIR" in prompt
        assert "Data Science Assistant, AIR" in prompt

    def test_includes_angle_tags(self):
        lib = _minimal_library()
        prompt = _build_content_library_tailor_prompt(_minimal_profile(), lib)
        assert "DEVOPS" in prompt
        assert "PIPELINE" in prompt
        assert "CS/SWE" in prompt

    def test_includes_skills_boundary(self):
        prompt = _build_content_library_tailor_prompt(_minimal_profile(), _minimal_library())
        assert "Python" in prompt
        assert "Airflow" in prompt
        assert "Docker" in prompt

    def test_includes_banned_words(self):
        from applypilot.scoring.validator import BANNED_WORDS
        prompt = _build_content_library_tailor_prompt(_minimal_profile(), _minimal_library())
        for word in BANNED_WORDS[:5]:
            assert word in prompt

    def test_includes_school(self):
        prompt = _build_content_library_tailor_prompt(_minimal_profile(), _minimal_library())
        assert "University of Illinois Urbana-Champaign" in prompt

    def test_includes_json_schema(self):
        prompt = _build_content_library_tailor_prompt(_minimal_profile(), _minimal_library())
        assert '"title"' in prompt
        assert '"summary"' in prompt
        assert '"skills"' in prompt
        assert '"experience"' in prompt
        assert '"projects"' in prompt
        assert '"education"' in prompt

    def test_includes_selection_process_instructions(self):
        prompt = _build_content_library_tailor_prompt(_minimal_profile(), _minimal_library())
        assert "SELECTION PROCESS" in prompt
        assert "5-7 projects" in prompt
        assert "Angle tags" in prompt

    def test_includes_fact_traceability_rule(self):
        prompt = _build_content_library_tailor_prompt(_minimal_profile(), _minimal_library())
        assert "MUST trace to a fact" in prompt

    def test_includes_project_raw_facts(self):
        prompt = _build_content_library_tailor_prompt(_minimal_profile(), _minimal_library())
        assert "Airflow, Celery, RabbitMQ" in prompt
        assert "Dagster, Docker, AKS" in prompt
        assert "OCR pipeline" in prompt

    def test_empty_profile(self):
        prompt = _build_content_library_tailor_prompt({}, _minimal_library())
        assert isinstance(prompt, str)
        assert len(prompt) > 200

    def test_empty_library(self):
        prompt = _build_content_library_tailor_prompt(_minimal_profile(), ContentLibrary())
        assert isinstance(prompt, str)
        assert "SELECTION PROCESS" in prompt


@pytest.mark.skipif(not REAL_LIBRARY.exists(), reason="content_library.md not found")
class TestBuildContentLibraryTailorPromptReal:
    def test_real_library_all_projects_in_prompt(self):
        lib = parse_content_library(REAL_LIBRARY)
        prompt = _build_content_library_tailor_prompt(_minimal_profile(), lib)
        for role in lib.roles:
            for proj in role.projects:
                assert proj.name in prompt, f"Project '{proj.name}' missing from prompt"

    def test_real_library_all_angles_in_prompt(self):
        lib = parse_content_library(REAL_LIBRARY)
        prompt = _build_content_library_tailor_prompt(_minimal_profile(), lib)
        for angle in lib.all_angles:
            assert angle in prompt, f"Angle '{angle}' missing from prompt"

    def test_real_library_role_count(self):
        lib = parse_content_library(REAL_LIBRARY)
        prompt = _build_content_library_tailor_prompt(_minimal_profile(), lib)
        assert len(lib.roles) == 4
        assert len(prompt) > 2000  # should be substantial with 19 projects
