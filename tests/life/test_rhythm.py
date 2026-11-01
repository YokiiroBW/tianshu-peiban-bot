from datetime import UTC,date,datetime
import pytest

from tianshu.life.clock import LifeClock, awake_and_due, day_to_close
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


@pytest.mark.parametrize('wake,sleep,previous,current,expected',[
    ('08:00','23:00',None,'2026-11-02T04:00:00+08:00',None),
    ('08:00','23:00','2026-11-02T00:30:00+08:00','2026-11-02T04:00:00+08:00',None),
    ('08:00','23:00','2026-11-02T08:00:00+08:00','2026-11-02T22:00:00+08:00',None),
    ('08:00','23:00','2026-11-02T22:30:00+08:00','2026-11-02T23:00:00+08:00','2026-11-02'),
    ('08:00','23:00','2026-11-02T23:00:00+08:00','2026-11-02T23:30:00+08:00',None),
    ('08:00','23:00','2026-11-01T23:00:00+08:00','2026-11-02T04:00:00+08:00','2026-11-01'),
    ('18:00','04:00','2026-11-01T23:30:00+08:00','2026-11-02T00:15:00+08:00','2026-11-01'),
    ('08:00','23:00','2026-11-01T22:30:00+08:00','2026-11-04T04:00:00+08:00','2026-11-01'),
])
def test_only_observed_day_or_real_sleep_transition_closes_day(wake,sleep,previous,current,expected):
    row={'timezone':'Asia/Shanghai','wake_at':wake,'sleep_at':sleep,'idle_seconds':5400,
        'last_tick_at':datetime.fromisoformat(previous) if previous else None}
    assert day_to_close(row,datetime.fromisoformat(current))==(date.fromisoformat(expected) if expected else None)


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
