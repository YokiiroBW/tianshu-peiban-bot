"""Real Conversation and Life result consumers resume the original receipt."""
import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from importlib.resources import files
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import NAMESPACE_URL, uuid5

import pytest

from tianshu.conversation.codec import document
from tianshu.conversation.rendering import ExpressionRenderer
from tianshu.conversation.results import ActionResultEvents, ResultConsumer
from tianshu.conversation.rbg_result_recovery import ResultRecovery
from tianshu.conversation.service import ConversationService
from tianshu.runtime.errors import AppError
from tianshu.runtime.execution import ExecutionLane
from tianshu.runtime.migrations import Migration, migrate
from tianshu.runtime.recovery_policy import RecoveryWait
from tianshu.runtime.serialization import wire
from tianshu.types import Action, OriginKind, Receipt
from test_planner_execution import setup


async def fixture(database, *, activity=True, state="running", turn=True, intent="original-intent"):
    value = await setup(database, "{}")
    await migrate(database, (
        Migration("rbg_submission", files("tianshu.conversation").joinpath("submission_confirmation.sql").read_text()),
        Migration("rbg_results", files("tianshu.conversation").joinpath("rbg_result_consumption.sql").read_text()),
    ))
    context = replace(value.context, conversation_id="chat")
    action_id = str(uuid5(NAMESPACE_URL, intent)) if intent.startswith("commitment:") else "original-action"
    action = Action(action_id, intent, "content.read", context.actor_id, context, {}, OriginKind.ACTOR,
                    activity_id="activity-1" if activity else None)
    original = Receipt("original-receipt", action_id, "attempt-1", "succeeded", "confirmed", "not_requested",
                       result={"read_basis": "actual_source_body", "summary": "actual reading"})
    observed_at = datetime.now(UTC)

    class Actions:
        current = original
        revision = 1
        invoke = AsyncMock()
        resume = AsyncMock()

        async def get(self, requested, _context):
            assert requested == action_id
            return {"action": wire(action), "receipt": wire(self.current), "result_revision": self.revision,
                    "result_observed_at": observed_at}

        async def handler(self, _action):
            return value.worker.catalog.handler("content.read")

    class Memory:
        fail_once = False
        entered = None
        release = None

        async def observe_episode(self, _context, episode, *, connection):
            await connection.execute("INSERT INTO test_result_episodes(event_id,revision,receipt_id) VALUES (%s,%s,%s) "
                "ON CONFLICT(event_id) DO UPDATE SET revision=excluded.revision,receipt_id=excluded.receipt_id "
                "WHERE test_result_episodes.revision<=excluded.revision",
                (episode["event_id"], episode["revision"], episode["receipt_id"]))
            if self.entered:
                self.entered.set()
                await self.release.wait()
            if self.fail_once:
                self.fail_once = False
                raise RuntimeError("crash before Life transaction commit")

    actions, memory = Actions(), Memory()
    value.worker.actions = actions
    value.worker.life.memory = memory
    value.worker.life.sources.record_event = AsyncMock(return_value=())
    value.jobs.register_static_routes()
    async def run_turn(**_kwargs):
        pass
    value.jobs.register("conversation.run", run_turn, execution_lane=ExecutionLane.CONVERSATION)
    renderer = ExpressionRenderer(database, value.jobs, value.worker.life.identity, value.worker.life.sources,
                                  actions, None, None)
    conversation = ConversationService(database, None, value.jobs, value.worker.life.identity,
        value.worker.life.sources, memory, None, value.worker.life, None, None, None,
        value.worker.catalog, actions, renderer, None)
    consumer = ResultConsumer(database, value.worker.life.identity, actions, memory, None, conversation, value.worker, renderer)
    async with database.transaction() as connection:
        await connection.execute("CREATE TABLE test_result_episodes(event_id text PRIMARY KEY,revision bigint,receipt_id text)")
        if activity:
            await connection.execute("INSERT INTO life.activities(id,actor_id,title,kind,state,action_id,scope_ids,checkpoint_data,context) "
                "VALUES ('activity-1','actor','Read the page','reading',%s,%s,ARRAY['actor-scope'],%s,%s)",
                (state, action_id, document({"capability_id": "content.read", "arguments": {}}), document(context)))
        if turn:
            await connection.execute("INSERT INTO conversation.turns(turn_id,actor_id,conversation_id,producer,event_id,event_revision,"
                "context,input_parts,source_refs,original_parts,original_source_refs,state,reply_owner,reply_required,"
                "occurred_at,received_at,deadline_at,tool_dispatch_complete) "
                "VALUES ('turn','actor','chat','test','input',1,%s,'[]','[]','[]','[]','waiting_external','tianshu',true,"
                "now(),now(),now()+interval '15 minutes',true)", (document(context),))
            await connection.execute("INSERT INTO conversation.tool_links(turn_id,call_id,action_id,capability_id,state,tool_name,"
                "provider_call_id,arguments_hash) VALUES ('turn','call',%s,'content.read','running','read','call','hash')", (action_id,))
    def event(result=original, revision=1):
        return {"type": "capability.result", "version": 1, "event_id": result.receipt_id,
                "action_id": action_id, "result_revision": revision, "result_observed_at": observed_at.isoformat(),
                "context": wire(context), "source_refs": [], "receipt": wire(result)}
    return SimpleNamespace(**{**vars(value), "consumer": consumer, "event": event, "action": action,
                              "original": original, "actions": actions, "memory": memory, "context": context})


@pytest.mark.asyncio
async def test_dialogue_commit_then_life_failure_resumes_only_missing_branch(database):
    value = await fixture(database)
    value.memory.fail_once = True
    with pytest.raises(RuntimeError, match="crash before Life"):
        await value.consumer.consume(value.event())
    async with database.transaction(read_only=True) as connection:
        progress = await (await connection.execute("SELECT consumer,disposition FROM conversation.result_consumer_progress")).fetchall()
        before = {row["consumer"]: row["disposition"] for row in progress}
        activity = await (await connection.execute("SELECT state,last_result_revision FROM life.activities WHERE id='activity-1'")).fetchone()
        consumed = await (await connection.execute("SELECT count(*) AS n FROM conversation.result_consumption")).fetchone()
        episodes = await (await connection.execute("SELECT count(*) AS n FROM test_result_episodes")).fetchone()
    assert before["dialogue"] == "done" and before["life_activity"] == "pending"
    assert activity["state"] == "running" and activity["last_result_revision"] == -1
    assert consumed["n"] == episodes["n"] == 0
    await value.consumer.consume(value.event())
    await value.consumer.consume(value.event())
    async with database.transaction(read_only=True) as connection:
        activity = await (await connection.execute("SELECT state,last_result_revision FROM life.activities WHERE id='activity-1'")).fetchone()
        link = await (await connection.execute("SELECT consumed,last_receipt_id FROM conversation.tool_links WHERE action_id=%s", (value.action.action_id,))).fetchone()
        episodes = await (await connection.execute("SELECT * FROM test_result_episodes")).fetchall()
        completed = await (await connection.execute("SELECT state FROM conversation.result_deliveries WHERE receipt_id='original-receipt'")).fetchone()
        turn_jobs = await (await connection.execute("SELECT count(*) AS n FROM runtime.job_keys WHERE task='conversation.run'")).fetchone()
    assert activity == {"state": "completed", "last_result_revision": 1}
    assert link == {"consumed": True, "last_receipt_id": "original-receipt"}
    assert len(episodes) == 1 and episodes[0]["receipt_id"] == "original-receipt"
    assert completed["state"] == "completed" and turn_jobs["n"] == 1
    value.actions.invoke.assert_not_awaited()
    value.actions.resume.assert_not_awaited()


@pytest.mark.asyncio
async def test_parallel_delivery_uses_one_consumer_claim_and_one_episode(database):
    value = await fixture(database, turn=False)
    value.memory.entered, value.memory.release = asyncio.Event(), asyncio.Event()
    first = asyncio.create_task(value.consumer.consume(value.event()))
    await value.memory.entered.wait()
    with pytest.raises(RecoveryWait):
        await value.consumer.consume(value.event())
    value.memory.release.set()
    await first
    await value.consumer.consume(value.event())
    async with database.transaction(read_only=True) as connection:
        episodes = await (await connection.execute("SELECT count(*) AS n FROM test_result_episodes")).fetchone()
        consumed = await (await connection.execute("SELECT count(*) AS n FROM conversation.result_consumption")).fetchone()
    assert episodes["n"] == consumed["n"] == 1
    value.actions.invoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_changed_life_snapshot_is_reloaded_after_rollback_without_reinvocation(database):
    value = await fixture(database, turn=False)
    sources = value.worker.life.sources
    original_prepare = sources.prepare_event_writes
    calls = 0

    async def change_between_sources_and_activity_lock(connection, context, events, *, additional_refs=()):
        nonlocal calls
        calls += 1
        await original_prepare(connection, context, events, additional_refs=additional_refs)
        if calls == 1:
            async with database.transaction() as concurrent:
                await concurrent.execute("UPDATE life.activities SET revision=revision+1 WHERE id='activity-1'")

    sources.prepare_event_writes = change_between_sources_and_activity_lock
    await value.consumer.consume(value.event())
    async with database.transaction(read_only=True) as connection:
        activity = await (await connection.execute("SELECT state,last_receipt_id FROM life.activities WHERE id='activity-1'")).fetchone()
        episodes = await (await connection.execute("SELECT count(*) AS n FROM test_result_episodes")).fetchone()
    assert calls == 2 and activity == {"state": "completed", "last_receipt_id": "original-receipt"}
    assert episodes["n"] == 1
    value.actions.invoke.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["completed", "cancelled"])
async def test_terminal_life_activity_never_regresses_or_creates_fallback_memory(database, state):
    value = await fixture(database, state=state, turn=False)
    await value.consumer.consume(value.event())
    async with database.transaction(read_only=True) as connection:
        row = await (await connection.execute("SELECT state FROM life.activities WHERE id='activity-1'")).fetchone()
        progress = await (await connection.execute("SELECT consumer,disposition FROM conversation.result_consumer_progress")).fetchall()
        episodes = await (await connection.execute("SELECT count(*) AS n FROM test_result_episodes")).fetchone()
    assert row["state"] == state and episodes["n"] == 0
    assert {p["consumer"]: p["disposition"] for p in progress}["life_activity"] == "superseded"


@pytest.mark.asyncio
async def test_recurred_commitment_keeps_ownership_after_original_pointer_is_cleared(database):
    value = await fixture(database, activity=False, turn=False, intent="commitment:old-promise:7")
    await value.consumer.consume(value.event())
    async with database.transaction(read_only=True) as connection:
        row = await (await connection.execute("SELECT owner_commitment_id FROM conversation.result_deliveries")).fetchone()
        episodes = await (await connection.execute("SELECT count(*) AS n FROM test_result_episodes")).fetchone()
    assert row["owner_commitment_id"] == "old-promise" and episodes["n"] == 0


@pytest.mark.asyncio
async def test_old_result_is_superseded_and_actual_new_head_gets_native_wake(database):
    value = await fixture(database, turn=False)
    newer = replace(value.original, receipt_id="new-receipt", execution="failed", error_code="failed")
    value.actions.current, value.actions.revision = newer, 2
    original_event = value.event()
    await value.consumer.consume(original_event)
    async with database.transaction(read_only=True) as connection:
        row = await (await connection.execute("SELECT state FROM conversation.result_deliveries WHERE receipt_id='original-receipt'")).fetchone()
        queued = await (await connection.execute("SELECT j.args FROM runtime.job_keys k JOIN procrastinate_jobs j ON j.id=k.job_id "
            "WHERE k.task='conversation.result' AND j.args->'event'->>'event_id'='new-receipt'")).fetchone()
    assert row["state"] == "superseded" and original_event["receipt"]["receipt_id"] == "original-receipt"
    assert queued["args"]["event"]["receipt"] == wire(newer)
    value.actions.invoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_completed_legacy_receipt_collision_is_reported_without_replay(database):
    value = await fixture(database, turn=False)
    async with database.transaction() as connection:
        await connection.execute("INSERT INTO conversation.result_consumption(receipt_id,action_id) VALUES (%s,%s)",
                                 (value.original.receipt_id, value.original.action_id))
    value.actions.current = replace(value.original, execution="failed", error_code="changed")
    value.actions.revision = 2
    with pytest.raises(AppError, match="result_receipt_revision_conflict"):
        await value.consumer.consume(value.event())
    async with database.transaction(read_only=True) as connection:
        assert not await (await connection.execute("SELECT 1 FROM test_result_episodes")).fetchone()
    value.actions.invoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_withdrawn_sources_invalidate_unfinished_result_without_side_effects(database):
    value = await fixture(database, turn=False)
    value.consumer.identity.check = AsyncMock(side_effect=AppError("source_unavailable", status=403))
    await value.consumer.consume(value.event())
    async with database.transaction(read_only=True) as connection:
        row = await (await connection.execute("SELECT state,reason FROM conversation.result_deliveries")).fetchone()
        episodes = await (await connection.execute("SELECT count(*) AS n FROM test_result_episodes")).fetchone()
    assert row == {"state": "invalidated", "reason": "source_unavailable"} and episodes["n"] == 0
    value.actions.invoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_unconfirmed_remote_rollback_stays_blocked_and_is_not_requeued(database):
    value = await fixture(database, turn=False)
    value.consumer.identity.sources = value.worker.life.sources
    async with database.transaction() as connection:
        await ActionResultEvents(value.jobs).publish(connection, value.event())
        saved = await (await connection.execute("SELECT original_job_id,data_generation FROM conversation.result_deliveries")).fetchone()
        await connection.execute("UPDATE procrastinate_jobs SET status='failed' WHERE id=%s", (saved["original_job_id"],))
    error = RecoveryWait("render_wait", datetime.now(UTC)+timedelta(seconds=5), dependency="render")
    error.remote_rollback_confirmed = False
    value.consumer.renderer.on_action_result = AsyncMock(side_effect=error)
    with pytest.raises(RecoveryWait):
        await value.consumer.consume(value.event())
    recovery = ResultRecovery(database, value.jobs, value.consumer.identity)
    candidates = await recovery.candidates()
    assert candidates[0]["eligible"] is False and candidates[0]["reason"] == "result_consumer_blocked"
    with pytest.raises(AppError, match="result_consumer_blocked"):
        await recovery.recover_result(saved["original_job_id"], data_generation=saved["data_generation"],
            action_id=value.action.action_id, receipt_id=value.original.receipt_id, result_revision=1)
    async with database.transaction(read_only=True) as connection:
        assert not await (await connection.execute("SELECT 1 FROM conversation.result_consumption")).fetchone()
    value.actions.invoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_native_result_job_requeues_exact_original_event_without_calling_action(database):
    value = await fixture(database, turn=False)
    value.consumer.identity.sources = value.worker.life.sources
    producer = ActionResultEvents(value.jobs)
    original = value.event()
    async with database.transaction() as connection:
        await producer.publish(connection, original)
        saved = await (await connection.execute("SELECT original_job_id,data_generation FROM conversation.result_deliveries "
            "WHERE receipt_id='original-receipt'")).fetchone()
        await connection.execute("UPDATE procrastinate_jobs SET status='failed' WHERE id=%s", (saved["original_job_id"],))
    recovery = ResultRecovery(database, value.jobs, value.consumer.identity)
    candidates = await recovery.candidates()
    assert any(item["target"]["id"] == str(saved["original_job_id"]) for item in candidates)
    queued = await recovery.recover_result(saved["original_job_id"], data_generation=saved["data_generation"],
        action_id=value.action.action_id, receipt_id="original-receipt", result_revision=1)
    async with database.transaction(read_only=True) as connection:
        row = await (await connection.execute("SELECT j.args,k.data_generation FROM procrastinate_jobs j "
            "JOIN runtime.job_keys k ON k.job_id=j.id WHERE j.id=%s", (queued["job_id"],))).fetchone()
    assert row["args"]["event"] == original and row["data_generation"] == saved["data_generation"]
    value.actions.invoke.assert_not_awaited()
    value.actions.resume.assert_not_awaited()
