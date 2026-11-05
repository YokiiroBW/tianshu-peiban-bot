"""New database categories do not relabel ordinary business exceptions as SQL faults."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from psycopg import OperationalError
from psycopg.errors import DeadlockDetected, QueryCanceled, UndefinedTable
from psycopg.pq import TransactionStatus

from tianshu.runtime.database import Database, DatabaseConfig
from tianshu.runtime.errors import AppError
from tianshu.runtime.recovery_policy import RecoveryWait


class FakeConnection:
    closed = False
    info = SimpleNamespace(transaction_status=TransactionStatus.IDLE)
    commit_error = None

    async def execute(self, *_args):
        pass

    @asynccontextmanager
    async def transaction(self):
        yield
        if self.commit_error:
            raise self.commit_error


class FakePool:
    def __init__(self, connection):
        self.value = connection

    @asynccontextmanager
    async def connection(self, **_kwargs):
        yield self.value

    def get_stats(self):
        return {}


async def test_existing_error_totals_and_bounded_failure_categories():
    db = Database(DatabaseConfig("postgresql://fixture@localhost/fixture"), role="fixture")
    connection = FakeConnection()
    db.pool = FakePool(connection)
    try:
        errors = [AppError("ordinary_domain_failure"),
                  RecoveryWait("dependency_wait", datetime.now(UTC) + timedelta(seconds=30)),
                  DeadlockDetected(), UndefinedTable(), QueryCanceled()]
        for error in errors:
            with pytest.raises(type(error)):
                async with db.transaction():
                    raise error
        connection.commit_error = OperationalError("connection lost on commit")
        with pytest.raises(OperationalError):
            async with db.transaction():
                pass
        counts = db.pool_stats()["pools"]["business"]["activity"]
        assert counts["transaction_errors"] == 5 and counts["commit_errors"] == 1
        assert counts["recovery_waits"] == 1 and counts["transaction_conflicts"] == 1
        assert counts["sql_errors"] == 1 and counts["commit_unknowns"] == 1
    finally:
        db.executor.shutdown(wait=True, cancel_futures=True)


def test_sync_transaction_classification_uses_the_same_counters():
    from contextlib import contextmanager

    class SyncConnection:
        closed = False
        info = SimpleNamespace(transaction_status=TransactionStatus.IDLE)

        def execute(self, *_args):
            pass

        @contextmanager
        def transaction(self):
            yield

    class SyncPool:
        @contextmanager
        def connection(self):
            yield SyncConnection()

        def get_stats(self):
            return {}

    db = Database(DatabaseConfig("postgresql://fixture@localhost/fixture"), role="fixture-sync")
    db.sync_pool = SyncPool()
    try:
        with pytest.raises(UndefinedTable):
            with db.sync_transaction():
                raise UndefinedTable()
        counts = db.pool_stats()["pools"]["sync"]["activity"]
        assert counts["transaction_errors"] == 1 and counts["sql_errors"] == 1
        assert counts["transaction_conflicts"] == 0 and counts["commit_unknowns"] == 0
    finally:
        db.executor.shutdown(wait=True, cancel_futures=True)
