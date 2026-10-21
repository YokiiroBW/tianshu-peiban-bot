"""Independent SELECT confirmation, transaction ownership and batch failures."""

import os
from types import SimpleNamespace

import psycopg
import pytest
from psycopg.pq import TransactionStatus
from tianshu_storage import IntegrityError, PostgresConnection, Row, StorageError

PG_URL = os.environ.get("TIANSHU_TEST_POSTGRES_URL", "")
REAL_PG = pytest.mark.skipif(
    not PG_URL, reason="explicit isolated PostgreSQL URL required"
)


class RawCursor:
    def __init__(self, columns, rows):
        self.description = [SimpleNamespace(name=name) for name in columns]
        self.pgresult = object()
        self.rows = iter(rows)
        self.rowcount = len(rows)
        self.ready = self.closed = False
        self.fetches = self.closes = 0
        self.close_error = False

    def fetchone(self):
        assert self.ready and not self.closed
        self.fetches += 1
        return next(self.rows, None)

    def close(self):
        self.closed = True
        self.closes += 1
        if self.close_error:
            raise psycopg.InterfaceError("synthetic secret cursor close error")


class Pipeline:
    def __init__(self, raw):
        self.raw = raw

    def __enter__(self):
        raw = self.raw
        raw.events.append(("enter",))
        if raw.enter_error:
            raise raw.enter_error
        assert not raw.in_pipeline
        self.first_cursor = len(raw.cursors)
        raw.in_pipeline = True

    def __exit__(self, kind, error, traceback):
        raw = self.raw
        raw.events.append(("sync",))
        raw.in_pipeline = False
        cursors = raw.cursors[self.first_cursor :]
        assert all(cursor.fetches == 0 for cursor in cursors)
        if raw.deferred_error:
            raw.info.transaction_status = TransactionStatus.INERROR
            if error is None:
                raise raw.deferred_error
        for cursor in cursors:
            cursor.ready = True


class RawConnection:
    def __init__(self, results=()):
        self.info = SimpleNamespace(transaction_status=TransactionStatus.INTRANS)
        self.results = list(results)
        self.events = []
        self.cursors = []
        self.in_pipeline = self.closed = False
        self.enter_error = self.deferred_error = self.execute_error = None
        self.execute_error_at = 2
        self.cursor_close_error = False
        self.rollbacks = self.commits = self.closes = 0

    def pipeline(self):
        self.events.append(("pipeline",))
        return Pipeline(self)

    def execute(self, statement, parameters=None, *, prepare=None):
        self.events.append(("execute", statement, parameters, prepare))
        if self.execute_error and len(self.cursors) + 1 == self.execute_error_at:
            raise self.execute_error
        if statement == "BEGIN":
            assert not self.in_pipeline
            self.info.transaction_status = TransactionStatus.INTRANS
            return RawCursor((), [])
        assert self.in_pipeline
        columns, rows = (
            self.results[len(self.cursors)]
            if len(self.cursors) < len(self.results)
            else (("value",), [(len(self.cursors),)])
        )
        cursor = RawCursor(columns, rows)
        cursor.close_error = self.cursor_close_error
        self.cursors.append(cursor)
        return cursor

    def rollback(self):
        self.rollbacks += 1
        self.info.transaction_status = TransactionStatus.IDLE

    def commit(self):
        self.commits += 1
        self.info.transaction_status = TransactionStatus.IDLE

    def close(self):
        self.closes += 1
        self.closed = True


def test_read_batch_confirms_all_results_in_order_without_consuming_or_using_dialect():
    raw = RawConnection(
        [
            (("ID", "id"), [(11, 22), (33, 44)]),
            (("value",), [("雪?",), (None,)]),
            (("answer",), [(3,)]),
        ]
    )
    releases = []

    def dialect(*args):
        pytest.fail("native read_batch must bypass product dialects")

    connection = PostgresConnection(raw, release=releases.append, dialect=dialect)
    cursors = connection.read_batch(
        iter(
            [
                ("SELECT 11, 22", ()),
                ("\n select ? UNION ALL SELECT ?", ["雪?", None]),
                ("SELECT ? /* 90% ? */", (3,)),
            ]
        )
    )
    assert [cursor.raw for cursor in cursors] == raw.cursors
    assert all(cursor.ready and cursor.fetches == 0 for cursor in raw.cursors)
    assert [event for event in raw.events if event[0] == "execute"] == [
        ("execute", "SELECT 11, 22", None, False),
        ("execute", "\n select %s UNION ALL SELECT %s", ["雪?", None], False),
        ("execute", "SELECT %s /* 90%% ? */", (3,), False),
    ]
    assert raw.events[-1] == ("sync",)
    row = cursors[0].fetchone()
    assert isinstance(row, Row) and row["id"] == 11 and row.keys() == ["ID", "id"]
    connection.row_factory = None
    assert cursors[0].fetchall() == [(33, 44)]
    assert cursors[1].fetchall() == [("雪?",), (None,)]
    connection.row_factory = Row
    assert cursors[2].fetchone()["answer"] == 3
    assert connection.total_changes == raw.rollbacks == raw.commits == 0
    assert releases == [] and not connection._closed


@pytest.mark.parametrize(
    ("invalid_request", "error"),
    [
        (("UPDATE records SET value=1", ()), ValueError),
        (("INSERT INTO records VALUES(1)", ()), ValueError),
        (("DELETE FROM records", ()), ValueError),
        (("COMMIT", ()), ValueError),
        (("BEGIN", ()), ValueError),
        (("WITH value AS (SELECT 1) SELECT * FROM value", ()), ValueError),
        (("-- comment\nSELECT 1", ()), ValueError),
        (("SELECTED", ()), ValueError),
        (("", ()), ValueError),
        ((None, ()), TypeError),
        (("SELECT 1",), ValueError),
        (("SELECT 1", (), "extra"), ValueError),
    ],
)
def test_read_batch_validates_every_request_before_sending_any(invalid_request, error):
    raw = RawConnection()
    connection = PostgresConnection(raw)
    with pytest.raises(error):
        connection.read_batch([("SELECT 1", ()), invalid_request])
    assert raw.events == [] and raw.rollbacks == raw.commits == 0


def test_read_batch_materializes_requests_before_sending_and_preserves_iterator_error():
    raw = RawConnection()
    original = ValueError("synthetic request iterator failure")

    def requests():
        yield "SELECT 1", ()
        raise original

    with pytest.raises(ValueError) as caught:
        PostgresConnection(raw).read_batch(requests())
    assert caught.value is original and raw.events == []


@pytest.mark.parametrize(
    "status",
    [
        TransactionStatus.IDLE,
        TransactionStatus.INERROR,
        TransactionStatus.ACTIVE,
        TransactionStatus.UNKNOWN,
    ],
)
def test_read_batch_requires_existing_healthy_transaction_but_empty_batch_is_noop(
    status,
):
    raw = RawConnection()
    raw.info.transaction_status = status
    connection = PostgresConnection(raw)
    assert connection.read_batch([]) == []
    with pytest.raises(
        StorageError, match="^postgresql_read_batch_requires_transaction$"
    ):
        connection.read_batch([("SELECT 1", ())])
    assert raw.events == [] and raw.rollbacks == raw.commits == 0


@pytest.mark.parametrize("wrapper_closed", [True, False])
@pytest.mark.parametrize("requests", [[], [("SELECT 1", ())]])
def test_read_batch_closed_connection_rejects_even_empty_batch(
    wrapper_closed, requests
):
    raw = RawConnection()
    connection = PostgresConnection(raw)
    if wrapper_closed:
        connection.close()
    else:
        raw.closed = True
    with pytest.raises(StorageError, match="^storage_connection_closed$"):
        connection.read_batch(requests)
    assert raw.events == []


@pytest.mark.parametrize(
    ("phase", "error", "expected"),
    [
        ("enter", psycopg.OperationalError, StorageError),
        ("execute", psycopg.OperationalError, StorageError),
        ("sync", psycopg.OperationalError, StorageError),
        ("sync", psycopg.errors.UniqueViolation, IntegrityError),
    ],
)
def test_read_batch_failure_returns_no_partial_results_and_keeps_owner_rollback(
    phase, error, expected
):
    raw = RawConnection()
    raw.cursor_close_error = True
    releases = []
    connection = PostgresConnection(raw, release=releases.append)
    if phase == "enter":
        raw.enter_error = error("synthetic secret driver error")
    elif phase == "execute":
        raw.execute_error = error("synthetic secret driver error")
    else:
        raw.deferred_error = error("synthetic secret driver error")
    with pytest.raises(expected) as caught:
        connection.read_batch([("SELECT 1", ()), ("SELECT 2", ())])
    assert "secret" not in str(caught.value) and caught.value.__suppress_context__
    assert all(cursor.closed and cursor.closes == 1 for cursor in raw.cursors)
    assert len(raw.cursors) == {"enter": 0, "execute": 1, "sync": 2}[phase]
    assert raw.events.count(("pipeline",)) == 1
    assert raw.rollbacks == raw.commits == 0 and releases == []
    assert not connection._closed
    connection.rollback()
    raw.enter_error = raw.execute_error = raw.deferred_error = None
    raw.cursor_close_error = False
    connection.native("BEGIN")
    assert (
        connection.read_batch([("SELECT 3", ())])[0].fetchone()[0]
        == len(raw.cursors) - 1
    )
    assert raw.rollbacks == 1 and raw.commits == 0 and releases == []


def test_read_batch_python_execution_error_cleans_up_without_replay_or_normalizing():
    raw = RawConnection()
    original = TypeError("synthetic parameter adaptation failure")
    raw.execute_error = original
    connection = PostgresConnection(raw)
    with pytest.raises(TypeError) as caught:
        connection.read_batch([("SELECT 1", ()), ("SELECT ?", (object(),))])
    assert caught.value is original
    assert len(raw.cursors) == 1 and raw.cursors[0].closed
    assert raw.events.count(("pipeline",)) == 1
    assert len([event for event in raw.events if event[0] == "execute"]) == 2
    assert raw.rollbacks == raw.commits == 0


@REAL_PG
def test_real_read_batch_orders_confirmed_results_and_requires_caller_transaction():
    with psycopg.connect(PG_URL, autocommit=True, connect_timeout=3) as raw:
        connection = PostgresConnection(raw)
        assert connection.read_batch([]) == []
        with pytest.raises(
            StorageError, match="^postgresql_read_batch_requires_transaction$"
        ):
            connection.read_batch([("SELECT 1", ())])
        connection.native("BEGIN")
        cursors = connection.read_batch(
            [
                (
                    "SELECT value AS id, value+1 AS ID FROM generate_series(1,1000) value",
                    (),
                ),
                ("SELECT ?::text AS value, ?::integer AS nullable", ("雪? 90%", None)),
                ("SELECT current_setting('transaction_read_only') AS value", ()),
            ]
        )
        assert raw.info.transaction_status == TransactionStatus.INTRANS
        assert connection.total_changes == 0
        first = cursors[0].fetchone()
        assert first["ID"] == first["id"] == 1 and first.keys() == ["id", "id"]
        assert len(cursors[0].fetchmany(499)) == 499
        connection.row_factory = None
        assert cursors[0].fetchall()[-1] == (1000, 1001)
        assert cursors[1].fetchone() == ("雪? 90%", None)
        assert cursors[2].fetchone() == ("off",)
        connection.rollback()
        assert raw.info.transaction_status == TransactionStatus.IDLE


@REAL_PG
@pytest.mark.parametrize("invalid", ["SELECT 1/0", "SELECT 1; SELECT 2"])
def test_real_read_batch_deferred_error_has_no_partial_return_and_rollback_recovers(
    invalid,
):
    with psycopg.connect(PG_URL, autocommit=True, connect_timeout=3) as raw:
        connection = PostgresConnection(raw)
        connection.native("BEGIN")
        with pytest.raises(ValueError, match="requires native SELECT"):
            connection.read_batch([("SELECT 1", ()), ("COMMIT", ())])
        assert raw.info.transaction_status == TransactionStatus.INTRANS
        with pytest.raises(
            StorageError, match="^postgresql_operation_failed$"
        ) as caught:
            connection.read_batch([("SELECT 1", ()), (invalid, ()), ("SELECT 3", ())])
        assert caught.value.__suppress_context__
        assert raw.info.transaction_status == TransactionStatus.INERROR
        with pytest.raises(
            StorageError, match="^postgresql_read_batch_requires_transaction$"
        ):
            connection.read_batch([("SELECT 4", ())])
        connection.rollback()
        connection.native("BEGIN")
        assert connection.read_batch([("SELECT 4", ())])[0].fetchone()[0] == 4
        connection.rollback()
