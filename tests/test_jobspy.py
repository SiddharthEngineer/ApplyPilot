"""Tests for jobspy.py site counting and site tracker."""

import pandas as pd
import pytest

from applypilot.discovery.jobspy import _SiteTracker, _site_counts


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
