"""Where a job's files go in Google Drive: Strelitzia/<Company>/<Role>/<YYYY-MM-DD>/.

The Claude Project's fixed Docs (rules, content library) go in Strelitzia/_Claude/.

Pure functions with no Google imports, so the layout is easy to test and stable across machines.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime

from strelitzia.database import job_company, job_key

KINDS = ("resume", "cover_letter")
_KIND_LABEL = {"resume": "Resume", "cover_letter": "Cover Letter"}
_KIND_DATE_COLUMN = {"resume": "tailored_at", "cover_letter": "cover_letter_at"}

_BAD_CHARS = re.compile(r'[/\\:*?"<>|\x00-\x1f\x7f]')


def clean_name(s: str | None, max_len: int = 80) -> str:
    """A Drive- and filesystem-safe folder or file name."""
    cleaned = _BAD_CHARS.sub(" ", s or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    cleaned = cleaned[:max_len].rstrip(" .")
    return cleaned or "Untitled"


DEFAULT_ROOT = "Strelitzia"
LEGACY_ROOT = "ApplyPilot"  # the default before the rename; DriveClient keeps using an existing one


def root_folder() -> str:
    return clean_name(os.environ.get("DRIVE_ROOT_FOLDER") or DEFAULT_ROOT)


# Each user's Drive is their own, so the paths are the same for everyone; the root folder's id is cached per user in
# user_drive.root_folder_id (strelitzia.storage.drive) so it's looked up by name once.

def cached_root_folder_id(conn, user_id: str) -> str | None:
    row = conn.execute("SELECT root_folder_id FROM user_drive WHERE user_id = ?", (user_id,)).fetchone()
    return row[0] if row and row[0] else None


def cache_root_folder_id(conn, user_id: str, folder_id: str | None) -> None:
    conn.execute("UPDATE user_drive SET root_folder_id = ? WHERE user_id = ?", (folder_id, user_id))
    conn.commit()


def _local_date(value: str | None) -> str | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone()
    return dt.date().isoformat()


def job_date(job: dict, kind: str) -> str:
    """Local date the file was generated: the kind's own timestamp, then tailored_at, then today."""
    return (
        _local_date(job.get(_KIND_DATE_COLUMN[kind]))
        or _local_date(job.get("tailored_at"))
        or datetime.now().astimezone().date().isoformat()
    )


def folder_path(job: dict, kind: str) -> list[str]:
    return [root_folder(), clean_name(job_company(job)), clean_name(job.get("title")), job_date(job, kind)]


CLAUDE_FOLDER = "_Claude"


def claude_folder_path() -> list[str]:
    return [root_folder(), CLAUDE_FOLDER]


def packet_folder_path(job: dict) -> list[str]:
    """The job's folder for its Claude packet Doc (next to the resume)."""
    return folder_path(job, "resume")


def file_name(job: dict, kind: str, suffix: str = "") -> str:
    base = f"{clean_name(job_company(job), 40)} - {clean_name(job.get('title'), 60)} - {_KIND_LABEL[kind]}"
    if suffix:
        base += f" ({suffix})"
    return base + ".pdf"


@dataclass(frozen=True)
class DriveTarget:
    folders: tuple[str, ...]
    name: str
    job_key: str
    kind: str  # "resume" | "cover_letter"
    site: str

    def collision_name(self) -> str:
        """Name used when a different job's file already has `name` in the same folder."""
        stem = self.name.removesuffix(".pdf")
        return f"{stem} ({self.job_key[:6]}).pdf"


def drive_target(job: dict, kind: str) -> DriveTarget:
    if kind not in KINDS:
        raise ValueError(f"Unknown file kind: {kind!r}")
    return DriveTarget(
        folders=tuple(folder_path(job, kind)),
        name=file_name(job, kind),
        job_key=job_key(job["url"]),
        kind=kind,
        site=job.get("site") or "",
    )
