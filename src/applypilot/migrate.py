"""Copy the jobs table from a SQLite file into another database (normally the Postgres job store) and check it.

    applypilot db migrate --from ~/.applypilot/applypilot.db --to "$APPLYPILOT_DATABASE_URL"
    applypilot db verify  --from ~/.applypilot/applypilot.db --to "$APPLYPILOT_DATABASE_URL"

`migrate` is safe to rerun: rows whose url is already in the destination are skipped, so a second run only
copies the jobs added since the first one. The SQLite file is never modified, and it's backed up first.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import date, datetime
from pathlib import Path

from applypilot.database import Connection, get_connection, init_db, table_columns

log = logging.getLogger(__name__)

# Non-null counts compared by `verify`, besides the row count.
VERIFY_COLUMNS = ("full_description", "fit_score", "tailored_resume_path", "resume_drive_url")


def _open_source(src: Path) -> sqlite3.Connection:
    if not src.exists():
        raise FileNotFoundError(f"No SQLite database at {src}")
    conn = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def backup_source(src: Path, today: date | None = None) -> Path:
    """Copy the SQLite file to `<src>.bak-<YYYY-MM-DD>` with the online backup API (safe while it's in use)."""
    dst = src.with_name(f"{src.name}.bak-{(today or datetime.now().astimezone().date()).isoformat()}")
    source = _open_source(src)
    target = sqlite3.connect(dst)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    return dst


def _count(conn, column: str | None = None) -> int:
    expr = f"COUNT({column})" if column else "COUNT(*)"
    return conn.execute(f"SELECT {expr} FROM jobs").fetchone()[0]


def migrate(src_sqlite: Path, dst_url: str, batch: int = 500, backup: bool = True) -> dict:
    """Copy every job from `src_sqlite` into `dst_url`, skipping urls the destination already has.

    Only columns both sides have are copied; source-only columns are logged and returned under "missing_columns".

    Returns:
        {"source": rows in the source, "inserted": rows added, "skipped": rows already there,
         "destination": rows in the destination afterwards, "missing_columns": [...]}.
    """
    src_sqlite = Path(src_sqlite).expanduser()
    backup_path = backup_source(src_sqlite) if backup else None
    source = _open_source(src_sqlite)
    try:
        dst: Connection = init_db(dst_url)
        src_cols = [r[1] for r in source.execute("PRAGMA table_info(jobs)").fetchall()]
        dst_cols = table_columns(dst)
        cols = [c for c in src_cols if c in dst_cols]
        missing = [c for c in src_cols if c not in dst_cols]
        if missing:
            log.warning("Source columns missing in the destination, not copied: %s", ", ".join(missing))

        before = _count(dst)
        col_list = ", ".join(cols)
        insert = (
            f"INSERT INTO jobs ({col_list}) VALUES ({', '.join('?' * len(cols))}) "
            "ON CONFLICT (url) DO NOTHING"
        )
        cur = source.execute(f"SELECT {col_list} FROM jobs ORDER BY url")
        n_source = 0
        while rows := cur.fetchmany(batch):
            dst.executemany(insert, [tuple(r) for r in rows])
            dst.commit()
            n_source += len(rows)
        after = _count(dst)
    finally:
        source.close()

    result = {
        "source": n_source,
        "inserted": after - before,
        "skipped": n_source - (after - before),
        "destination": after,
        "missing_columns": missing,
    }
    if backup_path:
        result["backup"] = str(backup_path)
    return result


def verify(src_sqlite: Path, dst_url: str) -> list[tuple[str, int, int]]:
    """Compare the row count and the non-null counts of VERIFY_COLUMNS. Returns (check, source, destination) rows."""
    source = _open_source(Path(src_sqlite).expanduser())
    try:
        dst = get_connection(dst_url)
        dst_cols = table_columns(dst)
        src_cols = {r[1] for r in source.execute("PRAGMA table_info(jobs)").fetchall()}
        checks = [("jobs", _count(source), _count(dst))]
        for col in VERIFY_COLUMNS:
            if col in src_cols or col in dst_cols:
                checks.append((
                    f"jobs.{col}",
                    _count(source, col) if col in src_cols else 0,
                    _count(dst, col) if col in dst_cols else 0,
                ))
    finally:
        source.close()
    return checks
