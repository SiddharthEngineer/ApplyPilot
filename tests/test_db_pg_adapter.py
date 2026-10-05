"""Tests for the Postgres adapter that need no Postgres server: SQL translation, PgRow, transaction handling."""

import subprocess
import sys

import pytest

from applypilot.db_pg import PgConnection, PgRow, describe_url, pg_row_factory, translate_sql


class TestTranslateSql:
    def test_qmark_placeholders(self):
        assert translate_sql("SELECT * FROM jobs WHERE url = ? AND fit_score >= ?") == (
            "SELECT * FROM jobs WHERE url = %s AND fit_score >= %s"
        )

    def test_qmark_inside_literal_is_kept(self):
        assert translate_sql("SELECT '?' AS q, \"a?b\" FROM jobs WHERE url = ?") == (
            "SELECT '?' AS q, \"a?b\" FROM jobs WHERE url = %s"
        )

    def test_doubled_quote_inside_literal(self):
        assert translate_sql("SELECT 'it''s ?' WHERE x = ?") == "SELECT 'it''s ?' WHERE x = %s"

    def test_like_percent_is_escaped(self):
        assert translate_sql("SELECT * FROM jobs WHERE strategy LIKE 'ats_%' AND title LIKE ?") == (
            "SELECT * FROM jobs WHERE strategy LIKE 'ats_%%' AND title LIKE %s"
        )
        assert translate_sql("SELECT 100 % 7") == "SELECT 100 %% 7"

    def test_named_params(self):
        assert translate_sql("UPDATE jobs SET title = :title WHERE url = :url") == (
            "UPDATE jobs SET title = %(title)s WHERE url = %(url)s"
        )

    def test_casts_and_timestamps_are_not_params(self):
        assert translate_sql("SELECT now()::text, '10:30:00' WHERE a = ?") == "SELECT now()::text, '10:30:00' WHERE a = %s"

    def test_comment_is_kept(self):
        assert translate_sql("SELECT 1 -- what? 50%\nWHERE a = ?") == "SELECT 1 -- what? 50%%\nWHERE a = %s"


class _Col:
    def __init__(self, name):
        self.name = name


class _FakeCursorDesc:
    description = (_Col("url"), _Col("title"))


class TestPgRow:
    def _row(self):
        return pg_row_factory(_FakeCursorDesc())(("u1", "Engineer"))

    def test_index_by_position_and_name(self):
        row = self._row()
        assert row[0] == "u1" and row[1] == "Engineer"
        assert row["url"] == "u1" and row["title"] == "Engineer"
        assert row[-1] == "Engineer"

    def test_keys_and_dict(self):
        row = self._row()
        assert row.keys() == ["url", "title"]
        assert dict(row) == {"url": "u1", "title": "Engineer"}
        assert dict(zip(row.keys(), row)) == {"url": "u1", "title": "Engineer"}

    def test_len_iter_unpack(self):
        url, title = self._row()
        assert (url, title) == ("u1", "Engineer")
        assert len(self._row()) == 2

    def test_missing_key(self):
        with pytest.raises(IndexError):
            self._row()["nope"]

    def test_dict_of_two_column_rows(self):
        rows = [PgRow(["url", "company"], ("u1", "NVIDIA")), PgRow(["url", "company"], ("u2", None))]
        assert dict(rows) == {"u1": "NVIDIA", "u2": None}


class _FakeRawCursor:
    def __init__(self, log, fail_on=None):
        self.log, self.fail_on = log, fail_on
        self.description = None
        self.rowcount = 1

    def execute(self, query, args=None):
        self.log.append(query)
        if self.fail_on and self.fail_on in query:
            raise RuntimeError("duplicate key")
        return self

    def executemany(self, query, rows):
        self.log.append(f"MANY {query} x{len(rows)}")


class _FakeRawConn:
    def __init__(self, fail_on=None):
        self.log: list[str] = []
        self.fail_on = fail_on
        self.closed = False

    def execute(self, query, args=None):
        return _FakeRawCursor(self.log, self.fail_on).execute(query, args)

    def cursor(self):
        return _FakeRawCursor(self.log, self.fail_on)

    def close(self):
        self.closed = True


def _conn(fail_on=None):
    raw = _FakeRawConn(fail_on)
    return PgConnection("postgresql://u:p@h/db", connect=lambda url, **kw: raw), raw


class TestTransactions:
    def test_reads_run_outside_a_transaction(self):
        conn, raw = _conn()
        conn.execute("SELECT 1")
        assert raw.log == ["SELECT 1"]
        assert not conn.in_transaction

    def test_first_write_opens_a_transaction_until_commit(self):
        conn, raw = _conn()
        conn.execute("INSERT INTO jobs (url) VALUES (?)", ("u1",))
        conn.execute("SELECT 1")
        conn.commit()
        assert raw.log[0] == "BEGIN"
        assert raw.log[1] == "INSERT INTO jobs (url) VALUES (%s)"
        assert raw.log[-1] == "COMMIT"
        assert not conn.in_transaction

    def test_failed_write_keeps_earlier_writes(self):
        conn, raw = _conn(fail_on="'dup'")
        conn.execute("INSERT INTO jobs (url) VALUES ('ok')")
        with pytest.raises(RuntimeError):
            conn.execute("INSERT INTO jobs (url) VALUES ('dup')")
        assert conn.in_transaction
        assert any(q.startswith("ROLLBACK TO SAVEPOINT") for q in raw.log)
        assert "ROLLBACK" not in raw.log
        conn.commit()
        assert raw.log[-1] == "COMMIT"

    def test_failed_first_write_rolls_back(self):
        conn, raw = _conn(fail_on="'dup'")
        with pytest.raises(RuntimeError):
            conn.execute("INSERT INTO jobs (url) VALUES ('dup')")
        assert raw.log[-1] == "ROLLBACK"
        assert not conn.in_transaction

    def test_commit_without_transaction_is_a_noop(self):
        conn, raw = _conn()
        conn.commit()
        conn.rollback()
        assert raw.log == []

    def test_executemany(self):
        conn, raw = _conn()
        conn.executemany("INSERT INTO jobs VALUES (?, ?)", [("a", 1), ("b", 2)])
        assert raw.log == ["BEGIN", "MANY INSERT INTO jobs VALUES (%s, %s) x2"]

    def test_begin_immediate_is_a_plain_begin(self):
        conn, raw = _conn()
        conn.execute("BEGIN IMMEDIATE")
        assert conn.in_transaction and raw.log == ["BEGIN"]
        conn.execute("SELECT url FROM jobs LIMIT 1 FOR UPDATE SKIP LOCKED")
        conn.rollback()
        assert raw.log[-1] == "ROLLBACK"

    def test_close_rolls_back(self):
        conn, raw = _conn()
        conn.execute("UPDATE jobs SET title = ?", ("x",))
        conn.close()
        assert raw.log[-1] == "ROLLBACK" and raw.closed


def test_nul_bytes_are_stripped_from_params():
    from applypilot.db_pg import _params

    assert _params(("a\x00b", 1, None)) == ("ab", 1, None)
    assert _params({"t": "x\x00"}) == {"t": "x"}


def test_describe_url_hides_password():
    assert describe_url("postgresql://applypilot:secret@127.0.0.1:5432/applypilot") == "applypilot@127.0.0.1"


def test_database_imports_without_psycopg():
    code = (
        "import sys; sys.modules['psycopg'] = None\n"
        "import applypilot.database, applypilot.db_pg\n"
        "print('ok')"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "ok"
