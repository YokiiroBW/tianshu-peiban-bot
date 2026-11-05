"""Only an observed, locally finished pure computation may yield a successor."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
import json
from unittest.mock import AsyncMock

import pytest

from tianshu.life.writing import WritingService
from tianshu.runtime.errors import AppError
from tianshu.runtime.serialization import wire
from tianshu.types import SourceRef
from historical_support import planner_setup
from test_planner_execution import mark
from test_rb_diary_experience import Broker, begin, diary_state, rb_fixture


async def setup_generation(database, kind):
    value = await rb_fixture(database)
    if kind == "diary":
        row = await begin(value, database)
        async def generate(attempt):
            return await value.diaries.generate(row["id"], attempt)
        async def current():
            return await diary_state(database)
        return value, row["generation_id"], generate, current

    value.life.sources.record_event = AsyncMock(return_value=(SourceRef("chapter-save", 1),))
    writing = WritingService(value.life, value.models, value.diaries.assembler)
    writing.bind_observability(recovery_policy=value.diaries.recovery.policy, statistics=None)
    async with database.transaction() as connection:
        await connection.execute("INSERT INTO life.works(id,actor_id,title,scope_ids) VALUES('work','actor','Story',ARRAY['actor-scope'])")
        await connection.execute("INSERT INTO life.chapters(id,work_id,position,title) VALUES('chapter','work',1,'First')")
    command = await writing.request_generation(value.context, {"id": "chapter", "expected_revision": 1,
        "client_request_id": "pure-unknown-chapter"})
    async def generate(attempt):
        return await writing.generate("chapter", attempt)
    async def current():
        async with database.transaction(read_only=True) as connection:
            return await (await connection.execute("SELECT * FROM life.chapters WHERE id='chapter'")).fetchone()
    return value, command.attempt_id, generate, current


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["diary", "chapter"])
@pytest.mark.parametrize("observation", ["absent", "unavailable", "not_found", "not_finished", "wrong_generation", "explicit_cancel"])
async def test_unavailable_status_does_not_create_unknown_or_a_successor(database, kind, observation):
    value, attempt, generate, current = await setup_generation(database, kind)
    await generate(attempt)
    assert len(value.models.requests) == 1
    # The previous submission is real, but a missing status is not evidence of
    # an unknown terminal observation and must not authorize another attempt.
    if observation in {"not_finished", "wrong_generation", "explicit_cancel"}:
        value.models.receipts[attempt] = replace(value.models.receipts[attempt], status="unknown", dispatch="unknown",
            error_code="timeout_unknown", finished_at=None if observation == "not_finished" else datetime.now(UTC),
            data_generation="old-generation" if observation == "wrong_generation" else value.models.requests[attempt].data_generation,
            cancellation_reason="explicit_cancel" if observation == "explicit_cancel" else None)
    else:
        error = None if observation == "absent" else AppError("broker_unavailable" if observation == "unavailable" else "not_found", status=404)
        value.models.status = AsyncMock(return_value=None, side_effect=error)
    await generate(attempt)
    async with database.transaction(read_only=True) as connection:
        ledger = await (await connection.execute(
            "SELECT status FROM runtime.recovery_attempts WHERE attempt_id=%s", (attempt,))).fetchone()
    assert ledger["status"] == "reserved"
    assert (await current())["generation_id"] == attempt
    assert len(value.models.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["diary", "chapter"])
async def test_interrupted_consumer_unknown_remains_retryable(database, kind):
    value, attempt, generate, current = await setup_generation(database, kind)
    await generate(attempt)
    value.models.receipts[attempt] = replace(value.models.receipts[attempt], status="unknown", dispatch="unknown",
        error_code="consumer_interrupted", cancellation_reason="consumer_interrupted", finished_at=datetime.now(UTC),
        data_generation=value.models.requests[attempt].data_generation)
    await generate(attempt)
    row = await current()
    assert row["generation_id"] == attempt and row["state"] == "unknown" and row["generation_retry_due_at"] is not None
    assert list(value.models.requests) == [attempt]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["diary", "chapter"])
async def test_finished_unknown_uses_delayed_distinct_attempt_and_applies_once(database, kind):
    value, attempt, generate, current = await setup_generation(database, kind)
    await generate(attempt)
    original = value.models.requests[attempt]
    value.models.receipts[attempt] = replace(value.models.receipts[attempt], status="unknown", dispatch="unknown",
        error_code="timeout_unknown", finished_at=datetime.now(UTC),data_generation=original.data_generation)
    await generate(attempt)
    first = await current()
    assert first["generation_id"] == attempt and first["generation_retry_due_at"] is not None
    await generate(attempt)
    assert len(value.models.requests) == 1

    past = datetime.now(UTC)-timedelta(seconds=1)
    table = "life.diaries" if kind == "diary" else "life.chapters"
    async with database.transaction() as connection:
        original_row = await (await connection.execute(
            "SELECT lineage_id,status FROM runtime.recovery_attempts WHERE attempt_id=%s", (attempt,))).fetchone()
        assert original_row["status"] == "unknown"
        await connection.execute("UPDATE runtime.recovery_lineages SET due_at=%s WHERE lineage_id=%s",
            (past,original_row["lineage_id"]))
        await connection.execute("UPDATE runtime.recovery_dependencies SET cooldown_until=%s", (past,))
        await connection.execute(f"UPDATE {table} SET generation_retry_due_at=%s WHERE id=%s", (past,first["id"]))
    original_status = value.models.status
    observations = 0
    async def status_disappears(identifier, *, phase="observe"):
        nonlocal observations
        observations += 1
        return None if observations >= 3 else await original_status(identifier,phase=phase)
    value.models.status = status_disappears
    await generate(attempt)
    assert (await current())["generation_id"] == attempt and len(value.models.requests) == 1
    value.models.status = original_status
    await generate(attempt)
    child = (await current())["generation_id"]
    assert child != attempt and len(value.models.requests) == 1
    await generate(child)
    assert len(value.models.requests) == 2 and value.models.requests[child].attempt_id == child
    assert value.models.requests[child].messages == original.messages
    assert value.models.requests[child].data_generation == original.data_generation
    value.models.complete(child, "本次独立尝试完成的正文。")
    await generate(child)
    value.models.complete(attempt, "原未知尝试迟到的正文。")
    await generate(attempt)
    saved = await current()
    assert saved["current_revision"] == 1 and saved["generation_id"] == child
    assert len(value.models.requests) == 2
    async with database.transaction(read_only=True) as connection:
        attempts = await (await connection.execute(
            "SELECT attempt_id,status FROM runtime.recovery_attempts WHERE lineage_id=%s ORDER BY attempt_number",
            (original_row["lineage_id"],))).fetchall()
    assert [entry["attempt_id"] for entry in attempts] == [attempt, child]
    assert [entry["status"] for entry in attempts] == ["unknown", "succeeded"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["diary", "chapter"])
async def test_finished_unknown_does_not_retry_after_its_material_changes(database, kind):
    value, attempt, generate, current = await setup_generation(database, kind)
    await generate(attempt)
    value.models.receipts[attempt] = replace(value.models.receipts[attempt], status="unknown", dispatch="unknown",
        error_code="timeout_unknown", finished_at=datetime.now(UTC),
        data_generation=value.models.requests[attempt].data_generation)
    await generate(attempt)
    row = await current()
    past = datetime.now(UTC)-timedelta(seconds=1)
    table = "life.diaries" if kind == "diary" else "life.chapters"
    async with database.transaction() as connection:
        await connection.execute("UPDATE runtime.recovery_lineages SET due_at=%s", (past,))
        await connection.execute("UPDATE runtime.recovery_dependencies SET cooldown_until=%s", (past,))
        await connection.execute(f"UPDATE {table} SET generation_retry_due_at=%s WHERE id=%s", (past,row["id"]))
        if kind == "chapter":
            await connection.execute("UPDATE life.works SET revision=revision+1 WHERE id='work'")
    if kind == "diary":
        value.memory.episodes = (replace(value.episode, revision=2),)
    await generate(attempt)
    after = await current()
    assert after["generation_id"] == attempt and after["state"] == "failed"
    assert after["reason"] == ("diary_episode_revised" if kind == "diary" else "writing_material_changed")
    assert list(value.models.requests) == [attempt]
    async with database.transaction(read_only=True) as connection:
        original = await (await connection.execute(
            "SELECT status FROM runtime.recovery_attempts WHERE attempt_id=%s", (attempt,))).fetchone()
    assert original["status"] == "unknown"


@pytest.mark.asyncio
async def test_planning_finished_unknown_has_one_delayed_successor_and_ignores_late_original(database):
    output = json.dumps({"activity": None, "arrangements": [], "goal_changes": [], "cancel_activity_ids": [],
        "reason": "take a break", "free_time_reason": "free time"})
    value = await planner_setup(database, output)
    broker = Broker(value.models.generation_profile)
    value.worker.models = value.worker.generations.models = broker
    async def plan():
        current = await mark(database)
        await value.worker.plan("actor", current["revision"], wire(value.context), value.data_generation)

    await plan()
    first = await mark(database)
    original = next(iter(broker.requests))
    broker.receipts[original] = replace(broker.receipts[original], status="unknown", dispatch="unknown",
        error_code="timeout_unknown", finished_at=datetime.now(UTC),data_generation=value.data_generation)
    await plan()
    waiting = await mark(database)
    assert waiting["generation_key"] == first["generation_key"] and waiting["repair_due_at"] is not None
    await plan()
    assert list(broker.requests) == [original]
    past = datetime.now(UTC)-timedelta(seconds=1)
    async with database.transaction() as connection:
        attempt = await (await connection.execute(
            "SELECT lineage_id,status FROM runtime.recovery_attempts WHERE attempt_id=%s", (original,))).fetchone()
        assert attempt["status"] == "unknown"
        await connection.execute("UPDATE runtime.recovery_lineages SET due_at=%s WHERE lineage_id=%s",
            (past,attempt["lineage_id"]))
        await connection.execute("UPDATE runtime.recovery_dependencies SET cooldown_until=%s", (past,))
        await connection.execute("UPDATE life.plan_watermarks SET repair_due_at=%s WHERE actor_id='actor'", (past,))
    await plan()
    child = next(key for key in broker.requests if key != original)
    assert len(broker.requests) == 2
    broker.complete(child, output)
    await plan()
    saved = await mark(database)
    assert saved["generation_consumed"] and saved["generation_key"] != first["generation_key"]
    broker.complete(original, output)
    await plan()
    assert (await mark(database))["generation_key"] == saved["generation_key"]
    async with database.transaction(read_only=True) as connection:
        rows = await (await connection.execute(
            "SELECT state FROM life.plan_generations ORDER BY created_at")).fetchall()
        attempts = await (await connection.execute(
            "SELECT attempt_id,status FROM runtime.recovery_attempts WHERE lineage_id=%s ORDER BY attempt_number",
            (attempt["lineage_id"],))).fetchall()
    assert [row["state"] for row in rows].count("applied") == 1
    assert [row["attempt_id"] for row in attempts] == [original, child]
    assert [row["status"] for row in attempts] == ["unknown", "succeeded"]


@pytest.mark.asyncio
async def test_planning_missing_observation_never_authorizes_a_successor(database):
    value = await planner_setup(database, "{}")
    broker = Broker(value.models.generation_profile)
    value.worker.models = value.worker.generations.models = broker
    await value.worker.plan("actor", 1, wire(value.context), value.data_generation)
    attempt = next(iter(broker.requests))
    broker.status = AsyncMock(side_effect=AppError("not_found", status=404))
    await value.worker.plan("actor", 1, wire(value.context), value.data_generation)
    assert (await mark(database))["reason"] == "planner_observation_unavailable"
    async with database.transaction(read_only=True) as connection:
        original = await (await connection.execute(
            "SELECT status FROM runtime.recovery_attempts WHERE attempt_id=%s", (attempt,))).fetchone()
    assert original["status"] == "reserved" and list(broker.requests) == [attempt]


@pytest.mark.asyncio
async def test_exhausted_unknown_still_accepts_its_late_original_result(database):
    output = json.dumps({"activity": None, "arrangements": [], "goal_changes": [], "cancel_activity_ids": [],
        "reason": "take a break", "free_time_reason": "free time"})
    value = await planner_setup(database, output)
    broker = Broker(value.models.generation_profile)
    value.worker.models = value.worker.generations.models = broker
    await value.worker.plan("actor", 1, wire(value.context), value.data_generation)
    attempt = next(iter(broker.requests))
    async with database.transaction() as connection:
        await connection.execute("UPDATE runtime.recovery_lineages SET max_attempts=1")
    broker.receipts[attempt] = replace(broker.receipts[attempt], status="unknown", dispatch="unknown",
        error_code="timeout_unknown", finished_at=datetime.now(UTC),data_generation=value.data_generation)
    await value.worker.plan("actor", 1, wire(value.context), value.data_generation)
    exhausted = await mark(database)
    assert exhausted["reason"] == "planner_attempts_exhausted" and exhausted["repair_due_at"] is None
    assert not exhausted["generation_consumed"] and list(broker.requests) == [attempt]
    broker.complete(attempt, output)
    await value.worker.plan("actor", 1, wire(value.context), value.data_generation)
    assert (await mark(database))["generation_consumed"] and list(broker.requests) == [attempt]
