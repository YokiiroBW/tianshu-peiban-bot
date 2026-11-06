"""Native result wake identities and independent durable consumer progress."""

import asyncio
from datetime import UTC, datetime, timedelta
from dataclasses import replace
from importlib.resources import files
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from psycopg import OperationalError
from psycopg.errors import LockNotAvailable, QueryCanceled
from psycopg.types.json import Jsonb

from tianshu.conversation.results import ActionResultEvents, ResultConsumer
from tianshu.conversation.expression import ExpressionService
from tianshu.conversation.rendering import ExpressionRenderer
from tianshu.conversation.service import ConversationService
from tianshu.conversation.types import ExpressionPlan
from tianshu.capabilities.actions import Actions as ActionService, encoded
from tianshu.capabilities.catalog import Catalog
from tianshu.capabilities.contracts import canonical
from tianshu.conversation import rbg_result_ledger as ledger
from tianshu.conversation.codec import receipt as decode_receipt
from tianshu.conversation.rbg_result_recovery import ResultRecovery
from tianshu.conversation.rbi_result_wake import ensure_on
from tianshu.runtime.errors import AppError
from tianshu.runtime.jobs import JobQueue, JobSpec
from tianshu.runtime.migrations import Migration, migrate
from tianshu.runtime.recovery_policy import RecoveryWait
from tianshu.runtime.serialization import wire
from tianshu.types import Action, Attempt, OriginKind, Receipt, ServiceClass, SourceRef, TrustedContext


async def fixture(database):
    await migrate(database, (
        Migration("rbi_execution_budget", files("tianshu.runtime").joinpath("rbi_execution_budget.sql").read_text()),
        Migration("rbi_test_life", files("tianshu.life").joinpath("schema.sql").read_text()),
        Migration("rbi_test_conversation", files("tianshu.conversation").joinpath("schema.sql").read_text()),
        Migration("rbi_test_capabilities", files("tianshu.capabilities").joinpath("schema.sql").read_text()),
        Migration("0041_conversation_submission_confirmation", files("tianshu.conversation").joinpath("submission_confirmation.sql").read_text()),
        Migration("rbg_result_consumption", files("tianshu.conversation").joinpath("rbg_result_consumption.sql").read_text()),
        Migration("rbi_result_reconcile", files("tianshu.conversation").joinpath("rbi_result_reconcile.sql").read_text()),
    ))
    jobs = JobQueue(database.config.dsn, database=database)
    jobs.register_static_routes()
    context = TrustedContext("principal", "actor", None, "chat", ("scope",), ("scope",), 1, OriginKind.ACTOR)
    action = Action("action", "intent", "content.read", "actor", context, {}, OriginKind.ACTOR,
                    activity_id="activity")
    result = Receipt("receipt", "action", "attempt", "succeeded", "confirmed", "not_requested",
                     result={"read_basis": "actual_source_body"})
    observed = datetime.now(UTC)
    event = {"type": "capability.result", "version": 1, "event_id": result.receipt_id,
             "action_id": action.action_id, "result_revision": 1, "result_observed_at": observed.isoformat(),
             "context": wire(context), "source_refs": [], "receipt": wire(result)}

    class Identity:
        revoked = False

        async def check(self, context, refs=()):
            if self.revoked:
                raise AppError("source_unavailable", status=403)

        async def validate_persistence(self, connection, context, refs):
            return not self.revoked

    identity = Identity()
    identity.sources = identity

    class Actions:
        invoke = AsyncMock()
        resume = AsyncMock()
        current = result
        revision = 1

        async def get(self, action_id, context):
            assert action_id == action.action_id
            return {"action": wire(action), "receipt": wire(self.current),
                    "result_revision": self.revision, "result_observed_at": observed}

        async def handler(self, action):
            return SimpleNamespace(definition=SimpleNamespace(permissions=frozenset(), model_tool=None,
                                                              public_command=None))

        async def assert_result_head_on(self, connection, context, *, action_id, expected_receipt,
                                        result_revision, data_generation, source_refs=()):
            await jobs.assert_generation_on(connection, data_generation)
            if self.revision > result_revision:
                raise AppError("result_head_advanced", status=409)
            if self.revision != result_revision or wire(self.current) != expected_receipt:
                raise AppError("result_receipt_revision_conflict", status=409)
            return {"action_id": action_id, "receipt_id": self.current.receipt_id,
                    "result_revision": self.revision}

    class Life:
        fail_once = False
        lose_ack_once = False
        wait_until = None
        activity_calls = 0

        async def _activity_result(self, context, actual, revision, observed_at, capability_id, *, on_complete):
            self.activity_calls += 1
            async with database.transaction() as connection:
                await on_complete.begin(connection)
                assert await identity.validate_persistence(connection, context, on_complete.source_refs)
                await on_complete.fence(connection)
                await connection.execute("INSERT INTO test_rbi_effects(receipt_id) VALUES (%s) ON CONFLICT DO NOTHING",
                                         (actual.receipt_id,))
                if self.fail_once:
                    self.fail_once = False
                    raise QueryCanceled("fixture query canceled")
                if self.wait_until:
                    raise RecoveryWait("activity_dependency_wait", self.wait_until, dependency="activity")
                await on_complete(connection, proof={"receipt_id": actual.receipt_id})
            if self.lose_ack_once:
                self.lose_ack_once = False
                raise OperationalError("fixture acknowledgement lost")

        async def _commitment_result(self, context, actual, revision, observed_at, capability_id, *, on_complete):
            async with database.transaction() as connection:
                await on_complete.begin(connection)
                await on_complete.fence(connection)
                await on_complete(connection, "not_applicable", reason="no_commitment")

        async def detached_result(self, context, actual, action, *, on_complete):
            async with database.transaction() as connection:
                await on_complete.begin(connection)
                await on_complete.fence(connection)
                await on_complete(connection, "not_applicable", reason="no_contact")

    class Conversation:
        def __init__(self):
            self.jobs, self.sources = jobs, identity

        async def on_result(self, context, actual, *, result_revision, result_fence=None):
            return None

    class Renderer:
        async def on_action_result(self, context, actual, revision, *, result_fence=None):
            return False

    async with database.transaction() as connection:
        await connection.execute("CREATE TABLE test_rbi_effects(receipt_id text PRIMARY KEY)")
        await connection.execute(
            "INSERT INTO life.activities(id,actor_id,title,kind,state,action_id,scope_ids,context) "
            "VALUES ('activity','actor','read','reading','running','action',ARRAY['scope'],%s)",
            (Jsonb(wire(context)),),
        )
    actions, life, renderer = Actions(), Life(), Renderer()
    consumer = ResultConsumer(database, identity, actions, SimpleNamespace(observe_episode=AsyncMock()),
                              None, Conversation(), life, renderer)
    recovery = ResultRecovery(database, jobs, identity, actions=actions)
    return SimpleNamespace(jobs=jobs, identity=identity, actions=actions, life=life, renderer=renderer,
                           consumer=consumer, recovery=recovery, event=event, action=action)


async def publish(value, database):
    async with database.transaction() as connection:
        await ActionResultEvents(value.jobs).publish(connection, value.event)
        return await (await connection.execute(
            "SELECT original_job_id,data_generation FROM conversation.result_deliveries WHERE receipt_id='receipt'"
        )).fetchone()


async def fail_native_job(connection, job_id):
    # Exercise the real PostgreSQL event triggers, including a started event;
    # changing todo straight to failed is not a provable execution history.
    await connection.execute("UPDATE procrastinate_jobs SET status='doing' WHERE id=%s", (job_id,))
    await connection.execute("UPDATE procrastinate_jobs SET status='failed',attempts=attempts+1 WHERE id=%s", (job_id,))


@pytest.mark.asyncio
async def test_action_head_fence_locks_the_real_row_and_checks_the_receipt(database):
    value = await fixture(database)
    ref = SourceRef("source", 1, {})
    action = replace(value.action, source_refs=(ref,))
    actual = decode_receipt(value.event["receipt"])
    attempt = Attempt("attempt", "action")
    owner = ActionService(database, value.jobs, Catalog(), value.identity)
    owner.sources = value.identity
    async with database.transaction() as connection:
        generation = await value.jobs.current_generation_on(connection)
        await connection.execute(
            "INSERT INTO capabilities.actions(id,intent,semantics,principal_id,kind,created_at,document,"
            "action,attempt,receipt,result_revision,result_observed_at) "
            "VALUES(%s,%s,%s,%s,%s,0,%s,%s::jsonb,%s::jsonb,%s::jsonb,1,now())",
            (action.action_id, action.intent_key, "fixture", action.context.principal_id,
             action.capability_id, canonical({"data_generation": generation}),
             encoded(action), encoded(attempt), encoded(actual)))
    async with database.transaction() as connection:
        await owner.assert_result_head_on(connection, action.context, action_id="action",
            expected_receipt=value.event["receipt"], result_revision=1,
            data_generation=generation, source_refs=(ref,))
        with pytest.raises(LockNotAvailable):
            async with database.transaction() as competing:
                await competing.execute("SET LOCAL lock_timeout='100ms'")
                await competing.execute("UPDATE capabilities.actions SET result_revision=2 WHERE id='action'")
    with pytest.raises(AppError, match="result_source_set_changed"):
        async with database.transaction() as connection:
            await owner.assert_result_head_on(connection, action.context, action_id="action",
                expected_receipt=value.event["receipt"], result_revision=1,
                data_generation=generation, source_refs=())
    async with database.transaction() as connection:
        await connection.execute("UPDATE capabilities.actions SET result_revision=2 WHERE id='action'")
    with pytest.raises(AppError, match="result_head_advanced"):
        async with database.transaction() as connection:
            await owner.assert_result_head_on(connection, action.context, action_id="action",
                expected_receipt=value.event["receipt"], result_revision=1,
                data_generation=generation, source_refs=(ref,))


@pytest.mark.asyncio
async def test_advanced_action_head_cannot_update_render_tool_or_channel_projections(database):
    value = await fixture(database)
    action, context = value.action, value.action.context
    actual = decode_receipt(value.event["receipt"])
    owner = ActionService(database, value.jobs, Catalog(), value.identity)
    owner.sources = value.identity
    async with database.transaction() as connection:
        generation = await value.jobs.current_generation_on(connection)
        await connection.execute(
            "INSERT INTO capabilities.actions(id,intent,semantics,principal_id,kind,created_at,document,"
            "action,attempt,receipt,result_revision,result_observed_at) "
            "VALUES(%s,%s,%s,%s,%s,0,%s,%s::jsonb,%s::jsonb,%s::jsonb,2,now())",
            (action.action_id, action.intent_key, "fixture", context.principal_id, action.capability_id,
             canonical({"data_generation": generation}), encoded(action),
             encoded(Attempt("attempt", "action")), encoded(actual)))
        await connection.execute(
            "INSERT INTO conversation.expression_plans(expression_id,actor_id,conversation_id,"
            "fingerprint,context,plan,source_refs,state,steps) "
            "VALUES('render','actor','chat','fixture',%s,'{}','[]','waiting_render',%s)",
            (Jsonb(wire(context)), Jsonb([{"action_id": "action", "result_revision": 0}])))
        await connection.execute(
            "INSERT INTO conversation.turns(turn_id,actor_id,conversation_id,producer,event_id,event_revision,"
            "context,input_parts,source_refs,original_parts,original_source_refs,state,reply_owner,reply_required,"
            "occurred_at,received_at,deadline_at) VALUES ('turn','actor','chat','fixture','turn',1,%s,"
            "'[]','[]','[]','[]','running','tianshu',true,now(),now(),now()+interval '15 minutes')",
            (Jsonb(wire(context)),))
        await connection.execute(
            "INSERT INTO conversation.tool_links(turn_id,call_id,action_id,capability_id,state,tool_name,"
            "provider_call_id,arguments_hash) VALUES('turn','call','action','content.read','queued',"
            "'read','provider-call','fixture')")
        await connection.execute(
            "INSERT INTO conversation.expressions(expression_id,actor_id,conversation_id,target_ref,context,"
            "public_parts,source_refs,state,fingerprint) VALUES('channel','actor','chat','chat',%s,"
            "'[]','[]','prepared','fixture')", (Jsonb(wire(context)),))

    async def fence(connection):
        await owner.assert_result_head_on(connection, context, action_id="action",
            expected_receipt=value.event["receipt"], result_revision=1,
            data_generation=generation, source_refs=())
    fence.source_refs = ()
    fence.action_id = "action"
    fence.action_ids = frozenset({"action"})

    renderer = ExpressionRenderer(database, value.jobs, value.identity, value.identity, owner, None, None)
    service = ConversationService.__new__(ConversationService)
    service.database, service.sources, service.actions = database, value.identity, owner
    contacts = SimpleNamespace(record_delivery=AsyncMock())
    expression = ExpressionService(database, value.jobs, value.identity, None, contacts, sources=value.identity)
    for operation in (
        lambda: renderer.on_action_result(context, actual, 1, result_fence=fence),
        lambda: service._tool_receipt(context, actual, 1, result_fence=fence),
        lambda: expression.channel_result(context, "channel", actual, result_fence=fence),
    ):
        with pytest.raises(AppError, match="result_head_advanced"):
            await operation()
    async with database.transaction(read_only=True) as connection:
        plan = await (await connection.execute(
            "SELECT state,steps FROM conversation.expression_plans WHERE expression_id='render'")).fetchone()
        link = await (await connection.execute(
            "SELECT state,last_receipt_id,result_revision FROM conversation.tool_links WHERE action_id='action'")).fetchone()
        channel = await (await connection.execute(
            "SELECT state,receipt FROM conversation.expressions WHERE expression_id='channel'")).fetchone()
    assert plan["state"] == "waiting_render" and plan["steps"][0]["result_revision"] == 0
    assert link == {"state": "queued", "last_receipt_id": None, "result_revision": -1}
    assert channel == {"state": "prepared", "receipt": None}
    contacts.record_delivery.assert_not_awaited()


@pytest.mark.asyncio
async def test_new_render_failure_replaces_unstarted_old_delivery(database):
    value = await fixture(database)
    context = value.action.context
    async with database.transaction() as connection:
        generation = await value.jobs.current_generation_on(connection)
        await connection.execute(
            "INSERT INTO conversation.expression_plans(expression_id,actor_id,conversation_id,"
            "fingerprint,context,plan,source_refs,state,steps) "
            "VALUES('render','actor','chat','fixture',%s,'{}','[]','delivering',%s)",
            (Jsonb(wire(context)), Jsonb([{"action_id": "action", "result_revision": 1,
                                        "state": "succeeded", "artifacts": []}])))

    async def fence(connection):
        await value.jobs.assert_generation_on(connection, generation)
    fence.source_refs = ()
    result = replace(decode_receipt(value.event["receipt"]), execution="failed", error_code="render_failed")
    renderer = ExpressionRenderer(database, value.jobs, value.identity, value.identity, None, None, None)
    assert await renderer.on_action_result(context, result, 2, result_fence=fence)
    async with database.transaction(read_only=True) as connection:
        plan = await (await connection.execute(
            "SELECT state,steps FROM conversation.expression_plans WHERE expression_id='render'")).fetchone()
        sent = await (await connection.execute("SELECT 1 FROM conversation.expressions WHERE expression_id='render'")).fetchone()
    assert plan["state"] == "failed" and plan["steps"][0]["result_revision"] == 2
    assert sent is None


@pytest.mark.asyncio
async def test_failed_life_branch_does_not_stop_independent_consumers_or_repeat_receipts(database):
    value = await fixture(database)
    value.life.fail_once = True
    await publish(value, database)
    with pytest.raises(QueryCanceled):
        await value.consumer.consume(value.event)
    async with database.transaction(read_only=True) as connection:
        rows = await (await connection.execute(
            "SELECT consumer,disposition,reason,attempts FROM conversation.result_consumer_progress ORDER BY consumer"
        )).fetchall()
        effects = await (await connection.execute("SELECT count(*) AS count FROM test_rbi_effects")).fetchone()
    progress = {row["consumer"]: row["disposition"] for row in rows}
    assert progress["life_activity"] == "pending"
    activity = next(row for row in rows if row["consumer"] == "life_activity")
    assert activity["reason"] == "QueryCanceled" and activity["attempts"] == 1
    assert all(progress[name] == "not_applicable" for name in
               ("renderer", "dialogue", "life_commitment", "detached", "memory"))
    assert effects["count"] == 0

    await value.consumer.consume(value.event)
    await value.consumer.consume(value.event)
    async with database.transaction(read_only=True) as connection:
        effects = await (await connection.execute("SELECT count(*) AS count FROM test_rbi_effects")).fetchone()
        result = await (await connection.execute(
            "SELECT state FROM conversation.result_deliveries WHERE receipt_id='receipt'"
        )).fetchone()
    assert effects["count"] == 1 and result["state"] == "completed"
    value.actions.invoke.assert_not_awaited()
    value.actions.resume.assert_not_awaited()


@pytest.mark.asyncio
async def test_observe_detects_failed_original_and_failed_recovery_with_no_live_work(database):
    value = await fixture(database)
    value.life.fail_once = True
    saved = await publish(value, database)
    with pytest.raises(QueryCanceled):
        await value.consumer.consume(value.event)
    async with database.transaction() as connection:
        await fail_native_job(connection, saved["original_job_id"])
    facts = await value.recovery.inspect(None, {"id": str(saved["original_job_id"])})
    assert facts["eligible"]
    async with database.transaction() as connection:
        first = await ensure_on(connection, value.jobs, saved["data_generation"], value.event)
    assert first["state"] == "accepted" and first["job_id"] != saved["original_job_id"]
    running = await value.recovery.observe(None, None, facts, "ops-observer")
    assert running["state"] == "pending" and running["job_id"] == first["job_id"]
    async with database.transaction() as connection:
        await fail_native_job(connection, first["job_id"])
    stalled = await value.recovery.observe(None, None, facts, "ops-observer")
    assert stalled["state"] == "failed" and stalled["reason"] == "result_execution_missing"
    assert stalled["runnable_consumers"] == ("life_activity",)
    async with database.transaction() as connection:
        second = await ensure_on(connection, value.jobs, saved["data_generation"], value.event)
    assert second["job_id"] not in {saved["original_job_id"], first["job_id"]}
    value.actions.invoke.assert_not_awaited()
    value.actions.resume.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_budget_is_shared_across_new_physical_jobs(database):
    value = await fixture(database)
    saved = await publish(value, database)
    original = saved["original_job_id"]
    ids = [original]
    async with database.transaction() as connection:
        await fail_native_job(connection, original)
    for _ in range(3):
        async with database.transaction() as connection:
            next_wake = await ensure_on(connection, value.jobs, saved["data_generation"], value.event)
            assert next_wake["state"] == "accepted"
            ids.append(next_wake["job_id"])
            await fail_native_job(connection, next_wake["job_id"])
    async with database.transaction() as connection:
        rejected = await ensure_on(connection, value.jobs, saved["data_generation"], value.event)
        delivery = await (await connection.execute(
            "SELECT original_job_id,rbi_job_ids FROM conversation.result_deliveries WHERE receipt_id='receipt'"
        )).fetchone()
        jobs = await (await connection.execute(
            "SELECT count(*) AS total FROM procrastinate_jobs WHERE task_name='conversation.result'"
        )).fetchone()
    assert rejected["state"] == "blocked" and rejected["reason"] == "execution_failure_budget_exhausted"
    assert rejected["budget"]["failed_executions"] == 4
    assert delivery["original_job_id"] == original and set(delivery["rbi_job_ids"]) == set(ids)
    assert jobs["total"] == 4


@pytest.mark.asyncio
async def test_a_missing_recorded_job_cannot_reinitialize_a_receipt_budget(database):
    value = await fixture(database)
    saved = await publish(value, database)
    async with database.transaction() as connection:
        await fail_native_job(connection, saved["original_job_id"])
        result = await ensure_on(connection, value.jobs, saved["data_generation"], value.event,
                                 known_job_ids=(999999999,))
        delivery = await (await connection.execute(
            "SELECT original_job_id,rbi_job_ids FROM conversation.result_deliveries WHERE receipt_id='receipt'"
        )).fetchone()
    assert result["state"] == "blocked" and result["reason"] == "history_unknown"
    assert delivery["original_job_id"] == saved["original_job_id"]
    assert 999999999 in delivery["rbi_job_ids"]


@pytest.mark.asyncio
async def test_native_history_scan_resumes_after_a_bounded_page(database):
    value = await fixture(database)
    saved = await publish(value, database)
    async with database.transaction() as connection:
        await connection.execute("UPDATE procrastinate_jobs SET status='doing' WHERE id=%s", (saved["original_job_id"],))
        await connection.execute("UPDATE procrastinate_jobs SET status='succeeded',attempts=attempts+1 WHERE id=%s",
                                 (saved["original_job_id"],))
        for number in range(20):
            job_id = await value.jobs.enqueue(connection, JobSpec("conversation.result", f"legacy-result:{number}",
                {"event": value.event}, ServiceClass.INTERACTIVE, subject_id="actor",
                data_generation=saved["data_generation"]))
            await connection.execute("UPDATE procrastinate_jobs SET status='doing' WHERE id=%s", (job_id,))
            await connection.execute("UPDATE procrastinate_jobs SET status='succeeded',attempts=attempts+1 WHERE id=%s",
                                     (job_id,))
    async with database.transaction() as connection:
        first = await ensure_on(connection, value.jobs, saved["data_generation"], value.event)
        partial = await (await connection.execute(
            "SELECT rbi_history_cursor,rbi_history_upper FROM conversation.result_deliveries WHERE receipt_id='receipt'"
        )).fetchone()
    assert first["state"] == "blocked" and first["reason"] == "history_unknown"
    assert 0 < partial["rbi_history_cursor"] < partial["rbi_history_upper"]
    async with database.transaction() as connection:
        second = await ensure_on(connection, value.jobs, saved["data_generation"], value.event)
        current = await (await connection.execute(
            "SELECT rbi_history_cursor,rbi_history_upper FROM conversation.result_deliveries WHERE receipt_id='receipt'"
        )).fetchone()
    assert second["state"] == "accepted" and second["reason"] == "scheduled"
    assert current["rbi_history_cursor"] > partial["rbi_history_cursor"]
    assert current["rbi_history_upper"] is None


@pytest.mark.asyncio
async def test_legacy_discovery_restores_only_the_original_job_pointer(database):
    value = await fixture(database)
    saved = await publish(value, database)
    async with database.transaction() as connection:
        await connection.execute("DELETE FROM conversation.result_deliveries WHERE receipt_id='receipt'")
    result = await value.recovery._discover_legacy()
    async with database.transaction(read_only=True) as connection:
        restored = await (await connection.execute(
            "SELECT original_job_id,state FROM conversation.result_deliveries WHERE receipt_id='receipt'"
        )).fetchone()
    assert result["registered"] == 1
    assert restored == {"original_job_id": saved["original_job_id"], "state": "pending"}


@pytest.mark.asyncio
async def test_action_bound_channel_result_does_not_bypass_a_missing_head(database):
    value = await fixture(database)
    missing = SimpleNamespace(get=AsyncMock(side_effect=AppError("not_found", status=404)))
    contacts = SimpleNamespace(record_delivery=AsyncMock())
    expression = ExpressionService(database, value.jobs, value.identity, None, contacts,
                                   sources=value.identity, actions=missing)
    plan = ExpressionPlan("channel", "actor", "chat", (), (), OriginKind.ACTOR)
    with pytest.raises(AppError, match="action_head_unavailable"):
        await expression._finish(value.action.context, plan, decode_receipt(value.event["receipt"]))
    contacts.record_delivery.assert_not_awaited()


@pytest.mark.asyncio
async def test_committed_atomic_proof_is_read_before_any_second_life_apply(database):
    value = await fixture(database)
    value.life.lose_ack_once = True
    await publish(value, database)
    await value.consumer.consume(value.event)
    await value.consumer.consume(value.event)
    async with database.transaction(read_only=True) as connection:
        effects = await (await connection.execute("SELECT count(*) AS count FROM test_rbi_effects")).fetchone()
        progress = await (await connection.execute(
            "SELECT disposition,proof FROM conversation.result_consumer_progress "
            "WHERE receipt_id='receipt' AND consumer='life_activity'"
        )).fetchone()
    assert effects["count"] == 1 and value.life.activity_calls == 1
    assert progress["disposition"] == "done" and progress["proof"]["receipt_id"] == "receipt"
    value.actions.invoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancellation_retains_the_original_claim_for_reconciliation(database):
    value = await fixture(database)
    await publish(value, database)
    value.life._activity_result = AsyncMock(side_effect=asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        await value.consumer.consume(value.event)
    async with database.transaction(read_only=True) as connection:
        progress = await (await connection.execute(
            "SELECT disposition,owner_token FROM conversation.result_consumer_progress "
            "WHERE receipt_id='receipt' AND consumer='life_activity'"
        )).fetchone()
    assert progress["disposition"] == "running" and progress["owner_token"]


@pytest.mark.asyncio
async def test_future_wake_and_live_original_do_not_create_an_unproved_successor(database):
    value = await fixture(database)
    saved = await publish(value, database)
    future = datetime.now(UTC) + timedelta(minutes=10)
    async with database.transaction() as connection:
        await connection.execute("UPDATE procrastinate_jobs SET scheduled_at=%s WHERE id=%s",
                                 (future, saved["original_job_id"]))
        existing = await ensure_on(connection, value.jobs, saved["data_generation"], value.event)
    assert existing["job_id"] == saved["original_job_id"] and existing["due_at"] == future
    async with database.transaction() as connection:
        worker = await (await connection.execute("INSERT INTO procrastinate_workers DEFAULT VALUES RETURNING id")).fetchone()
        await connection.execute("UPDATE procrastinate_jobs SET status='doing',worker_id=%s WHERE id=%s",
                                 (worker["id"], saved["original_job_id"]))
        successor = await ensure_on(connection, value.jobs, saved["data_generation"], value.event,
                                    allow_successor=True)
        same = await ensure_on(connection, value.jobs, saved["data_generation"], value.event,
                               allow_successor=False)
    assert successor["state"] in {"blocked", "waiting"} and successor["reason"] in {
        "execution_in_progress", "execution_outcome_unknown"}
    assert successor.get("job_id") is None
    assert same["state"] in {"blocked", "waiting"}
    assert same["reason"] in {"execution_in_progress", "execution_outcome_unknown"}


@pytest.mark.asyncio
async def test_blocked_renderer_does_not_prevent_independent_life_and_observation_stops(database):
    value = await fixture(database)
    saved = await publish(value, database)
    error = RecoveryWait("render_uncertain", datetime.now(UTC) + timedelta(minutes=1), dependency="renderer")
    error.remote_rollback_confirmed = False
    value.renderer.on_action_result = AsyncMock(side_effect=error)
    with pytest.raises(RecoveryWait):
        await value.consumer.consume(value.event)
    async with database.transaction() as connection:
        await fail_native_job(connection, saved["original_job_id"])
        rows = await (await connection.execute("SELECT consumer,disposition FROM conversation.result_consumer_progress")).fetchall()
    progress = {row["consumer"]: row["disposition"] for row in rows}
    assert progress["renderer"] == "blocked" and progress["life_activity"] == "done"
    assert progress["dialogue"] == progress["memory"] == "pending"
    facts = {"version": str(saved["original_job_id"]), "data_generation": saved["data_generation"],
             "action_id": "action", "receipt_id": "receipt", "result_revision": 1}
    observed = await value.recovery.observe(None, None, facts, "ops-observer")
    assert observed["state"] == "failed" and observed["reason"] == "result_consumer_blocked"


@pytest.mark.asyncio
async def test_lost_native_wait_keeps_original_due_without_blocking_other_consumers(database):
    value = await fixture(database)
    saved = await publish(value, database)
    due = datetime.now(UTC) + timedelta(minutes=5)
    value.life.wait_until = due
    with pytest.raises(RecoveryWait):
        await value.consumer.consume(value.event)
    async with database.transaction() as connection:
        await fail_native_job(connection, saved["original_job_id"])
        pending = await (await connection.execute(
            "SELECT disposition,rbi_retry_at FROM conversation.result_consumer_progress "
            "WHERE receipt_id='receipt' AND consumer='life_activity'"
        )).fetchone()
        replacement = await ensure_on(connection, value.jobs, saved["data_generation"], value.event)
    assert pending == {"disposition": "pending", "rbi_retry_at": due}
    assert replacement["due_at"] == due
    with pytest.raises(RecoveryWait):
        await value.consumer.consume(value.event)
    assert value.life.activity_calls == 1


@pytest.mark.asyncio
async def test_expired_consumer_lease_cannot_commit_a_late_atomic_effect(database):
    value = await fixture(database)
    saved = await publish(value, database)
    async with database.transaction() as connection:
        await ledger.initialize_on(connection, saved["data_generation"], value.event, wire(value.action), "actor")
        token, _ = await ledger.claim_on(connection, saved["data_generation"], "receipt", "life_activity")
        await connection.execute(
            "UPDATE conversation.result_consumer_progress SET lease_until=now()-interval '1 second' "
            "WHERE receipt_id='receipt' AND consumer='life_activity'"
        )
    with pytest.raises(AppError, match="result_consumer_claim_lost"):
        async with database.transaction() as connection:
            await connection.execute("INSERT INTO test_rbi_effects(receipt_id) VALUES ('receipt')")
            await ledger.complete_on(connection, saved["data_generation"], "receipt", "life_activity", token)
    async with database.transaction(read_only=True) as connection:
        count = await (await connection.execute("SELECT count(*) AS count FROM test_rbi_effects")).fetchone()
    assert count["count"] == 0
