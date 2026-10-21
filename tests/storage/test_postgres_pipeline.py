"""Setup confirmation/error boundaries with queued fake commands, without a server."""

import os
from types import SimpleNamespace
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.pq import TransactionStatus
from tianshu_storage import IntegrityError, PostgresConnection, Row, StorageError
from tianshu_storage.options import StorageOptions
from tianshu_storage.postgres import _bound_qmark, qmark
from tianshu_storage.runtime import Database, StorageRuntime

PG_URL = os.environ.get("TIANSHU_TEST_POSTGRES_URL", "")
REAL_PG = pytest.mark.skipif(
    not PG_URL, reason="explicit isolated PostgreSQL URL required"
)


class RawCursor:
    def __init__(self, rows=()):
        self.rows = iter(rows)
        self.rowcount = -1
        self.description = [SimpleNamespace(name="value")]

    def fetchone(self):
        return next(self.rows, None)


class Pipeline:
    def __init__(self, raw):
        self.raw = raw

    def __enter__(self):
        raw = self.raw
        raw.events.append(("enter",))
        if raw.closed:
            raise psycopg.InterfaceError("synthetic secret closed socket")
        if raw.enter_error:
            raise raw.enter_error
        assert not raw.in_pipeline
        raw.in_pipeline = True
        return self

    def __exit__(self, kind, error, traceback):
        raw = self.raw
        raw.events.append(("sync",))
        raw.in_pipeline = False
        if raw.deferred_error:
            # Like psycopg, cleanup preserves an already raised body error.
            if error is None:
                raise raw.deferred_error
            return
        for statement, parameters in raw.pending:
            raw.apply(statement, parameters)
        raw.pending.clear()


class RawConnection:
    def __init__(self):
        self.info = SimpleNamespace(transaction_status=TransactionStatus.IDLE)
        self.events = []
        self.pending = []
        self.confirmed = []
        self.in_pipeline = False
        self.closed = False
        self.lock_confirmed = False
        self.enter_error = None
        self.deferred_error = None
        self.immediate_error_at = None
        self.rollback_error = False
        self.settings_error = False
        self.binding_error = False
        self.binding = ("installation", "main", "ready")
        self.rollbacks = self.commits = self.closes = 0

    def pipeline(self):
        self.events.append(("pipeline",))
        return Pipeline(self)

    def execute(self, statement, parameters=None, *, prepare=None):
        statement = (
            statement.as_string()
            if isinstance(statement, sql.Composable)
            else statement
        )
        self.events.append(("execute", statement, parameters, self.in_pipeline))
        if self.closed:
            raise psycopg.InterfaceError("synthetic secret closed socket")
        if self.settings_error and "pg_catalog.set_config('search_path'" in statement:
            raise psycopg.OperationalError("synthetic secret settings error")
        if self.in_pipeline:
            if self.immediate_error_at == len(self.pending) + 1:
                raise psycopg.OperationalError("synthetic secret immediate error")
            self.pending.append((statement, parameters))
            return RawCursor()
        if "FROM __tianshu_storage_binding" in statement:
            assert len(self.confirmed) == 1
            assert "pg_catalog.set_config('search_path'" in self.confirmed[0][0]
            if self.binding_error:
                raise psycopg.OperationalError("synthetic secret binding error")
            return RawCursor([self.binding])
        if statement == "SELECT key,value FROM metadata":
            assert (
                self.lock_confirmed
                and self.info.transaction_status == TransactionStatus.INTRANS
            )
        self.apply(statement, parameters)
        return RawCursor([(1,)])

    def apply(self, statement, parameters):
        self.confirmed.append((statement, parameters))
        if statement == "BEGIN":
            self.info.transaction_status = TransactionStatus.INTRANS
        if statement.startswith("SELECT pg_advisory_xact_lock"):
            self.lock_confirmed = True

    def rollback(self):
        self.rollbacks += 1
        self.events.append(("rollback",))
        if self.rollback_error or self.closed:
            raise psycopg.OperationalError("synthetic secret rollback error")
        self.info.transaction_status = TransactionStatus.IDLE

    def commit(self):
        self.commits += 1
        self.info.transaction_status = TransactionStatus.IDLE

    def close(self):
        self.closes += 1
        self.closed = True


class Pool:
    def __init__(self, raw):
        self.raw = raw
        self.borrowed = []
        self.returned = []
        self.error = None

    def getconn(self, *, timeout):
        self.borrowed.append(timeout)
        if self.error:
            raise self.error
        return self.raw

    def putconn(self, raw):
        self.returned.append(raw)


def manager(tmp_path, raw):
    result = StorageRuntime(StorageOptions(), service="test", state_directory=tmp_path)
    result.backend = "postgresql"
    result._state = {"installation_id": "installation"}
    result._pool = Pool(raw)
    return result


@pytest.mark.parametrize(
    ("read_only", "timeout", "expected"), [(True, 1.0001, "1001ms"), (False, 0, "1ms")]
)
def test_checkout_confirms_settings_before_independent_binding_read(
    tmp_path, read_only, timeout, expected
):
    raw = RawConnection()
    runtime = manager(tmp_path, raw)
    database = Database(runtime, "main", tmp_path / "anchor.sqlite")
    connection = database.connect(timeout=timeout, read_only=read_only)
    assert raw.confirmed == [
        (
            (
                "SELECT pg_catalog.set_config('search_path', %s, false),"
                "pg_catalog.set_config('lock_timeout', %s, false),"
                "pg_catalog.set_config('default_transaction_read_only', %s, false)"
            ),
            (
                f'"{database.schema}", pg_catalog',
                expected,
                "on" if read_only else "off",
            ),
        ),
    ]
    executed = [entry for entry in raw.events if entry[0] == "execute"]
    assert [entry[3] for entry in executed] == [False, False]
    assert "FROM __tianshu_storage_binding" in executed[-1][1]
    assert raw.events.count(("pipeline",)) == raw.events.count(("sync",)) == 0
    assert runtime._pool.borrowed == [runtime.options.connect_timeout]
    assert runtime._pool.returned == [] and raw.rollbacks == 0
    connection.close()
    connection.close()
    assert runtime._pool.returned == [raw] and raw.rollbacks == 1


def test_checkout_keeps_identifier_quoting(tmp_path):
    raw = RawConnection()
    runtime = manager(tmp_path, raw)
    assert runtime._checkout('odd"schema') is raw
    assert raw.confirmed[0][1] == ('"odd""schema", pg_catalog', "5000ms", "off")
    assert 'odd"schema' not in raw.confirmed[0][0]


def test_checkout_resets_all_settings_on_each_borrow(tmp_path):
    raw = RawConnection()
    runtime = manager(tmp_path, raw)
    assert runtime._checkout("first", read_only=True, timeout=2) is raw
    runtime._release(raw)
    assert runtime._checkout("second", read_only=False, timeout=3) is raw
    assert [parameters for _, parameters in raw.confirmed] == [
        ('"first", pg_catalog', "2000ms", "on"),
        ('"second", pg_catalog', "3000ms", "off"),
    ]
    assert runtime._pool.borrowed == [runtime.options.connect_timeout] * 2
    assert runtime._pool.returned == [raw]


@pytest.mark.parametrize("failure", ["execute", "rollback", "closed"])
def test_checkout_errors_release_once_without_binding_or_raw_error_text(
    tmp_path, failure
):
    raw = RawConnection()
    runtime = manager(tmp_path, raw)
    if failure == "rollback":
        raw.settings_error = True
        raw.rollback_error = True
    elif failure == "closed":
        raw.closed = True
    else:
        raw.settings_error = True
    with pytest.raises(StorageError, match="^postgresql_unavailable$") as caught:
        Database(runtime, "main", tmp_path / "anchor.sqlite").connect()
    assert caught.value.__suppress_context__
    assert runtime._pool.returned == [raw] and raw.rollbacks == 1
    assert not any(
        "__tianshu_storage_binding" in entry[1]
        for entry in raw.events
        if entry[0] == "execute"
    )
    assert raw.closes == (1 if failure in {"rollback", "closed"} else 0)


def test_checkout_getconn_failure_does_not_return_an_unborrowed_connection(tmp_path):
    raw = RawConnection()
    runtime = manager(tmp_path, raw)
    runtime._pool.error = OSError("synthetic secret pool error")
    with pytest.raises(StorageError, match="^postgresql_unavailable$"):
        runtime._checkout("pg_catalog")
    assert runtime._pool.returned == [] and raw.events == []


@pytest.mark.parametrize("failure", ["mismatch", "query"])
def test_binding_failure_stays_outside_setup_pipeline_and_releases_once(
    tmp_path, failure
):
    raw = RawConnection()
    runtime = manager(tmp_path, raw)
    if failure == "mismatch":
        raw.binding = ("different", "main", "ready")
        code = "postgresql_binding_mismatch"
    else:
        raw.binding_error = True
        code = "postgresql_operation_failed"
    with pytest.raises(StorageError, match="^" + code + "$"):
        Database(runtime, "main", tmp_path / "anchor.sqlite").connect()
    assert len(raw.confirmed) == 1 and raw.events.count(("sync",)) == 0
    assert runtime._pool.returned == [raw] and raw.rollbacks == 1


def test_product_setup_confirms_existing_dialect_lock_before_metadata_statement():
    raw = RawConnection()

    def dialect(connection, statement, parameters):
        if statement == "BEGIN IMMEDIATE":
            connection.native("BEGIN")
            return connection.native("SELECT pg_advisory_xact_lock(?)", (42,))

    connection = PostgresConnection(raw, dialect=dialect)
    with connection.pipeline() as current:
        assert current is connection
        connection.native("SET synchronous_commit=on")
        connection.execute("BEGIN IMMEDIATE")
        assert not raw.confirmed and not raw.lock_confirmed
    assert raw.confirmed == [
        ("SET synchronous_commit=on", None),
        ("BEGIN", None),
        ("SELECT pg_advisory_xact_lock(%s)", (42,)),
    ]
    assert connection.native("SELECT key,value FROM metadata").fetchone()[0] == 1
    assert raw.events[-1] == ("execute", "SELECT key,value FROM metadata", None, False)
    assert raw.rollbacks == raw.commits == 0


@pytest.mark.parametrize(
    ("phase", "error", "expected"),
    [
        ("enter", psycopg.OperationalError, StorageError),
        ("execute", psycopg.OperationalError, StorageError),
        ("sync", psycopg.OperationalError, StorageError),
        ("sync", psycopg.errors.UniqueViolation, IntegrityError),
    ],
)
def test_product_pipeline_sanitizes_immediate_and_delayed_psycopg_errors(
    phase, error, expected
):
    raw = RawConnection()
    releases = []
    connection = PostgresConnection(raw, release=releases.append)
    if phase == "enter":
        raw.enter_error = error("synthetic secret error")
    elif phase == "sync":
        raw.deferred_error = error("synthetic secret error")
    else:
        raw.immediate_error_at = 1
    body_complete = False
    with pytest.raises(expected) as caught, connection, connection.pipeline():
        connection.native("SET synchronous_commit=on")
        body_complete = True
    assert body_complete is (phase == "sync")
    assert "secret" not in str(caught.value) and caught.value.__suppress_context__
    assert raw.rollbacks == 1 and raw.commits == 0 and releases == []
    connection.close()
    connection.close()
    assert releases == [raw]


def test_product_pipeline_preserves_body_error_without_retry_or_transaction_control():
    raw = RawConnection()
    connection = PostgresConnection(raw)
    original = ValueError("business error")
    with pytest.raises(ValueError) as caught, connection.pipeline():
        connection.native("SET synchronous_commit=on")
        raise original
    assert caught.value is original
    assert raw.confirmed == [("SET synchronous_commit=on", None)]
    assert raw.rollbacks == raw.commits == 0
    assert raw.events.count(("pipeline",)) == raw.events.count(("sync",)) == 1


@pytest.mark.parametrize("wrapper_closed", [True, False])
def test_closed_connections_fail_without_exposing_driver_text(wrapper_closed):
    raw = RawConnection()
    connection = PostgresConnection(raw)
    if wrapper_closed:
        connection.close()
        expected = "storage_connection_closed"
    else:
        raw.closed = True
        expected = "postgresql_operation_failed"
    with pytest.raises(StorageError, match="^" + expected + "$"), connection.pipeline():
        pytest.fail("closed connection entered pipeline")
    assert raw.events.count(("pipeline",)) == (0 if wrapper_closed else 1)


def test_row_exact_normalized_key_skips_casefold_and_fallback_keeps_first_match():
    class Key(str):
        calls = 0

        def casefold(self):
            type(self).calls += 1
            return super().casefold()

    row = Row([11, 22, 33, 44], ["ID", "id", "Straße", "STRASSE"])
    assert row[Key("id")] == 11 and Key.calls == 0
    assert row[Key("ID")] == 11 and Key.calls == 1
    assert row[Key("strasse")] == 33 and Key.calls == 1
    assert row[Key("Straße")] == 33 and Key.calls == 2
    assert row[-1] == 44 and row[1:3] == (22, 33)
    assert row.keys() == ["ID", "id", "Straße", "STRASSE"]
    with pytest.raises(IndexError, match="No item with that key"):
        row[Key("missing")]


@pytest.mark.parametrize(
    ("statement", "expected"),
    [
        (
            "SELECT ?,'?','it''s ?','90%' -- ?\n, ?",
            "SELECT %s,'?','it''s ?','90%%' -- ?\n, %s",
        ),
        (
            'SELECT "?", $$? 90%$$, $tag$?$tag$, ? /* outer ? /* nested ? */ ? */',
            'SELECT "?", $$? 90%%$$, $tag$?$tag$, %s /* outer ? /* nested ? */ ? */',
        ),
        ("SELECT '90%' /* ? */", "SELECT '90%' /* ? */"),
    ],
)
def test_cached_sql_binding_preserves_literals_comments_quotes_and_percent(
    statement, expected
):
    assert qmark(statement, True) == expected
    assert qmark(statement, True) == expected
    assert qmark(statement, False) == statement


def test_sql_text_cache_is_bounded_and_unparameterized_calls_do_not_enter_it():
    _bound_qmark.cache_clear()
    try:
        qmark("SELECT ?", True)
        qmark("SELECT ?", True)
        assert _bound_qmark.cache_info().hits == 1
        qmark("SELECT '?'", False)
        assert _bound_qmark.cache_info().misses == 1
        for number in range(513):
            qmark(f"SELECT ? /* statement {number} */", True)
        assert (
            _bound_qmark.cache_info().currsize
            == _bound_qmark.cache_info().maxsize
            == 512
        )
    finally:
        _bound_qmark.cache_clear()


@REAL_PG
def test_real_checkout_sets_quoted_schema_timeout_and_read_only_on_every_borrow(
    tmp_path,
):
    with psycopg.connect(PG_URL, autocommit=True, connect_timeout=3) as raw:
        runtime = manager(tmp_path, raw)
        for schema, timeout, read_only, expected_timeout in [
            ('synthetic "雪" schema', 1.0001, True, "1001ms"),
            ("pg_catalog", 0, False, "1ms"),
        ]:
            assert (
                runtime._checkout(schema, timeout=timeout, read_only=read_only) is raw
            )
            assert raw.execute(
                "SELECT current_setting('search_path'),current_setting('lock_timeout'),"
                "current_setting('default_transaction_read_only'),"
                "current_setting('transaction_read_only')"
            ).fetchone() == (
                sql.Identifier(schema).as_string() + ", pg_catalog",
                expected_timeout,
                "on" if read_only else "off",
                "on" if read_only else "off",
            )
            assert raw.info.transaction_status == TransactionStatus.IDLE
            runtime._release(raw)
        assert runtime._pool.borrowed == [runtime.options.connect_timeout] * 2
        assert runtime._pool.returned == [raw, raw]


@REAL_PG
def test_real_setup_pipeline_confirms_lock_and_preserves_delayed_error_rollback():
    lock_id = uuid4().int & ((1 << 63) - 1)
    with psycopg.connect(PG_URL, autocommit=True, connect_timeout=3) as raw:
        connection = PostgresConnection(raw)
        with connection.pipeline():
            connection.native("SET synchronous_commit=on")
            connection.native("BEGIN")
            connection.native("SELECT pg_advisory_xact_lock(?)", (lock_id,))
        assert raw.info.transaction_status == TransactionStatus.INTRANS
        # This read is an independent statement after all setup/lock confirmations.
        row = connection.native(
            "SELECT current_setting('synchronous_commit'),"
            "EXISTS(SELECT 1 FROM pg_locks WHERE locktype='advisory' "
            "AND pid=pg_backend_pid() AND granted)"
        ).fetchone()
        assert tuple(row) == ("on", True)
        connection.rollback()
        with (
            pytest.raises(StorageError, match="^postgresql_operation_failed$"),
            connection.pipeline(),
        ):
            connection.native("BEGIN")
            connection.native("SET synchronous_commit='invalid-storage-test-mode'")
        assert raw.info.transaction_status == TransactionStatus.INERROR
        connection.rollback()
        assert connection.native("SELECT 1").fetchone()[0] == 1
