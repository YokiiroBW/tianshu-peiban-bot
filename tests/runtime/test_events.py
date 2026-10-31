import json

import pytest

from tianshu.runtime.errors import AppError
from tianshu.runtime.events import EventLog


async def test_complete_event_sequence_is_durable_and_contains_only_safe_metadata(tmp_path):
    events = EventLog(tmp_path, "interactive")
    await events.open()
    try:
        for _ in range(12):
            await events.emit("http.finished", "succeeded", route="/api/v1/models/catalog",
                              method="GET", status=200, correlation_id="fixture-request")
    finally:
        await events.close()
    rows = [json.loads(line) for path in tmp_path.glob("*.jsonl") for line in path.read_text().splitlines()]
    assert [row["sequence"] for row in rows] == list(range(1, 13))
    assert all(row["route"] == "/api/v1/models/catalog" for row in rows)
    assert all("body" not in row and "credential" not in row for row in rows)


async def test_closed_log_refuses_new_events_without_deleting_existing_files(tmp_path):
    events = EventLog(tmp_path, "worker")
    await events.open()
    await events.emit("runtime.started", "succeeded")
    await events.close()
    with pytest.raises(AppError, match="runtime_log_unavailable"):
        await events.emit("runtime.started", "succeeded")
    assert len(list(tmp_path.glob("*.jsonl"))) == 1
