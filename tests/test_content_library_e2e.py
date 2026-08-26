"""Integration tests for content-library-based tailoring end-to-end flow."""

import json
from unittest.mock import MagicMock, patch

import pytest

from applypilot.scoring.content_library import ContentLibrary, Project, RoleSection


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
                ],
            ),
        ],
        all_angles={"DEVOPS", "PIPELINE"},
    )


def _valid_llm_response() -> str:
    return json.dumps({
        "title": "Data Engineer",
        "summary": "Data engineer with production pipeline experience.",
        "skills": {
            "Languages": "Python, SQL",
            "Frameworks": "Airflow, Dagster",
            "DevOps & Infra": "Docker, AWS",
        },
        "experience": [
            {
                "header": "Data Science Associate at AIR",
                "subtitle": "Sep 2025-Present",
                "bullets": [
                    "Built PatentsView data pipeline with Airflow and Celery, enabling successful federal data release on schedule",
                ],
            },
        ],
        "projects": [
            {
                "header": "PatentsView Pipeline",
                "subtitle": "Nov 2025-present",
                "bullets": [
                    "Built data pipeline using Airflow and Celery, enabling successful federal patent data release on schedule",
                ],
            },
        ],
        "education": "University of Illinois Urbana-Champaign | B.S. Computer Science",
    })


def _test_job() -> dict:
    return {
        "title": "Data Engineer",
        "site": "TechCorp",
        "location": "Remote",
        "full_description": "Looking for a data engineer with Airflow and Python.",
        "url": "https://example.com/job/1",
        "fit_score": 9,
    }


def _run_tailoring_with_tmp(tmp_path, validation_mode="lenient", jobs=None):
    """Helper that patches all dependencies and runs tailoring."""
    from applypilot.scoring.tailor import run_tailoring

    with (
        patch("applypilot.scoring.tailor.load_profile", return_value=_minimal_profile()),
        patch("applypilot.scoring.tailor.TAILORED_DIR", new=MagicMock()) as mock_dir,
        patch("applypilot.scoring.tailor.CONTENT_LIBRARY_PATH") as mock_cl,
        patch("applypilot.scoring.tailor.get_connection") as mock_conn,
        patch("applypilot.scoring.tailor.get_jobs_by_stage") as mock_jobs,
        patch("applypilot.scoring.tailor.parse_content_library", return_value=_minimal_library()),
        patch("applypilot.scoring.tailor.get_client") as mock_client,
    ):
        mock_cl.exists.return_value = True
        mock_client.return_value.chat.return_value = _valid_llm_response()
        mock_conn.return_value = MagicMock()
        mock_jobs.return_value = jobs or [_test_job()]

        mock_dir.__truediv__ = lambda self, x: tmp_path / x
        mock_dir.mkdir = tmp_path.mkdir

        return run_tailoring(source="content-library", validation_mode=validation_mode)


class TestRunTailoringContentLibrary:
    """Integration tests for run_tailoring(source='content-library')."""

    def test_processes_job_and_approves(self, tmp_path):
        result = _run_tailoring_with_tmp(tmp_path)
        assert result["approved"] == 1
        assert result["failed"] == 0
        assert result["errors"] == 0

    def test_saves_txt_and_report_files(self, tmp_path):
        _run_tailoring_with_tmp(tmp_path)

        txt_files = list(tmp_path.glob("*.txt"))
        assert len(txt_files) >= 2

        report_files = list(tmp_path.glob("*_REPORT.json"))
        assert len(report_files) == 1
        report = json.loads(report_files[0].read_text())
        assert report["source"] == "content-library"
        assert report["status"] == "approved"

    def test_no_jobs_returns_zero(self, tmp_path):
        from applypilot.scoring.tailor import run_tailoring

        with (
            patch("applypilot.scoring.tailor.load_profile", return_value=_minimal_profile()),
            patch("applypilot.scoring.tailor.TAILORED_DIR"),
            patch("applypilot.scoring.tailor.CONTENT_LIBRARY_PATH") as mock_cl,
            patch("applypilot.scoring.tailor.get_connection"),
            patch("applypilot.scoring.tailor.get_jobs_by_stage", return_value=[]),
            patch("applypilot.scoring.tailor.parse_content_library"),
        ):
            mock_cl.exists.return_value = True
            result = run_tailoring(source="content-library")

        assert result["approved"] == 0
        assert result["failed"] == 0
        assert result["errors"] == 0
        assert result["elapsed"] == 0.0

    def test_content_library_not_found(self):
        from applypilot.scoring.tailor import run_tailoring

        with (
            patch("applypilot.scoring.tailor.load_profile", return_value=_minimal_profile()),
            patch("applypilot.scoring.tailor.TAILORED_DIR"),
            patch("applypilot.scoring.tailor.CONTENT_LIBRARY_PATH") as mock_cl,
            patch("applypilot.scoring.tailor.get_jobs_by_stage") as mock_jobs,
        ):
            mock_cl.exists.return_value = False
            result = run_tailoring(source="content-library")

        assert result["errors"] == 1
        mock_jobs.assert_not_called()

    def test_resume_source_not_affected(self):
        from applypilot.scoring.tailor import run_tailoring

        with (
            patch("applypilot.scoring.tailor.load_profile", return_value=_minimal_profile()),
            patch("applypilot.scoring.tailor.RESUME_PATH") as mock_resume,
            patch("applypilot.scoring.tailor.get_connection"),
            patch("applypilot.scoring.tailor.get_jobs_by_stage", return_value=[]),
            patch("applypilot.scoring.tailor.parse_content_library") as mock_parse,
        ):
            mock_resume.exists.return_value = True
            mock_resume.read_text.return_value = "resume content"
            result = run_tailoring(source="resume")

        mock_parse.assert_not_called()
        assert result["approved"] == 0

    def test_multiple_jobs(self, tmp_path):
        jobs = [
            _test_job(),
            {
                "title": "ML Engineer",
                "site": "AICorp",
                "location": "NYC",
                "full_description": "ML engineer with Python.",
                "url": "https://example.com/job/2",
                "fit_score": 8,
            },
        ]

        result = _run_tailoring_with_tmp(tmp_path, jobs=jobs)

        assert result["approved"] == 2

    def test_db_updated_for_approved_jobs(self, tmp_path):
        from applypilot.scoring.tailor import run_tailoring

        with (
            patch("applypilot.scoring.tailor.load_profile", return_value=_minimal_profile()),
            patch("applypilot.scoring.tailor.TAILORED_DIR", new=MagicMock()) as mock_dir,
            patch("applypilot.scoring.tailor.CONTENT_LIBRARY_PATH") as mock_cl,
            patch("applypilot.scoring.tailor.get_connection") as mock_conn,
            patch("applypilot.scoring.tailor.get_jobs_by_stage", return_value=[_test_job()]),
            patch("applypilot.scoring.tailor.parse_content_library", return_value=_minimal_library()),
            patch("applypilot.scoring.tailor.get_client") as mock_client,
        ):
            mock_cl.exists.return_value = True
            mock_client.return_value.chat.return_value = _valid_llm_response()
            mock_dir.__truediv__ = lambda self, x: tmp_path / x
            mock_dir.mkdir = tmp_path.mkdir
            conn = MagicMock()
            mock_conn.return_value = conn

            result = run_tailoring(source="content-library", validation_mode="lenient")

        assert result["approved"] == 1
        assert conn.execute.call_count >= 1
        conn.commit.assert_called_once()
