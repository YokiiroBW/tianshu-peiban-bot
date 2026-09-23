import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from fixtures import candidate, fixture, put

from ops.recovery.engine import Control, Recovery, create_sandbox
from ops.recovery.manifest import release
from ops.recovery.safety import RecoveryError, file_hash, files, relative
from ops.recovery.snapshot import fingerprint

HERE = Path(__file__).resolve().parent
WORKSPACE = HERE.parents[2]


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        runtime = HERE / ".runtime"
        runtime.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=runtime)
        self.base = Path(self.temporary.name).resolve()
        self.assertTrue(self.base.is_relative_to(runtime.resolve()))
        self.root = self.base / "sandbox"
        self.scope, self.authority = fixture(self.root)
        self.recovery = Recovery(self.root, self.scope)
        self.source = self.root / "deployments/source"

    def tearDown(self):
        # Resolve and contain the recursive TemporaryDirectory cleanup on Windows.
        self.assertTrue(
            self.base.resolve().is_relative_to((HERE / ".runtime").resolve())
        )
        self.temporary.cleanup()

    def backup(self, name="backup-one"):
        return self.recovery.backup("source", name, execute=True)["snapshot_sha256"]

    def restore(self, checksum, target="restored", **options):
        return self.recovery.restore(
            "backup-one",
            checksum,
            target,
            "source",
            self.authority,
            execute=True,
            **options,
        )

    def tree(self, root=None):
        root = root or self.root
        return {name: file_hash(root / name) for name in files(root)}

    def modify(self, product, sql, parameters=()):
        db = sqlite3.connect(self.source / f"data/{product}/main.db")
        try:
            db.execute(sql, parameters)
            db.commit()
        finally:
            db.close()

    def test_init_and_backup_default_plan_do_not_write(self):
        new_root = self.base / "not-created"
        result = create_sandbox(new_root, self.scope)
        self.assertEqual(result["mode"], "plan")
        self.assertFalse(new_root.exists())
        before = self.tree()
        result = self.recovery.backup("source", "backup-one")
        self.assertEqual(result["mode"], "plan")
        self.assertEqual(before, self.tree())

    def test_update_plan_keeps_wal_and_every_file_unchanged(self):
        manifest, compatibility = candidate(self.root)
        db = sqlite3.connect(self.source / "data/gateway/main.db")
        try:
            db.execute("INSERT INTO facts VALUES('wal-marker','pending-checkpoint')")
            db.commit()
            before = self.tree()
            result = self.recovery.select_code(
                "source", manifest, compatibility, "plan-update", "plan-backup"
            )
            self.assertEqual(result["mode"], "plan")
            self.assertEqual(before, self.tree())
        finally:
            db.close()

    def test_changed_implicit_rowid_cannot_bypass_current_authority(self):
        checksum = self.backup()
        self.modify("platform", "UPDATE receipts SET rowid=42 WHERE id='unknown-job'")
        with self.assertRaisesRegex(RecoveryError, "current_authority_diverged"):
            self.restore(checksum)

    def test_rowid_case_shadowing_blocks_complete_restore_after_identity_change(self):
        cases = (
            (("_ROWID_",), "rowid"),
            (("_RoWiD_", "RoWiD"), "oid"),
            (("_rowid_", "rOwId"), "oid"),
            (("rOwId", "OiD"), "_rowid_"),
        )
        database = self.source / "data/platform/main.db"
        for index, (shadowed, identity) in enumerate(cases):
            with self.subTest(shadowed=shadowed):
                table = f"audit_probe_{index}"
                declaration = ",".join(f'"{name}" TEXT' for name in shadowed)
                with closing(sqlite3.connect(database)) as db:
                    db.execute(f"CREATE TABLE {table} ({declaration}, value TEXT)")
                    db.execute(
                        f"INSERT INTO {table}({identity},value) VALUES(1,'same')"
                    )
                    db.commit()
                    before = db.execute(f"SELECT * FROM {table}").fetchall()
                backup_name = f"case-shadow-{index}"
                checksum = self.backup(backup_name)
                self.modify("platform", f"UPDATE {table} SET {identity}=2")
                with closing(sqlite3.connect(database)) as db:
                    self.assertEqual(
                        db.execute(f"SELECT {identity} FROM {table}").fetchone(), (2,)
                    )
                    self.assertEqual(
                        db.execute(f"SELECT * FROM {table}").fetchall(), before
                    )
                target = f"case-target-{index}"
                with self.assertRaisesRegex(
                    RecoveryError, "current_authority_diverged"
                ):
                    self.recovery.restore(
                        backup_name,
                        checksum,
                        target,
                        "source",
                        self.authority,
                        execute=True,
                    )
                self.assertFalse((self.root / "deployments" / target).exists())

    def test_rowid_all_aliases_shadowed_without_identity_proof_rejects_backup(self):
        cases = (
            '"_rowid_" TEXT, "rowid" TEXT, "oid" TEXT, value TEXT',
            '"_RoWiD_" TEXT, "RoWiD" TEXT, "OiD" TEXT, value TEXT',
            'id INT PRIMARY KEY, "_RoWiD_" TEXT, "RoWiD" TEXT, "OiD" TEXT, value TEXT',
            'id INTEGER PRIMARY KEY DESC, "_RoWiD_" TEXT, "RoWiD" TEXT, "OiD" TEXT, value TEXT',
            'id INTEGER, "_RoWiD_" TEXT, "RoWiD" TEXT, "OiD" TEXT, value TEXT, PRIMARY KEY(id,value)',
        )
        for index, declaration in enumerate(cases):
            with self.subTest(declaration=declaration):
                self.modify("platform", f"CREATE TABLE audit_probe ({declaration})")
                try:
                    self.modify(
                        "platform", "INSERT INTO audit_probe(value) VALUES ('same')"
                    )
                    backup_name = f"unproven-identity-{index}"
                    with self.assertRaisesRegex(
                        RecoveryError, "sqlite_row_identity_unavailable"
                    ):
                        self.backup(backup_name)
                    self.assertFalse((self.root / "backups" / backup_name).exists())
                finally:
                    self.modify("platform", "DROP TABLE audit_probe")

    def test_rowid_without_rowid_controls_restore_then_reject_changed_primary_key(self):
        cases = (
            "id TEXT PRIMARY KEY, value TEXT",
            'id TEXT PRIMARY KEY, "_RoWiD_" TEXT, "RoWiD" TEXT, "OiD" TEXT, value TEXT',
        )
        for index, declaration in enumerate(cases):
            with self.subTest(declaration=declaration):
                table = f"without_rowid_probe_{index}"
                self.modify(
                    "platform", f"CREATE TABLE {table} ({declaration}) WITHOUT ROWID"
                )
                self.modify(
                    "platform", f"INSERT INTO {table}(id,value) VALUES ('one','same')"
                )
                backup_name = f"without-rowid-{index}"
                checksum = self.backup(backup_name)
                result = self.recovery.restore(
                    backup_name,
                    checksum,
                    f"without-rowid-ok-{index}",
                    "source",
                    self.authority,
                    execute=True,
                )
                self.assertEqual(result["status"], "restored_disabled")
                self.modify("platform", f"UPDATE {table} SET id='two'")
                with self.assertRaisesRegex(
                    RecoveryError, "current_authority_diverged"
                ):
                    self.recovery.restore(
                        backup_name,
                        checksum,
                        f"without-rowid-denied-{index}",
                        "source",
                        self.authority,
                        execute=True,
                    )

    def test_rowid_integer_primary_key_controls_with_all_aliases_shadowed(self):
        cases = (
            ("id INTEGER PRIMARY KEY, value TEXT", "id"),
            (
                'id INTEGER PRIMARY KEY, "_RoWiD_" TEXT, "RoWiD" TEXT, "OiD" TEXT, value TEXT',
                "id",
            ),
            (
                'id INTEGER, "_RoWiD_" TEXT, "RoWiD" TEXT, "OiD" TEXT, value TEXT, PRIMARY KEY(id DESC)',
                "id",
            ),
            (
                '"_RoWiD_" INTEGER PRIMARY KEY, "RoWiD" TEXT, "OiD" TEXT, value TEXT',
                '"_RoWiD_"',
            ),
        )
        for index, (declaration, identity) in enumerate(cases):
            with self.subTest(declaration=declaration):
                table = f"integer_primary_key_probe_{index}"
                self.modify("platform", f"CREATE TABLE {table} ({declaration})")
                self.modify(
                    "platform",
                    f"INSERT INTO {table}({identity},value) VALUES (1,'same')",
                )
                backup_name = f"integer-pk-{index}"
                checksum = self.backup(backup_name)
                result = self.recovery.restore(
                    backup_name,
                    checksum,
                    f"integer-pk-ok-{index}",
                    "source",
                    self.authority,
                    execute=True,
                )
                self.assertEqual(result["status"], "restored_disabled")
                self.modify("platform", f"UPDATE {table} SET {identity}=2")
                with self.assertRaisesRegex(
                    RecoveryError, "current_authority_diverged"
                ):
                    self.recovery.restore(
                        backup_name,
                        checksum,
                        f"integer-pk-denied-{index}",
                        "source",
                        self.authority,
                        execute=True,
                    )

    def test_sqlite_cannot_be_misclassified_as_raw_sidecar(self):
        inventory_path = self.source / "recovery-inventory.json"
        document = json.loads(inventory_path.read_bytes())
        next(
            item for item in document["resources"] if item["id"] == "platform-sidecar"
        )["kind"] = "file"
        put(inventory_path, document)
        with self.assertRaisesRegex(RecoveryError, "sqlite_must_use_backup_api"):
            self.backup()

    def test_unknown_secret_field_is_not_copied_into_backup(self):
        document = json.loads((self.source / "release-manifest.json").read_bytes())
        document["secret"] = "must-not-copy"
        with self.assertRaisesRegex(RecoveryError, "unexpected_or_missing_fields"):
            release(document)

    def test_base64_padded_filename_round_trip(self):
        name = "logs/platform/chunks/MWEwY2Y3MTAyN2Y6+Zg=="
        path = self.source / name
        path.parent.mkdir()
        path.write_bytes(b"synthetic opaque chunk")
        checksum = self.backup()
        self.restore(checksum)
        self.assertEqual(
            (self.root / "deployments/restored" / name).read_bytes(), path.read_bytes()
        )

    def test_full_snapshot_wal_sidecar_guard_contract_and_restore(self):
        # An idle SQLite connection leaves committed data only in WAL. No fixture owner runs.
        db = sqlite3.connect(self.source / "data/companion/main.db")
        try:
            db.execute("PRAGMA wal_autocheckpoint=0")
            db.execute("INSERT INTO facts VALUES('wal-only','committed')")
            db.commit()
            self.assertGreater(
                Path(str(self.source / "data/companion/main.db") + "-wal")
                .stat()
                .st_size,
                0,
            )
            checksum = self.backup()
            payload = self.root / "backups/backup-one/payload"
            with closing(
                sqlite3.connect(payload / "data/companion/main.db")
            ) as restored:
                self.assertEqual(
                    restored.execute(
                        "SELECT value FROM facts WHERE id='wal-only'"
                    ).fetchone(),
                    ("committed",),
                )
            self.assertFalse(list(payload.rglob("*-wal")))
            self.assertFalse(list(payload.rglob("*-shm")))
            self.assertTrue(
                (payload / "data/platform/main.db.web-inputs.sqlite").exists()
            )
            self.assertEqual(
                (payload / "contracts/synthetic/v1/README.md").read_bytes(),
                b"Synthetic contract original CRLF bytes.\r\n",
            )
            result = self.restore(checksum)
            self.assertEqual(result["status"], "restored_disabled")
            self.assertEqual(
                self.recovery.verify_restored("restored", "source", self.authority)[
                    "status"
                ],
                "state_verified",
            )
        finally:
            db.close()

    def test_restore_plan_keeps_all_files_unchanged(self):
        checksum = self.backup()
        before = self.tree()
        result = self.recovery.restore(
            "backup-one", checksum, "restored", "source", self.authority
        )
        self.assertEqual(result["mode"], "plan")
        self.assertEqual(before, self.tree())

    def test_partial_copy_failure_never_publishes(self):
        for index, point in enumerate(
            ("before_snapshot", "after_first_file", "before_publish")
        ):
            with self.subTest(point=point):
                recovery = Recovery(
                    self.root, self.scope, control=Control(fail_at=point)
                )
                with self.assertRaisesRegex(RecoveryError, "injected_failure"):
                    recovery.backup("source", f"failed-{index}", execute=True)
                self.assertFalse((self.root / f"backups/failed-{index}").exists())
        self.assertEqual(
            self.recovery.backup("source", "retry-ok", execute=True)["status"],
            "complete",
        )

    def test_cancellation_mid_snapshot_releases_locks(self):
        calls = 0

        def cancelled():
            nonlocal calls
            calls += 1
            return calls > 4

        recovery = Recovery(self.root, self.scope, control=Control(cancel=cancelled))
        with self.assertRaisesRegex(RecoveryError, "cancelled"):
            recovery.backup("source", "cancelled", execute=True)
        self.assertFalse((self.root / "backups/cancelled").exists())
        self.backup()

    def test_damaged_payload_and_changed_index_rejected(self):
        checksum = self.backup()
        payload = self.root / "backups/backup-one/payload/data/memory/main.db"
        original = payload.read_bytes()
        payload.write_bytes(original + b"damage")
        with self.assertRaisesRegex(RecoveryError, "payload_digest_mismatch"):
            self.restore(checksum)
        self.assertFalse((self.root / "deployments/restored").exists())
        payload.write_bytes(original)
        index = self.root / "backups/backup-one/snapshot.json"
        index.write_bytes(index.read_bytes() + b" ")
        with self.assertRaisesRegex(RecoveryError, "snapshot_digest_mismatch"):
            self.restore(checksum)

    def test_snapshot_traversal_and_duplicate_entries_rejected_even_with_digest(self):
        self.backup()
        index = self.root / "backups/backup-one/snapshot.json"
        document = json.loads(index.read_bytes())
        document["entries"][0]["path"] = "../../outside"
        put(index, document)
        with self.assertRaises(RecoveryError):
            self.restore(file_hash(index))
        self.assertFalse((self.base / "outside").exists())

    def test_extra_package_file_rejected(self):
        checksum = self.backup()
        (self.root / "backups/backup-one/extra").write_bytes(b"canary")
        with self.assertRaisesRegex(RecoveryError, "unexpected_package_file"):
            self.restore(checksum)

    def test_missing_sidecar_and_undeclared_file_rejected(self):
        extra = self.source / "data/platform/unregistered.sqlite"
        extra.write_bytes(b"not-approved")
        with self.assertRaisesRegex(RecoveryError, "unregistered_state_file"):
            self.backup()
        extra.rename(self.source / "unregistered-retained.sqlite")
        sidecar = self.source / "data/platform/main.db.web-inputs.sqlite"
        sidecar.rename(self.source / "retained.sqlite")
        with self.assertRaises(RecoveryError):
            self.backup()

    def test_memory_guard_ahead_of_db_fails_closed(self):
        guard = self.source / "data/memory/main.db.source-guard.json"
        value = json.loads(guard.read_bytes())
        value["revision"] = 8
        put(guard, value)
        with self.assertRaisesRegex(RecoveryError, "guard_database_mismatch"):
            self.backup()

    def test_old_backup_cannot_revive_forgotten_record_with_old_guard(self):
        checksum = self.backup()
        self.modify("memory", "UPDATE facts SET value='forgotten' WHERE id='visible'")
        self.modify(
            "memory", "UPDATE metadata SET value='8' WHERE key='source_revision'"
        )
        guard = self.source / "data/memory/main.db.source-guard.json"
        value = json.loads(guard.read_bytes())
        value["revision"] = 8
        put(guard, value)
        # The backup's own old database and old guard still agree; independent current state wins.
        self.assertEqual(
            self.recovery.verify("backup-one", checksum)["status"], "integrity_verified"
        )
        with self.assertRaisesRegex(RecoveryError, "current_authority_diverged"):
            self.restore(checksum)
        self.assertFalse((self.root / "deployments/restored").exists())

    def test_platform_companion_gateway_new_facts_each_block_old_restore(self):
        checksum = self.backup()
        for product in ("platform", "companion", "gateway"):
            with self.subTest(product=product):
                self.modify(product, "UPDATE facts SET value='4' WHERE id='sequence'")
                with self.assertRaisesRegex(
                    RecoveryError, "current_authority_diverged"
                ):
                    self.restore(checksum)
                self.modify(product, "UPDATE facts SET value='3' WHERE id='sequence'")

    def test_no_authority_or_wrong_identity_rejected(self):
        checksum = self.backup()
        with self.assertRaisesRegex(RecoveryError, "deployment_identity_mismatch"):
            self.recovery.restore(
                "backup-one", checksum, "restored", "source", "wrong", execute=True
            )
        with self.assertRaisesRegex(RecoveryError, "path_missing"):
            self.recovery.restore(
                "backup-one",
                checksum,
                "restored",
                "missing",
                self.authority,
                execute=True,
            )
        with self.assertRaisesRegex(
            RecoveryError, "authority_cannot_be_restore_target"
        ):
            self.restore(
                checksum,
                "source",
                replace_target=True,
                expected_target_id=self.authority,
            )

    def test_unrelated_or_restored_authority_never_trusted(self):
        checksum = self.backup()
        self.restore(checksum)
        marker = json.loads(
            (self.root / "deployments/restored/.deployment.json").read_bytes()
        )
        with self.assertRaisesRegex(RecoveryError, "current_authority_required"):
            self.recovery.restore(
                "backup-one",
                checksum,
                "another",
                "restored",
                marker["deployment_id"],
                execute=True,
            )

    def test_restore_failure_does_not_publish_target(self):
        checksum = self.backup()
        recovery = Recovery(
            self.root, self.scope, control=Control(fail_at="before_publish")
        )
        with self.assertRaisesRegex(RecoveryError, "injected_failure"):
            recovery.restore(
                "backup-one",
                checksum,
                "restored",
                "source",
                self.authority,
                execute=True,
            )
        self.assertFalse((self.root / "deployments/restored").exists())

    def test_replacement_requires_identity_and_retains_previous_target(self):
        checksum = self.backup()
        self.restore(checksum)
        marker = json.loads(
            (self.root / "deployments/restored/.deployment.json").read_bytes()
        )
        with self.assertRaisesRegex(RecoveryError, "explicit_replacement_required"):
            self.restore(checksum)
        result = self.restore(
            checksum, replace_target=True, expected_target_id=marker["deployment_id"]
        )
        self.assertTrue(result["previous_target_retained"])
        self.assertEqual(len(list((self.root / "quarantine").iterdir())), 1)

    def test_failure_after_quarantine_rolls_back_only_directory_switch(self):
        checksum = self.backup()
        self.restore(checksum)
        target = self.root / "deployments/restored"
        marker = json.loads((target / ".deployment.json").read_bytes())
        before = self.tree(target)
        recovery = Recovery(
            self.root, self.scope, control=Control(fail_at="after_quarantine")
        )
        with self.assertRaisesRegex(RecoveryError, "injected_failure"):
            recovery.restore(
                "backup-one",
                checksum,
                "restored",
                "source",
                self.authority,
                execute=True,
                replace_target=True,
                expected_target_id=marker["deployment_id"],
            )
        self.assertEqual(self.tree(target), before)

    def test_code_update_backs_up_first_and_never_changes_data(self):
        manifest, compatibility = candidate(self.root)
        before = self.tree(self.source / "data")
        result = self.recovery.select_code(
            "source", manifest, compatibility, "update-one", "pre-update", execute=True
        )
        self.assertEqual(result["backup"]["status"], "complete")
        self.assertEqual(before, self.tree(self.source / "data"))
        self.assertEqual(
            json.loads((self.source / "selected-code.json").read_bytes())["activation"],
            "disabled",
        )
        result = self.recovery.select_code(
            "source",
            manifest,
            compatibility,
            "rollback-one",
            rollback=True,
            execute=True,
        )
        self.assertFalse(result["data_restore"])
        self.assertEqual(before, self.tree(self.source / "data"))
        self.assertEqual(
            len(
                [
                    p
                    for p in (self.root / "backups").iterdir()
                    if not p.name.startswith(".")
                ]
            ),
            1,
        )

    def test_backup_failure_prevents_code_switch(self):
        manifest, compatibility = candidate(self.root)
        recovery = Recovery(
            self.root, self.scope, control=Control(fail_at="after_first_file")
        )
        with self.assertRaisesRegex(RecoveryError, "injected_failure"):
            recovery.select_code(
                "source",
                manifest,
                compatibility,
                "failed-update",
                "pre-update",
                execute=True,
            )
        self.assertFalse((self.source / "selected-code.json").exists())

    def test_code_switch_failure_recovers_previous_selection_without_data_restore(self):
        manifest, compatibility = candidate(self.root)
        self.recovery.select_code(
            "source", manifest, compatibility, "update-one", "pre-update", execute=True
        )
        previous = (self.source / "selected-code.json").read_bytes()
        recovery = Recovery(
            self.root, self.scope, control=Control(fail_at="after_code_switch")
        )
        with self.assertRaisesRegex(RecoveryError, "injected_failure"):
            recovery.select_code(
                "source",
                manifest,
                compatibility,
                "failed-update",
                "pre-failed",
                execute=True,
            )
        self.assertEqual((self.source / "selected-code.json").read_bytes(), previous)

    def test_incompatible_or_unverified_code_rejected(self):
        manifest, compatibility = candidate(self.root)
        document = json.loads(compatibility.read_bytes())
        document["read_write_schema_sha256"] = {}
        put(compatibility, document)
        with self.assertRaisesRegex(RecoveryError, "schema_incompatible"):
            self.recovery.select_code(
                "source",
                manifest,
                compatibility,
                "update-one",
                "pre-update",
                execute=True,
            )
        manifest, compatibility = candidate(self.root, "synthetic-v3")
        document = json.loads(manifest.read_bytes())
        document["products"]["memory"]["image"]["verification"] = "unverified"
        put(manifest, document)
        ref = json.loads(compatibility.read_bytes())
        ref["candidate_manifest_sha256"] = file_hash(manifest)
        put(compatibility, ref)
        with self.assertRaisesRegex(RecoveryError, "unverified_image"):
            self.recovery.select_code(
                "source",
                manifest,
                compatibility,
                "update-two",
                "pre-update",
                execute=True,
            )

    def test_path_escape_drive_unc_reserved_and_wrong_scope(self):
        for name in (
            "../outside",
            "/etc",
            "C:/other",
            "\\\\host\\share",
            "a/../b",
            "NUL",
            "a:stream",
            "a\\b",
            "a//b",
            "a/.",
        ):
            with self.subTest(name=name), self.assertRaises(RecoveryError):
                relative(name)
        with self.assertRaisesRegex(RecoveryError, "untrusted_synthetic_scope"):
            Recovery(self.root, "wrong-scope")

    def test_hardlink_rejected_before_copy(self):
        original = self.base / "unrelated"
        original.write_bytes(b"outside-canary")
        os.link(original, self.source / "logs/platform/alias.jsonl")
        with self.assertRaisesRegex(RecoveryError, "hardlinked_file"):
            self.backup()
        self.assertEqual(original.read_bytes(), b"outside-canary")

    def test_symlink_or_windows_junction_rejected(self):
        outside = self.base / "unrelated"
        outside.mkdir()
        alias = self.source / "logs/platform/alias"
        if os.name == "nt":
            # One fixed, local junction created for the negative test. No shell deletion/move.
            result = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(alias), str(outside)],
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0)
        else:
            alias.symlink_to(outside, target_is_directory=True)
        try:
            with self.assertRaisesRegex(RecoveryError, "linked_path"):
                self.backup()
        finally:
            if os.name == "nt":
                alias.rmdir()  # Removes only the junction itself, never recursively.
            else:
                alias.unlink()

    def test_duplicate_json_and_cross_product_volume_overlap(self):
        path = self.source / "recovery-inventory.json"
        path.write_text(
            '{"schema_version":"1.0.0","schema_version":"1.0.0"}', encoding="utf-8"
        )
        with self.assertRaisesRegex(RecoveryError, "duplicate_json_key"):
            self.backup()
        document = json.loads((self.source / "release-manifest.json").read_bytes())
        document["volumes"][2]["host_path"] = document["volumes"][0]["host_path"]
        with self.assertRaisesRegex(RecoveryError, "cross_product_volume_overlap"):
            release(document)

    def test_limits_and_broken_sqlite(self):
        recovery = Recovery(self.root, self.scope, max_bytes=16)
        with self.assertRaisesRegex(RecoveryError, "total_size_limit"):
            recovery.backup("source", "too-large", execute=True)
        (self.source / "data/gateway/main.db").write_bytes(b"invalid sqlite fixture")
        with self.assertRaises(sqlite3.Error):
            self.backup()

    def test_sqlite_active_writer_blocks_snapshot(self):
        db = sqlite3.connect(self.source / "data/gateway/main.db")
        db.execute("BEGIN IMMEDIATE")
        try:
            with self.assertRaises(sqlite3.OperationalError):
                self.backup()
            self.assertFalse((self.root / "backups/backup-one").exists())
        finally:
            db.rollback()
            db.close()

    def test_verified_release_is_explicitly_unsupported_until_evidence_adapter(self):
        document = json.loads((self.source / "release-manifest.json").read_bytes())
        document["status"] = "verified"
        with self.assertRaisesRegex(
            RecoveryError, "verified_release_evidence_adapter_required"
        ):
            release(document)

    def test_cli_plan_and_error_redaction(self):
        command = [
            sys.executable,
            "-B",
            "-m",
            "ops.recovery",
            "--root",
            str(self.root),
            "--scope-id",
            self.scope,
        ]
        result = subprocess.run(
            command + ["backup", "--deployment", "source", "--backup", "cli-backup"],
            cwd=WORKSPACE,
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["mode"], "plan")
        self.assertFalse((self.root / "backups/cli-backup").exists())
        result = subprocess.run(
            command
            + [
                "backup",
                "--deployment",
                "secret-canary/../../escape",
                "--backup",
                "cli-backup",
            ],
            cwd=WORKSPACE,
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("secret-canary", result.stdout + result.stderr)

    def test_restored_synthetic_app_functionality_and_restart(self):
        checksum = self.backup()
        self.restore(checksum)
        target = self.root / "deployments/restored"
        before = fingerprint(target / "data/gateway/main.db")
        for restart in range(2):
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-B",
                    str(HERE / "fixture_app.py"),
                    "--root",
                    str(self.root),
                    "--scope-id",
                    self.scope,
                    "--deployment",
                    "restored",
                ],
                cwd=WORKSPACE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                startup = json.loads(process.stdout.readline())
                url = "http://127.0.0.1:" + str(startup["port"])
                with urlopen(url + "/recall", timeout=3) as response:
                    self.assertEqual(
                        json.load(response)["results"], ["synthetic-answer"]
                    )
                with self.assertRaises(HTTPError) as denied:
                    urlopen(url + "/model", timeout=3)
                self.assertEqual(denied.exception.code, 403)
                denied.exception.close()
                with urlopen(url + "/receipt", timeout=3) as response:
                    self.assertEqual(json.load(response)["status"], "accepted")
                with self.assertRaises(HTTPError) as unknown:
                    urlopen(Request(url + "/retry-unknown", method="POST"), timeout=3)
                self.assertEqual(unknown.exception.code, 409)
                self.assertFalse(json.load(unknown.exception)["resent"])
                unknown.exception.close()
                with self.assertRaisesRegex(RecoveryError, "deployment_busy"):
                    self.recovery.backup("source", "while-running", execute=True)
            finally:
                process.terminate()
                process.communicate(timeout=5)
        self.assertEqual(before, fingerprint(target / "data/gateway/main.db"))


if __name__ == "__main__":
    unittest.main()
