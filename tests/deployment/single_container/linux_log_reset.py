"""Explicit post-cold-backup reset of the nine original runtime log roots only.

No action on import, stop/start, SSH, database connection or permission change.
The coordinator calls reset with the final cold stage and stopped-owner snapshot.
"""

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import time
from datetime import datetime


PRODUCERS = ("platform", "gateway", "memory", "knowledge", "companion")
CORE_OWNERS = set(PRODUCERS) | {
    "obs-guard",
    "obs-vector",
    "obs-loki",
    "obs-prometheus",
    "obs-grafana",
}
AUX_OWNERS = {"onebot", "inputs", "publisher"}
AUX_COVERAGE = {"onebot", "comic-inputs", "comic-publisher"}
ROOTS = (
    {f"/srv/tianshu/logs/{name}": name for name in PRODUCERS}
    | {
        f"/srv/tianshu/observability/data/{name}": "obs-" + name
        for name in ("guard", "vector", "loki")
    }
    | {"/mnt/tianshu-nas/loki-chunks": "obs-loki"}
)


def require(condition, code):
    if not condition:
        raise RuntimeError(code)


def read(value):
    return value if isinstance(value, dict) else json.loads(Path(value).read_bytes())


def digest(path):
    path = Path(path)
    safe_directory(path.parent)
    require(stat.S_ISREG(path.lstat().st_mode), "regular_backup_manifest_required")
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def identity(row):
    if "Id" not in row:  # Metadata-only Docker --format result.
        return row
    labels = row["Config"].get("Labels") or {}
    return {
        "id": row["Id"],
        "image": row["Image"],
        "mounts": row["Mounts"],
        "service": labels.get("com.docker.compose.service"),
        "project": labels.get("com.docker.compose.project"),
        "state": row["State"],
        "restart": row["HostConfig"]["RestartPolicy"]["Name"],
    }


def canonical_mounts(value):
    return sorted(json.dumps(row, sort_keys=True) for row in value)


def inspect_owners(identifiers):
    template = (
        '{"id":{{json .Id}},"image":{{json .Image}},"mounts":{{json .Mounts}},'
        '"service":{{json (index .Config.Labels "com.docker.compose.service")}},'
        '"project":{{json (index .Config.Labels "com.docker.compose.project")}},'
        '"state":{{json .State}},"restart":{{json .HostConfig.RestartPolicy.Name}}}'
    )
    result = subprocess.run(
        [
            "/usr/bin/docker",
            "--host",
            "unix:///var/run/docker.sock",
            "inspect",
            "--format",
            template,
            *identifiers,
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    return [json.loads(line) for line in result.stdout.splitlines()]


def check_owners(expected, inspect):
    current = [
        identity(row) for row in inspect([row["id"] for row in expected.values()])
    ]
    require(len(current) == 13, "thirteen_current_owners_required")
    rows = {row["service"]: row for row in current}
    require(set(rows) == set(expected), "current_owner_set_changed")
    for name, before in expected.items():
        now = rows[name]
        require(
            all(
                now[key] == before[key] for key in ("id", "image", "project", "service")
            )
            and canonical_mounts(now["mounts"]) == canonical_mounts(before["mounts"]),
            "owner_identity_image_or_mount_changed",
        )
        for row in (before, now):
            state = row["state"]
            require(
                state["Status"] == "exited"
                and state["ExitCode"] == 0
                and row["restart"] == "no"
                and not any(
                    state.get(key)
                    for key in (
                        "Running",
                        "Paused",
                        "Restarting",
                        "Dead",
                        "OOMKilled",
                        "Error",
                    )
                ),
                "normal_stopped_owner_required",
            )
        require(
            now["state"]["FinishedAt"] == before["state"]["FinishedAt"],
            "owner_cold_epoch_changed",
        )


def safe_directory(path):
    path = Path(path)
    require(
        path.is_absolute() and ".." not in path.parts, "absolute_directory_required"
    )
    for item in (*reversed(path.parents), path):
        info = item.lstat()
        require(
            stat.S_ISDIR(info.st_mode) and not item.is_symlink(),
            "directory_link_or_kind_refused",
        )
        require(
            not getattr(item, "is_junction", lambda: False)(),
            "directory_junction_refused",
        )
    return path


def mountpoints():
    if os.name != "posix":
        return set()

    def unescape(value):
        return re.sub(r"\\([0-7]{3})", lambda match: chr(int(match[1], 8)), value)

    return {
        Path(unescape(line.split()[4]))
        for line in Path("/proc/self/mountinfo").read_text().splitlines()
    }


def inventory(root, mounts):
    counts = {"files": 0, "directories": 0, "symlinks": 0, "bytes": 0}
    device = root.stat().st_dev

    def walk_error(error):
        raise error

    for folder, directories, names in os.walk(
        root, followlinks=False, onerror=walk_error
    ):
        require(
            os.access(folder, os.R_OK | os.W_OK | os.X_OK),
            "log_directory_permission_denied",
        )
        for name in [*directories, *names]:
            item = Path(folder) / name
            info = item.lstat()
            if stat.S_ISLNK(info.st_mode):
                counts["symlinks"] += 1
            elif stat.S_ISDIR(info.st_mode):
                require(
                    info.st_dev == device
                    and item not in mounts
                    and not getattr(item, "is_junction", lambda: False)(),
                    "nested_log_mount_refused",
                )
                counts["directories"] += 1
            else:
                counts["files"] += 1
                counts["bytes"] += info.st_size if stat.S_ISREG(info.st_mode) else 0
    return counts


def metadata(path):
    info = path.lstat()
    return (
        info.st_dev,
        info.st_ino,
        info.st_uid,
        info.st_gid,
        stat.S_IMODE(info.st_mode),
    )


def clear_children(root, expected_metadata):
    if os.name == "posix":
        require(shutil.rmtree.avoids_symlink_attacks, "fd_safe_rmtree_required")
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            info = os.fstat(fd)
            require(
                (
                    info.st_dev,
                    info.st_ino,
                    info.st_uid,
                    info.st_gid,
                    stat.S_IMODE(info.st_mode),
                )
                == expected_metadata,
                "log_root_identity_changed",
            )
            for name in os.listdir(fd):
                if stat.S_ISDIR(
                    os.stat(name, dir_fd=fd, follow_symlinks=False).st_mode
                ):
                    shutil.rmtree(name, dir_fd=fd)
                else:
                    os.unlink(name, dir_fd=fd)
            os.fsync(fd)
        finally:
            os.close(fd)
    else:  # Portable synthetic fixtures; the actual host uses the fd-safe branch.
        for child in root.iterdir():
            if stat.S_ISDIR(child.lstat().st_mode):
                shutil.rmtree(child)
            else:
                child.unlink()


def backup_verified(stage, backup, auxiliary, expected):
    operation = Path(backup["operation"])
    require(operation == stage / "cold-snapshot", "same_fresh_cold_stage_required")
    safe_directory(operation)
    manifest_path = operation / "manifest.json"
    report = backup["receipt"]
    disk_receipt = read(operation / "receipt.json")
    require(
        backup.get("status") == "cold_backup_verified"
        and report.get("status") == "offline_restore_verified"
        and report.get("restored_disabled") is True
        and report.get("authority_unchanged") is True
        and report.get("manifest_sha256") == digest(manifest_path)
        and disk_receipt.get("manifest_sha256") == report["manifest_sha256"]
        and disk_receipt.get("status") == "offline_restore_verified"
        and disk_receipt.get("restored_disabled") is True
        and disk_receipt.get("authority_unchanged") is True,
        "fresh_verified_cold_backup_required",
    )
    manifest = read(manifest_path)
    require(
        manifest.get("schema_version") == "resident-cold-snapshot/1",
        "cold_manifest_kind_changed",
    )
    owners = manifest["owners"]
    require(
        len(owners) == 10 and {row["service"] for row in owners} == CORE_OWNERS,
        "cold_manifest_ten_owners_required",
    )
    for row in owners:
        before = expected[row["service"]]
        require(
            row["id"] == before["id"]
            and row["image_id"] == before["image"]
            and row["project"] == before["project"]
            and canonical_mounts(row["mounts"]) == canonical_mounts(before["mounts"]),
            "cold_manifest_owner_changed",
        )
    finished = [
        datetime.fromisoformat(
            row["state"]["FinishedAt"].replace("Z", "+00:00")
        ).timestamp()
        for row in expected.values()
    ]
    require(
        manifest_path.stat().st_mtime >= max(finished),
        "cold_backup_predates_current_stop",
    )
    aux_manifest = Path(auxiliary["manifest_path"])
    require(
        not any(aux_manifest.is_relative_to(root) for root in ROOTS),
        "independent_auxiliary_manifest_required",
    )
    require(
        auxiliary.get("status") == "auxiliary_backup_verified"
        and len(auxiliary.get("coverage", [])) == 3
        and set(auxiliary.get("coverage", [])) == AUX_COVERAGE
        and auxiliary.get("cold_manifest_sha256") == report["manifest_sha256"]
        and auxiliary.get("manifest_sha256") == digest(auxiliary["manifest_path"]),
        "same_epoch_verified_auxiliary_backup_required",
    )
    return report["manifest_sha256"]


def write_new(path, value):
    with os.fdopen(
        os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w"
    ) as stream:
        json.dump(value, stream, sort_keys=True, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    if os.name == "posix":
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def reset(
    stage, owners13, backup_receipt, roots_plan, *, aux_backup_receipt, inspect=None
):
    """Clear only the fixed roots' descendants after all prerequisites pass.

    Inputs may be parsed dictionaries or explicit JSON paths. `inspect` is the
    narrow metadata reader injection used by synthetic tests; default is Docker.
    Root itself and its uid/gid/mode are preserved. No implicit retries occur.
    """
    stage = safe_directory(stage)
    require(
        not any(
            stage == Path(root)
            or stage.is_relative_to(root)
            or Path(root).is_relative_to(stage)
            for root in ROOTS
        ),
        "independent_receipt_stage_required",
    )
    expected = {name: identity(row) for name, row in read(owners13).items()}
    require(
        set(expected) == CORE_OWNERS | AUX_OWNERS,
        "exact_original_thirteen_owners_required",
    )
    require(
        len({row["id"] for row in expected.values()}) == 13
        and all(
            re.fullmatch("[0-9a-f]{64}", row["id"])
            and re.fullmatch("sha256:[0-9a-f]{64}", row["image"])
            and row["service"] == name
            for name, row in expected.items()
        ),
        "full_original_owner_identities_required",
    )
    inspect = inspect or inspect_owners
    check_owners(expected, inspect)
    backup = read(backup_receipt)
    manifest_sha = backup_verified(stage, backup, read(aux_backup_receipt), expected)
    roots = read(roots_plan)["roots"]
    require(
        len(roots) == 9
        and {row["source"] for row in roots} == set(ROOTS)
        and all(row["service"] == ROOTS[row["source"]] for row in roots),
        "exact_nine_log_roots_required",
    )
    for row in roots:
        source = row["source"]
        reader_names = (
            ("obs-guard", "obs-vector")
            if row["service"] in PRODUCERS
            else (row["service"],)
        )
        for reader in reader_names:
            matches = [
                mount
                for mount in expected[reader]["mounts"]
                if mount.get("Type") == "bind" and mount.get("Source") == source
            ]
            require(len(matches) == 1, "original_log_root_bind_required")
    mounts = mountpoints()
    records = []
    for row in roots:
        root = safe_directory(row["source"])
        counts = inventory(root, mounts)  # All nine preflight before any deletion.
        records.append(
            {"root": str(root), "before": counts, "metadata": metadata(root)}
        )
    require(not (stage / "log-reset-started.json").exists(), "new_log_reset_required")
    check_owners(expected, inspect)
    receipt = {
        "status": "log_reset_started",
        "cold_manifest_sha256": manifest_sha,
        "roots": records,
        "started_at": time.time(),
        "business_data_deleted": False,
    }
    write_new(stage / "log-reset-started.json", receipt)
    try:
        for record in records:
            root = safe_directory(record["root"])
            require(
                metadata(root) == tuple(record["metadata"]), "log_root_identity_changed"
            )
            inventory(root, mountpoints())
            clear_children(root, tuple(record["metadata"]))
            require(
                not any(root.iterdir()) and metadata(root) == tuple(record["metadata"]),
                "log_root_not_empty_or_metadata_changed",
            )
            record["deleted"] = record["before"]
        check_owners(expected, inspect)
        receipt.update(
            status="runtime_logs_reset",
            finished_at=time.time(),
            deleted_totals={
                key: sum(row["deleted"][key] for row in records)
                for key in ("files", "directories", "symlinks", "bytes")
            },
        )
        write_new(stage / "log-reset.json", receipt)
        return receipt
    except BaseException as exc:
        receipt.update(
            status="log_reset_incomplete_no_automatic_retry",
            error_class=type(exc).__name__,
        )
        write_new(stage / "log-reset-failed.json", receipt)
        raise
