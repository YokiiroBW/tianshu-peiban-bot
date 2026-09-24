"""Bounded Gateway event counts for the A1 unknown-no-resend clone observation."""

import json
import re
from collections import Counter, defaultdict

from .safety import child, require, safe_path

MAX_LOG_FILES = 256
MAX_LOG_BYTES = 256 * 1024**2
MAX_LOG_LINE = 4096
CORRELATION = re.compile(r"^[a-f0-9]{32}$")
TRACKED = {"request.accepted", "upstream.call_started", "upstream.call_finished"}


def gateway_counters(root, budget):
    """Read bounded Gateway JSONL counts without retaining log payloads."""
    log_root = child(root, "logs/gateway")
    require(log_root.is_dir(), "drill_observation_logs_missing")
    paths = sorted(log_root.glob("*.jsonl"))
    require(0 < len(paths) <= MAX_LOG_FILES, "drill_observation_log_inventory_invalid")
    total_size = 0
    bytes_read = 0
    result = defaultdict(lambda: defaultdict(Counter))
    for raw_path in paths:
        budget.check()
        path = safe_path(raw_path)
        require(path.is_file(), "drill_observation_log_inventory_invalid")
        total_size += path.stat().st_size
        require(total_size <= MAX_LOG_BYTES, "drill_observation_log_limit")
        with path.open("rb") as stream:
            while True:
                budget.check()
                line = stream.readline(MAX_LOG_LINE + 1)
                if not line:
                    break
                bytes_read += len(line)
                require(
                    bytes_read <= MAX_LOG_BYTES
                    and len(line) <= MAX_LOG_LINE
                    and line.endswith(b"\n"),
                    "drill_observation_log_invalid",
                )
                try:
                    record = json.loads(line)
                except (ValueError, UnicodeError):
                    require(False, "drill_observation_log_invalid")
                require(isinstance(record, dict), "drill_observation_log_invalid")
                if record.get("event") not in TRACKED:
                    continue
                correlation = record.get("correlation_id")
                event = record["event"]
                outcome = record.get("outcome")
                require(
                    record.get("service") == "gateway"
                    and record.get("schema_version") == "1.0.0"
                    and isinstance(correlation, str)
                    and CORRELATION.fullmatch(correlation) is not None,
                    "drill_observation_log_invalid",
                )
                expected_outcomes = {
                    "request.accepted": {"succeeded"},
                    "upstream.call_started": {"started"},
                    "upstream.call_finished": {"succeeded", "unknown"},
                }[event]
                require(outcome in expected_outcomes, "drill_observation_log_invalid")
                result[correlation][event][outcome] += 1

    counts = {
        correlation: {
            event: dict(sorted(outcomes.items()))
            for event, outcomes in sorted(events.items())
        }
        for correlation, events in sorted(result.items())
    }
    return counts


def summarize_a1_gateway_counts(counts):
    """Require the source's two unknown requests and two successful controls."""
    upstream = {
        correlation
        for correlation, events in counts.items()
        if "upstream.call_started" in events or "upstream.call_finished" in events
    }
    unknown = {
        correlation
        for correlation in upstream
        if counts[correlation].get("upstream.call_finished", {}).get("unknown", 0)
    }
    controls = {
        correlation
        for correlation in upstream
        if counts[correlation].get("upstream.call_finished", {}).get("succeeded", 0)
    }
    require(
        len(unknown) == 2
        and len(controls) == 2
        and unknown.isdisjoint(controls)
        and upstream == unknown | controls,
        "drill_observation_gateway_baseline_invalid",
    )
    for correlation in upstream:
        group = counts[correlation]
        finished = group.get("upstream.call_finished", {})
        require(
            group.get("request.accepted", {}).get("succeeded", 0) >= 1
            and group.get("upstream.call_started", {}).get("started", 0) == 1
            and sum(finished.values()) == 1,
            "drill_observation_gateway_baseline_invalid",
        )
    for correlation, group in counts.items():
        if correlation not in upstream:
            require(
                set(group) == {"request.accepted"}
                and group["request.accepted"].get("succeeded", 0) > 0,
                "drill_observation_gateway_baseline_invalid",
            )
    totals = Counter()
    for events in counts.values():
        for event, outcomes in events.items():
            totals[event] += sum(outcomes.values())
    unknown_finished = sum(
        counts[correlation]["upstream.call_finished"].get("unknown", 0)
        for correlation in unknown
    )
    successful_finished = sum(
        counts[correlation]["upstream.call_finished"].get("succeeded", 0)
        for correlation in controls
    )
    return {
        "correlation_groups": len(counts),
        "upstream_groups": len(upstream),
        "unknown_groups": len(unknown),
        "successful_control_groups": len(controls),
        "accepted_only_control_groups": len(set(counts) - upstream),
        "event_counts": dict(sorted(totals.items())),
        "finished_unknown": unknown_finished,
        "finished_succeeded_control": successful_finished,
    }
