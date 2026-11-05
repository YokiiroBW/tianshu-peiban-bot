"""A frozen model attempt releases the worker and is observed under the same ID."""
from datetime import UTC, datetime, timedelta
from importlib.resources import files
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tianshu.conversation.codec import document
from tianshu.conversation.service import ConversationService
from tianshu.models.types import ModelMessage, ModelPart, ModelReceipt, ModelRequest
from tianshu.runtime.migrations import Migration, migrate
from tianshu.types import OriginKind, RequestBudget, ServiceClass, TrustedContext


@pytest.mark.asyncio
async def test_model_request_is_frozen_and_enqueued_without_waiting_for_provider(database):
    await migrate(database, (Migration("0032_conversation", files("tianshu.conversation").joinpath("schema.sql").read_text()),
        Migration("0041_conversation_submission_confirmation", files("tianshu.conversation").joinpath("submission_confirmation.sql").read_text())))
    context = TrustedContext("p", "a", "person", "chat", ("s",), ("s",), 1,
                             OriginKind.HUMAN, conversation_kind="private", delivery_allowed=True)
    now = datetime.now(UTC)
    async with database.transaction() as conn:
        await conn.execute("INSERT INTO conversation.turns(turn_id,actor_id,conversation_id,producer,event_id,event_revision,"
            "context,input_parts,source_refs,original_parts,original_source_refs,state,reply_owner,reply_required,"
            "model_attempt_id,occurred_at,received_at,deadline_at) VALUES('turn','a','chat','test','event',1,"
            "%s,'[]','[]','[]','[]','running','tianshu',true,'attempt',%s,%s,%s)",
            (document(context), now, now, now+timedelta(minutes=10)))
        row = await (await conn.execute("SELECT * FROM conversation.turns WHERE turn_id='turn'")).fetchone()
    queued = []
    jobs = SimpleNamespace(enqueue=AsyncMock(side_effect=lambda _conn, spec: queued.append(spec)),
                           current_generation_on=AsyncMock(return_value="generation-1"))
    authority = SimpleNamespace(validate_persistence=AsyncMock(return_value=True))
    models = SimpleNamespace(generate=AsyncMock(), submit=AsyncMock(), result=AsyncMock())
    service = ConversationService(database, None, jobs, authority, authority, None, None,
                                  None, None, None, models, None, None, None, None)
    service.generation.prepare = AsyncMock()
    request = ModelRequest("turn:0", "attempt", "chat", ServiceClass.INTERACTIVE, context,
        RequestBudget(now+timedelta(minutes=10)), (ModelMessage("user", (ModelPart("text", text="你好"),)),))
    pack = SimpleNamespace(selection=({"kind": "current", "selected": True},), token_usage={"estimated_tokens": 10},
                           source_refs=(), missing=())
    service._prepare_model_request = AsyncMock(return_value=(request, pack, {}))
    await service._model_turn(context, row, "attempt", [])
    async with database.transaction(read_only=True) as conn:
        turn = await (await conn.execute("SELECT state,model_attempt_id FROM conversation.turns WHERE turn_id='turn'")).fetchone()
        selection = await (await conn.execute("SELECT attempt_id,selection FROM conversation.context_selections")).fetchone()
    assert turn == {"state": "waiting_external", "model_attempt_id": "attempt"}
    assert selection["attempt_id"] == "attempt" and selection["selection"][0]["selected"]
    assert {item.task for item in queued} == {"conversation.model_submit", "conversation.model_complete"}
    assert all(item.data_generation == "generation-1" for item in queued)
    models.generate.assert_not_awaited()
    models.submit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("confirmed", [False, True])
async def test_missing_observation_keeps_confirmed_receipt_on_observe_only_path(database, confirmed):
    await migrate(database, (Migration("0032_conversation", files("tianshu.conversation").joinpath("schema.sql").read_text()),
        Migration("0041_conversation_submission_confirmation", files("tianshu.conversation").joinpath("submission_confirmation.sql").read_text())))
    context = TrustedContext("p", "a", "person", "chat", ("s",), ("s",), 1,
                             OriginKind.HUMAN, conversation_kind="private", delivery_allowed=True)
    now = datetime.now(UTC)
    async with database.transaction() as conn:
        await conn.execute("INSERT INTO conversation.turns(turn_id,actor_id,conversation_id,producer,event_id,event_revision,"
            "context,input_parts,source_refs,original_parts,original_source_refs,state,reply_owner,reply_required,model_attempt_id,"
            "occurred_at,received_at,deadline_at) VALUES('turn','a','chat','test','event',1,%s,'[]','[]','[]','[]',"
            "'waiting_external','tianshu',true,'attempt',%s,%s,%s)", (document(context), now, now, now+timedelta(minutes=10)))
        await conn.execute("INSERT INTO conversation.context_selections(attempt_id,turn_id,round,request_digest,selection,"
            "token_usage,source_refs,round_snapshot) VALUES('attempt','turn',0,'digest','[]','{}','[]','{}')")
        if confirmed:
            prior = ModelReceipt("attempt", "intent", "unknown", "unknown", "route", 1,
                                 "provider", "model", "responses", "quota")
            await conn.execute("UPDATE conversation.context_selections SET model_receipt=%s WHERE attempt_id='attempt'", (document(prior),))
    models = SimpleNamespace(result=AsyncMock(return_value=None), submit=AsyncMock())
    service = ConversationService(database, None, None, None, None, None, None, None, None, None,
                                  models, None, None, None, None)
    service._schedule_model_observation = AsyncMock()
    await service.model_complete("turn", "attempt")
    scheduled = service._schedule_model_observation.await_args
    assert scheduled.args[1] == "attempt" and scheduled.kwargs == {"submit": not confirmed}
    models.submit.assert_not_awaited()
