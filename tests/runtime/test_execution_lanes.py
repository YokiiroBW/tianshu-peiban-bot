"""Native queue identity, isolation and bounded startup routing regressions."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path

import pytest

from tianshu.runtime.config import Secrets, Settings
from tianshu.runtime.diagnostics import RuntimeDiagnostics
from tianshu.runtime.events import EventLog
from tianshu.runtime.execution import ExecutionLane
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
        jobs.register(name, busy, execution_lane=lane)
    for name, lane in ready_tasks:
        jobs.register(name, ready, execution_lane=lane)
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


async def test_legacy_routing_isolates_bad_items_and_retains_all_original_identity(
    database, tmp_path,
):
    jobs = JobQueue(database.config.dsn)
    events = EventLog(tmp_path / "events", "worker")
    jobs.events = events

    async def noop(**_kwargs):
        pass

    async def lane_on(connection, payload):
        if payload.get("bad_sql"):
            await connection.execute("SELECT 1/0")
        return ExecutionLane.IMAGES

    jobs.register("memory.consolidate", noop)
    jobs.register("capabilities.execute", noop, lane_resolver=lane_on)
    obsolete = jobs.app.task(name="retired.fixture")(noop)
    due = datetime.now(UTC) + timedelta(hours=1)
    async with database.transaction() as conn:
        ids = []
        for task, payload in (
            (jobs.tasks["capabilities.execute"], {"bad_sql": True, "private": "private-output"}),
            (obsolete, {"private": "private-output"}),
            (jobs.tasks["memory.consolidate"], {"batch_id": "frozen-batch"}),
            (jobs.tasks["capabilities.execute"], {"action_id": "original-action", "attempt_id": "original-attempt"}),
            (jobs.tasks["capabilities.execute"], {"model_status": "unknown"}),
            (jobs.tasks["capabilities.execute"], {"model_status": "failed"}),
        ):
            ids.append(await task.configure(connection=conn, queue="elastic",
                schedule_at=due, priority=37).defer_async(**payload))
        await conn.execute("UPDATE procrastinate_jobs SET attempts=2 WHERE id=%s", (ids[2],))
        await conn.execute("UPDATE procrastinate_jobs SET status='doing' WHERE id=%s", (ids[4],))
        await conn.execute("UPDATE procrastinate_jobs SET status='failed' WHERE id=%s", (ids[5],))
        original = await (await conn.execute("SELECT * FROM procrastinate_jobs ORDER BY id")).fetchall()
    await events.open()
    try:
        result = await jobs.route_legacy_pending(database, batch_size=2)
        assert result["routed"] == 2 and result["blocked_count"] == 2
        assert result["blocked_complete"] is True
        assert {item["job_id"] for item in result["blocked"]} == set(ids[:2])
        async with database.transaction(read_only=True) as conn:
            current = await (await conn.execute("SELECT * FROM procrastinate_jobs ORDER BY id")).fetchall()
            observation = await jobs.observe_on(conn, ids[0])
        for before, after in zip(original, current, strict=True):
            assert {key: value for key, value in before.items() if key != "queue_name"} == {
                key: value for key, value in after.items() if key != "queue_name"}
        assert [row["queue_name"] for row in current] == [
            "elastic", "elastic", "memory_consolidation", "images", "elastic", "elastic",
        ]
        assert observation["execution_lane"] is None
        assert observation["wait_reason"] == "execution_lane_unresolved"
        again = await jobs.route_legacy_pending(database)
        assert again["routed"] == 0 and again["blocked_count"] == 2
    finally:
        await events.close()
    text = "".join(path.read_text() for path in (tmp_path / "events").glob("*.jsonl"))
    logged = [json.loads(line) for line in text.splitlines()]
    assert len(logged) == 4
    assert all(row["event"] == "job.routing_blocked" for row in logged)
    assert "private-output" not in text and "division by zero" not in text


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
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps(document), encoding="utf-8")
    loaded = Settings.load(settings_file)
    settings = replace(loaded, secrets=Secrets({"TIANSHU_DATABASE_URL":
        "postgresql://fixture:fixture@127.0.0.1:1/not-opened"}))
    document = settings.document
    assert len(settings.worker_lanes) == 34 and sum(settings.worker_lanes.values()) == 39
    assert settings.database("worker").max_connections == 4
    # The example has 23 role-pool connections: 23+8+34+1 = 66, not 39 listeners.
    document["database"]["connection_budget"] = 65
    with pytest.raises(ValueError, match="database_connection_budget_exceeded"):
        settings.database("worker")
    document["database"]["connection_budget"] = 66
    assert settings.database("worker").max_connections == 4
    document["worker"]["lanes"] = {"images": 8}
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
