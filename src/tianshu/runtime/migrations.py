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
    return (
        Migration("20261105_fresh_installation",
            files("tianshu.runtime").joinpath("baseline.sql").read_text(encoding="utf-8")),
        Migration("rbh_not_sent",
            files("tianshu.runtime").joinpath("rbh_not_sent.sql").read_text(encoding="utf-8")),
        *rbi_recovery_migrations(),
        *rbj_runtime_migrations(),
        Migration("rbh_growth_transport",
            files("tianshu.companion").joinpath("rbh_growth_transport.sql").read_text(encoding="utf-8")),
        Migration("rbi_growth_maintenance",
            files("tianshu.companion").joinpath("rbi_growth_maintenance.sql").read_text(encoding="utf-8")),
        Migration("rbi_memory_maintenance",
            files("tianshu.memory").joinpath("rbi_maintenance.sql").read_text(encoding="utf-8")),
        Migration("memory_episode_batch_lookup",
            files("tianshu.memory").joinpath("episode_batch_lookup.sql").read_text(encoding="utf-8")),
        *model_recovery_migrations(),
        *user_work_migrations(),
        Migration("0041_conversation_submission_confirmation",
            files("tianshu.conversation").joinpath("submission_confirmation.sql").read_text(encoding="utf-8")),
        Migration("rbg_result_consumption",
            files("tianshu.conversation").joinpath("rbg_result_consumption.sql").read_text(encoding="utf-8")),
        Migration("rbi_result_reconcile",
            files("tianshu.conversation").joinpath("rbi_result_reconcile.sql").read_text(encoding="utf-8")),
        Migration("rbj_result_repair_evidence",
            files("tianshu.conversation").joinpath("rbj_result_repair_evidence.sql").read_text(encoding="utf-8")),
        Migration("rbf_life_planning_wake",
            files("tianshu.life").joinpath("rbf_life_planning_wake.sql").read_text(encoding="utf-8")),
        Migration("rbg_plan_retirement",
            files("tianshu.life").joinpath("rbg_plan_retirement.sql").read_text(encoding="utf-8")),
        Migration("rbh_transport_recovery",
            files("tianshu.life").joinpath("rbh_transport_recovery.sql").read_text(encoding="utf-8")),
        Migration("rbi_activity_reconciliation",
            files("tianshu.life").joinpath("rbi_activity_reconciliation.sql").read_text(encoding="utf-8")),
        Migration("private_contact_default",
            files("tianshu.life").joinpath("private_contact_default.sql").read_text(encoding="utf-8")),
        Migration("role_administrator",
            files("tianshu.identity").joinpath("role_administrator.sql").read_text(encoding="utf-8")),
        Migration("persona_storage",
            files("tianshu.companion").joinpath("persona_storage.sql").read_text(encoding="utf-8")),
        Migration("rbf_source_proof_jit",
            files("tianshu.sources").joinpath("rbf_source_proof_jit.sql").read_text(encoding="utf-8")),
        Migration("rbf_source_proof_reuse",
            files("tianshu.sources").joinpath("rbf_source_proof_reuse.sql").read_text(encoding="utf-8")),
        Migration("rbj_lock_graph",
            files("tianshu.sources").joinpath("rbj_lock_graph.sql").read_text(encoding="utf-8")),
        Migration("rbj_source_proof",
            files("tianshu.sources").joinpath("rbj_source_proof.sql").read_text(encoding="utf-8")),
    )


def rbi_recovery_migrations():
    return (
        Migration("rbi_recovery_repairs",
            files("tianshu.runtime").joinpath("rbi_recovery_repairs.sql").read_text(encoding="utf-8")),
        Migration("rbi_execution_budget",
            files("tianshu.runtime").joinpath("rbi_execution_budget.sql").read_text(encoding="utf-8")),
    )


def rbj_runtime_migrations():
    return (
        Migration("rbj_wake",
            files("tianshu.runtime").joinpath("rbj_wake.sql").read_text(encoding="utf-8")),
        Migration("rbj_execution_repair",
            files("tianshu.runtime").joinpath("rbj_execution_repair.sql").read_text(encoding="utf-8")),
        Migration("rbj_native_admission",
            files("tianshu.runtime").joinpath("rbj_native_admission.sql").read_text(encoding="utf-8")),
    )


def model_recovery_migrations():
    return (
        Migration("models_function_bindings",
            files("tianshu.models").joinpath("function_bindings.sql").read_text(encoding="utf-8")),
        Migration("models_recovery_wait",
            files("tianshu.models").joinpath("recovery_wait.sql").read_text(encoding="utf-8")),
        Migration("models_prestart_reconciliation",
            files("tianshu.models").joinpath("prestart_reconciliation.sql").read_text(encoding="utf-8")),
        Migration("rbg_admission_retirement",
            files("tianshu.models").joinpath("rbg_admission_retirement.sql").read_text(encoding="utf-8")),
    )


def execution_migrations(jobs):
    """Shared queue, generation and Models ledger prerequisites for every domain."""
    return (
        Migration("0003_procrastinate", jobs.app.schema_manager.get_schema()),
        Migration("0007_rb_runtime", files("tianshu.runtime").joinpath("rb_runtime.sql").read_text()),
        Migration("0008_rb_recovery_change", files("tianshu.runtime").joinpath("rb_recovery_change.sql").read_text()),
        Migration("rb_recovery_computation", files("tianshu.runtime").joinpath("rb_recovery_computation.sql").read_text()),
        Migration("rbh_not_sent",
            files("tianshu.runtime").joinpath("rbh_not_sent.sql").read_text(encoding="utf-8")),
        *rbi_recovery_migrations(),
        *rbj_runtime_migrations(),
        Migration("0008_models_async_execution", files("tianshu.models").joinpath("async_execution.sql").read_text()),
        Migration("0009_models_generation_fence", files("tianshu.models").joinpath("generation_fence.sql").read_text()),
        *model_recovery_migrations(),
        *user_work_migrations(),
    )


def user_work_migrations():
    return (Migration("user_work_retirement",
        files("tianshu.runtime").joinpath("user_work_retirement.sql").read_text(encoding="utf-8")),)


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
