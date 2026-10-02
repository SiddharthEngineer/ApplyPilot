"""Integration tests for content-library-based tailoring end-to-end flow."""

import json
from pathlib import Path
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
        "roles": [
            {
                "role_key": "data-science-associate-air",
                "bullets": [
                    {"text": "Built PatentsView data pipeline with Airflow and Celery for a federal data release",
                     "project_ids": ["patentsview-pipeline"]},
                    {"text": "Released the PatentsView data on schedule using RabbitMQ task distribution",
                     "project_ids": ["patentsview-pipeline"]},
                ],
            },
        ],
        "skills": [
            {"category": "Languages", "items": "Python, SQL"},
            {"category": "DevOps & Infra", "items": "Docker, AWS"},
        ],
        "dropped_roles": [],
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


def _fake_render_pdf(overflows: int = 0):
    """Stub for pdf.render_pdf: writes a placeholder PDF; reports overflow for the first N renders."""
    calls: list[str] = []

    def render(html, out):
        calls.append(html)
        Path(out).write_bytes(b"%PDF-1.4 stub")
        over = len(calls) <= overflows
        return {"overflow": over, "content_height_pt": 800.0 if over else 700.0,
                "usable_height_pt": 741.6, "pages": 2 if over else 1}

    render.calls = calls
    return render


def _run_tailoring_with_tmp(tmp_path, validation_mode="lenient", jobs=None, render=None):
    """Helper that patches all dependencies and runs tailoring."""
    from applypilot.scoring.tailor import run_tailoring

    with (
        patch("applypilot.scoring.pdf.render_pdf", new=render or _fake_render_pdf()),
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
            patch("applypilot.scoring.pdf.render_pdf", new=_fake_render_pdf()),
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


class TestTemplateOutputs:
    """resume-template-tailoring Task 6: PDF/JSON/TXT outputs and the one-page fit loop."""

    def test_writes_pdf_json_txt(self, tmp_path):
        _run_tailoring_with_tmp(tmp_path)
        stem = "TechCorp_Data_Engineer"
        assert (tmp_path / f"{stem}.pdf").exists()
        sidecar = json.loads((tmp_path / f"{stem}.json").read_text())
        assert sidecar["job_url"] == "https://example.com/job/1"
        assert sidecar["roles"][0]["bullets"][0]["project_ids"] == ["patentsview-pipeline"]
        txt = (tmp_path / f"{stem}.txt").read_text()
        assert "WORK EXPERIENCE" in txt and "• Built PatentsView data pipeline" in txt

    def test_renders_through_template(self, tmp_path):
        render = _fake_render_pdf()
        _run_tailoring_with_tmp(tmp_path, render=render)
        assert len(render.calls) == 1
        assert "Built PatentsView data pipeline" in render.calls[0]
        assert 'id="resume-experience"' in render.calls[0]

    def test_db_stores_pdf_path(self, tmp_path):
        from applypilot.scoring.tailor import run_tailoring

        conn = MagicMock()
        with (
            patch("applypilot.scoring.pdf.render_pdf", new=_fake_render_pdf()),
            patch("applypilot.scoring.tailor.load_profile", return_value=_minimal_profile()),
            patch("applypilot.scoring.tailor.TAILORED_DIR", new=MagicMock()) as mock_dir,
            patch("applypilot.scoring.tailor.CONTENT_LIBRARY_PATH") as mock_cl,
            patch("applypilot.scoring.tailor.get_connection", return_value=conn),
            patch("applypilot.scoring.tailor.get_jobs_by_stage", return_value=[_test_job()]),
            patch("applypilot.scoring.tailor.parse_content_library", return_value=_minimal_library()),
            patch("applypilot.scoring.tailor.get_client") as mock_client,
        ):
            mock_cl.exists.return_value = True
            mock_client.return_value.chat.return_value = _valid_llm_response()
            mock_dir.__truediv__ = lambda self, x: tmp_path / x
            mock_dir.mkdir = tmp_path.mkdir
            run_tailoring(source="content-library", validation_mode="lenient")

        update = next(c for c in conn.execute.call_args_list if "tailored_resume_path=?" in c[0][0])
        assert update[0][1][0] == str(tmp_path / "TechCorp_Data_Engineer.pdf")

    def test_fit_loop_drops_bullets_until_one_page(self, tmp_path):
        # 2 bullets in a role whose minimum (no base resume) is 2 -> nothing droppable -> overflow status
        render = _fake_render_pdf(overflows=99)
        result = _run_tailoring_with_tmp(tmp_path, render=render)
        report = json.loads(next(tmp_path.glob("*_REPORT.json")).read_text())
        assert report["status"] == "overflow"
        assert report["page_overflow"] is True
        assert result["approved"] == 0 and result["failed"] == 1
        assert len(render.calls) == 1  # nothing above minimum, so no re-render

    def test_pdf_error_not_approved(self, tmp_path):
        def boom(html, out):
            raise RuntimeError("chromium missing")

        result = _run_tailoring_with_tmp(tmp_path, render=boom)
        report = json.loads(next(tmp_path.glob("*_REPORT.json")).read_text())
        assert report["status"] == "pdf_error"
        assert result["approved"] == 0
        assert (tmp_path / "TechCorp_Data_Engineer.json").exists()


class TestRunLimit:
    def test_tailor_stage_passes_limit(self):
        from applypilot import pipeline

        with patch("applypilot.scoring.tailor.run_tailoring", return_value={"approved": 0}) as rt:
            pipeline._run_tailor(min_score=7, source="content-library", limit=3)
            pipeline._run_tailor(min_score=7)
        assert rt.call_args_list[0].kwargs["limit"] == 3
        assert "limit" not in rt.call_args_list[1].kwargs

    def test_run_help_lists_limit(self):
        from typer.testing import CliRunner

        from applypilot.cli import app

        out = CliRunner().invoke(app, ["run", "--help"], env={"COLUMNS": "200"}).output
        assert "--limit" in out


class TestDefaultSource:
    """resume-template-tailoring Task 7: --source defaults to content-library when the library exists."""

    def _invoke(self, library_exists: bool):
        from typer.testing import CliRunner

        from applypilot.cli import app

        lib = MagicMock()
        lib.exists.return_value = library_exists
        with (
            patch("applypilot.config.CONTENT_LIBRARY_PATH", lib),
            patch("applypilot.config.check_tier"),
            patch("applypilot.pipeline.run_pipeline", return_value={}) as rp,
        ):
            result = CliRunner().invoke(app, ["run", "tailor"])
        assert result.exit_code == 0, result.output
        return rp.call_args.kwargs["source"]

    def test_default_content_library_when_present(self):
        assert self._invoke(True) == "content-library"

    def test_default_resume_when_missing(self):
        assert self._invoke(False) == "resume"

    def test_help_shows_default(self):
        from typer.testing import CliRunner

        from applypilot.cli import app

        out = CliRunner().invoke(app, ["run", "--help"], env={"COLUMNS": "250"}).output
        assert "default when content_library.md exists" in " ".join(out.split())


class TestCoverLetterEvidence:
    """resume-template-tailoring Task 7: cover letters cite the tailored resume's projects."""

    def _sidecar(self, tmp_path) -> dict:
        lib_path = tmp_path / "content_library.md"
        lib_path.write_text(
            "## CURRENT ROLE — Data Science Associate, AIR (Sep 2025–Present)\n\n"
            "### PatentsView Pipeline (Nov 2025–present)\n\n"
            "- **Context:** Federal patent database.\n"
            "- **Outcome/Metrics:** Released 9.1M records on schedule.\n\n"
            "### Unused Project (2024)\n\n- **Context:** Should not appear.\n",
            encoding="utf-8",
        )
        (tmp_path / "Job.json").write_text(json.dumps({
            "roles": [{"role_key": "data-science-associate-air", "title": "Data Science Associate",
                       "company": "AIR", "dates": "Sep 2025 – present", "tagline": None,
                       "bullets": [{"text": "Released 9.1M patent records.", "project_ids": ["patentsview-pipeline"]}]}],
            "skills": [], "dropped_roles": [], "job_url": "https://x",
        }), encoding="utf-8")
        return {"tailored_resume_path": str(tmp_path / "Job.pdf"), "lib": lib_path}

    def test_evidence_from_sidecar(self, tmp_path):
        from applypilot.scoring.cover_letter import tailored_evidence

        job = self._sidecar(tmp_path)
        ev = tailored_evidence(job, library_path=job["lib"])
        assert "Data Science Associate at AIR (Sep 2025 – present)" in ev
        assert "- Released 9.1M patent records." in ev
        assert "PatentsView Pipeline" in ev and "Released 9.1M records on schedule." in ev
        assert "Unused Project" not in ev

    def test_no_sidecar_returns_none(self, tmp_path):
        from applypilot.scoring.cover_letter import tailored_evidence

        assert tailored_evidence({"tailored_resume_path": str(tmp_path / "Legacy.txt")}) is None
        assert tailored_evidence({}) is None

    def test_generate_uses_evidence_instead_of_resume(self, tmp_path):
        from applypilot.scoring import cover_letter

        client = MagicMock()
        client.chat.return_value = "Dear Hiring Manager,\n\nI released 9.1M records.\n\nAlex"
        with patch.object(cover_letter, "get_client", return_value=client):
            cover_letter.generate_cover_letter("BASE RESUME TEXT", _test_job(), _minimal_profile(),
                                               validation_mode="lenient", evidence="SELECTED BULLETS: x")
        system, user = (m["content"] for m in client.chat.call_args[0][0])
        assert "CANDIDATE EVIDENCE:\nSELECTED BULLETS: x" in user
        assert "BASE RESUME TEXT" not in user
        assert "plus any tool named in the CANDIDATE EVIDENCE" in system
