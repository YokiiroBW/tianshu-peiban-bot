"""Actual context preparation and PG turns distinguish rejection from lost model RPCs."""
import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from importlib.resources import files
from types import SimpleNamespace

import pytest

from tianshu.conversation.codec import document
from tianshu.conversation.context.assembly import ContextAssembler
from tianshu.conversation.service import ConversationService
from tianshu.models.types import ModelReceipt, ModelResult
from tianshu.models.budgets import BACKGROUND_INPUT_TOKENS
from tianshu.runtime.api import Page
from tianshu.runtime.errors import AppError
from tianshu.runtime.migrations import Migration, migrate
from tianshu.types import Coverage, OriginKind, Receipt, ServiceClass, TrustedContext


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
    def __init__(self):
        self.items = []

    async def enqueue(self, _connection, spec):
        self.items.append(spec)


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
@pytest.mark.parametrize("autonomous", [False, True])
async def test_only_autonomous_chat_gets_200000_input_and_keeps_its_original_output_budget(database, autonomous):
    models = Models()
    service, context, _ = await prepare(database, models)
    async def no_history(*_args, **_kwargs):
        return Page((), None, Coverage(True))
    service.messages = no_history
    async with database.transaction(read_only=True) as connection:
        row = await (await connection.execute("SELECT * FROM conversation.turns WHERE turn_id='turn'")).fetchone()
    row = {**row, "autonomous": autonomous, "input_parts": [{"kind": "text", "text": "x"}]}
    _, small, _ = await service._prepare_model_request(context, row, "small", [])
    target = BACKGROUND_INPUT_TOKENS if autonomous else 16000 - 2048
    text = "x" * (1 + target - small.token_usage.estimated_tokens)
    row = {**row, "input_parts": [{"kind": "text", "text": text}]}
    request, pack, _ = await service._prepare_model_request(context, row, "full", [])
    assert BACKGROUND_INPUT_TOKENS == 200000 and pack.token_usage.estimated_tokens == target
    assert pack.token_usage.limit_tokens == (200000 + 2048 if autonomous else 16000)
    assert pack.token_usage.reserved_output_tokens == request.max_output_tokens == 2048
    assert request.service_class == (ServiceClass.ELASTIC if autonomous else ServiceClass.INTERACTIVE)
    assert request.business_deadline_at == (row["deadline_at"] if autonomous else None)
    assert request.execution_timeout_seconds == 900 and request.estimated_input_tokens == target
    assert any(part.text == text for message in request.messages for part in message.parts)
    overflow = {**row, "input_parts": [{"kind": "text", "text": "x" * (target + 1)}]}
    with pytest.raises(AppError, match="context_mandatory_budget_exceeded"):
        await service._prepare_model_request(context, overflow, "oversize", [])
    assert models.calls == models.reads == []


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["budget", "permission", "deadline"])
async def test_local_refusal_finishes_before_model_submission(database, reason):
    models = Models()
    service, context, authority = await prepare(
        database, models, oversized=reason == "budget", blocked=reason == "permission")
    if reason == "deadline":
        async with database.transaction() as connection:
            # Unsolicited group participation has a real live-topic expiry.
            await connection.execute("UPDATE conversation.turns SET autonomous=true,deadline_at=now()-interval '1 second' WHERE turn_id='turn'")
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


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["queued", "running", "read_failed"])
async def test_ordinary_turn_lease_observes_the_original_queue_without_cancel_or_resubmit(database, outcome):
    models = Models(outcome)
    entered, finish = asyncio.Event(), asyncio.Event()
    cancellations = []

    async def generate(request):
        models.calls.append(request)
        entered.set()
        await finish.wait()
        return ModelResult((), ModelReceipt(request.attempt_id, request.intent_id, "succeeded", "submitted",
            "route", 1, "provider", "model", "openai.chat", "quota"))

    async def cancel(attempt_id):
        cancellations.append(attempt_id)

    models.generate, models.cancel = generate, cancel
    service, context, _ = await prepare(database, models)
    # The collector's old local timestamp cannot expire a direct human reply
    # before this turn gets its own preparation and provider execution time.
    async with database.transaction() as connection:
        await connection.execute("UPDATE conversation.turns SET deadline_at=now()-interval '1 hour' WHERE turn_id='turn'")
    worker = asyncio.create_task(service.run("turn"))
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        original = models.calls[0]
        assert original.execution_timeout_seconds == 900.0
        assert original.business_deadline_at is None and original.max_queue_wait is None
        assert original.budget.remaining() > 0
        async with database.transaction() as connection:
            await connection.execute("UPDATE conversation.turns SET lease_until=now()-interval '1 second',deadline_at=now()-interval '1 hour' WHERE turn_id='turn'")
        await service.expire("turn", original.attempt_id)
        async with database.transaction(read_only=True) as connection:
            row = await (await connection.execute(
                "SELECT state,model_attempt_id,lease_until FROM conversation.turns WHERE turn_id='turn'")).fetchone()
        assert row["state"] == "running" and row["model_attempt_id"] == original.attempt_id
        assert row["lease_until"] > datetime.now(UTC)
        assert models.reads == [(original.attempt_id, context)]
        assert cancellations == [] and len(models.calls) == 1
        observed_job = service.jobs.items[-1]
        assert observed_job.task == "conversation.expire"
        assert observed_job.payload == {"turn_id": "turn", "attempt_id": original.attempt_id}
    finally:
        finish.set()
        await worker
    assert (await service.turn(context, "turn")).state == "completed"
    assert cancellations == [] and len(models.calls) == 1


@pytest.mark.asyncio
async def test_worker_lease_preserves_terminal_settlement_grace_and_unknown_original(database):
    models = Models("failed")
    cancellations = []
    now = datetime.now(UTC)
    finished_at = now
    observed = models.observed

    def terminal(attempt_id):
        return replace(observed(attempt_id), receipt=replace(observed(attempt_id).receipt, finished_at=finished_at))

    async def cancel(attempt_id):
        cancellations.append(attempt_id)

    models.observed, models.cancel = terminal, cancel
    service, context, _ = await prepare(database, models)
    async with database.transaction() as connection:
        await connection.execute("UPDATE conversation.turns SET state='running',model_attempt_id='original',lease_until=now()-interval '1 second' WHERE turn_id='turn'")
    await service.expire("turn", "original")
    assert (await service.turn(context, "turn")).state == "running"
    finished_at = now-timedelta(minutes=2)
    async with database.transaction() as connection:
        await connection.execute("UPDATE conversation.turns SET lease_until=now()-interval '1 second' WHERE turn_id='turn'")
    await service.expire("turn", "original")
    settled = await service.turn(context, "turn")
    assert settled.state == "unknown" and settled.model_attempt_ids == ("original",)
    assert models.calls == cancellations == []


@pytest.mark.asyncio
async def test_autonomous_turn_keeps_its_actual_business_expiry(database):
    models = Models("queued")
    cancellations = []

    async def cancel(attempt_id):
        cancellations.append(attempt_id)

    models.cancel = cancel
    service, context, _ = await prepare(database, models)
    async with database.transaction() as connection:
        await connection.execute("UPDATE conversation.turns SET autonomous=true,state='running',model_attempt_id='original',deadline_at=now()-interval '1 second',lease_until=now()-interval '1 second' WHERE turn_id='turn'")
    await service.expire("turn", "original")
    assert (await service.turn(context, "turn")).state == "unknown"
    assert cancellations == ["original"] and models.calls == models.reads == []


@pytest.mark.asyncio
@pytest.mark.parametrize("autonomous", [False, True])
@pytest.mark.parametrize("explicit_window", [False, True])
async def test_tool_action_and_photo_window_only_inherit_actual_business_expiry(database, autonomous, explicit_window):
    models = Models()
    service, context, _ = await prepare(database, models)
    definition = SimpleNamespace(id="images.generate", model_tool={"name": "create_image"}, long_running=True)
    actions = []

    async def invoke(action, **_kwargs):
        actions.append(action)
        return Receipt("receipt", action.action_id, "original-action-attempt", "pending", "none", "not_requested")

    async def record_receipt(_context, _result):
        pass

    service.actions = SimpleNamespace(invoke=invoke)
    service._tool_receipt = record_receipt
    expiry = datetime.now(UTC)+timedelta(minutes=1 if autonomous else -1)
    async with database.transaction() as connection:
        row = await (await connection.execute(
            "UPDATE conversation.turns SET autonomous=%s,deadline_at=%s WHERE turn_id='turn' RETURNING *",
            (autonomous, expiry))).fetchone()
    window = {"due_at": datetime.now(UTC).isoformat(), "expires_at": (datetime.now(UTC)+timedelta(minutes=5)).isoformat()}
    await service._invoke(context, row, "photo", definition, {"window": window} if explicit_window else {}, ())
    assert len(actions) == 1
    action = actions[0]
    assert action.deadline_at == (expiry if autonomous else None)
    assert action.arguments["delivery_kind"] == "requested_delivery"
    if explicit_window:
        assert action.arguments["window"] == window
    else:
        assert action.arguments["window"]["due_at"] == row["received_at"].isoformat()
        assert action.arguments["window"].get("expires_at") == (expiry.isoformat() if autonomous else None)
    assert models.calls == models.reads == []


@pytest.mark.asyncio
@pytest.mark.parametrize("autonomous", [False, True])
async def test_tool_queue_observation_does_not_expire_an_ordinary_turn(database, autonomous):
    models = Models()
    service, _context, _ = await prepare(database, models)
    async with database.transaction() as connection:
        await connection.execute(
            "UPDATE conversation.turns SET state='waiting_external',autonomous=%s,deadline_at=now()-interval '1 minute' WHERE turn_id='turn'",
            (autonomous,))
    await service.reconcile("turn", 0)
    async with database.transaction(read_only=True) as connection:
        row = await (await connection.execute("SELECT state,error_code FROM conversation.turns WHERE turn_id='turn'")).fetchone()
    assert row["state"] == ("unknown" if autonomous else "waiting_external")
    assert row["error_code"] == ("action_wait_deadline" if autonomous else None)
    assert len(service.jobs.items) == (0 if autonomous else 1)
    if not autonomous:
        assert service.jobs.items[0].task == "conversation.reconcile"
        assert service.jobs.items[0].payload == {"turn_id": "turn", "sequence": 1}
    assert models.calls == models.reads == []


@pytest.mark.asyncio
async def test_autonomous_chat_request_keeps_the_live_topic_deadline(database):
    models = Models("unknown", return_receipt=True)
    service, _context, _ = await prepare(database, models)
    expiry = datetime.now(UTC)+timedelta(seconds=45)
    async with database.transaction() as connection:
        await connection.execute("UPDATE conversation.turns SET autonomous=true,deadline_at=%s WHERE turn_id='turn'", (expiry,))
    await service.run("turn")
    assert len(models.calls) == 1
    request = models.calls[0]
    assert request.execution_timeout_seconds == 900.0
    assert request.business_deadline_at == request.budget.deadline_at == expiry
