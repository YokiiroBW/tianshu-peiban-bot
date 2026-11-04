import json
from types import SimpleNamespace

import pytest

from tianshu.runtime.errors import AppError
from tianshu.runtime.events import EventLog
from tianshu.runtime.jobs import ExecutionLane, JobQueue, JobSpec


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


@pytest.mark.parametrize(
    "failure,expected_code",
    [
        (
            AppError("reflection_conditions_invalid", "private-exception-message"),
            "reflection_conditions_invalid",
        ),
        (RuntimeError("private-exception-message"), "RuntimeError"),
        (AppError("invalid\ncode", "private-exception-message"), "unclassified_error"),
        (AppError("x" * 97, "private-exception-message"), "unclassified_error"),
    ],
)
async def test_failed_registered_job_persists_only_sanitized_error_identity(
    tmp_path, database, failure, expected_code
):
    events = EventLog(tmp_path, "worker")
    jobs = JobQueue(database.config.dsn, database=database)
    jobs.events = events

    async def failing(**_kwargs):
        raise failure

    task = jobs.register("fixture.failure", failing, execution_lane=ExecutionLane.MAINTENANCE)
    async with database.transaction() as connection:
        job_id = await jobs.enqueue(connection, JobSpec(
            "fixture.failure", "failure", {"model_text": "private-task-input"}
        ))
    await jobs.open()
    await events.open()
    try:
        with pytest.raises(type(failure)) as raised:
            await task(SimpleNamespace(job=SimpleNamespace(id=job_id)), model_text="private-task-input")
        assert raised.value is failure
    finally:
        await events.close()
        await jobs.close()
    text = "".join(path.read_text() for path in tmp_path.glob("*.jsonl"))
    rows = [json.loads(line) for line in text.splitlines()]
    assert [row["event"] for row in rows] == ["job.started", "job.failed"]
    assert rows[-1]["error_code"] == expected_code
    assert rows[-1]["task_name"] == "fixture.failure" and rows[-1]["job_id"] == job_id
    assert "private-exception-message" not in text and "private-task-input" not in text
