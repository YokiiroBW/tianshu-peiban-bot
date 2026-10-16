"""Canonical resident container metadata, preserving every observed field."""

import json


def normalize_inventory(rows):
    # The saved baseline is JSON while dataclass mounts can be tuples in memory.
    rows = json.loads(json.dumps(rows, sort_keys=True, allow_nan=False))
    for row in rows:
        row["mounts"].sort(key=lambda mount: json.dumps(mount, sort_keys=True, separators=(",", ":")))
    return sorted(rows, key=lambda row: row["id"])


def compare_inventory(before, after):
    """Existing non-targets must be identical; concurrent additions are recorded."""
    before, after = normalize_inventory(before), normalize_inventory(after)
    old, new = {row["id"]: row for row in before}, {row["id"]: row for row in after}
    if len(old) != len(before) or len(new) != len(after):
        raise ValueError("duplicate_container_identity")
    removed = sorted(old.keys() - new.keys())
    changed = sorted(identity for identity in old.keys() & new.keys() if old[identity] != new[identity])
    return {"preserved": not removed and not changed, "before": len(before), "after": len(after),
            "removed": removed, "changed": changed, "added": [new[identity] for identity in sorted(new.keys() - old.keys())]}
