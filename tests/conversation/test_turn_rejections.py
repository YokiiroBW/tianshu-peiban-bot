"""Actual context preparation and PG turns distinguish rejection from lost model RPCs."""
from datetime import UTC, datetime, timedelta
from importlib.resources import files

import pytest

from tianshu.conversation.codec import document
from tianshu.conversation.context.assembly import ContextAssembler
from tianshu.conversation.service import ConversationService
from tianshu.models.types import ModelReceipt, ModelResult
from tianshu.runtime.api import Page
from tianshu.runtime.errors import AppError
from tianshu.runtime.migrations import Migration, migrate
from tianshu.types import Coverage, OriginKind, TrustedContext


class Authority:
    blocked = False

    async def check(self, *_args):
        if self.blocked:
            raise AppError("forbidden", status=403)

    async def check_many(self, _context, groups):
        return tuple(True for _ in groups)

    async def validate_persistence(self, *_args):
        return True


class ReadPorts:
    async def actor(self, _context):
        return {"enabled": True, "persona_id": None}

    async def state(self, _context):
        return {"activity": None, "timezone": "Asia/Shanghai", "affect": {
            "source_refs": (), "feelings": (), "regulation": {"expression_direction": "internal"}}}

    async def commitments(self, _context, **_kwargs):
        return Page((), None, Coverage(True))

    async def material(self, _context):
        return ()

    async def discover(self, _context):
        return []

    async def hint(self, _context):
        return {"output_mode": "text"}

    async def wake(self, *_args):
        pass


class Jobs:
    async def enqueue(self, *_args):
        pass


class Models:
    def __init__(self, outcome=None, *, return_receipt=False, transport_code=None):
        self.calls, self.reads = [], []
        self.outcome, self.return_receipt, self.transport_code = outcome, return_receipt, transport_code

    def observed(self, attempt_id):
        if self.outcome == "read_failed":
            raise TimeoutError("original receipt read unavailable")
        if self.outcome is None:
            return None
        if self.outcome in {"queued", "cancelled"}:
            dispatch = "not_started"
        elif self.outcome == "unknown":
            dispatch = "unknown"
        else:
            dispatch = "submitted"
        return ModelResult((), ModelReceipt(
            attempt_id, "original", self.outcome, dispatch, "route", 1, "provider", "model",
            "openai.responses", "quota", error_code="provider_rejected" if self.outcome == "failed" else None))

    async def generate(self, request):
        self.calls.append(request)
        if self.return_receipt:
            return self.observed(request.attempt_id)
        if self.transport_code:
            raise AppError(self.transport_code, status=403)
        raise TimeoutError("submission response lost")

    async def result(self, attempt_id, *, context):
        self.reads.append((attempt_id, context))
        return self.observed(attempt_id)


async def prepare(database, models, *, oversized=False, blocked=False):
    await migrate(database, (Migration(
        "0032_conversation", files("tianshu.conversation").joinpath("schema.sql").read_text()),))
    context = TrustedContext("author", "actor", "person", "conversation", ("private",),
                             ("private",), 1, OriginKind.HUMAN, conversation_kind="private", delivery_allowed=True)
    authority, ports = Authority(), ReadPorts()
    authority.blocked = blocked
    service = ConversationService(
        database, None, Jobs(), authority, authority, None, ports, ports, ports,
        ContextAssembler(authority, None, None), models, ports, None, ports, None)
    parts = [{"kind": "text", "text": "请看看这一段"}]
    if oversized:
        parts += [{"kind": kind, "media": {"media_id": "source:"+kind+":1", "uri": "controlled",
                   "mime_type": mime}} for kind, mime in (("audio", "audio/wav"), ("video", "video/mp4"))]
    async with database.transaction() as connection:
        message = await (await connection.execute(
            "INSERT INTO conversation.messages(actor_id,conversation_id,speaker,parts,source_refs,scope_ids) "
            "VALUES ('actor','conversation','user',%s,'[]',%s) RETURNING id",
            (document(parts), list(context.scope_ids)))).fetchone()
        await connection.execute(
            "INSERT INTO conversation.turns(turn_id,actor_id,conversation_id,producer,event_id,event_revision,"
            "context,input_parts,source_refs,original_parts,original_source_refs,state,reply_owner,reply_required,"
            "message_cutoff,occurred_at,received_at,deadline_at) VALUES ('turn','actor','conversation','test','input',1,"
            "%s,%s,'[]',%s,'[]','queued','tianshu',true,%s,now(),now(),%s)",
            (document(context), document(parts), document(parts), message["id"], datetime.now(UTC)+timedelta(minutes=2)))
    return service, context, authority


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["budget", "permission", "deadline"])
async def test_local_refusal_finishes_before_model_submission(database, reason):
    models = Models()
    service, context, authority = await prepare(
        database, models, oversized=reason == "budget", blocked=reason == "permission")
    if reason == "deadline":
        async with database.transaction() as connection:
            await connection.execute("UPDATE conversation.turns SET deadline_at=now()-interval '1 second' WHERE turn_id='turn'")
    await service.run("turn")
    authority.blocked = False
    original = await service.turn(context, "turn")
    assert original.state == "failed"
    assert original.error_code == {"budget": "context_mandatory_budget_exceeded",
                                   "permission": "forbidden", "deadline": "deadline_exceeded"}[reason]
    assert original.model_attempt_ids
    assert models.calls == models.reads == []
    await service.run("turn")
    assert (await service.turn(context, "turn")).model_attempt_ids == original.model_attempt_ids
    assert models.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", [None, "read_failed", "queued", "running", "unknown", "failed", "cancelled"])
async def test_rpc_failure_requires_the_original_terminal_receipt(database, outcome):
    models = Models(outcome)
    service, context, _ = await prepare(database, models)
    await service.run("turn")
    original = await service.turn(context, "turn")
    assert original.state == ("failed" if outcome in {"failed", "cancelled"} else "unknown")
    assert len(models.calls) == len(models.reads) == 1
    assert models.reads == [(models.calls[0].attempt_id, context)]
    assert original.model_attempt_ids == (models.calls[0].attempt_id,)
    await service.run("turn")
    assert len(models.calls) == 1
    assert (await service.turn(context, "turn")).model_attempt_ids == original.model_attempt_ids


@pytest.mark.asyncio
async def test_error_name_after_rpc_is_not_pre_submission_evidence(database):
    models = Models(transport_code="forbidden")
    service, context, _ = await prepare(database, models)
    await service.run("turn")
    turn = await service.turn(context, "turn")
    assert turn.state == "unknown"
    assert turn.error_code == "forbidden"
    assert len(models.calls) == len(models.reads) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["failed", "unknown"])
async def test_returned_model_failure_preserves_the_actual_receipt_semantics(database, status):
    models = Models(status, return_receipt=True)
    service, context, _ = await prepare(database, models)
    await service.run("turn")
    turn = await service.turn(context, "turn")
    assert turn.state == status
    assert turn.error_code == ("provider_rejected" if status == "failed" else "model_unknown")
    assert len(models.calls) == 1
    assert models.reads == []
