"""Where a job's files go in Google Drive: ApplyPilot/<Company>/<Role>/<YYYY-MM-DD>/.

Pure functions with no Google imports, so the layout is easy to test and stable across machines.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from datetime import datetime

from applypilot.database import job_company

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


def root_folder() -> str:
    return clean_name(os.environ.get("DRIVE_ROOT_FOLDER") or "ApplyPilot")


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


def job_key(url: str) -> str:
    """Stable per-job ID stored on each Drive file, so either machine finds the same file."""
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]


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
