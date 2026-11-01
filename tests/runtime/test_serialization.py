"""Shared calendar-date snapshots keep the existing timestamp contract."""

from datetime import UTC, date, datetime, timedelta, timezone
import json

import pytest

from tianshu.runtime.serialization import wire


@pytest.mark.parametrize("timestamp,expected", [
    (datetime(2026, 11, 2, 3, 4, 5, 123456, tzinfo=UTC),
     "2026-11-02T03:04:05.123456+00:00"),
    (datetime(2026, 11, 2, 3, 4, 5, 123456, tzinfo=timezone(timedelta(hours=8))),
     "2026-11-02T03:04:05.123456+08:00"),
])
def test_nested_calendar_dates_are_json_serializable_without_changing_timestamps(timestamp, expected):
    snapshot = {"day": date(2026, 11, 2), "updated_at": timestamp,
                "related": [{"day": date(2024, 2, 29)}]}
    assert json.loads(json.dumps(wire(snapshot))) == {
        "day": "2026-11-02", "updated_at": expected,
        "related": [{"day": "2024-02-29"}],
    }


def test_naive_datetime_is_rejected_before_calendar_date_handling():
    with pytest.raises(ValueError, match="^naive_datetime$"):
        wire({"day": date(2026, 11, 2), "updated_at": datetime(2026, 11, 2, 3, 4, 5)})
