"""Enrichment, Workday, SmartExtract, and PDF smoke tests.

Cheap tests (no marks) run by default.
Live tests marked @live @expensive — run with: pytest -m live --run-live -v
"""

import pytest

# ---------------------------------------------------------------------------
# Enrichment — Tier 1/2 (no network)
# ---------------------------------------------------------------------------

class TestEnrichmentCheap:
    """Test enrichment functions that don't require network."""

    def test_extract_from_json_ld(self):
        from applypilot.enrichment.detail import extract_from_json_ld

        intel = {
            "json_ld": [
                {
                    "@type": "JobPosting",
                    "description": "<p>We are looking for a software engineer with 5+ years of experience.</p>",
                    "url": "https://example.com/apply",
                    "directApply": True,
                }
            ]
        }

        result = extract_from_json_ld(intel)

        assert result is not None
        assert "full_description" in result
        assert "application_url" in result
        assert len(result["full_description"]) > 50
        assert result["application_url"] == "https://example.com/apply"

    def test_extract_from_json_ld_no_posting(self):
        from applypilot.enrichment.detail import extract_from_json_ld

        intel = {"json_ld": [{"@type": "Organization", "name": "Test Corp"}]}

        result = extract_from_json_ld(intel)
        assert result is None

    def test_clean_description_html(self):
        from applypilot.enrichment.detail import clean_description

        html = "<p>Line 1</p><br><p>Line 2</p><li>Item 1</li><li>Item 2</li>"
        result = clean_description(html)

        assert "Line 1" in result
        assert "Line 2" in result
        assert "- Item 1" in result
        assert "- Item 2" in result

    def test_clean_description_plain_text(self):
        from applypilot.enrichment.detail import clean_description

        text = "Line 1\n\n\nLine 2"
        result = clean_description(text)

        assert "Line 1" in result
        assert "Line 2" in result
        assert "\n\n\n" not in result  # Should collapse triple newlines

    def test_clean_description_empty(self):
        from applypilot.enrichment.detail import clean_description

        assert clean_description("") == ""
        assert clean_description(None) == ""

    def test_resolve_url_absolute(self):
        from applypilot.enrichment.detail import resolve_url

        url = resolve_url("https://example.com/job/123", "indeed")
        assert url == "https://example.com/job/123"

    def test_resolve_url_relative_returns_none_for_unknown_site(self):
        from applypilot.enrichment.detail import resolve_url

        # Relative URL with unknown site returns None (no base URL configured)
        url = resolve_url("/job/123", "indeed")
        assert url is None

    def test_resolve_url_absolute_returns_unchanged(self):
        from applypilot.enrichment.detail import resolve_url

        # Absolute URLs are returned as-is (jsessionid stripping only for relative)
        url = resolve_url("https://example.com/job/123;jsessionid=abc", "indeed")
        assert url == "https://example.com/job/123;jsessionid=abc"


# ---------------------------------------------------------------------------
# Database store/dedup (no network)
# ---------------------------------------------------------------------------

class TestDatabaseStore:
    """Test database store and dedup functions."""

    def test_store_jobs_dedup(self, tmp_path):
        from applypilot.database import get_stats, init_db

        conn = init_db(tmp_path / "test.db")

        # Insert a job
        conn.execute(
            "INSERT OR IGNORE INTO jobs (url, title, site, location) VALUES (?, ?, ?, ?)",
            ("https://example.com/job/1", "Software Engineer", "indeed", "SF"),
        )
        conn.commit()

        # Insert same job again (should dedup)
        conn.execute(
            "INSERT OR IGNORE INTO jobs (url, title, site, location) VALUES (?, ?, ?, ?)",
            ("https://example.com/job/1", "Software Engineer", "indeed", "SF"),
        )
        conn.commit()

        stats = get_stats(conn)
        assert stats["total"] == 1  # Should not duplicate

        conn.close()


# ---------------------------------------------------------------------------
# PDF conversion (cheap — skip if playwright missing)
# ---------------------------------------------------------------------------

class TestPDFCheap:
    """Test PDF conversion without network."""

    def test_convert_to_pdf_text(self, tmp_path):
        pytest.importorskip("playwright")

        from applypilot.scoring.pdf import convert_to_pdf

        text = "Test User\nSan Francisco, CA\n\nEXPERIENCE\nSoftware Engineer"
        output_path = tmp_path / "test_resume.pdf"

        # This may fail if Playwright browsers not installed, but shouldn't crash
        try:
            result = convert_to_pdf(text, str(output_path))
            # If it succeeds, verify the file exists
            if result:
                assert output_path.exists()
        except (RuntimeError, OSError, ImportError) as e:
            # Playwright not installed or browser missing — acceptable
            pytest.skip(f"PDF conversion skipped: {e}")


# ---------------------------------------------------------------------------
# Workday / SmartExtract live (network)
# ---------------------------------------------------------------------------

@pytest.mark.live
@pytest.mark.expensive
class TestDetailPageLive:
    """Test scrape_detail_page with real network."""

    def test_scrape_detail_page(self, tmp_path, monkeypatch):
        from applypilot.enrichment.detail import scrape_detail_page

        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page()
                result = scrape_detail_page(page, "https://example.com/jobs/1")
                browser.close()

                assert isinstance(result, dict)
                assert "full_description" in result
                assert "application_url" in result
                assert "tier_used" in result
                assert result["tier_used"] in {1, 2, 3, None}
        except (TimeoutError, OSError, RuntimeError) as e:
            pytest.xfail(f"Detail page scrape failed (expected): {e}")


@pytest.mark.live
@pytest.mark.expensive
class TestWorkdayLive:
    """Test Workday discovery with real network."""

    def test_workday_smoke(self, tmp_path, monkeypatch):
        from applypilot.discovery.workday import run_workday_discovery

        monkeypatch.setenv("APPLYPILOT_DIR", str(tmp_path))
        (tmp_path / "tailored_resumes").mkdir(exist_ok=True)
        (tmp_path / "cover_letters").mkdir(exist_ok=True)
        (tmp_path / "logs").mkdir(exist_ok=True)

        try:
            # Limited run — may xfail on CAPTCHA
            run_workday_discovery(workers=1)
        except (RuntimeError, OSError) as e:
            # CAPTCHA or network issues are expected
            pytest.xfail(f"Workday failed (expected): {e}")


@pytest.mark.live
@pytest.mark.expensive
class TestSmartExtractLive:
    """Test SmartExtract with real network."""

    def test_smartextract_hackernews(self, tmp_path, monkeypatch):
        from applypilot.discovery.smartextract import _run_one_site, build_scrape_targets

        # Hacker News Jobs is static and reliable
        targets = build_scrape_targets(
            queries=[{"query": "engineer", "tier": 1}],
            locations=[{"label": "remote", "location": "", "remote": True}],
            sites=[{"name": "Hacker News Jobs", "type": "static", "url": "https://news.ycombinator.com/jobs"}],
        )

        if not targets:
            pytest.skip("No targets built")

        try:
            result = _run_one_site(targets[0], no_cache=True)
            # Should not crash
            assert isinstance(result, dict)
        except (RuntimeError, OSError) as e:
            pytest.xfail(f"SmartExtract failed (expected): {e}")
