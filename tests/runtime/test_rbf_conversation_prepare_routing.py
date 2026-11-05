"""Conversation preparation shares the existing dynamically selected lanes."""

import pytest

from tianshu.runtime.errors import AppError
from tianshu.runtime.execution import ExecutionLane, TASK_LANES, role_for_lane
from tianshu.runtime.jobs import JobQueue, JobSpec


async def test_prepare_producer_only_route_requires_actual_lane_and_is_observable(database):
    jobs = JobQueue(database.config.dsn, database=database)
    jobs.register_static_routes()
    assert TASK_LANES["conversation.prepare"] == TASK_LANES["conversation.run"] == ExecutionLane.CONVERSATION
    assert "conversation.prepare" in jobs.route_tasks and "conversation.prepare" not in jobs.tasks
    assert "conversation.prepare" in jobs.unresolved_lanes
    async with database.transaction() as connection:
        with pytest.raises(AppError, match="dynamic_job_lane_required"):
            await jobs.enqueue(connection, JobSpec("conversation.prepare", "missing-lane", {"turn_id": "t"}))
        assert not await (await connection.execute(
            "SELECT 1 FROM runtime.job_keys WHERE key='missing-lane'")).fetchone()
        for lane in (ExecutionLane.CONVERSATION, ExecutionLane.PROACTIVE):
            identifier = await jobs.enqueue(connection, JobSpec("conversation.prepare", "prepare-" + lane.value,
                {"turn_id": lane.value}, execution_lane=lane))
            observation = await jobs.observe_on(connection, identifier)
            assert observation["execution_lane"] == lane.value
            assert observation["wait_reason"] == "native_task_queue"
    assert role_for_lane(ExecutionLane.CONVERSATION) == "bot-foreground"
    assert role_for_lane(ExecutionLane.PROACTIVE) == "bot-background"


async def test_prepare_consumer_resolver_selects_existing_lane_from_payload(database):
    jobs = JobQueue(database.config.dsn, database=database)
    jobs.register_static_routes()

    async def prepare(**_payload):
        pass

    async def resolve_lane(_connection, payload):
        return ExecutionLane.PROACTIVE if payload["autonomous"] else ExecutionLane.CONVERSATION

    jobs.register("conversation.prepare", prepare, lane_resolver=resolve_lane)
    assert "conversation.prepare" not in jobs.unresolved_lanes
    async with database.transaction() as connection:
        for autonomous, lane in ((False, ExecutionLane.CONVERSATION), (True, ExecutionLane.PROACTIVE)):
            identifier = await jobs.enqueue(connection, JobSpec("conversation.prepare", "resolved-" + lane.value,
                {"autonomous": autonomous}))
            assert (await jobs.observe_on(connection, identifier))["execution_lane"] == lane.value
