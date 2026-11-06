"""Real ResultConsumer and Life callbacks commit the same receipt atomically."""
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from psycopg.errors import QueryCanceled

from test_rbg_result_consumers import fixture
from tianshu.conversation.codec import document
from tianshu.runtime.errors import AppError
from tianshu.types import SourceRef


async def result_fixture(database, kind):
    value = await fixture(database,activity=kind == "activity",turn=False)
    if kind == "commitment":
        async with database.transaction() as connection:
            await connection.execute("INSERT INTO life.commitments(id,actor_id,title,timezone,state,action_id,"
                "scope_ids,context) VALUES ('promise','actor','Synthetic promise','UTC','waiting_result',%s,"
                "ARRAY['actor-scope'],%s)",(value.action.action_id,document(value.context)))
    return value


async def state(database, kind):
    table,identifier = ("life.activities","activity-1") if kind == "activity" else ("life.commitments","promise")
    async with database.transaction(read_only=True) as connection:
        domain = await (await connection.execute(f"SELECT state,last_result_revision,last_receipt_id FROM {table} WHERE id=%s",
            (identifier,))).fetchone()
        progress = await (await connection.execute("SELECT disposition,proof FROM conversation.result_consumer_progress "
            "WHERE receipt_id='original-receipt' AND consumer=%s",("life_"+kind,))).fetchone()
        episodes = await (await connection.execute("SELECT count(*) AS n FROM test_result_episodes")).fetchone()
    return domain,progress,episodes["n"]


@pytest.mark.parametrize("kind",["activity","commitment"])
async def test_query_cancel_rolls_back_domain_and_consumer_proof_then_reuses_receipt(database,kind):
    value = await result_fixture(database,kind)
    original = value.memory.observe_episode
    attempts = 0
    async def cancel_during_life_write(context, episode, *, connection):
        nonlocal attempts
        attempts += 1
        await original(context,episode,connection=connection)
        if attempts == 1:
            raise QueryCanceled("synthetic cancellation inside Life result transaction")
    value.memory.observe_episode = cancel_during_life_write
    with pytest.raises(QueryCanceled):
        await value.consumer.consume(value.event())
    domain,progress,episodes = await state(database,kind)
    assert domain["last_result_revision"] == -1 and domain["last_receipt_id"] is None
    assert progress["disposition"] == "pending" and not progress["proof"] and episodes == 0
    await value.consumer.consume(value.event())
    await value.consumer.consume(value.event())
    domain,progress,episodes = await state(database,kind)
    assert domain["last_result_revision"] == 1 and domain["last_receipt_id"] == "original-receipt"
    assert progress["disposition"] == "done" and progress["proof"]["last_receipt_id"] == "original-receipt"
    assert episodes == 1 and attempts == 2
    value.actions.invoke.assert_not_awaited()
    value.actions.resume.assert_not_awaited()


@pytest.mark.parametrize("kind",["activity","commitment"])
async def test_lost_ack_after_business_commit_observes_proof_without_reapplying(database,kind):
    value = await result_fixture(database,kind)
    value.memory.observe_episode = AsyncMock(wraps=value.memory.observe_episode)
    name = "_activity_result" if kind == "activity" else "_commitment_result"
    original = getattr(value.worker,name)
    calls = 0
    async def lost_ack(*args,**kwargs):
        nonlocal calls
        calls += 1
        result = await original(*args,**kwargs)
        if calls == 1:
            raise OSError("synthetic acknowledgement loss after Life committed")
        return result
    setattr(value.worker,name,lost_ack)
    # The callback has already committed its proof in the Life transaction;
    # the consumer can resolve the lost acknowledgement from that proof.
    await value.consumer.consume(value.event())
    domain,progress,episodes = await state(database,kind)
    identifier = "activity-1" if kind == "activity" else "promise"
    assert domain == {"state":"completed","last_result_revision":1,"last_receipt_id":"original-receipt"}
    assert progress == {"disposition":"done","proof":{kind+"_id":identifier,
        "last_result_revision":1,"last_receipt_id":"original-receipt"}}
    assert episodes == 1 and calls == 1
    value.memory.observe_episode.assert_awaited_once()
    async with database.transaction(read_only=True) as connection:
        episode = await (await connection.execute("SELECT event_id,revision,receipt_id FROM test_result_episodes")).fetchone()
    assert episode == {"event_id":"action:"+value.action.action_id,"revision":1,"receipt_id":"original-receipt"}
    await value.consumer.consume(value.event())
    repeated_domain,repeated_progress,episodes = await state(database,kind)
    assert repeated_domain == domain and repeated_progress == progress and episodes == 1 and calls == 1
    value.memory.observe_episode.assert_awaited_once()
    value.actions.invoke.assert_not_awaited()
    value.actions.resume.assert_not_awaited()


@pytest.mark.parametrize("kind",["activity","commitment"])
async def test_advanced_head_aborts_after_prelocking_callback_sources(database,kind):
    value = await result_fixture(database,kind)
    extra = SourceRef("callback-only-source",1,{})
    prepared = []
    original_prepare = value.worker.life.sources.prepare_event_writes
    async def inspect_prepare(connection, context, events, *, additional_refs=()):
        prepared.extend(additional_refs)
        return await original_prepare(connection,context,events,additional_refs=additional_refs)
    value.worker.life.sources.prepare_event_writes = inspect_prepare
    async def complete(*_args,**_kwargs):
        pytest.fail("an advanced Action head must not complete the old receipt")
    async def fence(_connection):
        assert extra in prepared
        raise AppError("result_head_advanced",status=409)
    complete.fence,complete.source_refs = fence,(extra,)
    method = value.worker._activity_result if kind == "activity" else value.worker._commitment_result
    with pytest.raises(AppError,match="result_head_advanced"):
        await method(value.context,value.original,1,datetime.now(UTC),"content.read",on_complete=complete)
    domain,_,episodes = await state(database,kind)
    assert domain["last_result_revision"] == -1 and domain["last_receipt_id"] is None and episodes == 0
    value.actions.invoke.assert_not_awaited()


async def test_terminal_observation_hands_off_without_writing_life(database):
    value = await result_fixture(database,"activity")
    value.actions.assert_result_head_on = AsyncMock()
    blocked = {"state":"blocked","reason":"consumer_budget_exhausted","action_id":value.action.action_id}
    value.actions.ensure_result_consumption = AsyncMock(return_value=blocked)
    assert await value.worker.capability_result(value.context,value.original,data_generation=value.data_generation) == blocked
    domain,_,episodes = await state(database,"activity")
    assert domain["last_result_revision"] == -1 and domain["last_receipt_id"] is None and episodes == 0
    value.actions.assert_result_head_on.assert_not_awaited()
    value.actions.ensure_result_consumption.assert_awaited_once_with(value.context,value.action.action_id,
        data_generation=value.data_generation,expected_result_revision=1,expected_receipt_id="original-receipt")
    value.actions.invoke.assert_not_awaited()


@pytest.mark.parametrize("kind",["activity","commitment"])
async def test_terminal_same_revision_with_different_receipt_is_not_marked_done(database,kind):
    value = await result_fixture(database,kind)
    table,identifier = ("life.activities","activity-1") if kind == "activity" else ("life.commitments","promise")
    async with database.transaction() as connection:
        await connection.execute(f"UPDATE {table} SET state='completed',last_result_revision=1,last_receipt_id='other' WHERE id=%s",
            (identifier,))
    async def complete(*_args,**_kwargs):
        pytest.fail("conflicting evidence must never be completed")
    complete.fence = AsyncMock()
    complete.source_refs = ()
    method = value.worker._activity_result if kind == "activity" else value.worker._commitment_result
    with pytest.raises(AppError,match="local_result_application_unconfirmed"):
        await method(value.context,value.original,1,datetime.now(UTC),"content.read",
            on_complete=complete,expected_owner_id=identifier,data_generation=value.data_generation)
    domain,_,episodes = await state(database,kind)
    assert domain["last_receipt_id"] == "other" and episodes == 0
