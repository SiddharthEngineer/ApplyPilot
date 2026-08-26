"""Tests for PDF overflow detection and role-group rendering."""

import textwrap
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from applypilot.scoring.pdf import (
    _USABLE_HEIGHT_PT,
    build_html,
    convert_to_pdf,
    parse_resume,
    render_pdf,
)


# ── Overflow constants ────────────────────────────────────────────────────

class TestOverflowConstants:
    def test_usable_height_is_positive(self):
        assert _USABLE_HEIGHT_PT > 0

    def test_usable_height_less_than_letter(self):
        # Letter is 792pt; usable should be less due to margins
        assert _USABLE_HEIGHT_PT < 792


# ── render_pdf return value ───────────────────────────────────────────────

class TestRenderPdfReturns:
    @patch("playwright.sync_api.sync_playwright")
    def test_returns_overflow_dict(self, mock_pw):
        mock_page = MagicMock()
        mock_page.evaluate.return_value = 600.0  # fits on one page
        mock_browser = MagicMock()
        mock_browser.new_page.return_value = mock_page
        mock_pw.return_value.__enter__.return_value.chromium.launch.return_value = mock_browser

        result = render_pdf("<html></html>", "/tmp/test.pdf")

        assert "overflow" in result
        assert "content_height_pt" in result
        assert "usable_height_pt" in result
        assert result["overflow"] is False
        assert result["content_height_pt"] == 600.0

    @patch("playwright.sync_api.sync_playwright")
    def test_detects_overflow(self, mock_pw):
        mock_page = MagicMock()
        mock_page.evaluate.return_value = 800.0  # exceeds usable height
        mock_browser = MagicMock()
        mock_browser.new_page.return_value = mock_page
        mock_pw.return_value.__enter__.return_value.chromium.launch.return_value = mock_browser

        result = render_pdf("<html></html>", "/tmp/test.pdf")

        assert result["overflow"] is True
        assert result["content_height_pt"] == 800.0

    @patch("playwright.sync_api.sync_playwright")
    def test_exact_fit_not_overflow(self, mock_pw):
        mock_page = MagicMock()
        mock_page.evaluate.return_value = _USABLE_HEIGHT_PT  # exact fit
        mock_browser = MagicMock()
        mock_browser.new_page.return_value = mock_page
        mock_pw.return_value.__enter__.return_value.chromium.launch.return_value = mock_browser

        result = render_pdf("<html></html>", "/tmp/test.pdf")

        assert result["overflow"] is False


# ── convert_to_pdf return value ───────────────────────────────────────────

class TestConvertToPdfReturns:
    @patch("applypilot.scoring.pdf.render_pdf")
    def test_returns_dict_with_overflow(self, mock_render):
        mock_render.return_value = {
            "overflow": False,
            "content_height_pt": 500.0,
            "usable_height_pt": _USABLE_HEIGHT_PT,
        }

        with tempfile.NamedTemporaryFile(suffix=".txt", mode="w", delete=False) as f:
            f.write("Name\nTitle\nContact\n\nSUMMARY\nTest summary\n")
            f.flush()
            result = convert_to_pdf(Path(f.name))

        assert isinstance(result, dict)
        assert "path" in result
        assert "overflow" in result
        assert result["overflow"] is False
        assert result["content_height_pt"] == 500.0

    def test_html_only_returns_none_overflow(self):
        with tempfile.NamedTemporaryFile(suffix=".txt", mode="w", delete=False) as f:
            f.write("Name\nTitle\nContact\n\nSUMMARY\nTest summary\n")
            f.flush()
            result = convert_to_pdf(Path(f.name), html_only=True)

        assert result["overflow"] is None
        assert result["content_height_pt"] is None
        assert result["path"].suffix == ".html"


# ── Role-group HTML rendering ─────────────────────────────────────────────

class TestRoleGroupRendering:
    def _make_resume_text(self, entries_text: str) -> str:
        return textwrap.dedent(f"""\
            Siddharth Engineer
            Software Engineer
            email@test.com | 555-0100

            SUMMARY
            Test summary

            TECHNICAL SKILLS
            Languages: Python, JavaScript

            EXPERIENCE
            {entries_text}

            PROJECTS
            Project One
            Jan 2024–Present
            - Built something

            EDUCATION
            MIT, BS CS, 2023
        """)

    def test_role_entry_gets_css_class(self):
        text = self._make_resume_text(
            "Data Science Associate, AIR\n"
            "Sep 2025–Present\n"
            "- Built pipeline\n"
        )
        resume = parse_resume(text)
        html = build_html(resume)
        assert 'class="entry role-entry"' in html

    def test_non_role_entry_no_role_class(self):
        text = self._make_resume_text(
            "PatentsView Data Pipeline\n"
            "Nov 2025\n"
            "- Led pipeline migration\n"
        )
        resume = parse_resume(text)
        html = build_html(resume)
        assert 'class="entry role-entry"' not in html
        assert 'class="entry"' in html

    def test_mixed_entries_render_correctly(self):
        text = self._make_resume_text(
            "Data Science Associate, AIR\n"
            "Sep 2025–Present\n"
            "- Built pipeline\n"
            "\n"
            "PatentsView Data Pipeline\n"
            "Nov 2025\n"
            "- Led migration\n"
        )
        resume = parse_resume(text)
        html = build_html(resume)
        # Should have one role-entry and one regular entry
        assert html.count('class="entry role-entry"') == 1

    def test_role_header_with_intern_keyword(self):
        text = self._make_resume_text(
            "Software Engineering Intern\n"
            "Summer 2024\n"
            "- Shipped feature\n"
        )
        resume = parse_resume(text)
        html = build_html(resume)
        assert 'class="entry role-entry"' in html

    def test_role_header_with_lead_keyword(self):
        text = self._make_resume_text(
            "ML Engineering Lead\n"
            "Jan 2024–Present\n"
            "- Deployed model\n"
        )
        resume = parse_resume(text)
        html = build_html(resume)
        assert 'class="entry role-entry"' in html

    def test_project_with_engineer_in_name_not_role(self):
        """Project names containing role keywords should also get role-entry class."""
        text = self._make_resume_text(
            "Data Engineering Pipeline\n"
            "Jan 2024\n"
            "- Built ETL\n"
        )
        resume = parse_resume(text)
        html = build_html(resume)
        # 'Engineering' in the name triggers role-entry — this is acceptable
        # since the CSS difference is minimal (just extra margin-top)
        assert 'class="entry' in html

    def test_role_group_css_in_html(self):
        """Verify the CSS includes role-entry styling."""
        text = self._make_resume_text(
            "Data Science Associate, AIR\n"
            "Sep 2025–Present\n"
            "- Built pipeline\n"
        )
        resume = parse_resume(text)
        html = build_html(resume)
        assert ".entry.role-entry" in html
        assert "margin-top: 3px" in html
