"""Google Drive client: OAuth credentials, find-or-create folders, upsert and download files.

Needs the optional extra: pip install "applypilot[drive]". Google libraries are imported
inside functions so the core install works without them.

The OAuth scope is drive.file: ApplyPilot can only see files and folders it created. Every
machine that uses the same OAuth client sees the same files, so the laptop and the VPS share
one library.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from applypilot import config
from applypilot.storage.drive_layout import DriveTarget

log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/drive.file"]
FOLDER_MIME = "application/vnd.google-apps.folder"
PDF_MIME = "application/pdf"
_FILE_FIELDS = "id, name, parents, trashed, md5Checksum, webViewLink, appProperties"
INSTALL_HINT = 'Install the Drive extra: pip install -e ".[drive]"'


class DriveNotConfigured(RuntimeError):
    """Drive libraries are missing or there is no usable OAuth token."""


@dataclass(frozen=True)
class DriveFile:
    id: str
    url: str
    md5: str | None
    created: bool


def _q(value: str) -> str:
    """Quote a value for a Drive search query."""
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def file_md5(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# -- Credentials -------------------------------------------------------------

def drive_libraries_installed() -> bool:
    try:
        import google_auth_oauthlib  # noqa: F401
        import googleapiclient  # noqa: F401
    except ImportError:
        return False
    return True


def load_credentials():
    """Load the saved OAuth token, refreshing it if expired. Raises DriveNotConfigured."""
    if not drive_libraries_installed():
        raise DriveNotConfigured(INSTALL_HINT)
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    token_path = config.GOOGLE_TOKEN_PATH
    if not token_path.exists():
        raise DriveNotConfigured(f"No Google token at {token_path}. Run `applypilot drive auth` first.")
    creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if creds.valid:
        return creds
    if not (creds.expired and creds.refresh_token):
        raise DriveNotConfigured("The Google token can't be refreshed. Run `applypilot drive auth` again.")
    try:
        creds.refresh(Request())
    except RefreshError as e:
        raise DriveNotConfigured(f"Google sign-in expired ({e}). Run `applypilot drive auth` again.") from e
    _save_token(creds)
    return creds


def _save_token(creds) -> None:
    config.GOOGLE_TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.GOOGLE_TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
    config.set_restricted_permissions(config.GOOGLE_TOKEN_PATH)


def authorize(client_secret: Path, port: int = 8765, open_browser: bool = True) -> Path:
    """Run the OAuth consent flow in a browser and save the token. Returns the token path."""
    if not drive_libraries_installed():
        raise DriveNotConfigured(INSTALL_HINT)
    from google_auth_oauthlib.flow import InstalledAppFlow

    if not client_secret.exists():
        raise DriveNotConfigured(f"OAuth client file not found: {client_secret}")
    flow = InstalledAppFlow.from_client_secrets_file(str(client_secret), SCOPES)
    creds = flow.run_local_server(
        host="localhost", port=port, open_browser=open_browser,
        authorization_prompt_message="Open this URL in your browser to allow ApplyPilot to use Google Drive:\n{url}\n",
        success_message="ApplyPilot can now use Google Drive. You can close this tab.",
    )
    _save_token(creds)
    return config.GOOGLE_TOKEN_PATH


# -- Client ------------------------------------------------------------------

def _pdf_media(path: Path):
    from googleapiclient.http import MediaFileUpload
    return MediaFileUpload(str(path), mimetype=PDF_MIME, resumable=False)


class DriveClient:
    """Thin wrapper around a googleapiclient Drive v3 service (injectable for tests)."""

    def __init__(self, service, media_factory: Callable[[Path], object] | None = None):
        self._service = service
        self._media = media_factory or _pdf_media
        self._folders: dict[tuple[str, str], str] = {}

    @classmethod
    def from_credentials(cls) -> DriveClient:
        creds = load_credentials()
        from googleapiclient.discovery import build
        return cls(build("drive", "v3", credentials=creds, cache_discovery=False))

    def _list(self, q: str) -> list[dict]:
        files: list[dict] = []
        token = None
        while True:
            resp = self._service.files().list(
                q=q, spaces="drive", fields=f"nextPageToken, files({_FILE_FIELDS})",
                pageSize=100, pageToken=token,
            ).execute()
            files.extend(resp.get("files", []))
            token = resp.get("nextPageToken")
            if not token:
                return files

    def ensure_folder_path(self, folders) -> str:
        """Find or create each folder in turn; returns the deepest folder's ID."""
        parent = "root"
        for name in folders:
            key = (parent, name)
            if key not in self._folders:
                found = self._list(
                    f"name = {_q(name)} and mimeType = {_q(FOLDER_MIME)} "
                    f"and {_q(parent)} in parents and trashed = false"
                )
                if found:
                    self._folders[key] = found[0]["id"]
                else:
                    body = {"name": name, "mimeType": FOLDER_MIME, "parents": [parent]}
                    self._folders[key] = self._service.files().create(body=body, fields="id").execute()["id"]
            parent = self._folders[key]
        return parent

    def _get(self, file_id: str) -> dict | None:
        try:
            return self._service.files().get(fileId=file_id, fields=_FILE_FIELDS).execute()
        except Exception as e:  # noqa: BLE001 -- HttpError 404 when the file is gone or not ours
            log.debug("Drive file %s not found: %s", file_id, e)
            return None

    def find_file(self, target: DriveTarget, folder_id: str, known_id: str | None = None) -> dict | None:
        """The existing Drive file for this job and kind in folder_id, if any."""
        if known_id:
            meta = self._get(known_id)
            if meta and not meta.get("trashed") and folder_id in (meta.get("parents") or []):
                return meta
        found = self._list(
            f"{_q(folder_id)} in parents and trashed = false "
            f"and appProperties has {{ key='applypilot_job' and value={_q(target.job_key)} }} "
            f"and appProperties has {{ key='applypilot_kind' and value={_q(target.kind)} }}"
        )
        return found[0] if found else None

    def upsert_file(self, target: DriveTarget, local_path: Path, known_id: str | None = None) -> DriveFile:
        """Upload local_path to the target folder, replacing this job's existing file if there is one."""
        folder_id = self.ensure_folder_path(target.folders)
        existing = self.find_file(target, folder_id, known_id)
        if existing:
            meta = self._service.files().update(
                fileId=existing["id"], media_body=self._media(local_path), fields=_FILE_FIELDS,
            ).execute()
            created = False
        else:
            name = target.name
            if self._list(f"name = {_q(name)} and {_q(folder_id)} in parents and trashed = false"):
                name = target.collision_name()
            body = {
                "name": name, "parents": [folder_id], "mimeType": PDF_MIME,
                "appProperties": {
                    "applypilot_job": target.job_key, "applypilot_kind": target.kind,
                    "applypilot_site": target.site[:100],
                },
            }
            meta = self._service.files().create(
                body=body, media_body=self._media(local_path), fields=_FILE_FIELDS,
            ).execute()
            created = True
        return DriveFile(id=meta["id"], url=meta.get("webViewLink", ""), md5=meta.get("md5Checksum"), created=created)

    def download(self, file_id: str, dest: Path) -> Path:
        data = self._service.files().get_media(fileId=file_id).execute()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return dest
