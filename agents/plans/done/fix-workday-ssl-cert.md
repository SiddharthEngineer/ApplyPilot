# Plan: Fix Workday Scraper SSL Certificate Verification

**Started:** 2026-08-27
**Status:** ✅ Complete

---

## Goal

The Workday scraper fails with `SSL: CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate` when making HTTPS requests to Workday APIs (e.g., `*.myworkdayjobs.com`). This is a common Python issue on macOS where the default `urllib.request` SSL context cannot locate system CA certificates. The fix will configure `urllib` to use the `certifi` CA bundle (already a transitive dependency via `httpx`), enabling successful TLS verification for all Workday employer endpoints.

## Success Criteria

1. Running `applypilot discover workday` (or equivalent CLI command) successfully searches all configured employers without SSL errors
2. The error `<urlopen error [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable to get local issuer certificate (_ssl.c:1028)>` no longer appears in logs
3. Jobs are returned and stored in the database for at least one test employer (e.g., Manulife, Sun Life, Desjardins, Intact Financial from the error log)
4. No regression in existing functionality (proxy support, pagination, detail fetching)
5. Unit test verifies the SSL context is correctly configured with certifi's CA bundle

## Task Chain

### Task 1: Add SSL context configuration to workday.py

**Files:** `src/applypilot/discovery/workday.py` (modify)

**What:** Modify the `_urlopen` function and proxy setup to use an SSL context created with `certifi.where()` as the CA bundle. This involves:
- Importing `ssl` and `certifi` modules
- Creating a module-level `_ssl_context` using `ssl.create_default_context(cafile=certifi.where())`
- Updating `setup_proxy()` to pass the SSL context to the opener via `urllib.request.HTTPSHandler`
- Updating `_urlopen()` to use the SSL context when no proxy is configured

**Acceptance criteria:**
- `certifi` and `ssl` imports are present at top of file
- Module-level `_ssl_context` variable created with `certifi.where()`
- `setup_proxy()` builds opener with `HTTPSHandler(context=_ssl_context)`
- `_urlopen()` passes `context=_ssl_context` to `urllib.request.urlopen()` when no proxy
- Running `python -c "from applypilot.discovery.workday import _ssl_context; print(_ssl_context)"` shows a valid SSLContext with certifi CA file

**Status:** ✅ Complete

### Task 2: Add unit test for SSL context configuration

**Files:** `tests/test_workday_ssl.py` (new)

**What:** Create a new test file to verify the SSL context is properly configured with certifi's CA bundle. Test that:
- The `_ssl_context` variable exists and is an `ssl.SSLContext`
- The context's CA file path matches `certifi.where()`
- The opener created by `setup_proxy(None)` uses the custom SSL context

**Acceptance criteria:**
- Test file created at `tests/test_workday_ssl.py`
- Tests pass with `pytest tests/test_workday_ssl.py -v`
- Test verifies `_ssl_context.get_ca_certs()` returns non-empty list
- Test verifies proxy setup preserves SSL context

**Status:** ✅ Complete

### Task 3: Verify fix with manual integration test

**Files:** None (manual verification)

**What:** Run the workday discovery against a subset of employers that were failing (Manulife, Sun Life, Desjardins, Intact Financial) to confirm SSL errors are resolved and jobs are returned.

**Acceptance criteria:**
- Run `applypilot discover workday --employers manulife,sunlife,desjardins,intact --workers 1` (or equivalent CLI command)
- No SSL certificate errors in output
- At least one employer returns jobs (total > 0)
- Jobs are inserted into the database (verify with `sqlite3` query on jobs table)

**Status:** ✅ Complete — Verified 2026-08-27. All 5 previously-failing employers (Manulife, TD, Sun Life, Desjardins, Intact Financial) return jobs without SSL errors.

---

## Implementation Order

```
Task 1 (SSL Context) → Task 2 (Unit Test) → Task 3 (Integration Verify)
```

1. **Task 1** - Add SSL context configuration to workday.py (core fix)
2. **Task 2** - Add unit test for SSL context (verification)
3. **Task 3** - Manual integration test against failing employers (validation)

---

## Key Design Decisions

1. **Use `certifi` via existing `httpx` dependency** — `httpx>=0.24` already depends on `certifi`, so no new dependencies needed. This avoids adding `certifi` explicitly to pyproject.toml.

2. **Module-level SSL context** — Creating a single `_ssl_context` at module load ensures consistent TLS configuration across all requests and avoids recreating the context per request.

3. **Preserve proxy support** — The fix must work with both direct connections and proxy configurations. `urllib.request.HTTPSHandler(context=...)` allows injecting the custom SSL context into the opener chain.

4. **Minimal code change** — Only modify the HTTP transport layer (`setup_proxy`, `_urlopen`) without changing the higher-level API functions (`workday_search`, `workday_detail`, etc.).

---

## Historical Record

- 2026-08-27: Plan created, analyzing SSL certificate verification failure in workday scraper
- 2026-08-27: Task 1 completed — Added `_ssl_context` with certifi CA bundle to `workday.py`, updated `setup_proxy()` and `_urlopen()` to use it
- 2026-08-27: Task 2 completed — Created `tests/test_workday_ssl.py` with 5 tests, all passing, lint clean
- 2026-08-27: Task 3 completed — Integration test verified against Manulife (62 results), TD (93), Sun Life (18), Desjardins (2), Intact Financial (28). All returned jobs without SSL errors.