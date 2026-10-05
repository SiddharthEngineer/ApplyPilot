"""Postgres backend: a psycopg connection that behaves like the `sqlite3.Connection` ApplyPilot uses.

ApplyPilot's SQL is written for sqlite3 (`?` placeholders, `sqlite3.Row` rows, implicit transactions that
start at the first INSERT/UPDATE/DELETE and end at `commit()`). `PgConnection` keeps all of that working on
Postgres, so call sites don't change:

- `translate_sql` turns `?` into `%s` and `:name` into `%(name)s`, and escapes literal `%`.
- Rows are `PgRow`, which supports `row[0]`, `row["col"]`, `keys()` and `dict(row)`.
- The connection runs in autocommit mode and opens a transaction only for writes, like sqlite3. A read never
  leaves the session "idle in transaction", so another process's `ALTER TABLE` isn't blocked by it.
- A failed write inside a transaction is rolled back to a savepoint, so the transaction survives it. That keeps
  the `try: INSERT … except IntegrityError:` pattern working, as it does on SQLite.

psycopg is imported lazily: the base install (SQLite only) doesn't need it.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping, Sequence
from typing import Any, Self
from urllib.parse import urlsplit

_WRITE_VERBS = ("INSERT", "UPDATE", "DELETE", "REPLACE")
_NAMED = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def translate_sql(sql: str) -> str:
    """Rewrite sqlite3 paramstyle SQL for psycopg.

    `?` becomes `%s`, `:name` becomes `%(name)s`, and `%` becomes `%%`. Text inside single-quoted literals,
    double-quoted identifiers and `--` comments is left alone except for `%` escaping (psycopg parses `%`
    everywhere). `::` casts are kept.
    """
    out: list[str] = []
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        if ch in ("'", '"'):
            end = i + 1
            while end < n:
                if sql[end] == ch:
                    if end + 1 < n and sql[end + 1] == ch:  # doubled quote = escaped quote
                        end += 2
                        continue
                    break
                end += 1
            out.append(sql[i:end + 1].replace("%", "%%"))
            i = end + 1
        elif ch == "-" and sql.startswith("--", i):
            end = sql.find("\n", i)
            end = n if end == -1 else end
            out.append(sql[i:end].replace("%", "%%"))
            i = end
        elif ch == "?":
            out.append("%s")
            i += 1
        elif ch == "%":
            out.append("%%")
            i += 1
        elif ch == ":":
            if sql.startswith("::", i):
                out.append("::")
                i += 2
                continue
            m = _NAMED.match(sql, i + 1)
            if m and (i == 0 or not (sql[i - 1].isalnum() or sql[i - 1] == "_")):
                out.append(f"%({m.group(0)})s")
                i = m.end()
            else:
                out.append(":")
                i += 1
        else:
            out.append(ch)
            i += 1
    return "".join(out)


class PgRow(Sequence):
    """A result row that acts like `sqlite3.Row`: index by position or column name, `keys()`, `dict(row)`."""

    __slots__ = ("_index", "_keys", "_values")

    def __init__(self, keys: Sequence[str], values: Sequence[Any], index: Mapping[str, int] | None = None):
        self._keys = tuple(keys)
        self._values = tuple(values)
        self._index = index if index is not None else {k: i for i, k in enumerate(self._keys)}

    def __getitem__(self, key):  # type: ignore[override]
        if isinstance(key, str):
            try:
                return self._values[self._index[key]]
            except KeyError:
                raise IndexError(f"No item with that key: {key!r}") from None
        return self._values[key]

    def __len__(self) -> int:
        return len(self._values)

    def __iter__(self) -> Iterator[Any]:
        return iter(self._values)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, PgRow):
            return self._keys == other._keys and self._values == other._values
        return NotImplemented

    def __hash__(self) -> int:
        return hash((self._keys, self._values))

    def __repr__(self) -> str:
        return f"PgRow({dict(zip(self._keys, self._values))!r})"

    def keys(self) -> list[str]:
        return list(self._keys)


def pg_row_factory(cursor) -> Any:
    """psycopg row factory producing `PgRow`s."""
    desc = cursor.description
    if desc is None:
        return lambda values: values
    keys = [c.name for c in desc]
    index = {k: i for i, k in enumerate(keys)}
    return lambda values: PgRow(keys, values, index)


class PgCursor:
    """The cursor returned by `PgConnection.execute`: `fetchone`, `fetchall`, iteration, `rowcount`, `description`."""

    def __init__(self, cursor):
        self._cur = cursor

    @property
    def rowcount(self) -> int:
        return self._cur.rowcount

    @property
    def description(self):
        return self._cur.description

    @property
    def lastrowid(self) -> None:
        return None  # jobs is keyed by url; there is no rowid on Postgres

    def fetchone(self):
        return self._cur.fetchone() if self._cur.description is not None else None

    def fetchall(self) -> list:
        return self._cur.fetchall() if self._cur.description is not None else []

    def fetchmany(self, size: int = 1) -> list:
        return self._cur.fetchmany(size) if self._cur.description is not None else []

    def __iter__(self):
        return iter(self.fetchall())

    def close(self) -> None:
        self._cur.close()


def _is_write(sql: str) -> bool:
    head = sql.lstrip().split(None, 1)
    return bool(head) and head[0].upper() in _WRITE_VERBS


def _params(params: Sequence | Mapping | None):
    # Always pass a params object: psycopg only parses placeholders and `%%` when params is not None.
    if params is None:
        return ()
    if isinstance(params, Mapping):
        return {k: _clean(v) for k, v in params.items()}
    return tuple(_clean(v) for v in params)


def _clean(value):
    # Postgres text can't hold NUL bytes (SQLite can); scraped pages occasionally contain them.
    if isinstance(value, str) and "\x00" in value:
        return value.replace("\x00", "")
    return value


class PgConnection:
    """A Postgres connection with the subset of the `sqlite3.Connection` API ApplyPilot uses."""

    backend = "postgresql"

    def __init__(self, url: str, connect=None):
        if connect is None:
            import psycopg

            connect = psycopg.connect
        self.url = url
        self._conn = connect(url, autocommit=True, row_factory=pg_row_factory)
        self._in_tx = False
        self._sp = 0

    # -- sqlite3.Connection API -------------------------------------------------

    def execute(self, sql: str, params: Sequence | Mapping | None = ()) -> PgCursor:
        if sql.lstrip()[:5].upper() == "BEGIN":
            # sqlite3's `BEGIN IMMEDIATE`/`EXCLUSIVE` take a write lock; on Postgres it's a plain BEGIN, and
            # callers that need row locks add `FOR UPDATE` to their SELECT.
            if self._in_tx:
                return PgCursor(self._conn.cursor())
            cur = self._conn.execute("BEGIN")
            self._in_tx = True
            return PgCursor(cur)
        query = translate_sql(sql)
        args = _params(params)
        if not self._in_tx and not _is_write(sql):
            return PgCursor(self._conn.execute(query, args))
        return PgCursor(self._run_in_tx(lambda cur: cur.execute(query, args)))

    def executemany(self, sql: str, seq_of_params) -> PgCursor:
        query = translate_sql(sql)
        rows = [_params(p) for p in seq_of_params]
        return PgCursor(self._run_in_tx(lambda cur: cur.executemany(query, rows)))

    def commit(self) -> None:
        if self._in_tx:
            self._conn.execute("COMMIT")
            self._in_tx = False

    def rollback(self) -> None:
        if self._in_tx:
            self._conn.execute("ROLLBACK")
            self._in_tx = False

    def close(self) -> None:
        try:
            self.rollback()
        finally:
            self._conn.close()

    @property
    def in_transaction(self) -> bool:
        return self._in_tx

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        # Like sqlite3: commit on success, roll back on error, keep the connection open.
        if exc_type is None:
            self.commit()
        else:
            self.rollback()

    # -- internals --------------------------------------------------------------

    def _run_in_tx(self, run):
        cur = self._conn.cursor()
        if not self._in_tx:
            # First write of a transaction: on failure there's nothing earlier to keep, so roll it all back.
            self._conn.execute("BEGIN")
            self._in_tx = True
            try:
                run(cur)
            except Exception:
                self.rollback()
                raise
            return cur
        self._sp += 1
        sp = f"applypilot_sp{self._sp}"
        self._conn.execute(f"SAVEPOINT {sp}")
        try:
            run(cur)
        except Exception:
            self._conn.execute(f"ROLLBACK TO SAVEPOINT {sp}")
            self._conn.execute(f"RELEASE SAVEPOINT {sp}")
            raise
        self._conn.execute(f"RELEASE SAVEPOINT {sp}")
        return cur


def describe_url(url: str) -> str:
    """`user@host` for a database URL, without the password (for `doctor`)."""
    parts = urlsplit(url)
    host = parts.hostname or "localhost"
    return f"{parts.username}@{host}" if parts.username else host
