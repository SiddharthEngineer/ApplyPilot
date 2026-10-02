"""Tests for jobspy.py site counting and site tracker."""

import pandas as pd
import pytest

from applypilot.discovery.jobspy import _SiteTracker, _site_counts, _normalize_country


# ---------------------------------------------------------------------------
# _site_counts
# ---------------------------------------------------------------------------

class TestSiteCounts:
    def test_counts_per_site(self):
        df = pd.DataFrame({
            "site": ["zip_recruiter", "zip_recruiter", "zip_recruiter", "indeed", "linkedin"],
            "title": ["a", "b", "c", "d", "e"],
        })
        result = _site_counts(df, ["indeed", "linkedin", "zip_recruiter"])
        assert result == {"indeed": 1, "linkedin": 1, "zip_recruiter": 3}

    def test_requested_site_missing_from_df(self):
        df = pd.DataFrame({
            "site": ["indeed", "linkedin"],
            "title": ["a", "b"],
        })
        result = _site_counts(df, ["indeed", "linkedin", "zip_recruiter"])
        assert result == {"indeed": 1, "linkedin": 1, "zip_recruiter": 0}

    def test_empty_df(self):
        df = pd.DataFrame(columns=["site", "title"])
        result = _site_counts(df, ["indeed", "zip_recruiter"])
        assert result == {"indeed": 0, "zip_recruiter": 0}

    def test_no_site_column(self):
        df = pd.DataFrame({"title": ["a", "b"]})
        result = _site_counts(df, ["indeed", "linkedin"])
        assert result == {"indeed": 0, "linkedin": 0}

    def test_preserves_order(self):
        df = pd.DataFrame({"site": ["linkedin", "indeed", "indeed"]})
        result = _site_counts(df, ["linkedin", "indeed"])
        assert list(result.keys()) == ["linkedin", "indeed"]


# ---------------------------------------------------------------------------
# _SiteTracker
# ---------------------------------------------------------------------------

class TestSiteTracker:
    def test_note_increments_requests_and_counts(self):
        t = _SiteTracker(threshold=3)
        t.note(["indeed", "zip_recruiter"], {"indeed": 5, "zip_recruiter": 0})
        assert t.requests == {"indeed": 1, "zip_recruiter": 1}
        assert t.counts == {"indeed": 5, "zip_recruiter": 0}

    def test_consecutive_empty_bumps(self):
        t = _SiteTracker(threshold=3)
        t.note(["zip_recruiter"], {"zip_recruiter": 0})
        assert t.consecutive_empty["zip_recruiter"] == 1
        t.note(["zip_recruiter"], {"zip_recruiter": 0})
        assert t.consecutive_empty["zip_recruiter"] == 2

    def test_result_resets_counter(self):
        t = _SiteTracker(threshold=3)
        t.note(["zip_recruiter"], {"zip_recruiter": 0})
        t.note(["zip_recruiter"], {"zip_recruiter": 0})
        t.note(["zip_recruiter"], {"zip_recruiter": 2})
        assert t.consecutive_empty["zip_recruiter"] == 0
        assert "zip_recruiter" not in t.disabled

    def test_disable_after_threshold(self):
        t = _SiteTracker(threshold=3)
        t.note(["zip_recruiter"], {"zip_recruiter": 0})
        t.note(["zip_recruiter"], {"zip_recruiter": 0})
        newly = t.note(["zip_recruiter"], {"zip_recruiter": 0})
        assert "zip_recruiter" in t.disabled
        assert newly == ["zip_recruiter"]

    def test_disable_after_threshold_1(self):
        t = _SiteTracker(threshold=1)
        newly = t.note(["zip_recruiter"], {"zip_recruiter": 0})
        assert "zip_recruiter" in t.disabled
        assert newly == ["zip_recruiter"]

    def test_not_disabled_until_threshold(self):
        t = _SiteTracker(threshold=3)
        t.note(["zip_recruiter"], {"zip_recruiter": 0})
        newly = t.note(["zip_recruiter"], {"zip_recruiter": 0})
        assert "zip_recruiter" not in t.disabled
        assert newly == []

    def test_already_disabled_returns_empty(self):
        t = _SiteTracker(threshold=1)
        t.note(["zip_recruiter"], {"zip_recruiter": 0})
        newly = t.note(["zip_recruiter"], {"zip_recruiter": 0})
        assert newly == []
        assert t.requests["zip_recruiter"] == 2

    def test_active_sites_drops_disabled(self):
        t = _SiteTracker(threshold=1)
        t.note(["zip_recruiter"], {"zip_recruiter": 0})
        active = t.active_sites(["indeed", "linkedin", "zip_recruiter"])
        assert active == ["indeed", "linkedin"]

    def test_active_sites_preserves_order(self):
        t = _SiteTracker(threshold=1)
        t.note(["zip_recruiter"], {"zip_recruiter": 0})
        active = t.active_sites(["zip_recruiter", "indeed", "linkedin"])
        assert active == ["indeed", "linkedin"]

    def test_report_keys(self):
        t = _SiteTracker(threshold=3)
        t.note(["indeed", "zip_recruiter"], {"indeed": 5, "zip_recruiter": 0})
        report = t.report()
        assert set(report.keys()) == {"counts", "requests", "disabled"}
        assert report["counts"]["indeed"] == 5
        assert report["requests"]["indeed"] == 1
        assert report["disabled"] == []

    def test_report_disabled_sorted(self):
        t = _SiteTracker(threshold=1)
        t.note(["zip_recruiter", "google"], {"zip_recruiter": 0, "google": 0})
        report = t.report()
        assert report["disabled"] == ["google", "zip_recruiter"]

    def test_counts_accumulate(self):
        t = _SiteTracker(threshold=3)
        t.note(["indeed"], {"indeed": 3})
        t.note(["indeed"], {"indeed": 2})
        assert t.counts["indeed"] == 5

    def test_different_sites_independent(self):
        t = _SiteTracker(threshold=2)
        t.note(["zip_recruiter", "google"], {"zip_recruiter": 0, "google": 5})
        t.note(["zip_recruiter", "google"], {"zip_recruiter": 0, "google": 3})
        assert "google" not in t.disabled
        assert "zip_recruiter" in t.disabled

    def test_newly_disabled_list_only_includes_fresh(self):
        t = _SiteTracker(threshold=2)
        t.note(["zip_recruiter", "google"], {"zip_recruiter": 0, "google": 0})
        newly1 = t.note(["zip_recruiter", "google"], {"zip_recruiter": 0, "google": 0})
        assert set(newly1) == {"google", "zip_recruiter"}
        newly2 = t.note(["zip_recruiter", "google"], {"zip_recruiter": 0, "google": 0})
        assert newly2 == []


# ---------------------------------------------------------------------------
# Integration: tracker wired through _full_crawl / run_discovery
# ---------------------------------------------------------------------------

def _make_df(sites_with_counts: dict[str, int]) -> pd.DataFrame:
    """Build a DataFrame with one row per job, each tagged with its site."""
    rows = []
    for site, count in sites_with_counts.items():
        for _ in range(count):
            rows.append({"site": site, "title": "Engineer", "job_url": f"https://{site}.com/job"})
    return pd.DataFrame(rows)


def _make_cfg(sites, threshold=None, n_locations=5):
    defaults = {"results_per_site": 10, "hours_old": 72}
    if threshold is not None:
        defaults["site_fail_threshold"] = threshold
    locations = [{"location": f"City{i}", "remote": False} for i in range(n_locations)]
    return {
        "queries": [{"query": "engineer", "tier": 1}],
        "locations": locations,
        "sites": sites,
        "defaults": defaults,
    }


def _make_mock_conn():
    import unittest.mock as mock
    conn = mock.MagicMock()
    conn.execute.return_value.fetchone.return_value = [0]
    return conn


class TestFullCrawlTracker:
    """Integration tests: _full_crawl wires _SiteTracker correctly."""

    def test_disabled_after_consecutive_empty(self):
        """zip_recruiter is disabled after threshold consecutive 0-result searches."""
        import unittest.mock as mock
        import applypilot.discovery.jobspy as mod

        scrape_returns = [
            _make_df({"indeed": 2, "linkedin": 2, "zip_recruiter": 0}),
            _make_df({"indeed": 1, "linkedin": 1, "zip_recruiter": 0}),
            _make_df({"indeed": 3, "linkedin": 1, "zip_recruiter": 0}),
        ]
        call_idx = {"i": 0}

        def fake_scrape(**kwargs):
            idx = call_idx["i"]
            call_idx["i"] += 1
            return scrape_returns[min(idx, len(scrape_returns) - 1)]

        mock_scrape = mock.MagicMock(side_effect=fake_scrape)
        conn = _make_mock_conn()
        with mock.patch.object(mod, 'init_db', return_value=conn), \
             mock.patch.object(mod, 'get_connection', return_value=conn), \
             mock.patch.object(mod, 'scrape_jobs', mock_scrape):
            cfg = _make_cfg(["indeed", "linkedin", "zip_recruiter"], threshold=3, n_locations=3)
            result = mod._full_crawl(cfg)

        assert result["disabled_sites"] == ["zip_recruiter"]
        assert "indeed" not in result["disabled_sites"]
        assert "linkedin" not in result["disabled_sites"]

        # Verify scrape_jobs was called with zip_recruiter excluded after threshold
        calls = mock_scrape.call_args_list
        # First 3 calls include zip_recruiter; after that it's excluded
        for c in calls[:3]:
            assert "zip_recruiter" in c.kwargs["site_name"]
        for c in calls[3:]:
            assert "zip_recruiter" not in c.kwargs["site_name"]

    def test_all_sites_return_results_no_disabling(self):
        """No site is disabled when every site returns >=1 result."""
        import unittest.mock as mock
        import applypilot.discovery.jobspy as mod

        def fake_scrape(**kwargs):
            return _make_df({"indeed": 2, "linkedin": 1, "zip_recruiter": 1})

        conn = _make_mock_conn()
        with mock.patch.object(mod, 'init_db', return_value=conn), \
             mock.patch.object(mod, 'get_connection', return_value=conn), \
             mock.patch.object(mod, 'scrape_jobs', fake_scrape):
            cfg = _make_cfg(["indeed", "linkedin", "zip_recruiter"], threshold=3)
            result = mod._full_crawl(cfg)

        assert result["disabled_sites"] == []
        assert result["site_stats"]["disabled"] == []

    def test_threshold_1_disables_after_single_search(self):
        """site_fail_threshold: 1 disables a board after one empty search."""
        import unittest.mock as mock
        import applypilot.discovery.jobspy as mod

        def fake_scrape(**kwargs):
            return _make_df({"indeed": 2, "linkedin": 1, "zip_recruiter": 0})

        conn = _make_mock_conn()
        with mock.patch.object(mod, 'init_db', return_value=conn), \
             mock.patch.object(mod, 'get_connection', return_value=conn), \
             mock.patch.object(mod, 'scrape_jobs', fake_scrape):
            cfg = _make_cfg(["indeed", "linkedin", "zip_recruiter"], threshold=1, n_locations=1)
            result = mod._full_crawl(cfg)

        assert result["disabled_sites"] == ["zip_recruiter"]

    def test_errors_dont_increment_consecutive_empty(self):
        """Hard errors should not penalize a board's consecutive-empty count."""
        import unittest.mock as mock
        import applypilot.discovery.jobspy as mod

        # First two calls raise (simulating network errors), third returns 0 results.
        # With threshold=2, a real empty board would be disabled after 2 calls,
        # but since those were errors, it should NOT be disabled yet.
        call_count = {"i": 0}

        def fake_scrape(**kwargs):
            call_count["i"] += 1
            if call_count["i"] <= 2:
                raise ConnectionError("simulated network failure")
            return _make_df({"indeed": 2, "linkedin": 1, "zip_recruiter": 0})

        conn = _make_mock_conn()
        with mock.patch.object(mod, 'init_db', return_value=conn), \
             mock.patch.object(mod, 'get_connection', return_value=conn), \
             mock.patch.object(mod, 'scrape_jobs', fake_scrape):
            cfg = _make_cfg(["indeed", "linkedin", "zip_recruiter"], threshold=2, n_locations=3)
            result = mod._full_crawl(cfg)

        # zip_recruiter should NOT be disabled — errors don't count as empty
        assert "zip_recruiter" not in result["disabled_sites"]
        assert result["errors"] == 2

    def test_result_dict_has_disabled_sites_and_site_stats(self):
        """_full_crawl always returns disabled_sites and site_stats keys."""
        import unittest.mock as mock
        import applypilot.discovery.jobspy as mod

        def fake_scrape(**kwargs):
            return _make_df({"indeed": 1})

        conn = _make_mock_conn()
        with mock.patch.object(mod, 'init_db', return_value=conn), \
             mock.patch.object(mod, 'get_connection', return_value=conn), \
             mock.patch.object(mod, 'scrape_jobs', fake_scrape):
            cfg = _make_cfg(["indeed"], threshold=3)
            result = mod._full_crawl(cfg)

        assert "disabled_sites" in result
        assert "site_stats" in result
        assert isinstance(result["site_stats"], dict)

    def test_run_discovery_passes_through_keys(self):
        """run_discovery returns site_stats and disabled_sites from _full_crawl."""
        import unittest.mock as mock
        import applypilot.discovery.jobspy as mod

        def fake_scrape(**kwargs):
            return _make_df({"indeed": 1, "linkedin": 1, "zip_recruiter": 0})

        conn = _make_mock_conn()
        with mock.patch.object(mod, 'init_db', return_value=conn), \
             mock.patch.object(mod, 'get_connection', return_value=conn), \
             mock.patch.object(mod, 'scrape_jobs', fake_scrape):
            cfg = _make_cfg(["indeed", "linkedin", "zip_recruiter"], threshold=1, n_locations=1)
            result = mod.run_discovery(cfg)

        assert "disabled_sites" in result
        assert "site_stats" in result
        assert result["disabled_sites"] == ["zip_recruiter"]

    def test_run_discovery_empty_config_returns_keys(self):
        """run_discovery returns site_stats and disabled_sites even with empty config."""
        import applypilot.discovery.jobspy as mod

        result = mod.run_discovery({})

        assert result["site_stats"] == {}
        assert result["disabled_sites"] == []


# ---------------------------------------------------------------------------
# _normalize_country
# ---------------------------------------------------------------------------

class TestNormalizeCountry:
    def test_valid_country_lowercased(self):
        assert _normalize_country("UK") == "uk"
        assert _normalize_country("USA") == "usa"
        assert _normalize_country("Worldwide") == "worldwide"

    def test_none_returns_fallback(self):
        assert _normalize_country(None) == "usa"

    def test_empty_string_returns_fallback(self):
        assert _normalize_country("") == "usa"
        assert _normalize_country("   ") == "usa"

    def test_unknown_country_returns_fallback(self, caplog):
        import logging
        with caplog.at_level(logging.WARNING):
            result = _normalize_country("sri lanka")
        assert result == "usa"
        assert "Unsupported country_indeed" in caplog.text

    def test_case_insensitive(self):
        assert _normalize_country("Australia") == "australia"
        assert _normalize_country("CANADA") == "canada"

    def test_strip_whitespace(self):
        assert _normalize_country("  uk  ") == "uk"


# ---------------------------------------------------------------------------
# probe_boards
# ---------------------------------------------------------------------------

# JobSpy's own per-board logger names (created when jobspy is imported).
_JOBSPY_LOGGERS = {"indeed": "Indeed", "linkedin": "LinkedIn", "glassdoor": "Glassdoor",
                   "zip_recruiter": "ZipRecruiter", "google": "Google"}


class TestProbeBoards:
    """probe_boards calls scrape_jobs once per site and classifies each board."""

    def _fake_scrape(self, behaviour):
        """behaviour: site -> int (rows), str (JobSpy error log line), or Exception."""
        import logging

        def fake(**kwargs):
            (site,) = kwargs["site_name"]
            b = behaviour[site]
            if isinstance(b, Exception):
                raise b
            if isinstance(b, str):
                logging.getLogger(f"JobSpy:{_JOBSPY_LOGGERS[site]}").error(b)
                return pd.DataFrame()
            return _make_df({site: b})
        return fake

    def _probe(self, behaviour, **kw):
        from unittest import mock

        import applypilot.discovery.jobspy as mod

        fake = mock.MagicMock(side_effect=self._fake_scrape(behaviour))
        with mock.patch.object(mod, "scrape_jobs", fake):
            out = mod.probe_boards(list(behaviour), **kw)
        return out, fake

    def test_probe_one_call_per_site(self):
        out, fake = self._probe({"indeed": 3, "linkedin": 2})
        assert [c.kwargs["site_name"] for c in fake.call_args_list] == [["indeed"], ["linkedin"]]
        assert all(c.kwargs["results_wanted"] == 3 for c in fake.call_args_list)
        assert [(r.site, r.status, r.rows) for r in out] == [("indeed", "ok", 3), ("linkedin", "ok", 2)]

    def test_probe_403_log_is_blocked(self):
        out, _ = self._probe({
            "zip_recruiter": "ZipRecruiter response status code 403 with response: forbidden aa",
        })
        (r,) = out
        assert r.status == "blocked"
        assert r.rows == 0
        assert "403" in r.detail

    def test_probe_429_log_is_blocked(self):
        out, _ = self._probe({"glassdoor": "429 Response - Blocked by Glassdoor for too many requests"})
        assert out[0].status == "blocked"

    def test_probe_zero_rows_no_log_is_empty(self):
        out, _ = self._probe({"google": 0})
        assert (out[0].status, out[0].detail) == ("empty", "")

    def test_probe_other_log_line_is_error(self):
        out, _ = self._probe({"glassdoor": "Glassdoor: location not parsed"})
        assert out[0].status == "error"
        assert "location not parsed" in out[0].detail

    def test_probe_exception_is_error_and_other_sites_continue(self):
        out, _ = self._probe({"indeed": RuntimeError("boom"), "linkedin": 1})
        assert [(r.site, r.status) for r in out] == [("indeed", "error"), ("linkedin", "ok")]
        assert "boom" in out[0].detail

    def test_probe_handler_removed_after_run(self):
        import logging
        self._probe({"zip_recruiter": "status code 403"})
        assert not any(type(h).__name__ == "_JobSpyErrorCapture"
                       for h in logging.getLogger("JobSpy:ZipRecruiter").handlers)

    def test_probe_remote_location_sets_is_remote(self):
        _, fake = self._probe({"indeed": 1})
        assert fake.call_args.kwargs.get("is_remote") is True
        _, fake = self._probe({"indeed": 1}, location="Austin, TX")
        assert "is_remote" not in fake.call_args.kwargs


class TestDiscoverProbeCli:
    """`applypilot discover --probe` prints one row per configured board."""

    def test_cli_probe_uses_config_sites(self):
        from unittest import mock

        from typer.testing import CliRunner

        from applypilot.cli import app
        from applypilot.discovery.jobspy import BoardHealth

        health = [BoardHealth("indeed", "ok", 3, 0.9), BoardHealth("zip_recruiter", "blocked", 0, 0.4, "status code 403")]
        with mock.patch("applypilot.config.load_env"), \
                mock.patch("applypilot.config.load_search_config",
                           return_value={"sites": ["indeed", "zip_recruiter"]}), \
                mock.patch("applypilot.discovery.jobspy.probe_boards", return_value=health) as probe:
            result = CliRunner().invoke(app, ["discover", "--probe"])
        assert result.exit_code == 0, result.output
        assert probe.call_args.args[0] == ["indeed", "zip_recruiter"]
        assert "indeed" in result.output and "blocked" in result.output

    def test_cli_discover_without_probe_exits_nonzero(self):
        from typer.testing import CliRunner

        from applypilot.cli import app

        result = CliRunner().invoke(app, ["discover"])
        assert result.exit_code == 1
