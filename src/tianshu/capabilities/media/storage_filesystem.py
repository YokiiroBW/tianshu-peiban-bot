"""Capacity identity is distinct from each subvolume's file device/inode identity."""

import re
import sys
from pathlib import Path

LINUX = sys.platform.startswith("linux")


def decode_mount_path(value):
    return re.sub(r"\\([0-7]{3})", lambda match: chr(int(match[1], 8)), value)


def parse_mountinfo(document):
    mounts = []
    for line in document.splitlines():
        before, separator, after = line.partition(" - ")
        fields, filesystem = before.split(), after.split()
        if not separator or len(fields) < 6 or len(filesystem) < 3:
            continue
        if not re.fullmatch(r"[0-9]+:[0-9]+", fields[2]):
            continue
        mountpoint = decode_mount_path(fields[4])
        if not mountpoint.startswith("/"):
            continue
        mounts.append(
            {
                "device": fields[2],
                "mountpoint": mountpoint.rstrip("/") or "/",
                "filesystem": filesystem[0],
                "source": decode_mount_path(filesystem[1]),
            }
        )
    return mounts


def read_mountinfo():
    if not LINUX:
        return []
    try:
        return parse_mountinfo(Path("/proc/self/mountinfo").read_text(encoding="utf-8"))
    except (OSError, UnicodeError):
        return []


def capacity_key(directory, device, mounts):
    if LINUX:
        # Path.as_posix handles native separators without rewriting a literal
        # backslash in a Linux mountpoint (escaped as \134 by mountinfo).
        path = (directory.as_posix() if isinstance(directory, Path) else str(directory)).rstrip(
            "/"
        ) or "/"
        matching = [
            (len(mount["mountpoint"]), index, mount)
            for index, mount in enumerate(mounts)
            if path == mount["mountpoint"] or path.startswith(mount["mountpoint"].rstrip("/") + "/")
        ]
        if matching:
            mount = max(matching, key=lambda item: item[:2])[2]
            if mount["filesystem"] == "btrfs" and mount["source"].startswith("/"):
                # Btrfs stat devices identify subvolumes, while mountinfo identifies
                # their shared filesystem. These device numbers need not be equal.
                return ("btrfs", mount["device"], mount["source"])
    return ("device", device)
