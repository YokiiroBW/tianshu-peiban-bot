from datetime import UTC,datetime
from tianshu.life.clock import awake_and_due


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
