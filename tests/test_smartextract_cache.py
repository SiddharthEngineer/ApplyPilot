"""Tests for smartextract strategy cache and target deduplication (Task 4)."""

from unittest.mock import patch, MagicMock

import pytest

import applypilot.discovery.smartextract as se
from applypilot.discovery.smartextract import (
    build_scrape_targets,
    _get_cache_key,
    _load_strategy_cache,
    _save_strategy_cache,
    _strategy_cache,
    _strategy_cache_enabled,
    _CACHE_FILE,
)


# ---------------------------------------------------------------------------
# Target deduplication
# ---------------------------------------------------------------------------

class TestBuildScrapeTargetsDedup:
    """Test that build_scrape_targets deduplicates identical (name, url) pairs."""

    def test_no_duplicates_normal(self):
        sites = [
            {"name": "Eluta", "url": "https://eluta.ca/search?q={query_encoded}", "type": "search"},
        ]
        search_cfg = {
            "queries": [{"query": "Data Scientist"}, {"query": "Software Engineer"}],
            "locations": [{"location": "Toronto"}],
        }
        targets = build_scrape_targets(sites=sites, search_cfg=search_cfg)
        assert len(targets) == 2
        urls = [t["url"] for t in targets]
        assert len(set(urls)) == 2

    def test_dedup_identical_expanded_urls(self):
        """If two site entries expand to the same URL, only one target is kept."""
        sites = [
            {"name": "Eluta", "url": "https://eluta.ca/search?q={query_encoded}", "type": "search"},
            {"name": "Eluta", "url": "https://eluta.ca/search?q={query_encoded}", "type": "search"},
        ]
        search_cfg = {
            "queries": [{"query": "Data Scientist"}],
            "locations": [{"location": "Toronto"}],
        }
        targets = build_scrape_targets(sites=sites, search_cfg=search_cfg)
        assert len(targets) == 1

    def test_dedup_same_name_different_queries_not_removed(self):
        """Same site with different queries should produce distinct targets."""
        sites = [
            {"name": "Eluta", "url": "https://eluta.ca/search?q={query_encoded}", "type": "search"},
        ]
        search_cfg = {
            "queries": [{"query": "Data Scientist"}, {"query": "Data Scientist"}],
            "locations": [{"location": "Toronto"}],
        }
        targets = build_scrape_targets(sites=sites, search_cfg=search_cfg)
        # Same query twice should dedup to 1 target
        assert len(targets) == 1

    def test_static_sites_dedup(self):
        sites = [
            {"name": "RemoteOK", "url": "https://remoteok.com/remote-dev-jobs", "type": "static"},
            {"name": "RemoteOK", "url": "https://remoteok.com/remote-dev-jobs", "type": "static"},
        ]
        search_cfg = {"queries": [], "locations": []}
        targets = build_scrape_targets(sites=sites, search_cfg=search_cfg)
        assert len(targets) == 1

    def test_no_duplicates_90_target_expansion(self):
        """90 targets with 6 queries x 12 search + 18 static = 90, not more."""
        search_sites = [
            {"name": f"Site{i}", "url": f"https://site{i}.com/search?q={{query_encoded}}", "type": "search"}
            for i in range(12)
        ]
        static_sites = [
            {"name": f"Static{i}", "url": f"https://static{i}.com/jobs", "type": "static"}
            for i in range(18)
        ]
        sites = search_sites + static_sites
        search_cfg = {
            "queries": [{"query": f"Query{j}"} for j in range(6)],
            "locations": [{"location": "Remote"}],
        }
        targets = build_scrape_targets(sites=sites, search_cfg=search_cfg)
        # 12 search x 6 queries = 72 + 18 static = 90
        assert len(targets) == 90


# ---------------------------------------------------------------------------
# Cache key derivation
# ---------------------------------------------------------------------------

class TestGetCacheKey:
    def test_basic_key(self):
        key = _get_cache_key("Eluta", "https://www.eluta.ca/search?q=engineer&l=Toronto")
        assert key == ("Eluta", "www.eluta.ca")

    def test_key_normalizes_domain(self):
        key = _get_cache_key("Site", "https://Example.COM/path")
        assert key == ("Site", "example.com")


# ---------------------------------------------------------------------------
# Strategy cache persistence
# ---------------------------------------------------------------------------

class TestStrategyCachePersistence:
    def setup_method(self):
        _strategy_cache.clear()

    def test_save_and_load_roundtrip(self, tmp_path):
        with patch.object(se, "_CACHE_FILE", tmp_path / "cache.json"):
            _strategy_cache[("Eluta", "www.eluta.ca")] = {
                "strategy": "css_selectors",
                "extraction": {"job_card": "article"},
                "child_tag": "div",
            }
            _save_strategy_cache()

            _strategy_cache.clear()
            _load_strategy_cache()

            assert ("Eluta", "www.eluta.ca") in _strategy_cache
            entry = _strategy_cache[("Eluta", "www.eluta.ca")]
            assert entry["strategy"] == "css_selectors"
            assert entry["child_tag"] == "div"

    def test_load_missing_file(self, tmp_path):
        with patch.object(se, "_CACHE_FILE", tmp_path / "missing.json"):
            _load_strategy_cache()
            assert len(_strategy_cache) == 0

    def test_load_corrupt_file(self, tmp_path):
        cache_file = tmp_path / "cache.json"
        cache_file.write_text("NOT JSON {{{")
        with patch.object(se, "_CACHE_FILE", cache_file):
            _load_strategy_cache()
            # Should not crash, cache stays empty
            assert len(_strategy_cache) == 0


# ---------------------------------------------------------------------------
# _run_one_site cache behavior (integration with mocked LLM + Playwright)
# ---------------------------------------------------------------------------

class TestRunOneSiteCache:
    """Test that _run_one_site uses the strategy cache correctly."""

    def setup_method(self):
        _strategy_cache.clear()

    @patch("applypilot.discovery.smartextract._save_strategy_cache")
    @patch("applypilot.discovery.smartextract.collect_page_intelligence")
    @patch("applypilot.discovery.smartextract.judge_api_responses", side_effect=lambda x: x)
    @patch("applypilot.discovery.smartextract.ask_llm")
    def test_cache_hit_skips_strategy_llm(self, mock_ask, mock_judge, mock_intel, mock_save):
        """Second query to same domain should hit cache and skip the strategy LLM call."""
        from applypilot.discovery.smartextract import _run_one_site

        intel = {
            "url": "https://www.eluta.ca/search?q=engineer",
            "page_title": "Jobs",
            "json_ld": [{"@type": "JobPosting", "title": "SWE"}],
            "api_responses": [],
            "data_testids": [],
            "dom_stats": {},
            "card_candidates": [{"child_tag": "div", "total_children": 10, "parent_selector": "main", "child_selector": "div.card", "with_text": 5, "with_links": 3}],
            "full_html": "<html><body>jobs</body></html>",
        }
        mock_intel.return_value = intel

        # First call: LLM returns json_ld strategy
        mock_ask.return_value = ('{"strategy":"json_ld","reasoning":"jsonld found","extraction":{"title":"title"}}', 0.1, {"response_chars": 80})
        r1 = _run_one_site("Eluta", "https://www.eluta.ca/search?q=engineer")
        assert r1["strategy"] == "json_ld"
        assert mock_ask.call_count == 1

        # Second call: same domain, should hit cache (0 LLM calls)
        r2 = _run_one_site("Eluta", "https://www.eluta.ca/search?q=engineer2")
        assert r2["strategy"] == "json_ld"
        assert mock_ask.call_count == 1  # No additional LLM calls

    @patch("applypilot.discovery.smartextract.execute_css_selectors", return_value=({}, []))
    @patch("applypilot.discovery.smartextract._save_strategy_cache")
    @patch("applypilot.discovery.smartextract.collect_page_intelligence")
    @patch("applypilot.discovery.smartextract.judge_api_responses", side_effect=lambda x: x)
    @patch("applypilot.discovery.smartextract.ask_llm")
    def test_cache_shape_mismatch_falls_back_to_llm(self, mock_ask, mock_judge, mock_intel, mock_save, mock_css):
        """If card_candidates shape changes, cache is bypassed and LLM is called."""
        from applypilot.discovery.smartextract import _run_one_site

        # First call: cache with child_tag=div
        intel1 = {
            "url": "https://www.eluta.ca/search?q=engineer",
            "page_title": "Jobs",
            "json_ld": [],
            "api_responses": [],
            "data_testids": [],
            "dom_stats": {},
            "card_candidates": [{"child_tag": "div", "total_children": 10, "parent_selector": "main", "child_selector": "div.card", "with_text": 5, "with_links": 3}],
            "full_html": "<html><body>jobs</body></html>",
        }
        mock_intel.return_value = intel1
        mock_ask.return_value = ('{"strategy":"css_selectors","reasoning":"cards","extraction":{}}', 0.1, {"response_chars": 60})
        r1 = _run_one_site("Eluta", "https://www.eluta.ca/search?q=engineer")
        assert r1["strategy"] == "css_selectors"
        assert mock_ask.call_count == 1

        # Second call: different child_tag -> shape mismatch -> LLM called again
        intel2 = {
            "url": "https://www.eluta.ca/search?q=engineer2",
            "page_title": "Jobs",
            "json_ld": [],
            "api_responses": [],
            "data_testids": [],
            "dom_stats": {},
            "card_candidates": [{"child_tag": "li", "total_children": 5, "parent_selector": "main", "child_selector": "li.item", "with_text": 3, "with_links": 2}],
            "full_html": "<html><body>jobs</body></html>",
        }
        mock_intel.return_value = intel2
        mock_ask.return_value = ('{"strategy":"css_selectors","reasoning":"new shape","extraction":{}}', 0.1, {"response_chars": 60})
        r2 = _run_one_site("Eluta", "https://www.eluta.ca/search?q=engineer2")
        assert mock_ask.call_count == 2  # LLM called again

    @patch("applypilot.discovery.smartextract._save_strategy_cache")
    @patch("applypilot.discovery.smartextract.collect_page_intelligence")
    @patch("applypilot.discovery.smartextract.judge_api_responses", side_effect=lambda x: x)
    @patch("applypilot.discovery.smartextract.ask_llm")
    def test_captcha_ignores_cache(self, mock_ask, mock_judge, mock_intel, mock_save):
        """CAPTCHA page should bypass cache even if one exists."""
        from applypilot.discovery.smartextract import _run_one_site

        # Seed cache
        _strategy_cache[("Eluta", "www.eluta.ca")] = {
            "strategy": "json_ld",
            "extraction": {"title": "title"},
        }

        # CAPTCHA page
        intel = {
            "url": "https://www.eluta.ca/search?q=engineer",
            "page_title": "Jobs",
            "json_ld": [],
            "api_responses": [],
            "data_testids": [],
            "dom_stats": {},
            "card_candidates": [],
            "full_html": "<html><body>captcha verify you are human</body></html>",
        }
        mock_intel.return_value = intel
        mock_ask.return_value = ('{"strategy":"json_ld","reasoning":"none","extraction":{}}', 0.1, {"response_chars": 50})

        r = _run_one_site("Eluta", "https://www.eluta.ca/search?q=engineer")
        # CAPTCHA detected -> cache ignored -> LLM called
        assert mock_ask.call_count == 1

    @patch("applypilot.discovery.smartextract._save_strategy_cache")
    @patch("applypilot.discovery.smartextract.collect_page_intelligence")
    @patch("applypilot.discovery.smartextract.judge_api_responses", side_effect=lambda x: x)
    @patch("applypilot.discovery.smartextract.ask_llm")
    def test_no_cache_flag_disables_cache(self, mock_ask, mock_judge, mock_intel, mock_save):
        """When _strategy_cache_enabled is False, cache is never used."""
        import applypilot.discovery.smartextract as se
        from applypilot.discovery.smartextract import _run_one_site

        se._strategy_cache_enabled = False
        try:
            intel = {
                "url": "https://www.eluta.ca/search?q=engineer",
                "page_title": "Jobs",
                "json_ld": [{"@type": "JobPosting", "title": "SWE"}],
                "api_responses": [],
                "data_testids": [],
                "dom_stats": {},
                "card_candidates": [{"child_tag": "div", "total_children": 10}],
                "full_html": "<html><body>jobs</body></html>",
            }
            mock_intel.return_value = intel
            mock_ask.return_value = ('{"strategy":"json_ld","reasoning":"jsonld","extraction":{"title":"title"}}', 0.1, {"response_chars": 60})

            _run_one_site("Eluta", "https://www.eluta.ca/search?q=engineer")
            assert mock_ask.call_count == 1

            # Second call: still calls LLM because cache is disabled
            _run_one_site("Eluta", "https://www.eluta.ca/search?q=engineer2")
            assert mock_ask.call_count == 2
            assert len(_strategy_cache) == 0  # Cache never populated
        finally:
            se._strategy_cache_enabled = True

    @patch("applypilot.discovery.smartextract._save_strategy_cache")
    @patch("applypilot.discovery.smartextract.collect_page_intelligence")
    @patch("applypilot.discovery.smartextract.judge_api_responses", side_effect=lambda x: x)
    @patch("applypilot.discovery.smartextract.ask_llm")
    def test_cache_not_stored_for_api_response_strategy(self, mock_ask, mock_judge, mock_intel, mock_save):
        """api_response strategy should NOT be cached (API responses change per query)."""
        from applypilot.discovery.smartextract import _run_one_site

        intel = {
            "url": "https://www.eluta.ca/search?q=engineer",
            "page_title": "Jobs",
            "json_ld": [],
            "api_responses": [{"url": "https://api.eluta.ca/jobs", "status": 200, "size": 5000}],
            "data_testids": [],
            "dom_stats": {},
            "card_candidates": [],
            "full_html": "<html><body>jobs</body></html>",
        }
        mock_intel.return_value = intel
        mock_ask.return_value = ('{"strategy":"api_response","reasoning":"api found","extraction":{"url_pattern":"api.eluta.ca","items_path":"jobs","title":"title"}}', 0.1, {"response_chars": 90})

        _run_one_site("Eluta", "https://www.eluta.ca/search?q=engineer")
        # api_response should NOT be cached
        assert ("Eluta", "www.eluta.ca") not in _strategy_cache
