"""Planning observations never substitute for authorization or execution checks."""

import asyncio
import time
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tianshu.capabilities.catalog import Availability, CapabilityDefinition, Catalog
from tianshu.types import OriginKind, TrustedContext


def context():
    return TrustedContext("author", "actor", None, "chat", ("scope",), ("scope",), 1,
                          OriginKind.ACTOR, frozenset({"*"}))


def handler(name, callback):
    return SimpleNamespace(
        definition=CapabilityDefinition(name, name, "", {"type": "object"}, {"type": "object"}),
        availability=AsyncMock(side_effect=callback),
    )


async def identity_project(_context, rows):
    return rows


def configured_catalog(revisions):
    catalog = Catalog()
    configuration = SimpleNamespace(availability_revisions=AsyncMock(side_effect=lambda: revisions.copy()))
    catalog.configuration = SimpleNamespace(
        configuration=configuration,
        project=AsyncMock(side_effect=identity_project),
        availability=AsyncMock(side_effect=lambda _context, _handler, value: value),
    )
    return catalog


@pytest.mark.asyncio
async def test_planning_is_nonblocking_unknown_until_one_coalesced_refresh_and_execution_rechecks():
    catalog = configured_catalog({})
    gate = asyncio.Event()
    entered = asyncio.Event()
    current = {"configured": True}

    async def observe(_context):
        entered.set()
        await gate.wait()
        return Availability(True, current["configured"])

    selected = handler("example.read", observe)
    catalog.register(selected)
    catalog.on_change = AsyncMock()
    first = await catalog.planning(context())
    task = catalog.observations.pending[context()]
    assert first[0]["freshness"] == "unknown" and not first[0]["can_execute"]
    await entered.wait()
    second = await catalog.planning(context())
    assert not second[0]["can_execute"] and catalog.observations.pending[context()] is task
    gate.set()
    await task
    ready = await catalog.planning(context())
    assert ready[0]["freshness"] == "current" and ready[0]["can_execute"]
    assert ready[0]["observed_at"] and ready[0]["expires_at"]
    catalog.on_change.assert_awaited_once_with(context(), ("example.read",))
    current["configured"] = False
    assert not (await catalog.availability(context(), selected)).can_execute
    assert selected.availability.await_count == 2
    await catalog.close()


@pytest.mark.asyncio
async def test_targeted_revision_change_context_isolation_expiry_and_failure():
    revisions = {"image_engine": (("engine", 1),), "weather_connection": (("weather", 1),)}
    catalog = configured_catalog(revisions)

    async def observe(context):
        if context.actor_id == "broken":
            raise RuntimeError("provider unavailable")
        return Availability(True, True)

    image = handler("images.generate", observe)
    weather = handler("weather.current", observe)
    catalog.register(image)
    catalog.register(weather)
    original = context()
    await catalog.planning(original)
    await catalog.observations.pending[original]
    assert all(row["can_execute"] for row in await catalog.planning(original))

    revisions["image_engine"] = (("engine", 2),)
    rows = {row["definition"].id: row for row in await catalog.planning(original)}
    assert not rows["images.generate"]["can_execute"]
    assert rows["weather.current"]["can_execute"]
    await catalog.observations.pending[original]
    assert image.availability.await_count == 2 and weather.availability.await_count == 1

    changed_grant = replace(original, access_epoch=2)
    assert not any(row["can_execute"] for row in await catalog.planning(changed_grant))
    await catalog.observations.pending[changed_grant]
    broken = replace(original, actor_id="broken")
    await catalog.planning(broken)
    await catalog.observations.pending[broken]
    failed = await catalog.planning(broken)
    assert all(row["freshness"] == "unknown" and not row["can_execute"] for row in failed)

    catalog.observations.entries[(original, "weather.current")]["expires"] = time.monotonic() - 1
    rows = {row["definition"].id: row for row in await catalog.planning(original)}
    assert rows["weather.current"]["freshness"] == "expired"
    assert not rows["weather.current"]["can_execute"]
    await catalog.observations.pending[original]
    await catalog.close()
