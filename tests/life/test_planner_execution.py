from dataclasses import replace
from datetime import UTC, datetime
from importlib.resources import files
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from tianshu.capabilities.catalog import Availability, Catalog, CapabilityDefinition
from tianshu.conversation.context.assembly import ContextAssembler
from tianshu.runtime.serialization import wire
from tianshu.life import planner
from tianshu.life.service import LifeService
from tianshu.life.worker import LifeWorker
from tianshu.models.types import ModelMessage, ModelPart, ModelReceipt, ModelRequest, ModelResult
from tianshu.runtime.commands import CommandStore
from tianshu.runtime.errors import AppError
from tianshu.runtime.jobs import JobQueue
from tianshu.runtime.migrations import Migration, migrate
from tianshu.types import OriginKind, Receipt, TrustedContext, RequestBudget, ServiceClass, SourceRef
from tianshu.conversation.generation import attempt_for


class Identity:
    async def check(self, *_args):
        pass

    async def check_many(self, _context, groups):
        return tuple(True for _ in groups)


class Sources:
    async def validate_persistence(self, *_args):
        return True


class Companion:
    async def actor(self, _context):
        return {"enabled": True, "revision": 1, "persona_id": None}

    async def affect(self, _context):
        return {"regulation": {}, "source_refs": ()}


class ReadPage:
    definition = CapabilityDefinition(
        "content.read",
        "Read an actual page",
        "Read selected URL",
        {
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
            "additionalProperties": False,
        },
        {"type": "object"},
        model_tool={"name": "content_read"},
    )

    async def availability(self, _context):
        return Availability(True, True)


class Models:
    def __init__(self, output, *, lose_response=False):
        self.output, self.lose_response = output, lose_response
        self.calls, self.observations = [], []
        self.actual = None

    async def generate(self, request):
        self.calls.append(request)
        self.actual = ModelResult(
            (ModelPart("text", text=self.output),),
            ModelReceipt(
                request.attempt_id,
                request.intent_id,
                "unknown" if self.lose_response else "succeeded",
                "submitted",
                "fixture-route",
                1,
                "fixture-provider",
                "fixture-model",
                "native",
                "quota",
            ),
        )
        if self.lose_response:
            raise TimeoutError("fixture response lost after submission")
        return self.actual

    async def result(self, attempt_id, *, context):
        self.observations.append((attempt_id, context))
        assert self.actual is None or self.actual.receipt.attempt_id == attempt_id
        return self.actual


class Actions:
    def __init__(self):
        self.calls = []

    async def invoke(self, action, **kwargs):
        self.calls.append((action, kwargs))
        self.receipt = Receipt(
            str(uuid4()), action.action_id, str(uuid4()), "pending", "none", "not_requested"
        )
        return self.receipt

    async def get(self, action_id, _context):
        action = self.calls[-1][0]
        assert action_id == action.action_id
        return {
            "action": wire(action),
            "receipt": wire(self.receipt),
            "result_revision": 0,
            "result_observed_at": datetime.now(UTC),
        }


async def setup(database, output, *, lose_response=False, capability_id="content.read"):
    await migrate(
        database,
        (
            Migration("0031_life", files("tianshu.life").joinpath("schema.sql").read_text()),
            Migration(
                "0032_conversation",
                files("tianshu.conversation").joinpath("schema.sql").read_text(),
            ),
        ),
    )
    identity, sources, jobs, actions = (
        Identity(),
        Sources(),
        JobQueue(database.config.dsn),
        Actions(),
    )
    context = TrustedContext(
        "author",
        "actor",
        None,
        None,
        ("actor-scope",),
        ("actor-scope",),
        1,
        OriginKind.ACTOR,
        frozenset({"*"}),
    )
    life = LifeService(
        database,
        CommandStore(database),
        jobs,
        identity,
        Companion(),
        None,
        sources=sources,
        memory=None,
    )
    catalog = Catalog()
    handler = ReadPage()
    handler.definition = replace(handler.definition, id=capability_id)
    catalog.register(handler)
    models = Models(output, lose_response=lose_response)
    worker = LifeWorker(
        life, models, ContextAssembler(identity, None, None), catalog, actions, None
    )
    jobs.register("life.activity_step", worker.activity_step)
    jobs.register("life.plan", worker.plan)
    jobs.register("life.activity_reconcile", worker.reconcile_activity)
    async with database.transaction() as connection:
        await connection.execute("INSERT INTO life.actor_state(actor_id) VALUES ('actor')")
        await connection.execute(
            "INSERT INTO life.routines(actor_id,context,next_due_at) VALUES ('actor',%s,now())",
            (json.dumps(wire(context)),),
        )
        await connection.execute(
            "INSERT INTO life.plan_watermarks(actor_id,change_watermark) VALUES ('actor','rhythm:fixture')"
        )
    return SimpleNamespace(
        worker=worker, models=models, actions=actions, context=context, jobs=jobs
    )


async def mark(database):
    async with database.transaction(read_only=True) as connection:
        return await (
            await connection.execute("SELECT * FROM life.plan_watermarks WHERE actor_id='actor'")
        ).fetchone()


def chosen_activity(capability_id="content.read"):
    return json.dumps(
        {
            "activity": {
                "title": "Read the selected page",
                "kind": "reading",
                "goal_id": None,
                "capability_id": capability_id,
                "arguments": {"url": "https://example.test/page"},
                "duration_minutes": 30,
            },
            "reason": "A feasible activity with a concrete input",
        }
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("capability_id", ["content.read", "plugin.trailbook.observe"])
async def test_native_plan_creates_activity_then_dispatches_that_exact_action(
    database, capability_id
):
    fixture = await setup(database, chosen_activity(capability_id), capability_id=capability_id)
    await fixture.worker.plan("actor", 1, wire(fixture.context))
    request = fixture.models.calls[0]
    assert planner.TASK in request.system
    assert planner.frozen_material(request)["routine"]["wake_at"] == "08:00"
    async with database.transaction(read_only=True) as connection:
        rows = await (await connection.execute("SELECT * FROM life.activities")).fetchall()
        queued = await (
            await connection.execute(
                "SELECT count(*) AS n FROM procrastinate_jobs WHERE task_name='life.activity_step'"
            )
        ).fetchone()
    assert len(rows) == 1 and rows[0]["state"] == "planned" and queued["n"] == 1
    assert (await mark(database))["generation_consumed"] is True
    await fixture.worker.activity_step(rows[0]["id"], 1, wire(fixture.context))
    action, dispatch = fixture.actions.calls[0]
    assert action.context == fixture.context and action.activity_id == rows[0]["id"]
    assert action.arguments == {"url": "https://example.test/page"}
    assert action.capability_id == capability_id and dispatch["background"] is True
    async with database.transaction(read_only=True) as connection:
        row = await (
            await connection.execute("SELECT * FROM life.activities WHERE id=%s", (rows[0]["id"],))
        ).fetchone()
    assert row["state"] == "running" and row["waiting_reason"] == "capability_running"
    assert row["action_id"] == action.action_id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "output,reason",
    [
        ("A quiet moment might be pleasant today.", "planner_output_invalid"),
        ('{"activity":null,"reason":"No feasible activity now"}', "no_meaningful_activity"),
    ],
)
async def test_successful_model_invalid_text_and_legitimate_null_have_distinct_states(
    database, output, reason
):
    fixture = await setup(database, output)
    await fixture.worker.plan("actor", 1, wire(fixture.context))
    current = await mark(database)
    assert current["reason"] == reason and current["generation_consumed"] is True
    assert fixture.models.actual.receipt.status == "succeeded"
    await fixture.worker.plan("actor", 1, wire(fixture.context))
    assert len(fixture.models.calls) == 1
    async with database.transaction(read_only=True) as connection:
        assert not await (await connection.execute("SELECT id FROM life.activities")).fetchall()


@pytest.mark.asyncio
async def test_lost_response_and_refreshed_clock_only_observe_original_attempt(database):
    fixture = await setup(database, chosen_activity(), lose_response=True)
    with pytest.raises(TimeoutError):
        await fixture.worker.plan("actor", 1, wire(fixture.context))
    original = fixture.models.calls[0]
    key = (await mark(database))["generation_key"]
    restarted = LifeWorker(
        fixture.worker.life,
        fixture.models,
        fixture.worker.assembler,
        fixture.worker.catalog,
        fixture.actions,
        None,
    )
    await restarted.plan("actor", 1, wire(fixture.context))
    assert (await mark(database))["reason"] == "planner_unknown"
    refreshed = replace(fixture.context, access_epoch=2)
    await restarted.plan("actor", 1, wire(refreshed))
    current = await mark(database)
    assert current["generation_key"] == key and current["generation_consumed"] is False
    assert current["reason"] == "planner_original_context_changed"
    assert len(fixture.models.calls) == 1 and not fixture.actions.calls
    assert all(attempt == original.attempt_id for attempt, _ in fixture.models.observations)
    async with database.transaction(read_only=True) as connection:
        rows = await (
            await connection.execute(
                "SELECT request,attempt_id FROM conversation.generation_requests"
            )
        ).fetchall()
    assert len(rows) == 1 and rows[0]["attempt_id"] == original.attempt_id
    assert rows[0]["request"]["context"]["access_epoch"] == 1
    assert rows[0]["request"]["budget"]["deadline_at"] == original.budget.deadline_at.isoformat()


@pytest.mark.asyncio
async def test_pre_upgrade_finished_text_is_rejected_without_reissuing_the_model(database):
    from datetime import timedelta

    fixture = await setup(database, "A quiet moment might be pleasant today.")
    key = "pre-upgrade-plan"
    old = ModelRequest(
        key,
        attempt_for(key),
        "life.plan",
        ServiceClass.ELASTIC,
        fixture.context,
        RequestBudget(datetime.now(UTC) + timedelta(minutes=2)),
        (ModelMessage("user", (ModelPart("text", text="Choose a meaningful next activity."),)),),
    )
    await fixture.worker.generations.run(key, old)
    async with database.transaction() as connection:
        await connection.execute("UPDATE life.plan_watermarks SET generation_key=%s", (key,))
    await fixture.worker.plan("actor", 1, wire(fixture.context))
    assert len(fixture.models.calls) == 1 and not fixture.actions.calls
    assert fixture.models.actual.receipt.status == "succeeded"
    current = await mark(database)
    assert current["reason"] == "planner_original_contract_missing"
    assert current["generation_key"] == key and current["generation_consumed"] is True


@pytest.mark.asyncio
async def test_crash_before_frozen_request_does_not_submit_a_replacement(database):
    fixture = await setup(database, chosen_activity())
    key = "not-registered-before-crash"
    async with database.transaction() as connection:
        await connection.execute("UPDATE life.plan_watermarks SET generation_key=%s", (key,))
    await fixture.worker.plan("actor", 1, wire(fixture.context))
    assert not fixture.models.calls and not fixture.actions.calls
    assert fixture.models.observations == [(attempt_for(key), fixture.context)]
    current = await mark(database)
    assert current["reason"] == "planner_original_request_missing"
    assert current["generation_consumed"] is False


@pytest.mark.asyncio
async def test_recovery_does_not_enqueue_blocked_routine_context(database):
    fixture = await setup(database, chosen_activity())

    async def denied(*_args):
        raise AppError("access_changed", status=409)

    fixture.worker.life.identity.check = denied
    await fixture.worker.recover()
    async with database.transaction(read_only=True) as connection:
        assert not await (
            await connection.execute(
                "SELECT id FROM procrastinate_jobs WHERE task_name='life.plan'"
            )
        ).fetchall()
    assert not fixture.models.calls and not fixture.actions.calls


@pytest.mark.asyncio
async def test_imagined_completion_registers_actual_actor_original_in_same_transaction(database):
    fixture = await setup(database, '{}')
    original = SourceRef("imagined-completion", 1)
    fixture.worker.life.sources.record_event = AsyncMock(return_value=(original,))
    fixture.worker.life.memory = SimpleNamespace(observe_episode=AsyncMock())
    fixture.worker.life.signal = AsyncMock()
    async with database.transaction() as connection:
        await connection.execute("INSERT INTO life.activities(id,actor_id,title,kind,state,waiting_reason,next_due_at,scope_ids) "
            "VALUES ('imagination','actor','在房间整理书架','room','running','fictional_activity',now()-interval '1 second',ARRAY['actor-scope'])")
    await fixture.worker.activity_step("imagination", 1, wire(fixture.context))
    call = fixture.worker.life.sources.record_event.call_args
    assert call.kwargs["origin_kind"] == OriginKind.ACTOR
    assert call.kwargs["connection"] is not None
    assert call.kwargs["parts"][0].metadata["imagined"] is True
    episode = fixture.worker.life.memory.observe_episode.call_args.args[1]
    assert original in episode["source_refs"]
    assert episode["origin_kind"] == "actor"
    assert episode["actual_result"]["experience_kind"] == "imagined"
    assert episode["actual_result"]["software_action"] is False
    assert "execution" not in episode["actual_result"]
