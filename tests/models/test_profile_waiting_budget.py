"""Read-only eligibility uses the queue/business budget, not elapsed execution time."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from test_rb_profile_quota import request

from tianshu.models.broker import ModelBroker
from tianshu.models.client import ModelClient
from tianshu.runtime.errors import AppError
from tianshu.types import RequestBudget


@pytest.mark.asyncio
async def test_profile_can_recheck_unadmitted_request_with_expired_legacy_budget():
    original = replace(request(), budget=RequestBudget(datetime.now(UTC)-timedelta(hours=2)))
    broker = ModelBroker.__new__(ModelBroker)
    broker.recheck = AsyncMock()
    plan = SimpleNamespace(route_id="route", revision=1, protocol="openai.chat", model="fixture",
                           quota_group="quota", parameters={}, verified_capabilities=frozenset({"text"}))
    broker.catalog = SimpleNamespace(resolve=AsyncMock(return_value=plan))
    profile = await broker.generation_profile(original)
    assert profile["route_id"] == "route" and original.budget.remaining() < 0
    broker.catalog.resolve.assert_awaited_once_with(original)


@pytest.mark.asyncio
async def test_client_profile_uses_bounded_control_timeout_without_resetting_original_budget():
    original = replace(request(), budget=RequestBudget(datetime.now(UTC)-timedelta(hours=2)))
    client = ModelClient.__new__(ModelClient)
    client._json = AsyncMock(return_value={"route_id": "route"})
    await client.generation_profile(original)
    assert client._json.await_args.kwargs == {"body": original, "control": True, "timeout": 10}


@pytest.mark.asyncio
async def test_profile_still_respects_actual_business_deadline():
    original = replace(request(), business_deadline_at=datetime.now(UTC)-timedelta(seconds=1))
    broker = ModelBroker.__new__(ModelBroker)
    broker.recheck = AsyncMock()
    with pytest.raises(AppError, match="business_deadline_expired"):
        await broker.generation_profile(original)
    broker.recheck.assert_not_awaited()
