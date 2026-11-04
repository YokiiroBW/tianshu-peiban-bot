"""Adapt historical Life scenarios to the current, isolated domain contracts."""

from dataclasses import replace
from datetime import UTC, datetime
from importlib.resources import files

from tianshu.identity.service import IdentityService
from tianshu.runtime.jobs import JobQueue
from tianshu.runtime.migrations import Migration, migrate
from tianshu.runtime.recovery_policy import RecoveryPolicy


async def migrations(database, *, diary=False):
    await migrate(database, (Migration("0040_life_execution",
        files("tianshu.life").joinpath("rb_life_execution.sql").read_text()),))
    async with database.transaction(read_only=True) as connection:
        runtime = await (await connection.execute("SELECT to_regclass('runtime.recovery_lineages') AS name")).fetchone()
        column = await (await connection.execute(
            "SELECT 1 FROM information_schema.columns WHERE table_schema='life' "
            "AND table_name='diaries' AND column_name='scene_key'")).fetchone()
    if not runtime["name"]:
        await migrate(database, (Migration("rb_runtime_historical_test",
            files("tianshu.runtime").joinpath("rb_runtime.sql").read_text()),))
    if diary and not column:
        await migrate(database, (Migration("rb_experience_historical_test",
            files("tianshu.life").joinpath("rb_experience.sql").read_text()),))


def adapt_models(models):
    """Store an immutable result per physical attempt behind the submit port."""
    old_profile = models.generation_profile
    async def profile(request):
        return {**(await old_profile(request)), "quota_group": "quota"}
    models.generation_profile = profile
    saved = {}
    completion_jobs = {}
    old_status, old_result = models.status, models.result

    def finished(result, request):
        if result.receipt.status == "unknown" and result.receipt.finished_at is None:
            result = replace(result, receipt=replace(result.receipt, finished_at=datetime.now(UTC),
                data_generation=request.data_generation))
            models.actual = result
            if hasattr(models, "results"):
                models.results[result.receipt.attempt_id] = result
                models.receipts[result.receipt.attempt_id] = result.receipt
        return result

    async def submit(request, *, completion=None):
        if request.attempt_id in saved:
            assert saved[request.attempt_id][0] == request
            return saved[request.attempt_id][1].receipt
        completion_jobs[request.attempt_id] = completion
        try:
            result = await models.generate(request)
        except TimeoutError:
            # The fixture represents a lost response only after the original
            # physical attempt has produced its unknown receipt.
            result = finished(models.actual, request)
            assert result and result.receipt.attempt_id == request.attempt_id
            saved[request.attempt_id] = (request, result)
            raise
        result = finished(result, request)
        saved[request.attempt_id] = (request, result)
        return result.receipt

    async def status(attempt_id):
        # Allow scenarios to publish a later observation of the same attempt.
        if models.actual and models.actual.receipt.attempt_id == attempt_id:
            return models.actual.receipt
        result = await old_status(attempt_id)
        if result is not None:
            return result
        return saved[attempt_id][1].receipt if attempt_id in saved else None

    async def result(attempt_id, *, context):
        if models.actual and models.actual.receipt.attempt_id == attempt_id:
            models.observations.append((attempt_id, context))
            return models.actual
        if attempt_id in saved:
            models.observations.append((attempt_id, context))
            return saved[attempt_id][1]
        return await old_result(attempt_id, context=context)

    models.submit, models.status, models.result = submit, status, result
    models.completions = completion_jobs
    return models


async def adapt_planner(value, database):
    await migrations(database)
    adapt_models(value.models)
    value.worker.life.bind_recovery_policy(RecoveryPolicy())
    value.worker.catalog.planning = value.worker.catalog.discover
    async with database.transaction(read_only=True) as connection:
        value.data_generation = await value.jobs.current_generation_on(connection)
    return value


async def planner_setup(database, output, **kwargs):
    from test_planner_execution import setup

    return await adapt_planner(await setup(database, output, **kwargs), database)


async def diary_fixture(database, **kwargs):
    from test_diary_automation import fixture

    value = await fixture(database, **kwargs)
    await migrations(database, diary=True)
    value.identity.scene_id = IdentityService.scene_id
    value.jobs.current_generation_on = JobQueue.current_generation_on
    value.jobs.assert_generation_on = JobQueue.assert_generation_on
    async def current_generation():
        async with database.transaction(read_only=True) as connection:
            return await value.jobs.current_generation_on(connection)
    value.jobs.current_generation = current_generation
    adapt_models(value.models)
    value.diaries.bind_observability(recovery_policy=RecoveryPolicy(), statistics=None)
    return value
