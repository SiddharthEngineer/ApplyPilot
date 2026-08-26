"""Tests for content library tailor function and judge prompt."""

import json
from unittest.mock import MagicMock, patch

import pytest

from applypilot.scoring.content_library import ContentLibrary, Project, RoleSection
from applypilot.scoring.tailor import (
    _build_content_library_judge_prompt,
    _build_content_library_tailor_prompt,
    assemble_resume_text,
    extract_json,
    tailor_from_content_library,
)


def _minimal_profile() -> dict:
    return {
        "personal": {
            "full_name": "Siddharth Engineer",
            "email": "test@example.com",
            "phone": "555-1234",
        },
        "skills_boundary": {
            "languages": ["Python", "R", "SQL"],
            "frameworks": ["Airflow", "Dagster", "Flask"],
            "devops_infra": ["Docker", "AWS", "Azure"],
        },
        "resume_facts": {
            "preserved_school": "University of Illinois Urbana-Champaign",
            "preserved_companies": ["AIR"],
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


def _valid_llm_response() -> str:
    """Return a valid JSON response that the LLM would produce."""
    return json.dumps({
        "title": "Data Engineer",
        "summary": "Data engineer with experience building production pipelines using Airflow and Dagster.",
        "skills": {
            "Languages": "Python, SQL, R",
            "Frameworks": "Airflow, Dagster, Flask",
            "DevOps & Infra": "Docker, AWS, Azure",
        },
        "experience": [
            {
                "header": "Data Science Associate at AIR",
                "subtitle": "Sep 2025-Present",
                "bullets": [
                    "Built PatentsView data pipeline with Airflow and Celery, enabling successful federal data release on schedule",
                    "Designed CAFE multi-container architecture using Dagster and Docker, processing multi-modal classroom feedback",
                ],
            },
        ],
        "projects": [
            {
                "header": "Project Talent OCR",
                "subtitle": "May 2024-Aug 2024",
                "bullets": [
                    "Processed thousands of scanned PDFs using OCR pipeline and JSON extraction, producing structured student outcome data",
                ],
            },
        ],
        "education": "University of Illinois Urbana-Champaign | B.S. Computer Science",
    })


def _job() -> dict:
    return {
        "title": "Data Engineer",
        "site": "TechCorp",
        "location": "Remote",
        "full_description": "Looking for a data engineer with Airflow and Python experience.",
        "url": "https://example.com/job/1",
    }


class TestBuildContentLibraryJudgePrompt:
    def test_returns_nonempty_string(self):
        prompt = _build_content_library_judge_prompt(_minimal_profile())
        assert isinstance(prompt, str)
        assert len(prompt) > 200

    def test_includes_skills_boundary(self):
        prompt = _build_content_library_judge_prompt(_minimal_profile())
        assert "Python" in prompt
        assert "Airflow" in prompt

    def test_mentions_degree_fabrication(self):
        prompt = _build_content_library_judge_prompt(_minimal_profile())
        assert "degrees" in prompt.lower()
        assert "don't exist" in prompt

    def test_mentions_content_library_context(self):
        prompt = _build_content_library_judge_prompt(_minimal_profile())
        assert "content library" in prompt.lower()
        assert "SELECTED" in prompt or "selected" in prompt.lower()

    def test_includes_fabrication_rules(self):
        prompt = _build_content_library_judge_prompt(_minimal_profile())
        assert "FABRICATION" in prompt
        assert "FAIL" in prompt

    def test_empty_profile(self):
        prompt = _build_content_library_judge_prompt({})
        assert isinstance(prompt, str)
        assert len(prompt) > 100


class TestTailorFromContentLibrary:
    @patch("applypilot.scoring.tailor.get_client")
    def test_successful_tailoring(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.chat.return_value = _valid_llm_response()
        mock_get_client.return_value = mock_client

        tailored, report = tailor_from_content_library(
            _minimal_library(), _job(), _minimal_profile(),
            validation_mode="lenient",
        )

        assert report["status"] == "approved"
        assert report["source"] == "content-library"
        assert report["attempts"] >= 1
        assert "Data Engineer" in tailored
        assert "AIR" in tailored

    @patch("applypilot.scoring.tailor.get_client")
    def test_retry_on_invalid_json(self, mock_get_client):
        mock_client = MagicMock()
        # First two calls return invalid JSON, third returns valid
        mock_client.chat.side_effect = [
            "Sorry, I made an error.",
            "Here is the corrected version: ",
            _valid_llm_response(),
        ]
        mock_get_client.return_value = mock_client

        tailored, report = tailor_from_content_library(
            _minimal_library(), _job(), _minimal_profile(),
            max_retries=2, validation_mode="lenient",
        )

        assert report["status"] == "approved"
        assert report["attempts"] == 3
        assert mock_client.chat.call_count == 3

    @patch("applypilot.scoring.tailor.get_client")
    def test_exhausted_retries(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.chat.return_value = "not valid json at all"
        mock_get_client.return_value = mock_client

        tailored, report = tailor_from_content_library(
            _minimal_library(), _job(), _minimal_profile(),
            max_retries=2, validation_mode="lenient",
        )

        assert report["status"] == "exhausted_retries"
        assert report["attempts"] == 3  # 0, 1, 2 = 3 attempts

    @patch("applypilot.scoring.tailor.get_client")
    def test_content_library_in_system_prompt(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.chat.return_value = _valid_llm_response()
        mock_get_client.return_value = mock_client

        tailor_from_content_library(
            _minimal_library(), _job(), _minimal_profile(),
            validation_mode="lenient",
        )

        # Verify the system prompt contains content library data
        call_args = mock_client.chat.call_args
        messages = call_args[0][0]
        system_msg = messages[0]["content"]
        assert "PatentsView Pipeline" in system_msg
        assert "CAFE Pipeline" in system_msg
        assert "Project Talent OCR" in system_msg
        assert "DEVOPS" in system_msg

    @patch("applypilot.scoring.tailor.get_client")
    def test_job_description_in_user_message(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.chat.return_value = _valid_llm_response()
        mock_get_client.return_value = mock_client

        tailor_from_content_library(
            _minimal_library(), _job(), _minimal_profile(),
            validation_mode="lenient",
        )

        call_args = mock_client.chat.call_args
        messages = call_args[0][0]
        user_msg = messages[1]["content"]
        assert "Data Engineer" in user_msg
        assert "TechCorp" in user_msg

    @patch("applypilot.scoring.tailor.get_client")
    def test_avoid_notes_on_retry(self, mock_get_client):
        mock_client = MagicMock()
        # First call: invalid JSON (attempt 0), second and third: valid (attempts 1,2)
        mock_client.chat.side_effect = [
            "not json",
            _valid_llm_response(),
            _valid_llm_response(),
        ]
        mock_get_client.return_value = mock_client

        tailor_from_content_library(
            _minimal_library(), _job(), _minimal_profile(),
            max_retries=2, validation_mode="lenient",
        )

        # Second call should have avoid notes
        second_call_messages = mock_client.chat.call_args_list[1][0][0]
        system_prompt = second_call_messages[0]["content"]
        assert "AVOID THESE ISSUES" in system_prompt

    @patch("applypilot.scoring.tailor.get_client")
    def test_report_contains_source(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.chat.return_value = _valid_llm_response()
        mock_get_client.return_value = mock_client

        _, report = tailor_from_content_library(
            _minimal_library(), _job(), _minimal_profile(),
            validation_mode="lenient",
        )

        assert report["source"] == "content-library"
        assert "validation_mode" in report


class TestContentLibraryJudge:
    @patch("applypilot.scoring.tailor.get_client")
    def test_judge_pass(self, mock_get_client):
        from applypilot.scoring.tailor import judge_content_library_resume

        mock_client = MagicMock()
        mock_client.chat.return_value = "VERDICT: PASS\nISSUES: none"
        mock_get_client.return_value = mock_client

        result = judge_content_library_resume("resume text", "Data Engineer", _minimal_profile())
        assert result["passed"] is True
        assert result["verdict"] == "PASS"

    @patch("applypilot.scoring.tailor.get_client")
    def test_judge_fail(self, mock_get_client):
        from applypilot.scoring.tailor import judge_content_library_resume

        mock_client = MagicMock()
        mock_client.chat.return_value = "VERDICT: FAIL\nISSUES: Fabricated skill 'C++'"
        mock_get_client.return_value = mock_client

        result = judge_content_library_resume("resume text", "Data Engineer", _minimal_profile())
        assert result["passed"] is False
        assert result["verdict"] == "FAIL"
        assert "C++" in result["issues"]

    @patch("applypilot.scoring.tailor.get_client")
    def test_judge_uses_correct_prompt(self, mock_get_client):
        from applypilot.scoring.tailor import judge_content_library_resume

        mock_client = MagicMock()
        mock_client.chat.return_value = "VERDICT: PASS\nISSUES: none"
        mock_get_client.return_value = mock_client

        judge_content_library_resume("resume text", "Data Engineer", _minimal_profile())

        call_args = mock_client.chat.call_args
        messages = call_args[0][0]
        system_msg = messages[0]["content"]
        assert "content library" in system_msg.lower()


class TestAssembleResumeTextWithContentLibraryOutput:
    """Verify assemble_resume_text works with content-library-style JSON output."""

    def test_experience_grouped_by_role(self):
        data = {
            "title": "Data Engineer",
            "summary": "Experienced data engineer.",
            "skills": {"Languages": "Python, SQL", "Frameworks": "Airflow"},
            "experience": [
                {
                    "header": "Data Science Associate at AIR",
                    "subtitle": "Sep 2025-Present",
                    "bullets": ["Built pipeline with Airflow."],
                },
            ],
            "projects": [],
            "education": "UIUC | B.S. CS",
        }
        text = assemble_resume_text(data, _minimal_profile())
        assert "EXPERIENCE" in text
        assert "Data Science Associate at AIR" in text
        assert "Sep 2025-Present" in text
        assert "- Built pipeline with Airflow." in text

    def test_multiple_roles(self):
        data = {
            "title": "Data Engineer",
            "summary": "Experienced data engineer.",
            "skills": {"Languages": "Python, SQL"},
            "experience": [
                {
                    "header": "Data Science Associate at AIR",
                    "subtitle": "Sep 2025-Present",
                    "bullets": ["Built PatentsView pipeline."],
                },
                {
                    "header": "Data Science Assistant at AIR",
                    "subtitle": "Jun 2023-Sep 2025",
                    "bullets": ["Processed student outcome data."],
                },
            ],
            "projects": [],
            "education": "UIUC | B.S. CS",
        }
        text = assemble_resume_text(data, _minimal_profile())
        assert "Data Science Associate at AIR" in text
        assert "Data Science Assistant at AIR" in text
        assert "- Built PatentsView pipeline." in text
        assert "- Processed student outcome data." in text

    def test_empty_experience(self):
        data = {
            "title": "Data Engineer",
            "summary": "Experienced data engineer.",
            "skills": {"Languages": "Python"},
            "experience": [],
            "projects": [],
            "education": "UIUC | B.S. CS",
        }
        text = assemble_resume_text(data, _minimal_profile())
        assert "EXPERIENCE" in text
        assert "EDUCATION" in text
