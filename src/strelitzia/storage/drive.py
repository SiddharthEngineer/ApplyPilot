"""Google Drive client: OAuth credentials, find-or-create folders, upsert and download files.

Needs the optional extra: pip install "strelitzia[drive]". Google libraries are imported
inside functions so the core install works without them.

The OAuth scope is drive.file: Strelitzia can only see files and folders it created. Every
machine that uses the same OAuth client sees the same files, so the laptop and the VPS share
one library.
"""

from __future__ import annotations

import hashlib
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from strelitzia import config
from strelitzia.storage.drive_layout import (
    DEFAULT_ROOT,
    LEGACY_ROOT,
    DriveTarget,
    cache_root_folder_id,
    cached_root_folder_id,
)

log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/drive.file"]
FOLDER_MIME = "application/vnd.google-apps.folder"
PDF_MIME = "application/pdf"
DOC_MIME = "application/vnd.google-apps.document"  # uploading HTML with this target type makes a Google Doc
_FILE_FIELDS = "id, name, mimeType, parents, trashed, md5Checksum, webViewLink, appProperties"
# appProperties key prefix. Kept from before the rename (legacy): files already in Drive are tagged with it.
PROP_PREFIX = "applypilot_"
INSTALL_HINT = 'Install the Drive extra: pip install -e ".[drive]"'


class DriveNotConfigured(RuntimeError):
    """Drive libraries are missing or there is no usable OAuth token."""


@dataclass(frozen=True)
class DriveFile:
    id: str
    url: str
    md5: str | None
    created: bool
    folder_id: str | None = None  # the job's leaf folder (company/role/date)


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


def _refreshed(creds, save: Callable[[object], None], again: str):
    """creds, refreshed and saved when expired. `again` says how to sign in again. Raises DriveNotConfigured."""
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request

    if creds.valid:
        return creds
    if not (creds.expired and creds.refresh_token):
        raise DriveNotConfigured(f"The Google token can't be refreshed. {again}")
    try:
        creds.refresh(Request())
    except RefreshError as e:
        raise DriveNotConfigured(f"Google Drive access expired or was revoked ({e}). {again}") from e
    save(creds)
    return creds


def load_credentials():
    """Load the saved OAuth token, refreshing it if expired. Raises DriveNotConfigured."""
    if not drive_libraries_installed():
        raise DriveNotConfigured(INSTALL_HINT)
    from google.oauth2.credentials import Credentials

    token_path = config.GOOGLE_TOKEN_PATH
    if not token_path.exists():
        raise DriveNotConfigured(f"No Google token at {token_path}. Run `strelitzia drive auth` first.")
    creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    return _refreshed(creds, _save_token, "Run `strelitzia drive auth` again.")


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
        authorization_prompt_message="Open this URL in your browser to allow Strelitzia to use Google Drive:\n{url}\n",
        success_message="Strelitzia can now use Google Drive. You can close this tab.",
    )
    _save_token(creds)
    return config.GOOGLE_TOKEN_PATH


# -- Per-user tokens (the dashboard's Connect Google Drive) --------------------

CONNECT_HINT = "Connect Google Drive on the Claude setup page"
TOKEN_KEY_HINT = "STRELITZIA_TOKEN_KEY isn't set (a Fernet key, see agents/DEPLOY.md)"
LEGACY_IMPORT_STATE = "drive_legacy_token_imported"


def _fernet():
    """The Fernet cipher for stored tokens, or None without STRELITZIA_TOKEN_KEY."""
    key = config.env("TOKEN_KEY")
    if not key:
        return None
    from cryptography.fernet import Fernet
    try:
        return Fernet(key.encode())
    except ValueError as e:
        raise DriveNotConfigured("STRELITZIA_TOKEN_KEY isn't a valid Fernet key") from e


def save_user_token(conn, user_id: str, creds, *, refreshed: bool = False) -> None:
    """Store the user's OAuth credentials, encrypted. Refuses (DriveNotConfigured) without TOKEN_KEY.

    A refreshed token keeps the original connected_at.
    """
    _save_token_json(conn, user_id, creds.to_json(), refreshed=refreshed)


def _save_token_json(conn, user_id: str, token_json: str, *, refreshed: bool = False) -> None:
    from datetime import datetime, timezone

    fernet = _fernet()
    if fernet is None:
        raise DriveNotConfigured(TOKEN_KEY_HINT)
    token_enc = fernet.encrypt(token_json.encode("utf-8")).decode("ascii")
    conn.execute(
        "INSERT INTO user_drive (user_id, token_enc, connected_at) VALUES (?, ?, ?) "
        "ON CONFLICT (user_id) DO UPDATE SET token_enc = excluded.token_enc"
        + ("" if refreshed else ", connected_at = excluded.connected_at"),
        (user_id, token_enc, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()


def _stored_row(conn, user_id: str):
    """(token_enc, connected_at) of the user's Drive connection, or None."""
    return conn.execute("SELECT token_enc, connected_at FROM user_drive WHERE user_id = ? AND token_enc IS NOT NULL",
                        (user_id,)).fetchone()


def _stored_token(conn, user_id: str) -> dict | None:
    """The user's decrypted token info, or None when they haven't connected Drive. Raises DriveNotConfigured."""
    import json

    from cryptography.fernet import InvalidToken

    row = _stored_row(conn, user_id)
    if row is None:
        return None
    fernet = _fernet()
    if fernet is None:
        raise DriveNotConfigured(TOKEN_KEY_HINT)
    try:
        return json.loads(fernet.decrypt(row[0].encode("ascii")))
    except (InvalidToken, ValueError) as e:
        raise DriveNotConfigured(f"The stored Google token can't be read (was TOKEN_KEY changed?). {CONNECT_HINT} "
                                 "again.") from e


def load_user_credentials(conn, user_id: str):
    """The user's Drive credentials, refreshed (and re-saved) when expired. Raises DriveNotConfigured.

    A token Google refuses to refresh (revoked, or expired for good) is forgotten, so the user is offered Connect again.
    """
    if not drive_libraries_installed():
        raise DriveNotConfigured(INSTALL_HINT)
    from google.oauth2.credentials import Credentials

    info = _stored_token(conn, user_id)
    if info is None:
        raise DriveNotConfigured(f"Google Drive isn't connected. {CONNECT_HINT}.")
    creds = Credentials.from_authorized_user_info(info, SCOPES)
    try:
        return _refreshed(creds, lambda c: save_user_token(conn, user_id, c, refreshed=True), f"{CONNECT_HINT} again.")
    except DriveNotConfigured:
        disconnect(conn, user_id)
        raise


def stored_refresh_token(conn, user_id: str) -> str | None:
    """The user's refresh token (to revoke it at Google on disconnect), or None if there's none readable."""
    try:
        info = _stored_token(conn, user_id)
    except DriveNotConfigured:
        return None
    token = (info or {}).get("refresh_token")
    return token if isinstance(token, str) and token else None


def disconnect(conn, user_id: str) -> None:
    """Forget the user's Drive token (their files stay in their Drive)."""
    conn.execute("DELETE FROM user_drive WHERE user_id = ?", (user_id,))
    conn.commit()


def user_drive_status(conn, user_id: str) -> tuple[str, str]:
    """("not_installed" | "not_configured" | "not_authorized" | "authorized", detail) without touching the network.

    A stored token that doesn't decrypt (TOKEN_KEY changed) counts as not authorized, so Connect is offered again.
    """
    if not drive_libraries_installed():
        return "not_installed", INSTALL_HINT
    if not config.env("TOKEN_KEY"):
        return "not_configured", TOKEN_KEY_HINT
    row = _stored_row(conn, user_id)
    if row is None:
        return "not_authorized", CONNECT_HINT
    try:
        _stored_token(conn, user_id)
    except DriveNotConfigured as e:
        return "not_authorized", str(e)
    return "authorized", f"Connected {str(row[1] or '')[:10]}".strip()


def import_legacy_token(conn) -> bool:
    """Once: give the owner the CLI's token file (GOOGLE_TOKEN_PATH) if they have no stored token. The file stays.

    Waits (returns False without marking itself done) until TOKEN_KEY is set and the file exists.
    """
    from strelitzia import users
    from strelitzia.database import get_state, set_state

    if get_state(conn, LEGACY_IMPORT_STATE) or not config.env("TOKEN_KEY") or not config.GOOGLE_TOKEN_PATH.exists():
        return False
    owner_id = users.owner(conn).id
    imported = False
    if _stored_row(conn, owner_id) is None:
        import json

        try:
            token_json = config.GOOGLE_TOKEN_PATH.read_text(encoding="utf-8")
            json.loads(token_json)
        except (OSError, ValueError) as e:
            log.warning("Couldn't read the Google token file for the owner import: %s", type(e).__name__)
            return False
        _save_token_json(conn, owner_id, token_json)
        imported = True
    set_state(conn, LEGACY_IMPORT_STATE, "1")
    conn.commit()
    return imported


# -- Client ------------------------------------------------------------------

def _pdf_media(path: Path):
    from googleapiclient.http import MediaFileUpload
    return MediaFileUpload(str(path), mimetype=PDF_MIME, resumable=False)


def _html_media(html: str):
    import io

    from googleapiclient.http import MediaIoBaseUpload
    return MediaIoBaseUpload(io.BytesIO(html.encode("utf-8")), mimetype="text/html", resumable=False)


class DriveClient:
    """Thin wrapper around a googleapiclient Drive v3 service (injectable for tests)."""

    def __init__(self, service, media_factory: Callable[[Path], object] | None = None,
                 html_media_factory: Callable[[str], object] | None = None, conn=None, user_id: str | None = None):
        self._service = service
        self._media = media_factory or _pdf_media
        self._html_media = html_media_factory or _html_media
        self._folders: dict[tuple[str, str], str] = {}
        # With a user, the root folder's id is cached in their user_drive row (drive_layout.cached_root_folder_id).
        self._conn, self._user_id = conn, user_id

    @classmethod
    def from_credentials(cls) -> DriveClient:
        creds = load_credentials()
        from googleapiclient.discovery import build
        return cls(build("drive", "v3", credentials=creds, cache_discovery=False))

    @classmethod
    def for_user(cls, conn, user_id: str) -> DriveClient:
        """A client on the user's own Drive (their connected token). Raises DriveNotConfigured."""
        creds = load_user_credentials(conn, user_id)
        from googleapiclient.discovery import build
        return cls(build("drive", "v3", credentials=creds, cache_discovery=False), conn=conn, user_id=user_id)

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
                legacy_ok = parent == "root" and name == DEFAULT_ROOT and not os.environ.get("DRIVE_ROOT_FOLDER")
                cached = self._cached_root(name, legacy_ok) if parent == "root" else None
                if cached:
                    self._folders[key] = cached
                    parent = cached
                    continue
                found = self._find_folder(name, parent)
                if not found and legacy_ok:
                    # Files synced before the rename live under the legacy default root: keep one tree.
                    found = self._find_folder(LEGACY_ROOT, parent)
                if found:
                    self._folders[key] = found[0]["id"]
                else:
                    body = {"name": name, "mimeType": FOLDER_MIME, "parents": [parent]}
                    self._folders[key] = self._service.files().create(body=body, fields="id").execute()["id"]
                if parent == "root" and self._user_id:
                    cache_root_folder_id(self._conn, self._user_id, self._folders[key])
            parent = self._folders[key]
        return parent

    def _cached_root(self, name: str, legacy_ok: bool) -> str | None:
        """The user's cached root folder id, if it's still that folder (not trashed or renamed, and in this Drive)."""
        if not self._user_id:
            return None
        folder_id = cached_root_folder_id(self._conn, self._user_id)
        if not folder_id:
            return None
        meta = self._get(folder_id)
        names = (name, LEGACY_ROOT) if legacy_ok else (name,)
        if (meta and not meta.get("trashed") and meta.get("mimeType", FOLDER_MIME) == FOLDER_MIME
                and meta.get("name") in names):
            return folder_id
        return None

    def _find_folder(self, name: str, parent: str) -> list[dict]:
        return self._list(f"name = {_q(name)} and mimeType = {_q(FOLDER_MIME)} and {_q(parent)} in parents and trashed = false")

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
            f"and appProperties has {{ key='{PROP_PREFIX}job' and value={_q(target.job_key)} }} "
            f"and appProperties has {{ key='{PROP_PREFIX}kind' and value={_q(target.kind)} }}"
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
                    f"{PROP_PREFIX}job": target.job_key, f"{PROP_PREFIX}kind": target.kind,
                    f"{PROP_PREFIX}site": target.site[:100],
                },
            }
            meta = self._service.files().create(
                body=body, media_body=self._media(local_path), fields=_FILE_FIELDS,
            ).execute()
            created = True
        return DriveFile(id=meta["id"], url=meta.get("webViewLink", ""), md5=meta.get("md5Checksum"), created=created,
                         folder_id=folder_id)

    def upsert_doc(self, folder_id: str, name: str, html: str, known_id: str | None = None) -> DriveFile:
        """Write html as a Google Doc named `name` in folder_id, updating the existing Doc in place.

        The existing Doc is `known_id` (if it is still there), else a same-named Doc in the folder; with the
        drive.file scope both can only be Docs this app created.
        """
        existing = None
        if known_id:
            meta = self._get(known_id)
            if meta and not meta.get("trashed") and meta.get("mimeType", DOC_MIME) == DOC_MIME:
                existing = meta
        if existing is None:
            found = self._list(f"name = {_q(name)} and mimeType = {_q(DOC_MIME)} and {_q(folder_id)} in parents "
                               "and trashed = false")
            existing = found[0] if found else None
        if existing:
            meta = self._service.files().update(
                fileId=existing["id"], media_body=self._html_media(html), fields=_FILE_FIELDS,
            ).execute()
            created = False
        else:
            body = {"name": name, "parents": [folder_id], "mimeType": DOC_MIME}
            meta = self._service.files().create(
                body=body, media_body=self._html_media(html), fields=_FILE_FIELDS,
            ).execute()
            created = True
        return DriveFile(id=meta["id"], url=meta.get("webViewLink", ""), md5=meta.get("md5Checksum"), created=created,
                         folder_id=folder_id)

    def download(self, file_id: str, dest: Path) -> Path:
        data = self._service.files().get_media(fileId=file_id).execute()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return dest


def drive_status() -> tuple[str, str]:
    """("not_installed" | "not_authorized" | "authorized", detail) without touching the network."""
    if not drive_libraries_installed():
        return "not_installed", INSTALL_HINT
    if not config.GOOGLE_TOKEN_PATH.exists():
        return "not_authorized", "Run `strelitzia drive auth` (see README: Google Drive)"
    return "authorized", str(config.GOOGLE_TOKEN_PATH)
