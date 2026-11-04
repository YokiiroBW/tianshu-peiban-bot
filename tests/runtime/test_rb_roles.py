"""Role ownership and independent worker supervision."""

import asyncio

from tianshu.runtime.database import resource_budget
from tianshu.runtime.execution import (
    ExecutionLane, PROCESS_ROLES, ROLE_GROUPS, TASK_LANES, role_for_lane, role_worker_groups,
)
from tianshu.runtime.process import supervise_consumer
from tianshu.runtime.jobs import FairJobManager
from tianshu.runtime import process


def test_every_execution_lane_has_one_owner_and_budget_includes_real_listeners():
    lanes = [lane for groups in ROLE_GROUPS.values() for group in groups.values() for lane in group]
    assert len(lanes) == len(set(lanes))
    assert set(lanes) | {ExecutionLane.OPERATIONS} == set(ExecutionLane)
    assert role_for_lane(ExecutionLane.LIFE_CONTROL) == "bot-background"
    assert role_for_lane(ExecutionLane.CAPABILITY_CONTROL) == "independent"
    assert role_for_lane(ExecutionLane.DOWNLOADS) == "independent"
    assert set(PROCESS_ROLES) == {"interactive", "model-broker", "bot-foreground",
                                  "bot-background", "independent", "operations"}
    assert not role_worker_groups({}, "interactive")
    assert not role_worker_groups({}, "model-broker")
    assert not role_worker_groups({}, "operations")
    assert role_for_lane(ExecutionLane.OPERATIONS) == "operations"
    result = resource_budget({"database": {"connection_budget": 200}, "worker": {"queue_connections": 5}})
    pool = sum(item["business_connections"] + item["control_connections"] + item["sync_connections"]
               + item["queue_connections"] + item["queue_listeners"] + item["broker_owner_connections"]
               for item in result["roles"].values())
    assert result["postgres_connections"] == pool
    assert result["model_client_http_connections"] == 5 * (8 + 4 + 24 + 4)
    assert result["model_http_connections"] == 5 * 40 + 64 + 4
    tailored = resource_budget({"broker": {"transport": {"roles": {
        "bot-background": {"background_connections": 3}}}}})
    assert tailored["model_client_http_connections_by_role"]["bot-background"] == 8 + 4 + 3 + 4


def test_new_notification_routes_reach_the_owning_workers():
    expected = {
        "channels.statistics_reconcile": ExecutionLane.CONTROL,
        "conversation.supplement_notify": ExecutionLane.CONTROL,
        "conversation.example_usage": ExecutionLane.CONTROL,
        "conversation.statistics_reconcile": ExecutionLane.CONTROL,
        "conversation.model_complete": ExecutionLane.CONVERSATION,
        "channels.media_supplement": ExecutionLane.CONVERSATION,
        "channels.media_supplement_expire": ExecutionLane.CONVERSATION,
        "channels.resume_delivery": ExecutionLane.CONVERSATION,
        "companion.growth.input_retry": ExecutionLane.GROWTH,
        "life.experience_statistics_backfill": ExecutionLane.DIARY,
    }
    assert {task: TASK_LANES[task] for task in expected} == expected
    assert role_for_lane(TASK_LANES["channels.media_supplement"]) == "bot-foreground"
    assert role_for_lane(TASK_LANES["life.experience_statistics_backfill"]) == "bot-background"


async def test_one_exited_consumer_does_not_cancel_another(monkeypatch):
    live_started = asyncio.Event()
    release = asyncio.Event()

    class App:
        def perform_import_paths(self):
            pass

    class FakeWorker:
        def __init__(self, *, name, **_kwargs):
            self.name = name
            self.worker_id = None

        async def run(self):
            self.worker_id = 1
            if self.name.endswith("broken"):
                raise RuntimeError("broken listener")
            live_started.set()
            await release.wait()

    monkeypatch.setattr(process, "Worker", FakeWorker)

    class Queue:
        app = App()

    stop = asyncio.Event()
    registration = {"consumers": {"broken": {}, "live": {}}}
    group = {"queues": ["memory_index"], "concurrency": 2}
    broken = asyncio.create_task(supervise_consumer(Queue(), "broken", group, registration, stop, restart_limit=0))
    live = asyncio.create_task(supervise_consumer(Queue(), "live", group, registration, stop, restart_limit=0))
    await live_started.wait()
    await broken
    assert registration["consumers"]["broken"]["state"] == "failed"
    assert not live.done()
    stop.set()
    release.set()
    await live


async def test_busy_high_priority_lane_cannot_starve_other_lane(monkeypatch):
    async def native_fetch(_manager, queue, worker_id):
        assert worker_id == 7
        return queue  # Both native queues always have work.

    monkeypatch.setattr(FairJobManager, "_fetch_one", native_fetch)
    manager = FairJobManager(None)
    queues = ["due", "background"]
    assert [await manager.fetch_job(queues, 7) for _ in range(6)] == [
        "due", "background", "due", "background", "due", "background"]
