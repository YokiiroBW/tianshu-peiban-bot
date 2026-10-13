"""Apply prepared NFO bytes to an explicitly named legacy root, with resumable backups.

This maintenance executor does not generate metadata or call a downloader. The product
prepares the plan from its registered import inventory; the operator supplies the exact
host directory corresponding to that inventory's read-only container mount.
"""

import argparse
import base64
import hashlib
import json
import os
import shutil
import stat
import tempfile
import time
from pathlib import Path


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def checked_path(root, relative=None):
    root = Path(root).absolute()
    if relative is None:
        path = root
    else:
        parts = relative.split("/") if isinstance(relative, str) else []
        if not parts or any(
            p in {"", ".", ".."} or "\\" in p or ":" in p or "\0" in p for p in parts
        ):
            raise ValueError("unsafe_relative_path")
        path = root.joinpath(*parts)
    for part in (*reversed(path.parents), path):
        if part.is_symlink() or getattr(part, "is_junction", lambda: False)():
            raise ValueError("unsafe_path")
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("outside_root")
    return path


def observation(path):
    value = path.lstat()
    if not stat.S_ISREG(value.st_mode):
        raise ValueError("not_regular_file")
    return {
        "dev": value.st_dev,
        "ino": value.st_ino,
        "size_bytes": value.st_size,
        "mtime_ns": value.st_mtime_ns,
        "ctime_ns": value.st_ctime_ns,
    }


def verify_videos(root, rows):
    seen = set()
    for row in rows:
        relative = row["relative_path"]
        if relative in seen or Path(relative).suffix.lower() not in {".mp4", ".mkv"}:
            raise ValueError("invalid_video_inventory")
        seen.add(relative)
        actual = observation(checked_path(root, relative))
        if actual != row["observation"]:
            raise ValueError("video_observation_changed")
    return len(seen)


def sync_directory(path):
    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def durable_mkdir(path):
    missing = []
    cursor = path
    while not cursor.exists():
        missing.append(cursor)
        cursor = cursor.parent
    for directory in reversed(missing):
        directory.mkdir(mode=0o700)
        sync_directory(directory.parent)


def atomic_bytes(path, raw, source=None):
    descriptor, temporary = tempfile.mkstemp(prefix=".tianshu-nfo-", dir=path.parent)
    temporary = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        if source is not None:
            info = source.stat()
            shutil.copystat(source, temporary, follow_symlinks=False)
            if hasattr(os, "chown"):
                os.chown(temporary, info.st_uid, info.st_gid)
            os.utime(temporary, ns=(info.st_atime_ns, time.time_ns()))
            with temporary.open("r+b" if os.name == "nt" else "rb") as stream:
                os.fsync(stream.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        if temporary.exists():
            temporary.unlink()


def backup_original(original, saved, expected_sha):
    descriptor, temporary = tempfile.mkstemp(
        prefix=".tianshu-backup-", dir=saved.parent
    )
    os.close(descriptor)
    temporary = Path(temporary)
    try:
        shutil.copy2(original, temporary, follow_symlinks=False)
        with temporary.open("r+b" if os.name == "nt" else "rb") as stream:
            os.fsync(stream.fileno())
        if sha(temporary.read_bytes()) != expected_sha:
            raise ValueError("backup_verification_failed")
        os.replace(temporary, saved)
        sync_directory(saved.parent)
    finally:
        if temporary.exists():
            temporary.unlink()


def execute(plan, *, root, backup, action):
    if plan.get("schema_version") != 1 or action not in {"inspect", "apply", "verify"}:
        raise ValueError("invalid_plan")
    root = checked_path(root)
    backup = checked_path(backup)
    info = root.stat()
    if [info.st_dev, info.st_ino] != plan["root_identity"]:
        raise ValueError("root_identity_changed")
    if backup.is_relative_to(root) or root.is_relative_to(backup):
        raise ValueError("backup_must_be_separate")
    fingerprint = sha(json.dumps(plan, sort_keys=True, ensure_ascii=False).encode())
    files = plan["files"]
    if not files or len({row["relative_path"] for row in files}) != len(files):
        raise ValueError("duplicate_or_empty_nfo_plan")
    intent = backup / "manifest.json"
    if (
        intent.exists()
        and json.loads(intent.read_bytes())["plan_sha256"] != fingerprint
    ):
        raise ValueError("backup_plan_conflict")
    candidates = []
    already_applied = 0
    for row in files:
        relative = row["relative_path"]
        if Path(relative).suffix.lower() != ".nfo":
            raise ValueError("only_nfo_allowed")
        original = checked_path(root, relative)
        observation(original)
        raw = base64.b64decode(row["content_base64"], validate=True)
        if len(raw) > 2 * 1024 * 1024 or sha(raw) != row["after_sha256"]:
            raise ValueError("candidate_digest_mismatch")
        current = original.read_bytes()
        current_sha = sha(current)
        if current_sha not in {row["before_sha256"], row["after_sha256"]}:
            raise ValueError("original_nfo_changed")
        saved = checked_path(backup / "original", relative)
        if saved.exists() and sha(saved.read_bytes()) != row["before_sha256"]:
            raise ValueError("backup_digest_mismatch")
        if current_sha == row["after_sha256"]:
            if row["before_sha256"] != row["after_sha256"] and not saved.exists():
                raise ValueError("applied_file_without_backup")
            already_applied += 1
        elif action == "verify":
            raise ValueError("nfo_not_applied")
        candidates.append((row, original, saved, raw))
    video_count = verify_videos(root, plan["videos"])
    changed = 0
    if action == "apply":
        durable_mkdir(backup)
        # Back up every original before publishing even the first changed NFO.
        for row, original, saved, _ in candidates:
            if not saved.exists():
                if sha(original.read_bytes()) != row["before_sha256"]:
                    raise ValueError("original_changed_before_backup")
                durable_mkdir(saved.parent)
                backup_original(original, saved, row["before_sha256"])
        atomic_bytes(
            intent,
            (
                json.dumps(
                    {
                        "plan_sha256": fingerprint,
                        "root": str(root),
                        "files": len(files),
                        "videos": video_count,
                    },
                    indent=2,
                )
                + "\n"
            ).encode(),
        )
        for row, original, saved, raw in candidates:
            current_sha = sha(original.read_bytes())
            if current_sha == row["after_sha256"]:
                continue
            if current_sha != row["before_sha256"]:
                raise ValueError("original_changed_before_write")
            atomic_bytes(original, raw, source=original)
            if sha(original.read_bytes()) != row["after_sha256"]:
                raise ValueError("nfo_readback_failed")
            changed += 1
        verify_videos(root, plan["videos"])
    return {
        "status": "verified"
        if action == "verify"
        else "applied"
        if action == "apply"
        else "ready",
        "plan_sha256": fingerprint,
        "nfos": len(files),
        "videos_unchanged": video_count,
        "changed": changed,
        "already_applied": already_applied,
        "backup": str(backup),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["inspect", "apply", "verify"])
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--backup", type=Path, required=True)
    args = parser.parse_args()
    result = execute(
        json.loads(args.plan.read_bytes()),
        root=args.root,
        backup=args.backup,
        action=args.action,
    )
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
