"""Plan-first offline backups, disabled restores, and independent code selection."""

import os
import uuid
from contextlib import contextmanager

from .manifest import identifier, load_deployment, release, sha256
from .safety import (
    canonical,
    child,
    copy_new,
    digest,
    file_hash,
    files,
    lease,
    read_bytes,
    read_json,
    require,
    safe_path,
    sync_dir,
    sync_tree,
    write_new,
)
from .snapshot import (
    backup_sqlite,
    enumerate_inputs,
    freeze,
    state_fingerprints,
    verify_guards,
)


class Control:
    def __init__(self, fail_at=None, cancel=None):
        self.fail_at = fail_at
        self.cancel = cancel or (lambda: False)

    def check(self, point=None):
        require(not self.cancel(), "cancelled")
        require(point is None or point != self.fail_at, "injected_failure")


def create_sandbox(root, scope_id, *, execute=False):
    require(str(uuid.UUID(scope_id)) == scope_id, "invalid_scope_id")
    root = safe_path(root, exists=False)
    require(not root.exists(), "sandbox_must_be_new")
    require(root.parent.exists(), "parent_missing")
    result = {
        "operation": "init-sandbox",
        "mode": "execute" if execute else "plan",
        "scope_id": scope_id,
        "environment": "synthetic_only",
    }
    if execute:
        root.mkdir(mode=0o700)
        write_new(
            root / ".recovery-scope.json",
            canonical(
                {
                    "schema_version": "1.0.0",
                    "scope_id": scope_id,
                    "environment": "synthetic",
                    "ownership_protocol": "cooperative-offline-v1",
                }
            ),
        )
        write_new(root / ".recovery-owner", b"0")
        for name in ("deployments", "backups", "quarantine", "updates"):
            (root / name).mkdir()
        sync_dir(root)
    return result


class Recovery:
    def __init__(
        self, root, scope_id, *, control=None, max_bytes=1024**3, max_files=10000
    ):
        self.root = safe_path(root)
        scope = read_json(child(self.root, ".recovery-scope.json"))
        require(
            scope
            == {
                "schema_version": "1.0.0",
                "scope_id": scope_id,
                "environment": "synthetic",
                "ownership_protocol": "cooperative-offline-v1",
            },
            "untrusted_synthetic_scope",
        )
        self.scope_id = scope_id
        self.control = control or Control()
        require(
            0 < max_bytes <= 64 * 1024**3 and 0 < max_files <= 100000,
            "invalid_resource_budget",
        )
        self.max_bytes, self.max_files = max_bytes, max_files

    def deployment(self, name, *, authority=False, expected_id=None):
        identifier(name)
        root = child(self.root, "deployments/" + name)
        marker = read_json(child(root, ".deployment.json"))
        require(
            marker["scope_id"] == self.scope_id
            and marker["environment"] == "synthetic",
            "deployment_scope_mismatch",
        )
        require(
            marker["status"] in {"offline", "restored_disabled"},
            "deployment_not_offline",
        )
        if expected_id is not None:
            require(
                marker["deployment_id"] == expected_id, "deployment_identity_mismatch"
            )
        if authority:
            require(
                marker["role"] == "authority" and expected_id is not None,
                "current_authority_required",
            )
        return root, marker, load_deployment(root)

    @contextmanager
    def exclusive(self):
        self.control.check()
        with lease(child(self.root, ".recovery-owner")):
            yield

    def inputs(self, root, manifest, resources):
        return enumerate_inputs(
            root,
            manifest,
            resources,
            max_bytes=self.max_bytes,
            max_files=self.max_files,
        )

    @contextmanager
    def stage(self, parent):
        path = child(self.root, parent + "/.pending-" + uuid.uuid4().hex, exists=False)
        path.mkdir(mode=0o700)
        try:
            yield path
        except BaseException:
            # Never recursively remove a failed backup/restore; retain private quarantine evidence.
            if path.exists():
                try:
                    write_new(
                        path / "ABORTED.json",
                        canonical({"status": "aborted", "activation": "disabled"}),
                    )
                except OSError:
                    pass
            raise

    def _backup(self, source_name, backup_name):
        source, marker, (manifest, inventory, resources) = self.deployment(source_name)
        target = child(self.root, "backups/" + identifier(backup_name), exists=False)
        require(not target.exists(), "destination_exists")
        with freeze(source, inventory, resources):
            before = self.inputs(source, manifest, resources)
            self.control.check("before_snapshot")
            with self.stage("backups") as stage:
                payload = stage / "payload"
                payload.mkdir()
                for volume in manifest["volumes"]:
                    if volume["kind"] == "directory":
                        child(payload, volume["host_path"], exists=False).mkdir(
                            parents=True, exist_ok=True
                        )
                entries = []
                for index, (name, kind) in enumerate(before.items()):
                    self.control.check()
                    original = child(source, name)
                    destination = child(payload, name, exists=False)
                    if kind == "sqlite":
                        backup_sqlite(original, destination, self.control.check)
                    elif kind == "owner_lock":
                        # An OS lease is not persisted authority. Recreate only in disabled target.
                        continue
                    else:
                        copy_new(
                            original,
                            destination,
                            limit=self.max_bytes,
                            check_cancel=self.control.check,
                        )
                    entries.append(
                        {
                            "path": name,
                            "kind": kind,
                            "size": destination.stat().st_size,
                            "sha256": file_hash(destination),
                        }
                    )
                    require(
                        sum(entry["size"] for entry in entries) <= self.max_bytes,
                        "total_size_limit",
                    )
                    if index == 0:
                        self.control.check("after_first_file")
                require(
                    before == self.inputs(source, manifest, resources),
                    "source_file_set_changed",
                )
                for entry in entries:
                    if entry["kind"] != "sqlite":
                        require(
                            file_hash(child(source, entry["path"])) == entry["sha256"],
                            "source_changed_during_snapshot",
                        )
                copied_manifest, copied_inventory, _ = load_deployment(payload)
                require(
                    canonical(copied_manifest) == canonical(manifest)
                    and canonical(copied_inventory) == canonical(inventory),
                    "source_changed_during_snapshot",
                )
                facts = state_fingerprints(payload, resources, self.control.check)
                require(
                    facts == state_fingerprints(source, resources, self.control.check),
                    "source_changed_during_snapshot",
                )
                verify_guards(payload, inventory, resources)
                sealed = {
                    "schema_version": "1.0.0",
                    "scope_id": self.scope_id,
                    "source_deployment_id": marker["deployment_id"],
                    "release_manifest_sha256": file_hash(
                        child(payload, "release-manifest.json")
                    ),
                    "inventory_sha256": file_hash(
                        child(payload, "recovery-inventory.json")
                    ),
                    "consistency": "cooperative_offline_all_sqlite_reserved_wal_folded",
                    "entries": entries,
                    "state_fingerprints": facts,
                    "activation": "disabled",
                    "directory_fsync": os.name != "nt",
                }
                blob = canonical(sealed)
                write_new(stage / "snapshot.json", blob)
                sync_tree(stage)
                self.control.check("before_publish")
                require(not target.exists(), "destination_exists")
                stage.rename(target)
                sync_dir(target.parent)
        return {
            "operation": "backup",
            "status": "complete",
            "backup": backup_name,
            "snapshot_sha256": digest(blob),
            "release_manifest_sha256": sealed["release_manifest_sha256"],
            "directory_fsync": os.name != "nt",
        }

    def backup(self, source_name, backup_name, *, execute=False):
        source, _, (manifest, _, resources) = self.deployment(source_name)
        target = child(self.root, "backups/" + identifier(backup_name), exists=False)
        require(not target.exists(), "destination_exists")
        inputs = self.inputs(source, manifest, resources)
        if not execute:
            return {
                "operation": "backup",
                "mode": "plan",
                "source": source_name,
                "backup": backup_name,
                "files": len(inputs),
                "stop_order": ["platform", "companion", "memory", "gateway"],
                "requirement": "all_synthetic_runtime_owners_release_scope_lease",
                "wal": "folded_using_sqlite_backup",
                "config": "references_only",
                "activation": "disabled",
            }
        with self.exclusive():
            return self._backup(source_name, backup_name)

    def inspect_backup(self, name, snapshot_sha256):
        package = child(self.root, "backups/" + identifier(name))
        index = child(package, "snapshot.json")
        require(file_hash(index) == sha256(snapshot_sha256), "snapshot_digest_mismatch")
        document = read_json(index)
        require(
            document["schema_version"] == "1.0.0"
            and document["scope_id"] == self.scope_id,
            "backup_scope_mismatch",
        )
        require(document["activation"] == "disabled", "invalid_snapshot_state")
        payload = child(package, "payload")
        entries = document["entries"]
        require(0 < len(entries) <= self.max_files, "file_count_limit")
        require(
            sum(item["size"] for item in entries) <= self.max_bytes, "total_size_limit"
        )
        expected = {"snapshot.json"}
        seen = set()
        for entry in entries:
            path = child(payload, entry["path"])
            require(entry["path"].casefold() not in seen, "duplicate_snapshot_path")
            seen.add(entry["path"].casefold())
            require(
                type(entry["size"]) is int and entry["size"] >= 0, "invalid_file_size"
            )
            require(
                path.stat().st_size == entry["size"]
                and file_hash(path) == sha256(entry["sha256"]),
                "payload_digest_mismatch",
            )
            expected.add("payload/" + entry["path"])
        require(
            set(files(package, max_files=self.max_files + 1)) == expected,
            "unexpected_package_file",
        )
        manifest, inventory, resources = load_deployment(payload)
        require(
            file_hash(child(payload, "release-manifest.json"))
            == document["release_manifest_sha256"],
            "release_binding_mismatch",
        )
        require(
            file_hash(child(payload, "recovery-inventory.json"))
            == document["inventory_sha256"],
            "inventory_binding_mismatch",
        )
        # Detect omissions even if a malformed index was supplied with a matching receipt.
        expected_inputs = self.inputs_for_payload(payload, manifest, resources)
        require(
            {entry["path"]: entry["kind"] for entry in entries} == expected_inputs,
            "incomplete_snapshot",
        )
        verify_guards(payload, inventory, resources)
        require(
            state_fingerprints(payload, resources, self.control.check)
            == document["state_fingerprints"],
            "snapshot_state_mismatch",
        )
        return document, payload, (manifest, inventory, resources)

    def inputs_for_payload(self, root, manifest, resources):
        # Owner-lock files deliberately do not travel inside a backup.
        no_locks = {
            key: item for key, item in resources.items() if item["kind"] != "owner_lock"
        }
        omitted = {
            item["path"] for item in resources.values() if item["kind"] == "owner_lock"
        }
        return enumerate_inputs(
            root,
            manifest,
            no_locks,
            max_bytes=self.max_bytes,
            max_files=self.max_files,
            omitted=omitted,
        )

    def verify(self, backup_name, snapshot_sha256):
        with self.exclusive():
            document, _, _ = self.inspect_backup(backup_name, snapshot_sha256)
        return {
            "operation": "verify-backup",
            "status": "integrity_verified",
            "release_manifest_sha256": document["release_manifest_sha256"],
            "functional_acceptance": "not_run",
            "restoration_authority": "not_checked",
            "activation": "disabled",
        }

    def restore(
        self,
        backup_name,
        snapshot_sha256,
        target_name,
        authority_name,
        authority_id,
        *,
        execute=False,
        replace_target=False,
        expected_target_id=None,
    ):
        identifier(target_name)
        require(target_name != authority_name, "authority_cannot_be_restore_target")
        target = child(self.root, "deployments/" + target_name, exists=False)
        with self.exclusive():
            document, payload, (manifest, inventory, resources) = self.inspect_backup(
                backup_name, snapshot_sha256
            )
            authority, auth_marker, (auth_manifest, auth_inventory, auth_resources) = (
                self.deployment(
                    authority_name, authority=True, expected_id=authority_id
                )
            )
            require(
                auth_marker["deployment_id"] == document["source_deployment_id"],
                "unrelated_authority",
            )
            require(
                file_hash(child(authority, "release-manifest.json"))
                == document["release_manifest_sha256"],
                "release_version_mismatch",
            )
            require(
                file_hash(child(authority, "recovery-inventory.json"))
                == document["inventory_sha256"],
                "inventory_binding_mismatch",
            )
            if not execute:
                if target.exists():
                    require(
                        replace_target and expected_target_id is not None,
                        "explicit_replacement_required",
                    )
                    _, marker, _ = self.deployment(
                        target_name, expected_id=expected_target_id
                    )
                    require(
                        marker["role"] == "restored"
                        and marker["status"] == "restored_disabled",
                        "only_disabled_restore_replaceable",
                    )
                return {
                    "operation": "restore",
                    "mode": "plan",
                    "target": target_name,
                    "authority": "must_verify_current_state_on_execute",
                    "activation": "disabled",
                    "replace_target": replace_target,
                }
            # The backup's own guard can NEVER authorize itself. Full current state equality
            # is deliberately stricter than an undocumented product watermark interpretation.
            with freeze(authority, auth_inventory, auth_resources):
                self.inputs(authority, auth_manifest, auth_resources)
                require(
                    state_fingerprints(authority, auth_resources, self.control.check)
                    == document["state_fingerprints"],
                    "current_authority_diverged",
                )
                if target.exists():
                    require(
                        replace_target and expected_target_id is not None,
                        "explicit_replacement_required",
                    )
                    _, marker, _ = self.deployment(
                        target_name, expected_id=expected_target_id
                    )
                    require(
                        marker["role"] == "restored"
                        and marker["status"] == "restored_disabled",
                        "only_disabled_restore_replaceable",
                    )
                    files(
                        target, max_files=self.max_files
                    )  # Reject links before any directory move.
                else:
                    require(
                        not replace_target and expected_target_id is None,
                        "replacement_target_missing",
                    )
                quarantine = None
                with self.stage("deployments") as stage:
                    for entry in document["entries"]:
                        self.control.check()
                        copy_new(
                            child(payload, entry["path"]),
                            child(stage, entry["path"], exists=False),
                            limit=self.max_bytes,
                            check_cancel=self.control.check,
                        )
                    for item in resources.values():
                        if item["kind"] == "owner_lock":
                            write_new(child(stage, item["path"], exists=False), b"0")
                    for volume in manifest["volumes"]:
                        if volume["category"] == "logs":
                            child(stage, volume["host_path"], exists=False).mkdir(
                                parents=True, exist_ok=True
                            )
                    require(
                        state_fingerprints(stage, resources, self.control.check)
                        == document["state_fingerprints"],
                        "restored_state_mismatch",
                    )
                    verify_guards(stage, inventory, resources)
                    write_new(
                        stage / ".deployment.json",
                        canonical(
                            {
                                "scope_id": self.scope_id,
                                "deployment_id": str(uuid.uuid4()),
                                "environment": "synthetic",
                                "role": "restored",
                                "status": "restored_disabled",
                            }
                        ),
                    )
                    write_new(
                        stage / "RESTORE.json",
                        canonical(
                            {
                                "snapshot_sha256": snapshot_sha256,
                                "authority_deployment_id": authority_id,
                                "state": "restored_disabled",
                                "functional_acceptance": "not_run",
                                "activation": "disabled",
                            }
                        ),
                    )
                    self.control.check("before_publish")
                    sync_tree(stage)
                    # Check latest authority again immediately before publishing the target.
                    require(
                        state_fingerprints(
                            authority, auth_resources, self.control.check
                        )
                        == document["state_fingerprints"],
                        "current_authority_diverged",
                    )
                    try:
                        if target.exists():
                            quarantine = child(
                                self.root,
                                "quarantine/" + target_name + "-" + uuid.uuid4().hex,
                                exists=False,
                            )
                            target.rename(quarantine)
                            self.control.check("after_quarantine")
                        stage.rename(target)
                        sync_dir(target.parent)
                    except BaseException:
                        if quarantine is not None and not target.exists():
                            quarantine.rename(target)
                            sync_dir(target.parent)
                        raise
        return {
            "operation": "restore",
            "status": "restored_disabled",
            "target": target_name,
            "functional_acceptance": "not_run",
            "activation": "disabled",
            "previous_target_retained": quarantine is not None,
        }

    def verify_restored(self, target_name, authority_name, authority_id):
        with self.exclusive():
            target, marker, (_, inventory, resources) = self.deployment(target_name)
            require(marker["role"] == "restored", "restored_target_required")
            authority, _, (_, ai, ar) = self.deployment(
                authority_name, authority=True, expected_id=authority_id
            )
            require(
                file_hash(child(target, "release-manifest.json"))
                == file_hash(child(authority, "release-manifest.json")),
                "release_version_mismatch",
            )
            with freeze(authority, ai, ar), freeze(target, inventory, resources):
                require(
                    state_fingerprints(target, resources, self.control.check)
                    == state_fingerprints(authority, ar, self.control.check),
                    "current_authority_diverged",
                )
        return {
            "operation": "verify-restored",
            "status": "state_verified",
            "functional_acceptance": "external_runner_required",
            "activation": "disabled",
        }

    def select_code(
        self,
        source_name,
        candidate_path,
        compatibility_path,
        update_id,
        backup_name=None,
        *,
        rollback=False,
        execute=False,
    ):
        """Select an immutable release for the external packager; never run/migrate services."""
        identifier(update_id)
        candidate_path = safe_path(candidate_path)
        candidate_bytes = read_bytes(candidate_path, limit=4 * 1024 * 1024)
        candidate = release(read_json(candidate_path))
        compatibility = read_json(safe_path(compatibility_path))
        require(
            compatibility["candidate_manifest_sha256"] == digest(candidate_bytes),
            "compatibility_binding_mismatch",
        )
        require(
            compatibility["migration"] == "none", "migration_requires_separate_review"
        )
        for product in candidate["products"].values():
            require(
                product["image"]["verification"] == "verified"
                and product["image"]["digest"] is not None,
                "unverified_image",
            )
        with self.exclusive():
            source, marker, (current, inventory, resources) = self.deployment(
                source_name
            )
            require(
                compatibility["current_manifest_sha256"]
                == file_hash(child(source, "release-manifest.json")),
                "compatibility_binding_mismatch",
            )
            require(
                candidate["contracts"] == current["contracts"]
                and candidate["volumes"] == current["volumes"],
                "code_only_layout_mismatch",
            )
            event_path = child(self.root, "updates/" + update_id, exists=False)
            require(not event_path.exists(), "destination_exists")
            if not execute:
                return {
                    "operation": "rollback-code" if rollback else "prepare-update",
                    "mode": "plan",
                    "data_restore": False,
                    "migration": "none",
                    "backup_required": not rollback,
                    "schema_compatibility": "must_verify_on_execute",
                    "service_control": "external_not_run",
                }
            with freeze(source, inventory, resources):
                before = state_fingerprints(source, resources, self.control.check)
                schemas = {
                    name: facts["schema_sha256"]
                    for name, facts in before.items()
                    if "schema_sha256" in facts
                }
                require(
                    schemas == compatibility["read_write_schema_sha256"],
                    "schema_incompatible",
                )
            # _backup obtains all reservations itself; the scope lease remains held throughout.
            require(rollback or backup_name is not None, "pre_update_backup_required")
            receipt = None if rollback else self._backup(source_name, backup_name)
            pointer = source / "selected-code.json"
            previous = (
                read_bytes(pointer, limit=4 * 1024 * 1024) if pointer.exists() else None
            )
            with self.stage("updates") as stage:
                write_new(stage / "candidate-manifest.json", candidate_bytes)
                write_new(stage / "compatibility.json", canonical(compatibility))
                if previous is not None:
                    write_new(stage / "previous-selection.json", previous)
                write_new(
                    stage / "event.json",
                    canonical(
                        {
                            "operation": "rollback-code"
                            if rollback
                            else "prepare-update",
                            "deployment_id": marker["deployment_id"],
                            "backup": receipt,
                            "data_restore": False,
                            "activation": "disabled",
                            "state": "prepared",
                        }
                    ),
                )
                self.control.check("before_code_switch")
                stage.rename(event_path)
                temp = child(
                    source, ".code-" + uuid.uuid4().hex + ".pending", exists=False
                )
                write_new(
                    temp,
                    canonical(
                        {
                            "manifest_sha256": digest(candidate_bytes),
                            "release_id": candidate["release_id"],
                            "event": update_id,
                            "activation": "disabled",
                        }
                    ),
                )
                switched = False
                try:
                    os.replace(temp, safe_path(pointer, exists=False))
                    switched = True
                    sync_dir(source)
                    self.control.check("after_code_switch")
                    require(
                        before
                        == state_fingerprints(source, resources, self.control.check),
                        "unexpected_data_change",
                    )
                    write_new(
                        event_path / "COMMITTED.json",
                        canonical(
                            {"status": "selected_disabled", "activation": "disabled"}
                        ),
                    )
                except BaseException:
                    if switched:
                        if previous is not None:
                            recovery_temp = child(
                                source,
                                ".rollback-" + uuid.uuid4().hex + ".pending",
                                exists=False,
                            )
                            write_new(recovery_temp, previous)
                            os.replace(recovery_temp, pointer)
                        else:
                            # Move only the exact new pointer into its own event, never delete data.
                            pointer.rename(event_path / "failed-selection.json")
                        sync_dir(source)
                    write_new(
                        event_path / "ABORTED.json",
                        canonical({"status": "aborted", "activation": "disabled"}),
                    )
                    raise
        return {
            "operation": "rollback-code" if rollback else "prepare-update",
            "status": "selected_disabled",
            "release_id": candidate["release_id"],
            "backup": receipt,
            "data_restore": False,
            "service_control": "external_not_run",
            "activation": "disabled",
        }
