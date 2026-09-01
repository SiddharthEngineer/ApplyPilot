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

        from applypilot.cli import app

        runner = CliRunner()
        with patch("applypilot.config.CONTENT_LIBRARY_PATH", valid_content_library):
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

        from applypilot.cli import app

        runner = CliRunner()
        missing_path = tmp_path / "nonexistent" / "content_library.md"
        with patch("applypilot.config.CONTENT_LIBRARY_PATH", missing_path):
            result = runner.invoke(app, ["doctor"])

        assert result.exit_code == 0
        assert "content_library.md" in result.output
        assert "WARN" in result.output

    def test_content_library_malformed(self, malformed_content_library: Path) -> None:
        """When content library exists but has no role sections, parses but returns empty."""
        from typer.testing import CliRunner

        from applypilot.cli import app

        runner = CliRunner()
        with patch("applypilot.config.CONTENT_LIBRARY_PATH", malformed_content_library):
            result = runner.invoke(app, ["doctor"])

        assert result.exit_code == 0
        assert "content_library.md" in result.output
        assert "OK" in result.output
        assert "parsed:" in result.output

    def test_content_library_parse_error(self, tmp_path: Path) -> None:
        """When content library file exists but parse raises, doctor shows ERROR."""
        from typer.testing import CliRunner

        from applypilot.cli import app

        bad_file = tmp_path / "content_library.md"
        bad_file.write_text("data", encoding="utf-8")

        runner = CliRunner()
        with (
            patch("applypilot.config.CONTENT_LIBRARY_PATH", bad_file),
            patch("applypilot.scoring.content_library.parse_content_library", side_effect=RuntimeError("bad file")),
        ):
            result = runner.invoke(app, ["doctor"])

        assert result.exit_code == 0
        assert "content_library.md" in result.output
        assert "ERROR" in result.output
        assert "bad file" in result.output

    def test_tier_summary_shows_content_library_hint(self, valid_content_library: Path) -> None:
        """When content library is present, tier summary shows usage hint."""
        from typer.testing import CliRunner

        from applypilot.cli import app

        runner = CliRunner()
        with patch("applypilot.config.CONTENT_LIBRARY_PATH", valid_content_library):
            result = runner.invoke(app, ["doctor"])

        assert result.exit_code == 0
        assert "Content library mode available" in result.output
        assert "--source content-library" in result.output

    def test_tier_summary_no_hint_when_missing(self, tmp_path: Path) -> None:
        """When content library is missing, no usage hint in tier summary."""
        from typer.testing import CliRunner

        from applypilot.cli import app

        runner = CliRunner()
        missing_path = tmp_path / "nonexistent" / "content_library.md"
        with patch("applypilot.config.CONTENT_LIBRARY_PATH", missing_path):
            result = runner.invoke(app, ["doctor"])

        assert result.exit_code == 0
        assert "Content library mode available" not in result.output


class TestDoctorRateLimitTuning:
    """Tests for discovery model + RPM limit reporting in doctor."""

    def _invoke(self, tmp_path, monkeypatch, env, models):
        from typer.testing import CliRunner

        from applypilot.cli import app

        for key in ("GEMINI_API_KEY", "OPENAI_API_KEY", "OPENCODE_API_KEY", "LLM_URL",
                    "LLM_MODEL", "LLM_DISCOVERY_MODEL", "LLM_RPM_LIMIT", "LLM_RPM_WINDOW"):
            monkeypatch.delenv(key, raising=False)
        for key, val in env.items():
            monkeypatch.setenv(key, val)

        class _Resp:
            status_code = 200

            def json(self):
                return {"models": [{"name": f"models/{m}"} for m in models]}

        runner = CliRunner()
        with patch("applypilot.config.CONTENT_LIBRARY_PATH", tmp_path / "missing" / "content_library.md"):
                with patch("httpx.get", return_value=_Resp()):
                    with patch("applypilot.config.load_env", lambda: None):
                        return runner.invoke(app, ["doctor"])

    def test_discovery_model_and_rpm_lines_present(self, tmp_path, monkeypatch) -> None:
        """doctor prints Discovery model: and RPM limit: lines for Gemini."""
        result = self._invoke(
            tmp_path, monkeypatch,
            {"GEMINI_API_KEY": "k", "LLM_DISCOVERY_MODEL": "gemini-2.5-flash-lite", "LLM_RPM_LIMIT": "12"},
            ["gemini-3.6-flash", "gemini-2.5-flash-lite"],
        )
        assert result.exit_code == 0
        assert "Discovery model" in result.output
        assert "RPM limit" in result.output
        assert "12" in result.output

    def test_bad_discovery_model_warns_with_available(self, tmp_path, monkeypatch) -> None:
        """Bad discovery model triggers a WARN with an Available: model list."""
        result = self._invoke(
            tmp_path, monkeypatch,
            {"GEMINI_API_KEY": "k", "LLM_DISCOVERY_MODEL": "does-not-exist", "LLM_RPM_LIMIT": "12"},
            ["gemini-3.6-flash", "gemini-2.5-flash-lite"],
        )
        assert result.exit_code == 0
        assert "WARN" in result.output
        assert "Available:" in result.output

    def test_opencode_provider_no_gemini_missing(self, tmp_path, monkeypatch) -> None:
        """With OPENCODE_API_KEY set, Gemini is not reported MISSING."""
        result = self._invoke(
            tmp_path, monkeypatch,
            {"OPENCODE_API_KEY": "sk-test", "LLM_MODEL": "opencode/nemotron-3-nano-free", "LLM_RPM_LIMIT": "12"},
            [],
        )
        assert result.exit_code == 0
        assert "OpenCode" in result.output
        assert "Discovery model" in result.output
        # Gemini must not be reported as the LLM provider / missing key when OpenCode is set
        assert "LLM API key         OK  OpenCode" in result.output

