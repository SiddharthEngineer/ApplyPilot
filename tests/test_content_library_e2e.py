"""Content-library flows that outlive the API tailor: cover-letter evidence and the run command's options."""

import json
import re


def _plain(text: str) -> str:
    """Strip ANSI styling (rich colors help output when CI forces a terminal)."""
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


class TestRunLimit:
    def test_run_help_lists_limit(self):
        from typer.testing import CliRunner

        from strelitzia.cli import app

        out = _plain(CliRunner().invoke(app, ["run", "--help"], env={"COLUMNS": "200"}).output)
        assert "--limit" in out
        assert "--validation" not in out and "--source" not in out  # the API tailor's options are gone


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
        from strelitzia.scoring.cover_letter import tailored_evidence

        job = self._sidecar(tmp_path)
        ev = tailored_evidence(job, library_path=job["lib"])
        assert "Data Science Associate at AIR (Sep 2025 – present)" in ev
        assert "- Released 9.1M patent records." in ev
        assert "PatentsView Pipeline" in ev and "Released 9.1M records on schedule." in ev
        assert "Unused Project" not in ev

    def test_no_sidecar_returns_none(self, tmp_path):
        from strelitzia.scoring.cover_letter import tailored_evidence

        assert tailored_evidence({"tailored_resume_path": str(tmp_path / "Legacy.txt")}) is None
        assert tailored_evidence({}) is None
