"""Validate actual diagnostics/v1 closed records and causal assertions."""

import json
import math
import re
import uuid
from datetime import datetime

from .transport import check

FIELDS = {
    "schema_version",
    "timestamp",
    "service",
    "instance_id",
    "sequence",
    "event_id",
    "level",
    "event",
    "outcome",
    "correlation_id",
    "duration_ms",
    "error_code",
}
OUTCOMES = {
    "started",
    "succeeded",
    "failed",
    "cancelled",
    "unknown",
    "rejected",
    "degraded",
}


def validate_events(lines, catalog):
    """Raw JSONL bytes are checked before decoding; no event bodies enter reports."""
    check(bool(lines), "no_diagnostic_events")
    records, seen, positions, duplicates = [], {}, {}, 0
    for line in lines:
        check(isinstance(line, str), "invalid_log_line")
        raw = line.encode("utf-8")
        check(
            raw.endswith(b"\n")
            and b"\r" not in raw
            and len(raw) <= 4096
            and raw.count(b"\n") == 1,
            "diagnostic_line_framing",
        )
        pairs = json.loads(line, object_pairs_hook=list)
        check(
            isinstance(pairs, list)
            and all(isinstance(x, tuple) and len(x) == 2 for x in pairs),
            "diagnostic_object_required",
        )
        row = dict(pairs)
        check(len(row) == len(pairs) and set(row) == FIELDS, "diagnostic_closed_fields")
        check(row["schema_version"] == "1.0.0", "diagnostic_version")
        check(
            row["service"]
            in {"platform", "companion", "memory", "memory-knowledge", "gateway"},
            "diagnostic_service",
        )
        check(
            row["level"] in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"},
            "diagnostic_level",
        )
        check(row["outcome"] in OUTCOMES, "diagnostic_outcome")
        check(
            type(row["sequence"]) is int and row["sequence"] >= 1, "diagnostic_sequence"
        )
        for key in ("event_id", "instance_id"):
            try:
                check(
                    isinstance(row[key], str)
                    and re.fullmatch(
                        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
                        row[key],
                    ),
                    "diagnostic_uuid",
                )
                uuid.UUID(row[key])
            except (ValueError, TypeError, AttributeError):
                check(False, "diagnostic_uuid")
        try:
            check(
                isinstance(row["timestamp"], str)
                and re.fullmatch(
                    r"\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[Zz]|[+-]\d{2}:\d{2})",
                    row["timestamp"],
                ),
                "diagnostic_timestamp",
            )
            stamp = datetime.fromisoformat(
                row["timestamp"].upper().replace("Z", "+00:00")
            )
            check(stamp.tzinfo is not None, "diagnostic_timestamp")
        except (ValueError, TypeError, AttributeError):
            check(False, "diagnostic_timestamp")
        check(
            row["correlation_id"] is None
            or isinstance(row["correlation_id"], str)
            and re.fullmatch("[a-f0-9]{32}", row["correlation_id"]),
            "diagnostic_correlation",
        )
        duration = row["duration_ms"]
        check(
            duration is None
            or type(duration) in (int, float)
            and math.isfinite(duration)
            and duration >= 0,
            "diagnostic_duration",
        )
        check(
            isinstance(row["event"], str)
            and re.fullmatch("[a-z][a-z0-9_.]{0,63}", row["event"]),
            "diagnostic_event",
        )
        error = row["error_code"]
        check(
            error is None
            or isinstance(error, str)
            and re.fullmatch("[a-z][a-z0-9_]{0,63}", error),
            "diagnostic_error",
        )
        registered = catalog.get(row["service"], {})
        check(row["event"] in registered.get("events", []), "event_not_registered")
        check(
            error is None or error in registered.get("error_codes", []),
            "error_not_registered",
        )
        # One event ID has immutable contents, and a sequence position has one ID.
        event_id = row["event_id"]
        position = (row["service"], row["instance_id"], row["sequence"])
        check(event_id not in seen or seen[event_id] == row, "changed_duplicate_event")
        check(
            position not in positions or positions[position] == event_id,
            "sequence_collision",
        )
        if event_id in seen:
            duplicates += 1
        else:
            records.append(row)
        seen[event_id], positions[position] = row, event_id
    return records, duplicates


def causal(lines, catalog, correlation, expected, forbidden=()):
    records, duplicates = validate_events(lines, catalog)
    related = [r for r in records if r["correlation_id"] == correlation]
    facts = {(r["service"], r["event"], r["outcome"]) for r in related}
    check(all(tuple(item) in facts for item in expected), "causal_event_missing")
    check(
        not any(tuple(item) in facts for item in forbidden), "failure_logged_as_success"
    )
    return {
        "matching_events": len(related),
        "unique_events": len(records),
        "duplicates": duplicates,
    }
