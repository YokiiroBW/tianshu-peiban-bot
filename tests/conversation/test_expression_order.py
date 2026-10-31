from importlib.resources import files
from dataclasses import replace
from uuid import uuid4

import pytest

from tianshu.conversation.codec import document
from tianshu.conversation.rendering import ExpressionRenderer
from tianshu.conversation.expression import ExpressionService
from tianshu.conversation.service import ConversationService
from tianshu.conversation.types import ExpressionPlan
from tianshu.runtime.migrations import Migration, migrate
from tianshu.types import MediaRef, OriginKind, Receipt, TrustedContext


class Authority:
    async def check(self, *_args):
        pass

    async def validate_persistence(self, *_args):
        return True


class Jobs:
    async def enqueue(self, _connection, _spec):
        pass


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
        return Receipt(str(uuid4()), "delivery:"+plan.expression_id, "attempt:"+plan.expression_id,
                       "succeeded", "confirmed", "delivered")


async def prepare(database):
    await migrate(database, (Migration("0032_conversation", files("tianshu.conversation").joinpath("schema.sql").read_text()),))
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
                await renderer.channel_result(context, expression_id, delivered)
            return pending

        async def reconcile(self, _context, identifier):
            assert identifier == expression_id
            return self.observed

    class Contacts:
        def __init__(self):
            self.observations = []

        async def record_delivery(self, _context, _plan, result):
            self.observations.append(result)

    authority, jobs, channels, contacts = Authority(), Jobs(), Channels(), Contacts()
    delivery = ExpressionService(database, jobs, authority, channels, contacts, sources=authority)
    renderer = ExpressionRenderer(database, jobs, authority, authority, None, Voice(), delivery)
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
            current = await renderer.get(context, expression_id)
            assert (await renderer.reconcile(context, expression_id, current["revision"]))["state"] == "delivered"
        else:
            async with database.transaction() as connection:
                await connection.execute("UPDATE conversation.expressions SET lease_until=now()-interval '1 second' WHERE expression_id=%s", (expression_id,))
            await delivery.expire(expression_id)
            expired = await delivery.get(context, expression_id)
            assert expired["receipt"]["action_id"] == "original-action"
            assert expired["receipt"]["attempt_id"] == "original-attempt"
            unknown = replace(pending, receipt_id="unconfirmed", execution="unknown",
                              effect="unknown", delivery="unknown")
            channels.observed = unknown
            await renderer.channel_result(context, expression_id, unknown)
            assert (await renderer.send(context, plan)).state == "delivery_unknown"
            assert (await delivery.send(context, plan)).attempt_id == "original-attempt"
            channels.observed = delivered
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
