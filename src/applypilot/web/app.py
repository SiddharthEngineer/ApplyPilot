"""FastAPI app for the job dashboard. Every route lives under /app, which nginx proxies unchanged.

  /app/api/...   JSON API
  /app/...       the built UI (index.html + assets), with unknown paths falling back to index.html

Mutating requests need the header `X-ApplyPilot: 1`. nginx puts basic auth in front, and browsers resend
basic-auth credentials on cross-site form posts, but a cross-site form can't set a custom header.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse

from applypilot import __version__
from applypilot.database import Connection, backend_name, get_connection, init_db

PREFIX = "/app"
CSRF_HEADER = "X-ApplyPilot"
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

api = APIRouter(prefix=f"{PREFIX}/api")


def db_conn(request: Request) -> Connection:
    """This thread's connection to the app's database (each threadpool worker gets its own)."""
    return get_connection(request.app.state.db)


@api.get("/health")
def health(request: Request) -> dict:
    conn = db_conn(request)
    conn.execute("SELECT 1").fetchone()
    return {"ok": True, "db": backend_name(conn), "version": __version__}


def _default_static_dir() -> Path | None:
    value = os.environ.get("APPLYPILOT_WEB_DIR")
    return Path(value) if value else None


def create_app(static_dir: Path | str | None = None, db: Path | str | None = None) -> FastAPI:
    """The dashboard app.

    static_dir: the built UI (default: $APPLYPILOT_WEB_DIR); without it only the API is served.
    db: a SQLite path or database URL (default: APPLYPILOT_DATABASE_URL, else the SQLite DB_PATH).
    """
    app = FastAPI(title="ApplyPilot dashboard", version=__version__,
                  docs_url=f"{PREFIX}/api/docs", openapi_url=f"{PREFIX}/api/openapi.json", redoc_url=None)
    app.state.db = db
    init_db(db)

    @app.middleware("http")
    async def csrf_guard(request: Request, call_next):
        if request.method not in _SAFE_METHODS and request.headers.get(CSRF_HEADER) != "1":
            return JSONResponse({"detail": f"Missing {CSRF_HEADER}: 1 header"}, status_code=403)
        return await call_next(request)

    app.include_router(api)
    _mount_ui(app, Path(static_dir) if static_dir else _default_static_dir())
    return app


def _mount_ui(app: FastAPI, static_dir: Path | None) -> None:
    root = static_dir.resolve() if static_dir else None

    @app.get(PREFIX, include_in_schema=False)
    def ui_root_redirect():
        return RedirectResponse(f"{PREFIX}/")

    @app.get(PREFIX + "/{path:path}", include_in_schema=False)
    def ui(path: str):
        if path == "api" or path.startswith("api/"):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        if root is None or not (root / "index.html").is_file():
            return JSONResponse({"detail": "Dashboard UI not built (set APPLYPILOT_WEB_DIR)"}, status_code=404)
        if path:
            candidate = (root / path).resolve()
            if candidate.is_relative_to(root) and candidate.is_file():
                return FileResponse(candidate)
            if path.startswith("assets/"):  # a missing asset is a real 404, not the SPA page
                return JSONResponse({"detail": "Not Found"}, status_code=404)
        return FileResponse(root / "index.html")
