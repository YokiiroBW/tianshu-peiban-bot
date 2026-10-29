"""Closed diagnostics/v1 validation and safe segment enumeration. No source writes."""

import hashlib
import json
import math
import re
import uuid
from datetime import datetime
from pathlib import Path

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
SERVICES = ("platform", "companion", "memory", "memory-knowledge", "gateway")
LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
OUTCOMES = (
    "started",
    "succeeded",
    "failed",
    "cancelled",
    "unknown",
    "rejected",
    "degraded",
)


class InvalidEvent(ValueError):
    def __init__(self):
        super().__init__("invalid_safe_event")


def require(condition):
    if not condition:
        raise InvalidEvent()


def strict_json(raw):
    def pairs(values):
        obj = {}
        for key, value in values:
            if key in obj:
                raise InvalidEvent()
            obj[key] = value
        return obj

    def bad(_):
        raise InvalidEvent()

    return json.loads(raw, object_pairs_hook=pairs, parse_constant=bad)


class Policy:
    def __init__(self, snapshot):
        self.snapshot = snapshot

    def verify_contract(self, directory):
        for name, digest in self.snapshot["contract"]["files"].items():
            if name not in {
                "manifest.json",
                "README.md",
                "event.schema.json",
                "examples.json",
                "negative-examples.json",
            }:
                raise ValueError("contract_filename_invalid")
            if hashlib.sha256((directory / name).read_bytes()).hexdigest() != digest:
                raise ValueError("contract_bytes_mismatch")
        schema = json.loads((directory / "event.schema.json").read_bytes())
        if (
            set(schema["required"]) != FIELDS
            or set(schema["properties"]) != FIELDS
            or schema["additionalProperties"] is not False
        ):
            raise ValueError("contract_shape_mismatch")

    def validate(self, event):
        try:
            require(isinstance(event, dict))
            attributed = event.get("schema_version") == "1.1.0"
            expected = FIELDS | {"attributes"} if attributed else FIELDS
            require(set(event) == expected)
            require(
                event["schema_version"] in {"1.0.0", "1.1.0"}
                and event["service"] in SERVICES
            )
            if attributed:
                require(event["service"] == "companion")
                require(
                    event["event"]
                    in {
                        "peer.call.started",
                        "peer.call.finished",
                        "runtime.background_failed",
                    }
                )
                schema = self.snapshot["products"]["companion"]["peer_attribute_schema"]
                self.peer_attributes(event["attributes"], schema)
                require(event["attributes"]["error_code"] == event["error_code"])
            require(type(event["sequence"]) is int and event["sequence"] >= 1)
            require(event["level"] in LEVELS and event["outcome"] in OUTCOMES)
            for key in ("instance_id", "event_id"):
                require(isinstance(event[key], str))
                require(
                    re.fullmatch(
                        r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}",
                        event[key],
                    )
                )
                uuid.UUID(event[key])
            timestamp = event["timestamp"]
            require(
                isinstance(timestamp, str)
                and re.fullmatch(
                    r"\d{4}-\d\d-\d\d[Tt]\d\d:\d\d:\d\d(?:\.\d+)?(?:[Zz]|[+-]\d\d:\d\d)",
                    timestamp,
                )
            )
            require(
                datetime.fromisoformat(timestamp.upper().replace("Z", "+00:00")).tzinfo
            )
            require(
                event["correlation_id"] is None
                or (
                    isinstance(event["correlation_id"], str)
                    and re.fullmatch(r"[a-f0-9]{32}", event["correlation_id"])
                )
            )
            duration = event["duration_ms"]
            require(
                duration is None
                or (
                    type(duration) in (int, float)
                    and math.isfinite(duration)
                    and duration >= 0
                )
            )
            service = (
                "memory" if event["service"] == "memory-knowledge" else event["service"]
            )
            registry = self.snapshot["products"][service]
            require(event["event"] in registry["events"])
            require(
                event["error_code"] is None
                or event["error_code"] in registry["error_codes"]
            )
            require(len(canonical(event)) + 1 <= 4096)
        except (ValueError, TypeError, KeyError, OverflowError):
            raise InvalidEvent() from None
        return event

    def peer_attributes(self, attributes, schema):
        require(
            isinstance(attributes, dict) and set(attributes) == set(schema["required"])
        )
        for key, rule in schema["properties"].items():
            value = attributes[key]
            kinds = rule["type"] if isinstance(rule["type"], list) else [rule["type"]]
            if value is None and "null" in kinds:
                continue
            require(
                type(value) is int if "integer" in kinds else isinstance(value, str)
            )
            if "enum" in rule:
                require(value in rule["enum"])
            if "pattern" in rule:
                require(re.fullmatch(rule["pattern"], value))
            if "minimum" in rule:
                require(rule["minimum"] <= value <= rule["maximum"])

    def line(self, raw):
        try:
            if len(raw) > 4096 or not raw.endswith(b"\n") or b"\r" in raw:
                raise InvalidEvent()
            return self.validate(strict_json(raw.decode("utf-8")))
        except (ValueError, UnicodeError, TypeError):
            raise InvalidEvent() from None


def canonical(event):
    return json.dumps(
        event,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()


def digest(event):
    return digest_bytes(event).hex()


def digest_bytes(event):
    return hashlib.sha256(canonical(event)).digest()


def event_time(event):
    return datetime.fromisoformat(
        event["timestamp"].upper().replace("Z", "+00:00")
    ).timestamp()


def segments(directory):
    """Only one explicitly mounted product directory; refuse links/junctions."""
    directory = Path(directory)
    if directory.is_symlink() or (
        hasattr(directory, "is_junction") and directory.is_junction()
    ):
        raise ValueError("unsafe_log_root")
    root = directory.resolve(strict=True)
    for path in sorted(root.iterdir()):
        if re.fullmatch(r"[^/\\]+\.jsonl(?:\.\d+)?", path.name):
            if path.is_symlink() or path.resolve().parent != root or not path.is_file():
                raise ValueError("unsafe_log_segment")
            yield path
