"""Tests for doctor command content library validation."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def valid_content_library(tmp_path: Path) -> Path:
    """Create a valid content library file."""
    lib = tmp_path / "content_library.md"
    lib.write_text(
        "# Content Library\n\n"
        "## CURRENT ROLE — Data Science Associate, AIR (Sep 2025–Present)\n\n"
        "### PatentsView Data Pipeline Lead (Nov 2025–present)\n\n"
        "- **Context:** Led migration of legacy pipeline\n"
        "- **Scope/Scale:** 1M+ records\n"
        "- **Tools & Actions:** Python, Docker, Airflow\n"
        "- **Outcome/Metrics:** 99.9% uptime\n"
        "- **Angles:** DEVOPS, PIPELINE\n\n"
        "### Another Project (Jan 2025–May 2025)\n\n"
        "- **Context:** Built data dashboard\n"
        "- **Scope/Scale:** 10k users\n"
        "- **Tools & Actions:** React, D3.js\n"
        "- **Outcome/Metrics:** 50% faster reporting\n"
        "- **Angles:** FRONTEND, DATA-VISUALIZATION\n\n"
        "## PRIOR ROLE — Software Engineer, Acme Corp (Jun 2023–Aug 2025)\n\n"
        "### Legacy Migration (Jun 2023–Dec 2023)\n\n"
        "- **Context:** Migrated monolith to microservices\n"
        "- **Scope/Scale:** 500k requests/day\n"
        "- **Tools & Actions:** Go, Kubernetes, Terraform\n"
        "- **Outcome/Metrics:** 99.95% uptime\n"
        "- **Angles:** DEVOPS, MICROSERVICES\n",
        encoding="utf-8",
    )
    return lib


@pytest.fixture
def malformed_content_library(tmp_path: Path) -> Path:
    """Create a malformed content library file."""
    lib = tmp_path / "bad_library.md"
    lib.write_text(
        "# Bad Library\n\n"
        "This is not a valid content library format.\n",
        encoding="utf-8",
    )
    return lib


class TestDoctorContentLibraryCheck:
    """Tests for content library check in doctor command."""

    def test_content_library_present_and_valid(self, valid_content_library: Path) -> None:
        """When content library exists and parses OK, doctor shows OK with stats."""
        from typer.testing import CliRunner

        from strelitzia.cli import app

        runner = CliRunner()
        with patch("strelitzia.config.CONTENT_LIBRARY_PATH", valid_content_library):
            result = runner.invoke(app, ["doctor"])

        assert result.exit_code == 0
        assert "content_library.md" in result.output
        assert "OK" in result.output
        assert "parsed:" in result.output
        assert "project" in result.output
        assert "angle tag" in result.output

    def test_content_library_missing(self, tmp_path: Path) -> None:
        """When content library is missing, doctor shows WARN."""
        from typer.testing import CliRunner

        from strelitzia.cli import app

        runner = CliRunner()
        missing_path = tmp_path / "nonexistent" / "content_library.md"
        with patch("strelitzia.config.CONTENT_LIBRARY_PATH", missing_path):
            result = runner.invoke(app, ["doctor"])

        assert result.exit_code == 0
        assert "content_library.md" in result.output
        assert "WARN" in result.output

    def test_content_library_malformed(self, malformed_content_library: Path) -> None:
        """When content library exists but has no role sections, parses but returns empty."""
        from typer.testing import CliRunner

        from strelitzia.cli import app

        runner = CliRunner()
        with patch("strelitzia.config.CONTENT_LIBRARY_PATH", malformed_content_library):
            result = runner.invoke(app, ["doctor"])

        assert result.exit_code == 0
        assert "content_library.md" in result.output
        assert "OK" in result.output
        assert "parsed:" in result.output

    def test_content_library_parse_error(self, tmp_path: Path) -> None:
        """When content library file exists but parse raises, doctor shows ERROR."""
        from typer.testing import CliRunner

        from strelitzia.cli import app

        bad_file = tmp_path / "content_library.md"
        bad_file.write_text("data", encoding="utf-8")

        runner = CliRunner()
        with (
            patch("strelitzia.config.CONTENT_LIBRARY_PATH", bad_file),
            patch("strelitzia.scoring.content_library.parse_content_library", side_effect=RuntimeError("bad file")),
        ):
            result = runner.invoke(app, ["doctor"])

        assert result.exit_code == 0
        assert "content_library.md" in result.output
        assert "ERROR" in result.output
        assert "bad file" in result.output

    def test_tier_summary_shows_content_library_hint(self, valid_content_library: Path) -> None:
        """When content library is present, tier summary shows usage hint."""
        from typer.testing import CliRunner

        from strelitzia.cli import app

        runner = CliRunner()
        with patch("strelitzia.config.CONTENT_LIBRARY_PATH", valid_content_library):
            result = runner.invoke(app, ["doctor"])

        assert result.exit_code == 0
        assert "Content library mode is the default" in result.output
        assert "--source content-library" in result.output
        assert "strelitzia template init" in result.output

    def test_tier_summary_no_hint_when_missing(self, tmp_path: Path) -> None:
        """When content library is missing, no usage hint in tier summary."""
        from typer.testing import CliRunner

        from strelitzia.cli import app

        runner = CliRunner()
        missing_path = tmp_path / "nonexistent" / "content_library.md"
        with patch("strelitzia.config.CONTENT_LIBRARY_PATH", missing_path):
            result = runner.invoke(app, ["doctor"])

        assert result.exit_code == 0
        assert "Content library mode is the default" not in result.output


class TestDoctorClaude:
    """The Claude block in doctor: key presence (never the value) and the model and effort per stage."""

    def _invoke(self, tmp_path, monkeypatch, env):
        from typer.testing import CliRunner

        from strelitzia.cli import app

        for key in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "LLM_MONTHLY_BUDGET_USD", *(f"LLM_{p}_{k}" for p in
                    ("DISCOVERY", "EXTRACT", "SCORING", "TAILOR", "COVER") for k in ("MODEL", "EFFORT"))):
            monkeypatch.delenv(key, raising=False)
        for key, val in env.items():
            monkeypatch.setenv(key, val)
        db = tmp_path / "doctor.db"
        runner = CliRunner()
        with patch("strelitzia.config.CONTENT_LIBRARY_PATH", tmp_path / "missing" / "content_library.md"), \
                patch("strelitzia.config.DB_PATH", db), patch("strelitzia.database.DB_PATH", db), \
                patch("strelitzia.config.load_env", lambda: None):
            return runner.invoke(app, ["doctor"])

    def test_key_present_and_models_per_stage(self, tmp_path, monkeypatch) -> None:
        result = self._invoke(tmp_path, monkeypatch, {"ANTHROPIC_API_KEY": "sk-ant-secret-value",
                                                      "LLM_SCORING_EFFORT": "medium"})
        assert result.exit_code == 0
        assert "ANTHROPIC_API_KEY is set" in result.output
        assert "sk-ant-secret-value" not in result.output
        lines = {line.split()[0]: " ".join(line.split()[3:]) for line in result.output.splitlines()
                 if " model " in line and line.split()[0] in ("Discovery", "Extract", "Scoring", "Tailor", "Cover")}
        assert lines == {"Discovery": "claude-haiku-5-5 (effort low)", "Extract": "claude-haiku-5-5 (effort low)",
                         "Scoring": "claude-haiku-5-5 (effort medium)", "Tailor": "claude-sonnet-5-5 (effort medium)",
                         "Cover": "claude-sonnet-5-5 (effort medium)"}

    def test_missing_key_and_no_old_provider_lines(self, tmp_path, monkeypatch) -> None:
        result = self._invoke(tmp_path, monkeypatch, {"GEMINI_API_KEY": "g", "LLM_TAILOR_MODEL": "gemini-3.6-flash"})
        assert result.exit_code == 0
        assert "Set ANTHROPIC_API_KEY" in result.output
        assert "gemini" not in result.output.lower()

    def test_spend_against_budget_and_open_batches(self, tmp_path, monkeypatch) -> None:
        from strelitzia.database import close_connection, init_db
        from strelitzia.llm import record_usage

        db = tmp_path / "doctor.db"
        conn = init_db(db)
        record_usage("tailor", "claude-sonnet-5-5", {"input_tokens": 1_000_000}, conn=conn)  # $2.00
        conn.execute("INSERT INTO llm_batches (id, purpose, submitted_at, status, request_count, estimate_usd) "
                     "VALUES ('msgbatch_1', 'extract', '2026-10-13T01:00:00+00:00', 'submitted', 40, 0.5)")
        conn.commit()
        close_connection(db)
        result = self._invoke(tmp_path, monkeypatch, {"ANTHROPIC_API_KEY": "k", "LLM_MONTHLY_BUDGET_USD": "20"})
        assert result.exit_code == 0, result.output
        out = " ".join(result.output.split())  # rich wraps long lines
        assert "$2.00 + $0.50 estimated in open batches of $20.00 this month" in out
        assert "extract msgbatch_1 (40 requests" in out

    def test_no_database_yet(self, tmp_path, monkeypatch) -> None:
        result = self._invoke(tmp_path, monkeypatch, {"ANTHROPIC_API_KEY": "k"})
        assert result.exit_code == 0, result.output
        assert "$0.00 this month, no budget" in result.output
        assert "Open batches" in result.output
