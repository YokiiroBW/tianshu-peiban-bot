"""Configured services do not imply a bound voice, actor profile or full analysis."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from contextlib import asynccontextmanager

from tianshu.capabilities.contracts import Fault
from tianshu.capabilities.perception.service import Perception
from tianshu.capabilities.speech.service import Speech
from tianshu.capabilities.reactions.service import Reactions
from tianshu.models.types import ModelCompletion, ModelPart, ModelReceipt, ModelResult
from tianshu.types import MediaRef, OriginKind, TrustedContext
from tianshu.runtime.serialization import wire
from tianshu.capabilities.weather.service import WeatherService


@pytest.mark.asyncio
async def test_connected_fish_keeps_synthesis_unavailable_until_user_binds_voice():
    async def config_list(kind):
        return [{"id": "fish", "enabled": True, "configured": True}] if kind == "speech_connection" else []

    speech = Speech.__new__(Speech)
    speech.config = SimpleNamespace(list=AsyncMock(side_effect=config_list))
    availability = await speech.availability(SimpleNamespace(actor_id="actor"))
    assert not availability.can_execute
    assert availability.reason == "voice_profile_not_configured"


@pytest.mark.asyncio
async def test_truncated_multimodal_text_does_not_count_as_completed_understanding():
    context = TrustedContext("p", "actor", None, None, ("scope",), ("scope",), 1, OriginKind.ACTOR)
    result = ModelResult((ModelPart("text", text="不完整描述"),),
        ModelReceipt("a", "i", "succeeded", "submitted", "r", 1, "p", "m", "openai.chat", "q"),
        completion=ModelCompletion("incomplete", ("length",)))
    perception = Perception(SimpleNamespace(generate=AsyncMock(return_value=result)),
        SimpleNamespace(resolve=AsyncMock(return_value=(b"original", "image/png"))),
        SimpleNamespace(check=AsyncMock()), None)
    with pytest.raises(Fault) as incomplete:
        await perception.describe(context, MediaRef("original", "sha", "image/png", 8), question="看图")
    assert incomplete.value.code == "media_analysis_incomplete"
    assert incomplete.value.effect == "none"


@pytest.mark.asyncio
async def test_empty_reaction_library_does_not_spend_query_embedding():
    reaction = Reactions.__new__(Reactions)
    reaction.config = SimpleNamespace(list=AsyncMock(return_value=[]))
    reaction.authority = SimpleNamespace(check=AsyncMock())
    reaction.embed = AsyncMock()
    context = SimpleNamespace(actor_id="actor", scope_ids=("scope",))
    intent = SimpleNamespace(actor_id="actor", scope_ids=("scope",), meaning="开心", purpose="回应",
        attitude="温和", ironic=False, intensity=0.5, requested_pack_ids=())
    result = await reaction.search(context, intent)
    assert result["selected"] is None
    reaction.embed.assert_not_awaited()


@pytest.mark.asyncio
async def test_weather_reports_original_unknown_hold_without_new_read():
    context = TrustedContext("p", "actor", None, None, ("scope",), ("scope",), 1, OriginKind.ACTOR,
        permissions=frozenset({"weather.read", "life.read"}))
    physical = SimpleNamespace(status="unknown", hold_until=datetime.now(UTC) + timedelta(minutes=4))
    row = {"action": {"context": wire(context)}, "receipt": {"attempt_id": "original", "execution": "unknown"}}
    # Frozenset serialization order can differ between HTTP and worker processes.
    row["action"]["context"]["permissions"].reverse()

    @asynccontextmanager
    async def transaction(**_kwargs):
        yield SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(fetchall=AsyncMock(return_value=[row]))))

    service = WeatherService.__new__(WeatherService)
    service.runtime = SimpleNamespace(database=SimpleNamespace(transaction=transaction))
    service.models = SimpleNamespace(status=AsyncMock(return_value=physical))
    service.configuration = SimpleNamespace(get=AsyncMock(return_value={"revision": 1}))
    service.actions = SimpleNamespace(invoke=AsyncMock())
    with pytest.raises(Fault) as pending:
        await service._read(context, "current", {"location_id": "test"})
    assert pending.value.code == "weather_read_result_unknown"
    assert pending.value.details["attempt_id"] == "original"
    assert pending.value.details["retry_after"] == physical.hold_until.isoformat()
    service.actions.invoke.assert_not_awaited()
    physical.hold_until = datetime.now(UTC) - timedelta(seconds=1)
    await service._original_read_pending(context, "current", {"location_id": "test"})
    service.actions.invoke.assert_not_awaited()
