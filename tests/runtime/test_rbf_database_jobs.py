"""Resource contention is local, observable and opt-in recoverable."""

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from psycopg import OperationalError
from psycopg.errors import DeadlockDetected, LockNotAvailable, QueryCanceled
from psycopg.pq import TransactionStatus
from psycopg_pool import PoolTimeout
from procrastinate.exceptions import ConnectorException

from tianshu.runtime.database import Database, DatabaseConfig, classify_database_failure
from tianshu.runtime.execution import ExecutionLane
from tianshu.runtime.errors import AppError
from tianshu.runtime.jobs import JobQueue, JobSpec, current_execution_generation


async def test_confirmed_statement_timeout_retries_resource_wait_without_ordinary_attempt_budget(database):
    from tianshu.runtime.jobs import ResourceRecoveryRetry
    with pytest.raises(QueryCanceled) as captured:
        async with database.transaction(read_only=True) as connection:
            await connection.execute("SET LOCAL statement_timeout='10ms'")
            await connection.execute('SELECT pg_sleep(0.05)')
    failure = classify_database_failure(captured.value)
    assert failure.reason=='database_statement_timeout' and failure.rollback_confirmed and failure.retryable
    assert ResourceRecoveryRetry(retryable=False).get_retry_decision(
        exception=captured.value,job=SimpleNamespace(attempts=3)) is not None
    async with database.transaction(read_only=True) as connection:
        assert (await (await connection.execute('SELECT 1 AS n')).fetchone())['n']==1


async def test_user_cancelled_statement_is_not_a_resource_timeout(database):
    async def cancel(connection):
        await asyncio.sleep(.05)
        await connection.cancel_safe()
    with pytest.raises(QueryCanceled) as captured:
        async with database.transaction(read_only=True) as connection:
            cancellation = asyncio.create_task(cancel(connection))
            try:
                await connection.execute('SELECT pg_sleep(2)')
            finally:
                await cancellation
    assert classify_database_failure(captured.value) is None
from tianshu.runtime.recovery_policy import RecoveryWait


async def test_pool_identity_contention_reuse_and_reserved_control(database):
    db = Database(DatabaseConfig(database.config.dsn, max_connections=1, control_connections=1,
                                 sync_connections=1, connect_timeout=0.1), role="test-role")
    await db.open()
    try:
        async with db.transaction() as connection:
            name = await (await connection.execute("SELECT current_setting('application_name') AS name")).fetchone()
            assert name["name"] == "tianshu:test-role:business"
            async with db.transaction(connection=connection) as same:
                assert same is connection
                assert (await (await same.execute("SELECT 1 AS value")).fetchone())["value"] == 1
            with pytest.raises(PoolTimeout) as captured:
                async with db.transaction():
                    pass
            failure = classify_database_failure(captured.value)
            assert failure.reason == "database_pool_timeout" and failure.failure_phase == "connection_acquire"
            assert failure.pool_name == "business" and failure.role == "test-role"
            assert not failure.rollback_confirmed and not failure.retryable and not failure.receipt_required
            async with db.transaction(control=True) as control:
                name = await (await control.execute("SELECT current_setting('application_name') AS name")).fetchone()
                assert name["name"] == "tianshu:test-role:control"
            stats = db.pool_stats()["pools"]
            assert stats["business"]["activity"]["in_transaction"] == 1
            assert stats["business"]["activity"]["acquire_timeouts"] == 1
            assert stats["control"]["activity"]["acquire_count"] == 1
        assert db.pool_stats()["pools"]["business"]["activity"]["in_transaction"] == 0
        with pytest.raises(PoolTimeout) as captured:
            async with db.transaction():
                async with db.transaction():
                    pass
        failure = classify_database_failure(captured.value)
        assert failure.rollback_confirmed and failure.retryable and failure.failure_phase == "connection_acquire"
        held, release = asyncio.Event(), asyncio.Event()

        async def occupy():
            async with db.transaction():
                held.set()
                await release.wait()

        holder = asyncio.create_task(occupy())
        try:
            await held.wait()
            with pytest.raises(PoolTimeout) as captured:
                async with db.transaction():
                    pass
            # Another task's held connection is not this task's open transaction.
            failure = classify_database_failure(captured.value)
            assert failure.rollback_confirmed and failure.retryable
        finally:
            release.set()
            await holder
    finally:
        await db.close()


async def test_sqlstate_lock_failure_is_marked_only_after_rollback(database):
    db = Database(DatabaseConfig(database.config.dsn, max_connections=1, control_connections=1,
                                 sync_connections=1), role="lock-owner")
    await db.open()
    try:
        async with database.transaction() as holder:
            await holder.execute("SELECT id FROM runtime.installation_generation WHERE singleton=true FOR UPDATE")
            with pytest.raises(LockNotAvailable) as captured:
                async with db.transaction() as contender:
                    await contender.execute("SELECT id FROM runtime.installation_generation WHERE singleton=true FOR UPDATE NOWAIT")
        failure = classify_database_failure(captured.value)
        assert failure.reason == "database_lock_unavailable" and failure.sqlstate == "55P03"
        assert failure.failure_phase == "transaction" and failure.pool_name == "business"
        assert failure.rollback_confirmed and failure.retryable
        async with db.transaction() as connection:
            assert (await (await connection.execute("SELECT 1 AS value")).fetchone())["value"] == 1
    finally:
        await db.close()


async def test_commit_uncertain_and_foreign_timeout_never_authorize_replay(database):
    class BrokenConnection:
        closed = False
        info = SimpleNamespace(transaction_status=TransactionStatus.IDLE)

        async def execute(self, *_args):
            pass

        @asynccontextmanager
        async def transaction(self):
            yield
            raise OperationalError("fixture connection failed during commit")

    class BrokenPool:
        @asynccontextmanager
        async def connection(self, **_kwargs):
            yield BrokenConnection()

    db = Database(DatabaseConfig(database.config.dsn), role="commit-owner")
    db.pool = BrokenPool()
    try:
        with pytest.raises(OperationalError) as captured:
            async with db.transaction():
                pass
        failure = classify_database_failure(captured.value)
        assert failure.reason == "database_commit_unknown" and failure.failure_phase == "commit"
        assert failure.pool_name == "business" and failure.receipt_required and not failure.retryable
        assert not failure.rollback_confirmed
        ambiguous = classify_database_failure(PoolTimeout(), phase="commit", rollback_confirmed=True)
        assert ambiguous.receipt_required and not ambiguous.retryable
        assert classify_database_failure(TimeoutError("PoolTimeout"), phase="transaction", rollback_confirmed=True) is None
        assert classify_database_failure(QueryCanceled("statement timeout"), phase="transaction", rollback_confirmed=True) is None
        deadlock = classify_database_failure(DeadlockDetected(), phase="transaction", rollback_confirmed=True)
        assert deadlock.reason == "database_deadlock" and deadlock.retryable
        assert not classify_database_failure(DeadlockDetected(), phase="transaction", rollback_confirmed=False).retryable
    finally:
        db.executor.shutdown(wait=True, cancel_futures=True)


async def test_explicit_retry_keeps_original_job_and_never_replays_unknown_commit(database):
    events = []

    class Events:
        async def emit(self, event, outcome, **kwargs):
            events.append((event, outcome, kwargs))

    jobs = JobQueue(database.config.dsn, database=database)
    jobs.events = Events()
    retry_at = datetime.now(UTC) + timedelta(minutes=1)

    async def wait_once(**_kwargs):
        raise RecoveryWait("recovery_claim_lost", retry_at, dependency="quota")

    task = jobs.register("fixture.durable", wait_once, execution_lane=ExecutionLane.MAINTENANCE,
                         resource_recovery=True)
    ordinary = jobs.register("fixture.ordinary", wait_once, execution_lane=ExecutionLane.MAINTENANCE)
    async with database.transaction() as connection:
        generation = await jobs.current_generation_on(connection)
        spec = JobSpec("fixture.durable", "original", {"input_id": "same"}, data_generation=generation)
        job_id = await jobs.enqueue(connection, spec, queueing_lock="subject")
        await connection.execute("UPDATE procrastinate_jobs SET status='doing' WHERE id=%s", (job_id,))
        other_id = await jobs.enqueue(connection,
            JobSpec("fixture.durable", "next-wake", {"input_id": "same"}, data_generation=generation),
            queueing_lock="subject")
    await jobs.open()
    try:
        async with jobs.app.connector.pool.connection() as connection:
            name = await (await connection.execute("SELECT current_setting('application_name') AS name")).fetchone()
            assert name[0] == "tianshu:unassigned:queue"
        context = SimpleNamespace(job=SimpleNamespace(id=job_id, queueing_lock="subject", attempts=0))
        with pytest.raises(RecoveryWait) as captured:
            await task(context, input_id="same")
        decision = task.retry_strategy.get_retry_decision(exception=captured.value, job=context.job)
        assert decision.retry_at == retry_at and ordinary.retry_strategy is None
        await jobs.app.job_manager.retry_job(context.job, retry_at=decision.retry_at)
        async with database.transaction(read_only=True) as connection:
            saved = await (await connection.execute("SELECT j.status,j.args,j.queueing_lock,k.data_generation "
                "FROM procrastinate_jobs j JOIN runtime.job_keys k ON k.job_id=j.id WHERE k.key='original'")).fetchone()
            other = await (await connection.execute("SELECT status,queueing_lock FROM procrastinate_jobs WHERE id=%s",
                                                    (other_id,))).fetchone()
        assert saved == {"status": "todo", "args": {"input_id": "same"}, "queueing_lock": None,
                         "data_generation": generation}
        assert other["status"] == "todo" and other["queueing_lock"] is not None
        assert [(event, outcome) for event, outcome, _ in events] == [
            ("job.started", "started"), ("job.deferred", "deferred")]
        assert events[-1][2]["dependency_ref"] == "quota"
        commit_error = OperationalError("uncertain")
        commit_error.database_failure = classify_database_failure(commit_error, phase="commit", rollback_confirmed=False)
        assert task.retry_strategy.get_retry_decision(exception=commit_error, job=context.job) is None
        unconfirmed_wait = RecoveryWait("source_lock_set_changed", retry_at, dependency="sources:lock_set")
        unconfirmed_wait.transaction_rollback_confirmed = False
        assert task.retry_strategy.get_retry_decision(exception=unconfirmed_wait, job=context.job) is None
    finally:
        await jobs.close()


@pytest.mark.parametrize("wrapped", [False, True])
async def test_generation_lookup_pool_wait_preserves_original_job_without_calling_handler(database, monkeypatch, wrapped):
    events, calls = [], []

    class Events:
        async def emit(self, event, outcome, **kwargs):
            events.append((event, outcome, kwargs))

    jobs = JobQueue(database.config.dsn, database=database)
    jobs.events = Events()

    async def handle(input_id):
        calls.append((input_id, current_execution_generation()))

    task = jobs.register("fixture.generation_wait", handle, execution_lane=ExecutionLane.MAINTENANCE,
                         resource_recovery=True)
    async with database.transaction() as connection:
        generation = await jobs.current_generation_on(connection)
        job_id = await jobs.enqueue(connection, JobSpec("fixture.generation_wait", "original-generation-wait",
            {"input_id": "frozen"}, data_generation=generation), queueing_lock="generation-wake")
        await connection.execute("UPDATE procrastinate_jobs SET status='doing' WHERE id=%s", (job_id,))
        successor = await jobs.enqueue(connection, JobSpec("fixture.generation_wait", "pending-generation-wake",
            {"input_id": "frozen"}, data_generation=generation), queueing_lock="generation-wake")
    await jobs.open()
    try:
        timeout = PoolTimeout("fixture queue acquire")
        error = ConnectorException() if wrapped else timeout
        if wrapped:
            error.__cause__ = timeout
        original_lookup = jobs.app.connector.execute_query_one_async
        monkeypatch.setattr(jobs.app.connector, "execute_query_one_async", AsyncMock(side_effect=error))
        context = SimpleNamespace(job=SimpleNamespace(id=job_id, queueing_lock="generation-wake", attempts=0))
        with pytest.raises(type(error)) as captured:
            await task(context, input_id="frozen")
        assert not calls and not events
        failure = classify_database_failure(captured.value)
        assert failure.reason == "database_pool_timeout" and failure.failure_phase == "connection_acquire"
        assert failure.pool_name == "queue" and failure.role == database.role
        assert failure.rollback_confirmed and failure.retryable and not failure.receipt_required
        decision = task.retry_strategy.get_retry_decision(exception=captured.value, job=context.job)
        assert decision is not None and decision.retry_at > datetime.now(UTC)
        # This is the real native queue write; the failed lookup itself did not
        # claim a retry had already been persisted.
        await jobs.app.job_manager.retry_job(context.job, retry_at=decision.retry_at)
        async with database.transaction(read_only=True) as connection:
            saved = await (await connection.execute(
                "SELECT j.id,j.status,j.args,j.queueing_lock,k.key,k.data_generation FROM procrastinate_jobs j "
                "JOIN runtime.job_keys k ON k.job_id=j.id WHERE k.key='original-generation-wait'")).fetchone()
            pending = await (await connection.execute("SELECT status FROM procrastinate_jobs WHERE id=%s",
                                                       (successor,))).fetchone()
        assert saved == {"id": job_id, "status": "todo", "args": {"input_id": "frozen"},
                         "queueing_lock": None, "key": "original-generation-wait", "data_generation": generation}
        assert pending["status"] == "todo"
        monkeypatch.setattr(jobs.app.connector, "execute_query_one_async", original_lookup)
        await task(context, input_id="frozen")
        assert calls == [("frozen", generation)]
        async with database.transaction() as connection:
            await connection.execute("UPDATE runtime.installation_generation SET id='new-generation' WHERE singleton=true")
        with pytest.raises(AppError, match="job_generation_expired"):
            await task(context, input_id="frozen")
        assert calls == [("frozen", generation)]
    finally:
        await jobs.close()


async def test_generation_lookup_other_connector_errors_do_not_get_resource_retry(database, monkeypatch):
    jobs = JobQueue(database.config.dsn, database=database)

    async def handle(**_kwargs):
        raise AssertionError("handler must not run")

    task = jobs.register("fixture.generation_error", handle, execution_lane=ExecutionLane.MAINTENANCE,
                         retryable=True, resource_recovery=True)
    error = ConnectorException()
    error.__cause__ = OperationalError("fixture SQL error")
    monkeypatch.setattr(jobs.app.connector, "execute_query_one_async", AsyncMock(side_effect=error))
    context = SimpleNamespace(job=SimpleNamespace(id=1, queueing_lock=None, attempts=0))
    with pytest.raises(ConnectorException) as captured:
        await task(context)
    assert classify_database_failure(captured.value) is None
    assert task.retry_strategy.get_retry_decision(exception=captured.value, job=context.job) is None


async def test_generation_lookup_queue_outage_does_not_claim_retry_was_persisted(database, monkeypatch):
    events = []

    class Events:
        async def emit(self, event, outcome, **kwargs):
            events.append((event, outcome, kwargs))

    jobs = JobQueue(database.config.dsn, database=database)
    jobs.events = Events()

    async def handle(**_kwargs):
        raise AssertionError("handler must not run")

    task = jobs.register("fixture.queue_outage", handle, execution_lane=ExecutionLane.MAINTENANCE,
                         resource_recovery=True)
    async with database.transaction() as connection:
        job_id = await jobs.enqueue(connection, JobSpec("fixture.queue_outage", "outage-original", {}),
                                    queueing_lock="outage-wake")
        await connection.execute("UPDATE procrastinate_jobs SET status='doing' WHERE id=%s", (job_id,))
    error = ConnectorException()
    error.__cause__ = PoolTimeout("fixture queue unavailable")
    monkeypatch.setattr(jobs.app.connector, "execute_query_one_async", AsyncMock(side_effect=error))
    monkeypatch.setattr(jobs.app.connector, "execute_query_async", AsyncMock(side_effect=ConnectorException()))
    context = SimpleNamespace(job=SimpleNamespace(id=job_id, queueing_lock="outage-wake", attempts=0))
    with pytest.raises(ConnectorException) as captured:
        await task(context)
    decision = task.retry_strategy.get_retry_decision(exception=captured.value, job=context.job)
    assert decision is not None and not events
    with pytest.raises(ConnectorException):
        await jobs.app.job_manager.retry_job(context.job, retry_at=decision.retry_at)
    async with database.transaction(read_only=True) as connection:
        saved = await (await connection.execute("SELECT status FROM procrastinate_jobs WHERE id=%s", (job_id,))).fetchone()
    assert saved["status"] == "doing" and not events


async def test_source_wait_must_leave_rolled_back_transaction_before_resuming(database):
    due = datetime.now(UTC) + timedelta(seconds=10)
    with pytest.raises(RecoveryWait) as captured:
        async with database.transaction() as connection:
            await connection.execute("CREATE TEMP TABLE rbf_wait_rollback(value integer) ON COMMIT DROP")
            raise RecoveryWait("source_lock_set_changed", due, dependency="sources:lock_set")
    assert captured.value.transaction_rollback_confirmed is True
    async with database.transaction():
        with pytest.raises(RecoveryWait) as captured:
            async with database.transaction():
                raise RecoveryWait("source_lock_set_changed", due, dependency="sources:lock_set")
        assert captured.value.transaction_rollback_confirmed is False
    with pytest.raises(RecoveryWait) as captured:
        async with database.transaction():
            async with database.transaction():
                raise RecoveryWait("source_lock_set_changed", due, dependency="sources:lock_set")
    assert captured.value.transaction_rollback_confirmed is True


async def test_cancelled_sql_releases_the_original_connection(database):
    db = Database(DatabaseConfig(database.config.dsn, max_connections=1, control_connections=1,
                                 sync_connections=1), role="cancel-test")
    await db.open()
    started = asyncio.Event()

    async def work():
        async with db.transaction() as connection:
            started.set()
            await connection.execute("SELECT pg_sleep(30)")

    task = asyncio.create_task(work())
    try:
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        async with db.transaction() as connection:
            assert (await (await connection.execute("SELECT 1 AS value")).fetchone())["value"] == 1
        assert db.pool_stats()["pools"]["business"]["activity"]["in_transaction"] == 0
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await db.close()


async def test_cancel_during_savepoint_entry_settles_before_outer_rollback(database):
    from tianshu.runtime.cancellation import transaction_on

    entered, release = asyncio.Event(), asyncio.Event()
    db = Database(DatabaseConfig(database.config.dsn,max_connections=1),role="savepoint-cancel")
    await db.open()

    class EntryBarrier:
        def __init__(self,connection):self.native=connection.transaction()
        async def __aenter__(self):
            await self.native.__aenter__()
            entered.set()
            await release.wait()
        async def __aexit__(self,*error):return await self.native.__aexit__(*error)

    async def work():
        async with db.transaction() as conn:
            proxy=SimpleNamespace(transaction=lambda:EntryBarrier(conn))
            async with transaction_on(proxy):
                pytest.fail("cancelled entry must never run its body")

    task=asyncio.create_task(work())
    try:
        await entered.wait()
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):await task
        async with db.transaction() as conn:
            assert (await (await conn.execute("SELECT 1 AS value")).fetchone())["value"]==1
    finally:
        release.set()
        task.cancel()
        await asyncio.gather(task,return_exceptions=True)
        await db.close()


async def test_repeated_cancellation_keeps_sync_slot_until_thread_exits(database):
    db = Database(DatabaseConfig(database.config.dsn, max_connections=1, control_connections=1,
                                 sync_connections=1), role="sync-cancel")
    await db.open()
    entered, release = threading.Event(), threading.Event()

    def operation(_connection):
        entered.set()
        release.wait(timeout=10)
        return "finished"

    task = asyncio.create_task(db.run_sync(operation))
    follower = None
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        follower = asyncio.create_task(db.run_sync(lambda _connection: "next"))
        await asyncio.sleep(0)
        activity = db.pool_stats()["pools"]["sync"]["activity"]
        assert activity["sync_slot_active"] == 1 and activity["sync_slot_waiting"] == 1
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert await follower == "next"
    finally:
        release.set()
        await asyncio.gather(task, *([follower] if follower else []), return_exceptions=True)
        await db.close()
