"""Cold, byte-preserving ten-owner backup and disabled offline restore verification.

This adapter never stops/starts services, edits authority, or activates the restored tree.
Configuration and credentials are copied as opaque protected bytes, never interpreted.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import stat
import subprocess
import time
from pathlib import Path

from .safety import (RecoveryError, canonical, copy_new, digest, file_hash, read_json,
                     require, safe_path, sync_tree, walk_tree, write_new)

OWNERS = {"platform", "memory", "companion", "gateway", "knowledge",
          "obs-loki", "obs-vector", "obs-prometheus", "obs-grafana", "obs-guard"}
PROJECTS = {"tianshu-v2-resident", "tianshu-v2-resident-obs"}
LIMIT = 20 * 1024**3


def inspect_owners(docker: str) -> list[dict]:
    template = ('{"id":{{json .Id}},"image_id":{{json .Image}},'
                '"project":{{json (index .Config.Labels "com.docker.compose.project")}},'
                '"service":{{json (index .Config.Labels "com.docker.compose.service")}},'
                '"mounts":{{json .Mounts}},"state":{{json .State.Status}},'
                '"exit":{{json .State.ExitCode}},"running":{{json .State.Running}},'
                '"paused":{{json .State.Paused}},"restarting":{{json .State.Restarting}},'
                '"dead":{{json .State.Dead}},"oom":{{json .State.OOMKilled}},'
                '"error":{{if .State.Error}}true{{else}}false{{end}},'
                '"restart":{{json .HostConfig.RestartPolicy.Name}}}')
    rows = []
    for project in sorted(PROJECTS):
        result = subprocess.run([docker, "ps", "-aq", "--filter",
                                 "label=com.docker.compose.project=" + project],
                                capture_output=True, text=True, timeout=20, check=True)
        for identifier in result.stdout.split():
            result = subprocess.run([docker, "inspect", "--format", template, identifier],
                                    capture_output=True, text=True, timeout=20, check=True)
            rows.append(json.loads(result.stdout))
    return rows


def require_stopped(expected: list[dict], observed: list[dict]) -> None:
    require(len(expected) == len(observed) == 10, "ten_owners_required")
    require({row["service"] for row in expected} == OWNERS, "owner_set_mismatch")
    require(len({row["id"] for row in expected}) == 10, "duplicate_owner_identity")
    actual = {row["id"]: row for row in observed}
    require(set(actual) == {row["id"] for row in expected}, "owner_identity_changed")
    for before in expected:
        now = actual[before["id"]]
        require(now["project"] in PROJECTS, "owner_project_mismatch")
        require(now["project"] == ("tianshu-v2-resident-obs" if now["service"].startswith("obs-")
                                    else "tianshu-v2-resident"), "owner_project_mismatch")
        for key in ("project", "service", "image_id"):
            require(now[key] == before[key], "owner_signature_changed")
        # Docker enumerates mount maps in varying order. Retain every field and duplicate,
        # comparing canonical entries without treating list order as a changed mount.
        require(sorted(canonical(mount) for mount in now["mounts"]) ==
                sorted(canonical(mount) for mount in before["mounts"]), "owner_signature_changed")
        require(now["state"] == "exited" and now["exit"] == 0 and now["restart"] == "no",
                "normal_stopped_owner_required")
        require(not any(now[key] for key in ("running", "paused", "restarting", "dead", "oom", "error")),
                "owner_exit_not_confirmed")


def inventory(root: Path, check) -> dict:
    root = safe_path(root)
    entries = {}; total = 0
    for directory, dirs, names in walk_tree(root):
        for path in [Path(directory), *(Path(directory) / name for name in sorted(names))]:
            check()
            info = path.stat(); relative = path.relative_to(root).as_posix()
            require(len(entries) < 100000, "file_count_limit")
            item = {"mode": stat.S_IMODE(info.st_mode), "uid": info.st_uid, "gid": info.st_gid}
            if path.is_dir():
                item["kind"] = "directory"
            else:
                require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "unsupported_state_file")
                total += info.st_size; require(total <= LIMIT, "deployment_size_limit")
                item.update(kind="file", size=info.st_size, sha256=file_hash(path))
                after = path.stat()
                require((info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns) ==
                        (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns), "authority_changed")
            entries[relative] = item
    return {"bytes": total, "entries": entries}


def copy_tree(source: Path, target: Path, recorded: dict, check) -> None:
    require(not target.exists(), "destination_exists")
    target.mkdir(mode=0o700)
    for name, item in sorted(recorded["entries"].items(), key=lambda pair: (pair[0].count("/"), pair[0])):
        check(); destination = target if name == "." else target / name
        if item["kind"] == "directory":
            destination.mkdir(mode=0o700, exist_ok=name == ".")
        else:
            copy_new(source / name, destination, limit=LIMIT, check_cancel=check)
        os.chmod(destination, item["mode"])
        if os.name == "posix":
            os.chown(destination, item["uid"], item["gid"])
    sync_tree(target)
    require(inventory(target, check) == recorded, "copied_state_mismatch")


def verify_sqlite(root: Path, recorded: dict, scratch: Path, check, memory_guards=()) -> tuple[int, int]:
    databases = 0
    verified_guards = set()
    for name, item in recorded["entries"].items():
        if item["kind"] != "file":
            continue
        path = root / name
        with path.open("rb") as stream:
            header = stream.read(16)
        if header != b"SQLite format 3\x00":
            continue
        check(); databases += 1
        # SQLite may maintain shared-memory metadata while reading a WAL. Use a separate
        # disposable verification copy so neither authority nor disabled restore changes.
        folder = scratch / str(databases); folder.mkdir(mode=0o700)
        database = folder / "database.sqlite"
        for suffix in ("", "-wal", "-shm", "-journal"):
            companion = Path(str(path) + suffix)
            if companion.exists():
                copy_new(companion, Path(str(database) + suffix), limit=LIMIT, check_cancel=check)
        connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=5)
        try:
            connection.execute("PRAGMA trusted_schema=OFF")
            connection.set_progress_handler(lambda: int(time.monotonic() > check.deadline), 10000)
            require(connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)], "sqlite_integrity_failed")
            require(connection.execute("PRAGMA foreign_key_check").fetchone() is None, "sqlite_foreign_key_failed")
            guard_name = name + ".source-guard.json"
            if guard_name in memory_guards:
                metadata = dict(connection.execute(
                    "SELECT key,value FROM metadata WHERE key IN "
                    "('schema','source_instance','source_revision','source_recovery')"))
                require(metadata.get("schema") == "3", "unsupported_memory_schema")
                paired = {"schema": 3, "instance": metadata["source_instance"],
                          "revision": int(metadata["source_revision"]),
                          "recovery": metadata["source_recovery"]}
                require(paired["revision"] >= 0 and bool(paired["instance"]), "invalid_guard_metadata")
                require(canonical(read_json(root / guard_name)) == canonical(paired),
                        "guard_database_mismatch")
                verified_guards.add(guard_name)
        finally:
            connection.close()
    require(verified_guards == set(memory_guards), "memory_guard_not_verified")
    return databases, len(verified_guards)


def control_inventory(path: Path, check) -> dict:
    path = safe_path(path)
    if path.is_dir():
        return {"kind": "directory", "inventory": inventory(path, check)}
    info = path.stat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "unsupported_control_file")
    require(info.st_size <= LIMIT, "control_size_limit")
    value = {"kind": "file", "size": info.st_size, "sha256": file_hash(path),
             "mode": stat.S_IMODE(info.st_mode), "uid": info.st_uid, "gid": info.st_gid}
    after = path.stat()
    require((info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns) ==
            (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns), "control_changed")
    return value


def copy_control(source: Path, target: Path, recorded: dict, check) -> None:
    if recorded["kind"] == "directory":
        copy_tree(source, target, recorded["inventory"], check)
    else:
        copy_new(source, target, limit=LIMIT, check_cancel=check)
        os.chmod(target, recorded["mode"])
        if os.name == "posix":
            os.chown(target, recorded["uid"], recorded["gid"])
        sync_tree(target.parent)
    require(control_inventory(target, check) == recorded, "control_copy_mismatch")


class Deadline:
    def __init__(self, seconds: int):
        self.deadline = time.monotonic() + seconds

    def __call__(self):
        require(time.monotonic() < self.deadline, "snapshot_timeout")


def snapshot(root: Path, operation: Path, expected: list[dict], observe, *, seconds=600,
             controls=(), memory_guards=()) -> dict:
    root = safe_path(root); operation = safe_path(operation, exists=False)
    require(not operation.exists() and root not in operation.parents and operation not in root.parents,
            "independent_new_operation_required")
    require(all(not mount.get("RW") or Path(mount["Source"]).is_relative_to(root)
                for owner in expected for mount in owner["mounts"]), "writable_mount_outside_authority")
    check = Deadline(seconds)
    controls = [safe_path(path) for path in controls]
    require(len(controls) == len(set(controls)) <= 32, "invalid_control_set")
    require(all(operation != path and operation not in path.parents and path not in operation.parents
                for path in controls), "control_operation_overlap")
    require(len(memory_guards) == len(set(memory_guards)) <= 100, "invalid_guard_set")
    require(all(name.endswith(".source-guard.json") and not Path(name).is_absolute()
                and ".." not in Path(name).parts for name in memory_guards), "invalid_guard_path")
    require_stopped(expected, observe())
    original = inventory(root, check)
    external = {str(path): control_inventory(path, check) for path in controls}
    require(original["bytes"] + sum(item.get("size", item.get("inventory", {}).get("bytes", 0))
                                     for item in external.values()) <= LIMIT,
            "complete_snapshot_size_limit")
    operation.mkdir(mode=0o700)
    external_backup = operation / "controls-backup"; external_backup.mkdir(mode=0o700)
    external_restore = operation / "controls-restored-disabled"; external_restore.mkdir(mode=0o700)
    for index, path in enumerate(controls):
        destination = external_backup / str(index)
        copy_control(path, destination, external[str(path)], check)
        copy_control(destination, external_restore / str(index), external[str(path)], check)
    backup = operation / "backup"; restored = operation / "restored-disabled"
    copy_tree(root, backup, original, check)
    require_stopped(expected, observe())
    require(inventory(root, check) == original, "current_authority_diverged")
    copy_tree(backup, restored, original, check)
    scratch = operation / "sqlite-verification"; scratch.mkdir(mode=0o700)
    database_count, guard_count = verify_sqlite(restored, original, scratch, check, memory_guards)
    require(database_count > 0, "no_sqlite_evidence")
    require(inventory(restored, check) == original, "restored_state_changed")
    require(inventory(root, check) == original, "current_authority_diverged")
    require_stopped(expected, observe())
    require(all(control_inventory(path, check) == external[str(path)] for path in controls),
            "current_control_diverged")
    manifest = {"schema_version": "resident-cold-snapshot/1", "owners": expected,
                "inventory": original, "restore_status": "restored_disabled",
                "external_controls": external, "memory_guards": list(memory_guards),
                "memory_guards_verified": guard_count,
                "sqlite_databases_verified": database_count,
                "authority_unchanged": True, "functional_restore_test": False,
                "original_restore_activation": False}
    raw = canonical(manifest)
    write_new(operation / "manifest.json", raw + b"\n")
    report = {"status": "offline_restore_verified", "owner_count": 10,
              "bytes": original["bytes"], "entries": len(original["entries"]),
              "sqlite_databases_verified": database_count, "manifest_sha256": digest(raw + b"\n"),
              "external_controls_verified": len(controls), "memory_guards_verified": guard_count,
              "authority_unchanged": True, "restored_disabled": True,
              "functional_restore_test": False, "original_restore_activation": False}
    write_new(operation / "receipt.json", canonical(report) + b"\n")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expectation", type=Path, required=True)
    parser.add_argument("--operation", type=Path, required=True)
    parser.add_argument("--docker", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    try:
        expectation = read_json(args.expectation)
        require(set(expectation) == {"root", "containers", "controls", "memory_guards"}, "invalid_expectation")
        if not args.execute:
            print(json.dumps({"status": "plan", "owners_required": 10, "activation": False}))
            return 0
        require(os.name == "posix" and os.geteuid() == 0, "linux_root_required")
        require(bool(expectation["controls"]) and bool(expectation["memory_guards"]),
                "production_controls_and_guard_required")
        print(json.dumps(snapshot(Path(expectation["root"]), args.operation, expectation["containers"],
                                  lambda: inspect_owners(args.docker),
                                  controls=[Path(path) for path in expectation["controls"]],
                                  memory_guards=expectation["memory_guards"]), sort_keys=True))
        return 0
    except (RecoveryError, OSError, sqlite3.Error, subprocess.SubprocessError, KeyError, ValueError):
        print(json.dumps({"status": "refused", "reason": "snapshot_or_restore_not_verified"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
