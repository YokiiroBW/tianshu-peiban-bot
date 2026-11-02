"""Failed pure computations yield, survive restart and apply one exact version."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import json

import pytest

from tianshu.companion.growth import GrowthService
from tianshu.companion.reflection_output import frozen_episodes
from tianshu.conversation.codec import signature
from tianshu.models.types import ModelCompletion, ModelPart, ModelReceipt, ModelResult
from tianshu.runtime.errors import AppError
from test_growth_output import note, prepare, state


class ScriptedModels:
    def __init__(self, *script):
        self.script, self.calls, self.results, self.reads = script, [], {}, []

    async def generate(self, request):
        spec = self.script[len(self.calls)]
        self.calls.append(request)
        kinds = spec.get("kinds", ())
        notes = [{**note(kind), "episode_ids": list(frozen_episodes(request))} for kind in kinds]
        for value in notes:
            if spec.get("affect"):
                value["affect"] = {"appraisal": "对原经历的感受", "valence": 0.2, "arousal": 0.2,
                    "stress": 0, "social_need": 0.1, "confidence": 0.6, "goal_relevance": 0,
                    "attribution": "situation", "control": 0.5}
        status = spec.get("status", "succeeded")
        result = ModelResult((ModelPart("text", text=json.dumps({"notes": notes})),) if status == "succeeded" else (),
            ModelReceipt(request.attempt_id, request.intent_id, status,
                "unknown" if status == "unknown" else "submitted", "route", 1, "provider", "model", "openai.chat", "quota",
                error_code=spec.get("error_code")),
            completion=ModelCompletion("complete", ("stop",)) if status == "succeeded" else ModelCompletion())
        self.results[request.attempt_id] = result
        if spec.get("rpc_error"):
            raise AppError(spec["rpc_error"], status=500)
        return result

    async def result(self, attempt_id, *, context):
        self.reads.append(attempt_id)
        return self.results.get(attempt_id)


async def retry_row(database):
    async with database.transaction(read_only=True) as connection:
        return await (await connection.execute("SELECT * FROM companion.growth_retry_batches")).fetchone()


async def due(database):
    async with database.transaction() as connection:
        await connection.execute("UPDATE companion.growth_retry_batches SET due_at=now()-interval '1 second'")


async def tail(growth, memory, context, *, identifier="later", sequence=2):
    episode = replace(memory.episode, episode_id=identifier, event_id=identifier, sequence=sequence,
        cursor=f"e:{sequence}", occurred_at=memory.episode.occurred_at+timedelta(minutes=sequence))
    memory.episodes += (episode,)
    await growth.changed(context, identifier, episode=episode)
    return episode


@pytest.mark.asyncio
async def test_unknown_yields_exact_versions_tail_advances_and_retry_cannot_regress_cursor(database):
    models = ScriptedModels({"status": "unknown", "error_code": "transport_unknown", "rpc_error": "ServerDisconnectedError"},
        {}, {"kinds": ("reflection",)})
    growth, memory, jobs, context, _ = await prepare(database, models)
    scope = signature(sorted(context.scope_ids))
    failure = await growth.organize("actor", scope, 1)
    original = models.calls[0]
    original_result = models.results[original.attempt_id]
    async with database.transaction(read_only=True) as connection:
        frozen_before = await (await connection.execute(
            "SELECT request FROM conversation.generation_requests WHERE attempt_id=%s", (original.attempt_id,))).fetchone()
    assert failure["state"] == "retry_waiting" and (await state(database))["active_batch"] is None
    row = await retry_row(database)
    assert row["attempt_number"] == 1 and 25 < (row["due_at"]-datetime.now(UTC)).total_seconds() <= 30
    assert any(job.task == "companion.growth.retry" for job in jobs.items)
    await tail(growth, memory, context)
    await growth.organize("actor", scope, (await state(database))["revision"])
    assert set(frozen_episodes(models.calls[1])) == {"later"}
    assert (await state(database))["cursor"] == "e:2"
    await due(database)
    await growth.retries.run("actor", scope, row["retry_key"])
    assert len(models.calls) == 3 and set(frozen_episodes(models.calls[2])) == {"episode"}
    assert models.calls[2].attempt_id != original.attempt_id
    assert models.calls[2].intent_id.startswith(original.intent_id + ":retry:")
    assert models.calls[2].execution_timeout_seconds == 180 and models.calls[2].business_deadline_at is None
    assert (await state(database))["cursor"] == "e:2"
    assert (await retry_row(database))["state"] == "completed"
    assert models.results[original.attempt_id] is original_result
    async with database.transaction(read_only=True) as connection:
        frozen_after = await (await connection.execute(
            "SELECT request FROM conversation.generation_requests WHERE attempt_id=%s", (original.attempt_id,))).fetchone()
        versions = await (await connection.execute("SELECT reviewed_revision,retry_key FROM companion.growth_inputs")).fetchall()
    assert frozen_after == frozen_before and all(item["reviewed_revision"] == 1 and item["retry_key"] is None for item in versions)
    assert len(memory.proposals) == 1


@pytest.mark.asyncio
async def test_retry_failure_releases_owner_again_with_backoff_and_one_logical_record(database):
    models = ScriptedModels({"status": "unknown", "error_code": "timeout_unknown"},
        {"status": "failed", "error_code": "upstream_http_503"}, {})
    growth, memory, _jobs, context, _ = await prepare(database, models)
    scope = signature(sorted(context.scope_ids))
    await growth.organize("actor", scope, 1)
    row = await retry_row(database)
    await due(database)
    outcome = await growth.retries.run("actor", scope, row["retry_key"])
    assert outcome["state"] == "retry_waiting"
    row = await retry_row(database)
    assert row["attempt_number"] == 2 and 55 < (row["due_at"]-datetime.now(UTC)).total_seconds() <= 60
    assert len(row["history"]) == 2 and row["previous_material_key"] == models.calls[0].intent_id
    assert (await state(database))["active_batch"] is None
    await tail(growth, memory, context)
    await growth.organize("actor", scope, (await state(database))["revision"])
    assert set(frozen_episodes(models.calls[-1])) == {"later"}


@pytest.mark.asyncio
async def test_due_retry_observes_live_or_successful_original_without_duplicate_submission(database):
    models = ScriptedModels({"status": "unknown", "error_code": "transport_unknown"})
    growth, memory, _jobs, context, _ = await prepare(database, models)
    scope = signature(sorted(context.scope_ids))
    await growth.organize("actor", scope, 1)
    original = models.calls[0]
    result = models.results[original.attempt_id]
    row = await retry_row(database)
    models.results[original.attempt_id] = replace(result, receipt=replace(result.receipt, status="running"))
    await due(database)
    await growth.retries.run("actor", scope, row["retry_key"])
    assert len(models.calls) == 1 and (await retry_row(database))["waiting_on_original"]
    models.results[original.attempt_id] = replace(result, parts=(ModelPart("text", text='{"notes":[]}'),),
        receipt=replace(result.receipt, status="succeeded"), completion=ModelCompletion("complete", ("stop",)))
    await due(database)
    await growth.retries.run("actor", scope, row["retry_key"])
    await growth.retries.run("actor", scope, row["retry_key"])
    assert len(models.calls) == 1 and (await retry_row(database))["state"] == "completed"
    assert (await state(database))["cursor"] == "e:1" and not memory.proposals


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["upstream_http_400", "model_function_not_configured", "cancelled_unknown", "consumer_interrupted"])
async def test_permanent_and_explicit_cancel_stop_while_trusted_interrupt_can_retry(database, code):
    models = ScriptedModels({"status": "unknown", "error_code": code})
    growth, _memory, _jobs, context, _ = await prepare(database, models)
    outcome = await growth.organize("actor", signature(sorted(context.scope_ids)), 1)
    assert outcome["state"] == ("retry_waiting" if code == "consumer_interrupted" else "retry_blocked")
    assert (await state(database))["active_batch"] is None
    row = await retry_row(database)
    assert (row["due_at"] is not None) == (code == "consumer_interrupted")


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["source", "version", "permission"])
async def test_retry_rechecks_source_version_and_current_authorization_before_creating_attempt(database, monkeypatch, change):
    models = ScriptedModels({"status": "unknown", "error_code": "timeout_unknown"})
    growth, memory, _jobs, context, _ = await prepare(database, models)
    scope = signature(sorted(context.scope_ids))
    await growth.organize("actor", scope, 1)
    row = await retry_row(database)
    if change == "version":
        memory.episodes = (replace(memory.episode, revision=2),)
        await growth.changed(context, "new-version", episode=memory.episodes[0])
    elif change == "source":
        async def unavailable(*_args):
            return False
        monkeypatch.setattr(growth.companion.sources, "validate_persistence", unavailable)
    else:
        async def forbidden(*_args, **_kwargs):
            raise AppError("permission_denied", status=403)
        monkeypatch.setattr(growth.companion.identity, "check", forbidden)
        monkeypatch.setattr(growth.companion.identity, "refresh_scheduled_context", forbidden, raising=False)
    await due(database)
    await growth.retries.run("actor", scope, row["retry_key"])
    assert len(models.calls) == 1 and (await retry_row(database))["state"] == "blocked"
    assert (await state(database))["active_batch"] is None and not memory.proposals


@pytest.mark.asyncio
async def test_restart_recovers_waiting_batch_without_rewriting_original_or_duplicating_attempt(database):
    models = ScriptedModels({"status": "unknown", "error_code": "transport_unknown"}, {})
    growth, _memory, jobs, context, _ = await prepare(database, models)
    scope = signature(sorted(context.scope_ids))
    await growth.organize("actor", scope, 1)
    row = await retry_row(database)
    restarted = GrowthService(growth.companion, jobs, models, growth.assembler, on_projection_changed=growth.on_projection_changed)
    before = len(models.calls)
    await restarted.recover()
    assert len(models.calls) == before and (await retry_row(database))["current_material_key"] == row["current_material_key"]
    await due(database)
    await restarted.retries.run("actor", scope, row["retry_key"])
    await restarted.retries.run("actor", scope, row["retry_key"])
    assert len(models.calls) == 2 and (await retry_row(database))["state"] == "completed"


@pytest.mark.asyncio
async def test_older_retry_preserves_newer_projection_and_records_partial_coverage(database):
    models = ScriptedModels({"status": "unknown", "error_code": "timeout_unknown"}, {"kinds": ("style",)},
        {"kinds": ("reflection", "style", "interest"), "affect": True})
    growth, memory, _jobs, context, _ = await prepare(database, models)
    scope = signature(sorted(context.scope_ids))
    await growth.organize("actor", scope, 1)
    row = await retry_row(database)
    newer = await tail(growth, memory, context)
    await growth.organize("actor", scope, (await state(database))["revision"])
    await due(database)
    await growth.retries.run("actor", scope, row["retry_key"])
    async with database.transaction(read_only=True) as connection:
        styles = await (await connection.execute("SELECT episode_ids FROM companion.style_rules")).fetchall()
        affects = await (await connection.execute("SELECT count(*) AS n FROM companion.affect_events")).fetchone()
        original = await (await connection.execute("SELECT missing FROM companion.growth_inputs WHERE episode_id='episode'")).fetchone()
    assert len(styles) == 1 and styles[0]["episode_ids"] == [newer.episode_id] and affects["n"] == 0
    assert "projection_superseded_by_newer_material" in original["missing"]
    assert [(item.kind, item.occurred_at) for item in memory.proposals] == [
        ("style", memory.proposals[0].occurred_at), ("reflection", memory.episode.occurred_at)]
    assert (await retry_row(database))["outcome"] == "historical_only"
    assert (await state(database))["reviewed_material_at"] == newer.occurred_at


@pytest.mark.asyncio
async def test_late_original_response_cannot_apply_after_successor_was_selected(database):
    started, release = asyncio.Event(), asyncio.Event()
    models = ScriptedModels({"status": "unknown", "error_code": "transport_unknown"}, {"kinds": ("style",)})
    generate = models.generate

    async def late(request):
        result = await generate(request)
        if len(models.calls) == 1:
            started.set()
            await release.wait()
            return replace(result, parts=(ModelPart("text", text=json.dumps({"notes": [note("style")]})),),
                receipt=replace(result.receipt, status="succeeded"), completion=ModelCompletion("complete", ("stop",)))
        return result

    models.generate = late
    growth, memory, _jobs, context, notifications = await prepare(database, models)
    scope = signature(sorted(context.scope_ids))
    original_task = asyncio.create_task(growth.organize("actor", scope, 1))
    try:
        await asyncio.wait_for(started.wait(), timeout=10)
        await growth.organize("actor", scope, 1)  # Observe the original unknown, yielding its pointer.
        row = await retry_row(database)
        await due(database)
        await growth.retries.run("actor", scope, row["retry_key"])
        assert len(memory.proposals) == len(notifications) == 1
        release.set()
        outcome = await original_task
        assert outcome["state"] == "superseded_attempt"
        assert len(memory.proposals) == len(notifications) == 1
        async with database.transaction(read_only=True) as connection:
            count = await (await connection.execute("SELECT count(*) AS n FROM companion.style_rules")).fetchone()
        assert count["n"] == 1 and (await retry_row(database))["state"] == "completed"
        assert models.results[models.calls[0].attempt_id].receipt.status == "unknown"
    finally:
        release.set()
        await original_task


@pytest.mark.asyncio
async def test_explicit_cancel_arriving_before_successor_freeze_wins_over_original_interruption(database):
    models = ScriptedModels({"status": "unknown", "error_code": "consumer_interrupted"})
    growth, _memory, _jobs, context, _ = await prepare(database, models)
    scope = signature(sorted(context.scope_ids))
    await growth.organize("actor", scope, 1)
    row = await retry_row(database)
    read = models.result
    observations = 0

    async def cancelled_late(attempt_id, *, context):
        nonlocal observations
        result = await read(attempt_id, context=context)
        observations += 1
        if observations == 2:
            return replace(result, receipt=replace(result.receipt, cancellation_reason="explicit_cancel"))
        return result

    models.result = cancelled_late
    await due(database)
    await growth.retries.run("actor", scope, row["retry_key"])
    assert observations == 2 and len(models.calls) == 1
    stopped = await retry_row(database)
    assert stopped["state"] == "blocked" and stopped["last_error"] == "explicit_cancel"
    assert models.results[models.calls[0].attempt_id].receipt.error_code == "consumer_interrupted"


@pytest.mark.asyncio
async def test_existing_active_unknown_is_adopted_by_normal_recover_then_releases_the_partition(database, monkeypatch):
    models = ScriptedModels({"status": "unknown", "error_code": "transport_unknown"}, {})
    growth, memory, jobs, context, _ = await prepare(database, models)
    scope = signature(sorted(context.scope_ids))

    async def old_unknown(state_value, batch, code, result=None):
        async with database.transaction() as connection:
            await connection.execute("UPDATE companion.organize_state SET last_error=%s", (code,))
        return {"state": "waiting"}

    with monkeypatch.context() as old_release:
        old_release.setattr(growth.retries, "defer", old_unknown)
        await growth.organize("actor", scope, 1)
    original = models.calls[0]
    assert (await state(database))["active_batch"]["material_key"] == original.intent_id
    restarted = GrowthService(growth.companion, jobs, models, growth.assembler, on_projection_changed=growth.on_projection_changed)
    await restarted.recover()
    await restarted.organize("actor", scope, (await state(database))["revision"])
    assert len(models.calls) == 1 and (await state(database))["active_batch"] is None
    assert (await retry_row(database))["state"] == "waiting"
    await tail(restarted, memory, context)
    await restarted.organize("actor", scope, (await state(database))["revision"])
    assert len(models.calls) == 2 and set(frozen_episodes(models.calls[-1])) == {"later"}
    assert models.results[original.attempt_id].receipt.status == "unknown"


@pytest.mark.asyncio
async def test_transient_application_read_failure_reuses_successful_result_on_retry(database, monkeypatch):
    models = ScriptedModels({"kinds": ("reflection",)})
    growth, memory, _jobs, context, _ = await prepare(database, models)
    scope = signature(sorted(context.scope_ids))
    record = memory.record_reflection
    failed = False

    async def interrupted(*args, **kwargs):
        nonlocal failed
        if not failed:
            failed = True
            raise TimeoutError("synthetic_source_read_timeout")
        return await record(*args, **kwargs)

    monkeypatch.setattr(memory, "record_reflection", interrupted)
    outcome = await growth.organize("actor", scope, 1)
    assert outcome["state"] == "retry_waiting" and (await state(database))["active_batch"] is None
    await due(database)
    await growth.retries.run("actor", scope, (await retry_row(database))["retry_key"])
    assert len(models.calls) == len(memory.proposals) == 1
    assert (await retry_row(database))["state"] == "completed"
