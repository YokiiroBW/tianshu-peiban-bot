from dataclasses import replace
from datetime import UTC, datetime, timedelta
from importlib.resources import files
from types import SimpleNamespace

import pytest

from tianshu.conversation.codec import document
from tianshu.conversation.dependencies import ConversationDependencies
from tianshu.conversation.participation import GroupParticipation, eligible
from tianshu.conversation.context.assembly import ContextAssembler
from tianshu.conversation.service import ConversationService
from tianshu.conversation.types import InputMessage, InputPart
from tianshu.life.attention import AttentionPolicy
from tianshu.models.types import ModelMessage, ModelPart, ModelReceipt, ModelResult
from tianshu.models.budgets import BACKGROUND_INPUT_TOKENS
from tianshu.runtime.migrations import Migration, migrate
from tianshu.types import OriginKind, SourceRef, TrustedContext


def context():
    return TrustedContext("author", "actor", "person", "group", ("scope",), ("group",), 1,
                          OriginKind.HUMAN, conversation_kind="group", delivery_allowed=False)


def test_only_live_unclaimed_group_observations_are_candidates():
    now = datetime.now(UTC)
    message = InputMessage("qq:connection", "event", 1, now, now, (InputPart("text", "topic"),),
                           reply_required=False, conversation_kind="group", autonomous_eligible=True)
    assert eligible(context(), message, now=now)
    assert not eligible(context(), replace(message, autonomous_eligible=False), now=now)
    assert not eligible(context(), replace(message, reply_owner="external"), now=now)
    assert not eligible(context(), replace(message, command="/explicit"), now=now)
    assert not eligible(context(), replace(message, occurred_at=now-timedelta(minutes=4)), now=now)
    assert not eligible(replace(context(), conversation_kind="private"), message, now=now)


class Models:
    def __init__(self):
        self.calls = []
        self.reads = []

    async def generate(self, request):
        self.calls.append(request)
        return ModelResult((), ModelReceipt(request.attempt_id, request.intent_id, "unknown", "unknown",
                                           "route", 1, "provider", "model", "responses", "quota"))

    async def result(self, attempt_id, *, context):
        self.reads.append(attempt_id)
        return ModelResult((), ModelReceipt(attempt_id, "original", "unknown", "unknown",
                                           "route", 1, "provider", "model", "responses", "quota"))


async def prepare(database, *, authorized=True):
    await migrate(database, (Migration("0032_conversation", files("tianshu.conversation").joinpath("schema.sql").read_text()),))
    ctx = context()
    refs = (SourceRef("original", 1),)
    now = datetime.now(UTC)
    async with database.transaction() as connection:
        await connection.execute("INSERT INTO conversation.group_participation VALUES('actor','group',true,1)")
        await connection.execute(
            "INSERT INTO conversation.turns(turn_id,actor_id,conversation_id,producer,event_id,event_revision,context,"
            "input_parts,source_refs,original_parts,original_source_refs,state,reply_owner,reply_required,occurred_at,"
            "received_at,deadline_at,participation_state,participation_policy_revision,participation_deadline) "
            "VALUES('turn','actor','group','qq:connection','event',1,%s,'[]',%s,'[]',%s,'observed','tianshu',false,%s,%s,%s,'waiting',1,%s)",
            (document(ctx), document(refs), document(refs), now, now, now+timedelta(minutes=2), now+timedelta(minutes=1)),
        )
    class Identity:
        async def check(self, *_args):
            pass
        async def check_many(self, _context, groups):
            return tuple(True for _ in groups)
        async def for_participation(self, original, *, source_refs):
            return replace(original, origin_kind=OriginKind.ACTOR, delivery_allowed=True) if authorized else None
    class Sources:
        async def validate_persistence(self, *_args):
            return True
    class Contacts:
        async def preferences(self, *_args, **_kwargs):
            return {"enabled":True,"timezone":"UTC","quiet_start":"00:00","quiet_end":"00:00",
                    "daily_quota":3,"unanswered_limit":3,"cooldown_seconds":1800}
        async def history(self, *_args, **_kwargs):
            return []
    class Companion:
        async def actor(self, _context):
            return {"enabled":True,"persona_id":None}
    class Assembler:
        async def assemble(self, *_args, **_kwargs):
            return SimpleNamespace(messages=(ModelMessage("user",(ModelPart("text",text="group topic"),)),),
                                   system="character",source_refs=refs,token_usage=SimpleNamespace(estimated_tokens=40))
    class Service:
        async def messages(self, *_args, **_kwargs):
            return SimpleNamespace(items=())
    service = Service()
    service.database = database
    service.sources = Sources()
    service.models = Models()
    service.identity = Identity()
    service.contacts = Contacts()
    service.companion = Companion()
    service.assembler = Assembler()
    service.life = SimpleNamespace(attention=AttentionPolicy())
    return GroupParticipation(service), service


@pytest.mark.asyncio
async def test_observation_flag_does_not_grant_delivery_or_call_models(database):
    participation, service = await prepare(database, authorized=False)
    await participation.decide("turn")
    assert service.models.calls == []
    async with database.transaction(read_only=True) as connection:
        row = await (await connection.execute("SELECT state,participation_state FROM conversation.turns WHERE turn_id='turn'")).fetchone()
    assert row == {"state":"observed","participation_state":"skipped"}


@pytest.mark.asyncio
async def test_unknown_participation_reads_original_attempt_without_another_charge(database):
    participation, service = await prepare(database)
    await participation.decide("turn")
    await participation.decide("turn")
    assert len(service.models.calls) == 1
    assert service.models.reads == [service.models.calls[0].attempt_id]
    assert service.models.calls[0].context.origin_kind == OriginKind.ACTOR
    assert service.models.calls[0].context.delivery_allowed
    request = service.models.calls[0]
    assert request.execution_timeout_seconds == 90.0
    assert request.business_deadline_at == request.budget.deadline_at
    assert request.max_queue_wait is None
    async with database.transaction() as connection:
        await ConversationDependencies().invalidate(connection, (SourceRef("original",1),), "forgotten")
    await participation.decide("turn")
    assert len(service.models.calls) == 1
    async with database.transaction(read_only=True) as connection:
        row = await (await connection.execute("SELECT request,state FROM conversation.generation_requests")).fetchone()
    assert row["request"] is None
    assert row["state"] == "invalidated"


@pytest.mark.asyncio
async def test_group_participation_has_200000_input_but_keeps_256_output_and_frozen_unknown(database):
    participation, service = await prepare(database)
    async def topic(*_args, **_kwargs):
        return SimpleNamespace(items=({"speaker": "user", "parts": [{"kind": "text", "text": "x"}],
            "source_refs": [{"source_id": "original", "revision": 1}]},))
    service.messages = topic
    service._message = ConversationService._message
    class BoundaryAssembler(ContextAssembler):
        calls = 0
        async def assemble(self, *args, **kwargs):
            self.calls += 1
            base = await super().assemble(*args, **kwargs)
            padding = BACKGROUND_INPUT_TOKENS - base.token_usage.estimated_tokens
            recent = kwargs["recent_messages"]
            message = recent[-1]
            filled = replace(message, parts=(replace(message.parts[0], text="x" * (padding + 1)),))
            self.pack = await super().assemble(*args, **{**kwargs, "recent_messages": (*recent[:-1], filled)})
            return self.pack
    assembler = BoundaryAssembler(service.identity, None, None)
    service.assembler = assembler
    await participation.decide("turn")
    request = service.models.calls[0]
    assert request.estimated_input_tokens == BACKGROUND_INPUT_TOKENS == 200000
    assert request.max_output_tokens == assembler.pack.token_usage.reserved_output_tokens == 256
    assert assembler.pack.token_usage.limit_tokens == 200000 + 256
    await participation.decide("turn")
    assert assembler.calls == len(service.models.calls) == 1
    assert service.models.reads == [request.attempt_id]
