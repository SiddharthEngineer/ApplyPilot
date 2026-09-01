"""Tests for init wizard content library, site passwords, and pre-fill support."""

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
    def test_content_library_mode_skips_resume_prompts(self, mock_ask, mock_confirm, temp_content_library, tmp_path):
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


# ---------------------------------------------------------------------------
# Pre-fill helpers
# ---------------------------------------------------------------------------


class TestStrToBool:
    """Test the _str_to_bool helper."""

    def test_true_strings(self):
        from applypilot.wizard.init import _str_to_bool

        assert _str_to_bool("yes") is True
        assert _str_to_bool("Yes") is True
        assert _str_to_bool("YES") is True
        assert _str_to_bool("true") is True
        assert _str_to_bool("1") is True

    def test_false_strings(self):
        from applypilot.wizard.init import _str_to_bool

        assert _str_to_bool("no") is False
        assert _str_to_bool("No") is False
        assert _str_to_bool("false") is False
        assert _str_to_bool("0") is False

    def test_bool_passthrough(self):
        from applypilot.wizard.init import _str_to_bool

        assert _str_to_bool(True) is True
        assert _str_to_bool(False) is False

    def test_unknown_returns_default(self):
        from applypilot.wizard.init import _str_to_bool

        assert _str_to_bool("maybe") is True  # default=True
        assert _str_to_bool("maybe", default=False) is False

    def test_none_returns_default(self):
        from applypilot.wizard.init import _str_to_bool

        assert _str_to_bool(None) is True
        assert _str_to_bool(None, default=False) is False


class TestJoinList:
    """Test the _join_list helper."""

    def test_join_list(self):
        from applypilot.wizard.init import _join_list

        assert _join_list(["Python", "JS"]) == "Python, JS"

    def test_empty_list(self):
        from applypilot.wizard.init import _join_list

        assert _join_list([]) == ""

    def test_string_passthrough(self):
        from applypilot.wizard.init import _join_list

        assert _join_list("hello") == "hello"

    def test_none_returns_empty(self):
        from applypilot.wizard.init import _join_list

        assert _join_list(None) == ""


class TestLoadExistingProfile:
    """Test _load_existing_profile helper."""

    def test_loads_valid_profile(self, tmp_path):
        from applypilot.wizard.init import _load_existing_profile

        profile_path = tmp_path / "profile.json"
        data = {"personal": {"full_name": "Test User"}}
        profile_path.write_text(json.dumps(data), encoding="utf-8")
        with patch("applypilot.wizard.init.PROFILE_PATH", profile_path):
            result = _load_existing_profile()
        assert result == data

    def test_returns_none_when_missing(self, tmp_path):
        from applypilot.wizard.init import _load_existing_profile

        profile_path = tmp_path / "nonexistent.json"
        with patch("applypilot.wizard.init.PROFILE_PATH", profile_path):
            result = _load_existing_profile()
        assert result is None

    def test_returns_none_on_invalid_json(self, tmp_path):
        from applypilot.wizard.init import _load_existing_profile

        profile_path = tmp_path / "profile.json"
        profile_path.write_text("not json {{{", encoding="utf-8")
        with patch("applypilot.wizard.init.PROFILE_PATH", profile_path):
            result = _load_existing_profile()
        assert result is None


class TestLoadExistingEnv:
    """Test _load_existing_env helper."""

    def test_parses_env_file(self, tmp_path):
        from applypilot.wizard.init import _load_existing_env

        env_path = tmp_path / ".env"
        env_path.write_text(
            "# ApplyPilot configuration\nGEMINI_API_KEY=abc123\nLLM_MODEL=gemini-2.0-flash\n",
            encoding="utf-8",
        )
        with patch("applypilot.wizard.init.ENV_PATH", env_path):
            result = _load_existing_env()
        assert result == {"GEMINI_API_KEY": "abc123", "LLM_MODEL": "gemini-2.0-flash"}

    def test_returns_empty_when_missing(self, tmp_path):
        from applypilot.wizard.init import _load_existing_env

        env_path = tmp_path / "nonexistent.env"
        with patch("applypilot.wizard.init.ENV_PATH", env_path):
            result = _load_existing_env()
        assert result == {}


# ---------------------------------------------------------------------------
# Pre-fill behaviour: site passwords
# ---------------------------------------------------------------------------


class TestSetupSitePasswordsPrefill:
    """Test that _setup_site_passwords uses existing passwords as defaults."""

    @patch("applypilot.wizard.init.Prompt.ask")
    def test_existing_passwords_used_as_defaults(self, mock_ask):
        """When existing passwords are provided, they become the defaults."""
        from applypilot.wizard.init import _setup_site_passwords

        existing = {"workday": "old_pw", "greenhouse": "", "lever": "lever_pw", "ashby": ""}
        mock_ask.side_effect = ["new_wd", "", "new_lever", ""]
        result = _setup_site_passwords(existing=existing)

        calls = mock_ask.call_args_list
        assert calls[0].kwargs.get("default") == "old_pw"
        assert calls[1].kwargs.get("default") == ""
        assert calls[2].kwargs.get("default") == "lever_pw"
        assert calls[3].kwargs.get("default") == ""

        assert result == {
            "workday": "new_wd",
            "greenhouse": "",
            "lever": "new_lever",
            "ashby": "",
        }

    @patch("applypilot.wizard.init.Prompt.ask")
    def test_no_existing_passwords_default_to_empty(self, mock_ask):
        """When no existing passwords, all defaults are empty."""
        from applypilot.wizard.init import _setup_site_passwords

        mock_ask.side_effect = ["", "", "", ""]
        _setup_site_passwords(existing=None)

        for call in mock_ask.call_args_list:
            assert call.kwargs.get("default") == ""


# ---------------------------------------------------------------------------
# Pre-fill behaviour: profile
# ---------------------------------------------------------------------------


class TestSetupProfilePrefill:
    """Test that _setup_profile uses existing profile values as defaults."""

    @patch("applypilot.wizard.init._setup_site_passwords")
    @patch("applypilot.wizard.init.Confirm.ask")
    @patch("applypilot.wizard.init.Prompt.ask")
    @patch("applypilot.wizard.init.PROFILE_PATH")
    def test_personal_info_prefilled(self, mock_path, mock_ask, mock_confirm, mock_sp):
        """Existing personal info is used as defaults for prompts."""
        from applypilot.wizard.init import _setup_profile

        existing = {
            "personal": {
                "full_name": "Jane Doe",
                "preferred_name": "Jane",
                "email": "jane@example.com",
                "phone": "555-0000",
                "city": "Toronto",
                "province_state": "Ontario",
                "country": "Canada",
                "postal_code": "M5V 1J2",
                "address": "123 Main St",
                "linkedin_url": "https://linkedin.com/in/janedoe",
                "github_url": "https://github.com/janedoe",
                "portfolio_url": "",
                "website_url": "",
            },
            "site_passwords": {"workday": "", "greenhouse": "", "lever": "", "ashby": ""},
            "work_authorization": {
                "legally_authorized_to_work": "Yes",
                "require_sponsorship": "No",
                "work_permit_type": "PR",
            },
            "compensation": {
                "salary_expectation": "90000",
                "salary_currency": "CAD",
                "salary_range_min": "80000",
                "salary_range_max": "100000",
            },
            "experience": {
                "current_title": "Backend Engineer",
                "target_role": "Senior Backend Engineer",
                "years_of_experience_total": "5",
                "education_level": "Bachelor's",
            },
            "skills_boundary": {
                "programming_languages": ["Python", "Go"],
                "frameworks": ["FastAPI"],
                "tools": ["Docker", "AWS"],
            },
            "resume_facts": {
                "preserved_companies": ["Acme Corp"],
                "preserved_projects": ["Project X"],
                "preserved_school": "U of T",
                "real_metrics": ["99.9% uptime"],
            },
            "eeo_voluntary": {
                "gender": "Decline to self-identify",
                "race_ethnicity": "Decline to self-identify",
                "veteran_status": "Decline to self-identify",
                "disability_status": "Decline to self-identify",
            },
            "availability": {"earliest_start_date": "In 2 weeks"},
        }

        mock_sp.return_value = existing["site_passwords"]
        mock_confirm.side_effect = [True, False]  # authorized=True, sponsorship=False
        # Return existing values for all prompts (press Enter to keep)
        mock_ask.side_effect = [
            "Jane Doe",  # full_name
            "Jane",  # preferred_name
            "jane@example.com",  # email
            "555-0000",  # phone
            "Toronto",  # city
            "Ontario",  # province_state
            "Canada",  # country
            "M5V 1J2",  # postal_code
            "123 Main St",  # address
            "https://linkedin.com/in/janedoe",  # linkedin
            "https://github.com/janedoe",  # github
            "",  # portfolio
            "",  # website
            "PR",  # work_permit_type
            "90000",  # salary
            "CAD",  # currency
            "80000-100000",  # range
            "Backend Engineer",  # current_title
            "Senior Backend Engineer",  # target_role
            "5",  # years
            "Bachelor's",  # education
            "Python, Go",  # languages
            "FastAPI",  # frameworks
            "Docker, AWS",  # tools
            "Acme Corp",  # companies
            "Project X",  # projects
            "U of T",  # school
            "99.9% uptime",  # metrics
            "In 2 weeks",  # start date
        ]

        _setup_profile(existing=existing)

        # Verify defaults passed to first Prompt.ask (full_name)
        first_call = mock_ask.call_args_list[0]
        assert first_call.kwargs.get("default") == "Jane Doe"

        # Verify email default
        email_call = mock_ask.call_args_list[2]
        assert email_call.kwargs.get("default") == "jane@example.com"

    @patch("applypilot.wizard.init._setup_site_passwords")
    @patch("applypilot.wizard.init.Confirm.ask")
    @patch("applypilot.wizard.init.Prompt.ask")
    @patch("applypilot.wizard.init.PROFILE_PATH")
    def test_empty_profile_allows_fresh_input(self, mock_path, mock_ask, mock_confirm, mock_sp):
        """When no existing profile, all defaults are empty."""
        from applypilot.wizard.init import _setup_profile

        mock_sp.return_value = {"workday": "", "greenhouse": "", "lever": "", "ashby": ""}
        mock_confirm.side_effect = [True, False]

        # All prompts get empty defaults
        mock_ask.side_effect = [
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",  # personal (13)
            "",
            "",
            "",  # work auth permit + compensation (3)
            "",
            "",
            "",  # compensation + experience (3+3 already counted)
            "",
            "",
            "",
            "",
            "",
            "",  # experience + skills (6)
            "",
            "",
            "",
            "",  # resume facts (4)
            "Immediately",  # start date
        ]

        _setup_profile(existing=None)

        # Every Prompt.ask should have default=""
        for call in mock_ask.call_args_list:
            assert call.kwargs.get("default", None) == "" or call.kwargs.get("default") is not None


# ---------------------------------------------------------------------------
# Pre-fill behaviour: searches
# ---------------------------------------------------------------------------


class TestSetupSearchesPrefill:
    """Test that _setup_searches uses existing config as defaults."""

    @patch("applypilot.wizard.init.SEARCH_CONFIG_PATH")
    @patch("applypilot.wizard.init.Prompt.ask")
    def test_existing_searches_used_as_defaults(self, mock_ask, mock_path):
        """Existing search config is used as defaults for prompts."""
        from applypilot.wizard.init import _setup_searches

        existing = {
            "defaults": {"location": "New York, NY", "distance": 25},
            "location_accept": ["New York", "Brooklyn", "Remote"],
            "queries": [
                {"query": "Backend Engineer", "tier": 1},
                {"query": "Full Stack Developer", "tier": 2},
            ],
        }

        mock_ask.side_effect = [
            "New York, NY",  # location
            "25",  # distance
            "New York, Brooklyn, Remote",  # accept patterns
            "Backend Engineer, Full Stack Developer",  # roles
        ]

        _setup_searches(existing=existing)

        calls = mock_ask.call_args_list
        assert calls[0].kwargs.get("default") == "New York, NY"
        assert calls[1].kwargs.get("default") == "25"
        assert calls[2].kwargs.get("default") == "New York, Brooklyn, Remote"
        assert calls[3].kwargs.get("default") == "Backend Engineer, Full Stack Developer"

    @patch("applypilot.wizard.init.SEARCH_CONFIG_PATH")
    @patch("applypilot.wizard.init.Prompt.ask")
    def test_no_existing_searches_use_fallbacks(self, mock_ask, mock_path):
        """When no existing searches, defaults are used."""
        from applypilot.wizard.init import _setup_searches

        mock_ask.side_effect = [
            "Chicago",  # location
            "0",  # distance
            "Chicago, Remote, US",  # accept patterns (auto-generated from location)
            "Software Engineer",  # roles
        ]

        _setup_searches(existing=None)

        calls = mock_ask.call_args_list
        assert calls[0].kwargs.get("default") == "Chicago"
        assert calls[1].kwargs.get("default") == "0"


# ---------------------------------------------------------------------------
# Wizard-generated searches.yaml: sites + site_fail_threshold
# ---------------------------------------------------------------------------


class TestSetupSearchesSitesThreshold:
    """Test that _setup_searches writes explicit sites (no zip_recruiter) and site_fail_threshold."""

    @patch("applypilot.wizard.init.SEARCH_CONFIG_PATH")
    @patch("applypilot.wizard.init.Prompt.ask")
    def test_writes_sites_without_zip_recruiter(self, mock_ask, mock_path):
        """Generated YAML contains a sites list that excludes zip_recruiter."""
        import yaml

        from applypilot.wizard.init import _setup_searches

        written: list[str] = []

        def _capture_write(text, encoding="utf-8"):
            written.append(text)

        mock_path.write_text.side_effect = _capture_write
        mock_ask.side_effect = ["Chicago", "0", "Chicago, Remote, US", "Software Engineer"]

        _setup_searches(existing=None)

        cfg = yaml.safe_load(written[0])
        assert "sites" in cfg
        sites = cfg["sites"]
        assert isinstance(sites, list)
        assert "zip_recruiter" not in sites
        assert "indeed" in sites
        assert "linkedin" in sites
        assert "glassdoor" in sites
        assert "google" in sites

    @patch("applypilot.wizard.init.SEARCH_CONFIG_PATH")
    @patch("applypilot.wizard.init.Prompt.ask")
    def test_writes_site_fail_threshold(self, mock_ask, mock_path):
        """Generated YAML contains defaults.site_fail_threshold == 3."""
        import yaml

        from applypilot.wizard.init import _setup_searches

        written: list[str] = []

        def _capture_write(text, encoding="utf-8"):
            written.append(text)

        mock_path.write_text.side_effect = _capture_write
        mock_ask.side_effect = ["Chicago", "0", "Chicago, Remote, US", "Software Engineer"]

        _setup_searches(existing=None)

        cfg = yaml.safe_load(written[0])
        assert cfg["defaults"]["site_fail_threshold"] == 3

    @patch("applypilot.wizard.init.SEARCH_CONFIG_PATH")
    @patch("applypilot.wizard.init.Prompt.ask")
    def test_valid_yaml(self, mock_ask, mock_path):
        """Generated YAML is valid and parseable."""
        import yaml

        from applypilot.wizard.init import _setup_searches

        written: list[str] = []

        def _capture_write(text, encoding="utf-8"):
            written.append(text)

        mock_path.write_text.side_effect = _capture_write
        mock_ask.side_effect = ["Chicago", "0", "Chicago, Remote, US", "Software Engineer"]

        _setup_searches(existing=None)

        cfg = yaml.safe_load(written[0])
        assert isinstance(cfg, dict)
        assert cfg["defaults"]["location"] == "Chicago"
        assert cfg["defaults"]["distance"] == 0


# ---------------------------------------------------------------------------
# Pre-fill behaviour: AI features
# ---------------------------------------------------------------------------


class TestSetupAiFeaturesPrefill:
    """Test that _setup_ai_features uses existing .env as defaults."""

    @patch("applypilot.wizard.init.ENV_PATH")
    @patch("applypilot.wizard.init.Prompt.ask")
    @patch("applypilot.wizard.init.Confirm.ask")
    def test_existing_gemini_key_detected(self, mock_confirm, mock_ask, mock_env):
        """Existing Gemini key auto-selects gemini provider."""
        from applypilot.wizard.init import _setup_ai_features

        existing_env = {"GEMINI_API_KEY": "my-secret-key", "LLM_MODEL": "gemini-2.0-flash"}
        mock_confirm.return_value = True  # Enable AI
        mock_ask.side_effect = [
            "gemini",  # provider (default should be gemini)
            "my-secret-key",  # API key (pre-filled)
            "gemini-2.0-flash",  # model
            "gemini-3.1-flash-lite",  # discovery model
            "12",  # rpm limit
        ]

        _setup_ai_features(existing_env=existing_env)

        provider_call = mock_ask.call_args_list[0]
        assert provider_call.kwargs.get("default") == "gemini"

        key_call = mock_ask.call_args_list[1]
        assert key_call.kwargs.get("default") == "my-secret-key"

    @patch("applypilot.wizard.init.ENV_PATH")
    @patch("applypilot.wizard.init.Prompt.ask")
    @patch("applypilot.wizard.init.Confirm.ask")
    def test_existing_openai_key_detected(self, mock_confirm, mock_ask, mock_env):
        """Existing OpenAI key auto-selects openai provider."""
        from applypilot.wizard.init import _setup_ai_features

        existing_env = {"OPENAI_API_KEY": "sk-xxx", "LLM_MODEL": "gpt-4o"}
        mock_confirm.return_value = True
        mock_ask.side_effect = [
            "openai",
            "sk-xxx",
            "gpt-4o",
            "gpt-4o",  # discovery model (falls back to LLM_MODEL)
            "12",  # rpm limit
        ]

        _setup_ai_features(existing_env=existing_env)

        provider_call = mock_ask.call_args_list[0]
        assert provider_call.kwargs.get("default") == "openai"

    @patch("applypilot.wizard.init.ENV_PATH")
    @patch("applypilot.wizard.init.Prompt.ask")
    @patch("applypilot.wizard.init.Confirm.ask")
    def test_no_existing_env_defaults_to_gemini(self, mock_confirm, mock_ask, mock_env):
        """No existing env defaults to gemini provider."""
        from applypilot.wizard.init import _setup_ai_features

        mock_confirm.return_value = True
        mock_ask.side_effect = [
            "gemini",
            "new-key",
            "gemini-2.0-flash",
            "gemini-3.1-flash-lite",  # discovery model
            "12",  # rpm limit
        ]

        _setup_ai_features(existing_env={})

        provider_call = mock_ask.call_args_list[0]
        assert provider_call.kwargs.get("default") == "gemini"

    @patch("applypilot.wizard.init.set_restricted_permissions")
    @patch("applypilot.wizard.init.ENV_PATH")
    @patch("applypilot.wizard.init.Prompt.ask")
    @patch("applypilot.wizard.init.Confirm.ask")
    def test_writes_discovery_model_and_rpm_limit(self, mock_confirm, mock_ask, mock_env, mock_perm):
        """Default answers write LLM_DISCOVERY_MODEL and LLM_RPM_LIMIT to .env."""
        from applypilot.wizard.init import _setup_ai_features

        written: list[str] = []

        def _capture(text, encoding="utf-8"):
            written.append(text)

        mock_env.write_text.side_effect = _capture
        mock_confirm.return_value = True
        # provider, api_key, model, discovery_model, rpm_limit
        mock_ask.side_effect = [
            "gemini",
            "new-key",
            "gemini-3.6-flash",
            "gemini-3.1-flash-lite",
            "12",
        ]

        _setup_ai_features(existing_env={})

        content = written[0]
        assert "LLM_DISCOVERY_MODEL=gemini-3.1-flash-lite" in content
        assert "LLM_RPM_LIMIT=12" in content
        assert "GEMINI_API_KEY=new-key" in content

    @patch("applypilot.wizard.init.set_restricted_permissions")
    @patch("applypilot.wizard.init.ENV_PATH")
    @patch("applypilot.wizard.init.Prompt.ask")
    @patch("applypilot.wizard.init.Confirm.ask")
    def test_discovery_model_default_depends_on_provider(self, mock_confirm, mock_ask, mock_env, mock_perm):
        """Non-gemini providers default discovery model to LLM_MODEL, not flash-lite."""
        from applypilot.wizard.init import _setup_ai_features

        written: list[str] = []

        def _capture(text, encoding="utf-8"):
            written.append(text)

        mock_env.write_text.side_effect = _capture
        mock_confirm.return_value = True
        mock_ask.side_effect = [
            "openai",
            "sk-xxx",
            "gpt-4o-mini",
            "gpt-4o-mini",  # discovery model default falls back to LLM_MODEL
            "12",
        ]

        _setup_ai_features(existing_env={})

        content = written[0]
        assert "LLM_DISCOVERY_MODEL=gpt-4o-mini" in content

