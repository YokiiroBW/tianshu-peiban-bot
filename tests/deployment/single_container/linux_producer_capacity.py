"""Installed producer capacity recovery in a network-none Linux image, as 10001.

Only a new /tmp output is written. No app, database, model, contract or network
client is started. Run with the image's Python; stdlib is sufficient for this tool.
"""

import argparse
import asyncio
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import sys
import time


class Refused(RuntimeError):
    pass


def require(value, code):
    if not value:
        raise Refused(code)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def installed(module):
    path = Path(module.__file__).resolve()
    require(path.is_relative_to(Path(sys.prefix).resolve()), "source_overlay_refused")
    return digest(path)


def until(predicate, deadline, code):
    while not predicate():
        require(time.monotonic() < deadline, code)
        time.sleep(0.01)


def lines(directory):
    rows = []
    for path in sorted(directory.glob("*.jsonl*")):
        raw = path.read_bytes()
        require(not raw or raw.endswith(b"\n"), "partial_log_tail")
        records = [json.loads(line) for line in raw.splitlines()]
        numbers = [row["sequence"] for row in records]
        require(numbers == sorted(numbers), "segment_sequence_order_changed")
        rows.extend(records)
    numbers = [row["sequence"] for row in rows]
    require(
        all(type(number) is int and number > 0 for number in numbers)
        and len(set(numbers)) == len(numbers),
        "duplicate_or_invalid_sequence",
    )
    return rows


def sealed_cleanup(directory, protected):
    require(
        protected.is_file() and protected.parent == directory, "active_tail_required"
    )
    before = protected.read_bytes()
    removed = 0
    for path in directory.glob("*.jsonl*"):
        if path != protected:
            path.unlink()
            removed += 1
    require(removed > 0, "sealed_prefix_required")
    require(protected.read_bytes() == before, "active_tail_changed")
    return before, removed


def deny_directory_write(directory):
    directory.chmod(0o500)
    path = directory / ".ts369-permission-check.tmp"
    try:
        with path.open("xb") as stream:
            stream.write(b"synthetic permission check\n")
    except PermissionError:
        return
    finally:
        if path.exists():
            path.unlink()
    raise Refused("actual_directory_permission_refusal_required")


async def memory_queue(adapter):
    tasks = [
        asyncio.create_task(adapter.aemit("request.started", outcome="started"))
        for _ in range(3)
    ]
    await asyncio.sleep(0)
    observed = adapter.writer.admitted
    require(all(await asyncio.gather(*tasks)), "memory_accepted_record_lost")
    return observed


def memory_case(module, root, deadline):
    directory = root / "memory"
    directory.mkdir(mode=0o700)
    adapter = module.Diagnostics(module.CHAT_SERVICE, {"log_directory": str(directory)})
    # These are the actual native sink's existing testable bounds, changed before
    # its first event. No writer, permission check or fsync implementation is replaced.
    adapter.sink.segment_bytes = 1024
    adapter.sink.directory_bytes = 4096
    try:
        for _ in range(50):
            require(time.monotonic() < deadline, "memory_fill_deadline")
            if not adapter.emit("runtime.started"):
                break
        else:
            raise Refused("memory_capacity_not_reached")
        require(adapter.log_state() == "log_capacity", "memory_not_capacity_red")
        until(
            lambda: adapter.writer.admitted == 0, deadline, "memory_pending_not_settled"
        )
        sequence = adapter.sink.sequence
        require(not adapter.recover(), "memory_still_full_recovered")
        require(adapter.sink.sequence == sequence, "memory_proof_spent_sequence")
        protected = sorted(directory.glob("*.jsonl"))[-1]
        open_at_capacity = adapter.sink._handle is not None
        before, removed = sealed_cleanup(directory, protected)
        try:
            deny_directory_write(directory)
            require(not adapter.recover(), "memory_permission_failure_became_ready")
            require(adapter.log_state() == "log_capacity", "memory_permission_red_lost")
        finally:
            directory.chmod(0o700)
        until(
            lambda: adapter.log_state() == "ok",
            deadline,
            "memory_auto_recovery_deadline",
        )
        require(
            adapter.sink.sequence == sequence, "memory_maintenance_changed_sequence"
        )
        require(protected.read_bytes() == before, "memory_tail_changed")
        require(adapter.emit("runtime.ready"), "memory_real_append_failed")
        pending = asyncio.run(memory_queue(adapter))
        until(
            lambda: adapter.writer.admitted == 0, deadline, "memory_queue_not_drained"
        )
        rows = lines(directory)
        require(
            sorted(row["sequence"] for row in rows if row["sequence"] > sequence)
            == list(range(sequence + 1, sequence + 5)),
            "memory_sequence_or_queue_loss",
        )
        require(
            not list(directory.glob(".tianshu-log-probe-*.tmp")),
            "memory_probe_file_leaked",
        )
        return {
            "auto_recovered": True,
            "real_chmod_refused": True,
            "sealed_removed": removed,
            "tail_preserved": True,
            "open_handle_at_capacity": open_at_capacity,
            "queued_settled": 3,
            "pending_observed": pending,
            "sequence_delta": adapter.sink.sequence - sequence,
            "sink_type": type(adapter.sink),
        }
    finally:
        directory.chmod(0o700)
        require(adapter.shutdown(), "memory_writer_shutdown_unconfirmed")
        require(
            adapter.writer.thread is None or not adapter.writer.thread.is_alive(),
            "memory_writer_still_alive",
        )


def knowledge_case(module, root, sink_type):
    directory = root / "knowledge"
    directory.mkdir(mode=0o700)
    adapter = module.Diagnostics(
        module.KNOWLEDGE_SERVICE, {"log_directory": str(directory)}
    )
    try:
        require(type(adapter.sink) is sink_type, "knowledge_different_sink")
        require(adapter.emit("runtime.started"), "knowledge_real_write_failed")
        rows = lines(directory)
        require(
            len(rows) == 1 and rows[0]["service"] == module.KNOWLEDGE_SERVICE,
            "knowledge_service_changed",
        )
        return {"same_sink_as_memory": True, "real_durable_records": 1}
    finally:
        require(adapter.shutdown(), "knowledge_writer_shutdown_unconfirmed")


def companion_case(module, root, deadline):
    directory = root / "companion"
    directory.mkdir(mode=0o700)
    adapter = module.LogAdapter(directory, max_segment_bytes=512, drain_seconds=2)
    adapter.max_directory_bytes = 4096
    try:
        for _ in range(50):
            require(time.monotonic() < deadline, "companion_fill_deadline")
            if not asyncio.run(adapter.admit("runtime.background_work", "succeeded")):
                break
        else:
            raise Refused("companion_capacity_not_reached")
        require(adapter.health() == "capacity_exhausted", "companion_not_capacity_red")
        sequence = adapter._sequence
        require(not adapter.probe(), "companion_still_full_recovered")
        require(adapter._sequence == sequence, "companion_full_proof_spent_sequence")
        protected = adapter._segment_path(adapter._segment_index)
        require(
            adapter._stream is not None and not adapter._stream.closed,
            "companion_active_handle_required",
        )
        before, removed = sealed_cleanup(directory, protected)
        try:
            deny_directory_write(directory)
            require(not adapter.probe(), "companion_permission_failure_became_ready")
            require(
                adapter.degraded and not adapter.accepts_business(),
                "companion_permission_red_lost",
            )
            # An existing writable FD survives chmod. The real proof must have
            # rotated and met the directory's refusal when opening the next file.
            require(
                adapter._stream is not None and adapter._stream.closed,
                "companion_permission_open_not_exercised",
            )
            permission_sequence = adapter._sequence
        finally:
            directory.chmod(0o700)
        until(
            lambda: adapter.health() == "ok",
            deadline,
            "companion_auto_recovery_deadline",
        )
        require(protected.read_bytes() == before, "companion_active_tail_changed")
        recovered_sequence = adapter._sequence
        require(
            recovered_sequence > permission_sequence > sequence,
            "companion_permission_number_reused",
        )
        pending = 0
        dropped = adapter.dropped
        for _ in range(3):
            require(
                adapter.emit("runtime.background_work", "succeeded", fsync=True),
                "companion_queue_refused",
            )
            pending = max(pending, adapter.pending)
        require(adapter.flush(), "companion_queue_not_drained")
        require(
            adapter.dropped == dropped and adapter.health() == "ok",
            "companion_accepted_record_lost",
        )
        require(
            asyncio.run(adapter.admit("runtime.background_work", "succeeded")),
            "companion_real_append_failed",
        )
        rows = lines(directory)
        require(
            sorted(
                row["sequence"] for row in rows if row["sequence"] > recovered_sequence
            )
            == list(range(recovered_sequence + 1, recovered_sequence + 5)),
            "companion_sequence_or_queue_loss",
        )
        require(
            any(row["event"] == "runtime.log_probe" for row in rows),
            "companion_no_durable_recovery_proof",
        )
        return {
            "auto_recovered": True,
            "real_chmod_refused": True,
            "sealed_removed": removed,
            "active_tail_preserved": True,
            "active_open_at_cleanup": True,
            "queued_settled": 3,
            "pending_observed": pending,
            "post_recovery_sequence_delta": adapter._sequence - recovered_sequence,
            "permission_attempt_numbers_not_reused": True,
        }
    finally:
        directory.chmod(0o700)
        require(adapter.close(), "companion_writer_shutdown_unconfirmed")


def run(output, seconds, receipt):
    require(sys.platform == "linux", "linux_required")
    require(os.getuid() == 10001 and os.getgid() == 10001, "uid_gid_10001_required")
    interfaces = {
        line.split(":", 1)[0].strip()
        for line in Path("/proc/net/dev").read_text().splitlines()
        if ":" in line
    }
    require(interfaces == {"lo"}, "network_none_required")
    require(
        importlib.metadata.version("tianshu-storage") == "0.1.3",
        "shared_storage_version_changed",
    )
    from tianshu_companion import observability as companion
    from tianshu_memory import diagnostics as memory

    receipt["code_sha256"] = {
        "memory": installed(memory),
        "companion": installed(companion),
    }
    require(
        callable(getattr(memory.Diagnostics, "recover", None)),
        "memory_recovery_api_missing",
    )
    path = Path(output).resolve()
    require(
        path.is_relative_to(Path("/tmp").resolve()) and path != Path("/tmp"),
        "new_tmp_output_required",
    )
    require(not path.exists(), "new_output_required")
    path.mkdir(mode=0o700)
    receipt["phase"] = "memory"
    deadline = time.monotonic() + seconds
    receipt["memory"] = memory_case(memory, path, deadline)
    sink_type = receipt["memory"].pop("sink_type")
    receipt["phase"] = "knowledge"
    receipt["knowledge"] = knowledge_case(memory, path, sink_type)
    receipt["phase"] = "companion"
    receipt["companion"] = companion_case(companion, path, deadline)
    receipt["phase"] = "complete"
    receipt["status"] = "passed"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--seconds", type=float, default=18)
    args = parser.parse_args(argv)
    started = time.monotonic()
    receipt = {
        "status": "failed",
        "phase": "preconditions",
        "limits": {
            "unknown_real_fsync_fault": "not_exercised_no_privileged_fault_filesystem",
            "knowledge_capacity": "same_native_sink_only_not_a_duplicate_stress_run",
            "pending": "real_unheld_io_no_claim_of_sustained_kernel_block",
        },
    }
    try:
        require(0 < args.seconds <= 30, "invalid_deadline")
        run(args.output, args.seconds, receipt)
    except Refused as error:
        receipt["code"] = str(error)
    except Exception:  # noqa: BLE001 - no private exception content on stdout
        receipt["code"] = "producer_probe_unexpected"
    receipt["elapsed_seconds"] = round(time.monotonic() - started, 3)
    print(json.dumps(receipt, sort_keys=True), flush=True)
    return 0 if receipt["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
