"""Ordered fresh-install baseline and subsequent product migrations."""

from dataclasses import dataclass
from importlib.resources import files

from .database import Database


@dataclass(frozen=True, slots=True)
class Migration:
    name: str
    sql: str


def foundation_migrations():
    return (
        Migration("0001_runtime", files("tianshu.runtime").joinpath("schema.sql").read_text()),
    )


async def migrate(database: Database, migrations: tuple[Migration, ...]):
    async with database.transaction(control=True) as connection:
        await connection.execute("SELECT pg_advisory_xact_lock(733104310)")
        await connection.execute("CREATE SCHEMA IF NOT EXISTS runtime")
        await connection.execute("CREATE TABLE IF NOT EXISTS runtime.schema_migrations "
            "(name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())")
        for migration in migrations:
            cursor = await connection.execute(
                "SELECT name FROM runtime.schema_migrations WHERE name=%s", (migration.name,))
            if await cursor.fetchone():
                continue
            await connection.execute(migration.sql, prepare=False)
            await connection.execute("INSERT INTO runtime.schema_migrations(name) VALUES (%s)",
                                     (migration.name,))
