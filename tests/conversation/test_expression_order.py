from importlib.resources import files
from uuid import uuid4

import pytest

from tianshu.conversation.codec import document
from tianshu.conversation.rendering import ExpressionRenderer
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
