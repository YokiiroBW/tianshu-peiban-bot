"""Read Vector 0.58 file-source checkpoints without storing per-event state.

The deployed configuration uses checksum fingerprinting over the first line,
bounded by max_line_bytes=4095.  Checkpoint positions are useful only when
acknowledgements are enabled and the source file has not changed.
"""

import json
import subprocess
from datetime import datetime
from pathlib import Path


_POLY = 0x42F0E1EBA9EA3693  # CRC-64/ECMA-182, as used by Vector 0.58
_MASK = (1 << 64) - 1


def compatible_configuration(path, roots):
    """Only use offsets for the reviewed source, fingerprint and sink contract."""
    try:
        version = subprocess.run(["vector", "--version"], capture_output=True,
                                 text=True, timeout=3, check=True).stdout
        if not version.startswith("vector 0.58.0 "):
            return False
        config = json.loads(Path(path).read_bytes())
        sink = config["sinks"]["loki"]
        if (sink["type"] != "loki" or sink.get("acknowledgements", {}).get("enabled") is not True
                or sink.get("buffer", {}).get("type") != "disk"
                or sink["buffer"].get("when_full") != "block"):
            return False
        for service, root in roots.items():
            source = config["sources"][service]
            ack = source.get("acknowledgements")
            if (source.get("type") != "file" or source.get("read_from") != "beginning"
                    or source.get("ignore_checkpoints") is not False
                    or source.get("max_line_bytes") != 4095
                    or source.get("fingerprint") != {"strategy": "checksum", "lines": 1}
                    or source.get("include") != [str(Path(root) / "*.jsonl"),
                                                str(Path(root) / "*.jsonl.[0-9]*")]
                    or source.get("multiline") is not None
                    or source.get("encoding") is not None
                    or source.get("start_at_beginning") is not None
                    or source.get("remove_after_secs") is not None
                    or source.get("line_delimiter", "\n") != "\n"
                    or ack is False or (isinstance(ack, dict) and ack.get("enabled") is False)):
                return False
            transform = config["transforms"][service + "_safe"]
            if (transform.get("type") != "remap" or transform.get("inputs") != [service]
                    or service + "_safe" not in sink.get("inputs", [])):
                return False
        return True
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError, AttributeError):
        return False


def checksum(data):
    value = 0
    for byte in data:
        value ^= byte << 56
        for _ in range(8):
            value = ((value << 1) ^ (_POLY if value & (1 << 63) else 0)) & _MASK
    return value


def fingerprint(path, max_line_bytes=4095):
    with Path(path).open("rb") as source:
        first = source.readline(max_line_bytes)
    if not first or (not first.endswith(b"\n") and len(first) < max_line_bytes):
        return None
    return checksum(first)


def checkpoint_positions(path):
    """Return validated positions or fail closed on an unknown format."""
    data = json.loads(Path(path).read_bytes())
    if data.get("version") != "1" or not isinstance(data.get("checkpoints"), list):
        raise ValueError("vector_checkpoint_format_invalid")
    positions = {}
    for item in data["checkpoints"]:
        key = item["fingerprint"]
        position = item["position"]
        if set(key) != {"first_lines_checksum"} or type(key["first_lines_checksum"]) is not int:
            raise ValueError("vector_fingerprint_format_invalid")
        code = key["first_lines_checksum"]
        if not 0 <= code <= _MASK or type(position) is not int or position < 0 or code in positions:
            raise ValueError("vector_checkpoint_value_invalid")
        modified = datetime.fromisoformat(item["modified"].replace("Z", "+00:00"))
        if modified.tzinfo is None:
            raise ValueError("vector_checkpoint_time_invalid")
        positions[code] = (position, int(modified.timestamp() * 1_000_000_000))
    return positions
