"""Add a job by link or by paste (the dashboard's Add job), then read, extract and score it in the background.

`add_job` saves the row at once. The `intake` dashboard task runs `run_intake`: scrape the posting with one
Chromium page (skipped when a description was pasted), extract its fields, and assess it. A page that can't
be read (login wall, block, thin text) leaves `intake_status='needs_paste'`, and the dashboard offers a paste
form; `save_pasted` stores what the user pasted and the task runs again without scraping.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from strelitzia.database import JOB_BOARD_SITES, Connection, get_connection, job_key
from strelitzia.llm import LLMStopRun

log = logging.getLogger(__name__)

MANUAL_URL_PREFIX = "manual://"
# A scraped description shorter than this is treated as unreadable (a login wall, a cookie page, a stub).
MIN_DESCRIPTION_CHARS = 400

_TRACKING_PARAMS = {"refid", "trackingid", "trk", "src"}
_LINKEDIN_VIEW = re.compile(r"/jobs/view/(?:[^/]*-)?(\d+)/?$")  # /jobs/view/<id> or /jobs/view/<slug>-<id>
# Hosts that show a login wall to logged-out visitors (often with HTTP 200).
_WALLED_HOSTS = ("linkedin.com", "indeed.com")
_WALL_URL_MARKERS = ("authwall", "/login", "/signin", "/uas/", "/checkpoint/", "secure.indeed.com")
_WALL_PHRASES = ("join linkedin", "authwall", "sign in to", "sign in or join", "join now to see",
                 "agree & join", "create an account or sign in")


@dataclass
class ManualFields:
    title: str | None = None
    company: str | None = None
    location: str | None = None
    description: str | None = None


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def _on_host(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def normalize_url(raw: str) -> str:
    """The canonical form of a pasted job link: no tracking parameters or fragment; LinkedIn links by job id.

    Raises ValueError for anything that isn't an http(s) URL.
    """
    text = (raw or "").strip()
    if not text:
        raise ValueError("The link is empty")
    if "://" not in text:
        if re.match(r"[A-Za-z][A-Za-z0-9+.-]*:(?!\d)", text):  # mailto:, javascript:, ... (host:port is fine)
            raise ValueError("That isn't a web link (http or https)")
        text = "https://" + text
    parts = urlsplit(text)
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        raise ValueError("That isn't a web link (http or https)")
    host = parts.hostname.lower()
    query = parse_qsl(parts.query, keep_blank_values=True)
    if _on_host(host, "linkedin.com"):
        m = _LINKEDIN_VIEW.search(parts.path)
        job_id = m.group(1) if m else next((v for k, v in query if k == "currentJobId" and v.isdigit()), None)
        if job_id:
            return f"https://www.linkedin.com/jobs/view/{job_id}/"
    netloc = host + (f":{parts.port}" if parts.port else "")
    kept = [(k, v) for k, v in query if not (k.lower().startswith("utm_") or k.lower() in _TRACKING_PARAMS)]
    # A fragment is usually an anchor, but hash-routed career sites put the job in it (#/job/123).
    fragment = parts.fragment if "/" in parts.fragment else ""
    return urlunsplit((parts.scheme.lower(), netloc, parts.path or "/", urlencode(kept), fragment))


def site_for(url: str) -> str:
    """`site` for a job added by link: the job board's name (linkedin, indeed, ...) or the hostname."""
    host = _host(url)
    host = host[4:] if host.startswith("www.") else host
    labels = host.split(".")
    board = labels[-2] if len(labels) >= 2 else host
    return board if board in JOB_BOARD_SITES else host


def _row(conn: Connection, url: str) -> dict | None:
    row = conn.execute("SELECT * FROM jobs WHERE url = ?", (url,)).fetchone()
    return dict(row) if row else None


def _clean(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


def find_existing(conn: Connection, url: str) -> dict | None:
    """The job row for a pasted link, matched as normalized or as given. Raises ValueError for a non-web link.

    A `manual://` url (a job added without a link) only matches exactly, and must exist.
    """
    url = url.strip()
    if url.startswith(MANUAL_URL_PREFIX):
        row = _row(conn, url)
        if row is None:
            raise ValueError("No job was added with that link")
        return row
    normalized = normalize_url(url)
    return _row(conn, normalized) or _row(conn, url) or _same_posting(conn, normalized)


def _same_posting(conn: Connection, normalized: str) -> dict | None:
    """A row stored under another form of the same link (tracking parameters, a LinkedIn slug)."""
    parts = urlsplit(normalized)
    host = (parts.hostname or "").removeprefix("www.")
    m = _LINKEDIN_VIEW.search(parts.path) if _on_host(host, "linkedin.com") else None
    needle = f"linkedin.com/jobs/%{m.group(1)}" if m else host + parts.path.rstrip("/")
    escaped = needle.replace("!", "!!").replace("_", "!_")
    if not m:
        escaped = escaped.replace("%", "!%")
    rows = conn.execute("SELECT * FROM jobs WHERE url LIKE ? ESCAPE '!' LIMIT 50", (f"%{escaped}%",)).fetchall()
    for row in rows:
        try:
            if normalize_url(row["url"]) == normalized:
                return dict(row)
        except ValueError:
            continue
    return None


def add_job(conn: Connection, url: str | None, manual: ManualFields | None = None) -> tuple[dict, bool]:
    """Save a job the user added. Returns (row, created).

    A link the database already has (as given or normalized) adds no row: the job is marked `source='manual'`
    (so the prefilter never hides it) and empty fields are filled from `manual`. Without a link, `manual.description`
    is required and the job gets the synthetic url `manual://<uuid4 hex>`. A given description is saved as the
    full description, so the intake task skips the scrape.
    """
    manual = manual or ManualFields()
    description = _clean(manual.description)
    now = _now()
    if url and url.strip():
        existing = find_existing(conn, url)
        if existing:
            _mark_manual(conn, existing, manual, now)
            return _row(conn, existing["url"]), False
        normalized = normalize_url(url)
    else:
        if not description:
            raise ValueError("Give a link or paste the description")
        normalized = MANUAL_URL_PREFIX + uuid.uuid4().hex
    is_web = not normalized.startswith(MANUAL_URL_PREFIX)
    values = {
        "url": normalized, "job_key": job_key(normalized), "title": _clean(manual.title),
        "company": _clean(manual.company), "location": _clean(manual.location),
        "site": site_for(normalized) if is_web else None, "strategy": "manual", "source": "manual",
        "discovered_at": now, "added_at": now, "intake_status": "queued",
        "full_description": description, "detail_scraped_at": now if description else None,
    }
    names = ", ".join(values)
    conn.execute(f"INSERT INTO jobs ({names}) VALUES ({', '.join('?' * len(values))})", tuple(values.values()))
    conn.commit()
    return _row(conn, normalized), True


def _mark_manual(conn: Connection, job: dict, manual: ManualFields, now: str) -> None:
    updates: dict = {} if job.get("job_key") else {"job_key": job_key(job["url"])}  # discovery may not set it
    if job.get("source") != "manual":
        updates.update(source="manual", added_at=now)
    for col in ("title", "company", "location"):
        if not job.get(col) and _clean(getattr(manual, col)):
            updates[col] = _clean(getattr(manual, col))
    if not job.get("full_description") and _clean(manual.description):
        updates.update(full_description=_clean(manual.description), detail_scraped_at=now)
    if updates:
        _set(conn, job["url"], **updates)


def _set(conn: Connection, url: str, **values) -> None:
    sets = ", ".join(f"{k} = ?" for k in values)
    conn.execute(f"UPDATE jobs SET {sets} WHERE url = ?", (*values.values(), url))
    conn.commit()


def needs_intake(job: dict) -> bool:
    """Whether the intake task has work left for this job (it isn't extracted and scored yet)."""
    return job.get("fit_score") is None or not job.get("details_json")


def save_pasted(conn: Connection, url: str, manual: ManualFields) -> None:
    """Save a pasted description (and any title/company/location given). The caller then queues `intake`."""
    values = {col: _clean(getattr(manual, col)) for col in ("title", "company", "location")}
    values = {k: v for k, v in values.items() if v}
    _set(conn, url, **values, full_description=_clean(manual.description), detail_scraped_at=_now(),
         detail_error=None, intake_status="queued", intake_error=None)


def is_unreadable(result: dict, url: str) -> str | None:
    """Why a scrape result can't be used (failed, too short, a login wall), or None when it's a real posting."""
    if result.get("status") not in ("ok", "partial") or not result.get("full_description"):
        return f"The page couldn't be read ({result.get('error') or 'no description found'})."
    text = result["full_description"]
    host = _host(url)
    if any(_on_host(host, d) for d in _WALLED_HOSTS):
        final = (result.get("final_url") or url).lower()
        lowered = text.lower()
        if any(m in final for m in _WALL_URL_MARKERS) or any(p in lowered for p in _WALL_PHRASES) \
                or (len(text) < 1500 and "sign in" in lowered):
            return "The site showed a sign-in page instead of the posting."
    if len(text.strip()) < MIN_DESCRIPTION_CHARS:
        return f"Only {len(text.strip())} characters of the posting could be read."
    return None


def _scrape(url: str) -> dict:
    from strelitzia.enrichment.detail import detail_page, scrape_detail_page

    with detail_page() as page:
        result = scrape_detail_page(page, url)
        try:
            result["final_url"] = page.url
        except Exception:  # noqa: BLE001 -- only used for the login-wall check
            pass
    return result


def _location_text(posting) -> str | None:
    if not posting.locations:
        return None
    loc = posting.locations[0]
    return ", ".join(p for p in (loc.city, loc.state, loc.country) if p) or None


def _score(url: str) -> None:
    from strelitzia.web.tasks import assess_task

    assess_task(url, "rescore")


def run_intake(url: str, conn: Connection | None = None) -> None:
    """The `intake` task: scrape (unless there's a description), extract and score one job.

    Raises on failure after recording it in `intake_status` / `intake_error`, so the task shows failed.
    """
    from strelitzia.enrichment.extract import _save_posting, extract_one

    conn = conn or get_connection()
    job = _row(conn, url)
    if job is None:
        raise LookupError(f"No job with url {url}")
    try:
        if not job.get("full_description") and url.startswith(("http://", "https://")):
            _set(conn, url, intake_status="scraping", intake_error=None)
            result = _scrape(url)
            now = _now()
            reason = is_unreadable(result, url)
            if reason:
                # Keep whatever was found, so the paste form can be prefilled with it.
                _set(conn, url, detail_scraped_at=now, detail_error=result.get("error") or reason,
                     application_url=job.get("application_url") or result.get("application_url"),
                     description=job.get("description") or result.get("full_description"),
                     intake_status="needs_paste", intake_error=reason)
                return
            _set(conn, url, full_description=result["full_description"],
                 application_url=result.get("application_url") or job.get("application_url"),
                 detail_scraped_at=now, detail_error=None)
        elif not job.get("full_description"):
            _set(conn, url, intake_status="needs_paste", intake_error="There's no link to read; paste the description.")
            return
        _set(conn, url, intake_status="scraping", intake_error=None)
        job = _row(conn, url)
        posting = extract_one(job)
        _save_posting(conn, url, posting)
        fills = {"title": posting.title_normalized, "location": _location_text(posting)}
        _set(conn, url, **{k: v for k, v in fills.items() if v and not job.get(k)}, extract_error=None)
        _score(url)
        _set(conn, url, intake_status="ok", intake_error=None)
    except LLMStopRun as e:
        from strelitzia.web.tasks import STOP_MESSAGES

        _set(conn, url, intake_status="error", intake_error=STOP_MESSAGES.get(e.reason, str(e)))
        raise
    except Exception as e:
        from strelitzia.web.tasks import GenerationFailed

        conn.rollback()
        message = str(e) if isinstance(e, GenerationFailed) else f"{type(e).__name__}: {e}"
        _set(conn, url, intake_status="error", intake_error=message[:500])
        raise
