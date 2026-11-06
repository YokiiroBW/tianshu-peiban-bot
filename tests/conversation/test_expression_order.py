from importlib.resources import files
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from tianshu.capabilities.actions import Actions as CapabilityActions, encoded
from tianshu.capabilities.catalog import Catalog
from tianshu.capabilities.contracts import canonical
from tianshu.conversation.codec import document, receipt
from tianshu.conversation.rendering import ExpressionRenderer
from tianshu.conversation.expression import ExpressionService
from tianshu.conversation.service import ConversationService
from tianshu.conversation.types import ExpressionPlan
from tianshu.runtime.migrations import Migration, migrate
from tianshu.runtime.jobs import JobQueue
from tianshu.runtime.errors import AppError
from tianshu.runtime.serialization import wire
from tianshu.types import Action, ArtifactRef, Attempt, MediaRef, OriginKind, Receipt, TrustedContext


class Authority:
    async def check(self, *_args):
        pass

    async def validate_persistence(self, *_args):
        return True


class Jobs:
    def __init__(self):
        self.items = []

    current_generation_on = staticmethod(JobQueue.current_generation_on)
    assert_generation_on = staticmethod(JobQueue.assert_generation_on)

    async def observe_on(self, _connection, **_kwargs):
        return None

    async def enqueue(self, _connection, spec, *, queueing_lock=None):
        self.items.append(spec)


class ActionHeads(CapabilityActions):
    """A real Action row and public fence for synthetic channel receipts."""

    def __init__(self, database, jobs, authority):
        super().__init__(database, jobs, Catalog(), authority)
        self.sources = authority

    async def put(self, context, result, *, action=None):
        action = action or Action(result.action_id, "test:" + result.action_id, "channels.deliver",
                                  context.actor_id, context, {}, OriginKind.ACTOR)
        attempt = Attempt(result.attempt_id, result.action_id)
        async with self.database.transaction() as connection:
            generation = await self.jobs.current_generation_on(connection)
            current = await (await connection.execute(
                "SELECT receipt FROM capabilities.actions WHERE id=%s FOR UPDATE", (result.action_id,))).fetchone()
            if current:
                if current["receipt"] != wire(result):
                    await connection.execute(
                        "UPDATE capabilities.actions SET receipt=%s::jsonb,result_revision=result_revision+1,"
                        "result_observed_at=now() WHERE id=%s", (encoded(result), result.action_id))
                return
            await connection.execute(
                "INSERT INTO capabilities.actions(id,intent,semantics,principal_id,kind,created_at,document,"
                "action,attempt,receipt,result_revision,result_observed_at) "
                "VALUES(%s,%s,%s,%s,%s,0,%s,%s::jsonb,%s::jsonb,%s::jsonb,1,now())",
                (action.action_id, action.intent_key, "fixture", context.principal_id, action.capability_id,
                 canonical({"data_generation": generation}), encoded(action), encoded(attempt), encoded(result)))


async def prepare_action_schema(database):
    await migrate(database, (Migration("capabilities-0001", files("tianshu.capabilities").joinpath("schema.sql").read_text()),))


async def action_heads(database, jobs, authority):
    await prepare_action_schema(database)
    return ActionHeads(database, jobs, authority)


class Voice:
    def __init__(self, mode="text"):
        self.mode = mode

    async def expression_profile(self, _context):
        return {"profile_id": "fixed", "revision": 3, "output_mode": self.mode}


class Delivery:
    def __init__(self):
        self.sent = []

    async def send(self, _context, plan):
        self.sent.append(plan)
        return Receipt(str(uuid4()), None, None, "succeeded", "confirmed", "delivered")


@pytest.mark.asyncio
async def test_contact_projection_recovers_after_atomic_rollback_without_redelivery(database):
    context = await prepare(database)
    delivered = Receipt("delivered", "action", "attempt", "succeeded", "confirmed", "delivered")
    class Channels:
        calls = 0
        async def deliver(self, _context, _plan):
            self.calls += 1
            return delivered
    class Contacts:
        calls = 0
        async def record_delivery(self, _context, _plan, result, *, connection=None):
            if result.delivery != "delivered":
                return
            self.calls += 1
            assert connection is not None
            await connection.execute("INSERT INTO test_contact_projection(receipt_id) VALUES (%s) ON CONFLICT DO NOTHING",
                                     (result.receipt_id,))
            if self.calls == 1:
                raise RuntimeError("contact transaction interrupted")
    authority, jobs, channels, contacts = Authority(), Jobs(), Channels(), Contacts()
    actions = await action_heads(database, jobs, authority)
    await actions.put(context, delivered)
    async with database.transaction() as connection:
        await connection.execute("CREATE TABLE test_contact_projection(receipt_id text PRIMARY KEY)")
    service = ExpressionService(database, jobs, authority, channels, contacts, sources=authority, actions=actions)
    plan = ExpressionPlan("recover-contact", context.actor_id, context.conversation_id,
                          ({"kind": "text", "text": "hello"},), (), OriginKind.ACTOR)
    with pytest.raises(RuntimeError, match="contact transaction interrupted"):
        await service.send(context, plan)
    async with database.transaction(read_only=True) as connection:
        original = await (await connection.execute(
            "SELECT state,receipt FROM conversation.expressions WHERE expression_id=%s", (plan.expression_id,))).fetchone()
        count = await (await connection.execute("SELECT count(*) AS count FROM test_contact_projection")).fetchone()
    assert original == {"state": "sending", "receipt": None} and count["count"] == 0
    await service.channel_result(context, plan.expression_id, delivered)
    async with database.transaction(read_only=True) as connection:
        count = await (await connection.execute("SELECT count(*) AS count FROM test_contact_projection")).fetchone()
    assert count["count"] == 1 and channels.calls == 1


async def prepare(database):
    await migrate(database, (Migration("0032_conversation", files("tianshu.conversation").joinpath("schema.sql").read_text()),
        Migration("0041_conversation_submission_confirmation", files("tianshu.conversation").joinpath("submission_confirmation.sql").read_text())))
    return TrustedContext("principal", "actor", "person", "web-conversation", ("private",),
                          ("private",), 1, OriginKind.HUMAN, conversation_kind="private",
                          delivery_allowed=True)


async def input_turn(database, context, turn_id):
    async with database.transaction() as connection:
        await connection.execute(
            "INSERT INTO conversation.turns(turn_id,actor_id,conversation_id,producer,event_id,event_revision,"
            "context,input_parts,source_refs,original_parts,original_source_refs,state,reply_owner,reply_required,"
            "occurred_at,received_at,deadline_at) VALUES (%s,%s,%s,'test',%s,1,%s,'[]','[]','[]','[]',"
            "'running','tianshu',true,now(),now(),now()+interval '15 minutes')",
            (turn_id, context.actor_id, context.conversation_id, turn_id, document(context)))


@pytest.mark.asyncio
async def test_faster_second_turn_waits_for_the_original_input_order(database):
    context = await prepare(database)
    await input_turn(database, context, "first")
    await input_turn(database, context, "second")
    delivery = Delivery()
    renderer = ExpressionRenderer(database, Jobs(), Authority(), Authority(), None, Voice(), delivery)
    second = ExpressionPlan("second-answer", "actor", "web-conversation",
                            ({"kind": "text", "text": "second"},), (), OriginKind.ACTOR, turn_id="second")
    waiting = await renderer.send(context, second)
    assert waiting.state == "waiting_order"
    assert not delivery.sent
    async with database.transaction() as connection:
        row = await (await connection.execute(
            "UPDATE conversation.expression_plans SET created_at=now()-interval '1 day' "
            "WHERE expression_id='second-answer' RETURNING deadline_at")).fetchone()
    assert row["deadline_at"] is None
    await renderer.expire("second-answer")
    assert (await renderer.get(context, "second-answer"))["state"] == "waiting_order"
    first = ExpressionPlan("first-answer", "actor", "web-conversation",
                           ({"kind": "text", "text": "first"},), (), OriginKind.ACTOR, turn_id="first")
    assert (await renderer.send(context, first)).state == "delivered"
    async with database.transaction() as connection:
        await connection.execute("UPDATE conversation.turns SET state='completed' WHERE turn_id='first'")
    assert (await renderer.release("second-answer")).state == "delivered"
    assert [item.expression_id for item in delivery.sent] == ["first-answer", "second-answer"]
    await renderer.release("second-answer")
    assert len(delivery.sent) == 2


@pytest.mark.asyncio
async def test_existing_audio_is_presented_once_without_another_synthesis(database):
    context = await prepare(database)
    delivery = Delivery()
    # No Actions object: attempting automatic synthesis would fail this scenario.
    renderer = ExpressionRenderer(database, Jobs(), Authority(), Authority(), None, Voice("audio"), delivery)
    media = MediaRef("artifact:spoken", "controlled", "audio/wav")
    plan = ExpressionPlan("already-spoken", "actor", "web-conversation",
                          ({"kind": "audio", "media": {"media_id": media.media_id, "uri": media.uri,
                                                        "mime_type": media.mime_type}},), (), OriginKind.ACTOR)
    assert (await renderer.send(context, plan)).state == "delivered"
    await renderer.send(context, plan)
    assert len(delivery.sent) == 1


@pytest.mark.asyncio
async def test_release_rechecks_the_channel_action_head_before_projecting_its_receipt(database):
    context = await prepare(database)
    authority, jobs = Authority(), Jobs()
    actions = await action_heads(database, jobs, authority)
    pending = Receipt("pending", "channel-action", "channel-attempt", "pending", "none", "pending")
    delivered = replace(pending, receipt_id="delivered", execution="succeeded",
                        effect="confirmed", delivery="delivered")
    await actions.put(context, pending)
    real_get = actions.get
    advanced = False

    async def get_then_advance(action_id, current_context):
        nonlocal advanced
        snapshot = await real_get(action_id, current_context)
        if action_id == pending.action_id and not advanced:
            advanced = True
            await actions.put(context, delivered)
        return snapshot

    actions.get = get_then_advance

    class ChannelDelivery:
        calls = 0

        async def send(self, _context, _plan):
            self.calls += 1
            return pending

    delivery = ChannelDelivery()
    renderer = ExpressionRenderer(database, jobs, authority, authority, actions, Voice(), delivery)
    plan = ExpressionPlan("racing-channel", "actor", "web-conversation",
                          ({"kind": "text", "text": "one delivery"},), (), OriginKind.ACTOR)
    with pytest.raises(AppError, match="result_head_advanced"):
        await renderer.send(context, plan)
    async with database.transaction(read_only=True) as connection:
        projection = await (await connection.execute(
            "SELECT state,receipt FROM conversation.expression_plans WHERE expression_id=%s",
            (plan.expression_id,))).fetchone()
    assert projection == {"state": "delivering", "receipt": None}
    assert (await real_get(pending.action_id, context))["receipt"]["receipt_id"] == "delivered"
    assert (await renderer.release(plan.expression_id)).state == "delivering"
    assert delivery.calls == 1


@pytest.mark.asyncio
async def test_release_does_not_treat_a_missing_action_head_as_channel_only(database):
    context = await prepare(database)
    authority, jobs = Authority(), Jobs()
    actions = await action_heads(database, jobs, authority)

    class ChannelDelivery:
        calls = 0

        async def send(self, _context, _plan):
            self.calls += 1
            return Receipt("pending", "missing-action", "attempt", "pending", "none", "pending")

    delivery = ChannelDelivery()
    renderer = ExpressionRenderer(database, jobs, authority, authority, actions, Voice(), delivery)
    plan = ExpressionPlan("missing-head", "actor", "web-conversation",
                          ({"kind": "text", "text": "one delivery"},), (), OriginKind.ACTOR)
    with pytest.raises(AppError, match="action_head_unavailable"):
        await renderer.send(context, plan)
    async with database.transaction(read_only=True) as connection:
        projection = await (await connection.execute(
            "SELECT state,receipt FROM conversation.expression_plans WHERE expression_id=%s",
            (plan.expression_id,))).fetchone()
    assert projection == {"state": "delivering", "receipt": None}
    assert (await renderer.release(plan.expression_id)).state == "delivering"
    assert delivery.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["pending", "failed_projection", "finish_before_ack"])
async def test_async_channel_receipt_converges_without_redelivery(database, scenario):
    context = await prepare(database)
    await input_turn(database, context, "async-turn")
    expression_id = "turn:async-turn"
    async with database.transaction() as connection:
        await connection.execute(
            "UPDATE conversation.turns SET expression_id=%s WHERE turn_id='async-turn'",
            (expression_id,))

    pending = Receipt("accepted", "original-action", "original-attempt",
                      "pending", "none", "not_requested")
    delivered = replace(pending, receipt_id="delivered", execution="succeeded",
                        effect="confirmed", delivery="delivered")

    class Channels:
        calls = 0
        observed = pending

        async def deliver(self, _context, _plan):
            self.calls += 1
            if scenario == "finish_before_ack":
                self.observed = delivered
                await actions.put(context, delivered)
                await renderer.channel_result(context, expression_id, delivered)
            return pending

        async def reconcile(self, _context, identifier):
            assert identifier == expression_id
            return self.observed

    class Contacts:
        def __init__(self):
            self.observations = []

        async def record_delivery(self, _context, _plan, result, *, connection=None):
            if result.delivery == "delivered" and any(item.delivery == "delivered" for item in self.observations):
                return
            self.observations.append(result)

    authority, jobs, channels, contacts = Authority(), Jobs(), Channels(), Contacts()
    actions = await action_heads(database, jobs, authority)
    await actions.put(context, pending)
    delivery = ExpressionService(database, jobs, authority, channels, contacts, sources=authority, actions=actions)
    renderer = ExpressionRenderer(database, jobs, authority, authority, actions, Voice(), delivery)
    conversation = ConversationService(
        database, None, jobs, authority, authority, None, None, None, None,
        None, None, None, None, renderer, contacts)
    plan = ExpressionPlan(expression_id, "actor", "web-conversation",
                          ({"kind": "text", "text": "你好"},), (), OriginKind.ACTOR,
                          turn_id="async-turn")
    initial = await renderer.send(context, plan)
    await conversation.expression_result(context, expression_id, await renderer.get(context, expression_id))
    if scenario != "finish_before_ack":
        assert initial.state == "waiting_delivery"
        assert initial.receipt == pending
        original = await delivery.get(context, expression_id)
        assert original["state"] == "waiting_delivery"
        assert original["receipt"]["action_id"] == "original-action"
        async with database.transaction(read_only=True) as connection:
            turn = await (await connection.execute(
                "SELECT state,completed_at FROM conversation.turns WHERE turn_id='async-turn'"
            )).fetchone()
        assert turn == {"state": "waiting_expression", "completed_at": None}
        if scenario == "failed_projection":
            # Reproduce the observed split: renderer delivered, lower receipt/turn failed.
            async with database.transaction() as connection:
                await connection.execute("UPDATE conversation.expressions SET state='failed' WHERE expression_id=%s", (expression_id,))
                await connection.execute("UPDATE conversation.expression_plans SET state='delivered',receipt=%s,revision=revision+1 WHERE expression_id=%s", (document(delivered), expression_id))
                await connection.execute("UPDATE conversation.turns SET state='failed',error_code=NULL WHERE turn_id='async-turn'")
            channels.observed = delivered
            await actions.put(context, delivered)
            current = await renderer.get(context, expression_id)
            assert (await renderer.reconcile(context, expression_id, current["revision"]))["state"] == "delivered"
        else:
            async with database.transaction() as connection:
                await connection.execute("UPDATE conversation.expressions SET lease_until=now()-interval '1 second' WHERE expression_id=%s", (expression_id,))
            await delivery.expire(expression_id)
            expired = await delivery.get(context, expression_id)
            assert expired["state"] == "waiting_delivery"
            assert expired["receipt"]["action_id"] == "original-action"
            assert expired["receipt"]["attempt_id"] == "original-attempt"
            unknown = replace(pending, receipt_id="unconfirmed", execution="unknown",
                              effect="unknown", delivery="unknown")
            channels.observed = unknown
            await actions.put(context, unknown)
            await renderer.channel_result(context, expression_id, unknown)
            assert (await renderer.send(context, plan)).state == "delivery_unknown"
            assert (await delivery.send(context, plan)).attempt_id == "original-attempt"
            channels.observed = delivered
            await actions.put(context, delivered)
            current = await renderer.get(context, expression_id)
            assert (await renderer.reconcile(context, expression_id, current["revision"]))["state"] == "delivered"

    await conversation.expression_result(context, expression_id, await renderer.get(context, expression_id))

    async def projections():
        async with database.transaction(read_only=True) as connection:
            original = await (await connection.execute("SELECT state,receipt,revision FROM conversation.expressions WHERE expression_id=%s", (expression_id,))).fetchone()
            rendered = await (await connection.execute("SELECT state,receipt,revision FROM conversation.expression_plans WHERE expression_id=%s", (expression_id,))).fetchone()
            turn = await (await connection.execute("SELECT state,error_code,revision FROM conversation.turns WHERE turn_id='async-turn'")).fetchone()
            messages = await (await connection.execute("SELECT count(*) AS count FROM conversation.messages WHERE expression_id=%s", (expression_id,))).fetchone()
        return original, rendered, turn, messages

    stable = await projections()
    for projection in stable[:2]:
        assert projection["state"] == "delivered"
        assert projection["receipt"]["action_id"] == "original-action"
        assert projection["receipt"]["attempt_id"] == "original-attempt"
        assert projection["receipt"]["receipt_id"] == "delivered"
    assert stable[2]["state"] == "completed"
    assert stable[2]["error_code"] is None
    assert stable[3]["count"] == 1
    await renderer.channel_result(context, expression_id, pending)
    await renderer.channel_result(context, expression_id, delivered)
    assert await projections() == stable
    assert channels.calls == 1
    assert contacts.observations.count(delivered) == 1


@pytest.mark.asyncio
async def test_render_queue_has_no_synthetic_expiry_and_observes_one_original_tts_action(database):
    context = await prepare(database)
    delivery, jobs = Delivery(), Jobs()

    class Actions(ActionHeads):
        def __init__(self):
            super().__init__(database, jobs, Authority())
            self.calls = []

        async def invoke(self, action, **_kwargs):
            self.calls.append(action)
            pending_tts = Receipt("pending-tts", action.action_id, "original-tts-attempt", "pending", "none", "not_requested")
            await self.put(context, pending_tts, action=action)

    await prepare_action_schema(database)
    actions = Actions()
    renderer = ExpressionRenderer(database, jobs, Authority(), Authority(), actions, Voice("audio"), delivery)
    plan = ExpressionPlan("queued-speech", "actor", "web-conversation",
        ({"kind": "text", "text": "稍后朗读"},), (), OriginKind.ACTOR)
    assert (await renderer.send(context, plan)).state == "waiting_render"
    async with database.transaction() as connection:
        row = await (await connection.execute(
            "UPDATE conversation.expression_plans SET created_at=now()-interval '1 day' "
            "WHERE expression_id=%s RETURNING deadline_at", (plan.expression_id,))).fetchone()
    assert row["deadline_at"] is None
    await renderer.expire(plan.expression_id)
    await renderer.recover()
    assert all(item.task != "conversation.render_expire" for item in jobs.items)
    await renderer.render(plan.expression_id, 0)
    await renderer.render(plan.expression_id, 0)
    assert len(actions.calls) == 1 and actions.calls[0].deadline_at is None
    assert (await renderer.get(context, plan.expression_id))["state"] == "waiting_render"
    current = (await actions.get(actions.calls[0].action_id, context))["receipt"]
    spoken = replace(receipt(current), receipt_id="spoken", execution="succeeded", effect="confirmed",
        artifacts=(ArtifactRef("speech", MediaRef("artifact:speech", "controlled", "audio/wav")),))
    await actions.put(context, spoken, action=actions.calls[0])
    await renderer.render(plan.expression_id, 0)
    assert (await renderer.get(context, plan.expression_id))["state"] == "delivered"
    assert len(actions.calls) == len(delivery.sent) == 1
    await renderer.send(context, plan)
    assert len(actions.calls) == len(delivery.sent) == 1


@pytest.mark.asyncio
async def test_autonomous_expression_preserves_true_expiry_before_delivery(database):
    context = await prepare(database)
    await input_turn(database, context, "topic-turn")
    expiry = datetime.now(UTC)-timedelta(seconds=1)
    async with database.transaction() as connection:
        await connection.execute("UPDATE conversation.turns SET autonomous=true,deadline_at=%s WHERE turn_id='topic-turn'", (expiry,))
    delivery, jobs = Delivery(), Jobs()
    renderer = ExpressionRenderer(database, jobs, Authority(), Authority(), None, Voice(), delivery)
    plan = ExpressionPlan("expired-topic", "actor", "web-conversation",
        ({"kind": "text", "text": "旧话题"},), (), OriginKind.ACTOR, turn_id="topic-turn")
    result = await renderer.send(context, plan)
    assert result.state == "failed" and result.reason == "expression_window_expired"
    async with database.transaction(read_only=True) as connection:
        row = await (await connection.execute("SELECT deadline_at FROM conversation.expression_plans WHERE expression_id=%s", (plan.expression_id,))).fetchone()
    assert row["deadline_at"] == expiry
    assert any(item.task == "conversation.render_expire" and item.scheduled_at == expiry for item in jobs.items)
    assert delivery.sent == []


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["pending", "running", "read_failed", "succeeded", "failed", "unknown", "absent"])
async def test_delivery_lease_observes_original_channel_queue_and_converges_projections(database, status):
    context = await prepare(database)
    await input_turn(database, context, "queued-delivery")
    pending = Receipt("channel-pending", "original-action", "original-attempt", "pending", "none", "pending")

    class Channels:
        calls = 0
        reads = 0

        async def deliver(self, _context, _plan):
            self.calls += 1
            return pending

        async def reconcile(self, _context, expression_id):
            assert expression_id == "queued-delivery"
            self.reads += 1
            if status == "read_failed":
                raise TimeoutError("original channel ledger unavailable")
            if status == "absent":
                return None
            observed = replace(pending, receipt_id="channel-"+status, execution=status,
                effect="confirmed" if status == "succeeded" else "unknown" if status == "unknown" else "none",
                delivery="delivered" if status == "succeeded" else "failed" if status == "failed" else "unknown" if status == "unknown" else "pending")
            await actions.put(context, observed)
            return observed

    class Contacts:
        async def record_delivery(self, *_args, connection=None):
            pass

    authority, jobs, channels = Authority(), Jobs(), Channels()
    actions = await action_heads(database, jobs, authority)
    await actions.put(context, pending)
    delivery = ExpressionService(database, jobs, authority, channels, Contacts(), sources=authority, actions=actions)
    renderer = ExpressionRenderer(database, jobs, authority, authority, actions, Voice(), delivery)
    plan = ExpressionPlan("queued-delivery", "actor", "web-conversation",
        ({"kind": "text", "text": "只提交一次"},), (), OriginKind.ACTOR, turn_id="queued-delivery")
    assert (await renderer.send(context, plan)).state == "waiting_delivery"
    async with database.transaction() as connection:
        await connection.execute("UPDATE conversation.expressions SET lease_until=now()-interval '1 second' WHERE expression_id=%s", (plan.expression_id,))
    await renderer.expire_delivery(plan.expression_id)
    observed = await delivery.get(context, plan.expression_id)
    outer = await renderer.get(context, plan.expression_id)
    if status in {"pending", "running", "read_failed"}:
        assert observed["state"] == outer["state"] == "waiting_delivery"
        async with database.transaction(read_only=True) as connection:
            row = await (await connection.execute("SELECT lease_until FROM conversation.expressions WHERE expression_id=%s", (plan.expression_id,))).fetchone()
        assert row["lease_until"] > datetime.now(UTC)
        assert jobs.items[-1].task in {"conversation.expression_expire", "conversation.expression_result"}
    else:
        expected = "delivered" if status == "succeeded" else "failed" if status == "failed" else "unknown"
        assert observed["state"] == expected
        assert outer["state"] == ("delivery_unknown" if expected == "unknown" else expected)
    assert observed["receipt"]["action_id"] == "original-action"
    assert observed["receipt"]["attempt_id"] == "original-attempt"
    await renderer.send(context, plan)
    await delivery.send(context, plan)
    assert channels.calls == channels.reads == 1


@pytest.mark.asyncio
async def test_expression_timing_migration_preserves_legacy_plan_and_deadline(database):
    context = await prepare(database)
    plan = ExpressionPlan("legacy-plan", "actor", "web-conversation",
        ({"kind": "text", "text": "原表达"},), (), OriginKind.ACTOR)
    expiry = datetime.now(UTC)+timedelta(minutes=15)
    async with database.transaction() as connection:
        await connection.execute("ALTER TABLE conversation.expression_plans ALTER COLUMN deadline_at SET NOT NULL")
        await connection.execute(
            "INSERT INTO conversation.expression_plans(expression_id,actor_id,conversation_id,fingerprint,context,plan,source_refs,state,deadline_at) "
            "VALUES (%s,'actor','web-conversation','original-fingerprint',%s,%s,'[]','waiting_order',%s)",
            (plan.expression_id, document(context), document(plan), expiry))
        before = await (await connection.execute("SELECT * FROM conversation.expression_plans WHERE expression_id=%s", (plan.expression_id,))).fetchone()
    await migrate(database, (Migration("0036_conversation_expression_queue_timing",
        files("tianshu.conversation").joinpath("expression_queue_timing.sql").read_text()),))
    async with database.transaction(read_only=True) as connection:
        after = await (await connection.execute("SELECT * FROM conversation.expression_plans WHERE expression_id=%s", (plan.expression_id,))).fetchone()
        nullable = await (await connection.execute(
            "SELECT is_nullable FROM information_schema.columns WHERE table_schema='conversation' "
            "AND table_name='expression_plans' AND column_name='deadline_at'")).fetchone()
    assert after == before
    assert nullable["is_nullable"] == "YES"
