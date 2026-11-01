from dataclasses import replace
from datetime import UTC, datetime, timedelta
from importlib.resources import files

import pytest

from tianshu.conversation.dependencies import ConversationDependencies
from tianshu.conversation.generation import GenerationRequests, attempt_for
from tianshu.models.types import ModelMessage, ModelPart, ModelReceipt, ModelRequest, ModelResult
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

    async def generate(self, request):
        self.calls.append(request)
        raise TimeoutError("response lost after provider submission")

    async def result(self, attempt_id, *, context):
        self.observations.append((attempt_id, context))
        return ModelResult((), ModelReceipt(attempt_id,"original","unknown","unknown",
            "route",1,"provider","model","responses","quota"))


async def prepare(database):
    await migrate(database, (Migration("0032_conversation", files("tianshu.conversation").joinpath("schema.sql").read_text()),))
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
    (material,) = await generations.pending_materials(context, prefix="private-")
    assert material["request"] is None
    assert material["state"] == "invalidated"
    assert await generations.pending_materials(replace(context, person_id="another"), prefix="private-") == ()
    with pytest.raises(AppError,match="source_unavailable"):
        await generations.run("private-material",request("private-material",context))
    assert len(models.calls) == 1


@pytest.mark.asyncio
async def test_confirmed_absence_registers_the_frozen_original_request(database):
    context = await prepare(database)
    class BeforeAdmission(Models):
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
async def test_original_inventory_matches_scope_set_without_replacing_frozen_context(database):
    context = replace(await prepare(database), scope_ids=("private", "shared"))
    models = Models()
    generations = GenerationRequests(database, Sources(), models)
    original = request("organize:fixed", context)
    with pytest.raises(TimeoutError):
        await generations.run(original.intent_id, original)
    latest = replace(context, scope_ids=("shared", "private"), person_id="later-person",
                     conversation_id="later-conversation")
    (material,) = await generations.pending_materials(latest, prefix="organize:")
    assert material["attempt_id"] == original.attempt_id
    assert material["request"].context == original.context
    assert material["request"].budget.trace_id == original.budget.trace_id
    for scopes in ((), ("private",), ("private", "shared", "other")):
        assert await generations.pending_materials(replace(latest, scope_ids=scopes), prefix="organize:") == ()
    assert len(models.calls) == 1
    assert models.observations == []


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
