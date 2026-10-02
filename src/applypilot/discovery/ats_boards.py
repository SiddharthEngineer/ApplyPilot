"""Greenhouse / Lever / Ashby job boards: public JSON APIs with no bot protection.

Many companies host their careers page on one of these applicant tracking
systems, and each exposes an unauthenticated JSON list of open jobs. Boards to
crawl are listed in ``config/ats_boards.yaml``. Titles are matched against the
search queries from searches.yaml and locations go through the same filter as
JobSpy results.
"""

import html
import logging
import re
import sqlite3
from datetime import UTC, datetime

import httpx
import yaml
from bs4 import BeautifulSoup

from applypilot import config
from applypilot.database import init_db
from applypilot.discovery.jobspy import _load_location_config, _location_ok

log = logging.getLogger(__name__)

ATS_URLS = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true",
    "lever": "https://api.lever.co/v0/postings/{slug}?mode=json",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true",
}

_TIMEOUT = 30.0
# Descriptions at least this long are stored as full_description so enrich can skip the job.
_FULL_DESCRIPTION_MIN = 200


def load_ats_boards() -> list[dict]:
    """Return the ``boards`` list from ``config/ats_boards.yaml`` (``[]`` if missing)."""
    path = config.CONFIG_DIR / "ats_boards.yaml"
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return [b for b in data.get("boards", []) if b.get("kind") in ATS_URLS and b.get("slug")]


# -- Normalization -----------------------------------------------------------

def _html_to_text(raw: str | None) -> str | None:
    if not raw:
        return None
    text = BeautifulSoup(html.unescape(raw), "html.parser").get_text("\n")
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line) or None


def _with_remote(location: str | None, remote: bool) -> str | None:
    if not remote or (location and "remote" in location.lower()):
        return location
    return f"{location} (Remote)" if location else "Remote"


def _greenhouse(data: dict) -> list[dict]:
    return [{
        "url": j.get("absolute_url"),
        "title": j.get("title"),
        "location": (j.get("location") or {}).get("name"),
        "description": _html_to_text(j.get("content")),
        "salary": None,
        "application_url": j.get("absolute_url"),
    } for j in data.get("jobs", [])]


def _lever_salary(rng: dict | None) -> str | None:
    if not rng or not rng.get("min"):
        return None
    cur = rng.get("currency") or ""
    out = f"{cur} {rng['min']:,}"
    if rng.get("max"):
        out += f"-{rng['max']:,}"
    if rng.get("interval"):
        out += f"/{rng['interval']}"
    return out.strip()


def _lever(data: list) -> list[dict]:
    jobs = []
    for j in data:
        cats = j.get("categories") or {}
        parts = [j.get("descriptionPlain")]
        for section in j.get("lists") or []:
            parts.append(section.get("text"))
            parts.append(_html_to_text(section.get("content")))
        parts.append(j.get("additionalPlain"))
        jobs.append({
            "url": j.get("hostedUrl"),
            "title": j.get("text"),
            "location": _with_remote(cats.get("location"), j.get("workplaceType") == "remote"),
            "description": "\n\n".join(p.strip() for p in parts if p and p.strip()) or None,
            "salary": _lever_salary(j.get("salaryRange")),
            "application_url": j.get("applyUrl"),
        })
    return jobs


def _ashby(data: dict) -> list[dict]:
    jobs = []
    for j in data.get("jobs", []):
        if j.get("isListed") is False:
            continue
        comp = j.get("compensation") or {}
        jobs.append({
            "url": j.get("jobUrl"),
            "title": j.get("title"),
            "location": _with_remote(j.get("location"), bool(j.get("isRemote"))),
            "description": j.get("descriptionPlain") or _html_to_text(j.get("descriptionHtml")),
            "salary": comp.get("compensationTierSummary") or comp.get("scrapeableCompensationSalarySummary"),
            "application_url": j.get("applyUrl"),
        })
    return jobs


_NORMALIZERS = {"greenhouse": _greenhouse, "lever": _lever, "ashby": _ashby}


def normalize(kind: str, data, company: str) -> list[dict]:
    """Convert one ATS API response into ``jobs`` table rows."""
    jobs = _NORMALIZERS[kind](data)
    for job in jobs:
        job["site"] = company
        job["strategy"] = f"ats_{kind}"
        desc = job.get("description")
        job["full_description"] = desc if desc and len(desc) >= _FULL_DESCRIPTION_MIN else None
    return [j for j in jobs if j.get("url") and j.get("title")]


def fetch_board(kind: str, slug: str, company: str | None = None,
                client: httpx.Client | None = None) -> list[dict]:
    """Fetch and normalize every open job on one board. Raises on HTTP errors."""
    if kind not in ATS_URLS:
        raise ValueError(f"Unknown ATS kind {kind!r}; expected one of {sorted(ATS_URLS)}")
    url = ATS_URLS[kind].format(slug=slug)
    if client is None:
        with httpx.Client(timeout=_TIMEOUT, follow_redirects=True) as c:
            resp = c.get(url)
    else:
        resp = client.get(url)
    resp.raise_for_status()
    return normalize(kind, resp.json(), company or slug)


# -- Filtering + storage -----------------------------------------------------

def _word_re(word: str) -> re.Pattern:
    # Whole word, optional plural: "engineer" matches "Engineers" but not "Engineering",
    # and "ai" doesn't match "Spain" or "Training".
    return re.compile(rf"(?<![a-z0-9]){re.escape(word)}s?(?![a-z0-9])")


def title_matches(title: str, queries: list[str]) -> bool:
    """True if every word of at least one query appears as a whole word in the title (case-insensitive)."""
    t = title.lower()
    return any(q.split() and all(_word_re(w).search(t) for w in q.lower().split()) for q in queries)


def store_ats_jobs(conn: sqlite3.Connection, jobs: list[dict]) -> tuple[int, int]:
    """Insert jobs, skipping URLs already in the DB. Returns (new, existing)."""
    now = datetime.now(UTC).isoformat()
    new = existing = 0
    for j in jobs:
        try:
            conn.execute(
                "INSERT INTO jobs (url, title, salary, description, location, site, strategy, discovered_at, "
                "full_description, application_url, detail_scraped_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (j["url"], j["title"], j.get("salary"), j.get("description"), j.get("location"),
                 j["site"], j["strategy"], now, j.get("full_description"), j.get("application_url"),
                 now if j.get("full_description") else None),
            )
            new += 1
        except sqlite3.IntegrityError:
            existing += 1
    conn.commit()
    return new, existing


def run_ats_discovery(
    queries: list[str] | None = None,
    accept_locs: list[str] | None = None,
    reject_locs: list[str] | None = None,
    boards: list[dict] | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict:
    """Fetch every configured board, keep jobs whose title matches a query and whose
    location passes the filter, and store them. One failing board doesn't stop the rest.

    Queries and location lists default to the user's searches.yaml.
    """
    if queries is None or accept_locs is None or reject_locs is None:
        search_cfg = config.load_search_config() or {}
        if queries is None:
            queries = [q["query"] for q in search_cfg.get("queries", []) if q.get("query")]
        cfg_accept, cfg_reject = _load_location_config(search_cfg)
        accept_locs = cfg_accept if accept_locs is None else accept_locs
        reject_locs = cfg_reject if reject_locs is None else reject_locs
    boards = load_ats_boards() if boards is None else boards

    stats = {"boards": len(boards), "fetched": 0, "matched": 0, "new": 0, "existing": 0,
             "errors": 0, "per_board": {}}
    if not boards or not queries:
        log.info("ATS discovery: nothing to do (%d boards, %d queries)", len(boards), len(queries or []))
        return stats

    conn = conn or init_db()
    with httpx.Client(timeout=_TIMEOUT, follow_redirects=True) as client:
        for b in boards:
            name = b.get("name") or b["slug"]
            key = f"{b['kind']}:{b['slug']}"
            try:
                jobs = fetch_board(b["kind"], b["slug"], company=name, client=client)
            except (httpx.HTTPError, ValueError) as e:
                log.warning("ATS %s (%s) failed: %s", name, key, e)
                stats["errors"] += 1
                stats["per_board"][key] = {"error": str(e)[:200]}
                continue
            keep = [j for j in jobs
                    if title_matches(j["title"], queries)
                    and _location_ok(j.get("location"), accept_locs, reject_locs)]
            new, existing = store_ats_jobs(conn, keep)
            log.info("ATS %s: %d jobs, %d match -> %d new, %d dupes", name, len(jobs), len(keep), new, existing)
            stats["fetched"] += len(jobs)
            stats["matched"] += len(keep)
            stats["new"] += new
            stats["existing"] += existing
            stats["per_board"][key] = {"jobs": len(jobs), "matched": len(keep), "new": new}

    log.info("ATS discovery complete: %d boards, %d matched, %d new, %d errors",
             len(boards), stats["matched"], stats["new"], stats["errors"])
    return stats
