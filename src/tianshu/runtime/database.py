"""Native asynchronous PostgreSQL. No backend selection or SQL translation."""

from __future__ import annotations

import asyncio
import math
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from psycopg.rows import dict_row
from psycopg.conninfo import conninfo_to_dict
from psycopg import ProgrammingError
from psycopg_pool import AsyncConnectionPool, ConnectionPool

from tianshu.types import RequestBudget


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

    def __init__(self, config: DatabaseConfig):
        self.config = config
        options = {"conninfo": config.dsn, "min_size": 0, "open": False,
            "kwargs": {"autocommit": True, "row_factory": dict_row},
            "timeout": config.connect_timeout}
        self.pool = AsyncConnectionPool(max_size=config.max_connections, **options)
        self.control_pool = AsyncConnectionPool(max_size=config.control_connections, **options)
        self.sync_pool = ConnectionPool(conninfo=config.dsn, min_size=0,
            max_size=config.sync_connections, open=False,
            kwargs={"autocommit": True, "row_factory": dict_row},
            timeout=config.connect_timeout)
        self.executor = ThreadPoolExecutor(max_workers=config.sync_connections,
                                           thread_name_prefix="tianshu-db")
        self._sync_slots = asyncio.Semaphore(config.sync_connections)

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
    def sync_transaction(self, budget: RequestBudget | None = None, *, read_only=False):
        """Native short transactions for mature synchronous repositories in worker threads."""
        if budget:
            budget.check()
        with self.sync_pool.connection() as connection:
            with connection.transaction():
                if read_only:
                    connection.execute("SET TRANSACTION READ ONLY")
                milliseconds = max(1, math.ceil(budget.remaining() * 1000)) if budget else 30000
                connection.execute("SELECT set_config('statement_timeout',%s,true), "
                    "set_config('lock_timeout',%s,true)",
                    (str(milliseconds), str(min(milliseconds, 5000))))
                yield connection

    async def run_sync(self, operation, budget: RequestBudget | None = None):
        """Run operation(conn) without returning thread capacity before SQL has ended."""
        async with self._sync_slots:
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
                await asyncio.to_thread(cancel_current)
                try:
                    await asyncio.shield(future)
                except Exception:
                    pass
                raise

    @asynccontextmanager
    async def transaction(self, budget: RequestBudget | None = None, *, control=False,
                          read_only=False):
        pool = self.control_pool if control else self.pool
        timeout = min(self.config.connect_timeout, budget.remaining()) if budget else None
        if budget:
            budget.check()
        async with pool.connection(timeout=timeout) as connection:
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
            except (asyncio.CancelledError, TimeoutError):
                # Cancel the actual SQL before returning this connection to its pool.
                await asyncio.shield(connection.cancel_safe())
                raise
