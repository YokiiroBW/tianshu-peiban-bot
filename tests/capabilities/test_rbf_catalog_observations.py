import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tianshu.capabilities.catalog import Availability, CapabilityDefinition, Catalog
from tianshu.types import OriginKind, TrustedContext


def context():
    return TrustedContext("author", "actor", None, "chat", ("scope",), ("scope",), 1,
                          OriginKind.ACTOR, frozenset({"*"}))


def handler(name, observe):
    return SimpleNamespace(definition=CapabilityDefinition(name, name, "", {"type": "object"},
                                                            {"type": "object"}),
                           availability=AsyncMock(side_effect=observe))


@pytest.mark.asyncio
async def test_expiry_failure_and_probe_time_do_not_change_known_semantics():
    catalog = Catalog()
    observations = [Availability(True, True, verified_at="first", constraints={"limit": 1}),
                    RuntimeError("temporarily unavailable"),
                    Availability(True, True, verified_at="later", constraints={"limit": 1}),
                    Availability(True, True, verified_at="new", constraints={"limit": 2})]
    async def observe(_context):
        item = observations.pop(0)
        if isinstance(item, Exception):
            raise item
        return item
    selected = handler("weather.current", observe)
    catalog.register(selected)
    catalog.on_change = AsyncMock()
    catalog.on_ready = AsyncMock()
    ctx = context()
    first = (await catalog.planning(ctx))[0]
    assert first["semantic_availability"] is None and not first["can_execute"]
    await catalog.observations.pending[ctx]
    known = (await catalog.planning(ctx))[0]
    digest = catalog.observation_digest(ctx)
    assert known["semantic_availability"].verified_at is None
    assert known["observed_at"] and known["expires_at"]
    assert catalog.on_change.await_count == 1

    catalog.observations.entries[(ctx, "weather.current")]["expires"] = time.monotonic() - 1
    stale = (await catalog.planning(ctx))[0]
    assert not stale["can_execute"] and stale["freshness"] == "expired"
    assert stale["semantic_availability"] == known["semantic_availability"]
    assert stale["observed_at"] == known["observed_at"]
    await catalog.observations.pending[ctx]
    failed = (await catalog.planning(ctx))[0]
    assert failed["freshness"] == "unknown" and not failed["can_execute"]
    assert failed["semantic_availability"] == known["semantic_availability"]
    assert catalog.observation_digest(ctx) == digest
    assert catalog.on_change.await_count == 1

    catalog.observations.entries[(ctx, "weather.current")]["expires"] = time.monotonic() - 1
    await catalog.planning(ctx)
    await catalog.observations.pending[ctx]
    assert catalog.observation_digest(ctx) == digest and catalog.on_change.await_count == 1
    catalog.on_ready.assert_awaited_once_with(ctx, ("weather.current",))
    catalog.observations.entries[(ctx, "weather.current")]["expires"] = time.monotonic() - 1
    await catalog.planning(ctx)
    await catalog.observations.pending[ctx]
    assert catalog.observation_digest(ctx) != digest and catalog.on_change.await_count == 2
    await catalog.close()


@pytest.mark.asyncio
async def test_probe_requested_during_another_batch_is_not_lost():
    catalog, ctx = Catalog(), context()
    slow, fast_notified = asyncio.Event(), asyncio.Event()
    async def fast(_):
        return Availability(True, True)
    async def wait(_):
        await slow.wait()
        return Availability(True, True)
    one, two = handler("one", fast), handler("two", wait)
    catalog.register(one)
    catalog.register(two)
    async def changed(_context, ids):
        if ids == ("one",):
            fast_notified.set()
    catalog.on_change = changed
    await catalog.planning(ctx)
    first_batch = catalog.observations.pending[ctx]
    await asyncio.wait_for(fast_notified.wait(), 1)
    catalog.observations.entries[(ctx, "one")]["expires"] = time.monotonic() - 1
    await catalog.planning(ctx)
    slow.set()
    await first_batch
    await asyncio.sleep(0)
    next_batch = catalog.observations.pending.get(ctx)
    if next_batch is not None:
        await next_batch
    assert one.availability.await_count == 2
    await catalog.close()


@pytest.mark.asyncio
async def test_slow_probe_and_slow_notification_do_not_block_other_known_results():
    catalog, ctx = Catalog(), context()
    slow, notification = asyncio.Event(), asyncio.Event()
    first_wake, second_wake = asyncio.Event(), asyncio.Event()
    async def observe_slow(_):
        await slow.wait()
        return Availability(True, True)
    async def observe_fast(_):
        return Availability(True, True)
    async def changed(_context, ids):
        if "fast.one" in ids:
            first_wake.set()
        if "fast.two" in ids:
            second_wake.set()
        if "fast.one" in ids:
            await notification.wait()
    catalog.register(handler("fast.one", observe_fast))
    catalog.register(handler("fast.two", observe_fast))
    catalog.register(handler("slow", observe_slow))
    catalog.on_change = changed
    await catalog.planning(ctx)
    await asyncio.wait_for(first_wake.wait(), 1)
    await asyncio.wait_for(second_wake.wait(), 1)
    rows = {row["definition"].id: row for row in await catalog.planning(ctx)}
    assert rows["fast.one"]["can_execute"] and rows["fast.two"]["can_execute"]
    assert not rows["slow"]["can_execute"]
    slow.set()
    notification.set()
    await catalog.observations.pending[ctx]
    await catalog.close()
