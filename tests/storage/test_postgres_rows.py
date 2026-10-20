"""Result-scoped row metadata and cursor compatibility without a database server."""

import os
import weakref
from collections import namedtuple
from types import SimpleNamespace

import psycopg
import pytest
from psycopg.pq import TransactionStatus
from tianshu_storage import MemoryCursor, PostgresConnection, Row, StorageError
from tianshu_storage.postgres import Cursor

Column = namedtuple("Column", "name type_code")
PG_URL = os.environ.get("TIANSHU_TEST_POSTGRES_URL", "")
REAL_PG = pytest.mark.skipif(
    not PG_URL, reason="explicit isolated PostgreSQL URL required"
)


class Result:
    pass


class RawCursor:
    """Like psycopg, description creates fresh columns and each result has a token."""

    def __init__(self, columns, rows=(), *, token=True, rowcount=None):
        self.has_token = token
        self.closed = False
        self.description_reads = 0
        self.columns_created = 0
        self.replace_result(columns, rows, rowcount=rowcount)

    def replace_result(self, columns, rows=(), *, rowcount=None):
        rows = tuple(rows)
        self.columns = None if columns is None else tuple(columns)
        self.type_codes = tuple(range(len(columns or ())))
        self.rows = iter(rows)
        self.rowcount = len(rows) if rowcount is None else rowcount
        if self.has_token:
            self.pgresult = Result()

    @property
    def description(self):
        self.description_reads += 1
        if self.closed or self.columns is None:
            return None
        self.columns_created += len(self.columns)
        return [Column(name, kind) for name, kind in zip(self.columns, self.type_codes)]

    def fetchone(self):
        if self.closed:
            raise psycopg.InterfaceError("synthetic closed cursor")
        if self.columns is None:
            raise psycopg.ProgrammingError("synthetic no result")
        return next(self.rows, None)

    def close(self):
        self.closed = True
        self.rows = iter(())
        if self.has_token:
            self.pgresult = None


class RawConnection:
    def __init__(self):
        self.info = SimpleNamespace(transaction_status=TransactionStatus.IDLE)
        self.cursors = []

    def execute(self, statement, parameters=None, *, prepare=False):
        assert prepare is False
        if statement == "SELECT broken":
            raise psycopg.ProgrammingError("synthetic invalid query")
        if statement == "SELECT first":
            result = RawCursor(("FIRST",), [(1,), (2,)])
        elif statement == "SELECT second":
            result = RawCursor(("Second", "extra"), [(3, 4)])
        else:
            if statement == "BEGIN":
                self.info.transaction_status = TransactionStatus.INTRANS
            result = RawCursor(
                None, rowcount=1 if statement.startswith("INSERT") else 0
            )
        self.cursors.append(result)
        return result


@pytest.mark.parametrize("source", ["row", "memory", "cursor"])
def test_row_access_keeps_first_casefold_match_original_keys_and_value_sequence(source):
    columns = ("ID", "id", "Straße", "STRASSE", "tail")
    values = (11, 22, 33, 44, 55)
    if source == "row":
        row = Row(values, columns)
    elif source == "memory":
        row = MemoryCursor([values], columns).fetchone()
    else:
        row = Cursor(
            SimpleNamespace(row_factory=Row), RawCursor(columns, [values])
        ).fetchone()

    assert row["ID"] == row["Id"] == row["id"] == 11
    assert row["strasse"] == row["STRASSE"] == 33
    assert row[0] == 11 and row[-1] == 55 and row[1:3] == (22, 33)
    assert tuple(row) == values and len(row) == 5
    assert row.keys() == list(columns)
    keys = row.keys()
    keys.clear()
    assert row.keys() == list(columns)
    with pytest.raises(IndexError, match="No item with that key"):
        row["absent"]
    with pytest.raises(IndexError):
        row[5]
    with pytest.raises(TypeError):
        row._positions["id"] = 99


def test_direct_row_owns_its_values_and_column_snapshot():
    values, columns = [1], ["Original"]
    row = Row(values, columns)
    values[0], columns[0] = 2, "Changed"
    assert row["original"] == 1
    assert row.keys() == ["Original"]


def test_one_metadata_build_per_result_for_mixed_bulk_fetch_methods():
    raw = RawCursor(("id", "name", "value"), [(i, str(i), i * 2) for i in range(1000)])
    cursor = Cursor(SimpleNamespace(row_factory=Row), raw)
    cursor.arraysize = 4
    first = cursor.fetchone()
    batch = cursor.fetchmany()
    rest = cursor.fetchall()
    assert len(batch) == 4 and len(rest) == 995
    assert first["id"] == 0 and rest[-1]["value"] == 1998
    assert raw.description_reads == 1 and raw.columns_created == 3
    assert first._positions is batch[0]._positions is rest[-1]._positions
    assert cursor.fetchone() is None
    assert list(cursor) == []
    assert raw.description_reads == 1


def test_public_description_stays_live_and_retains_full_column_information():
    raw = RawCursor(("id",), [(1,), (2,)])
    cursor = Cursor(SimpleNamespace(row_factory=Row), raw)
    first = cursor.fetchone()
    before = cursor.description
    raw.type_codes = (999,)
    after = cursor.description
    assert before is not after
    assert before[0].name == after[0].name == "id"
    assert before[0].type_code == 0 and after[0].type_code == 999
    assert cursor.fetchone()["id"] == first["id"] + 1
    assert raw.description_reads == 3


def test_result_change_rebuilds_metadata_without_changing_earlier_rows():
    raw = RawCursor(("Before",), [(1,)])
    cursor = Cursor(SimpleNamespace(row_factory=Row), raw)
    before = cursor.fetchone()
    raw.replace_result(("After", "AFTER"), [(2, 3)])
    after = cursor.fetchone()
    assert before["before"] == 1 and before.keys() == ["Before"]
    assert after["after"] == 2 and after.keys() == ["After", "AFTER"]
    assert before._positions is not after._positions
    with pytest.raises(IndexError):
        before["after"]
    assert raw.description_reads == 2


def test_live_factory_can_switch_between_tuples_and_rows_mid_result():
    connection = SimpleNamespace(row_factory=None)
    raw = RawCursor(("id",), [(1,), (2,), (3,), (4,)])
    cursor = Cursor(connection, raw)
    assert cursor.fetchone() == (1,)
    assert raw.description_reads == 0
    connection.row_factory = object()
    first = cursor.fetchone()
    assert isinstance(first, Row) and first["ID"] == 2
    connection.row_factory = None
    assert cursor.fetchone() == (3,)
    connection.row_factory = Row
    last = cursor.fetchone()
    assert last["id"] == 4 and first._positions is last._positions
    assert raw.description_reads == 1


def test_tuple_mode_does_not_inspect_even_a_new_result_metadata():
    connection = SimpleNamespace(row_factory=None)
    raw = RawCursor(("old",), [(1,)])
    cursor = Cursor(connection, raw)
    assert cursor.fetchall() == [(1,)]
    raw.replace_result(("new", "extra"), [(2, 3)])
    assert cursor.fetchall() == [(2, 3)]
    assert raw.description_reads == 0 and raw.columns_created == 0


@pytest.mark.parametrize("token", [False, True])
def test_zero_column_rows_and_empty_result_are_distinct_from_no_result(token):
    raw = RawCursor((), [(), ()], token=token)
    cursor = Cursor(SimpleNamespace(row_factory=Row), raw)
    rows = cursor.fetchall()
    assert len(rows) == 2 and tuple(rows[0]) == () and rows[0].keys() == []
    assert rows[0]._positions is rows[1]._positions
    raw.replace_result(("empty",), [])
    assert cursor.fetchone() is None
    assert cursor.description[0].name == "empty"
    raw.replace_result(None)
    assert cursor.description is None
    with pytest.raises(StorageError, match="postgresql_operation_failed"):
        cursor.fetchone()


def test_cursor_without_pgresult_updates_metadata_from_dynamic_description():
    raw = RawCursor(("before",), [(1,), (2,)], token=False)
    cursor = Cursor(SimpleNamespace(row_factory=Row), raw)
    before, same = cursor.fetchall()
    assert before._positions is same._positions
    raw.replace_result(("after",), [(3,)])
    after = cursor.fetchone()
    assert after["after"] == 3 and before["before"] == 1
    assert before._positions is not after._positions
    assert raw.description_reads == 3


def test_execute_and_executemany_delegates_keep_result_and_count_lifecycles():
    raw = RawConnection()
    connection = PostgresConnection(raw)
    cursor = connection.cursor()
    assert cursor.execute("SELECT first") is cursor
    first = cursor.fetchone()
    assert first["first"] == 1 and cursor.rowcount == 2
    assert cursor.execute("SELECT second") is cursor
    assert cursor.description[0][0] == "Second"
    assert tuple(cursor.fetchone()) == (3, 4)
    assert first.keys() == ["FIRST"] and first["FIRST"] == 1
    assert cursor.executemany("INSERT item VALUES(?)", [(1,), (2,)]) is cursor
    assert cursor.rowcount == 2 and cursor.lastrowid is None
    assert cursor.description == [] and cursor.fetchall() == []
    assert connection.total_changes == 2
    cursor.executemany("INSERT item VALUES(?)", [])
    assert cursor.rowcount == 0 and cursor.fetchone() is None


def test_failed_proxy_execute_preserves_previous_delegate_and_rows():
    cursor = PostgresConnection(RawConnection()).cursor().execute("SELECT first")
    old = cursor.fetchone()
    with pytest.raises(StorageError, match="postgresql_operation_failed"):
        cursor.execute("SELECT broken")
    assert cursor.fetchone()["first"] == 2 and old["first"] == 1


def test_close_releases_result_reference_and_preserves_materialized_rows():
    raw = RawCursor(("id",), [(1,)])
    result = weakref.ref(raw.pgresult)
    cursor = Cursor(SimpleNamespace(row_factory=Row), raw)
    row = cursor.fetchone()
    cursor.close()
    assert result() is None
    assert row["id"] == 1 and row.keys() == ["id"]
    assert cursor.description is None
    with pytest.raises(StorageError, match="postgresql_operation_failed"):
        cursor.fetchone()


def test_proxy_close_closes_current_delegate():
    connection = PostgresConnection(RawConnection())
    cursor = connection.cursor().execute("SELECT first")
    row = cursor.fetchone()
    cursor.close()
    assert connection.raw.cursors[-1].closed
    assert row["FIRST"] == 1


def test_memory_cursor_reuses_metadata_and_keeps_noop_close_and_counts():
    cursor = MemoryCursor([(1, 2), (3, 4)], (name for name in ("Name", "NAME")), 7, 42)
    first = cursor.fetchone()
    cursor.close()
    second = cursor.fetchone()
    assert cursor.rowcount == 7 and cursor.lastrowid == 42
    assert [column[0] for column in cursor.description] == ["Name", "NAME"]
    assert first["name"] == 1 and second["name"] == 3
    assert first._positions is second._positions
    assert cursor.fetchone() is None


@REAL_PG
def test_real_postgres_rows_across_fetch_modes_factories_and_result_changes():
    statement = """
        SELECT n AS "ID", n + 1000 AS id, 'row-' || n::text AS label,
               n + 2000 AS "Straße", n + 3000 AS "STRASSE"
          FROM generate_series(1, 1000) AS series(n) ORDER BY n;
        SELECT 42 AS "Changed", 43 AS changed
    """
    with psycopg.connect(PG_URL, autocommit=True, connect_timeout=3) as raw:
        connection = PostgresConnection(raw)
        cursor = connection.native(statement)
        try:
            first = cursor.fetchone()
            assert first["id"] == 1 and first["strasse"] == 2001
            assert first.keys() == ["ID", "id", "label", "Straße", "STRASSE"]
            before = cursor.description
            assert before is not cursor.description and before[0].name == "ID"
            connection.row_factory = None
            assert cursor.fetchone() == (2, 1002, "row-2", 2002, 3002)
            connection.row_factory = Row
            cursor.arraysize = 31
            batch = cursor.fetchmany()
            rest = cursor.fetchall()
            assert len(batch) == 31 and len(rest) == 967
            assert batch[0]["id"] == 3 and rest[-1]["id"] == 1000
            assert first._positions is batch[0]._positions is rest[-1]._positions
            assert cursor.raw.nextset() is True
            assert cursor.description[0].name == "Changed"
            second = cursor.fetchone()
            assert second["changed"] == 42 and second.keys() == ["Changed", "changed"]
            assert second._positions is not first._positions
            assert first["ID"] == 1 and first.keys()[0] == "ID"
            cursor.raw.execute('SELECT 77 AS "Third"', prepare=False)
            assert cursor.description[0].name == "Third"
            assert cursor.fetchone()["third"] == 77
            assert second["CHANGED"] == 42 and second.keys() == ["Changed", "changed"]
        finally:
            cursor.close()
        assert first["Id"] == 1 and second["changed"] == 42
