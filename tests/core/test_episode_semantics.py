"""Real PG result/input producers preserve audit, coverage and original evidence semantics."""

import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from importlib.resources import files
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from psycopg.types.json import Jsonb

from tianshu.capabilities.actions import Actions
from tianshu.capabilities.catalog import Catalog
from tianshu.capabilities.service import Operation as CapabilityOperation
from tianshu.capabilities.weather.service import WeatherService
from tianshu.channels.operations import Operation as ChannelOperation, probe_apis, technical_api
from tianshu.conversation.results import ResultConsumer
from tianshu.conversation.service import ConversationService
from tianshu.conversation.types import InputMessage, InputPart
from tianshu.models.types import ModelPart, ModelReceipt, ModelResult
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
        ),
    )
    catalog = Catalog()
    actions = Actions(core.database, core.memory.jobs, catalog, core.identity)
    life = SimpleNamespace(
        capability_result=AsyncMock(return_value=False), detached_result=AsyncMock()
    )
    growth = SimpleNamespace(changed=AsyncMock())
    result = ResultConsumer(
        core.database,
        core.identity,
        actions,
        core.memory,
        growth,
        SimpleNamespace(on_result=AsyncMock()),
        life,
        SimpleNamespace(on_action_result=AsyncMock(return_value=False)),
    )
    return result, catalog, life, growth


async def consume(core, result_consumer, context, capability, *, result=None, coverage=None):
    identifier = str(uuid4())
    action = Action(
        identifier, identifier, capability, context.actor_id, context, {}, OriginKind.HUMAN
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
        await conn.execute(
            "INSERT INTO capabilities.actions(id,intent,semantics,principal_id,kind,created_at,document,"
            "action,attempt,receipt,result_revision,result_observed_at) "
            "VALUES(%s,%s,'fixture',%s,%s,%s,'{}',%s,%s,%s,1,now())",
            (
                identifier,
                identifier,
                context.principal_id,
                capability,
                datetime.now(UTC).timestamp(),
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
    assert not technical_api("get_group_member_list") and not technical_api("get_msg")


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


class EmptyExtractor:
    def __init__(self):
        self.requests = []

    async def generate(self, request):
        self.requests.append(request)
        return ModelResult(
            (ModelPart("text", text='{"claims":[],"matters":[]}'),),
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
            ),
        )


async def test_weather_actual_json_receipt_reaches_extraction_without_media_missing(
    core, ordinary_users
):
    alice, _ = ordinary_users
    result_consumer, catalog, _, _ = await consumer(core)
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
    models = EmptyExtractor()
    core.steps.models = models
    await core.workers.extract(episode.episode_id, episode.sequence)
    current = (await core.memory.episodes_since(alice, None, 10)).items[0]
    assert current.extraction_state == "completed"
    assert current.extraction_coverage.missing == ()
    assert len(models.requests) == 1
    material = next(
        part.text
        for message in models.requests[0].messages
        for part in message.parts
        if part.text and part.text.startswith("材料 original_evidence")
    )
    captured = json.loads(material.split("\n", 1)[1])["sources"][0]
    assert captured["origin_kind"] == "tool_result"
    assert json.loads(captured["content"])["current"]["temperature"]["value"] == 22
