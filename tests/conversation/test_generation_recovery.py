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
