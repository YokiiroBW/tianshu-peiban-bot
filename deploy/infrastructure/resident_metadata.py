"""Canonical resident container metadata, preserving every observed field."""

import json


def normalize_inventory(rows):
    # The saved baseline is JSON while dataclass mounts can be tuples in memory.
    rows = json.loads(json.dumps(rows, sort_keys=True, allow_nan=False))
    for row in rows:
        row["mounts"].sort(key=lambda mount: json.dumps(mount, sort_keys=True, separators=(",", ":")))
    return sorted(rows, key=lambda row: row["id"])
