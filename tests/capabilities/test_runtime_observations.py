"""Persistent owner observations and empty maintenance wakes, using isolated PG."""

import asyncio
from datetime import UTC, datetime, timedelta
from importlib.resources import files
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tianshu.capabilities.contracts import Fault
from tianshu.capabilities.external_catalog import ExternalCatalog
from tianshu.capabilities.infrastructure.runtime import Infrastructure
from tianshu.capabilities.media.background import Background
from tianshu.capabilities.media.runtime import Media
from tianshu.capabilities.service import Capabilities
from tianshu.runtime.jobs import JobQueue
from tianshu.runtime.migrations import Migration, migrate


async def prepare(database):
    await migrate(database, (
        Migration("capabilities-0001", files("tianshu.capabilities").joinpath("schema.sql").read_text()),
        Migration("capabilities-0002-runtime-observations", files("tianshu.capabilities").joinpath("runtime_observations.sql").read_text()),
    ))


@pytest.mark.asyncio
async def test_node_test_is_persisted_without_mutating_configuration_revision(database):
    await prepare(database)
    runtime = SimpleNamespace(database=database)
    catalog = ExternalCatalog("unused", runtime=runtime)
    await asyncio.to_thread(catalog.save, "infrastructure:node", {"enabled": True, "kind": "host", "label": "node"}, None, None, 0, "save")
    revision, _ = await asyncio.to_thread(catalog.snapshot)
    result = {"state": "connected", "code": "read_observed", "checked_at": datetime.now(UTC).isoformat(), "connection_revision": 1}
    await asyncio.to_thread(catalog.record_test, "infrastructure:node", revision, result)
    after, rows = await asyncio.to_thread(catalog.snapshot)
    assert after == revision == 1
    assert rows["infrastructure:node"]["last_test"] == result
    assert rows["infrastructure:node"]["value"]["connection_revision"] == 1
    with pytest.raises(Fault):
        await asyncio.to_thread(catalog.record_test, "infrastructure:node", revision, {**result, "connection_revision": 9})


@pytest.mark.asyncio
async def test_empty_maintenance_uses_next_due_and_change_wakes_replace_old_generation(database):
    await prepare(database)
    jobs = JobQueue(database.config.dsn)
    runtime = SimpleNamespace(database=database, jobs=jobs)
    operation = AsyncMock()
    background = Background(runtime, next_due=AsyncMock(return_value={"known": True, "due": None}))
    background.start("subscriptions", operation)
    await background.activate()
    await background.run("subscriptions", wake_generation=1)
    async with database.transaction(read_only=True) as connection:
        row = await (await connection.execute("SELECT * FROM capabilities.maintenance WHERE name='subscriptions'")).fetchone()
    assert row["next_due_at"] > datetime.now(UTC) + timedelta(minutes=4)
    assert row["wake_generation"] == 2
    await background.schedule("subscriptions", datetime.now(UTC))
    await background.run("subscriptions", wake_generation=2)
    assert operation.await_count == 1
    await background.run("subscriptions", wake_generation=3)
    assert operation.await_count == 2
    await background.close()


@pytest.mark.asyncio
async def test_registered_maintenance_adapter_forwards_the_persisted_generation():
    handlers = {}
    jobs = SimpleNamespace(tasks=handlers, register=lambda name, handler: handlers.update({name: handler}))
    capabilities = Capabilities.__new__(Capabilities)
    capabilities.runtime = SimpleNamespace(jobs=jobs)
    capabilities.actions = SimpleNamespace(register_tasks=lambda: None)
    background = SimpleNamespace(operations={"subscriptions": object()}, run=AsyncMock())
    capabilities.media = SimpleNamespace(background=background)
    capabilities.infrastructure = None
    capabilities.register_tasks()
    await handlers["capabilities.media.subscriptions"](wake_generation=23)
    background.run.assert_awaited_once_with("subscriptions", wake_generation=23)


@pytest.mark.asyncio
async def test_http_engine_projection_reads_worker_owner_and_becomes_unknown_on_expiry(database):
    await prepare(database)
    async with database.transaction() as connection:
        await connection.execute("INSERT INTO capabilities.execution_owners(id,role,expires_at) VALUES('worker','worker',now()+interval '1 minute')")
        await connection.execute("INSERT INTO capabilities.runtime_observations(name,owner_id,body) VALUES('media.engine','worker','{\"state\":\"ready\",\"version\":\"fixture\",\"code\":null}')")
    http_media = Media.__new__(Media)
    http_media.storage_runtime = SimpleNamespace(database=database)
    observed = await http_media._engine_observation()
    assert observed["state"] == "ready" and observed["owner_live"]
    assert observed["version"] == "fixture"
    async with database.transaction() as connection:
        await connection.execute("UPDATE capabilities.execution_owners SET stopped_at=now() WHERE id='worker'")
    stale = await http_media._engine_observation()
    assert stale["state"] == "not_verified" and stale["stale"]
    assert stale["code"] == "media_worker_observation_stale"


@pytest.mark.asyncio
async def test_detached_collector_failure_has_persisted_terminal_run_and_structured_failure():
    infrastructure = Infrastructure.__new__(Infrastructure)
    saved = {}

    async def local(operation, *args):
        return operation(*args)

    infrastructure.local = local
    infrastructure.slots = asyncio.Semaphore(1)
    infrastructure.node_locks = {}
    infrastructure.runs = {"node": {"attempt_id": "fixture", "state": "queued", "stage": "queued", "reason_codes": []}}
    infrastructure.jobs, infrastructure.due = {}, {}
    infrastructure.current = lambda *_args: True
    infrastructure.repo = SimpleNamespace(record=lambda node, operation, revision, response: saved.update({operation: response}))
    infrastructure.clock = lambda: 1793520000
    infrastructure.config = {"poll_interval_seconds": 10}
    infrastructure.storage_runtime = SimpleNamespace(events=SimpleNamespace(emit=AsyncMock()))
    infrastructure._call = AsyncMock(return_value={"status": "ok", "capabilities": [], "reason_codes": [], "data": {}})
    infrastructure._record_test = AsyncMock(side_effect=AttributeError("fixture failure"))
    await infrastructure._collect("node", 1)
    run = saved["collection.run"]["data"]["run"]
    assert run["state"] == "failed" and run["stage"] == "finished"
    assert run["error_type"] == "AttributeError"
    event = infrastructure.storage_runtime.events.emit.call_args
    assert event.args == ("job.failed", "failed")
    assert event.kwargs["error_code"] == "infrastructure_collection_failed"
