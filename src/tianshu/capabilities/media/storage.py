"""Read capacity from configured media roots, grouped by their actual filesystem."""

import os
import shutil
from pathlib import Path

from ..contracts import digest
from .storage_filesystem import capacity_key, read_mountinfo


def media_storage(targets):
    groups = {}
    mounts = read_mountinfo()
    for identity, target in sorted(targets.items()):
        if target.get("kind") != "media_server":
            continue
        directory = Path(target["directory"])
        usage, device = None, None
        try:
            if directory.is_dir():
                device = directory.stat().st_dev
                usage = shutil.disk_usage(directory)
        except OSError:
            pass
        key = (
            capacity_key(directory, device, mounts)
            if device is not None
            else ("path", os.path.normcase(str(directory)))
        )
        volume = groups.setdefault(
            key,
            {
                "storage_id": digest({"device": str(key)}),
                "label": target["label"],
                "target_ids": [],
                "directories": [],
                "state": "unavailable",
                "total_bytes": None,
                "used_bytes": None,
                "free_bytes": None,
            },
        )
        volume["target_ids"].append(identity)
        if str(directory) not in volume["directories"]:
            volume["directories"].append(str(directory))
        if usage is not None:
            volume.update(
                state="available",
                total_bytes=usage.total,
                used_bytes=usage.used,
                free_bytes=usage.free,
            )
    volumes = list(groups.values())
    return {
        "state": "not_configured"
        if not volumes
        else "available"
        if any(v["state"] == "available" for v in volumes)
        else "unavailable",
        "volumes": volumes,
    }
