from importlib.resources import files

import pytest_asyncio

from tianshu.runtime.migrations import Migration, migrate


@pytest_asyncio.fixture
async def operations_db(database):
    async with database.transaction(read_only=True) as conn:
        existing = await (await conn.execute("SELECT to_regclass('runtime.recovery_lineages') AS recovery, "
                                             "to_regclass('operations.incidents') AS incidents")).fetchone()
    migrations = []
    if not existing["recovery"]:
        migrations.append(Migration("rb-runtime-for-operations-test", files("tianshu.runtime").joinpath("rb_runtime.sql").read_text()))
    if not existing["incidents"]:
        migrations.append(Migration("operations-test", files("tianshu.operations").joinpath("schema.sql").read_text()))
    if migrations:
        await migrate(database, tuple(migrations))
    return database
