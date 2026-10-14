"""Stage and retire an explicit media file map without downloading or changing video bytes."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
from pathlib import Path


def digest(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def file_hash(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def checked_root(value):
    root = Path(value)
    if not root.is_absolute() or root == root.parent:
        raise ValueError("invalid_root")
    for part in (root, *root.parents):
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            raise ValueError("symlink_root")
    if root.resolve() != root or not root.is_dir():
        raise ValueError("root_not_directory")
    return root


def checked_path(root, relative):
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise ValueError("invalid_relative")
    parts = relative.split("/")
    if any(p in {"", ".", ".."} or ":" in p or "\0" in p for p in parts):
        raise ValueError("invalid_relative")
    path = root.joinpath(*parts)
    for parent in (path, *path.parents):
        if not parent.is_relative_to(root):
            break
        if parent.is_symlink() or (hasattr(parent, "is_junction") and parent.is_junction()):
            raise ValueError("symlink_path")
    if not path.resolve().is_relative_to(root):
        raise ValueError("path_outside_root")
    return path


def observation(path):
    value = path.lstat()
    if not stat.S_ISREG(value.st_mode):
        raise ValueError("not_regular_file")
    return {key: getattr(value, attr) for key, attr in (
        ("dev", "st_dev"), ("ino", "st_ino"), ("size_bytes", "st_size"),
        ("mtime_ns", "st_mtime_ns"),
    )}


def validate_plan(plan):
    if plan.get("schema_version") != 1 or not isinstance(plan.get("files"), list) or not plan["files"]:
        raise ValueError("invalid_plan")
    source, target = checked_root(plan["source_root"]), checked_root(plan["target_root"])
    if source == target or source.is_relative_to(target) or target.is_relative_to(source):
        raise ValueError("overlapping_roots")
    old, new = set(), set()
    for row in plan["files"]:
        before, after = row["old_relative_path"], row["new_relative_path"]
        checked_path(source, before)
        checked_path(target, after)
        if before in old or after.casefold() in new:
            raise ValueError("duplicate_mapping")
        old.add(before)
        new.add(after.casefold())
        if row["kind"] not in {"video", "sidecar"}:
            raise ValueError("invalid_kind")
        if row["kind"] == "sidecar" and (
            not isinstance(row.get("sha256"), str) or len(row["sha256"]) != 64
        ):
            raise ValueError("missing_sidecar_hash")
    return source, target


def check_source(source, row):
    if observation(source) != row["observation"]:
        raise ValueError("source_changed")
    if row["kind"] == "sidecar" and file_hash(source) != row["sha256"]:
        raise ValueError("source_content_changed")


def check_target(source, target, row, *, source_required=True):
    if source_required:
        check_source(source, row)
    actual = observation(target)
    if row["kind"] == "video":
        # This executor deliberately requires a hard link. It does not quietly copy huge videos.
        if actual != row["observation"]:
            raise ValueError("target_not_same_video")
    else:
        if (actual["dev"], actual["ino"]) == (row["observation"]["dev"], row["observation"]["ino"]):
            raise ValueError("sidecar_is_hardlink")
        if actual["size_bytes"] != row["observation"]["size_bytes"] or file_hash(target) != row["sha256"]:
            raise ValueError("target_content_changed")


def make_parents(root, parent, plan):
    pending = []
    current = parent
    while current != root and not current.exists():
        pending.append(current)
        current = current.parent
    for directory in reversed(pending):
        directory.mkdir()
        if os.name == "posix":
            os.chown(directory, plan["directory_uid"], plan["directory_gid"])
            directory.chmod(0o755)


def sync_directories(root, paths):
    if os.name != "posix":
        return
    directories = {root}
    for path in paths:
        directories.update(p for p in path.parents if p.is_relative_to(root))
    for directory in sorted(directories, key=lambda p: len(p.parts), reverse=True):
        if directory.is_dir():
            handle = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(handle)
            finally:
                os.close(handle)


def stage(plan):
    source, target = validate_plan(plan)
    # Complete preflight before the first link or copy. Existing unrelated files are never replaced.
    for row in plan["files"]:
        old = checked_path(source, row["old_relative_path"])
        new = checked_path(target, row["new_relative_path"])
        check_source(old, row)
        if new.exists():
            check_target(old, new, row)
    linked = copied = existing = 0
    for row in plan["files"]:
        old = checked_path(source, row["old_relative_path"])
        new = checked_path(target, row["new_relative_path"])
        # Preserve existing directory permissions. New media parents belong to the service account.
        make_parents(target, new.parent, plan)
        check_source(old, row)
        if new.exists():
            check_target(old, new, row)
            existing += 1
            continue
        if row["kind"] == "video":
            os.link(old, new)
            linked += 1
        else:
            descriptor, name = tempfile.mkstemp(prefix=".tianshu-relocation-", dir=new.parent)
            temporary = Path(name)
            try:
                with old.open("rb") as src, os.fdopen(descriptor, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                    dst.flush()
                    os.fsync(dst.fileno())
                shutil.copystat(old, temporary, follow_symlinks=False)
                if os.name == "posix":
                    source_stat = old.stat()
                    os.chown(temporary, source_stat.st_uid, source_stat.st_gid)
                    with temporary.open("rb") as completed:
                        os.fsync(completed.fileno())
                # Link a completed private copy without replacing a concurrently created target.
                os.link(temporary, new)
            finally:
                temporary.unlink(missing_ok=True)
            copied += 1
        check_target(old, new, row)
    sync_directories(target, [checked_path(target, r["new_relative_path"]) for r in plan["files"]])
    return {"status": "staged", "plan_digest": digest(plan), "linked": linked, "copied": copied, "existing": existing}


def verify(plan, *, source_required=True):
    source, target = validate_plan(plan)
    for row in plan["files"]:
        check_target(
            checked_path(source, row["old_relative_path"]),
            checked_path(target, row["new_relative_path"]), row,
            source_required=source_required,
        )
    return {"status": "verified", "plan_digest": digest(plan), "files": len(plan["files"]), "source_required": source_required}


def retire_sources(plan, verification):
    """Only called after the operator has checked database rebind and Emby readback."""
    if verification != {"plan_digest": digest(plan), "database_verified": True, "emby_verified": True}:
        raise ValueError("external_verification_missing")
    source, target = validate_plan(plan)
    for row in plan["files"]:
        old, new = checked_path(source, row["old_relative_path"]), checked_path(target, row["new_relative_path"])
        check_target(old, new, row, source_required=old.exists())
    sync_directories(target, [checked_path(target, r["new_relative_path"]) for r in plan["files"]])
    removed = missing = 0
    parents = set()
    for row in plan["files"]:
        old, new = checked_path(source, row["old_relative_path"]), checked_path(target, row["new_relative_path"])
        if old.exists():
            check_target(old, new, row)
            old.unlink()
            removed += 1
        else:
            missing += 1
        parents.update(p for p in old.parents if p != source and p.is_relative_to(source))
    for directory in sorted(parents, key=lambda p: len(p.parts), reverse=True):
        if directory.exists() and not any(directory.iterdir()):
            directory.rmdir()
    sync_directories(source, [checked_path(source, r["old_relative_path"]) for r in plan["files"]])
    verify(plan, source_required=False)
    return {"status": "retired", "plan_digest": digest(plan), "removed": removed, "already_absent": missing}
