"""Isolated PostgreSQL stories for bounded Memory index statistics repair."""

import asyncio
from importlib.resources import files
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest
import pytest_asyncio
from psycopg.types.json import Jsonb

from tianshu.operations.statistics import StatisticsProjection
from tianshu.runtime.errors import AppError
from tianshu.runtime.migrations import Migration, migrate


def load_helper(name, path):
    spec = spec_from_file_location(name, path)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


core_fixtures = load_helper("_rbj_index_core", Path(__file__).parents[1] / "core" / "conftest.py")
index_examples = load_helper("_rbj_index_examples", Path(__file__).parents[1] / "core" / "test_rb_retrieval_index.py")
episodes = load_helper("_rbj_index_episodes", Path(__file__).parents[1] / "core" / "test_rb_consolidation_write.py")
core = core_fixtures.core
ordinary_users = core_fixtures.ordinary_users


@pytest_asyncio.fixture
async def projected_core(core):
    await migrate(core.database, (Migration(
        "operations-0001", files("tianshu.operations").joinpath("schema.sql").read_text(encoding="utf-8")),))
    statistics = StatisticsProjection(core.database)
    core.workers.index_jobs.bind_observability(statistics=statistics)
    return core


async def current_generation(core):
    async with core.database.transaction(read_only=True) as conn:
        return await core.memory.jobs.current_generation_on(conn)


async def seed_attempts(core, generation, *index_ids):
    async with core.database.transaction() as conn:
        for index_id in index_ids:
            await conn.execute(
                "INSERT INTO memory.index_jobs(index_id,kind,object_id,revision,profile_key,profile,state,"
                "attempt_count,generation,content_hash) VALUES(%s,'episode',%s,1,'rbj-profile',%s,"
                "'retry_wait',1,%s,'rbj-content')",
                (index_id, "missing:" + index_id, Jsonb({"route_id": "rbj-route"}), generation))
            await conn.execute(
                "INSERT INTO memory.index_attempts(index_id,number,intent_id,step_id,model_attempt_id,"
                "status,error_code,event_revision) VALUES(%s,1,%s,%s,%s,'failed','upstream_http_500',2)",
                (index_id, "intent:" + index_id, "step:" + index_id, "attempt:" + index_id))


async def read_statistics(core):
    async with core.database.transaction(read_only=True) as conn:
        events = await (await conn.execute(
            "SELECT event_id,revision FROM operations.statistic_events "
            "WHERE family='recovery' AND event_id LIKE 'memory-index:attempt:rbj-%' ORDER BY event_id")).fetchall()
        producer = await (await conn.execute(
            "SELECT * FROM operations.statistic_producers WHERE producer='memory.index.recovery'")).fetchone()
        cursor = await (await conn.execute("SELECT * FROM memory.index_stats_cursor WHERE singleton")).fetchone()
        total = await (await conn.execute(
            "SELECT COALESCE(sum(total),0) AS total FROM operations.statistic_buckets "
            "WHERE grain='minute' AND family='recovery' AND module='memory' AND metric='recovery_eligible'"
        )).fetchone()
    return events, producer, cursor, total["total"]


async def test_maintenance_backfills_after_rescan_failure_and_keeps_unverified_coverage(
        projected_core, ordinary_users, monkeypatch):
    core = projected_core
    context, _ = ordinary_users
    generation = await current_generation(core)
    await seed_attempts(core, generation, "rbj-b", "rbj-d")
    await index_examples.fact(core, context, "rbj-scan-interruption")
    models, _ = index_examples.bind_models(core)
    scan_calls = []

    async def fail_object_scan(*args, **kwargs):
        scan_calls.append((args, kwargs))
        raise RuntimeError("injected index object scan failure")

    monkeypatch.setattr(core.workers.index_jobs, "_load_on", fail_object_scan)
    with pytest.raises(RuntimeError, match="injected index object scan failure"):
        await core.workers.index_jobs.recover(generation)
    assert scan_calls and not models.requests
    events, producer, _, _ = await read_statistics(core)
    assert not events and producer is None

    # Maintenance has its own bounded route to the existing backfill even when
    # the separate object scan failed before reaching its former backfill call.
    await core.workers.reconcile_due(limit=1)
    events, producer, cursor, _ = await read_statistics(core)
    assert events == [{"event_id": "memory-index:attempt:rbj-b", "revision": 2}]
    assert cursor["after_index_id"] == "rbj-b" and cursor["after_number"] == 1
    assert producer["complete"] is False and producer["watermark"] == "rbj-b:1"
    assert producer["detail"]["data_generation"] == generation

    # A new earlier key and a changed already-scanned attempt do not become
    # covered merely because the current multi-transaction traversal ends.
    await seed_attempts(core, generation, "rbj-a")
    async with core.database.transaction() as conn:
        await conn.execute("UPDATE memory.index_attempts SET error_code='upstream_http_503',"
            "event_revision=event_revision+1,updated_at=now() WHERE index_id='rbj-b'")
    await core.workers.reconcile_due(limit=1)  # rbj-d
    await core.workers.reconcile_due(limit=1)  # end of this traversal
    events, producer, cursor, _ = await read_statistics(core)
    assert events == [{"event_id": "memory-index:attempt:rbj-b", "revision": 2},
                      {"event_id": "memory-index:attempt:rbj-d", "revision": 2}]
    assert cursor["after_index_id"] == "" and cursor["after_number"] == 0
    assert producer["complete"] is False and producer["watermark"] == "rbj-d:1"
    assert producer["detail"]["backfill"]["state"] == "scanned_unverified"

    # Subsequent cycles revisit the earlier key and the changed revision.
    for _ in range(5):
        await core.workers.reconcile_due(limit=1)
    events, producer, _, total = await read_statistics(core)
    assert events == [{"event_id": "memory-index:attempt:rbj-a", "revision": 2},
                      {"event_id": "memory-index:attempt:rbj-b", "revision": 3},
                      {"event_id": "memory-index:attempt:rbj-d", "revision": 2}]
    assert total == 3 and producer["complete"] is False
    assert not models.requests


async def test_projection_failure_keeps_original_cursor_and_retries(projected_core, monkeypatch):
    core = projected_core
    generation = await current_generation(core)
    empty = await core.workers.index_jobs.backfill_statistics(generation, limit=2)
    _, producer, _, _ = await read_statistics(core)
    assert empty["complete"] and empty["watermark"] is None
    assert producer["complete"] is False and producer["watermark"] is None
    await seed_attempts(core, generation, "rbj-before", "rbj-failed")
    statistics = core.workers.index_jobs.recovery.statistics
    original = statistics.record_safely_on
    failed = False

    async def fail_once(conn, event):
        nonlocal failed
        if event["event_id"] == "memory-index:attempt:rbj-failed" and not failed:
            failed = True
            return False
        return await original(conn, event)

    monkeypatch.setattr(statistics, "record_safely_on", fail_once)
    result = await core.workers.index_jobs.backfill_statistics(generation, limit=2)
    events, producer, cursor, _ = await read_statistics(core)
    assert events == [{"event_id": "memory-index:attempt:rbj-before", "revision": 2}]
    assert not result["complete"] and result["processed"] == 1 and result["watermark"] == "rbj-before:1"
    assert cursor["generation"] == generation and cursor["after_index_id"] == "rbj-before"
    assert producer["complete"] is False and producer["detail"]["backfill"]["state"] == "failed"
    assert producer["detail"]["backfill"]["reason"] == "event_write_pending"

    result = await core.workers.index_jobs.backfill_statistics(generation, limit=1)
    events, producer, cursor, total = await read_statistics(core)
    assert result["processed"] == 1 and events == [
        {"event_id": "memory-index:attempt:rbj-before", "revision": 2},
        {"event_id": "memory-index:attempt:rbj-failed", "revision": 2}]
    assert cursor["after_index_id"] == "rbj-failed" and total == 2
    assert producer["complete"] is False
    result = await core.workers.index_jobs.backfill_statistics(generation, limit=1)
    assert result["complete"] and result["coverage"] == "partial"
    assert result["watermark"] == "rbj-failed:1"


async def test_backfill_and_consolidation_failures_are_independent(
        projected_core, ordinary_users, monkeypatch):
    core = projected_core
    context, _ = ordinary_users
    generation = await current_generation(core)
    await episodes.episode(core, context, "rbj-index-statistics-isolation")
    await seed_attempts(core, generation, "rbj-first")
    statistics = core.workers.index_jobs.recovery.statistics
    original_mark = statistics.mark_producer_on
    original_partition_scan = core.workers._reconcile_due
    partition_results = []
    marks = 0

    async with core.database.transaction() as conn:
        await original_mark(conn, "memory.index.recovery", watermark="legacy:1", complete=True,
                            detail={"source": "memory.index_attempts"})

    async def fail_marker_after_start(*args, **kwargs):
        nonlocal marks
        marks += 1
        if marks == 1:
            return await original_mark(*args, **kwargs)
        raise RuntimeError("injected statistics marker failure")

    async def partition_scan(*args, **kwargs):
        result = await original_partition_scan(*args, **kwargs)
        partition_results.append(result)
        return result

    monkeypatch.setattr(statistics, "mark_producer_on", fail_marker_after_start)
    monkeypatch.setattr(core.workers, "_reconcile_due", partition_scan)
    with pytest.raises(RuntimeError, match="injected statistics marker failure"):
        await core.workers.reconcile_due(limit=1)
    events, producer, cursor, _ = await read_statistics(core)
    assert partition_results[-1]["scanned"] == 1
    assert not events and cursor["generation"] is None
    assert producer["complete"] is False and producer["watermark"] is None
    assert producer["detail"]["data_generation"] == generation

    monkeypatch.setattr(statistics, "mark_producer_on", original_mark)
    await core.workers.reconcile_due(limit=1)
    events, producer, _, _ = await read_statistics(core)
    assert len(events) == 1 and producer["complete"] is False

    await seed_attempts(core, generation, "rbj-second")

    async def fail_consolidation(*_args, **_kwargs):
        raise RuntimeError("injected consolidation maintenance failure")

    monkeypatch.setattr(core.workers, "_reconcile_due", fail_consolidation)
    with pytest.raises(RuntimeError, match="injected consolidation maintenance failure"):
        await core.workers.reconcile_due(limit=1)
    events, producer, _, total = await read_statistics(core)
    assert len(events) == 2 and total == 2 and producer["complete"] is False


async def test_blocked_index_statistics_does_not_block_partition_maintenance(
        projected_core, ordinary_users, monkeypatch):
    core = projected_core
    context, _ = ordinary_users
    await episodes.episode(core, context, "rbj-blocked-index-statistics")
    entered, release, partition_done = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original_partition_scan = core.workers._reconcile_due

    async def blocked_backfill(*_args, **_kwargs):
        entered.set()
        await release.wait()
        return {"complete": False}

    async def partition_scan(*args, **kwargs):
        result = await original_partition_scan(*args, **kwargs)
        partition_done.set()
        return result

    monkeypatch.setattr(core.workers.index_jobs, "backfill_statistics", blocked_backfill)
    monkeypatch.setattr(core.workers, "_reconcile_due", partition_scan)
    task = asyncio.create_task(core.workers.reconcile_due(limit=1))
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        await asyncio.wait_for(partition_done.wait(), timeout=5)
        assert not task.done()
    finally:
        release.set()
        result = await task
    assert result["scanned"] == 1


async def test_backfill_cursor_fences_generation_and_does_not_wait_for_another_page(projected_core):
    core = projected_core
    original = await current_generation(core)
    replacement = "rbj-new-generation"
    await seed_attempts(core, original, "rbj-z-old")
    await seed_attempts(core, replacement, "rbj-a-new")
    first = await core.workers.index_jobs.backfill_statistics(original, limit=1)
    assert first["watermark"] == "rbj-z-old:1"

    async with core.database.transaction() as conn:
        await conn.execute("SELECT * FROM memory.index_stats_cursor WHERE singleton FOR UPDATE")
        busy = await asyncio.wait_for(
            core.workers.index_jobs.backfill_statistics(original, limit=1), timeout=5)
    assert busy["complete"] is False and busy["reason"] == "cursor_busy_or_missing"

    async with core.database.transaction() as conn:
        await conn.execute("UPDATE runtime.installation_generation SET id=%s WHERE singleton=true", (replacement,))
    with pytest.raises(AppError, match="job_generation_expired"):
        await core.workers.index_jobs.backfill_statistics(original, limit=1)
    events, producer, cursor, _ = await read_statistics(core)
    assert len(events) == 1 and cursor["generation"] == original
    assert producer["detail"]["data_generation"] == original

    result = await core.workers.index_jobs.backfill_statistics(replacement, limit=1)
    events, producer, cursor, total = await read_statistics(core)
    assert result["watermark"] == "rbj-a-new:1" and cursor["generation"] == replacement
    assert cursor["after_index_id"] == "rbj-a-new" and len(events) == 2 and total == 2
    assert producer["complete"] is False and producer["detail"]["data_generation"] == replacement
