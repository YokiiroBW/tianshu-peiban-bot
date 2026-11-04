"""Native queue identity, isolation and bounded startup routing regressions."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path

import pytest

from tianshu.runtime.config import Secrets, Settings
from tianshu.runtime.diagnostics import RuntimeDiagnostics
from tianshu.runtime.execution import ExecutionLane, TASK_LANES
from tianshu.runtime.jobs import JobQueue, JobSpec
from tianshu.types import ServiceClass


@pytest.mark.parametrize("busy_tasks,ready_tasks", [
    ((("memory.consolidate", 1, ExecutionLane.MEMORY_CONSOLIDATION),
      ("fixture.download", 1, ExecutionLane.DOWNLOADS)), (
        ("channels.ingest", ExecutionLane.INBOUND),
        ("conversation.cancel", ExecutionLane.CONTROL),
        ("conversation.run", ExecutionLane.CONVERSATION),
        ("life.contact_due", ExecutionLane.PROACTIVE),
        ("life.commitment_due", ExecutionLane.SCHEDULE),
        ("fixture.image", ExecutionLane.IMAGES),
        ("fixture.reactions", ExecutionLane.REACTIONS),
        ("fixture.speech", ExecutionLane.SPEECH),
        ("memory.index_object", ExecutionLane.MEMORY_INDEX),
        ("companion.organize", ExecutionLane.GROWTH),
    )),
    ((("conversation.run", 2, ExecutionLane.CONVERSATION),), (
        ("channels.ingest", ExecutionLane.INBOUND),
        ("conversation.expression_release", ExecutionLane.DELIVERY),
        ("conversation.cancel", ExecutionLane.CONTROL),
    )),
    ((("life.chapter_generate", 1, ExecutionLane.WRITING),), (
        ("life.diary_review", ExecutionLane.DIARY),
        ("life.diary_collect", ExecutionLane.DIARY),
        ("life.diary_generate", ExecutionLane.DIARY),
    )),
])
async def test_busy_business_family_does_not_claim_other_families(
    database, busy_tasks, ready_tasks,
):
    jobs = JobQueue(database.config.dsn)
    started = asyncio.Queue()
    release = asyncio.Event()
    completed = []

    async def busy(identity):
        await started.put(identity)
        await release.wait()

    async def ready(identity):
        completed.append(identity)

    for name, _count, lane in busy_tasks:
        jobs.register(name, busy, execution_lane=None if name in TASK_LANES else lane)
        assert jobs.task_lanes[name] == lane
    for name, lane in ready_tasks:
        jobs.register(name, ready, execution_lane=None if name in TASK_LANES else lane)
        assert jobs.task_lanes[name] == lane
    async with database.transaction() as conn:
        for name, count, _lane in busy_tasks:
            for index in range(count):
                await jobs.enqueue(conn, JobSpec(name, f"busy:{name}:{index}", {"identity": index}))
        for name, _lane in ready_tasks:
            # Equal model priority cannot make these share an execution slot.
            await jobs.enqueue(conn, JobSpec(name, name, {"identity": name}, ServiceClass.ELASTIC))
    await jobs.open()
    workers = []
    try:
        for _name, count, lane in busy_tasks:
            workers.append(asyncio.create_task(jobs.app.run_worker_async(
                queues=[lane.value], concurrency=count, wait=False,
                listen_notify=False, install_signal_handlers=False,
            )))
        for _ in range(sum(count for _name, count, _lane in busy_tasks)):
            await asyncio.wait_for(started.get(), timeout=5)
        for _name, lane in ready_tasks:
            workers.append(asyncio.create_task(jobs.app.run_worker_async(
                queues=[lane.value], concurrency=1, wait=False,
                listen_notify=False, install_signal_handlers=False,
            )))
        await asyncio.wait_for(asyncio.gather(*workers[len(busy_tasks):]), timeout=5)
        assert set(completed) == {name for name, _lane in ready_tasks}
        assert all(not worker.done() for worker in workers[:len(busy_tasks)])
    finally:
        release.set()
        for worker in workers:
            if not worker.done():
                worker.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        await jobs.close()


async def test_pending_reroute_preserves_job_and_refuses_started_or_retried_work(database):
    jobs = JobQueue(database.config.dsn)

    async def noop(**_kwargs):
        pass

    jobs.register("fixture.pending", noop, execution_lane=ExecutionLane.MAINTENANCE)
    due = datetime.now(UTC) + timedelta(hours=1)
    async with database.transaction() as conn:
        job_id = await jobs.enqueue(conn, JobSpec("fixture.pending", "original-key",
            {"attempt_id": "original-attempt"}, scheduled_at=due))
        assert not await jobs.reroute_pending(conn, job_id, task="wrong.task",
            execution_lane=ExecutionLane.IMAGES)
        assert await jobs.reroute_pending(conn, job_id, task="fixture.pending",
            execution_lane=ExecutionLane.IMAGES, service_class=ServiceClass.DUE)
        row = await (await conn.execute("SELECT * FROM procrastinate_jobs WHERE id=%s", (job_id,))).fetchone()
        assert row["queue_name"] == "images" and row["priority"] == 100
        assert row["attempts"] == 0 and row["scheduled_at"] == due
        assert row["args"] == {"attempt_id": "original-attempt"}
        assert await jobs.enqueue(conn, JobSpec("fixture.pending", "original-key", {})) == job_id
        await conn.execute("UPDATE procrastinate_jobs SET status='doing' WHERE id=%s", (job_id,))
        assert not await jobs.reroute_pending(conn, job_id, execution_lane=ExecutionLane.SPEECH)
        await conn.execute("UPDATE procrastinate_jobs SET status='todo',attempts=1 WHERE id=%s", (job_id,))
        assert not await jobs.reroute_pending(conn, job_id, execution_lane=ExecutionLane.SPEECH)
        observed = await jobs.observe_on(conn, key="original-key")
        assert observed["job_id"] == job_id and observed["execution_lane"] == "images"
        assert observed["wait_reason"] == "scheduled"


async def test_task_diagnostics_and_action_observation_use_the_same_database_clock(database):
    jobs = JobQueue(database.config.dsn)

    async def noop(**_kwargs):
        pass

    jobs.register("fixture.diagnostic", noop, execution_lane=ExecutionLane.IMAGES)
    async with database.transaction() as conn:
        job_id = await jobs.enqueue(conn, JobSpec("fixture.diagnostic", "diagnostic", {},
            scheduled_at=datetime.now(UTC) + timedelta(hours=1)))
        action_view = await jobs.observe_on(conn, job_id)
    view = await RuntimeDiagnostics(database, None, None).tasks()
    task_view = next(row for row in view["jobs"] if row["id"] == job_id)
    assert (task_view["execution_lane"], task_view["wait_reason"]) == (
        action_view["execution_lane"], action_view["wait_reason"])
    assert task_view["wait_reason"] == "scheduled"
    assert "scheduled" not in task_view and "args" not in task_view
    assert {"jobs", "counts", "limit", "complete"} <= view.keys()


def test_connection_budget_counts_listeners_once_per_lane_and_keeps_schema_aligned(tmp_path):
    document = json.loads((Path(__file__).resolve().parents[2] /
        "deploy/core/settings.example.json").read_text(encoding="utf-8"))
    document["worker"]["lanes"]["diary"] = 1
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps(document), encoding="utf-8")
    loaded = Settings.load(settings_file)
    settings = replace(loaded, secrets=Secrets({"TIANSHU_DATABASE_URL":
        "postgresql://fixture:fixture@127.0.0.1:1/not-opened"}))
    document = settings.document
    assert len(settings.worker_lanes) == 35 and sum(settings.worker_lanes.values()) == 40
    assert settings.database("worker").max_connections == 4
    # The example has 23 role-pool connections: 23+8+35+1 = 67, not 40 listeners.
    document["database"]["connection_budget"] = 66
    with pytest.raises(ValueError, match="database_connection_budget_exceeded"):
        settings.database("worker")
    document["database"]["connection_budget"] = 67
    assert settings.database("worker").max_connections == 4
    document["worker"]["lanes"] = {"images": 8}
    assert settings.worker_lanes["diary"] == 1
    assert settings.database("worker").max_connections == 4
    settings_file.write_text(json.dumps({**document, "worker": {"elastic_concurrency": 2}}), encoding="utf-8")
    with pytest.raises(ValueError, match="invalid_configuration:worker:additionalProperties"):
        Settings.load(settings_file)


async def test_new_jobs_use_server_resolver_without_changing_model_priority(database):
    jobs = JobQueue(database.config.dsn)

    async def noop(**_kwargs):
        pass

    async def lane_on(connection, payload):
        assert connection is not None
        return ExecutionLane.IMAGES if payload["kind"] == "image" else ExecutionLane.PERCEPTION

    jobs.register("fixture.dynamic", noop, lane_resolver=lane_on)
    async with database.transaction() as conn:
        for service_class in (ServiceClass.INTERACTIVE, ServiceClass.DUE, ServiceClass.ELASTIC):
            identifier = await jobs.enqueue(conn, JobSpec("fixture.dynamic", service_class.value,
                {"kind": "image"}, service_class))
            row = await jobs.observe_on(conn, identifier)
            assert row["execution_lane"] == "images"
            assert row["priority"] == (100 if service_class == ServiceClass.DUE else 0)


async def test_sync_repository_enqueue_uses_the_same_business_route_and_identity(database):
    jobs = JobQueue(database.config.dsn)

    async def noop(**_kwargs):
        pass

    jobs.register("capabilities.infrastructure_job", noop)
    spec = JobSpec("capabilities.infrastructure_job", "sync-original",
        {"operation_id": "original-operation"}, ServiceClass.ELASTIC)

    def enqueue(connection):
        first = jobs.enqueue_sync(connection, spec)
        assert jobs.enqueue_sync(connection, spec) == first
        return first

    identifier = await database.run_sync(enqueue)
    async with database.transaction(read_only=True) as connection:
        observed = await jobs.observe_on(connection, key="sync-original")
        assert observed["job_id"] == identifier
        assert observed["execution_lane"] == "infrastructure" and observed["attempts"] == 0
