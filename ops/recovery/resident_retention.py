"""Retain one verified resident cold backup, using the existing snapshot proof.

Only the five data directories of a recognized cold snapshot are removable.
Original manifests/receipts and new, hash-bound retention receipts remain.
No image, container, live authority, or arbitrary release directory is removed.
"""

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import uuid
from pathlib import Path, PurePosixPath, PureWindowsPath


DATA_DIRECTORIES = ("backup", "controls-backup", "restored-disabled",
                    "controls-restored-disabled", "sqlite-verification")
VERIFICATION_DIRECTORIES = DATA_DIRECTORIES[2:]
OWNERS = {"platform", "gateway", "memory", "knowledge", "companion",
          "obs-loki", "obs-vector", "obs-prometheus", "obs-grafana", "obs-guard"}


def _require(condition, code):
    if not condition:
        raise RuntimeError(code)


def _json(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _absolute(value):
    path = PurePosixPath(value)
    _require(isinstance(value, str) and ".." not in path.parts
             and (path.is_absolute() or PureWindowsPath(value).is_absolute())
             and path.as_posix() == value, "retention_absolute_path_required")
    return path


def _overlap(left, right):
    left = PurePosixPath(str(left).replace("\\", "/"))
    right = PurePosixPath(str(right).replace("\\", "/"))
    return left == right or left in right.parents or right in left.parents


def _snapshot_path(value, updates, authority):
    path, parent, root = _absolute(value), _absolute(updates), _absolute(authority)
    _require(path.name == "cold-snapshot" and parent in path.parents
             and len(path.relative_to(parent).parts) in (2, 3), "retention_snapshot_scope_invalid")
    _require(not _overlap(path, root), "retention_authority_overlap")
    return path


def validate_proof(manifest, receipt, authority):
    """Validate ownership and the existing offline-restore receipt, without secrets."""
    _require(manifest["schema_version"] == "resident-cold-snapshot/1"
             and manifest["restore_status"] == "restored_disabled"
             and receipt["status"] == "offline_restore_verified"
             and manifest["authority_unchanged"] is True
             and receipt["authority_unchanged"] is True
             and receipt["restored_disabled"] is True
             and manifest["functional_restore_test"] is False
             and manifest["original_restore_activation"] is False
             and receipt["functional_restore_test"] is False
             and receipt["original_restore_activation"] is False,
             "retention_unverified_snapshot")
    owners = manifest["owners"]
    _require(len(owners) == receipt["owner_count"] == 10
             and {row["service"] for row in owners} == OWNERS
             and len({row["id"] for row in owners}) == 10, "retention_owner_scope_changed")
    for owner in owners:
        project = "tianshu-v2-resident-obs" if owner["service"].startswith("obs-") else "tianshu-v2-resident"
        _require(owner["project"] == project, "retention_owner_scope_changed")
        _require(any(mount.get("Type") == "bind" and _overlap(mount.get("Source", ""), authority)
                     and PurePosixPath(str(authority)) in PurePosixPath(mount["Source"].replace("\\", "/")).parents
                     for mount in owner["mounts"]), "retention_authority_binding_missing")
    _require(receipt["sqlite_databases_verified"] == manifest["sqlite_databases_verified"] > 0
             and receipt["memory_guards_verified"] == manifest["memory_guards_verified"]
             and receipt["external_controls_verified"] == len(manifest["external_controls"])
             and receipt["bytes"] == manifest["inventory"]["bytes"]
             and receipt["entries"] == len(manifest["inventory"]["entries"]),
             "retention_snapshot_proof_changed")


def make_plan(*, updates_root, authority_root, keep, snapshots, containers,
              protected_images=(), allocated_kib=None):
    """Create an immutable allowlist from a read-only catalog; do not touch paths."""
    _absolute(updates_root); _absolute(authority_root)
    _require(len(snapshots) == len({row["path"] for row in snapshots}), "retention_duplicate_snapshot")
    _require(keep in {row["path"] for row in snapshots}, "retention_keep_missing")
    records = []
    for row in sorted(snapshots, key=lambda row: (row["path"] != keep, row["path"])):
        path = _snapshot_path(row["path"], updates_root, authority_root)
        validate_proof(row["manifest"], row["receipt"], authority_root)
        _require(row["receipt"]["manifest_sha256"] == row["manifest_sha256"], "retention_manifest_hash_changed")
        _require(all(re.fullmatch("[a-f0-9]{64}", row[key]) for key in ("manifest_sha256", "receipt_sha256")),
                 "retention_hash_required")
        directories = VERIFICATION_DIRECTORIES if str(path) == keep else DATA_DIRECTORIES
        present = set(row.get("children", DATA_DIRECTORIES))
        records.append({"path": str(path), "device": row["device"], "inode": row["inode"],
                        "manifest_sha256": row["manifest_sha256"], "receipt_sha256": row["receipt_sha256"],
                        "remove": [name for name in directories if name in present],
                        "keep_data": str(path) == keep})
    plan = {"schema_version": "resident-cold-retention/1", "updates_root": updates_root,
            "authority_root": authority_root, "keep": keep, "snapshots": records,
            "protected_images": list(protected_images), "allocated_kib_before": allocated_kib,
            "images_or_containers_removed": False}
    check_bind_references(plan, containers() if callable(containers) else containers)
    return plan


def check_bind_references(plan, containers):
    targets = [str(PurePosixPath(row["path"]) / name) for row in plan["snapshots"] for name in row["remove"]]
    for container in containers:
        for mount in container["mounts"]:
            if mount.get("Type") == "bind":
                _require(not any(_overlap(mount["Source"], target) for target in targets),
                         "retention_container_references_data")


def docker_references(docker):
    """Read every container, including stopped containers; never inspect environment."""
    identifiers = subprocess.run([docker, "ps", "-aq", "--no-trunc"], check=True,
                                 capture_output=True, text=True, timeout=30).stdout.split()
    _require(all(re.fullmatch("[a-f0-9]{64}", value) for value in identifiers), "retention_container_id_invalid")
    template = '{"id":{{json .Id}},"image":{{json .Image}},"mounts":{{json .Mounts}}}'
    result = []
    for offset in range(0, len(identifiers), 64):
        process = subprocess.run([docker, "inspect", "--format", template, *identifiers[offset:offset + 64]],
                                 check=True, capture_output=True, text=True, timeout=30)
        result.extend(json.loads(line) for line in process.stdout.splitlines())
    _require({row["id"] for row in result} == set(identifiers), "retention_container_inventory_incomplete")
    return result


def _safe_directory(path):
    path = Path(path)
    _require(path.is_absolute(), "retention_absolute_path_required")
    for part in [*reversed(path.parents), path]:
        info = part.lstat()
        _require(stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode), "retention_directory_changed")
    return path


def read_evidence(path, authority):
    path = _safe_directory(path)
    info = path.stat()
    for name in ("manifest.json", "receipt.json"):
        file_info = (path / name).lstat()
        _require(stat.S_ISREG(file_info.st_mode) and file_info.st_nlink == 1, "retention_metadata_changed")
    manifest_raw, receipt_raw = (path / "manifest.json").read_bytes(), (path / "receipt.json").read_bytes()
    manifest, receipt = json.loads(manifest_raw), json.loads(receipt_raw)
    validate_proof(manifest, receipt, str(authority).replace("\\", "/"))
    _require(receipt["manifest_sha256"] == _sha(manifest_raw), "retention_manifest_hash_changed")
    return ({"path": path.as_posix(), "device": info.st_dev, "inode": info.st_ino,
             "manifest_sha256": _sha(manifest_raw), "receipt_sha256": _sha(receipt_raw),
             "manifest": manifest, "receipt": receipt, "children": [child.name for child in path.iterdir()]})


def verify_kept_copies(path, manifest, snapshot):
    """Reuse the snapshot's byte/mode/uid/gid inventory for the only remaining backup."""
    path = _safe_directory(path)
    check = snapshot.Deadline(600)
    _require(snapshot.inventory(_safe_directory(path / "backup"), check) == manifest["inventory"],
             "retention_kept_backup_changed")
    controls = _safe_directory(path / "controls-backup")
    _require({child.name for child in controls.iterdir()} == {str(index) for index in range(len(manifest["external_controls"]))},
             "retention_kept_controls_changed")
    # Manifest JSON sorts source keys; copy indices retain the caller's order.
    # Compare the complete multiset rather than inventing a sorted-key mapping.
    actual = [snapshot.control_inventory(controls / str(index), check) for index in range(len(manifest["external_controls"]))]
    _require(sorted(_json(value) for value in actual) == sorted(_json(value) for value in manifest["external_controls"].values()),
             "retention_kept_controls_changed")


def verification_copies_available(path):
    """A removed validation tree requires an applied, manifest-bound retention proof."""
    path = Path(path)
    missing = [name for name in VERIFICATION_DIRECTORIES if not (path / name).is_dir()]
    if not missing:
        return True
    manifest_hash = _sha((path / "manifest.json").read_bytes())
    receipt_hash = _sha((path / "receipt.json").read_bytes())
    for file in path.glob("retention-*.json"):
        record = json.loads(file.read_bytes())
        if (record.get("status") in {"applied", "verified_before_pruning"} and record.get("snapshot") == path.as_posix()
                and record.get("manifest_sha256") == manifest_hash and record.get("receipt_sha256") == receipt_hash
                and record.get("kept_backup_and_controls_verified") is True and record.get("kept_data") is True
                and set(missing) <= set(record.get("verification_directories", record.get("removed", [])))):
            return True
    return False


def _write_record(path, value):
    with Path(path).open("xb") as stream:
        stream.write(_json(value) + b"\n"); stream.flush(); os.fsync(stream.fileno())
    if os.name == "posix":
        descriptor = os.open(Path(path).parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _free_bytes(path):
    usage = shutil.disk_usage(path)
    return usage.free


def apply_plan(plan, *, snapshot, containers, execute=False):
    """Validate all pins first; dry-run and execution share the exact same checks."""
    _require(plan["schema_version"] == "resident-cold-retention/1", "retention_plan_version_invalid")
    _safe_directory(plan["updates_root"])
    keep, proof = None, {}
    for row in plan["snapshots"]:
        _snapshot_path(row["path"], plan["updates_root"], plan["authority_root"])
        allowed = VERIFICATION_DIRECTORIES if row["path"] == plan["keep"] else DATA_DIRECTORIES
        _require(row["keep_data"] == (row["path"] == plan["keep"])
                 and len(row["remove"]) == len(set(row["remove"]))
                 and set(row["remove"]) <= set(allowed), "retention_deletion_scope_changed")
        evidence = read_evidence(row["path"], plan["authority_root"])
        for key in ("device", "inode", "manifest_sha256", "receipt_sha256"):
            _require(evidence[key] == row[key], "retention_catalog_changed")
        for name in row["remove"]:
            _safe_directory(Path(row["path"]) / name)
        proof[row["path"]] = evidence
        if row["keep_data"]:
            _require(keep is None, "retention_duplicate_keep")
            keep = row
    _require(keep is not None, "retention_keep_missing")
    check_bind_references(plan, containers() if callable(containers) else containers)
    verify_kept_copies(keep["path"], proof[keep["path"]]["manifest"], snapshot)
    identity = _sha(_json(plan) + b"\n")
    before = _free_bytes(plan["updates_root"])
    report = {"status": "planned", "plan_sha256": identity, "keep": plan["keep"],
              "snapshots": len(plan["snapshots"]), "old_full_backups": len(plan["snapshots"]) - 1,
              "kept_backup_and_controls_verified": True, "removed": [], "free_bytes_before": before,
              "protected_images": plan["protected_images"], "images_or_containers_removed": False}
    if not execute:
        return report
    # Persist proof before removing any validation copy. A process interruption
    # may prevent an applied/partial receipt, but cannot erase the completed
    # offline verification or the freshly rechecked sole backup's proof.
    checkpoint = Path(keep["path"]) / ("retention-checkpoint-" + identity + ".json")
    checkpoint_value = {"status": "verified_before_pruning", "plan_sha256": identity,
                        "snapshot": keep["path"], "manifest_sha256": keep["manifest_sha256"],
                        "receipt_sha256": keep["receipt_sha256"], "kept_data": True,
                        "kept_backup_and_controls_verified": True,
                        "verification_directories": list(VERIFICATION_DIRECTORIES)}
    _write_record(checkpoint, checkpoint_value)
    report["verified_checkpoint"] = checkpoint.as_posix()
    # Checkpoint the kept snapshot first; a later failure can never erase it.
    for row in sorted(plan["snapshots"], key=lambda row: (not row["keep_data"], row["path"])):
        check_bind_references(plan, containers() if callable(containers) else containers)
        path = Path(row["path"])
        removed = []
        receipt = path / ("retention-" + identity + ".json")
        _require(not receipt.exists(), "retention_receipt_already_exists")
        try:
            for name in row["remove"]:
                current = read_evidence(path, plan["authority_root"])
                _require(all(current[key] == row[key] for key in ("device", "inode", "manifest_sha256", "receipt_sha256")),
                         "retention_catalog_changed")
                target = _safe_directory(path / name)
                shutil.rmtree(target)
                removed.append(name)
                report["removed"].append(target.as_posix())
        finally:
            removed = [name for name in row["remove"] if not (path / name).exists()]
            record = {"status": "applied" if removed == row["remove"] else "partially_applied",
                      "plan_sha256": identity, "snapshot": path.as_posix(), "manifest_sha256": row["manifest_sha256"],
                      "receipt_sha256": row["receipt_sha256"], "removed": removed, "kept_data": row["keep_data"],
                      "kept_backup_and_controls_verified": True, "keep": plan["keep"]}
            _write_record(receipt, record)
    report.update(status="applied", free_bytes_after=_free_bytes(plan["updates_root"]))
    report["observed_free_delta_bytes"] = report["free_bytes_after"] - before
    report["free_delta_includes_concurrent_workloads"] = True
    return report


def after_verified_snapshot(root, operation, *, snapshot, docker):
    """The resident cold CLI and rendered releases call this only after a good receipt."""
    root, operation = Path(root), Path(operation)
    updates = root.with_name(root.name + "-updates")
    if operation.name != "cold-snapshot" or updates not in operation.parents:
        return {"status": "outside_resident_release_namespace"}
    skipped = []
    try:
        _require(bool(docker), "retention_docker_required")
        records = []
        for path in sorted([*updates.glob("*/cold-snapshot"), *updates.glob("*/*/cold-snapshot")]):
            try:
                evidence = read_evidence(path, root)
            except (OSError, ValueError, KeyError, RuntimeError):
                # A failed or foreign historical attempt must not turn a good
                # fresh cold backup into a production release failure.
                if path == operation:
                    raise
                skipped.append(path.as_posix())
                continue
            if path == operation or (path / "backup").is_dir():
                records.append(evidence)
        plan = make_plan(updates_root=updates.as_posix(), authority_root=root.as_posix(), keep=operation.as_posix(),
                         snapshots=records, containers=docker_references(docker))
        result = apply_plan(plan, snapshot=snapshot, containers=lambda: docker_references(docker), execute=True)
        result["skipped_unverified_snapshots"] = skipped
        return result
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as error:
        # The original verified snapshot receipt remains authoritative. Cleanup
        # failure never asks its caller to stop owners or roll back live data.
        result = {"status": "requires_inspection", "keep": operation.as_posix(),
                  "code": str(error) if re.fullmatch("[a-z0-9_]+", str(error)) else "retention_failed",
                  "skipped_unverified_snapshots": skipped, "database_restore": False}
        try:
            with (operation / ("retention-deferred-" + uuid.uuid4().hex + ".json")).open("xb") as stream:
                stream.write(_json(result) + b"\n"); stream.flush(); os.fsync(stream.fileno())
        except OSError:
            result["retention_receipt_write_failed"] = True
        return result


def install_snapshot_retention(snapshot, docker):
    """Adapt fixed older resident snapshot sources without duplicating their backup code."""
    original = snapshot.snapshot
    if getattr(original, "resident_retention_installed", False):
        return

    def retained_snapshot(root, operation, expected, observe, **kwargs):
        # The current public implementation accepts docker; immutable historical
        # sources get the same post-verification policy through this adapter.
        import inspect
        current = "docker" in inspect.signature(original).parameters
        result = original(root, operation, expected, observe, **({**kwargs, "docker": docker} if current else kwargs))
        if not current:
            result["retention"] = after_verified_snapshot(root, operation, snapshot=snapshot, docker=docker)
        return result

    retained_snapshot.resident_retention_installed = True
    snapshot.snapshot = retained_snapshot
