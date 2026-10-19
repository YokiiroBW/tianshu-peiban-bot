"""Copy explicit cold/bulk directories while preserving bytes and hard links.

The caller owns service quiescence and the final mount switch. This module never
starts services or removes originals. A verified copy can be moved into service
while the original stays in a separate, explicitly supplied rollback directory.
"""

import hashlib
import os
import stat
import subprocess
from pathlib import Path


def directory(path):
    path = Path(path)
    if not path.is_absolute() or path.resolve() != path or not path.is_dir():
        raise ValueError("canonical_directory_required")
    return path


def inventory(root):
    root = directory(root)
    result, inodes = {}, {}
    for parent, dirs, files in os.walk(root, followlinks=False):
        for path in [Path(parent), *(Path(parent) / name for name in sorted(files))]:
            info = path.lstat()
            name = path.relative_to(root).as_posix()
            entry = {"mode": stat.S_IMODE(info.st_mode), "uid": info.st_uid, "gid": info.st_gid}
            if stat.S_ISDIR(info.st_mode):
                entry["kind"] = "directory"
            elif stat.S_ISREG(info.st_mode):
                key = info.st_dev, info.st_ino
                if key not in inodes:
                    digest = hashlib.sha256()
                    with path.open("rb") as stream:
                        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
                            digest.update(block)
                    inodes[key] = name, digest.hexdigest()
                anchor, sha = inodes[key]
                entry.update(kind="file", size=info.st_size, sha256=sha,
                             modified_seconds=int(info.st_mtime), hardlink=anchor)
                after = path.stat()
                if (info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_ino) != (
                        after.st_size, after.st_mtime_ns, after.st_ctime_ns, after.st_ino):
                    raise ValueError("source_changed_during_inventory")
            else:
                raise ValueError("unsupported_storage_entry")
            result[name] = entry
        if any((Path(parent) / name).is_symlink() for name in dirs):
            raise ValueError("storage_symlink_rejected")
        dirs.sort()
    return result


def copy_verified(source, target, *, rsync="rsync"):
    source, target = directory(source), Path(target)
    directory(target.parent)
    if target.exists() or source in target.parents or target in source.parents:
        raise ValueError("independent_new_destination_required")
    before = inventory(source)
    subprocess.run([rsync, "-aHAX", "--numeric-ids", "--", str(source) + "/", str(target) + "/"],
                   check=True, capture_output=True, timeout=3600)
    return verify_copy(source, target, before)


def verify_copy(source, target, before):
    source, target = directory(source), directory(target)
    # Synology's share ACL can mask a copied directory's group mode. Restore
    # the recorded Unix owner/mode explicitly, as the resident snapshot does.
    for name, entry in before.items():
        path = target / name
        if path.is_symlink() or not path.exists():
            raise ValueError("storage_copy_entry_changed")
        os.chown(path, entry["uid"], entry["gid"])
        os.chmod(path, entry["mode"])
    after = inventory(target)
    if before != after or inventory(source) != before:
        raise ValueError("storage_copy_mismatch")
    descriptor = os.open(target, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return {"source": str(source), "target": str(target), "entries": before,
            "files": sum(row["kind"] == "file" for row in before.values()),
            "logical_bytes": sum(row.get("size", 0) for row in before.values()),
            "status": "copy_verified"}


def retain_original(copy, retained):
    """Caller must keep writers stopped until mounts reference the verified copy."""
    source, target, retained = directory(copy["source"]), directory(copy["target"]), Path(retained)
    directory(retained.parent)
    if retained.exists() or retained == source or source in retained.parents or retained in source.parents:
        raise ValueError("independent_new_retention_required")
    if inventory(source) != copy["entries"] or inventory(target) != copy["entries"]:
        raise ValueError("verified_copy_changed")
    mode = source.stat()
    source.rename(retained)
    source.mkdir(mode=stat.S_IMODE(mode.st_mode))
    os.chown(source, mode.st_uid, mode.st_gid)
    os.chmod(source, stat.S_IMODE(mode.st_mode))
    return {"source": str(source), "retained": str(retained), "target": str(target)}
