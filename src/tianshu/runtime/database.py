"""Native asynchronous PostgreSQL. No backend selection or SQL translation."""

from __future__ import annotations

import asyncio
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from psycopg import Error as PsycopgError, InterfaceError, OperationalError
from psycopg.pq import TransactionStatus
from psycopg.rows import dict_row
from psycopg.conninfo import conninfo_to_dict
from psycopg import ProgrammingError
from psycopg_pool import AsyncConnectionPool, ConnectionPool, PoolTimeout

from tianshu.types import RequestBudget


@dataclass(frozen=True, slots=True)
class DatabaseFailure:
    """Evidence about one local database failure, not a provider result."""

    reason: str
    failure_phase: str
    pool_name: str | None
    role: str | None
    sqlstate: str | None
    rollback_confirmed: bool
    retryable: bool
    receipt_required: bool
    retry_at: datetime | None


def classify_database_failure(error, *, phase=None, rollback_confirmed=None):
    """Classify typed local resource errors; never infer safety from their text.

    Prefer metadata attached by Database after its transaction has exited.
    Explicit overrides describe the *local transaction*, not prior effects or
    whether a whole business handler is safe to rerun.
    """
    saved = getattr(error, "database_failure", None)
    if isinstance(saved, DatabaseFailure) and phase is None and rollback_confirmed is None:
        return saved
    phase = phase or (saved.failure_phase if isinstance(saved, DatabaseFailure) else None)
    if phase not in {"connection_acquire", "transaction", "commit"}:
        return None
    rolled_back = (saved.rollback_confirmed if isinstance(saved, DatabaseFailure) else False) if rollback_confirmed is None else rollback_confirmed
    if type(rolled_back) is not bool:
        raise ValueError("database_rollback_confirmation_required")
    sqlstate = error.sqlstate if isinstance(error, PsycopgError) else None
    if (phase == "commit" and isinstance(error, (OperationalError, InterfaceError, OSError, asyncio.CancelledError))
            and sqlstate not in {"55P03", "40P01", "40001"}):
        reason = "database_commit_unknown"
    elif isinstance(error, PoolTimeout):
        reason = "database_pool_timeout"
    elif isinstance(error, PsycopgError) and sqlstate in {"55P03", "40P01", "40001"}:
        reason = {"55P03": "database_lock_unavailable", "40P01": "database_deadlock",
                  "40001": "database_serialization"}[sqlstate]
    else:
        return None
    receipt_required = reason == "database_commit_unknown"
    retryable = rolled_back and not receipt_required
    return DatabaseFailure(reason, phase, getattr(error, "pool_name", None),
        getattr(error, "database_role", None), sqlstate, rolled_back and not receipt_required,
        retryable, receipt_required, datetime.now(UTC) + timedelta(seconds=10) if retryable else None)


def _annotate_failure(error, *, phase, pool_name, role, rollback_confirmed, remaining_outer=0):
    """Keep the original exception type and carry only bounded, nonsecret facts."""
    from .recovery_policy import RecoveryWait

    if not isinstance(error, (RecoveryWait, PsycopgError)) and not (
        phase == "commit" and isinstance(error, (OSError, asyncio.CancelledError))
    ):
        return
    pending = getattr(error, "_tianshu_pending_rollbacks", None)
    if pending is None:
        pending, all_rolled_back = remaining_outer, rollback_confirmed
    else:
        pending = max(0, pending - 1)
        all_rolled_back = getattr(error, "_tianshu_rollbacks_ok", False) and rollback_confirmed
    try:
        error._tianshu_pending_rollbacks = pending
        error._tianshu_rollbacks_ok = all_rolled_back
    except (AttributeError, TypeError):
        return
    confirmed = bool(pending == 0 and all_rolled_back)
    if isinstance(error, RecoveryWait):
        error.transaction_rollback_confirmed = bool(phase == "transaction" and confirmed)
        return
    previous = getattr(error, "database_failure", None)
    if isinstance(previous, DatabaseFailure):
        phase, pool_name, role = previous.failure_phase, previous.pool_name, previous.role
    try:
        error.failure_phase, error.pool_name, error.database_role = phase, pool_name, role
        failure = classify_database_failure(error, phase=phase, rollback_confirmed=confirmed)
        if failure:
            error.database_failure = failure
            error.rollback_confirmed = failure.rollback_confirmed
            error.receipt_required = failure.receipt_required
    except (AttributeError, TypeError):
        # An unannotatable exception is never treated as automatically retryable.
        return


def _rolled_back(connection):
    try:
        return not connection.closed and connection.info.transaction_status == TransactionStatus.IDLE
    except (AttributeError, OperationalError):
        return False


async def _finish_cleanup(future):
    """Do not release scarce capacity on a repeated caller cancellation."""
    while not future.done():
        try:
            await asyncio.shield(future)
        except asyncio.CancelledError:
            continue
        except Exception:
            break
    try:
        future.result()
        return True
    except BaseException:
        return False


async def _cancel_before_release(connection):
    """Keep a cancelling connection checked out until cancellation ends."""
    if not await _finish_cleanup(asyncio.create_task(connection.cancel_safe())):
        # Never return a connection with an unresolved cancellation to the pool.
        await _finish_cleanup(asyncio.create_task(connection.close()))


def pool_application_name(role, pool_name):
    role = "".join(character if character.isalnum() or character in "-_" else "_"
                   for character in str(role or "unassigned"))[:32]
    return f"tianshu:{role}:{pool_name}"


def resource_budget(document, enabled_roles=None):
    """Calculate configured upper bounds for the actual per-role pools.

    Provider and browser connections are reported separately because their
    limits belong to those adapters, not to PostgreSQL or the model client.
    """
    from .execution import PROCESS_ROLES, role_worker_groups

    roles = tuple(enabled_roles or PROCESS_ROLES)
    if len(set(roles)) != len(roles) or set(roles) - set(PROCESS_ROLES):
        raise ValueError("invalid_process_roles")
    database = document.get("database", {})
    configured_roles = database.get("roles", {})
    worker = document.get("worker", {})
    per_role = {}
    for role in roles:
        value = configured_roles.get(role, {})
        groups = role_worker_groups(worker, role)
        per_role[role] = {
            "business_connections": value.get("max_connections", 4),
            "control_connections": value.get("control_connections", 2),
            "sync_connections": value.get("sync_connections", 2),
            "sync_threads": value.get("sync_connections", 2),
            "queue_connections": worker.get("queue_connections", 8) if groups else 0,
            "queue_listeners": len(groups),
            "broker_owner_connections": 1 if role == "model-broker" else 0,
            "groups": groups,
        }
    pg_fields = ("business_connections", "control_connections", "sync_connections",
                 "queue_connections", "queue_listeners", "broker_owner_connections")
    total = sum(sum(row[field] for field in pg_fields) for row in per_role.values())
    broker = document.get("broker", {})
    transport = broker.get("transport", {})
    http_by_role = {}
    for role in roles:
        if role == "model-broker":
            continue
        configured = {**transport, **transport.get("roles", {}).get(role, {})}
        http_by_role[role] = sum(configured.get(name, default) for name, default in (
            ("foreground_connections", 8), ("due_connections", 4),
            ("background_connections", 24), ("control_connections", 4)))
    upstream = broker.get("upstream_connections", 64) if "model-broker" in roles else 0
    diagnostic = broker.get("diagnostic_connections", 4) if "model-broker" in roles else 0
    return {
        "roles": per_role,
        "postgres_connections": total,
        "postgres_limit": database.get("connection_budget", 72),
        "database_threads": sum(row["sync_threads"] for row in per_role.values()),
        "worker_concurrency": sum(group["concurrency"] for row in per_role.values()
                                  for group in row["groups"].values()),
        "runtime_log_writers": len(roles),
        "supervisor_output_writers": (len(roles) + len(document.get("observability", {}).get("processes", []))
                                      + (2 if document.get("supervisor", {}).get("comics") else 0)),
        "capability_file_threads_per_instance": document.get("capabilities", {}).get("file_workers", 2),
        "model_client_http_connections_by_role": http_by_role,
        "model_client_http_connections": sum(http_by_role.values()),
        "provider_http_connections": upstream,
        "diagnostic_http_connections": diagnostic,
        "model_http_connections": sum(http_by_role.values()) + upstream + diagnostic,
    }


@dataclass(frozen=True, slots=True)
class DatabaseConfig:
    dsn: str = field(repr=False)
    max_connections: int = 8
    control_connections: int = 2
    connect_timeout: float = 5.0
    sync_connections: int = 2

    def __post_init__(self):
        try:
            parsed=conninfo_to_dict(self.dsn) if isinstance(self.dsn,str) and self.dsn.strip() else {}
        except ProgrammingError:
            raise ValueError("postgresql_dsn_required") from None
        if not parsed:
            raise ValueError("postgresql_dsn_required")
        if self.max_connections < 1 or self.control_connections < 1 or self.sync_connections < 1:
            raise ValueError("positive_connection_budget_required")


class Database:
    """A process budget with a reserved pool for cancellation and lightweight status."""

    def __init__(self, config: DatabaseConfig, *, role=None):
        self.config, self.role = config, role or "unassigned"
        options = {"conninfo": config.dsn, "min_size": 0, "open": False,
            "timeout": config.connect_timeout}
        self.pool = AsyncConnectionPool(max_size=config.max_connections, **options,
            kwargs={"autocommit": True, "row_factory": dict_row,
                    "application_name": pool_application_name(self.role, "business")})
        self.control_pool = AsyncConnectionPool(max_size=config.control_connections, **options,
            kwargs={"autocommit": True, "row_factory": dict_row,
                    "application_name": pool_application_name(self.role, "control")})
        self.sync_pool = ConnectionPool(conninfo=config.dsn, min_size=0,
            max_size=config.sync_connections, open=False,
            kwargs={"autocommit": True, "row_factory": dict_row,
                    "application_name": pool_application_name(self.role, "sync")},
            timeout=config.connect_timeout)
        self.executor = ThreadPoolExecutor(max_workers=config.sync_connections,
                                           thread_name_prefix="tianshu-db")
        self._sync_slots = asyncio.Semaphore(config.sync_connections)
        self._metrics_lock = threading.Lock()
        self._transaction_scopes = {}
        self._metrics = {name: {"acquire_waiting": 0, "in_transaction": 0,
            "acquire_count": 0, "acquire_wait_ms": 0, "acquire_timeouts": 0,
            "transaction_errors": 0, "commit_errors": 0, "sync_slot_waiting": 0, "sync_slot_active": 0}
            for name in ("business", "control", "sync")}

    def _metrics_add(self, pool_name, **values):
        with self._metrics_lock:
            for key, value in values.items():
                self._metrics[pool_name][key] += value

    def _scope_depth(self, key):
        with self._metrics_lock:
            return self._transaction_scopes.get(key, 0)

    def _enter_scope(self, key):
        with self._metrics_lock:
            self._transaction_scopes[key] = self._transaction_scopes.get(key, 0) + 1

    def _leave_scope(self, key):
        with self._metrics_lock:
            remaining = self._transaction_scopes[key] - 1
            if remaining:
                self._transaction_scopes[key] = remaining
            else:
                del self._transaction_scopes[key]

    def pool_stats(self):
        """Process-local occupancy and wait counters, with no SQL or credentials."""
        with self._metrics_lock:
            current = {name: dict(values) for name, values in self._metrics.items()}
        return {"role": self.role, "pools": {
            name: {"activity": current[name], "pool": pool.get_stats()}
            for name, pool in (("business", self.pool), ("control", self.control_pool), ("sync", self.sync_pool))
        }}

    @asynccontextmanager
    async def _sync_slot(self):
        acquired = False
        self._metrics_add("sync", sync_slot_waiting=1)
        try:
            async with self._sync_slots:
                acquired = True
                self._metrics_add("sync", sync_slot_waiting=-1, sync_slot_active=1)
                try:
                    yield
                finally:
                    self._metrics_add("sync", sync_slot_active=-1)
        finally:
            if not acquired:
                self._metrics_add("sync", sync_slot_waiting=-1)

    async def open(self):
        await self.pool.open(wait=True)
        await self.control_pool.open(wait=True)
        await asyncio.to_thread(self.sync_pool.open, wait=True)

    async def close(self):
        await self.pool.close()
        await self.control_pool.close()
        await asyncio.to_thread(self.sync_pool.close)
        self.executor.shutdown(wait=True, cancel_futures=True)

    @contextmanager
    def sync_transaction(self, budget: RequestBudget | None = None, *, read_only=False, connection=None):
        """Use an existing owner transaction, or acquire a short sync transaction.

        An explicitly supplied connection remains governed by its owning
        transaction; this context does not commit it or change isolation.
        """
        if budget:
            budget.check()
        if connection is not None:
            yield connection
            return
        scope_key = ("sync", threading.get_ident())
        outer_depth = self._scope_depth(scope_key)
        started, acquired, phase = time.monotonic(), False, "connection_acquire"
        self._metrics_add("sync", acquire_waiting=1)
        try:
            with self.sync_pool.connection() as connection:
                acquired, phase = True, "transaction"
                self._enter_scope(scope_key)
                self._metrics_add("sync", acquire_waiting=-1, in_transaction=1, acquire_count=1,
                                  acquire_wait_ms=int((time.monotonic()-started)*1000))
                try:
                    with connection.transaction():
                        if read_only:
                            connection.execute("SET TRANSACTION READ ONLY")
                        milliseconds = max(1, math.ceil(budget.remaining() * 1000)) if budget else 30000
                        connection.execute("SELECT set_config('statement_timeout',%s,true), "
                            "set_config('lock_timeout',%s,true)",
                            (str(milliseconds), str(min(milliseconds, 5000))))
                        yield connection
                        phase = "commit"
                except BaseException as error:
                    self._metrics_add("sync", **{phase + "_errors": 1})
                    _annotate_failure(error, phase=phase, pool_name="sync", role=self.role,
                                      rollback_confirmed=_rolled_back(connection), remaining_outer=outer_depth)
                    raise
                finally:
                    self._leave_scope(scope_key)
                    self._metrics_add("sync", in_transaction=-1)
        except BaseException as error:
            if not acquired:
                self._metrics_add("sync", acquire_waiting=-1,
                    acquire_wait_ms=int((time.monotonic()-started)*1000),
                    acquire_timeouts=int(isinstance(error, PoolTimeout)))
                _annotate_failure(error, phase="connection_acquire", pool_name="sync", role=self.role,
                                  rollback_confirmed=True, remaining_outer=outer_depth)
            raise

    async def run_sync(self, operation, budget: RequestBudget | None = None):
        """Run operation(conn) without returning thread capacity before SQL has ended."""
        async with self._sync_slot():
            holder = {}
            holder_lock = threading.Lock()
            cancelled = threading.Event()

            def run():
                with self.sync_transaction(budget) as connection:
                    with holder_lock:
                        holder["connection"] = connection
                    try:
                        if cancelled.is_set():
                            raise TimeoutError("sync_operation_cancelled_before_start")
                        return operation(connection)
                    finally:
                        with holder_lock:
                            holder.pop("connection", None)

            future = asyncio.get_running_loop().run_in_executor(self.executor, run)
            try:
                return await asyncio.shield(future)
            except asyncio.CancelledError:
                cancelled.set()
                def cancel_current():
                    # Keep the connection out of the pool until this cancellation has
                    # targeted the original SQL, never a later caller's transaction.
                    with holder_lock:
                        connection = holder.get("connection")
                        if connection:
                            connection.cancel_safe()
                await _finish_cleanup(asyncio.create_task(asyncio.to_thread(cancel_current)))
                await _finish_cleanup(future)
                raise

    @asynccontextmanager
    async def transaction(self, budget: RequestBudget | None = None, *, control=False,
                          read_only=False, connection=None):
        """Use the caller's explicit connection or acquire one isolated transaction.

        Supplied connections retain the outer transaction's isolation and commit
        boundary. They must belong to this task and must not be shared with a
        concurrently executing child task.
        """
        if budget:
            budget.check()
        if connection is not None:
            if budget:
                async with budget.timeout():
                    yield connection
            else:
                yield connection
            return
        pool = self.control_pool if control else self.pool
        pool_name = "control" if control else "business"
        scope_key = ("async", asyncio.current_task())
        outer_depth = self._scope_depth(scope_key)
        timeout = min(self.config.connect_timeout, budget.remaining()) if budget else None
        started, acquired, phase = time.monotonic(), False, "connection_acquire"
        self._metrics_add(pool_name, acquire_waiting=1)
        try:
            async with pool.connection(timeout=timeout) as connection:
                acquired, phase = True, "transaction"
                self._enter_scope(scope_key)
                self._metrics_add(pool_name, acquire_waiting=-1, in_transaction=1, acquire_count=1,
                                  acquire_wait_ms=int((time.monotonic()-started)*1000))
                try:
                    async with connection.transaction():
                        if read_only:
                            await connection.execute("SET TRANSACTION READ ONLY")
                        milliseconds = max(1, math.ceil(budget.remaining() * 1000)) if budget else 30000
                        await connection.execute(
                            "SELECT set_config('statement_timeout', %s, true), "
                            "set_config('lock_timeout', %s, true)",
                            (str(milliseconds), str(min(milliseconds, 5000))),
                        )
                        if budget:
                            async with budget.timeout():
                                yield connection
                        else:
                            yield connection
                        phase = "commit"
                except BaseException as error:
                    if isinstance(error, (asyncio.CancelledError, TimeoutError)):
                        # Cancel the actual SQL before returning the connection to its pool.
                        await _cancel_before_release(connection)
                    self._metrics_add(pool_name, **{phase + "_errors": 1})
                    _annotate_failure(error, phase=phase, pool_name=pool_name, role=self.role,
                                      rollback_confirmed=_rolled_back(connection), remaining_outer=outer_depth)
                    raise
                finally:
                    self._leave_scope(scope_key)
                    self._metrics_add(pool_name, in_transaction=-1)
        except BaseException as error:
            if not acquired:
                self._metrics_add(pool_name, acquire_waiting=-1,
                    acquire_wait_ms=int((time.monotonic()-started)*1000),
                    acquire_timeouts=int(isinstance(error, PoolTimeout)))
                _annotate_failure(error, phase="connection_acquire", pool_name=pool_name, role=self.role,
                                  rollback_confirmed=True, remaining_outer=outer_depth)
            raise
