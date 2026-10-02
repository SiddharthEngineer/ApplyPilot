"""JobSpy-based job discovery: searches Indeed, LinkedIn, Glassdoor, ZipRecruiter.

Uses python-jobspy to scrape multiple job boards, deduplicates results,
parses salary ranges, and stores everything in the ApplyPilot database.

Search queries, locations, and filtering rules are loaded from the user's
search configuration YAML (searches.yaml) rather than being hardcoded.
"""

import logging
import os
import re
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone

import pandas as pd
from jobspy import scrape_jobs

from applypilot import config
from applypilot.database import get_connection, init_db, store_jobs

log = logging.getLogger(__name__)

# Boards crawled when searches.yaml has no ``sites`` list.
DEFAULT_SITES = ("indeed", "linkedin", "zip_recruiter")

# Boards that answer HTTP 403 (Cloudflare) unless requests go through a proxy.
_REQUIRES_PROXY = frozenset({"glassdoor", "zip_recruiter"})
# Boards JobSpy can't currently scrape (Google returns no job data).
_UNSUPPORTED = frozenset({"google"})
# Lowest allowed site_fail_threshold: 1 lets a single throttled search disable a board.
_MIN_FAIL_THRESHOLD = 2

# JobSpy supported country codes for Indeed
_SUPPORTED_COUNTRIES = frozenset({
    "usa", "uk", "canada", "australia", "germany", "india", "france",
    "spain", "italy", "brazil", "mexico", "netherlands", "switzerland",
    "sweden", "norway", "denmark", "finland", "ireland", "new zealand",
    "singapore", "south africa", "poland", "portugal", "belgium",
    "austria", "argentina", "chile", "colombia", "peru", "japan",
    "south korea", "taiwan", "hong kong", "malaysia", "indonesia",
    "philippines", "thailand", "vietnam", "turkey", "united arab emirates",
    "saudi arabia", "israel", "egypt", "nigeria", "pakistan", "bangladesh",
    "romania", "czech republic", "hungary", "greece", "ukraine", "worldwide",
})

_COUNTRY_FALLBACK = "usa"


def _normalize_country(raw: str | None) -> str:
    """Validate *raw* against JobSpy's supported country list.

    Returns the normalised (lower-cased) country string if valid.
    If *raw* is ``None``, empty, or not in the allow-list a warning is
    logged and the fallback (``"usa"``) is returned so that crawls remain
    fail-open.
    """
    if not raw:
        return _COUNTRY_FALLBACK

    normalised = raw.strip().lower()
    if normalised in _SUPPORTED_COUNTRIES:
        return normalised

    log.warning(
        "Unsupported country_indeed %r — falling back to %r. "
        "Supported: %s",
        raw, _COUNTRY_FALLBACK,
        ", ".join(sorted(_SUPPORTED_COUNTRIES)),
    )
    return _COUNTRY_FALLBACK


# -- Proxy parsing -----------------------------------------------------------

def parse_proxy(proxy_str: str) -> dict:
    """Parse host:port:user:pass into components."""
    parts = proxy_str.split(":")
    if len(parts) == 4:
        host, port, user, passwd = parts
        return {
            "host": host,
            "port": port,
            "user": user,
            "pass": passwd,
            "jobspy": f"{user}:{passwd}@{host}:{port}",
            "playwright": {
                "server": f"http://{host}:{port}",
                "username": user,
                "password": passwd,
            },
        }
    elif len(parts) == 2:
        host, port = parts
        return {
            "host": host,
            "port": port,
            "user": None,
            "pass": None,
            "jobspy": f"{host}:{port}",
            "playwright": {"server": f"http://{host}:{port}"},
        }
    else:
        raise ValueError(
            f"Proxy format not recognized: {proxy_str}. "
            f"Expected: host:port:user:pass or host:port"
        )


# -- Retry wrapper -----------------------------------------------------------

def _scrape_with_retry(kwargs: dict, max_retries: int = 2, backoff: float = 5.0):
    """Call scrape_jobs with retry on transient failures."""
    for attempt in range(max_retries + 1):
        try:
            return scrape_jobs(**kwargs)
        except Exception as e:
            err = str(e).lower()
            transient = any(k in err for k in ("timeout", "429", "proxy", "connection", "reset", "refused"))
            if transient and attempt < max_retries:
                wait = backoff * (attempt + 1)
                log.warning("Retry %d/%d in %.0fs: %s", attempt + 1, max_retries, wait, e)
                time.sleep(wait)
            else:
                raise


# -- JobSpy error capture ----------------------------------------------------

# JobSpy logs per-board HTTP failures (e.g. Cloudflare 403s) to its own
# non-propagating ``JobSpy:<Board>`` loggers instead of raising them.
# Lines a board logs when it simply found nothing (not a failure).
_NO_RESULTS_RE = re.compile(r"no job", re.IGNORECASE)
_BLOCKED_RE = re.compile(r"status code:? ?(400|401|403|429)\b|\b429 Response|Blocked by", re.IGNORECASE)


class _JobSpyErrorCapture(logging.Handler):
    """Collects WARNING+ messages emitted by JobSpy's per-board loggers."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())

    @property
    def blocked(self) -> str | None:
        """First captured message that indicates the board refused the request."""
        return next((m for m in self.messages if _BLOCKED_RE.search(m)), None)


@contextmanager
def _capture_jobspy_errors():
    """Attach a :class:`_JobSpyErrorCapture` to every ``JobSpy:*`` logger for the block."""
    handler = _JobSpyErrorCapture()
    names = [n for n in logging.root.manager.loggerDict if n.startswith("JobSpy")]
    loggers = [logging.getLogger(n) for n in names]
    for lg in loggers:
        lg.addHandler(handler)
    try:
        yield handler
    finally:
        for lg in loggers:
            lg.removeHandler(handler)


# -- Board health probe ------------------------------------------------------

@dataclass
class BoardHealth:
    """Result of probing one job board with a tiny search."""

    site: str
    status: str  # ok | empty | blocked | error
    rows: int
    latency_s: float
    detail: str = ""


def probe_boards(
    sites: list[str],
    query: str = "Software Engineer",
    location: str = "Remote",
    proxy: str | None = None,
    country_indeed: str = "usa",
    include_ats: bool = False,
) -> list[BoardHealth]:
    """Run a 3-result search against each board separately and classify it.

    No DB writes. A board that logs an HTTP 400/401/403/429 is ``blocked``,
    an exception is ``error``, and 0 rows with nothing logged is ``empty``.
    With ``include_ats``, also adds one ``ats:<kind>`` row per ATS kind
    (the first board of that kind in ats_boards.yaml; rows = its open jobs).
    """
    proxy_config = parse_proxy(proxy) if proxy else None
    results: list[BoardHealth] = []
    for site in sites:
        kwargs = {
            "site_name": [site],
            "search_term": query,
            "location": location,
            "results_wanted": 3,
            "description_format": "markdown",
            "country_indeed": _normalize_country(country_indeed),
            "verbose": 0,
        }
        if location.strip().lower() == "remote":
            kwargs["is_remote"] = True
        if proxy_config:
            kwargs["proxies"] = [proxy_config["jobspy"]]

        start = time.monotonic()
        with _capture_jobspy_errors() as cap:
            try:
                df = scrape_jobs(**kwargs)
            except Exception as e:  # noqa: BLE001 - any failure is reported, not raised
                results.append(BoardHealth(site, "error", 0, time.monotonic() - start, str(e)[:200]))
                continue
        latency = time.monotonic() - start
        rows = 0 if df is None else len(df)

        if rows:
            status, detail = "ok", cap.blocked or ""
        elif cap.blocked:
            status, detail = "blocked", cap.blocked
        elif errors := [m for m in cap.messages if not _NO_RESULTS_RE.search(m)]:
            status, detail = "error", errors[0]
        else:
            status, detail = "empty", ""
        results.append(BoardHealth(site, status, rows, latency, detail[:200]))
    if include_ats:
        results.extend(_probe_ats())
    return results


def _probe_ats() -> list[BoardHealth]:
    """One health row per ATS kind, from the first configured board of that kind."""
    import httpx

    from applypilot.discovery.ats_boards import fetch_board, load_ats_boards

    first: dict[str, dict] = {}
    for b in load_ats_boards():
        first.setdefault(b["kind"], b)
    results = []
    for kind, b in first.items():
        name = f"ats:{kind}"
        start = time.monotonic()
        try:
            rows = len(fetch_board(kind, b["slug"], company=b.get("name")))
        except httpx.HTTPStatusError as e:
            code = e.response.status_code
            status = "blocked" if code in (401, 403, 429) else "error"
            results.append(BoardHealth(name, status, 0, time.monotonic() - start, f"{b['slug']}: HTTP {code}"))
            continue
        except (httpx.HTTPError, ValueError) as e:
            results.append(BoardHealth(name, "error", 0, time.monotonic() - start, f"{b['slug']}: {e}"[:200]))
            continue
        results.append(BoardHealth(name, "ok" if rows else "empty", rows, time.monotonic() - start, b["slug"]))
    return results


# -- Location filtering ------------------------------------------------------

def _load_location_config(search_cfg: dict) -> tuple[list[str], list[str]]:
    """Extract accept/reject location lists from search config.

    Falls back to sensible defaults if not defined in the YAML.
    """
    accept = search_cfg.get("location_accept", [])
    reject = search_cfg.get("location_reject_non_remote", [])
    return accept, reject


def _location_ok(location: str | None, accept: list[str], reject: list[str]) -> bool:
    """Check if a job location passes the user's location filter.

    Remote jobs are always accepted. Non-remote jobs must match an accept
    pattern and not match a reject pattern.
    """
    if not location:
        return True  # unknown location -- keep it, let scorer decide

    loc = location.lower()

    # Remote jobs always OK
    if any(r in loc for r in ("remote", "anywhere", "work from home", "wfh", "distributed")):
        return True

    # Reject non-remote matches
    for r in reject:
        if r.lower() in loc:
            return False

    # Accept matches
    for a in accept:
        if a.lower() in loc:
            return True

    # No match -- reject unknown
    return False


# -- DB storage (JobSpy DataFrame -> SQLite) ---------------------------------

def store_jobspy_results(conn: sqlite3.Connection, df, source_label: str) -> tuple[int, int]:
    """Store JobSpy DataFrame results into the DB. Returns (new, existing)."""
    now = datetime.now(timezone.utc).isoformat()
    new = 0
    existing = 0

    for _, row in df.iterrows():
        url = str(row.get("job_url", ""))
        if not url or url == "nan":
            continue

        title = str(row.get("title", "")) if str(row.get("title", "")) != "nan" else None
        company = str(row.get("company", "")) if str(row.get("company", "")) != "nan" else None
        location_str = str(row.get("location", "")) if str(row.get("location", "")) != "nan" else None

        # Build salary string from min/max
        salary = None
        min_amt = row.get("min_amount")
        max_amt = row.get("max_amount")
        interval = str(row.get("interval", "")) if str(row.get("interval", "")) != "nan" else ""
        currency = str(row.get("currency", "")) if str(row.get("currency", "")) != "nan" else ""
        if min_amt and str(min_amt) != "nan":
            if max_amt and str(max_amt) != "nan":
                salary = f"{currency}{int(float(min_amt)):,}-{currency}{int(float(max_amt)):,}"
            else:
                salary = f"{currency}{int(float(min_amt)):,}"
            if interval:
                salary += f"/{interval}"

        description = str(row.get("description", "")) if str(row.get("description", "")) != "nan" else None
        site_name = str(row.get("site", source_label))
        is_remote = row.get("is_remote", False)

        site_label = f"{site_name}"
        if is_remote:
            location_str = f"{location_str} (Remote)" if location_str else "Remote"

        strategy = "jobspy"

        # If JobSpy gave us a full description, promote it directly
        full_description = None
        detail_scraped_at = None
        if description and len(description) > 200:
            full_description = description
            detail_scraped_at = now

        # Extract apply URL if JobSpy provided it
        apply_url = str(row.get("job_url_direct", "")) if str(row.get("job_url_direct", "")) != "nan" else None

        try:
            conn.execute(
                "INSERT INTO jobs (url, title, salary, description, location, site, strategy, discovered_at, "
                "full_description, application_url, detail_scraped_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (url, title, salary, description, location_str, site_label, strategy, now,
                 full_description, apply_url, detail_scraped_at),
            )
            new += 1
        except sqlite3.IntegrityError:
            existing += 1

    conn.commit()
    return new, existing


# -- Single search execution -------------------------------------------------

def _run_one_search(
    search: dict,
    sites: list[str],
    results_per_site: int,
    hours_old: int,
    proxy_config: dict | None,
    defaults: dict,
    max_retries: int,
    accept_locs: list[str],
    reject_locs: list[str],
    glassdoor_map: dict,
) -> dict:
    """Run a single search query and store results in DB."""
    s = search
    label = f"\"{s['query']}\" in {s['location']} {'(remote)' if s.get('remote') else ''}"
    if "tier" in s:
        label += f" [tier {s['tier']}]"

    # Glassdoor needs a simplified location; the other boards use the original.
    gd_location = glassdoor_map.get(s["location"], s["location"].split(",")[0])

    # One call per board, so a failure or block is attributed to the right board.
    all_dfs = []
    counts: dict[str, int] = {}
    blocked: dict[str, str] = {}
    failed: list[str] = []
    for site in sites:
        kwargs = {
            "site_name": [site],
            "search_term": s["query"],
            "location": gd_location if site == "glassdoor" else s["location"],
            "results_wanted": results_per_site,
            "hours_old": hours_old,
            "description_format": "markdown",
            "verbose": 0,
        }
        if site != "glassdoor":
            kwargs["country_indeed"] = _normalize_country(defaults.get("country_indeed"))
        if s.get("remote"):
            kwargs["is_remote"] = True
        if proxy_config:
            kwargs["proxies"] = [proxy_config["jobspy"]]
        if site == "linkedin":
            # Off by default: enrich fetches full descriptions, and the per-job
            # LinkedIn fetch is what triggers its throttling.
            kwargs["linkedin_fetch_description"] = bool(defaults.get("linkedin_fetch_description", False))
        with _capture_jobspy_errors() as cap:
            try:
                site_df = _scrape_with_retry(kwargs, max_retries=max_retries)
            except Exception as e:
                log.error("[%s] (%s): %s", label, site, e)
                failed.append(site)
                continue
        counts[site] = _site_counts(site_df, [site])[site] if site_df is not None else 0
        if counts[site] == 0 and cap.blocked:
            blocked[site] = cap.blocked
        if site_df is not None and len(site_df):
            all_dfs.append(site_df)

    base = {"label": label, "sites": {site: counts.get(site, 0) for site in sites},
            "blocked": blocked, "failed": failed, "errors": len(failed)}

    if not all_dfs:
        if failed and len(failed) == len(sites):
            log.error("[%s]: all sites failed", label)
        else:
            log.info("[%s] 0 results", label)
        return {"new": 0, "existing": 0, "filtered": 0, "total": 0, **base}

    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FutureWarning)
        df = pd.concat(all_dfs, ignore_index=True) if len(all_dfs) > 1 else all_dfs[0]

    # Filter by location before storing
    before = len(df)
    df = df[df.apply(lambda row: _location_ok(
        str(row.get("location", "")) if str(row.get("location", "")) != "nan" else None,
        accept_locs, reject_locs,
    ), axis=1)]
    filtered = before - len(df)

    conn = get_connection()
    new, existing = store_jobspy_results(conn, df, s["query"])

    msg = f"[{label}] {before} results -> {new} new, {existing} dupes"
    if filtered:
        msg += f", {filtered} filtered (location)"
    log.info(msg)

    return {"new": new, "existing": existing, "filtered": filtered, "total": before, **base}


# -- Single query search -----------------------------------------------------

def search_jobs(
    query: str,
    location: str,
    sites: list[str] | None = None,
    remote_only: bool = False,
    results_per_site: int = 50,
    hours_old: int = 72,
    proxy: str | None = None,
    country_indeed: str = "usa",
    conn: sqlite3.Connection | None = None,
) -> dict:
    """Run a single job search via JobSpy and store results in DB."""
    if sites is None:
        sites = ["indeed", "linkedin", "zip_recruiter"]

    proxy_config = parse_proxy(proxy) if proxy else None

    log.info("Search: \"%s\" in %s | sites=%s | remote=%s", query, location, sites, remote_only)

    kwargs = {
        "site_name": sites,
        "search_term": query,
        "location": location,
        "results_wanted": results_per_site,
        "hours_old": hours_old,
        "description_format": "markdown",
        "country_indeed": _normalize_country(country_indeed),
        "verbose": 2,
    }

    if remote_only:
        kwargs["is_remote"] = True

    if proxy_config:
        kwargs["proxies"] = [proxy_config["jobspy"]]

    if "linkedin" in sites:
        kwargs["linkedin_fetch_description"] = True

    try:
        df = scrape_jobs(**kwargs)
    except Exception as e:
        log.error("JobSpy search failed: %s", e)
        return {"error": str(e), "total": 0, "new": 0, "existing": 0}

    total = len(df)
    log.info("JobSpy returned %d results", total)

    if total == 0:
        return {"total": 0, "new": 0, "existing": 0}

    if "site" in df.columns:
        site_counts = df["site"].value_counts()
        for site, count in site_counts.items():
            log.info("  %s: %d", site, count)

    conn = conn or init_db()
    new, existing = store_jobspy_results(conn, df, query)
    log.info("Stored: %d new, %d already in DB", new, existing)

    db_total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    pending = conn.execute("SELECT COUNT(*) FROM jobs WHERE detail_scraped_at IS NULL").fetchone()[0]
    log.info("DB total: %d jobs, %d pending detail scrape", db_total, pending)

    return {"total": total, "new": new, "existing": existing}


# -- Full crawl (all queries x all locations) --------------------------------

def _full_crawl(
    search_cfg: dict,
    tiers: list[int] | None = None,
    locations: list[str] | None = None,
    sites: list[str] | None = None,
    results_per_site: int = 100,
    hours_old: int = 72,
    proxy: str | None = None,
    max_retries: int = 2,
) -> dict:
    """Run all search queries from search config across all locations."""
    if sites is None:
        sites = list(DEFAULT_SITES)

    # Build search combinations from config
    queries = search_cfg.get("queries", [])
    locs = search_cfg.get("locations", [])
    defaults = search_cfg.get("defaults", {})
    glassdoor_map = search_cfg.get("glassdoor_location_map", {})
    accept_locs, reject_locs = _load_location_config(search_cfg)

    if tiers:
        queries = [q for q in queries if q.get("tier") in tiers]
    if locations:
        locs = [loc for loc in locs if loc.get("label") in locations]

    searches = []
    for q in queries:
        for loc in locs:
            searches.append({
                "query": q["query"],
                "location": loc["location"],
                "remote": loc.get("remote", False),
                "tier": q.get("tier", 0),
            })

    proxy_config = parse_proxy(proxy) if proxy else None

    # Per-crawl site tracker: auto-disable boards that keep returning 0 results
    site_fail_threshold = defaults.get("site_fail_threshold", 3)
    if site_fail_threshold < _MIN_FAIL_THRESHOLD:
        log.warning("site_fail_threshold: %s is too aggressive (one throttled search would disable a board); "
                    "using %d. Set it to 3 in searches.yaml.", site_fail_threshold, _MIN_FAIL_THRESHOLD)
        site_fail_threshold = _MIN_FAIL_THRESHOLD
    tracker = _SiteTracker(threshold=site_fail_threshold)

    log.info("Full crawl: %d search combinations", len(searches))
    log.info("Sites: %s | Results/site: %d | Hours old: %d",
             ", ".join(sites), results_per_site, hours_old)

    # Ensure DB schema is ready
    init_db()

    total_new = 0
    total_existing = 0
    total_errors = 0
    completed = 0

    for s in searches:
        active = tracker.active_sites(sites)
        if not active:
            log.warning("All sites disabled — stopping crawl early")
            break

        result = _run_one_search(
            s, active, results_per_site, hours_old,
            proxy_config, defaults, max_retries,
            accept_locs, reject_locs, glassdoor_map,
        )
        completed += 1
        total_new += result["new"]
        total_existing += result["existing"]
        total_errors += result["errors"]

        # Boards whose call raised (network failure, bad config, ...) are left
        # out of the tracker so hard errors don't count as empty searches.
        noted = [site for site in active if site not in result["failed"]]
        newly_disabled = tracker.note(noted, result["sites"], blocked=result["blocked"])
        for site_name in newly_disabled:
            log.warning(
                "%s disabled for the rest of the crawl: %s. Remove it from 'sites' in "
                "searches.yaml to stop requesting it.",
                site_name, tracker.reasons[site_name],
            )

        if completed % 5 == 0 or completed == len(searches):
            log.info("Progress: %d/%d queries done (%d new, %d dupes, %d errors)",
                     completed, len(searches), total_new, total_existing, total_errors)

    # Final stats
    conn = get_connection()
    db_total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]

    log.info("Full crawl complete: %d new | %d dupes | %d errors | %d total in DB",
             total_new, total_existing, total_errors, db_total)

    return {
        "new": total_new,
        "existing": total_existing,
        "errors": total_errors,
        "db_total": db_total,
        "queries": len(searches),
        "disabled_sites": sorted(tracker.disabled),
        "site_stats": tracker.report(),
    }


# -- Public entry point ------------------------------------------------------

def _site_counts(df: pd.DataFrame, requested_sites: list[str]) -> dict[str, int]:
    """Count rows per site for only the requested sites.

    Returns a dict with an entry for each requested site. Sites not present
    in the DataFrame report 0. The DataFrame is expected to have a ``site``
    column (as JobSpy returns).
    """
    counts: dict[str, int] = {}
    if df.empty or "site" not in df.columns:
        for site in requested_sites:
            counts[site] = 0
        return counts

    site_col = df["site"]
    for site in requested_sites:
        mask = site_col == site
        counts[site] = int(mask.sum())

    return counts


@dataclass
class _SiteTracker:
    """Tracks per-site results across a crawl and disables boards that are blocked
    or keep returning 0 jobs."""

    threshold: int = 3
    counts: dict[str, int] = field(default_factory=dict)
    requests: dict[str, int] = field(default_factory=dict)
    consecutive_empty: dict[str, int] = field(default_factory=dict)
    disabled: set[str] = field(default_factory=set)
    reasons: dict[str, str] = field(default_factory=dict)

    def active_sites(self, sites: list[str]) -> list[str]:
        """Return ``sites`` with disabled boards removed, preserving order."""
        return [s for s in sites if s not in self.disabled]

    def note(self, requested: list[str], counts: dict[str, int],
             blocked: dict[str, str] | set[str] | None = None) -> list[str]:
        """Record results for one search. A board in ``blocked`` (refused the request,
        e.g. HTTP 403) is disabled immediately; a board with 0 results is disabled after
        ``threshold`` consecutive empty searches. Returns the boards newly disabled."""
        blocked = blocked or {}
        newly_disabled: list[str] = []

        for site in requested:
            self.requests[site] = self.requests.get(site, 0) + 1
            n = counts.get(site, 0)
            self.counts[site] = self.counts.get(site, 0) + n
            self.consecutive_empty[site] = 0 if n else self.consecutive_empty.get(site, 0) + 1

            if site in self.disabled:
                continue
            if site in blocked:
                msg = blocked[site] if isinstance(blocked, dict) else ""
                code = re.search(r"\b(4\d\d)\b", msg or "")
                self.reasons[site] = f"blocked (HTTP {code.group(1)})" if code else "blocked"
            elif self.consecutive_empty[site] >= self.threshold:
                self.reasons[site] = f"0 results on {self.consecutive_empty[site]} consecutive searches"
            else:
                continue
            self.disabled.add(site)
            newly_disabled.append(site)

        return newly_disabled

    def report(self) -> dict:
        """Return ``{"counts", "requests", "disabled", "reasons"}`` for crawl stats."""
        return {
            "counts": dict(self.counts),
            "requests": dict(self.requests),
            "disabled": sorted(self.disabled),
            "reasons": dict(self.reasons),
        }


def _gate_sites(sites: list[str], proxy: str | None, allow_unsupported: bool) -> list[str]:
    """Drop boards that can't work in this setup, logging one warning per dropped board.

    Glassdoor/ZipRecruiter need a proxy (they answer HTTP 403 otherwise); Google is
    skipped unless ``defaults.allow_unsupported: true``.
    """
    kept = []
    for site in sites:
        if site in _REQUIRES_PROXY and not proxy:
            log.warning("Skipping %s: blocked without a proxy (Cloudflare HTTP 403). "
                        "Set PROXY or 'proxy' in searches.yaml to enable it.", site)
        elif site in _UNSUPPORTED and not allow_unsupported:
            log.warning("Skipping %s: JobSpy returns no jobs for it. "
                        "Set defaults.allow_unsupported: true in searches.yaml to try anyway.", site)
        else:
            kept.append(site)
    return kept


def run_discovery(cfg: dict | None = None) -> dict:
    """Main entry point for JobSpy-based job discovery.

    Loads search queries and locations from the user's search config YAML,
    then runs a full crawl across all configured job boards.

    Args:
        cfg: Override the search configuration dict. If None, loads from
             the user's searches.yaml file.

    Returns:
        Dict with stats: new, existing, errors, db_total, queries.
    """
    if cfg is None:
        cfg = config.load_search_config()

    if not cfg:
        log.warning("No search configuration found. Run `applypilot init` to create one.")
        return {"new": 0, "existing": 0, "errors": 0, "db_total": 0, "queries": 0,
                "site_stats": {}, "disabled_sites": []}

    proxy = cfg.get("proxy") or os.environ.get("PROXY")
    sites = _gate_sites(list(cfg.get("sites") or DEFAULT_SITES), proxy,
                        bool(cfg.get("defaults", {}).get("allow_unsupported", False)))
    results_per_site = cfg.get("defaults", {}).get("results_per_site", 100)
    hours_old = cfg.get("defaults", {}).get("hours_old", 72)
    tiers = cfg.get("tiers")
    locations = cfg.get("location_labels")

    return _full_crawl(
        search_cfg=cfg,
        tiers=tiers,
        locations=locations,
        sites=sites,
        results_per_site=results_per_site,
        hours_old=hours_old,
        proxy=proxy,
    )
