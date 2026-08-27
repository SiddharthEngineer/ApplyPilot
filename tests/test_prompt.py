"""Tests for prompt.py — verify no passwords or secrets in prompt output."""

from unittest.mock import patch

import pytest

from applypilot.apply.prompt import _build_captcha_section, build_prompt


def _minimal_profile(**overrides):
    """Build a minimal valid profile for testing."""
    profile = {
        "personal": {
            "full_name": "Test User",
            "email": "test@example.com",
            "phone": "555-123-4567",
            "city": "Toronto",
        },
        "work_authorization": {
            "legally_authorized_to_work": "Yes",
            "require_sponsorship": "No",
        },
        "compensation": {
            "salary_expectation": "100000",
            "salary_currency": "USD",
        },
        "experience": {
            "years_of_experience_total": "5",
            "education_level": "Bachelor's",
        },
        "availability": {"earliest_start_date": "Immediately"},
        "eeo_voluntary": {},
        "site_passwords": {
            "workday": "my_secret_password",
            "greenhouse": "gh_password_123",
            "lever": "lever_pass!",
            "ashby": "ashby_pw",
        },
    }
    profile.update(overrides)
    return profile


def _minimal_job():
    return {
        "url": "https://example.com/job",
        "title": "Software Engineer",
        "site": "example.com",
        "application_url": "https://example.com/apply",
        "fit_score": 8,
        "tailored_resume_path": "/tmp/resume.pdf",
    }


class TestNoPasswordsInPrompt:
    """Verify that passwords never appear in prompt output."""

    @pytest.fixture(autouse=True)
    def _mock_dependencies(self, tmp_path):
        """Mock external dependencies for build_prompt."""
        resume_pdf = tmp_path / "resume.pdf"
        resume_pdf.write_bytes(b"%PDF-1.4 fake")

        with (
            patch("applypilot.apply.prompt.config.load_profile") as mock_profile,
            patch("applypilot.apply.prompt.config.load_search_config") as mock_search,
            patch("applypilot.apply.prompt.config.APPLY_WORKER_DIR", tmp_path),
            patch("applypilot.apply.prompt.shutil.copy"),
            patch("applypilot.apply.prompt.Path") as mock_path_cls,
            patch("applypilot.config.load_blocked_sso", return_value=["sso.google.com"]),
        ):
            mock_profile.return_value = _minimal_profile()
            mock_search.return_value = {"location": {"accept_patterns": ["Toronto"]}}

            mock_instance = mock_path_cls.return_value
            mock_instance.with_suffix.return_value.resolve.return_value = resume_pdf
            mock_instance.exists.return_value = True

            yield mock_profile

    def test_no_workday_password(self, _mock_dependencies):
        prompt = build_prompt(_minimal_job(), "Test Resume", cdp_port=9222)
        assert "my_secret_password" not in prompt

    def test_no_greenhouse_password(self, _mock_dependencies):
        prompt = build_prompt(_minimal_job(), "Test Resume", cdp_port=9222)
        assert "gh_password_123" not in prompt

    def test_no_lever_password(self, _mock_dependencies):
        prompt = build_prompt(_minimal_job(), "Test Resume", cdp_port=9222)
        assert "lever_pass!" not in prompt

    def test_no_ashby_password(self, _mock_dependencies):
        prompt = build_prompt(_minimal_job(), "Test Resume", cdp_port=9222)
        assert "ashby_pw" not in prompt

    def test_no_capsolver_key(self, _mock_dependencies):
        with patch.dict("os.environ", {"CAPSOLVER_API_KEY": "CAP_d41d8cd98f00b204e9800998ecf8427e"}):
            prompt = build_prompt(_minimal_job(), "Test Resume", cdp_port=9222)
            assert "CAP_d41d8cd98f00b204e9800998ecf8427e" not in prompt

    def test_ats_login_tool_in_prompt(self, _mock_dependencies):
        prompt = build_prompt(_minimal_job(), "Test Resume", cdp_port=9222)
        assert "ats_login" in prompt
        assert 'ats="' in prompt or "ats=\"" in prompt

    def test_cdp_port_interpolated(self, _mock_dependencies):
        prompt = build_prompt(_minimal_job(), "Test Resume", cdp_port=9333)
        assert "cdp_port=9333" in prompt

    def test_url_pattern_table_preserved(self, _mock_dependencies):
        prompt = build_prompt(_minimal_job(), "Test Resume", cdp_port=9222)
        assert "*.myworkdayjobs.com" in prompt
        assert "boards.greenhouse.io" in prompt
        assert "jobs.lever.co" in prompt
        assert "jobs.ashbyhq.com" in prompt


class TestCaptchaSection:
    """Test CAPTCHA section no longer contains API key or broken instructions."""

    def test_no_capsolver_key_in_captcha_section(self):
        with patch.dict("os.environ", {"CAPSOLVER_API_KEY": "REAL_KEY_abc123"}):
            section = _build_captcha_section()
            assert "REAL_KEY_abc123" not in section

    def test_captcha_section_mentions_captcha_solve_tool(self):
        section = _build_captcha_section()
        assert "captcha_solve" in section

    def test_captcha_section_not_fstring(self):
        section = _build_captcha_section()
        assert "{capsolver_key}" not in section

    def test_no_browser_evaluate_capsolver_api_call(self):
        section = _build_captcha_section()
        assert "api.capsolver.com/createTask" not in section
        assert "api.capsolver.com/getTaskResult" not in section

    def test_no_instruction_to_read_api_key_via_browser(self):
        section = _build_captcha_section()
        assert "CAPSOLVER_API_KEY" not in section

    def test_section_instructs_token_injection(self):
        section = _build_captcha_section()
        assert "INJECT TOKEN" in section
        assert "browser_evaluate" in section

    def test_manual_fallback_references_captcha_solve(self):
        section = _build_captcha_section()
        assert "captcha_solve" in section
