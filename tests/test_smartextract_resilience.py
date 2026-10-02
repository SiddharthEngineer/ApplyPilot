"""SmartExtract keeps going when individual sites fail (job-board-discovery-repair Task 6)."""

from unittest.mock import MagicMock, patch

import applypilot.discovery.smartextract as se

TARGETS = [
    {"name": "Broken", "url": "https://broken.example/jobs", "query": None},
    {"name": "Good", "url": "https://good.example/jobs", "query": None},
]

GOOD = {"name": "Good", "status": "PASS", "strategy": "json_ld", "total": 1, "titles": 1,
        "jobs": [{"url": "https://good.example/jobs/1", "title": "Data Scientist", "location": "Remote"}]}


def _run_all_with(side_effect):
    conn = MagicMock()
    with patch.object(se, "init_db", return_value=conn), \
            patch.object(se, "get_stats", return_value={"total": 0, "pending_detail": 0}), \
            patch.object(se, "_run_one_site", side_effect=side_effect) as one, \
            patch.object(se, "_store_jobs_filtered", return_value=(1, 0)) as store:
        out = se._run_all(TARGETS, ["Remote"], [])
    return out, one, store


def test_site_exception_does_not_stop_run():
    out, one, store = _run_all_with([TimeoutError("Timeout 30000ms exceeded."), GOOD])
    assert one.call_count == 2
    assert out["total"] == 2 and out["passed"] == 1 and out["total_new"] == 1
    store.assert_called_once()


def test_missing_browser_stops_early():
    err = Exception("BrowserType.launch: Executable doesn't exist at /root/.cache/ms-playwright/x")
    out, one, store = _run_all_with([err, GOOD])
    assert one.call_count == 1
    assert out["passed"] == 0
    store.assert_not_called()


def test_safe_wrapper_reports_error():
    with patch.object(se, "_run_one_site", side_effect=RuntimeError("net::ERR_HTTP2_PROTOCOL_ERROR")):
        r = se._run_one_site_safe("X", "https://x.example")
    assert r["status"] == "ERROR" and "ERR_HTTP2_PROTOCOL_ERROR" in r["error"] and not r.get("fatal")


def test_load_sites_skips_disabled(tmp_path, monkeypatch):
    (tmp_path / "sites.yaml").write_text(
        "sites:\n"
        "  - {name: A, url: 'https://a.example', type: static}\n"
        "  - {name: B, url: 'https://b.example', type: static, disabled: true, disabled_reason: dead URL}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(se, "CONFIG_DIR", tmp_path)
    assert [s["name"] for s in se.load_sites()] == ["A"]


def test_shipped_sites_have_reason_when_disabled():
    import yaml

    data = yaml.safe_load((se.CONFIG_DIR / "sites.yaml").read_text(encoding="utf-8"))
    for s in data["sites"]:
        if s.get("disabled"):
            assert s.get("disabled_reason"), f"{s['name']} is disabled without a disabled_reason"


def test_networkidle_timeout_uses_loaded_page():
    page = MagicMock()
    page.wait_for_load_state.side_effect = se.PlaywrightTimeoutError("Timeout 15000ms exceeded.")
    page.title.return_value = "Jobs"
    page.query_selector_all.return_value = []
    page.query_selector.return_value = None
    page.evaluate.return_value = []
    page.content.return_value = "<html><body>jobs</body></html>"
    browser = MagicMock()
    browser.new_page.return_value = page
    pw = MagicMock()
    pw.chromium.launch.return_value = browser
    ctx = MagicMock()
    ctx.__enter__.return_value = pw
    with patch.object(se, "sync_playwright", return_value=ctx):
        intel = se.collect_page_intelligence("https://slow.example/jobs")
    assert intel["page_title"] == "Jobs"
    page.wait_for_load_state.assert_called_once_with("networkidle", timeout=se._NETWORKIDLE_TIMEOUT_MS)


def test_headful_retry_failure_keeps_headless_result():
    small = {"url": "https://tiny.example", "full_html": "<html>tiny</html>", "json_ld": [], "api_responses": [],
             "data_testids": [], "next_data": None,
             "card_candidates": [], "page_title": "t"}
    calls = []

    def collect(url, headless=True):
        calls.append(headless)
        if not headless:
            raise RuntimeError("Missing X server or $DISPLAY")
        return dict(small)

    with patch.object(se, "collect_page_intelligence", side_effect=collect), \
            patch.object(se, "ask_llm", return_value=('{"strategy": "json_ld", "extraction": {}}', 0.1,
                                                      {"response_chars": 40})), \
            patch.object(se, "execute_json_ld", return_value=[]), \
            patch.object(se, "_strategy_cache_enabled", False):
        r = se._run_one_site("Tiny", "https://tiny.example")
    assert calls == [True, False]
    assert r["status"] == "FAIL"  # ran to completion on the headless result
