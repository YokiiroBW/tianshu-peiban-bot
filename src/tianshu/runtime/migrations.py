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
        Migration("0002_models", files("tianshu.models").joinpath("schema.sql").read_text()),
        Migration("0004_models_execution_timing", files("tianshu.models").joinpath("execution_timing.sql").read_text()),
        Migration("0005_models_provider_execution_clock", files("tianshu.models").joinpath("provider_execution_clock.sql").read_text()),
        Migration("0006_models_cancellation_reasons", files("tianshu.models").joinpath("cancellation_reasons.sql").read_text()),
    )


def product_migrations():
    """Install the frozen empty schema, then apply subsequent named upgrades."""
    return (Migration("20261105_fresh_installation",
        files("tianshu.runtime").joinpath("baseline.sql").read_text(encoding="utf-8")),)


def execution_migrations(jobs):
    """Shared queue, generation and Models ledger prerequisites for every domain."""
    return (
        Migration("0003_procrastinate", jobs.app.schema_manager.get_schema()),
        Migration("0007_rb_runtime", files("tianshu.runtime").joinpath("rb_runtime.sql").read_text()),
        Migration("0008_rb_recovery_change", files("tianshu.runtime").joinpath("rb_recovery_change.sql").read_text()),
        Migration("0008_models_async_execution", files("tianshu.models").joinpath("async_execution.sql").read_text()),
        Migration("0009_models_generation_fence", files("tianshu.models").joinpath("generation_fence.sql").read_text()),
    )


async def migrate(database: Database, migrations: tuple[Migration, ...]):
    async with database.transaction(control=True) as connection:
        await connection.execute("SELECT pg_advisory_xact_lock(733104310)")
        await connection.execute("CREATE SCHEMA IF NOT EXISTS runtime")
        await connection.execute(
            "CREATE TABLE IF NOT EXISTS runtime.schema_migrations "
            "(name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
        )
        for migration in migrations:
            cursor = await connection.execute(
                "SELECT name FROM runtime.schema_migrations WHERE name=%s", (migration.name,)
            )
            if await cursor.fetchone():
                continue
            await connection.execute(migration.sql, prepare=False)
            await connection.execute("SELECT set_config('search_path','public',true)")
            await connection.execute(
                "INSERT INTO runtime.schema_migrations(name) VALUES (%s)", (migration.name,)
            )
        version = await (
            await connection.execute("SELECT extversion FROM pg_extension WHERE extname='vector'")
        ).fetchone()
        if not version or tuple(int(part) for part in version["extversion"].split(".")[:2]) < (
            0,
            8,
        ):
            raise ValueError("pgvector_0_8_or_newer_required")
