"""Adapt historical Life scenarios to the current, isolated domain contracts."""

from dataclasses import replace
from datetime import UTC, datetime
from importlib.resources import files

from tianshu.identity.service import IdentityService
from tianshu.models.ledger import submission_digest
from tianshu.runtime.errors import AppError
from tianshu.runtime.jobs import JobQueue
from tianshu.runtime.migrations import Migration, migrate
from tianshu.runtime.recovery_policy import RecoveryPolicy


async def migrations(database, *, diary=False):
    await migrate(database, (Migration("0040_life_execution",
        files("tianshu.life").joinpath("rb_life_execution.sql").read_text()),
        Migration("rbg_life_plan_retirement_test",
        files("tianshu.life").joinpath("rbg_plan_retirement.sql").read_text())))
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
    if getattr(models, "_life_port_adapted", False):
        return models
    old_profile = models.generation_profile
    async def profile(request):
        return {**(await old_profile(request)), "quota_group": "quota"}
    models.generation_profile = profile
    saved = {}
    completion_jobs = {}
    old_status, old_result = models.status, models.result

    def finished(result, request):
        settled = result.receipt.status in {"succeeded", "failed", "cancelled", "unknown"}
        result = replace(result, receipt=replace(result.receipt,
            finished_at=result.receipt.finished_at or (datetime.now(UTC) if settled else None),
            request_digest=submission_digest(request), data_generation=request.data_generation))
        models.actual = result
        if hasattr(models, "results"):
            models.results[result.receipt.attempt_id] = result
            models.receipts[result.receipt.attempt_id] = result.receipt
        return result

    async def submit(request, *, completion=None):
        if request.attempt_id in retirements and request.attempt_id not in saved:
            raise AppError("model_admission_retired",status=409)
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

    async def status(attempt_id, *, phase="observe"):
        assert phase in {"pre_submit", "observe"}
        # The fake's receipt ledger is authoritative even when a previously
        # returned body has an older status or cancellation observation.
        result = await old_status(attempt_id)
        if result is not None:
            return result
        return saved[attempt_id][1].receipt if attempt_id in saved else None

    async def result(attempt_id, *, context):
        if hasattr(models, "results") and attempt_id in models.results:
            return await old_result(attempt_id,context=context)
        if models.actual and models.actual.receipt.attempt_id == attempt_id:
            models.observations.append((attempt_id, context))
            return models.actual
        if attempt_id in saved:
            models.observations.append((attempt_id, context))
            return saved[attempt_id][1]
        return await old_result(attempt_id, context=context)

    async def reusable_result(attempt_id, *, context, expected_request_digest=None, expected_data_generation=None):
        from tianshu.models.results import validate_reusable_receipt
        current = await models.status(attempt_id)
        if current is None:
            raise AppError("attempt_not_found", status=404)
        validate_reusable_receipt(current, attempt_id, expected_request_digest=expected_request_digest,
            expected_data_generation=expected_data_generation)
        original = await models.result(attempt_id, context=context)
        if original is None:
            raise AppError("model_result_unavailable", status=409)
        return replace(original, receipt=current)

    models.submit, models.status, models.result = submit, status, result
    models.reusable_result = reusable_result
    retirements = {}
    async def inspect_admission_retirement(attempt_id, *, lineage_id, data_generation, context, original_context):
        receipt = await status(attempt_id)
        return {"attempt_id":attempt_id,"lineage_id":lineage_id,"data_generation":data_generation,
            "state":"sealed" if attempt_id in retirements else "unsealed","reason":retirements.get(attempt_id),
            "explicit_cancel":bool(receipt and receipt.cancellation_reason == "explicit_cancel"),
            "status":receipt.status if receipt else None,"dispatch":receipt.dispatch if receipt else None,
            "error_code":receipt.error_code if receipt else None}
    async def seal_admission(attempt_id, *, lineage_id, data_generation, context, original_context, reason,
                             expected_status=None):
        current = await status(attempt_id)
        explicit = bool(current and current.cancellation_reason == "explicit_cancel")
        if (attempt_id not in retirements and not explicit and expected_status is not None
                and expected_status != (current.status if current else "absent")):
            return {"attempt_id":attempt_id,"lineage_id":lineage_id,"data_generation":data_generation,
                "state":"observation_changed","reason":reason,"status":current.status if current else None,
                "dispatch":current.dispatch if current else None,"explicit_cancel":False}
        retirements.setdefault(attempt_id,reason)
        return {"attempt_id":attempt_id,"lineage_id":lineage_id,"data_generation":data_generation,
            "state":"explicit_cancelled" if explicit else "sealed_existing" if current else "sealed_absent",
            "reason":retirements[attempt_id],"status":current.status if current else None,
            "dispatch":current.dispatch if current else None,"explicit_cancel":explicit}
    models.inspect_admission_retirement = inspect_admission_retirement
    models.seal_admission = seal_admission
    models.completions = completion_jobs
    models._life_port_adapted = True
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
