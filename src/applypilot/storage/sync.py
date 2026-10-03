"""Move tailored resume and cover letter PDFs to Google Drive and save the links in the DB.

A local PDF is deleted only after Drive reports the same MD5 checksum. The .txt/.json working
files stay local because the cover-letter and apply stages read them. When a later stage needs a
moved PDF, ensure_local_pdf() downloads it back to its original path.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from applypilot.database import get_connection
from applypilot.storage.drive import DriveClient, DriveNotConfigured, file_md5
from applypilot.storage.drive_layout import KINDS, drive_target

log = logging.getLogger(__name__)

_PATH_COLUMN = {"resume": "tailored_resume_path", "cover_letter": "cover_letter_path"}


def local_pdf(job: dict, kind: str) -> Path | None:
    """Where this job's PDF of the given kind lives (or lived) on disk."""
    path = job.get(_PATH_COLUMN[kind])
    return Path(path).with_suffix(".pdf") if path else None


def sync_job(conn: sqlite3.Connection, client: DriveClient, job: dict, keep_local: bool = False) -> dict:
    """Upload this job's local PDFs, save their links, and delete the local copies once verified."""
    counts = {"uploaded": 0, "updated": 0, "moved": 0, "errors": 0}
    for kind in KINDS:
        pdf = local_pdf(job, kind)
        if not pdf or not pdf.exists():
            continue
        result = client.upsert_file(drive_target(job, kind), pdf, known_id=job.get(f"{kind}_drive_id"))
        counts["uploaded" if result.created else "updated"] += 1
        now = datetime.now(UTC).isoformat()
        conn.execute(
            f"UPDATE jobs SET {kind}_drive_id = ?, {kind}_drive_url = ?, drive_synced_at = ? WHERE url = ?",
            (result.id, result.url, now, job["url"]),
        )
        conn.commit()
        job[f"{kind}_drive_id"], job[f"{kind}_drive_url"] = result.id, result.url
        if keep_local:
            continue
        if result.md5 and result.md5 == file_md5(pdf):
            pdf.unlink()
            counts["moved"] += 1
        else:
            log.error("Drive checksum mismatch for %s; keeping the local copy.", pdf)
            counts["errors"] += 1
    return counts


def _jobs_with_files(conn: sqlite3.Connection) -> list[dict]:
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE tailored_resume_path IS NOT NULL "
            "OR (cover_letter_path IS NOT NULL AND cover_letter_path != '') "
            "ORDER BY tailored_at"
        ).fetchall()
    finally:
        conn.row_factory = None
    return [dict(r) for r in rows]


def run_drive_sync(conn: sqlite3.Connection | None = None, client: DriveClient | None = None,
                   limit: int | None = None, keep_local: bool = False) -> dict:
    """Move every local tailored resume and cover letter PDF to Drive.

    Returns counts: uploaded (new Drive files), updated (replaced content), moved (local copies
    deleted), missing (no local PDF and no Drive copy), errors. Raises DriveNotConfigured.
    """
    conn = conn or get_connection()
    totals = {"uploaded": 0, "updated": 0, "moved": 0, "missing": 0, "errors": 0}
    pending = []
    for job in _jobs_with_files(conn):
        has_local = False
        for kind in KINDS:
            pdf = local_pdf(job, kind)
            if not pdf:
                continue
            if pdf.exists():
                has_local = True
            elif not job.get(f"{kind}_drive_id"):
                totals["missing"] += 1
        if has_local:
            pending.append(job)
    if limit is not None:
        pending = pending[:limit]
    if not pending:
        return totals

    client = client or DriveClient.from_credentials()
    for job in pending:
        try:
            for key, n in sync_job(conn, client, job, keep_local=keep_local).items():
                totals[key] += n
        except DriveNotConfigured:
            raise
        except Exception as e:  # noqa: BLE001 -- one bad job must not stop the rest
            log.error("Drive sync failed for %s: %s", job["url"], e)
            totals["errors"] += 1
    return totals


def ensure_local_pdf(job: dict, kind: str, client: DriveClient | None = None) -> Path | None:
    """The job's PDF on disk, downloading it from Drive first if it was moved. None if unavailable."""
    pdf = local_pdf(job, kind)
    if not pdf:
        return None
    if pdf.exists():
        return pdf
    file_id = job.get(f"{kind}_drive_id")
    if not file_id:
        return None
    try:
        client = client or DriveClient.from_credentials()
        return client.download(file_id, pdf)
    except Exception as e:  # noqa: BLE001 -- the caller reports the missing file
        log.warning("Could not download %s from Drive: %s", pdf.name, e)
        return None
