import os
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
import pytest
import pytest_asyncio

from tianshu.runtime.database import Database, DatabaseConfig
from tianshu.runtime.jobs import JobQueue
from tianshu.runtime.migrations import execution_migrations, foundation_migrations, migrate


@pytest_asyncio.fixture
async def database():
    """Requires an explicitly supplied disposable-database admin, never a production default."""
    admin_dsn = os.environ.get("TIANSHU_TEST_POSTGRES_ADMIN_DSN")
    if not admin_dsn:
        pytest.skip("explicit disposable PostgreSQL+pgvector admin DSN required")
    name = "tianshu_test_"+uuid4().hex
    admin = await psycopg.AsyncConnection.connect(admin_dsn, autocommit=True)
    await admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    db = Database(DatabaseConfig(make_conninfo(admin_dsn, dbname=name)))
    await db.open()
    queue = JobQueue(db.config.dsn, database=db)
    try:
        await migrate(db, foundation_migrations() + execution_migrations(queue))
        yield db
    finally:
        await db.close()
        await admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
        await admin.close()
