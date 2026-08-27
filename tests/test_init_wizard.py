"""Tests for init wizard content library and site passwords support."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def temp_content_library(tmp_path: Path) -> Path:
    """Create a temporary content library file."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    content_library = src_dir / "content_library.md"
    content_library.write_text(
        "# Content Library\n\n## CURRENT ROLE — Data Science Associate, AIR (Sep 2025–Present)\n\n"
        "### PatentsView Data Pipeline Lead (Nov 2025–present)\n\n"
        "- **Context:** Led migration of legacy pipeline\n"
        "- **Scope/Scale:** 1M+ records\n"
        "- **Tools & Actions:** Python, Docker, Airflow\n"
        "- **Outcome/Metrics:** 99.9% uptime\n"
        "- **Angles:** DEVOPS, PIPELINE\n",
        encoding="utf-8",
    )
    return content_library


@pytest.fixture
def temp_resume_txt(tmp_path: Path) -> Path:
    """Create a temporary resume text file."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    resume = src_dir / "resume.txt"
    resume.write_text("John Doe\nSoftware Engineer\n", encoding="utf-8")
    return resume


@pytest.fixture
def temp_resume_pdf(tmp_path: Path) -> Path:
    """Create a temporary resume PDF file."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    resume = src_dir / "resume.pdf"
    resume.write_bytes(b"%PDF-1.4 fake pdf content")
    return resume


class TestSetupResumeWorkflowChoice:
    """Test the workflow choice in _setup_resume()."""

    @patch("applypilot.wizard.init.Confirm.ask")
    @patch("applypilot.wizard.init.Prompt.ask")
    def test_traditional_workflow_selected(self, mock_ask, mock_confirm):
        """Test that selecting option 1 triggers traditional resume setup."""
        from applypilot.wizard.init import _setup_resume

        mock_ask.return_value = "1"
        mock_confirm.return_value = False
        with patch("applypilot.wizard.init._setup_traditional_resume") as mock_traditional:
            _setup_resume()
            mock_traditional.assert_called_once()

    @patch("applypilot.wizard.init.Confirm.ask")
    @patch("applypilot.wizard.init.Prompt.ask")
    def test_content_library_workflow_selected(self, mock_ask, mock_confirm):
        """Test that selecting option 2 triggers content library setup."""
        from applypilot.wizard.init import _setup_resume

        mock_ask.return_value = "2"
        mock_confirm.return_value = False
        with patch("applypilot.wizard.init._setup_content_library") as mock_cl:
            _setup_resume()
            mock_cl.assert_called_once()


class TestSetupTraditionalResume:
    """Test traditional resume setup."""

    @patch("applypilot.wizard.init.Prompt.ask")
    def test_txt_file_copied(self, mock_ask, temp_resume_txt, tmp_path):
        """Test that .txt file is copied to RESUME_PATH."""
        from applypilot.wizard.init import _setup_traditional_resume

        dest_dir = tmp_path / "dest"
        dest_dir.mkdir()
        dest_path = dest_dir / "resume.txt"
        mock_ask.return_value = str(temp_resume_txt)
        with patch("applypilot.wizard.init.RESUME_PATH", dest_path):
            _setup_traditional_resume()
            assert dest_path.exists()

    @patch("applypilot.wizard.init.Prompt.ask")
    def test_pdf_file_copied(self, mock_ask, temp_resume_pdf, tmp_path):
        """Test that .pdf file is copied to RESUME_PDF_PATH."""
        from applypilot.wizard.init import _setup_traditional_resume

        dest_dir = tmp_path / "dest"
        dest_dir.mkdir()
        dest_path = dest_dir / "resume.pdf"
        mock_ask.return_value = str(temp_resume_pdf)
        with patch("applypilot.wizard.init.RESUME_PDF_PATH", dest_path):
            _setup_traditional_resume()
            assert dest_path.exists()


class TestSetupContentLibrary:
    """Test content library setup."""

    @patch("applypilot.wizard.init.Prompt.ask")
    def test_content_library_copied(self, mock_ask, temp_content_library, tmp_path):
        """Test that content library file is copied to CONTENT_LIBRARY_PATH."""
        from applypilot.wizard.init import _setup_content_library

        dest_dir = tmp_path / "dest"
        dest_dir.mkdir()
        dest_path = dest_dir / "content_library.md"
        mock_ask.return_value = str(temp_content_library)
        with patch("applypilot.wizard.init.CONTENT_LIBRARY_PATH", dest_path):
            _setup_content_library()
            assert dest_path.exists()

    @patch("applypilot.wizard.init.Prompt.ask")
    def test_nonexistent_file_prompts_again(self, mock_ask, tmp_path):
        """Test that non-existent file prompts for another path."""
        from applypilot.wizard.init import _setup_content_library

        dest_dir = tmp_path / "dest"
        dest_dir.mkdir()
        dest_path = dest_dir / "content_library.md"
        mock_ask.side_effect = [
            str(tmp_path / "nonexistent.md"),
            str(tmp_path / "also_nonexistent.md"),
        ]
        with patch("applypilot.wizard.init.CONTENT_LIBRARY_PATH", dest_path), pytest.raises(StopIteration):
            _setup_content_library()


class TestSetupPdfReference:
    """Test optional PDF reference setup."""

    @patch("applypilot.wizard.init.Prompt.ask")
    def test_pdf_reference_copied(self, mock_ask, temp_resume_pdf, tmp_path):
        """Test that PDF reference is copied to RESUME_REFERENCE_PATH."""
        from applypilot.wizard.init import _setup_pdf_reference

        dest_dir = tmp_path / "dest"
        dest_dir.mkdir()
        dest_path = dest_dir / "resume_reference.pdf"
        mock_ask.return_value = str(temp_resume_pdf)
        with patch("applypilot.wizard.init.RESUME_REFERENCE_PATH", dest_path):
            _setup_pdf_reference()
            assert dest_path.exists()

    @patch("applypilot.wizard.init.Prompt.ask")
    def test_non_pdf_rejected(self, mock_ask, temp_resume_txt, tmp_path):
        """Test that non-PDF files are rejected."""
        from applypilot.wizard.init import _setup_pdf_reference

        dest_dir = tmp_path / "dest"
        dest_dir.mkdir()
        dest_path = dest_dir / "resume_reference.pdf"
        mock_ask.side_effect = [str(temp_resume_txt), str(temp_resume_txt)]
        with patch("applypilot.wizard.init.RESUME_REFERENCE_PATH", dest_path), pytest.raises(StopIteration):
            _setup_pdf_reference()


class TestIntegration:
    """Integration tests for the init wizard."""

    @patch("applypilot.wizard.init.Confirm.ask")
    @patch("applypilot.wizard.init.Prompt.ask")
    def test_content_library_mode_skips_resume_prompts(
        self, mock_ask, mock_confirm, temp_content_library, tmp_path
    ):
        """Test that content library mode doesn't ask for resume.txt/resume.pdf."""
        from applypilot.wizard.init import _setup_resume

        dest_dir = tmp_path / "dest"
        dest_dir.mkdir()
        dest_path = dest_dir / "content_library.md"
        # Select content library (2), then skip PDF reference (No)
        mock_ask.side_effect = ["2", str(temp_content_library)]
        mock_confirm.return_value = False

        with patch("applypilot.wizard.init.CONTENT_LIBRARY_PATH", dest_path):
            _setup_resume()
            # Should only have 2 calls to Prompt.ask: workflow choice and content library path
            # Should NOT be asked for resume.txt or resume.pdf
            assert mock_ask.call_count == 2
            assert dest_path.exists()


class TestSetupSitePasswords:
    """Test the site-specific password setup."""

    @patch("applypilot.wizard.init.Prompt.ask")
    def test_prompts_for_each_ats(self, mock_ask):
        """Test that _setup_site_passwords prompts for all 4 ATS platforms."""
        from applypilot.wizard.init import _setup_site_passwords

        mock_ask.side_effect = ["pw1", "pw2", "pw3", "pw4"]
        result = _setup_site_passwords()

        assert mock_ask.call_count == 4
        assert result == {
            "workday": "pw1",
            "greenhouse": "pw2",
            "lever": "pw3",
            "ashby": "pw4",
        }

    @patch("applypilot.wizard.init.Prompt.ask")
    def test_all_blank_passwords(self, mock_ask):
        """Test that leaving all passwords blank is valid."""
        from applypilot.wizard.init import _setup_site_passwords

        mock_ask.side_effect = ["", "", "", ""]
        result = _setup_site_passwords()

        assert result == {
            "workday": "",
            "greenhouse": "",
            "lever": "",
            "ashby": "",
        }

    @patch("applypilot.wizard.init.Prompt.ask")
    def test_partial_passwords(self, mock_ask):
        """Test that some passwords can be filled while others are blank."""
        from applypilot.wizard.init import _setup_site_passwords

        mock_ask.side_effect = ["workday_pass", "", "lever_pass", ""]
        result = _setup_site_passwords()

        assert result["workday"] == "workday_pass"
        assert result["greenhouse"] == ""
        assert result["lever"] == "lever_pass"
        assert result["ashby"] == ""


class TestProfileMigration:
    """Test backward-compat migration of personal.password -> site_passwords."""

    def test_migration_when_site_passwords_missing(self, tmp_path):
        """Test that load_profile migrates personal.password to site_passwords."""
        from applypilot.config import load_profile

        profile_data = {
            "personal": {"password": "old_pass", "email": "test@test.com"},
            "work_authorization": {},
        }
        profile_path = tmp_path / "profile.json"
        profile_path.write_text(json.dumps(profile_data), encoding="utf-8")

        with patch("applypilot.config.PROFILE_PATH", profile_path):
            result = load_profile()

        assert "site_passwords" in result
        assert result["site_passwords"]["workday"] == "old_pass"
        assert result["site_passwords"]["greenhouse"] == ""
        assert result["site_passwords"]["lever"] == ""
        assert result["site_passwords"]["ashby"] == ""

    def test_no_migration_when_site_passwords_exists(self, tmp_path):
        """Test that existing site_passwords are preserved."""
        from applypilot.config import load_profile

        profile_data = {
            "personal": {"password": "old_pass", "email": "test@test.com"},
            "site_passwords": {"workday": "keep_this", "greenhouse": "", "lever": "", "ashby": ""},
        }
        profile_path = tmp_path / "profile.json"
        profile_path.write_text(json.dumps(profile_data), encoding="utf-8")

        with patch("applypilot.config.PROFILE_PATH", profile_path):
            result = load_profile()

        assert result["site_passwords"]["workday"] == "keep_this"

    def test_migration_with_empty_legacy_password(self, tmp_path):
        """Test migration when legacy password is empty."""
        from applypilot.config import load_profile

        profile_data = {
            "personal": {"password": "", "email": "test@test.com"},
        }
        profile_path = tmp_path / "profile.json"
        profile_path.write_text(json.dumps(profile_data), encoding="utf-8")

        with patch("applypilot.config.PROFILE_PATH", profile_path):
            result = load_profile()

        assert result["site_passwords"] == {"workday": "", "greenhouse": "", "lever": "", "ashby": ""}
