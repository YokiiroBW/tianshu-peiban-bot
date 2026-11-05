from dataclasses import replace
from datetime import UTC, datetime, timedelta
from importlib.resources import files

import pytest

from tianshu.conversation.dependencies import ConversationDependencies
from tianshu.conversation.generation import GenerationRequests, attempt_for
from tianshu.models.types import ModelCompletion, ModelEvent, ModelMessage, ModelPart, ModelReceipt, ModelRequest, ModelResult
from tianshu.runtime.errors import AppError
from tianshu.runtime.migrations import Migration, migrate
from tianshu.types import OriginKind, RequestBudget, ServiceClass, SourceRef, TrustedContext


class Sources:
    async def validate_persistence(self, *_args):
        return True


class Models:
    def __init__(self):
        self.calls = []
        self.observations = []
        self.reuses = []

    async def status(self, attempt_id, *, phase="observe"):
        assert phase == "pre_submit"
        if not self.calls:
            return None
        return ModelReceipt(attempt_id,"original","unknown","unknown",
            "route",1,"provider","model","responses","quota")

    async def require_receipt(self, attempt_id):
        value = await self.status(attempt_id, phase="pre_submit")
        if value is None:
            raise AppError("attempt_not_found", status=404)
        return value

    async def reusable_result(self, attempt_id, *, context, expected_request_digest=None, expected_data_generation=None):
        self.reuses.append((attempt_id, expected_data_generation))
        return await self.result(attempt_id, context=context)

    async def generate(self, request):
        self.calls.append(request)
        raise TimeoutError("response lost after provider submission")

    async def result(self, attempt_id, *, context):
        self.observations.append((attempt_id, context))
        return ModelResult((), ModelReceipt(attempt_id,"original","unknown","unknown",
            "route",1,"provider","model","responses","quota"))


async def prepare(database):
    await migrate(database, (Migration("0032_conversation", files("tianshu.conversation").joinpath("schema.sql").read_text()),
        Migration("0041_conversation_submission_confirmation", files("tianshu.conversation").joinpath("submission_confirmation.sql").read_text())))
    return TrustedContext("author","actor","person","conversation",("private",),("person",),1,OriginKind.ACTOR)


def request(key, context):
    return ModelRequest(key,attempt_for(key),"reflect",ServiceClass.ELASTIC,context,
        RequestBudget(datetime.now(UTC)+timedelta(minutes=2)),
        (ModelMessage("user",(ModelPart("text",text="actual experience"),)),),
        source_refs=(SourceRef("original",1),))


@pytest.mark.asyncio
async def test_interrupted_generation_only_reads_its_original_attempt(database):
    context = await prepare(database)
    models = Models()
    generations = GenerationRequests(database,Sources(),models)
    with pytest.raises(TimeoutError):
        await generations.run("same-material",request("same-material",context))
    result = await generations.run("same-material",request("same-material",context))
    assert result.receipt.status == "unknown"
    assert len(models.calls) == 1
    assert models.observations == [(attempt_for("same-material"),context)]
    frozen = await generations.request(attempt_for("same-material"),context)
    assert frozen.budget.trace_id == models.calls[0].budget.trace_id


@pytest.mark.asyncio
async def test_withdrawal_clears_frozen_generation_text_without_resubmission(database):
    context = await prepare(database)
    models = Models()
    generations = GenerationRequests(database,Sources(),models)
    with pytest.raises(TimeoutError):
        await generations.run("private-material",request("private-material",context))
    async with database.transaction() as connection:
        await ConversationDependencies().invalidate(connection,(SourceRef("original",1),),"forgotten")
    async with database.transaction(read_only=True) as connection:
        row = await (await connection.execute("SELECT request,state,attempt_id FROM conversation.generation_requests WHERE material_key='private-material'")).fetchone()
    assert row["request"] is None
    assert row["state"] == "invalidated"
    assert row["attempt_id"] == attempt_for("private-material")
    with pytest.raises(AppError,match="source_unavailable"):
        await generations.run("private-material",request("private-material",context))
    assert len(models.calls) == 1


@pytest.mark.asyncio
async def test_confirmed_absence_registers_the_frozen_original_request(database):
    context = await prepare(database)
    class BeforeAdmission(Models):
        async def status(self, attempt_id, *, phase="observe"):
            assert phase == "pre_submit"
            return None

        async def result(self, attempt_id, *, context):
            return None

    models = BeforeAdmission()
    generations = GenerationRequests(database,Sources(),models)
    for _ in range(2):
        with pytest.raises(TimeoutError):
            await generations.run("before-admission",request("before-admission",context))
    assert len(models.calls) == 2
    assert len({item.attempt_id for item in models.calls}) == 1
    assert len({item.budget.trace_id for item in models.calls}) == 1
    assert len({item.budget.deadline_at for item in models.calls}) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("execution,business,expired", [
    (None, None, True), (180.0, None, False),
    (180.0, "future", False), (180.0, "past", True),
])
async def test_original_admission_uses_business_expiry_and_preserves_legacy_budget(database, execution, business, expired):
    context = await prepare(database)
    now = datetime.now(UTC)
    original = replace(request("admission-timing", context),
        budget=RequestBudget(now-timedelta(minutes=1)), execution_timeout_seconds=execution,
        business_deadline_at=None if business is None else now+timedelta(minutes=1 if business == "future" else -1))

    class BeforeAdmission(Models):
        async def result(self, attempt_id, *, context):
            self.observations.append((attempt_id, context))
            return None

    models = BeforeAdmission()
    generations = GenerationRequests(database, Sources(), models)
    await generations.prepare(original.intent_id, original)
    async with database.transaction(read_only=True) as connection:
        saved = await (await connection.execute(
            "SELECT request FROM conversation.generation_requests WHERE attempt_id=%s", (original.attempt_id,))).fetchone()
    # A later candidate must not replace either the legacy timing or the new
    # explicit execution contract frozen under this original identity.
    later = replace(original, budget=RequestBudget(now+timedelta(minutes=5)),
        execution_timeout_seconds=900.0, business_deadline_at=None)
    with pytest.raises(AppError if expired else TimeoutError) as failure:
        await generations.run(original.intent_id, later)
    if expired:
        assert failure.value.code == "generation_request_expired"
    assert models.calls == ([] if expired else [original])
    async with database.transaction(read_only=True) as connection:
        current = await (await connection.execute(
            "SELECT request,state FROM conversation.generation_requests WHERE attempt_id=%s", (original.attempt_id,))).fetchone()
    assert current["request"] == saved["request"]
    assert current["state"] == ("expired" if expired else "claimed")


@pytest.mark.asyncio
async def test_expired_business_request_observes_unknown_original_without_submission(database):
    context = await prepare(database)
    original = replace(request("expired-observation", context), execution_timeout_seconds=180.0,
        business_deadline_at=datetime.now(UTC)-timedelta(minutes=1))
    class OriginalModels(Models):
        async def status(self, attempt_id, *, phase="observe"):
            return ModelReceipt(attempt_id,"original","unknown","unknown",
                "route",1,"provider","model","responses","quota")

    models = OriginalModels()
    generations = GenerationRequests(database, Sources(), models)
    await generations.prepare(original.intent_id, original)
    async with database.transaction() as connection:
        await connection.execute("UPDATE conversation.generation_requests SET state='claimed' WHERE attempt_id=%s", (original.attempt_id,))
    result = await generations.run(original.intent_id, original)
    assert result.receipt.status == "unknown"
    assert models.calls == [] and models.observations == [(original.attempt_id, context)]
    assert await generations.request(original.attempt_id, context) == original




@pytest.mark.asyncio
async def test_prepare_and_domain_pointer_commit_or_rollback_together_without_model_io(database):
    context = await prepare(database)
    models = Models()
    generations = GenerationRequests(database, Sources(), models)
    original = request("atomic-plan", context)
    async with database.transaction() as connection:
        await connection.execute("CREATE TABLE conversation.plan_fixture(attempt_id text)")
    with pytest.raises(RuntimeError, match="domain write failed"):
        async with database.transaction() as connection:
            frozen = await generations.prepare("atomic-plan", original, connection=connection)
            await connection.execute("INSERT INTO conversation.plan_fixture VALUES(%s)", (frozen.attempt_id,))
            raise RuntimeError("domain write failed")
    assert await generations.request(original.attempt_id, context) is None
    async with database.transaction() as connection:
        frozen = await generations.prepare("atomic-plan", original, connection=connection)
        await connection.execute("INSERT INTO conversation.plan_fixture VALUES(%s)", (frozen.attempt_id,))
    async with database.transaction(read_only=True) as connection:
        row = await (await connection.execute(
            "SELECT g.state,p.attempt_id FROM conversation.generation_requests g "
            "JOIN conversation.plan_fixture p USING(attempt_id)")).fetchone()
    assert row == {"state": "prepared", "attempt_id": original.attempt_id}
    assert frozen == original and models.calls == models.observations == []
    candidate = replace(original,attempt_id="unused-later-candidate")
    assert await generations.prepare("atomic-plan",candidate) == original


@pytest.mark.asyncio
async def test_current_epoch_observes_original_without_replacing_frozen_context(database):
    original = request("plan-old-epoch", replace(await prepare(database),
        scope_ids=("private", "actor"), audience=("person", "author")))
    models = Models()
    generations = GenerationRequests(database, Sources(), models)
    await generations.prepare(original.intent_id, original)
    reader = replace(original.context, access_epoch=2, origin_kind=OriginKind.TIMER,
        scope_ids=tuple(reversed(original.context.scope_ids)),
        audience=tuple(reversed(original.context.audience)))
    frozen = await generations.observe_request(original.attempt_id, observer=reader)
    result = await generations.read(original.attempt_id, frozen.context, observer=reader)
    assert result.receipt.status == "unknown" and models.calls == []
    observed = models.observations[0][1]
    assert observed.access_epoch == 2 and observed.scope_ids == original.context.scope_ids
    assert observed.audience == original.context.audience
    assert frozen == original
    assert await generations.request(original.attempt_id, original.context) == original


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("principal_id", "another-author"), ("actor_id", "another-actor"),
    ("person_id", "another-person"), ("conversation_id", "another-chat"),
    ("scope_ids", ("private", "additional")), ("scope_ids", ()),
    ("audience", ("another-reader",)), ("delivery_allowed", True),
    ("conversation_kind", "group"),
])
async def test_observation_refuses_a_changed_semantic_scene(database, field, value):
    original = request("fixed-scene", await prepare(database))
    models = Models()
    generations = GenerationRequests(database, Sources(), models)
    await generations.prepare(original.intent_id, original)
    reader = replace(original.context, access_epoch=2, **{field: value})
    with pytest.raises(AppError, match="generation_observation_context_mismatch"):
        await generations.observe_request(original.attempt_id, observer=reader)
    with pytest.raises(AppError, match="generation_observation_context_mismatch"):
        await generations.read(original.attempt_id, original.context, observer=reader)
    assert models.calls == models.observations == []


@pytest.mark.asyncio
async def test_observer_absence_never_submits_and_current_sources_gate_frozen_text(database):
    original = request("unregistered-plan", await prepare(database))
    class BeforeAdmission(Models):
        async def result(self, attempt_id, *, context):
            self.observations.append((attempt_id, context))
            return None

    sources, models = Sources(), BeforeAdmission()
    generations = GenerationRequests(database, sources, models)
    await generations.prepare(original.intent_id, original)
    reader = replace(original.context, access_epoch=2)
    assert await generations.run(original.intent_id, original, observer=reader) is None
    assert models.calls == []
    async def denied(*_args):
        return False
    sources.validate_persistence = denied
    with pytest.raises(AppError, match="source_unavailable"):
        await generations.observe_request(original.attempt_id, observer=reader)
    assert models.calls == []


@pytest.mark.asyncio
async def test_stream_returns_only_the_durable_complete_result_not_partial_events(database):
    context = await prepare(database)

    class StreamModels(Models):
        actual = None

        async def stream(self, frozen):
            self.calls.append(frozen)
            yield ModelEvent("text", text="not the persisted content")
            self.actual = ModelResult((ModelPart("text", text="persisted complete content"),),
                ModelReceipt(frozen.attempt_id, frozen.intent_id, "succeeded", "submitted", "r", 1, "p", "m", "openai.chat", "q"),
                completion=ModelCompletion("complete", ("stop",)))
            yield ModelEvent("receipt", receipt=self.actual.receipt)

        async def result(self, attempt_id, *, context):
            self.observations.append((attempt_id, context))
            return self.actual

    models = StreamModels()
    generations = GenerationRequests(database, Sources(), models)
    original = request("streamed-material", context)
    result = await generations.run(original.intent_id, original, stream=True)
    assert result.parts[0].text == "persisted complete content" and result.completion.state == "complete"
    assert await generations.run(original.intent_id, original, stream=True) == result
    assert models.calls == [original] and len(models.observations) == 3
    assert models.reuses == [(original.attempt_id, original.data_generation)]


@pytest.mark.asyncio
@pytest.mark.parametrize("known_result", [False, True])
async def test_interrupted_stream_observes_the_original_known_or_unknown_attempt(database, known_result):
    context = await prepare(database)

    class Interrupted(Models):
        actual = None

        async def stream(self, frozen):
            self.calls.append(frozen)
            yield ModelEvent("text", text='{"activity":null}')
            self.actual = ModelResult((), ModelReceipt(frozen.attempt_id, frozen.intent_id,
                "succeeded" if known_result else "unknown", "submitted", "r", 1, "p", "m", "openai.chat", "q"))
            # No terminal transport event: the original model ledger is still
            # authoritative on a later read, including if its response was lost.

        async def result(self, attempt_id, *, context):
            self.observations.append((attempt_id, context))
            return self.actual

    models = Interrupted()
    generations = GenerationRequests(database, Sources(), models)
    original = request("interrupted-stream", context)
    with pytest.raises(AppError, match="generation_stream_interrupted"):
        await generations.run(original.intent_id, original, stream=True)
    result = await generations.run(original.intent_id, original, stream=True)
    assert result.receipt.status == ("succeeded" if known_result else "unknown")
    assert models.calls == [original] and result.receipt.attempt_id == original.attempt_id
