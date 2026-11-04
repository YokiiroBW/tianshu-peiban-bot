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


def product_migrations(jobs):
    """The sole installed product baseline; collecting it does not construct/start services."""
    resources = (
        ("0010_identity", "tianshu.identity", ("schema.sql",)),
        ("0011_sources", "tianshu.sources", ("schema.sql", "functions.sql")),
        ("0012_sources_rb", "tianshu.sources", ("0012_sources_rb.sql",)),
        ("0020_memory", "tianshu.memory", ("schema.sql",)),
        ("0021_memory_consolidation", "tianshu.memory", ("0021_memory_consolidation.sql",)),
        ("0022_memory_runtime", "tianshu.memory", ("0022_memory_runtime.sql",)),
        ("0023_memory_index_retry", "tianshu.memory", ("0023_memory_index_retry.sql",)),
        ("0024_memory_consolidation_retry", "tianshu.memory", ("0024_memory_consolidation_retry.sql",)),
        ("0025_memory_write", "tianshu.memory", ("0025_memory_write.sql",)),
        ("0026_memory_retrieval", "tianshu.memory", ("rb_retrieval.sql",)),
        ("0030_companion", "tianshu.companion", ("schema.sql",)),
        ("0031_life", "tianshu.life", ("schema.sql",)),
        ("0032_conversation", "tianshu.conversation", ("schema.sql",)),
        ("0033_life_contact_expiry", "tianshu.life", ("contact_expiry.sql",)),
        ("0034_companion_growth_batches", "tianshu.companion", ("growth_batches.sql",)),
        ("0035_life_planning_recovery", "tianshu.life", ("planning_recovery.sql",)),
        ("0036_conversation_expression_queue_timing", "tianshu.conversation", ("expression_queue_timing.sql",)),
        ("0037_companion_growth_retry", "tianshu.companion", ("growth_retry.sql",)),
        ("0038_life_computation_retry", "tianshu.life", ("0038_life_computation_retry.sql",)),
        ("0039_companion_learning", "tianshu.companion", ("habit_revisions.sql",)),
        ("0039_conversation_context_selection", "tianshu.conversation", ("context_selection.sql",)),
        ("0040_conversation_input_supplements", "tianshu.conversation", ("input_supplements.sql",)),
        ("0040_life_execution", "tianshu.life", ("rb_life_execution.sql",)),
        ("0041_life_experience", "tianshu.life", ("rb_experience.sql",)),
        ("capabilities-0001", "tianshu.capabilities", ("schema.sql",)),
        ("capabilities-0002-runtime-observations", "tianshu.capabilities", ("runtime_observations.sql",)),
        ("capabilities-projects-0001", "tianshu.capabilities.projects", ("schema.sql",)),
        ("capabilities-projects-0002-independent", "tianshu.capabilities.projects", ("independent.sql",)),
        ("channels-0001", "tianshu.channels", ("schema.sql",)),
        ("channels-0002-media-supplements", "tianshu.channels", ("media_supplements.sql",)),
        ("operations-0001", "tianshu.operations", ("schema.sql",)),
    )
    return (
        *foundation_migrations(),
        *execution_migrations(jobs),
        *(
            Migration(name, "\n".join(files(package).joinpath(part).read_text() for part in parts))
            for name, package, parts in resources
        ),
    )


def execution_migrations(jobs):
    """Shared queue, generation and Models ledger prerequisites for every domain."""
    return (
        Migration("0003_procrastinate", jobs.app.schema_manager.get_schema()),
        Migration("0007_rb_runtime", files("tianshu.runtime").joinpath("rb_runtime.sql").read_text()),
        Migration("0008_models_async_execution", files("tianshu.models").joinpath("async_execution.sql").read_text()),
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
