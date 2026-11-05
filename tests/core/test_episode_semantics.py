"""Real PG result/input producers preserve audit, coverage and original evidence semantics."""

import json
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime
from importlib.resources import files
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from psycopg.types.json import Jsonb

from tianshu.capabilities.actions import Actions
from tianshu.capabilities.catalog import CapabilityDefinition, Catalog
from tianshu.capabilities.service import Operation as CapabilityOperation
from tianshu.capabilities.weather.service import WeatherService
from tianshu.channels.operations import Operation as ChannelOperation, probe_apis, technical_api
from tianshu.conversation.results import ResultConsumer
from tianshu.conversation.service import ConversationService
from tianshu.conversation.types import InputMessage, InputPart
from tianshu.memory.extraction_contract import consolidation_example
from tianshu.models.ledger import submission_digest
from tianshu.models.results import validate_reusable_receipt
from tianshu.models.types import ModelCompletion, ModelPart, ModelReceipt, ModelResult
from tianshu.runtime.errors import AppError
from tianshu.runtime.migrations import Migration, migrate
from tianshu.runtime.serialization import wire
from tianshu.types import Action, Attempt, Coverage, MediaRef, OriginKind, Receipt


async def consumer(core):
    await migrate(
        core.database,
        tuple(
            Migration(name, files(package).joinpath("schema.sql").read_text())
            for name, package in (
                ("0032_conversation", "tianshu.conversation"),
                ("capabilities-0001", "tianshu.capabilities"),
            )
        ) + (
            Migration("rbg_life", files("tianshu.life").joinpath("schema.sql").read_text()),
            Migration("rbg_conversation_results", files("tianshu.conversation").joinpath("rbg_result_consumption.sql").read_text()),
        ),
    )
    catalog = Catalog()
    actions = Actions(core.database, core.memory.jobs, catalog, core.identity)
    async def detached(_context, _result, _action, *, on_complete):
        async with core.database.transaction() as connection:
            await on_complete(connection, "not_applicable", reason="no_detached_contact")
    life = SimpleNamespace(_activity_result=AsyncMock(), _commitment_result=AsyncMock(),
                           detached_result=AsyncMock(side_effect=detached))
    growth = SimpleNamespace(changed=AsyncMock())
    # Product binds its combined Growth/Diary callback here. ResultConsumer never
    # independently notifies Growth, including when the same receipt is replayed.
    core.memory.bind_episode_observer(growth.changed)
    result = ResultConsumer(
        core.database,
        core.identity,
        actions,
        core.memory,
        growth,
        SimpleNamespace(on_result=AsyncMock(), jobs=core.memory.jobs),
        life,
        SimpleNamespace(on_action_result=AsyncMock(return_value=False)),
    )
    return result, catalog, life, growth


async def consume(core, result_consumer, context, capability, *, result=None, coverage=None,
                  origin_kind=OriginKind.HUMAN):
    identifier = str(uuid4())
    action = Action(
        identifier, identifier, capability, context.actor_id, context, {}, origin_kind
    )
    attempt = Attempt(str(uuid4()), identifier, "submitted")
    receipt = Receipt(
        str(uuid4()),
        identifier,
        attempt.attempt_id,
        "succeeded",
        "confirmed",
        "not_requested",
        coverage=coverage,
        result=result or {},
    )
    async with core.database.transaction() as conn:
        data_generation = await core.memory.jobs.current_generation_on(conn)
        await conn.execute(
            "INSERT INTO capabilities.actions(id,intent,semantics,principal_id,kind,created_at,document,"
            "action,attempt,receipt,result_revision,result_observed_at) "
            "VALUES(%s,%s,'fixture',%s,%s,%s,%s,%s,%s,%s,1,now())",
            (
                identifier,
                identifier,
                context.principal_id,
                capability,
                datetime.now(UTC).timestamp(),
                json.dumps({"data_generation": data_generation}),
                Jsonb(wire(action)),
                Jsonb(wire(attempt)),
                Jsonb(wire(receipt)),
            ),
        )
    event = {
        "type": "capability.result",
        "version": 1,
        "context": wire(context),
        "action_id": identifier,
        "event_id": receipt.receipt_id,
        "result_revision": 1,
        "result_observed_at": datetime.now(UTC).isoformat(),
        "receipt": wire(receipt),
        "source_refs": [],
    }
    await result_consumer.consume(event)
    await result_consumer.consume(event)  # Original result replay remains idempotent.
    return action, receipt


async def test_technical_results_keep_audit_but_content_reads_remain_experiences(
    core, ordinary_users
):
    alice, _ = ordinary_users
    result_consumer, catalog, life, growth = await consumer(core)
    catalog.register(
        CapabilityOperation(None, "images", "profiles.save", permission="config.publish")
    )
    catalog.register(ChannelOperation(None, "api"))
    catalog.register(ChannelOperation(None, "probe"))
    cases = [("images.profiles.save", None, False), ("channels.probe", None, False)]
    cases += [
        ("channels.api", {"calls": [{"api_name": name, "state": "confirmed"}]}, False)
        for name in (
            *probe_apis("onebot.v11"),
            *probe_apis("telegram"),
            "get_credentials",
            "set_restart",
        )
    ]
    cases += [
        ("channels.api", {"calls": [{"api_name": name, "state": "confirmed"}]}, True)
        for name in (
            "get_msg",
            "get_forward_msg",
            "get_group_info",
            "get_group_member_list",
            "getChat",
        )
    ]
    audits, expected = [], []
    for capability, payload, meaningful in cases:
        action, receipt = await consume(core, result_consumer, alice, capability, result=payload)
        audits.append((action.action_id, receipt.receipt_id))
        if meaningful:
            expected.append(action.action_id)
    async with core.database.transaction(read_only=True) as conn:
        audit = await (
            await conn.execute(
                "SELECT id,receipt->>'receipt_id' AS receipt_id FROM capabilities.actions"
            )
        ).fetchall()
        episodes = await (
            await conn.execute("SELECT action_id FROM memory.episodes ORDER BY action_id")
        ).fetchall()
        consumed = await (
            await conn.execute("SELECT count(*) AS n FROM conversation.result_consumption")
        ).fetchone()
    assert {(row["id"], row["receipt_id"]) for row in audit} == set(audits)
    assert [row["action_id"] for row in episodes] == sorted(expected)
    assert consumed["n"] == len(cases)
    assert life.detached_result.await_count == growth.changed.await_count == len(expected)
    assert {call.kwargs["episode"].actual_result["capability_id"] for call in growth.changed.await_args_list} == {"channels.api"}
    assert all(call.kwargs["connection"] is not None for call in growth.changed.await_args_list)
    assert not technical_api("get_group_member_list") and not technical_api("get_msg")


async def test_internal_results_use_action_origin_and_keep_the_original_audit(core, ordinary_users):
    alice, _ = ordinary_users
    result_consumer, catalog, life, growth = await consumer(core)
    catalog.register(ChannelOperation(None, "read"))
    for capability in ("weather.current", "resources.assets.search"):
        catalog.register(SimpleNamespace(definition=CapabilityDefinition(
            capability, capability, "synthetic content read", {"type": "object"}, {"type": "object"}
        )))
    # Previously persisted ingress reads have a human authority context even
    # though their actual action source is observation. Internal reads remain
    # technical even if a malformed historical record also mislabeled the action.
    cases = [
        ("channels.read", OriginKind.OBSERVATION),
        ("channels.read", OriginKind.HUMAN),
        ("weather.current", OriginKind.OBSERVATION),
        ("resources.assets.search", OriginKind.OBSERVATION),
    ]
    audits = []
    for capability, origin in cases:
        action, actual = await consume(
            core, result_consumer, alice, capability, origin_kind=origin,
            result={"data": {"synthetic": "original lookup data"}},
        )
        audits.append((action.action_id, actual.receipt_id))
    async with core.database.transaction(read_only=True) as conn:
        audit = await (await conn.execute(
            "SELECT id,receipt->>'receipt_id' AS receipt_id FROM capabilities.actions"
        )).fetchall()
        episodes = await (await conn.execute("SELECT episode_id FROM memory.episodes")).fetchall()
        consumed = await (await conn.execute(
            "SELECT count(*) AS n FROM conversation.result_consumption"
        )).fetchone()
    assert {(row["id"], row["receipt_id"]) for row in audit} == set(audits)
    assert not episodes
    assert consumed["n"] == len(cases)
    life.detached_result.assert_not_awaited()
    growth.changed.assert_not_awaited()


async def test_received_input_coverage_is_distinct_from_media_understanding(core, ordinary_users):
    alice, _ = ordinary_users
    await consumer(core)
    image = core.files.put_bytes(b"unanalysed image bytes", media_type="image/png")
    ports = SimpleNamespace(actor=AsyncMock(return_value={"enabled": True}))
    growth = SimpleNamespace(changed=AsyncMock())
    service = ConversationService(
        core.database,
        None,
        core.memory.jobs,
        core.identity,
        core.sources,
        core.memory,
        ports,
        None,
        growth,
        None,
        None,
        None,
        None,
        None,
        SimpleNamespace(received=AsyncMock()),
    )
    now = datetime.now(UTC)
    message = InputMessage(
        "web",
        "received-parts",
        1,
        now,
        now,
        (InputPart("text", text="这是实际收到的原话。"), InputPart("image", media=image)),
        reply_required=False,
        conversation_kind=alice.conversation_kind,
    )
    result = await service.ingest(alice, message)
    await service._observe_turn(alice, result["input_id"])
    page = await core.memory.episodes_since(alice, None, 20)
    assert len(page.items) == 1
    episode = page.items[0]
    assert episode.coverage.complete and episode.coverage.total == 2
    assert episode.coverage.representation == "received_input_parts"
    assert not episode.coverage.missing
    assert episode.origin_kind == OriginKind.HUMAN and len(episode.source_refs) == 2
    # No producer can claim that receiving an image already analysed its contents.
    assert "consumption" not in episode.actual_result
    assert episode.actual_result["modalities"] == ["text", "image"]
    fallback = await core.memory.observe_episode(
        alice,
        {
            "event_id": "unspecified-coverage",
            "occurred_at": now,
            "source_refs": episode.source_refs,
            "origin_kind": "human",
        },
    )
    assert not fallback.coverage.complete and tuple(fallback.coverage.missing) == (
        "coverage_unknown",
    )


@pytest.mark.parametrize(
    "mime", ["application/json", "Application/Problem+Json; charset=utf-8", "application/xml"]
)
async def test_structured_utf8_capture_preserves_the_original_representation(
    core, ordinary_users, mime
):
    alice, _ = ordinary_users
    raw = (
        '{ "事实": "原始文本", "spaces": true }\n'
        if "xml" not in mime
        else "<事实>原始文本</事实>\n"
    )
    ref = await core.sources.capture(
        alice, locator={"kind": "structured", "mime": mime}, raw=raw.encode("utf-8"), mime_type=mime
    )
    read = await core.sources.read(alice, ref, {"max_characters": 2000})
    assert read["content"] == raw
    assert read["origin_kind"] == "tool_result"
    assert read["media"]["mime_type"] == mime
    assert read["coverage"].complete


async def test_non_utf8_structured_capture_keeps_bytes_without_claiming_readable_text(
    core, ordinary_users
):
    alice, _ = ordinary_users
    originals = (
        ("application/xml; charset=utf-16", "<事实>原始文本</事实>".encode("utf-16")),
        ("application/problem+json", b'{"text":"\xff"}'),
    )
    for mime, raw in originals:
        ref = await core.sources.capture(
            alice, locator={"kind": "structured-original", "mime": mime}, raw=raw, mime_type=mime
        )
        read = await core.sources.read(alice, ref, {"max_characters": 2000})
        assert read["content"] is None
        assert read["origin_kind"] == "tool_result"
        assert await core.sources.resolve(alice, MediaRef(**read["media"])) == (raw, mime)
    # The established text/* contract still rejects invalid UTF8.
    with pytest.raises(UnicodeDecodeError):
        await core.sources.capture(
            alice, locator={"kind": "strict-text"}, raw=b"\xff", mime_type="text/plain"
        )


class Permit:
    async def submitted(self):
        pass

    async def finish(self, **_kwargs):
        pass


class WeatherModels:
    @asynccontextmanager
    async def resource(self, _request):
        yield Permit()


class EmptyConsolidator:
    def __init__(self):
        self.requests = []
        self.submissions = []
        self.results = {}
        self.frozen_requests = {}
        self.status_lookups = []
        self.reuses = []

    async def generation_profile(self, request):
        return {"route_id":"fixture","route_revision":1,"protocol":"openai.chat","model":"fixture",
            "output_parameters":{"max_tokens":request.max_output_tokens}}

    async def generate(self, request):
        self.requests.append(request)
        material = json.loads(request.messages[0].parts[0].text)
        if material.get("model_protocol") == "memory.proposal.v2":
            proposal = {"targets": [{"subject_id": target["subject_id"], "status": "no_change",
                "operations": [], "unresolved": [], "reason": "没有新增事实"}
                for target in material["targets"]], "needs_context": []}
        else:
            proposal = consolidation_example({"batch_id": material["batch_id"], "targets": {
                target["subject_id"]: target for target in material["targets"]}})
        result = ModelResult(
            (ModelPart("text", text=json.dumps(proposal)),),
            ModelReceipt(
                request.attempt_id,
                request.intent_id,
                "succeeded",
                "submitted",
                "fixture",
                1,
                "fixture",
                "fixture",
                "openai.chat",
                "fixture",
                finished_at=datetime.now(UTC),
                request_digest=submission_digest(request),
                data_generation=request.data_generation,
            ),
            completion=ModelCompletion("complete", ("stop",)),
        )
        self.frozen_requests[request.attempt_id] = request
        self.results[request.attempt_id] = result
        return result

    async def submit(self, request, *, completion=None):
        previous = self.frozen_requests.get(request.attempt_id)
        if previous is not None:
            assert submission_digest(previous) == submission_digest(request)
            return self.results[request.attempt_id].receipt
        assert completion is not None and completion.task == "memory.consolidate"
        assert completion.data_generation == request.data_generation
        self.submissions.append((request, completion))
        return (await self.generate(request)).receipt

    async def status(self, attempt_id, *, phase="observe"):
        assert phase in {"pre_submit", "observe"}
        self.status_lookups.append((attempt_id, phase))
        result = self.results.get(attempt_id)
        return result.receipt if result is not None else None

    async def result(self, attempt_id, *, context):
        request = self.frozen_requests.get(attempt_id)
        if request is None:
            return None
        assert context == request.context
        return self.results[attempt_id]

    async def reusable_result(self, attempt_id, *, context, expected_request_digest=None,
                              expected_data_generation=None):
        result = await self.result(attempt_id, context=context)
        if result is None:
            raise AppError("model_result_unavailable", status=409)
        validate_reusable_receipt(result.receipt, attempt_id,
            expected_request_digest=expected_request_digest, expected_data_generation=expected_data_generation)
        self.reuses.append((attempt_id, expected_data_generation))
        return result


async def test_weather_actual_json_receipt_reaches_extraction_without_media_missing(
    core, ordinary_users
):
    alice, _ = ordinary_users
    result_consumer, catalog, _, _ = await consumer(core)
    models = EmptyConsolidator()
    core.steps.models = models
    policy = replace(core.memory.consolidation.policy, quiet_seconds=0, max_wait_seconds=0,
                     max_input_tokens=64000, context_messages=0)
    core.memory.consolidation.policy = core.memory.consolidation.materials.policy = policy
    configuration = SimpleNamespace(
        get=AsyncMock(
            return_value={
                "enabled": True,
                "configured": True,
                "host": "tenant.re.qweatherapi.com",
                "quota_group": "fixture",
            }
        ),
        secret=AsyncMock(return_value="synthetic-local-fixture"),
    )
    weather = WeatherService(
        SimpleNamespace(),
        authority=core.identity,
        sources=core.sources,
        configuration=configuration,
        models=WeatherModels(),
    )
    catalog.register(weather.handlers[1])
    weather.request = AsyncMock(
        side_effect=[
            {
                "location": [
                    {
                        "id": "city",
                        "name": "地点",
                        "adm1": "省",
                        "adm2": "市",
                        "country": "CN",
                        "tz": "Asia/Shanghai",
                        "utcOffset": "+08:00",
                        "lat": "31.2",
                        "lon": "121.5",
                    }
                ]
            },
            {
                "temperature": {"unit": "°C", "value": 22},
                "feelsLike": {"unit": "°C", "value": 23},
                "condition": {"text": "晴", "code": "100"},
                "wind": {"scale": 2},
                "metadata": {"attributions": ["QWeather"]},
            },
        ]
    )
    action = Action(
        "weather-fixture",
        "weather-fixture",
        "weather.current",
        alice.actor_id,
        alice,
        {"location_id": "city"},
        OriginKind.HUMAN,
    )
    result = await weather.execute("current", action, Attempt("weather-attempt", action.action_id))
    assert result.coverage == Coverage(True, total=1, representation="weather_current_response")
    assert result.result["observed_at"] is None
    assert result.result["fetched_at"] and not result.result["stale"]
    original, _ = await consume(
        core,
        result_consumer,
        alice,
        "weather.current",
        result=result.result,
        coverage=result.coverage,
    )
    episode = (await core.memory.episodes_since(alice, None, 10)).items[0]
    assert episode.episode_id == "action:" + original.action_id and episode.coverage.complete
    async with core.database.transaction(read_only=True) as connection:
        row = await (await connection.execute(
            "SELECT consolidation_partition_id FROM memory.episodes WHERE episode_id=%s",
            (episode.episode_id,),
        )).fetchone()
    await core.workers.consolidate(row["consolidation_partition_id"])
    current = (await core.memory.episodes_since(alice, None, 10)).items[0]
    assert current.extraction_state == "completed"
    assert current.extraction_coverage.missing == ()
    assert len(models.requests) == 1
    assert len(models.submissions) == 1 and models.submissions[0][0] == models.requests[0]
    assert (models.requests[0].attempt_id, "pre_submit") in models.status_lookups
    assert (models.requests[0].attempt_id, models.requests[0].data_generation) in models.reuses
    material = json.loads(models.requests[0].messages[0].parts[0].text)
    originals = [item for item in material["materials"] if item["role"] == "new"]
    assert len(originals) == 1
    captured = originals[0]
    assert captured["origin_kind"] == "tool_result"
    assert captured["speaker_id"] is None
    assert json.loads(captured["text"])["current"]["temperature"]["value"] == 22
    assert "media_missing" not in material["coverage"].get("missing", ())
    assert all(captured["id"] not in target["new_material_ids"] for target in material["targets"]
               if target["subject_id"] == alice.person_id)
    assert current.origin_kind == OriginKind.TOOL_RESULT and current.speaker_id is None
    assert not (await core.claims.list(alice)).items
