"""Tests for pipeline.py discover stage disabled-site banner."""

import unittest.mock as mock

import pytest

from applypilot.pipeline import _run_discover

_PATCH_TARGET = "applypilot.discovery.jobspy.run_discovery"


@pytest.fixture(autouse=True)
def _stub_network_scrapers():
    """Stub Workday and SmartExtract so _run_discover() never hits the network.

    _run_discover() imports these inside the function, so patch the source module attributes.
    """
    with mock.patch("applypilot.discovery.workday.run_workday_discovery", return_value=None) as wd, \
         mock.patch("applypilot.discovery.smartextract.run_smart_extract", return_value=None) as se:
        yield wd, se


class TestDiscoverDisabledSiteBanner:
    def test_banner_printed_when_sites_disabled(self, capsys):
        """Disabled sites produce a yellow banner mentioning the site name."""
        mock_result = {"disabled_sites": ["zip_recruiter"], "site_stats": {}}

        with mock.patch(_PATCH_TARGET, return_value=mock_result):
            _run_discover()

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


def test_workday_and_smartextract_are_stubbed(_stub_network_scrapers):
    """The stubs, not the real scrapers, run during _run_discover()."""
    wd, se = _stub_network_scrapers
    with mock.patch(_PATCH_TARGET, return_value={"disabled_sites": [], "site_stats": {}}):
        stats = _run_discover()

    wd.assert_called_once()
    se.assert_called_once()
    assert stats["workday"] == "ok"
    assert stats["smartextract"] == "ok"
