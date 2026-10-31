"""Metadata-only filesystem accounting; no links, cross-device descent or file reads."""

import os
import re
import stat
from pathlib import Path

from ..contracts import Fault, require
from .storage_filesystem import capacity_key, read_mountinfo


def server_path(value):
    require(isinstance(value, str) and value and len(value) <= 4096, "invalid_input", 400)
    value = value.replace("\\", "/")
    require(value.startswith("/") or re.match(r"^[A-Za-z]:/", value), "invalid_input", 400)
    require(
        not any(part in {".", ".."} for part in value.split("/"))
        and not any(ord(c) < 32 for c in value),
        "invalid_input",
        400,
    )
    return value if re.fullmatch(r"[A-Za-z]:/", value) else value.rstrip("/") or "/"


def map_location(server_id, location, mappings):
    try:
        location = server_path(location)
    except Fault:
        return None
    windows = bool(re.match(r"^[A-Za-z]:/", location))
    compare = location.casefold() if windows else location
    matches = []
    for mapping in mappings:
        if mapping["server_id"] != server_id:
            continue
        prefix = mapping["server_prefix"]
        compared_prefix = prefix.casefold() if windows else prefix
        if compare == compared_prefix or compare.startswith(compared_prefix.rstrip("/") + "/"):
            matches.append(mapping)
    if not matches:
        return None
    # Explicit mappings are appended after direct ones, so equal prefixes override deliberately.
    selected = max(enumerate(matches), key=lambda item: (len(item[1]["server_prefix"]), item[0]))[1]
    suffix = location[len(selected["server_prefix"].rstrip("/")) :].lstrip("/")
    return (
        Path(selected["local_prefix"]).joinpath(*suffix.split("/"))
        if suffix
        else Path(selected["local_prefix"])
    )


class ScanCancelled(Exception):
    pass


def scan_files(volumes, subscription_roots, emby_roots, discovery_code, cancel):
    """Return category measurements; capacity and residual arithmetic belong to the projection."""
    data = {
        volume["storage_id"]: {
            "subscription_logical": 0,
            "subscription_allocated": 0,
            "emby_logical": 0,
            "emby_allocated": 0,
            "allocated": True,
            "subscription_complete": True,
            "emby_complete": discovery_code is None,
            "code": discovery_code,
        }
        for volume in volumes
    }
    filesystems = {}

    def checked():
        if cancel.is_set():
            raise ScanCancelled

    def root_stat(root):
        checked()
        for part in (*reversed(root.parents), root):
            observed = part.lstat()
            if stat.S_ISLNK(observed.st_mode) or part.is_junction():
                raise OSError("linked observation root")
        observed = root.lstat()
        if not stat.S_ISDIR(observed.st_mode):
            raise OSError("observation root is not a directory")
        return observed

    checked()
    mounts = read_mountinfo()

    for volume in volumes:
        for directory in volume["directories"]:
            try:
                observed = root_stat(Path(directory))
                filesystems[capacity_key(Path(directory), observed.st_dev, mounts)] = volume[
                    "storage_id"
                ]
                data[volume["storage_id"]]["allocated"] &= hasattr(observed, "st_blocks")
            except OSError:
                data[volume["storage_id"]].update(
                    subscription_complete=False, code="storage_subscription_unavailable"
                )

    files, directories = set(), set()

    def failed(category, identity=None):
        affected = [identity] if identity is not None else list(data)
        for key in affected:
            data[key][category + "_complete"] = False
            if category == "subscription":
                data[key]["code"] = "storage_subscription_unavailable"
            elif data[key]["code"] is None:
                data[key]["code"] = "storage_path_unavailable"

    def walk(root, category, identity=None):
        checked()
        try:
            observed = root_stat(root)
        except OSError:
            failed(category, identity)
            return
        identity = filesystems.get(capacity_key(root, observed.st_dev, mounts))
        if identity is None:
            # A separately mounted volume is outside this media disk's scope.
            return
        device = observed.st_dev
        stack = [(root, observed)]
        while stack:
            checked()
            directory, directory_stat = stack.pop()
            directory_key = (directory_stat.st_dev, directory_stat.st_ino)
            if directory_key in directories:
                continue
            directories.add(directory_key)
            try:
                with os.scandir(directory) as entries:
                    for entry in entries:
                        checked()
                        try:
                            value = entry.stat(follow_symlinks=False)
                            # Windows DirEntry caches zero st_dev/st_ino. A native
                            # lstat supplies real identity before device/inode accounting.
                            if not value.st_dev or not value.st_ino:
                                value = Path(entry.path).lstat()
                            if (
                                value.st_dev != device
                                or stat.S_ISLNK(value.st_mode)
                                or Path(entry.path).is_junction()
                            ):
                                continue
                            if stat.S_ISDIR(value.st_mode):
                                stack.append((Path(entry.path), value))
                            elif stat.S_ISREG(value.st_mode):
                                if not value.st_ino:
                                    failed(category, identity)
                                    continue
                                inode = (value.st_dev, value.st_ino)
                                if inode in files:
                                    continue
                                files.add(inode)
                                result = data[identity]
                                result[category + "_logical"] += value.st_size
                                blocks = getattr(value, "st_blocks", None)
                                if blocks is None:
                                    result["allocated"] = False
                                else:
                                    result[category + "_allocated"] += blocks * 512
                        except OSError:
                            failed(category, identity)
            except OSError:
                failed(category, identity)

    # Subscription roots always win category ownership, including their control pool.
    for identity, root in subscription_roots:
        walk(Path(root), "subscription", identity)
    for root in emby_roots:
        walk(Path(root), "emby")
    checked()
    return {
        identity: {
            "subscription_bytes": value[
                "subscription_allocated" if value["allocated"] else "subscription_logical"
            ]
            if value["subscription_complete"]
            else None,
            "emby_bytes": value["emby_allocated" if value["allocated"] else "emby_logical"]
            if value["emby_complete"] and (value["subscription_complete"] or not emby_roots)
            else None,
            "basis": "allocated" if value["allocated"] else "logical",
            "code": value["code"],
        }
        for identity, value in data.items()
    }
