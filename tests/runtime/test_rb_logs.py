import asyncio
from datetime import UTC, datetime
import json
import logging
import threading
from types import SimpleNamespace
from uuid import uuid4

import pytest

from tianshu.runtime.errors import AppError
from tianshu.runtime.diagnostics import RuntimeDiagnostics
from tianshu.runtime.events import EventLog
from tianshu.runtime.logging import StructuredFormatter
from tianshu.runtime.rb_logs_output import output_record, secret_values
from tianshu.runtime.rb_logs_query import LogQuery


STAMP = "2026-11-05T00:00:00.123456789+00:00"
START = int(datetime(2026, 11, 5, tzinfo=UTC).timestamp()) * 1_000_000_000
END = START + 3600 * 1_000_000_000


def event(instance, sequence, *, timestamp=STAMP, level="INFO", module="runtime", **extra):
    return {"schema_version": "2.0.0", "timestamp": timestamp, "service": "tianshu",
            "role": "bot-background", "instance_id": instance, "sequence": sequence,
            "event_id": str(uuid4()), "event": "job.finished", "level": level,
            "module": module, "outcome": "succeeded", **extra}


def source(directory, name, rows, *, persisted=None, clean=True, missing_count=0):
    path = directory / (name + ".jsonl")
    data = b"".join(json.dumps(row, ensure_ascii=False).encode() + b"\n" for row in rows)
    path.write_bytes(data)
    state = {"instance_id": name, "role": "bot-background", "persisted_bytes": len(data) if persisted is None else persisted,
             "generated_sequence": len(rows), "persisted_sequence": len(rows), "pending": 0,
             "clean_shutdown": clean, "missing_count": missing_count}
    path.with_suffix(".state.json").write_text(json.dumps(state), encoding="utf-8")
    return path, data


async def test_emit_does_not_wait_for_slow_disk_and_retries_a_partial_write(tmp_path, monkeypatch):
    log = EventLog(tmp_path, "worker")
    first_write = threading.Event()
    allow_write = threading.Event()
    original = log._append_batch
    attempts = 0

    def interrupted(payload, start):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            log.directory.mkdir(parents=True, exist_ok=True)
            log.path.write_bytes(payload[:17])
            first_write.set()
            raise OSError("temporary write failure")
        allow_write.wait(5)
        return original(payload, start)

    monkeypatch.setattr(log, "_append_batch", interrupted)
    await log.open()
    await asyncio.wait_for(log.emit("job.started", "started", job_id=1), 1)
    assert await asyncio.to_thread(first_write.wait, 5)
    await asyncio.wait_for(log.emit("job.finished", "succeeded", job_id=1), 1)
    assert log.ready
    allow_write.set()
    await log.close()
    rows = [json.loads(line) for line in log.path.read_text(encoding="utf-8").splitlines()]
    checkpoint = json.loads(log.state_path.read_text(encoding="utf-8"))
    assert [row["sequence"] for row in rows] == [1, 2]
    assert [row["event"] for row in rows] == ["job.started", "job.finished"]
    assert checkpoint["persisted_sequence"] == 2 and checkpoint["clean_shutdown"] is True
    assert checkpoint["persisted_bytes"] == log.path.stat().st_size


async def test_real_buffer_exhaustion_records_a_gap_without_failing_business(tmp_path, monkeypatch):
    log = EventLog(tmp_path, "worker", max_pending=1, hard_max_pending=2)
    started, release = threading.Event(), threading.Event()
    original = log._append_batch

    def slow(payload, offset):
        started.set()
        release.wait(5)
        return original(payload, offset)

    monkeypatch.setattr(log, "_append_batch", slow)
    await log.open()
    await log.emit("job.started", "started", job_id=1)
    assert await asyncio.to_thread(started.wait, 5)
    for job_id in range(2, 12):
        await asyncio.wait_for(log.emit("job.started", "started", job_id=job_id), 1)
    assert log.status()["missing_count"] > 0
    release.set()
    await log.close()
    checkpoint = json.loads(log.state_path.read_text(encoding="utf-8"))
    assert checkpoint["missing_count"] > 0
    assert checkpoint["possible_missing_first"] <= checkpoint["possible_missing_last"]
    assert checkpoint["clean_shutdown"] is True
    page = LogQuery(tmp_path).page(start_ns=0, end_ns=24 * 3600 * 10**9)
    assert page["coverage"]["complete"] is False
    assert "writer_buffer_exhausted" in page["coverage"]["missing"]


async def test_byte_limit_marks_oversized_buffer_admission_as_an_explicit_gap(tmp_path):
    log = EventLog(tmp_path, "worker", hard_max_pending_bytes=128)
    await log.open()
    await log.emit_output("x" * 512)
    await log.close()
    state = json.loads(log.state_path.read_text(encoding="utf-8"))
    assert state["missing_count"] == 1
    assert state["pending_bytes"] == 0 and state["hard_limit_bytes"] == 128
    page = LogQuery(tmp_path).page(start_ns=0, end_ns=24 * 3600 * 10**9)
    assert "writer_buffer_exhausted" in page["coverage"]["missing"]


def test_structured_output_preserves_severity_and_redacts_secrets_and_content():
    formatter = StructuredFormatter()
    formatter.secrets = ("secret-value",)
    record = logging.LogRecord("tianshu.example", logging.WARNING, __file__, 1,
                               "request failed token=secret-value body={private input}", (), None)
    record.job_id = 17
    line = formatter.format(record)
    output = output_record("bot-background", "process-id", 1, line, formatter.secrets)
    assert output["level"] == "WARNING" and output["module"] == "tianshu.example"
    assert output["job_id"] == 17
    assert "secret-value" not in json.dumps(output) and "private input" not in json.dumps(output)
    assert output_record("vector", "process-id", 2, "ordinary external output")["level"] == "UNCLASSIFIED"
    assert "database-password" in secret_values({"APP_DSN": "postgresql://u:database-password@localhost/db"})


def test_snapshot_pages_enumerate_more_than_server_limit_at_one_timestamp(tmp_path):
    rows = [event("instance-" + str(i % 3), i + 1, level="WARNING" if i % 2 else "ERROR")
            for i in range(1507)]
    for part in range(3):
        source(tmp_path, "source-" + str(part), rows[part::3])
    # A collector copy of the same source event is not a second log.
    source(tmp_path, "source-duplicate", [rows[0]])
    query = LogQuery(tmp_path)
    cursor, actual = None, []
    while True:
        page = query.page(start_ns=START, end_ns=END, cursor=cursor, limit=97)
        actual.extend(page["entries"])
        if not page["next_cursor"]:
            break
        cursor = page["next_cursor"]
    assert len(actual) == 1507
    assert {row["event_id"] for row in actual} == {row["event_id"] for row in rows}
    assert page["coverage"]["complete"] is True
    assert page["next_end_ns"] is None
    warnings = query.page(start_ns=START, end_ns=END, level="WARNING", limit=1000)
    assert len(warnings["entries"]) == len([row for row in rows if row["level"] == "WARNING"])


def test_cursor_freezes_filter_and_source_horizon_late_events_need_refresh(tmp_path):
    rows = [event("one", index) for index in range(1, 6)]
    path, original = source(tmp_path, "one", rows)
    query = LogQuery(tmp_path)
    first = query.page(start_ns=START, end_ns=END, limit=2, role="bot-background")
    late = event("one", 6)
    added = json.dumps(late).encode() + b"\n"
    with path.open("ab") as stream:
        stream.write(added)
    state_path = path.with_suffix(".state.json")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["persisted_bytes"] = len(original) + len(added)
    state_path.write_text(json.dumps(state), encoding="utf-8")
    cursor, seen = first["next_cursor"], list(first["entries"])
    with pytest.raises(AppError, match="log_cursor_query_mismatch"):
        query.page(start_ns=START, end_ns=END, cursor=cursor, level="ERROR")
    while cursor:
        page = query.page(start_ns=START, end_ns=END, limit=2, role="bot-background", cursor=cursor)
        seen.extend(page["entries"])
        cursor = page["next_cursor"]
    assert len(seen) == 5 and late["event_id"] not in {row["event_id"] for row in seen}
    refreshed = query.page(start_ns=START, end_ns=END, limit=10, role="bot-background")
    assert late["event_id"] in {row["event_id"] for row in refreshed["entries"]}


def test_unclean_writer_does_not_claim_tail_was_persisted_or_sent(tmp_path):
    rows = [event("one", 1), event("one", 2)]
    first = json.dumps(rows[0], ensure_ascii=False).encode() + b"\n"
    source(tmp_path, "one", rows, persisted=len(first), clean=False)
    query = LogQuery(tmp_path)
    page = query.page(start_ns=START, end_ns=END)
    by_id = {row["event_id"]: row for row in page["entries"]}
    assert by_id[rows[0]["event_id"]]["persistence"] == "persisted"
    assert by_id[rows[1]["event_id"]]["persistence"] == "unverified"
    assert {"unpersisted_or_unclean_writer", "uncheckpointed_tail"} <= set(page["coverage"]["missing"])
    changes = query.changes()
    assert [row["event_id"] for row in changes["entries"]] == [rows[0]["event_id"]]
    assert changes["coverage"]["complete"] is False


def test_incremental_checkpoint_revisits_each_source_and_accepts_late_timestamps(tmp_path):
    a = [event("a", index) for index in range(1, 4)]
    b = [event("b", index) for index in range(1, 4)]
    path, original = source(tmp_path, "a", a)
    source(tmp_path, "b", b)
    query = LogQuery(tmp_path)
    checkpoint, seen = None, []
    while True:
        page = query.changes(checkpoint=checkpoint, limit=2)
        seen.extend(page["entries"])
        checkpoint = page["next_checkpoint"]
        if page["coverage"]["caught_up"]:
            break
    assert {row["event_id"] for row in seen} == {row["event_id"] for row in a + b}
    late = event("a", 4, timestamp="2026-11-05T00:00:00.000000001+00:00")
    encoded = json.dumps(late).encode() + b"\n"
    with path.open("ab") as stream:
        stream.write(encoded)
    state_path = path.with_suffix(".state.json")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["persisted_bytes"] = len(original) + len(encoded)
    state_path.write_text(json.dumps(state), encoding="utf-8")
    source(tmp_path, "new", [event("new", 1)])
    resumed = query.changes(checkpoint=checkpoint, limit=20)
    repeated = query.changes(checkpoint=checkpoint, limit=20)
    assert [row["event_id"] for row in resumed["entries"]] == [row["event_id"] for row in repeated["entries"]]
    assert len(resumed["entries"]) == 2 and late["event_id"] in {row["event_id"] for row in resumed["entries"]}
    assert all("message" not in row and "body" not in row for row in resumed["entries"])


def test_source_replaced_during_paging_is_reported_instead_of_silent_success(tmp_path):
    path, _ = source(tmp_path, "one", [event("one", index) for index in range(1, 5)])
    query = LogQuery(tmp_path)
    first = query.page(start_ns=START, end_ns=END, limit=1)
    path.write_bytes(b"")
    second = query.page(start_ns=START, end_ns=END, limit=1, cursor=first["next_cursor"])
    assert second["coverage"]["snapshot_complete"] is False
    assert "source_changed" in second["coverage"]["missing"]


def test_page_byte_cap_still_resumes_all_large_records(tmp_path, monkeypatch):
    from tianshu.runtime import rb_logs_query

    rows = [event("one", index, message="ordinary detail " * 30) for index in range(1, 8)]
    source(tmp_path, "one", rows)
    monkeypatch.setattr(rb_logs_query, "MAX_PAGE_BYTES", 1500)
    query = LogQuery(tmp_path)
    checkpoint, seen = None, []
    while True:
        page = query.page(start_ns=START, end_ns=END, limit=100, cursor=checkpoint)
        seen.extend(page["entries"])
        checkpoint = page["next_cursor"]
        if checkpoint is None:
            break
    assert len(seen) == len(rows)
    assert {row["event_id"] for row in seen} == {row["event_id"] for row in rows}


async def test_diagnostics_exposes_filters_redacted_export_and_incremental_metadata(tmp_path, monkeypatch):
    logs = tmp_path / "logs"
    logs.mkdir()
    selected = event("one", 1, level="WARNING", module="runtime", job_id=12,
                     message="authorization=Bearer secret-value body=private-dialogue", body="private-dialogue")
    excluded = event("one", 2, level="ERROR", module="tianshu.conversation", job_id=12)
    source(logs, "one", [selected, excluded])

    class Settings:
        artifact_directory = tmp_path / "artifacts"
        secrets = SimpleNamespace(environ={"APP_TOKEN": "secret-value"})

        def get(self, _name, default=None):
            return default

    diagnostics = RuntimeDiagnostics(None, None, Settings())

    async def pipeline():
        return {"collector": {"status": "unavailable", "collected_sequence": None},
                "loki": {"status": "unavailable"}}

    monkeypatch.setattr(diagnostics, "_log_pipeline", pipeline)
    result = await diagnostics.logs(start_ns=START, end_ns=END, level="WARNING",
                                    job_id=12, exclude_conversation=True)
    assert len(result["entries"]) == 1
    exported = json.dumps(result, ensure_ascii=False)
    assert "secret-value" not in exported and "private-dialogue" not in exported
    assert result["freshness"]["collector"]["status"] == "unavailable"
    changes = await diagnostics.log_changes(limit=10)
    assert [row["level"] for row in changes["entries"]] == ["WARNING", "ERROR"]
    assert all("message" not in row and "body" not in row for row in changes["entries"])


async def test_failure_identifiers_reach_incremental_reader_without_receipt_content(tmp_path):
    log = EventLog(tmp_path, "independent")
    await log.open()
    await log.emit("capability.failed", "failed", module="capabilities",
                   material_id="action-123", failure_id="attempt-456", attempt_id="attempt-456")
    # Only opaque identity strings are accepted for the new references; a
    # mistakenly supplied receipt object must never be serialized as an ID.
    await log.emit("capability.failed", "failed", module="capabilities",
                   material_id={"receipt": "private-result"}, failure_id="{private-result}")
    await log.close()

    changes = LogQuery(tmp_path).changes()
    assert changes["entries"][0]["event"] == "capability.failed"
    assert changes["entries"][0]["material_id"] == "action-123"
    assert changes["entries"][0]["failure_id"] == "attempt-456"
    assert changes["entries"][1]["material_id"] is None
    assert changes["entries"][1]["failure_id"] is None
    assert "private-result" not in log.path.read_text(encoding="utf-8")
