"""Life observes terminal heads through the shared consumer and keeps old evidence."""
import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from test_rbi_result_atomicity import result_fixture, state
from tianshu.life.rbj_recovery_deadline import consumed_activity
from tianshu.runtime.errors import AppError


@pytest.mark.parametrize("execution", ["succeeded", "failed", "cancelled", "unknown"])
async def test_terminal_heads_are_handed_off_without_direct_domain_write(database, execution):
    value = await result_fixture(database, "activity")
    value.actions.current = replace(value.original, execution=execution)
    handoff = {"state":"waiting", "reason":"consumer_pending", "action_id":value.action.action_id,
        "receipt_id":value.original.receipt_id, "result_revision":1}
    value.actions.ensure_result_consumption = AsyncMock(return_value=handoff)
    value.worker._activity_result = AsyncMock(side_effect=AssertionError("terminal direct write"))
    value.worker._commitment_result = AsyncMock(side_effect=AssertionError("terminal direct write"))
    assert await value.worker.capability_result(value.context,value.original,
        data_generation=value.data_generation) == handoff
    value.actions.ensure_result_consumption.assert_awaited_once_with(value.context,value.action.action_id,
        data_generation=value.data_generation,expected_result_revision=1,
        expected_receipt_id=value.original.receipt_id)
    value.worker._activity_result.assert_not_awaited()
    value.actions.invoke.assert_not_awaited()


async def test_advanced_pending_head_rolls_back_control_and_hands_off_new_terminal(database):
    value = await result_fixture(database, "activity")
    value.actions.current = replace(value.original,execution="running",receipt_id="running-receipt")
    newer = replace(value.original,receipt_id="new-receipt")
    async def advance(*_args,**_kwargs):
        value.actions.current, value.actions.revision = newer, 2
        raise AppError("result_head_advanced",status=409)
    value.actions.assert_result_head_on = AsyncMock(side_effect=advance)
    handoff = {"state":"blocked", "reason":"budget_exhausted", "action_id":value.action.action_id,
        "receipt_id":"new-receipt", "result_revision":2}
    value.actions.ensure_result_consumption = AsyncMock(return_value=handoff)
    assert await value.worker.capability_result(value.context,value.actions.current,
        data_generation=value.data_generation) == handoff
    domain,_,episodes = await state(database,"activity")
    assert domain["last_result_revision"] == -1 and domain["last_receipt_id"] is None and episodes == 0
    value.actions.ensure_result_consumption.assert_awaited_once_with(value.context,value.action.action_id,
        data_generation=value.data_generation,expected_result_revision=2,expected_receipt_id="new-receipt")
    value.actions.invoke.assert_not_awaited()
    value.actions.resume.assert_not_awaited()


@pytest.mark.parametrize("next_revision,expected", [(1,"result_receipt_revision_conflict"), (0,"result_head_regressed")])
async def test_reported_advance_requires_a_strictly_new_head(database,next_revision,expected):
    value = await result_fixture(database,"activity")
    value.actions.current = replace(value.original,execution="running",receipt_id="running-receipt")
    async def changed(*_args,**_kwargs):
        value.actions.current = replace(value.original,receipt_id="different-receipt")
        value.actions.revision = next_revision
        raise AppError("result_head_advanced",status=409)
    value.actions.assert_result_head_on = AsyncMock(side_effect=changed)
    value.actions.ensure_result_consumption = AsyncMock()
    with pytest.raises(AppError,match=expected):
        await value.worker.capability_result(value.context,value.actions.current,data_generation=value.data_generation)
    value.actions.ensure_result_consumption.assert_not_awaited()
    domain,_,episodes = await state(database,"activity")
    assert domain["last_result_revision"] == -1 and episodes == 0


async def test_source_failure_is_not_mistaken_for_head_advance(database):
    value = await result_fixture(database,"activity")
    value.actions.current = replace(value.original,execution="running")
    value.actions.assert_result_head_on = AsyncMock(side_effect=AppError("source_unavailable",status=409))
    value.actions.ensure_result_consumption = AsyncMock()
    with pytest.raises(AppError,match="source_unavailable"):
        await value.worker.capability_result(value.context,value.actions.current,data_generation=value.data_generation)
    value.actions.ensure_result_consumption.assert_not_awaited()


async def test_continuously_advancing_control_head_leaves_one_original_wake(database,monkeypatch):
    from tianshu.life import rbj_result_observation
    value = await result_fixture(database,"activity")
    value.actions.current = replace(value.original,execution="running",receipt_id="running-1")
    async def advance(*_args,**_kwargs):
        value.actions.revision += 1
        value.actions.current = replace(value.actions.current,receipt_id=f"running-{value.actions.revision}")
        raise AppError("result_head_advanced",status=409)
    value.actions.assert_result_head_on = AsyncMock(side_effect=advance)
    value.actions.ensure_result_consumption = AsyncMock()
    deferred = {"state":"waiting","reason":"result_head_advancing","action_id":value.action.action_id,"job_id":99}
    defer = AsyncMock(return_value=deferred)
    monkeypatch.setattr(rbj_result_observation,"_defer_head",defer)
    assert await value.worker.capability_result(value.context,value.actions.current,
        data_generation=value.data_generation) == deferred
    assert value.actions.assert_result_head_on.await_count == rbj_result_observation.MAX_HEAD_OBSERVATIONS
    defer.assert_awaited_once_with(value.worker,value.context,value.action.action_id,value.data_generation)
    value.actions.ensure_result_consumption.assert_not_awaited()
    value.actions.invoke.assert_not_awaited()


def test_deadline_continuation_requires_exact_persisted_activity_proof():
    result = type("Result",(),{"action_id":"a","receipt_id":"r"})()
    progress = {"data_generation":"g","action_id":"a","receipt_id":"r","result_revision":3,
        "disposition":"done","proof":{"activity_id":"x","last_result_revision":3,"last_receipt_id":"r"}}
    handoff = {"state":"verified","reason":"completed","action_id":"a","receipt_id":"r",
        "result_revision":3,"consumer_progress":{"life_activity":progress}}
    assert consumed_activity(handoff,{"id":"x"},result,3,"g")
    for changed in (
        {**handoff,"state":"blocked"}, {**handoff,"reason":"superseded"},
        {**handoff,"receipt_id":"new"}, {**handoff,"consumer_progress":{}},
        {**handoff,"consumer_progress":{"life_activity":{**progress,"data_generation":"old"}}},
        {**handoff,"consumer_progress":{"life_activity":{**progress,"disposition":"superseded"}}},
        {**handoff,"consumer_progress":{"life_activity":{**progress,"proof":{}}}},
    ):
        assert not consumed_activity(changed,{"id":"x"},result,3,"g")


async def test_observer_races_normal_delivery_without_second_domain_application(database):
    value = await result_fixture(database,"activity")
    value.memory.observe_episode = AsyncMock(wraps=value.memory.observe_episode)
    value.actions.ensure_result_consumption = AsyncMock(return_value={"state":"accepted",
        "reason":"consumer_scheduled","action_id":value.action.action_id,
        "receipt_id":value.original.receipt_id,"result_revision":1})
    observed,_ = await asyncio.gather(
        value.worker.capability_result(value.context,value.original,data_generation=value.data_generation),
        value.consumer.consume(value.event()))
    domain,progress,episodes = await state(database,"activity")
    assert observed["state"] == "accepted" and progress["disposition"] == "done"
    assert domain["last_receipt_id"] == value.original.receipt_id and episodes == 1
    value.memory.observe_episode.assert_awaited_once()
    value.actions.invoke.assert_not_awaited()


@pytest.mark.parametrize("kind",["activity","commitment"])
async def test_consumer_proof_failure_rolls_back_domain_and_memory(database,kind):
    value = await result_fixture(database,kind)
    async def failed_proof(*_args,**_kwargs):
        raise RuntimeError("proof write failed")
    failed_proof.fence,failed_proof.source_refs = AsyncMock(),()
    method = value.worker._activity_result if kind == "activity" else value.worker._commitment_result
    with pytest.raises(RuntimeError,match="proof write failed"):
        await method(value.context,value.original,1,datetime.now(UTC),"content.read",
            on_complete=failed_proof,data_generation=value.data_generation)
    domain,_,episodes = await state(database,kind)
    assert domain["last_result_revision"] == -1 and domain["last_receipt_id"] is None and episodes == 0
