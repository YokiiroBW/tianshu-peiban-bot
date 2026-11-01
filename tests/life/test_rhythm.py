from datetime import UTC,datetime
import pytest

from tianshu.life.clock import LifeClock, awake_and_due
from tianshu.types import ServiceClass


def test_sleep_only_schedules_the_next_wake_boundary():
    row={"timezone":"Asia/Shanghai","wake_at":"08:00","sleep_at":"23:00","idle_seconds":5400}
    awake,due=awake_and_due(row,datetime(2026,10,31,16,30,tzinfo=UTC))
    assert not awake
    assert due==datetime(2026,11,1,0,0,tzinfo=UTC)


def test_an_overnight_routine_stops_at_its_actual_sleep_boundary():
    row={"timezone":"Asia/Shanghai","wake_at":"18:00","sleep_at":"04:00","idle_seconds":5400}
    awake,due=awake_and_due(row,datetime(2026,10,31,19,30,tzinfo=UTC))
    assert awake
    assert due==datetime(2026,10,31,20,0,tzinfo=UTC)


@pytest.mark.asyncio
async def test_clock_boundaries_use_due_capacity_including_recovery():
    submitted = []

    class Jobs:
        async def enqueue(self, _connection, spec):
            submitted.append(spec)

    clock = LifeClock(None, None, Jobs(), None, None)
    due = datetime(2026, 11, 1, 0, 0, tzinfo=UTC)
    row = {"actor_id": "actor", "revision": 3, "next_due_at": due}
    await clock._enqueue(None, row)
    await clock._enqueue(None, row, recovery=True)

    for spec in submitted:
        assert spec.task == "life.clock"
        # JobQueue uses service_class.value as the actual queue name and applies
        # its existing DUE priority/reserved capacity; no new queue is involved.
        assert spec.service_class is ServiceClass.DUE
        assert spec.service_class.value == "due"
        assert spec.scheduled_at == due
        assert spec.payload == {"actor_id": "actor", "expected_revision": 3, "due_at": due.isoformat()}
    assert submitted[1].key.startswith(submitted[0].key + ":recover:")
