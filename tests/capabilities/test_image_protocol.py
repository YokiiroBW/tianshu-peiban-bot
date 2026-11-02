import unittest
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from importlib.resources import files
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
import pytest_asyncio

from tianshu.capabilities.actions import Actions
from tianshu.capabilities.catalog import Availability, Catalog
from tianshu.capabilities.contracts import Fault, canonical, model_json
from tianshu.capabilities.images.comfy import ComfyUI
from tianshu.capabilities.images.planning import ImagePlanError, image_plan
from tianshu.capabilities.images.provider import ImageResult
from tianshu.capabilities.images.service import Images
from tianshu.capabilities.images.workflow import Workflow
from tianshu.capabilities.images.engines import Engines
from tianshu.capabilities.external import ProviderError
from tianshu.capabilities.tracking import TrackedBroker
from tianshu.models.types import ModelCompletion, ModelPart, ModelReceipt, ModelResult
from tianshu.runtime.jobs import JobQueue
from tianshu.runtime.migrations import Migration, migrate
from tianshu.runtime.serialization import wire
from tianshu.types import Action, ArtifactRef, MediaRef, OriginKind, TrustedContext


PLAN = {"pose": "seated", "expression": "calm", "camera": "eye level", "light": "daylight"}


def plan_result(attempt_id="synthetic-attempt:plan", intent_id="synthetic-action:plan"):
    receipt = ModelReceipt(
        attempt_id, intent_id, "succeeded", "submitted", "synthetic-route", 1,
        "synthetic-provider", "synthetic-model", "gemini.generate", "synthetic-quota",
        finished_at=datetime.now(UTC),
    )
    return ModelResult(
        (ModelPart("text", text="```json\n" + canonical(PLAN) + "\n```"),), receipt,
        completion=ModelCompletion("complete", ("STOP",)),
    )

class ImageProtocol(unittest.IsolatedAsyncioTestCase):
    async def test_server_generated_handle_is_preserved(self):
        adapter=object.__new__(ComfyUI)
        adapter.json=AsyncMock(return_value={"prompt_id":"actual-provider-id"})
        result=await adapter.submit("requested-id",{"graph":{}})
        self.assertEqual(result.handle,"actual-provider-id")

    async def test_absent_after_cancel_is_still_unknown(self):
        adapter=object.__new__(ComfyUI)
        adapter.json=AsyncMock(return_value={"cancelled":True})
        adapter.poll=AsyncMock(side_effect=[ImageResult("queued"),ImageResult("unknown")])
        result=await adapter.cancel_pending("actual-provider-id",{})
        self.assertEqual(result.state,"unknown")

    def test_reference_identity_is_not_ignored_by_text_only_workflow(self):
        workflow=Workflow.standard("fixture.safetensors")
        intent={"character":"A","outfit":"B","pose":"C","scene":"D","camera":"E","references":[{"media_id":"artifact:fixture","mime_type":"image/png"}]}
        with self.assertRaises(ProviderError):Engines.compile({"engine":"comfyui"},intent,{"graph":workflow.graph,"bindings":workflow.bindings,"outputs":workflow.outputs})

    def test_model_json_accepts_only_one_complete_document(self):
        for raw in (canonical(PLAN), "\n```json\n" + canonical(PLAN) + "\n```\n",
                    "```\r\n" + canonical(PLAN) + "\r\n```"):
            with self.subTest(raw=raw):
                self.assertEqual(model_json(raw), PLAN)
        for raw in ("prefix " + canonical(PLAN), canonical(PLAN) + " tail", "{}{}",
                    "```json\n{}", "```python\n{}\n```", "```json\n{}\n```\n{}",
                    "```json\n{}\n```\n```json\n{}\n```", '{"a":1,"a":2}',
                    '{"a":NaN}', '{"a":Infinity}', '{"a":1e999}', None):
            with self.subTest(raw=raw), self.assertRaises(Fault):
                model_json(raw)

    def test_complete_fenced_plan_ignores_thought_and_enforces_domain_shape(self):
        result = plan_result()
        self.assertEqual(image_plan(replace(result, parts=(
            ModelPart("text", text="private reasoning", native={"thought": True}), *result.parts,
        ))), PLAN)
        invalid = ({}, {**PLAN, "light": None}, {**PLAN, "character": "replacement"},
                   {**PLAN, "negative_constraints": "no"}, {**PLAN, "negative_constraints": [1]}, [])
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ImagePlanError):
                image_plan(replace(result, parts=(ModelPart("text", text=canonical(value)),)))
        self.assertEqual(image_plan(replace(result, parts=(ModelPart(
            "text", text=canonical({**PLAN, "negative_constraints": ["blur"]}),
        ),)))["negative_constraints"], ["blur"])

    async def test_unfinished_or_invalid_planning_never_enters_image_stage(self):
        result = plan_result()
        cases = (
            replace(result, completion=ModelCompletion("incomplete", ("MAX_TOKENS",))),
            replace(result, completion=ModelCompletion()),
            replace(result, parts=(ModelPart("tool_call", name="unexpected"),)),
            replace(result, parts=(ModelPart("text", text="{} trailing"),)),
            replace(result, receipt=replace(result.receipt, status="unknown", dispatch="unknown")),
        )
        connection = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(
            fetchone=AsyncMock(return_value=None),
        )))

        @asynccontextmanager
        async def transaction(**kwargs):
            yield connection

        images = Images(SimpleNamespace(database=SimpleNamespace(transaction=transaction)),
                        SimpleNamespace(resource=Mock()), None, None,
                        SimpleNamespace(check=AsyncMock()))
        action = SimpleNamespace(action_id="synthetic-action", context=None, source_refs=())
        attempt = SimpleNamespace(attempt_id="synthetic-attempt")
        for value in cases:
            with self.subTest(completion=value.completion, parts=value.parts):
                with self.assertRaises(ImagePlanError) as failure:
                    image_plan(value)
                images.prepare = AsyncMock(side_effect=failure.exception)
                receipt = await images.execute(action, attempt)
                self.assertEqual(receipt.effect, "none")
                self.assertEqual(receipt.result["image_submission"], "not_started")
                self.assertEqual(receipt.result["planning"]["receipt"], wire(value.receipt))
                self.assertEqual(receipt.execution, "unknown" if value.receipt.status == "unknown" else "failed")
        images.broker.resource.assert_not_called()
        self.assertTrue(all("SELECT" in call.args[0] for call in connection.execute.await_args_list))


@pytest_asyncio.fixture
async def image_recovery(database, monkeypatch):
    await migrate(database, (Migration(
        "capabilities-0001", files("tianshu.capabilities").joinpath("schema.sql").read_text(),
    ),))
    context = TrustedContext(
        "synthetic-user", "synthetic-actor", "synthetic-person", "synthetic-chat",
        ("synthetic-private",), ("synthetic-private",), 1, OriginKind.HUMAN, frozenset({"*"}),
    )
    ledger, requests, resources = {}, [], []

    async def generate(request):
        requests.append(request)
        assert request.function in {"image.plan", "image.consistency"}
        result = plan_result(request.attempt_id, request.intent_id)
        if request.function == "image.consistency":
            # This fixture cannot establish scene truth; the bounded verification
            # still consumes the actual image once and reports unassessed fields.
            result = replace(result, parts=(ModelPart("text", text='{"checks":[]}'),))
        ledger[request.attempt_id] = result
        return result

    async def status(identifier):
        return ledger[identifier].receipt if identifier in ledger else None

    async def result(identifier, *, context):
        return ledger.get(identifier)

    @asynccontextmanager
    async def resource(request):
        resources.append(request)
        yield SimpleNamespace(submitted=AsyncMock(), finish=AsyncMock(), observe_provider=AsyncMock())

    broker = SimpleNamespace(generate=AsyncMock(side_effect=generate), status=AsyncMock(side_effect=status),
        result=AsyncMock(side_effect=result), resource=resource,
        generation_profile=AsyncMock(return_value={"route_id": "synthetic-route", "route_revision": 1,
            "protocol": "gemini.generate", "verified_capabilities": ["vision"],
            "max_output_tokens": 8192, "output_parameters": {}}))
    workflow = Workflow.standard("synthetic.safetensors")
    profile = {"id": context.actor_id, "actor_id": context.actor_id, "engine_id": "synthetic-engine",
               "workflow_id": "synthetic-workflow", "character": "fictional synthetic character"}
    engine = {"id": "synthetic-engine", "engine": "comfyui", "model": "synthetic-model",
              "enabled": True, "configured": True, "quota_group": "synthetic-quota",
              "account_id": "synthetic-account", "endpoint": "http://synthetic.invalid"}

    async def configuration(kind, identifier):
        return {"image_profile": profile, "image_engine": engine, "image_workflow": {
            "graph": workflow.graph, "bindings": workflow.bindings, "outputs": workflow.outputs,
        }}[kind]

    sources = SimpleNamespace(validate_persistence=AsyncMock(return_value=True))
    artifacts = SimpleNamespace(sources=sources, resolve=AsyncMock(), put=AsyncMock(return_value=ArtifactRef(
        "synthetic-artifact", MediaRef("artifact:synthetic", "artifact:synthetic", "image/png"),
    )))
    scene = {"revision": "current-scene", "local_time": "2026-11-02T12:00:00+08:00",
             "source_refs": [], "place": {"name": "synthetic room"}}
    life = SimpleNamespace(scene=AsyncMock(side_effect=lambda *args, **kwargs: dict(scene)))
    authority = SimpleNamespace(check=AsyncMock())
    images = Images(SimpleNamespace(database=database, get=configuration, secret=AsyncMock(return_value={})),
                    TrackedBroker(broker), artifacts, life, authority)
    catalog, jobs = Catalog(), JobQueue(database.config.dsn)
    catalog.register(images)
    images.availability = AsyncMock(return_value=Availability(True, True))
    owner = Actions(database, jobs, catalog, authority)
    owner.sources = sources
    owner.register_tasks()
    adapter = SimpleNamespace(
        submit=AsyncMock(return_value=ImageResult("queued", handle="synthetic-provider-handle")),
        poll=AsyncMock(return_value=ImageResult("completed", [{"filename": "synthetic.png"}])),
        image=AsyncMock(return_value=(b"image", 1, 1)), cancel_pending=AsyncMock(return_value=ImageResult("unknown")),
        close=AsyncMock(),
    )
    monkeypatch.setattr("tianshu.capabilities.images.service.ComfyUI", Mock(return_value=adapter))
    # Artifact persistence is separately covered; keep this regression about attempts and submission.
    images.store_images = AsyncMock(return_value=[artifacts.put.return_value])
    action = Action(str(uuid4()), str(uuid4()), "images.generate", context.actor_id, context,
                    {"delivery_kind": "create_only"}, OriginKind.HUMAN)
    pending = await owner.invoke(action, background=True)
    _, original, _ = await owner._load(action.action_id)
    original = replace(original, started_at=datetime.now(UTC))
    async with database.transaction() as connection:
        await connection.execute("UPDATE capabilities.actions SET attempt=%s::jsonb WHERE id=%s",
                                 (canonical(wire(original)), action.action_id))
    planning = plan_result(original.attempt_id + ":plan", action.action_id + ":plan")
    ledger[planning.receipt.attempt_id] = planning
    await owner.submitted(original)
    await owner.save(replace(pending, execution="unknown", effect="unknown", error_code="image_plan_invalid"),
                     original)
    try:
        yield SimpleNamespace(database=database, owner=owner, images=images, context=context,
                              action=action, original=original, planning=planning, broker=broker,
                              ledger=ledger, requests=requests, resources=resources, adapter=adapter,
                              scene=scene, sources=sources, engine=engine)
    finally:
        await owner.close()


@pytest.mark.asyncio
async def test_original_plan_reconciles_and_explicit_resume_uses_new_identity(image_recovery):
    state = image_recovery
    original_plan = state.ledger[state.planning.receipt.attempt_id]
    reconciled = await state.owner.reconcile(state.action.action_id, state.context)
    assert (reconciled.execution, reconciled.effect) == ("failed", "none")
    assert reconciled.attempt_id == state.original.attempt_id
    assert reconciled.result["image_submission"] == "not_started"
    assert reconciled.result["planning"]["receipt"] == wire(original_plan.receipt)
    assert not state.requests and not state.resources
    state.adapter.submit.assert_not_awaited()
    async with state.database.transaction(read_only=True) as connection:
        assert await (await connection.execute("SELECT * FROM capabilities.image_work")).fetchone() is None
    pending = await state.owner.resume(state.action.action_id, state.context, client_request_id="explicit-resume")
    assert pending.action_id == state.action.action_id and pending.attempt_id != state.original.attempt_id
    assert await state.owner.resume(state.action.action_id, state.context,
                                    client_request_id="explicit-resume") == pending
    assert not state.requests and not state.resources
    state.scene["revision"] = "fresh-scene-after-explicit-resume"
    done = await state.owner.run(state.action.action_id, mode="resume")
    assert (done.execution, done.effect) == ("succeeded", "confirmed"), done.error_code
    assert done.result["scene"]["revision"] == "fresh-scene-after-explicit-resume"
    plans = [request for request in state.requests if request.function == "image.plan"]
    checks = [request for request in state.requests if request.function == "image.consistency"]
    assert len(plans) == len(checks) == len(state.resources) == 1
    assert len(state.requests) == 2
    assert plans[0].attempt_id == pending.attempt_id + ":plan"
    assert plans[0].intent_id == state.action.action_id + ":plan"
    assert timedelta(minutes=9) < plans[0].budget.deadline_at - datetime.now(UTC) <= timedelta(minutes=10)
    assert plans[0].execution_timeout_seconds == 600 and plans[0].business_deadline_at is None
    assert checks[0].execution_timeout_seconds == 180 and checks[0].business_deadline_at is None
    assert state.resources[0].execution_timeout_seconds == 600 and state.resources[0].business_deadline_at is None
    assert state.resources[0].execution_start == "provider_running"
    assert checks[0].attempt_id == pending.attempt_id + ":consistency"
    assert checks[0].intent_id == state.action.action_id + ":consistency"
    assert any(part.kind == "image" and part.media == done.artifacts[0].media
               for message in checks[0].messages for part in message.parts)
    assert done.result["consistency"]["state"] == "unknown"
    assert done.result["consistency"]["regeneration_attempts"] == 0
    assert state.resources[0].attempt_id == pending.attempt_id
    assert state.ledger[state.planning.receipt.attempt_id] == original_plan
    assert await state.owner.resume(state.action.action_id, state.context,
                                    client_request_id="explicit-resume") == done
    assert [request.function for request in state.requests] == ["image.plan", "image.consistency"]
    assert len(state.resources) == 1
    state.adapter.submit.assert_awaited_once()
    async with state.database.transaction(read_only=True) as connection:
        rows = await (await connection.execute(
            "SELECT id,attempt,receipt FROM capabilities.attempts WHERE action_id=%s ORDER BY id",
            (state.action.action_id,),
        )).fetchall()
        jobs = await (await connection.execute("SELECT count(*) AS n FROM procrastinate_jobs")).fetchone()
    assert {row["id"] for row in rows} == {state.original.attempt_id, pending.attempt_id}
    assert jobs["n"] == 2
    old = next(row for row in rows if row["id"] == state.original.attempt_id)
    assert old["receipt"] == wire(reconciled) and old["attempt"]["dispatch"] == "submitted"


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid,code", [("control", "invalid_input"), ("range", "image_workflow_invalid")])
async def test_compile_rejection_after_settled_plan_has_no_image_effect(image_recovery, invalid, code):
    state = image_recovery
    if invalid == "control":
        state.scene["place"] = {"name": "synthetic room\x00"}
    else:
        state.engine["parameters"] = {"width": 9000}
    action = replace(state.action, action_id=str(uuid4()), intent_key=str(uuid4()))
    pending = await state.owner.invoke(action, background=True)
    done = await state.owner.run(action.action_id)
    assert (done.execution, done.effect, done.error_code) == ("failed", "none", code), done.error_code
    saved = await state.owner.get(action.action_id, state.context)
    assert saved["attempt"]["attempt_id"] == pending.attempt_id
    assert saved["attempt"]["dispatch"] == "submitted"
    assert len(state.requests) == 1 and state.requests[0].attempt_id == pending.attempt_id + ":plan"
    assert state.ledger[pending.attempt_id + ":plan"].receipt.status == "succeeded"
    assert not state.resources
    state.adapter.submit.assert_not_awaited()
    async with state.database.transaction(read_only=True) as connection:
        assert await (await connection.execute(
            "SELECT * FROM capabilities.image_work WHERE action_id=%s", (action.action_id,),
        )).fetchone() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("unsafe", ["unknown_plan", "image_request", "submitted_work", "revoked", "changed"])
async def test_image_resume_refusal_keeps_original_head_and_attempt(image_recovery, unsafe):
    state = image_recovery
    if unsafe == "unknown_plan":
        state.ledger[state.planning.receipt.attempt_id] = replace(state.planning, receipt=replace(
            state.planning.receipt, status="unknown", dispatch="unknown", finished_at=None,
        ))
    elif unsafe == "image_request":
        state.ledger[state.original.attempt_id] = replace(state.planning, receipt=replace(
            state.planning.receipt, attempt_id=state.original.attempt_id, intent_id=state.action.action_id,
        ))
    elif unsafe == "submitted_work":
        async with state.database.transaction() as connection:
            await connection.execute(
                "INSERT INTO capabilities.image_work(action_id,body,submitted,provider_handle) "
                "VALUES(%s,%s::jsonb,true,'original-image-handle')",
                (state.action.action_id, canonical({"engine": {"engine": "comfyui"}})),
            )
    elif unsafe == "revoked":
        state.sources.validate_persistence.return_value = False
    elif unsafe == "changed":
        original_status = state.broker.status.side_effect

        async def changed(identifier):
            if identifier.endswith(":plan"):
                async with state.database.transaction() as connection:
                    await connection.execute(
                        "UPDATE capabilities.actions SET result_revision=result_revision+1 WHERE id=%s",
                        (state.action.action_id,),
                    )
            return await original_status(identifier)

        state.broker.status.side_effect = changed
    before = await state.owner.get(state.action.action_id, state.context)
    with pytest.raises(Fault) as failure:
        await state.owner.resume(state.action.action_id, state.context, client_request_id="unsafe-resume")
    assert failure.value.code in {"image_plan_outcome_unknown", "image_submission_unverified",
                                  "image_resume_unverified", "source_revoked", "image_recovery_changed"}
    after = await state.owner.get(state.action.action_id, state.context)
    assert before["action"] == after["action"] and before["attempt"] == after["attempt"]
    assert before["receipt"] == after["receipt"]
    assert after["result_revision"] == before["result_revision"] + int(unsafe == "changed")
    assert not state.requests and not state.resources
    state.adapter.submit.assert_not_awaited()
    async with state.database.transaction(read_only=True) as connection:
        controls = await (await connection.execute("SELECT count(*) AS n FROM capabilities.action_controls")).fetchone()
        attempts = await (await connection.execute("SELECT count(*) AS n FROM capabilities.attempts")).fetchone()
        jobs = await (await connection.execute("SELECT count(*) AS n FROM procrastinate_jobs")).fetchone()
    assert controls["n"] == 0 and attempts["n"] == jobs["n"] == 1
