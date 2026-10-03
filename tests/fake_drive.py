"""In-memory fake of the Drive v3 `files()` resource, for hermetic tests.

Understands the query clauses used by applypilot.storage.drive. Pass `media_factory=fake_media`
to DriveClient so uploads carry the file's bytes.
"""

import hashlib
import itertools
import re
from collections import Counter
from pathlib import Path

_CLAUSES = [
    (re.compile(r"name = '((?:[^'\\]|\\.)*)'"), lambda f, v: f["name"] == v),
    (re.compile(r"mimeType = '((?:[^'\\]|\\.)*)'"), lambda f, v: f["mimeType"] == v),
    (re.compile(r"'((?:[^'\\]|\\.)*)' in parents"), lambda f, v: v in f["parents"]),
    (re.compile(r"appProperties has \{ key='([^']*)' and value='((?:[^'\\]|\\.)*)' \}"),
     lambda f, k, v: f.get("appProperties", {}).get(k) == v),
]


def _unescape(s: str) -> str:
    return re.sub(r"\\(.)", r"\1", s)


def fake_media(path) -> bytes:
    return Path(path).read_bytes()


class _Req:
    def __init__(self, fn):
        self._fn = fn

    def execute(self):
        return self._fn()


class FakeNotFound(Exception):
    pass


class FakeFiles:
    def __init__(self, drive):
        self._d = drive

    def list(self, q, **kw):
        self._d.calls["list"] += 1

        def run():
            files = [f for f in self._d.store.values() if self._d.matches(f, q)]
            return {"files": [self._d.public(f) for f in files]}
        return _Req(run)

    def create(self, body, media_body=None, fields=None):
        self._d.calls["create"] += 1

        def run():
            f = {
                "id": f"id{next(self._d.ids)}", "name": body["name"], "mimeType": body.get("mimeType", ""),
                "parents": list(body.get("parents", ["root"])), "trashed": False,
                "appProperties": dict(body.get("appProperties", {})), "content": media_body,
            }
            self._d.store[f["id"]] = f
            return self._d.public(f)
        return _Req(run)

    def update(self, fileId, media_body=None, fields=None, body=None):
        self._d.calls["update"] += 1

        def run():
            f = self._d.store[fileId]
            if media_body is not None:
                f["content"] = media_body
            return self._d.public(f)
        return _Req(run)

    def get(self, fileId, fields=None):
        self._d.calls["get"] += 1

        def run():
            if fileId not in self._d.store:
                raise FakeNotFound(fileId)
            return self._d.public(self._d.store[fileId])
        return _Req(run)

    def get_media(self, fileId):
        self._d.calls["get_media"] += 1
        return _Req(lambda: self._d.store[fileId]["content"])


class FakeDrive:
    """A fake Drive service: `FakeDrive().files()` behaves like `service.files()`."""

    def __init__(self, corrupt_md5: bool = False):
        self.store: dict[str, dict] = {}
        self.calls: Counter = Counter()
        self.ids = itertools.count(1)
        self.corrupt_md5 = corrupt_md5

    def files(self):
        return FakeFiles(self)

    def matches(self, f: dict, q: str) -> bool:
        if "trashed = false" in q and f["trashed"]:
            return False
        for pattern, test in _CLAUSES:
            for m in pattern.finditer(q):
                if not test(f, *(_unescape(g) for g in m.groups())):
                    return False
        return True

    def public(self, f: dict) -> dict:
        out = {k: v for k, v in f.items() if k != "content"}
        if f["mimeType"] != "application/vnd.google-apps.folder" and f["content"] is not None:
            out["md5Checksum"] = "0" * 32 if self.corrupt_md5 else hashlib.md5(f["content"]).hexdigest()
            out["webViewLink"] = f"https://drive.google.com/file/d/{f['id']}/view"
        return out

    def folders(self) -> list[dict]:
        return [f for f in self.store.values() if f["mimeType"] == "application/vnd.google-apps.folder"]

    def pdfs(self) -> list[dict]:
        return [f for f in self.store.values() if f["mimeType"] == "application/pdf"]

    def path_of(self, file_id: str) -> list[str]:
        parts = []
        f = self.store[file_id]
        while True:
            parts.append(f["name"])
            parent = f["parents"][0]
            if parent == "root":
                return list(reversed(parts))
            f = self.store[parent]
