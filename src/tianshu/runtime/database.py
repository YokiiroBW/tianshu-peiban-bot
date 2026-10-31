"""Native asynchronous PostgreSQL. No backend selection or SQL translation."""

from __future__ import annotations

import asyncio
import math
from contextlib import asynccontextmanager
from dataclasses import dataclass

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from tianshu.types import RequestBudget


@dataclass(frozen=True, slots=True)
class DatabaseConfig:
    dsn: str
    max_connections: int = 8
    control_connections: int = 2
    connect_timeout: float = 5.0

    def __post_init__(self):
        if not self.dsn.startswith(("postgresql://", "postgres://")):
            raise ValueError("postgresql_dsn_required")
        if self.max_connections < 1 or self.control_connections < 1:
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

    async def open(self):
        await self.pool.open(wait=True)
        await self.control_pool.open(wait=True)

    async def close(self):
        await self.pool.close()
        await self.control_pool.close()

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
            except asyncio.CancelledError:
                # Cancel the actual SQL before returning this connection to its pool.
                await asyncio.shield(connection.cancel_safe())
                raise
