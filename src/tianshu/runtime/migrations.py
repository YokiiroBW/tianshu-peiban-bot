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
    )


def product_migrations(jobs):
    """The sole installed product baseline; collecting it does not construct/start services."""
    resources = (
        ("0010_identity", "tianshu.identity", ("schema.sql",)),
        ("0011_sources", "tianshu.sources", ("schema.sql", "functions.sql")),
        ("0020_memory", "tianshu.memory", ("schema.sql",)),
        ("0021_memory_consolidation", "tianshu.memory", ("0021_memory_consolidation.sql",)),
        ("0030_companion", "tianshu.companion", ("schema.sql",)),
        ("0031_life", "tianshu.life", ("schema.sql",)),
        ("0032_conversation", "tianshu.conversation", ("schema.sql",)),
        ("0033_life_contact_expiry", "tianshu.life", ("contact_expiry.sql",)),
        ("0034_companion_growth_batches", "tianshu.companion", ("growth_batches.sql",)),
        ("capabilities-0001", "tianshu.capabilities", ("schema.sql",)),
        ("capabilities-0002-runtime-observations", "tianshu.capabilities", ("runtime_observations.sql",)),
        ("capabilities-projects-0001", "tianshu.capabilities.projects", ("schema.sql",)),
        ("channels-0001", "tianshu.channels", ("schema.sql",)),
    )
    return (
        *foundation_migrations(),
        Migration("0003_procrastinate", jobs.app.schema_manager.get_schema()),
        *(
            Migration(name, "\n".join(files(package).joinpath(part).read_text() for part in parts))
            for name, package, parts in resources
        ),
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
