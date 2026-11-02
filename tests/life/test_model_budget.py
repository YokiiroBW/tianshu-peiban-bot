from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tianshu.life.model_budget import generation_request
from tianshu.runtime.errors import AppError
from test_planner_contract import context


@pytest.mark.asyncio
@pytest.mark.parametrize("model,protocol,expected,timeout", [
    ("deepseek-v4.1-flash", "openai.chat", 16384, 300),
    ("gemini-3.8-flash-high", "openai.chat", 32768, 600),
    ("gemini-3.8-flash-high", "gemini.generate", 32768, 600),
    ("gemini-4", "gemini.generate", 32768, 600),
    ("unfamiliar-configured-model", "openai.responses", 16384, 300),
])
async def test_budget_uses_actual_profile_and_pins_existing_route(model, protocol, expected, timeout):
    models = SimpleNamespace(generation_profile=AsyncMock(return_value={
        "model": model, "protocol": protocol, "route_id": "actual", "route_revision": 7,
        "verified_capabilities": ["text", "stream"], "max_output_tokens": 16384, "output_parameters": {}}))
    request = await generation_request(models, context(), key="original", attempt_id="original-id", function="life.plan")
    assert request.max_output_tokens == expected and request.route_id == "actual" and request.route_revision == 7
    assert timeout - 3 < request.budget.remaining() <= timeout
    assert request.execution_timeout_seconds == timeout and request.max_queue_wait is None
    assert request.business_deadline_at is None
    assert request.required_capabilities == {"text", "stream"}
    assert request.budget.deadline_at > datetime.now(UTC)
    assert models.generation_profile.call_args.args[0].required_capabilities == frozenset()


@pytest.mark.asyncio
async def test_actual_life_boundary_is_separate_from_its_model_execution_budget():
    models = SimpleNamespace(generation_profile=AsyncMock(return_value={
        "model": "deepseek-v4.1-flash", "protocol": "openai.chat", "route_id": "actual", "route_revision": 7,
        "verified_capabilities": ["text", "stream"], "max_output_tokens": 16384, "output_parameters": {}}))
    boundary = datetime.now(UTC) + timedelta(hours=1)
    request = await generation_request(models, context(), key="original", attempt_id="original-id",
        function="life.plan", business_deadline_at=boundary)
    assert request.business_deadline_at == boundary and request.execution_timeout_seconds == 300
    assert request.budget.deadline_at < boundary and request.max_queue_wait is None


@pytest.mark.asyncio
async def test_model_name_never_substitutes_for_verified_stream_capability():
    models = SimpleNamespace(generation_profile=AsyncMock(return_value={
        "model": "gemini-4", "verified_capabilities": ["text"], "max_output_tokens": 32768}))
    with pytest.raises(AppError, match="model_capability_unverified"):
        await generation_request(models, context(), key="unregistered", attempt_id="same", function="reflect")
