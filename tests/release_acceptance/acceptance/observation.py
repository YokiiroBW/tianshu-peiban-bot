"""Elapsed monotonic time, bounded probe gaps, and per-sample durable checkpoints."""

import time
from pathlib import Path

from .evidence import canonical, digest, utc
from .inputs import ROLES, require
from .health import validate_ready
from .suite import env_value
from .transport import Client


def observe(config, report, directory, duration=86400, interval=30):
    require(
        0 < duration <= 7 * 86400 and 0 < interval <= 60, "observation_duration_invalid"
    )
    path = Path(directory) / "observation.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    require(not path.exists(), "observation_journal_already_exists")
    clients = {
        role: Client(config["endpoints"][role], config["runtime_kind"] == "synthetic")
        for role in ROLES
    }
    # Preflight credentials before any network traffic.
    tokens = {
        role: env_value(config["endpoints"][role].get("diagnostics_token_env"))
        for role in ROLES
    }
    start, last, max_gap, count, failures, previous = (
        time.monotonic(),
        None,
        0,
        0,
        0,
        "0" * 64,
    )
    interrupted = False
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        try:
            while True:
                sample_start = time.monotonic()
                age = sample_start - start
                gap = sample_start - last if last is not None else 0
                last, max_gap = sample_start, max(max_gap, gap)
                health = {}
                for role, client in clients.items():
                    try:
                        status, body = client.request(
                            "GET",
                            "/health/ready",
                            headers={"Authorization": "Bearer " + tokens[role]},
                        )
                        validate_ready(role, status, body)
                        health[role] = "ready"
                    except Exception:
                        health[role] = "failed"
                        failures += 1
                sample = {
                    "run_id": report.data["run_id"],
                    "sequence": count + 1,
                    "timestamp": utc(),
                    "elapsed_seconds": round(age, 6),
                    "gap_seconds": round(gap, 6),
                    "health": health,
                    "previous_sha256": previous,
                }
                previous = digest(canonical(sample))
                stream.write(canonical({**sample, "sha256": previous}).decode() + "\n")
                stream.flush()
                import os

                os.fsync(stream.fileno())
                count += 1
                if age >= duration:
                    break
                time.sleep(min(interval, max(0, duration - (time.monotonic() - start))))
        except KeyboardInterrupt:
            interrupted = True
    elapsed = time.monotonic() - start
    # A suspended machine/blocked loop must not count the unobserved period as healthy.
    completed = (
        not interrupted
        and elapsed >= 86400
        and count >= 2
        and failures == 0
        and max_gap <= interval + 30
    )
    status = "pass" if completed else "fail" if failures else "not_run"
    report.add(
        "observation_24h",
        status,
        "observed_24h"
        if completed
        else "probe_failed"
        if failures
        else "observation_incomplete",
        {
            "elapsed_seconds": round(elapsed, 3),
            "requested_seconds": duration,
            "samples": count,
            "probe_failures": failures,
            "max_gap_seconds": round(max_gap, 3),
            "interval_seconds": interval,
            "interrupted": interrupted,
            "journal_head_sha256": previous,
            "journal_sha256": digest(path.read_bytes()),
            "coverage": "readiness_only",
        },
    )
    report.data["claims"]["observation_24h"] = "pass" if completed else "not_run"
    # This does not exercise 24 hours of dialogue, memory, backup, or log throughput.
    report.add(
        "observation_workload",
        "not_run",
        "readiness_observation_is_not_sustained_workload",
    )
    return report
