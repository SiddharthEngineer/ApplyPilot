"""Tests for pipeline.py discover stage disabled-site banner."""

import unittest.mock as mock

from applypilot.pipeline import _run_discover

_PATCH_TARGET = "applypilot.discovery.jobspy.run_discovery"


class TestDiscoverDisabledSiteBanner:
    def test_banner_printed_when_sites_disabled(self, capsys):
        """Disabled sites produce a yellow banner mentioning the site name."""
        mock_result = {"disabled_sites": ["zip_recruiter"], "site_stats": {}}

        with mock.patch(_PATCH_TARGET, return_value=mock_result):
            stats = _run_discover()

        captured = capsys.readouterr()
        assert "zip_recruiter" in captured.out
        assert "skipped" in captured.out.lower() or "blocked" in captured.out.lower()

    def test_stats_show_disabled_when_sites_disabled(self, capsys):
        """stats['jobspy'] includes 'disabled' when a site is skipped."""
        mock_result = {"disabled_sites": ["zip_recruiter"], "site_stats": {}}

        with mock.patch(_PATCH_TARGET, return_value=mock_result):
            stats = _run_discover()

        assert "disabled" in stats["jobspy"]
        assert "zip_recruiter" in stats["jobspy"]

    def test_no_banner_when_no_disabled_sites(self, capsys):
        """No banner is printed when disabled_sites is empty."""
        mock_result = {"disabled_sites": [], "site_stats": {}}

        with mock.patch(_PATCH_TARGET, return_value=mock_result):
            stats = _run_discover()

        captured = capsys.readouterr()
        assert "skipped" not in captured.out.lower()
        assert "blocked" not in captured.out.lower()
        assert stats["jobspy"] == "ok"

    def test_error_still_printed(self, capsys):
        """JobSpy exceptions still produce a red error banner."""
        with mock.patch(_PATCH_TARGET, side_effect=RuntimeError("boom")):
            stats = _run_discover()

        captured = capsys.readouterr()
        assert "boom" in captured.out
        assert stats["jobspy"].startswith("error:")
