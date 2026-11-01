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
from tianshu.capabilities.media.books.audio.runtime import BookAudio
from tianshu.capabilities.media.music.navidrome.sync import Navidrome
from tianshu.capabilities.media.scheduling import scan_due
from tianshu.capabilities.media.runtime import Media
from tianshu.capabilities.service import Capabilities
from tianshu.runtime.jobs import JobQueue
from tianshu.runtime.migrations import Migration, migrate
from tianshu.runtime.serialization import wire
from tianshu.types import ArtifactRef, MediaRef, OriginKind, Receipt, TrustedContext


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
        row = await (await connection.execute(
            "SELECT *,now() AS database_now FROM capabilities.maintenance WHERE name='subscriptions'")).fetchone()
    assert row["next_due_at"] > row["database_now"] + timedelta(minutes=4)
    assert row["wake_generation"] == 2
    await background.schedule("subscriptions")
    await background.run("subscriptions", wake_generation=2)
    assert operation.await_count == 1
    await background.run("subscriptions", wake_generation=3)
    assert operation.await_count == 2
    await background.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("skew", [-3600, 3600])
async def test_immediate_maintenance_and_idle_delay_use_database_clock(database, monkeypatch, skew):
    await prepare(database)

    class SkewedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return super().now(tz) + timedelta(seconds=skew)

    monkeypatch.setattr("tianshu.capabilities.media.background.datetime", SkewedDatetime)
    jobs = JobQueue(database.config.dsn)
    runtime = SimpleNamespace(database=database, jobs=jobs)
    operation = AsyncMock()
    background = Background(runtime, next_due=AsyncMock(return_value={"known": True, "due": None}))
    background.start("subscriptions", operation)
    await background.activate()
    await background.run("subscriptions", wake_generation=1)
    operation.assert_awaited_once()
    async with database.transaction(read_only=True) as connection:
        row = await (await connection.execute(
            "SELECT *,now() AS database_now FROM capabilities.maintenance WHERE name='subscriptions'")).fetchone()
    assert row["database_now"] + timedelta(minutes=4) < row["next_due_at"]
    assert row["next_due_at"] < row["database_now"] + timedelta(minutes=6)
    assert row["wake_generation"] == 2
    await background.close()


@pytest.mark.asyncio
async def test_future_maintenance_cannot_run_before_its_database_deadline(database):
    await prepare(database)
    jobs = JobQueue(database.config.dsn)
    jobs.enqueue = AsyncMock(wraps=jobs.enqueue)
    runtime = SimpleNamespace(database=database, jobs=jobs)
    operation = AsyncMock()
    background = Background(runtime, next_due=AsyncMock(return_value={"known": True, "due": None}))
    background.start("subscriptions", operation)
    async with database.transaction(read_only=True) as connection:
        due = (await (await connection.execute("SELECT now()+interval '1 hour' AS due")).fetchone())["due"]
    await background.schedule("subscriptions", due)
    await background.activate()
    assert jobs.enqueue.call_args.args[1].scheduled_at == due
    enqueued = jobs.enqueue.await_count
    await background.dispatch("subscriptions", wake_generation=2)
    assert jobs.enqueue.await_count == enqueued
    await background.run("subscriptions", wake_generation=2)
    operation.assert_not_awaited()
    async with database.transaction(read_only=True) as connection:
        row = await (await connection.execute(
            "SELECT next_due_at,wake_generation FROM capabilities.maintenance WHERE name='subscriptions'")).fetchone()
    assert row == {"next_due_at": due, "wake_generation": 2}
    await background.close()


@pytest.mark.asyncio
async def test_startup_restores_past_due_maintenance_without_a_current_generation_job(database):
    await prepare(database)
    async with database.transaction() as connection:
        await connection.execute(
            "INSERT INTO capabilities.maintenance(name,owner,lease_until,next_due_at,wake_generation) "
            "VALUES('subscriptions','',to_timestamp(0),now()-interval '1 hour',7)")
    jobs = JobQueue(database.config.dsn)
    runtime = SimpleNamespace(database=database, jobs=jobs)
    operation = AsyncMock()
    background = Background(runtime, next_due=AsyncMock(return_value={"known": True, "due": None}))
    background.start("subscriptions", operation)
    await background.activate()
    async with database.transaction(read_only=True) as connection:
        row = await (await connection.execute(
            "SELECT *,now() AS database_now FROM capabilities.maintenance WHERE name='subscriptions'")).fetchone()
        queued = await (await connection.execute(
            "SELECT key,job_id FROM runtime.job_keys WHERE task='capabilities.media.subscriptions'")).fetchall()
    assert row["wake_generation"] == 8
    assert row["next_due_at"] < row["database_now"]
    assert [item["key"] for item in queued] == ["media-maintenance:subscriptions:8"]
    assert queued[0]["job_id"] is not None
    await background.run("subscriptions", wake_generation=7)
    operation.assert_not_awaited()
    await background.run("subscriptions", wake_generation=8)
    operation.assert_awaited_once()
    await background.close()


@pytest.mark.asyncio
async def test_startup_preserves_busy_owner_and_schedules_after_its_lease(database):
    await prepare(database)
    async with database.transaction() as connection:
        lease = (await (await connection.execute(
            "INSERT INTO capabilities.maintenance(name,owner,lease_until,next_due_at,wake_generation) "
            "VALUES('subscriptions','live-owner',now()+interval '1 minute',now()-interval '1 hour',7) "
            "RETURNING lease_until")).fetchone())["lease_until"]
    jobs = JobQueue(database.config.dsn)
    jobs.enqueue = AsyncMock(wraps=jobs.enqueue)
    runtime = SimpleNamespace(database=database, jobs=jobs)
    operation = AsyncMock()
    background = Background(runtime, next_due=AsyncMock(return_value={"known": True, "due": None}))
    background.start("subscriptions", operation)
    await background.activate()
    assert jobs.enqueue.call_args.args[1].scheduled_at == lease
    await background.run("subscriptions", wake_generation=8)
    operation.assert_not_awaited()
    async with database.transaction() as connection:
        row = await (await connection.execute(
            "SELECT owner,lease_until,wake_generation FROM capabilities.maintenance WHERE name='subscriptions'")).fetchone()
        assert row == {"owner": "live-owner", "lease_until": lease, "wake_generation": 8}
        await connection.execute("UPDATE capabilities.maintenance SET lease_until=now()-interval '1 second' WHERE name='subscriptions'")
    await background.run("subscriptions", wake_generation=8)
    operation.assert_awaited_once()
    await background.close()


@pytest.mark.asyncio
async def test_close_with_pending_change_restarts_on_a_new_native_generation(database):
    await prepare(database)
    jobs = JobQueue(database.config.dsn)
    runtime = SimpleNamespace(database=database, jobs=jobs)

    async def close_after_change():
        await original.schedule("subscriptions")
        await original.close()

    original = Background(runtime, next_due=AsyncMock(return_value={"known": True, "due": None}))
    original.start("subscriptions", close_after_change)
    await original.activate()
    await original.run("subscriptions", wake_generation=1)
    async with database.transaction(read_only=True) as connection:
        row = await (await connection.execute(
            "SELECT *,now() AS database_now FROM capabilities.maintenance WHERE name='subscriptions'")).fetchone()
        queued = await (await connection.execute(
            "SELECT key FROM runtime.job_keys WHERE task='capabilities.media.subscriptions' ORDER BY key")).fetchall()
    assert row["wake_generation"] == 2
    assert row["next_due_at"] <= row["database_now"]
    assert [item["key"] for item in queued] == ["media-maintenance:subscriptions:1", "media-maintenance:subscriptions:2"]
    # A new process has new task handlers, with the same persistent job ledger.
    restarted = Background(SimpleNamespace(database=database, jobs=JobQueue(database.config.dsn)),
        next_due=AsyncMock(return_value={"known": True, "due": None}))
    operation = AsyncMock()
    restarted.start("subscriptions", operation)
    await restarted.activate()
    await restarted.run("subscriptions", wake_generation=2)
    operation.assert_not_awaited()
    await restarted.run("subscriptions", wake_generation=3)
    operation.assert_awaited_once()
    await restarted.close()


@pytest.mark.asyncio
async def test_running_maintenance_preserves_a_change_and_invalidates_busy_wakes(database):
    await prepare(database)
    jobs = JobQueue(database.config.dsn)
    runtime = SimpleNamespace(database=database, jobs=jobs)
    changed = False

    async def operation():
        nonlocal changed
        if not changed:
            changed = True
            await background.schedule("subscriptions")
            # Its current lease still belongs to the first run. This wake is
            # observed while busy and must not run the same operation twice.
            await background.run("subscriptions", wake_generation=2)

    observed = AsyncMock(side_effect=operation)
    background = Background(runtime, next_due=AsyncMock(return_value={"known": True, "due": None}))
    background.start("subscriptions", observed)
    await background.activate()
    await background.run("subscriptions", wake_generation=1)
    observed.assert_awaited_once()
    async with database.transaction(read_only=True) as connection:
        row = await (await connection.execute(
            "SELECT *,now() AS database_now FROM capabilities.maintenance WHERE name='subscriptions'")).fetchone()
    assert row["wake_generation"] == 3
    assert row["next_due_at"] <= row["database_now"]
    await background.run("subscriptions", wake_generation=2)
    observed.assert_awaited_once()
    await background.run("subscriptions", wake_generation=3)
    assert observed.await_count == 2
    async with database.transaction(read_only=True) as connection:
        row = await (await connection.execute(
            "SELECT *,now() AS database_now FROM capabilities.maintenance WHERE name='subscriptions'")).fetchone()
    assert row["next_due_at"] > row["database_now"] + timedelta(minutes=4)
    await background.close()


@pytest.mark.asyncio
async def test_lost_maintenance_owner_cannot_finish_or_reschedule_newer_owner(database):
    await prepare(database)
    jobs = JobQueue(database.config.dsn)
    runtime = SimpleNamespace(database=database, jobs=jobs)

    async def replaced_owner():
        async with database.transaction() as connection:
            await connection.execute(
                "UPDATE capabilities.maintenance SET owner='new-owner',lease_until=now()+interval '1 minute',"
                "next_due_at=now()+interval '30 seconds',wake_generation=2,body='{\"state\":\"running\"}' "
                "WHERE name='subscriptions'")

    background = Background(runtime, next_due=AsyncMock(return_value={"known": True, "due": None}))
    background.start("subscriptions", replaced_owner)
    await background.activate()
    await background.run("subscriptions", wake_generation=1)
    async with database.transaction(read_only=True) as connection:
        row = await (await connection.execute(
            "SELECT * FROM capabilities.maintenance WHERE name='subscriptions'")).fetchone()
        queued = await (await connection.execute(
            "SELECT key FROM runtime.job_keys WHERE task='capabilities.media.subscriptions'")).fetchall()
    assert row["owner"] == "new-owner"
    assert row["wake_generation"] == 2
    assert row["body"] == {"state": "running"}
    assert [item["key"] for item in queued] == ["media-maintenance:subscriptions:1"]
    await background.close()


@pytest.mark.asyncio
async def test_registered_maintenance_adapter_forwards_the_persisted_generation():
    handlers = {}
    jobs = SimpleNamespace(tasks=handlers, register=lambda name, handler: handlers.update({name: handler}))
    capabilities = Capabilities.__new__(Capabilities)
    capabilities.runtime = SimpleNamespace(jobs=jobs)
    capabilities.actions = SimpleNamespace(register_tasks=lambda: None)
    background = SimpleNamespace(operations={"subscriptions": object()}, dispatch=AsyncMock(), run=AsyncMock())
    capabilities.media = SimpleNamespace(background=background)
    capabilities.infrastructure = None
    capabilities.register_tasks()
    assert handlers["capabilities.media.library_job"] is handlers["capabilities.media_job"]
    await handlers["capabilities.media.subscriptions"](wake_generation=23)
    background.dispatch.assert_awaited_once_with("subscriptions", wake_generation=23)
    background.run.assert_not_awaited()
    await handlers["capabilities.media.work.subscriptions"](wake_generation=23)
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


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["music_lyrics", "music_navidrome"])
async def test_maintenance_dispatch_queues_once_without_running_the_work(database, name):
    await prepare(database)
    jobs = JobQueue(database.config.dsn)
    runtime = SimpleNamespace(database=database, jobs=jobs)
    operation = AsyncMock()
    background = Background(runtime, next_due=AsyncMock(return_value={"known": True, "due": None}))
    background.start(name, operation)
    await background.activate()
    await background.dispatch(name, wake_generation=1)
    await background.dispatch(name, wake_generation=1)
    operation.assert_not_awaited()
    async with database.transaction(read_only=True) as connection:
        work = await (await connection.execute(
            "SELECT key FROM runtime.job_keys WHERE task=%s",
            ("capabilities.media.work." + name,))).fetchall()
        state = await (await connection.execute(
            "SELECT owner,wake_generation,next_due_at FROM capabilities.maintenance WHERE name=%s",
            (name,))).fetchone()
    assert len(work) == 1
    assert state["owner"] == "" and state["wake_generation"] == 1
    assert state["next_due_at"] is not None
    await background.run(name, wake_generation=1)
    operation.assert_awaited_once()
    await background.dispatch(name, wake_generation=1)
    operation.assert_awaited_once()
    await background.close()


def work_context():
    return TrustedContext("principal", "actor", "person", "conversation", ("scope",),
        (), 1, OriginKind.TIMER, frozenset({"*"}))


@pytest.mark.asyncio
async def test_maintenance_dispatch_preserves_busy_owner_and_schedules_work_after_its_lease(database):
    await prepare(database)
    jobs = JobQueue(database.config.dsn)
    jobs.enqueue = AsyncMock(wraps=jobs.enqueue)
    operation = AsyncMock()
    background = Background(SimpleNamespace(database=database, jobs=jobs))
    background.start("music_lyrics", operation)
    await background.activate()
    async with database.transaction() as connection:
        lease = (await (await connection.execute(
            "UPDATE capabilities.maintenance SET owner='existing-owner',lease_until=now()+interval '1 hour' "
            "WHERE name='music_lyrics' RETURNING lease_until")).fetchone())["lease_until"]
    await background.dispatch("music_lyrics", wake_generation=1)
    queued = jobs.enqueue.call_args.args[1]
    assert queued.task == "capabilities.media.work.music_lyrics"
    assert queued.scheduled_at == lease
    operation.assert_not_awaited()
    async with database.transaction(read_only=True) as connection:
        state = await (await connection.execute(
            "SELECT owner,lease_until,wake_generation FROM capabilities.maintenance WHERE name='music_lyrics'")).fetchone()
    assert state == {"owner": "existing-owner", "lease_until": lease, "wake_generation": 1}
    await background.close()


@pytest.mark.asyncio
async def test_automatic_subscription_dispatch_keeps_original_action_without_waiting_for_scan():
    rows = [{"state": "active", "next_scan_at": 1, "subscription_id": identity,
        "revision": 3, "context": wire(work_context()), "source_refs": []}
        for identity in ("first", "second")]

    async def local(operation, *args):
        return operation(*args)

    actions = SimpleNamespace(invoke=AsyncMock(return_value={"execution": "pending"}))
    media = SimpleNamespace(local=local,
        repo=SimpleNamespace(list=lambda _kind: rows, clock=lambda: 10),
        capabilities=SimpleNamespace(actions=actions))
    await scan_due(media, "subscription", "subscription_id", None)
    first_ids = [call.args[0].action_id for call in actions.invoke.call_args_list]
    assert len(set(first_ids)) == 2
    assert all(call.kwargs == {"background": True} for call in actions.invoke.call_args_list)
    actions.invoke.reset_mock()
    await scan_due(media, "subscription", "subscription_id", None)
    assert [call.args[0].action_id for call in actions.invoke.call_args_list] == first_ids
    assert [call.args[0].arguments["subscription_id"] for call in actions.invoke.call_args_list] == ["first", "second"]


@pytest.mark.asyncio
@pytest.mark.parametrize("second_execution", ["succeeded", "unknown"])
async def test_book_audio_yields_between_fragments_and_preserves_original_unknown(monkeypatch, second_execution):
    audio = BookAudio.__new__(BookAudio)
    state = {"id": "chapter-job", "owner": "original-owner", "book_id": "book", "chapter_id": "chapter",
        "root": "/synthetic", "key": "whole-chapter", "text": "first second",
        "profile": {"id": "voice", "revision": 1}, "profile_id": "profile",
        "context": wire(work_context()), "source_refs": [], "state": "queued", "generation": 0}
    fragments = [{"text": "first", "start": 0, "end": 5}, {"text": "second", "start": 6, "end": 12}]
    monkeypatch.setattr("tianshu.capabilities.media.books.audio.runtime.segments", lambda _text: fragments)
    caches, paid = {}, []

    async def local(operation, *args, **kwargs):
        return operation(*args, **kwargs)

    def claim():
        return dict(state) if state["state"] in {"queued", "working"} else None

    def update(job, **changes):
        assert job["id"] == state["id"] and job["owner"] == state["owner"]
        state.update(changes)
        return dict(state)

    def publish(_root, key, text, _raw, _alignment):
        caches[key] = {"key": key, "text": text}
        return caches[key]

    async def invoke(action):
        paid.append(action)
        execution = "succeeded" if len(paid) == 1 else second_execution
        return Receipt("receipt", action.action_id, "original-attempt:" + action.action_id,
            execution, "unknown" if execution == "unknown" else "confirmed", "not_requested",
            artifacts=() if execution == "unknown" else (ArtifactRef("artifact",
                MediaRef("media", "artifact:media", "audio/mpeg")),), result={"alignment": []})

    service = SimpleNamespace(configuration=SimpleNamespace(get=AsyncMock(return_value=state["profile"])),
        actions=SimpleNamespace(invoke=AsyncMock(side_effect=invoke)),
        artifacts=SimpleNamespace(get=AsyncMock(return_value=(b"audio", None))))
    audio.books = SimpleNamespace(media=SimpleNamespace(capabilities=service))
    audio.repo = SimpleNamespace(get=lambda *_args: None, clock=lambda: 100)
    audio.local, audio.lock = local, asyncio.Lock()
    audio.ledger = SimpleNamespace(claim=claim, update=update)
    audio.files = SimpleNamespace(read=lambda _root, key: caches.get(key), publish=publish,
        assemble=AsyncMock(return_value={"chapter_id": "chapter"}))

    assert await audio.run_once()
    assert len(paid) == 1 and state["state"] == "queued"
    assert state["completed_parts"] == 1 and state["lease"] == 0
    audio.files.assemble.assert_not_awaited()
    first_action = paid[0].action_id
    assert await audio.run_once()
    assert len(paid) == 2 and paid[0].action_id == first_action
    assert paid[1].action_id != first_action and paid[1].arguments["text"] == "second"
    assert state["id"] == "chapter-job" and state["generation"] == 0
    if second_execution == "unknown":
        assert state["state"] == "unknown" and state["code"] == "book_audio_unknown"
        assert len(caches) == 1
        audio.files.assemble.assert_not_awaited()
    else:
        assert state["state"] == "ready" and state["completed_parts"] == 2
        assert len(caches) == 2
        audio.files.assemble.assert_awaited_once()
    assert not await audio.run_once()
    assert len(paid) == 2


@pytest.mark.asyncio
async def test_navidrome_work_advances_one_due_link_and_leaves_the_other_for_next_turn():
    sync = Navidrome.__new__(Navidrome)
    rows = [{"link_id": identity, "connection_id": "server", "status": "queued",
        "auto_sync": True, "next_sync_at": due, "revision": revision}
        for identity, due, revision in (("later", 5, 7), ("earlier", 1, 4))]
    released = []

    async def local(operation, *args, **kwargs):
        return operation(*args, **kwargs)

    async def advance(_connection, link, _owner, _generation, _catalog):
        link["next_sync_at"] = 1000

    sync.media = SimpleNamespace(local=local)
    sync.repo = SimpleNamespace(list=lambda _kind: rows, clock=lambda: 10,
        scan_claim=lambda *_args: 3, scan_heartbeat=lambda *_args: None,
        scan_release=lambda *args: released.append(args))
    sync.state = SimpleNamespace(saved=lambda: {"connection_id": "server", "enabled": True},
        connection=lambda: {"connection_id": "server"})
    sync.sync = AsyncMock(side_effect=advance)
    await sync.tick()
    assert sync.sync.await_count == 1
    assert sync.sync.call_args.args[1]["link_id"] == "earlier"
    assert rows[0]["next_sync_at"] == 5 and rows[0]["revision"] == 7
    await sync.tick()
    assert sync.sync.await_count == 2
    assert sync.sync.call_args.args[1]["link_id"] == "later"
    assert len(released) == 2


@pytest.mark.asyncio
async def test_waiting_on_one_infrastructure_node_does_not_consume_other_node_slots():
    infrastructure = Infrastructure.__new__(Infrastructure)

    async def local(operation, *args):
        return operation(*args)

    infrastructure.local = local
    infrastructure.current = lambda *_args: True
    infrastructure.slots = asyncio.Semaphore(2)
    held = asyncio.Lock()
    infrastructure.node_locks = {"busy": held}
    infrastructure.logs = {(node, container): {"state": "queued"}
        for node, container in (("busy", "one"), ("busy", "two"), ("independent", "three"))}
    infrastructure.log_jobs = {}
    infrastructure._call = AsyncMock(return_value={"status": "ok", "data": {},
        "observed_at": "2026-11-01T00:00:00Z", "reason_codes": []})
    await held.acquire()
    waiters = [asyncio.create_task(infrastructure._logs("busy", container, 1))
        for container in ("one", "two")]
    try:
        await asyncio.sleep(0)
        await asyncio.wait_for(infrastructure._logs("independent", "three", 1), timeout=2)
        assert infrastructure.logs[("independent", "three")]["state"] == "observed"
        infrastructure._call.assert_awaited_once()
    finally:
        for task in waiters:
            task.cancel()
        await asyncio.gather(*waiters, return_exceptions=True)
        held.release()
