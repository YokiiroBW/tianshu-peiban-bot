"""Offline safety and original-helper checks; never connect to PostgreSQL."""

from contextlib import redirect_stdout
import importlib.util
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


spec = importlib.util.spec_from_file_location(
    "ts353_clone", Path(__file__).with_name("linux_clone.py")
)
clone = importlib.util.module_from_spec(spec)
spec.loader.exec_module(clone)


class CloneTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.identity = "a" * 32
        self.role = "ts325_role_" + self.identity
        self.database = "ts325_" + self.identity
        self.comment = "tianshu:ts325:" + "b" * 64 + ":pg2:" + self.role
        self.binding = {
            "purpose": "group-modes-isolated-pg2",
            "production": False,
            "isolation_id": self.identity,
            "postgresql_url": "postgresql://"
            + self.role
            + ":SYNTHETIC@relay/"
            + self.database
            + "?host=%2Frun%2Fowned-relay&port=25008",
        }
        self.resource = {
            "kind": "pg2",
            "isolation_id": self.identity,
            "role": self.role,
            "database": self.database,
            "comment": self.comment,
            "role_created": True,
            "database_created": True,
            "cleaned": False,
            "binding_file": "/a/copied/remote/path.json",
        }
        self.state = {
            "version": 1,
            "purpose": "isolated-group-modes-release-pg2",
            "status": "prepared_not_run",
            "token": "b" * 64,
            "admin_binding_file": str(self.root / "must-not-read-admin.json"),
            "resources": [self.resource],
        }
        self.binding_path, self.state_path = (
            self.root / "binding.json",
            self.root / "state.json",
        )
        self.save()

    def save(self):
        self.binding_path.write_text(json.dumps(self.binding))
        self.state_path.write_text(json.dumps(self.state))

    def test_owned_binding_does_not_follow_admin_and_allows_unix_query(self):
        # Missing admin file and a copied binding_file path are deliberate.
        self.assertEqual(
            clone.owned_binding(self.binding_path, self.state_path),
            (self.binding, self.resource),
        )

    def test_state_or_uri_cannot_target_other_identity_or_override_pg_user(self):
        for suffix in (
            "&user=postgres",
            "&dbname=production",
            "&options=-crole%3Dpostgres",
            "&host=second",
            "#fragment",
        ):
            with self.subTest(suffix=suffix):
                self.binding["postgresql_url"] += suffix
                self.save()
                with self.assertRaises(clone.CloneError):
                    clone.owned_binding(self.binding_path, self.state_path)
                self.binding["postgresql_url"] = self.binding["postgresql_url"][
                    : -len(suffix)
                ]
        self.resource["database"] = "production"
        self.save()
        with self.assertRaises(clone.CloneError):
            clone.owned_binding(self.binding_path, self.state_path)

    def test_refuse_cleaned_or_unowned_resources(self):
        for field, value in (
            ("cleaned", True),
            ("role_created", False),
            ("database_created", False),
            ("comment", "other"),
        ):
            old = self.resource[field]
            self.resource[field] = value
            self.save()
            with self.subTest(field=field), self.assertRaises(clone.CloneError):
                clone.owned_binding(self.binding_path, self.state_path)
            self.resource[field] = old

    def test_live_identity_checks_all_role_flags_and_comments(self):
        expected = [
            self.database,
            self.role,
            self.role,
            self.role,
            self.comment,
            False,
            False,
            False,
            False,
            False,
            True,
            self.comment,
        ]
        tools = SimpleNamespace(query=Mock(return_value=[expected]))
        clone.verify_identity(tools, {}, self.resource)
        for index in range(len(expected)):
            wrong = expected.copy()
            wrong[index] = (
                not wrong[index] if isinstance(wrong[index], bool) else "other"
            )
            tools.query.return_value = [wrong]
            with self.subTest(index=index), self.assertRaises(clone.CloneError):
                clone.verify_identity(tools, {}, self.resource)

    def test_relative_archive_and_guard_paths_cannot_escape(self):
        for name in ("../production", "/absolute", "nested/../../production"):
            with self.subTest(name=name), self.assertRaises(clone.CloneError):
                clone.relative(self.root, name)

    def test_stage_overlap_refused_before_clone_directory_creation(self):
        snapshot = self.root / "snapshot"
        authority = self.root / "production"
        mounted = self.root / "media"
        value = {
            "authority_root": str(authority),
            "owners": [{"mounts": [{"Source": str(mounted)}]}],
        }
        clone.validate_stage(self.root / "new-stage", snapshot, value)
        for stage in (
            self.root,
            snapshot,
            snapshot / "child",
            authority,
            authority / "child",
            mounted / "child",
        ):
            with self.subTest(stage=stage), self.assertRaises(clone.CloneError):
                clone.validate_stage(stage, snapshot, value)

    def snapshot_fixture(self):
        root = self.root / "snapshot"
        backup = root / "backup"
        backup.mkdir(parents=True)
        rows, inventory = [], {}
        for service, count in (
            ("platform", 17),
            ("gateway", 1),
            ("memory", 3),
            ("knowledge", 1),
            ("companion", 2),
            ("actor-memory", 1),
        ):
            marker = {
                "service": service,
                "backend": "postgresql",
                "state": "active",
                "databases": {
                    "domain_" + str(i): service.replace("-", "_") + "_" + str(i)
                    for i in range(count)
                },
                "installation_id": "f" * 32,
            }
            name = ".tianshu-storage-" + service + ".json"
            (backup / name).write_text(json.dumps(marker))
            archive = root / (service + ".dump")
            archive.write_bytes(b"synthetic archive " + service.encode())
            guards = []
            if service in {"memory", "knowledge", "actor-memory"}:
                guard = service + ".guard.json"
                (backup / guard).write_bytes(b"frozen guard")
                inventory[guard] = {"kind": "file", "sha256": clone.sha(backup / guard)}
                guards.append(guard)
            rows.append(
                {
                    "service": service,
                    "schemas": marker["databases"],
                    "binding": name,
                    "installation_id": marker["installation_id"],
                    "archive": archive.name,
                    "sha256": clone.sha(archive),
                    "bytes": archive.stat().st_size,
                    "native_restore_verified": True,
                    "memory_guards": guards,
                }
            )
        value = {
            "schema_version": "resident-cold-snapshot/1",
            "postgresql": rows,
            "inventory": {"entries": inventory},
        }
        (root / "manifest.json").write_text(json.dumps(value))
        receipt = {
            "status": "offline_restore_verified",
            "manifest_sha256": clone.sha(root / "manifest.json"),
            "restored_disabled": True,
            "authority_unchanged": True,
            "postgresql_databases_verified": 25,
            "postgresql_services_verified": 6,
            "sqlite_databases_verified": 39,
            "memory_guards_verified": 3,
        }
        (root / "receipt.json").write_text(json.dumps(receipt))
        return root

    def test_manifest_requires_exact_today_domains_and_archive_bytes(self):
        root = self.snapshot_fixture()
        _, value = clone.manifest(root)
        self.assertEqual(sum(len(row["schemas"]) for row in value["postgresql"]), 25)
        (root / "platform.dump").write_bytes(b"wrong")
        with self.assertRaises(clone.CloneError):
            clone.manifest(root)

    def test_old_twenty_four_domain_snapshot_is_not_accepted(self):
        root = self.snapshot_fixture()
        receipt = clone.read(root / "receipt.json")
        receipt["postgresql_databases_verified"] = 24
        (root / "receipt.json").write_text(json.dumps(receipt))
        with self.assertRaises(clone.CloneError):
            clone.manifest(root)

    def test_frozen_file_verification_allows_only_binding_epoch(self):
        source, target = self.root / "snapshot/backup", self.root / "clone/root"
        source.mkdir(parents=True)
        target.mkdir(parents=True)
        marker = {
            "version": 1,
            "namespace": "same",
            "installation_id": "c" * 32,
            "service": "platform",
            "epoch": "d" * 32,
            "databases": {"main": "schema"},
        }
        name = ".tianshu-storage-platform.json"
        (source / name).write_text(json.dumps(marker))
        (source / "frozen.guard").write_bytes(b"frozen bytes\n")
        (target / "frozen.guard").write_bytes(b"frozen bytes\n")
        marker["epoch"] = "e" * 32
        (target / name).write_text(json.dumps(marker))

        def inventory(root, check):
            entries = {
                ".": {"kind": "directory", "mode": 448, "uid": 10001, "gid": 10001}
            }
            for path in root.iterdir():
                entries[path.name] = {
                    "kind": "file",
                    "mode": 384,
                    "uid": 10001,
                    "gid": 10001,
                    "size": path.stat().st_size,
                    "sha256": clone.sha(path),
                }
            return {"entries": entries}

        ctx = SimpleNamespace(
            root=target,
            snapshot=source.parent,
            resident=SimpleNamespace(inventory=inventory),
            tools=SimpleNamespace(check=lambda: None),
            manifest={"inventory": inventory(source, None)},
        )
        _, proof = clone.verify_files(ctx)
        self.assertEqual(proof["binding_epochs_advanced"], 1)
        marker["installation_id"] = "f" * 32
        (target / name).write_text(json.dumps(marker))
        with self.assertRaises(clone.CloneError):
            clone.verify_files(ctx)
        marker["installation_id"] = "c" * 32
        (target / name).write_text(json.dumps(marker))
        (target / "frozen.guard").write_bytes(b"changed")
        with self.assertRaises(clone.CloneError):
            clone.verify_files(ctx)

    def fake_context(self, *, empty=True, fail_restore=False):
        snapshot = self.root / "snapshot"
        snapshot.mkdir()
        (snapshot / "manifest.json").write_text("{}")
        (self.root / "config").mkdir()
        (self.root / "config/storage-backup.json").write_text("{}")
        rows = []
        for index in range(6):
            (snapshot / str(index)).write_bytes(b"archive")
            rows.append({"archive": str(index)})
        tools = Mock()
        tools.query.return_value = [[0, 0]] if empty else [[1, 0]]
        if fail_restore:
            tools.run.side_effect = RuntimeError("private failure body")

        def copy(source, target):
            target.mkdir(parents=True)
            return {"status": "files_prepared_disabled"}

        return SimpleNamespace(
            stage=self.root,
            root=self.root / "clone/root",
            snapshot=snapshot,
            manifest={"postgresql": rows},
            binding=self.binding,
            resource=self.resource,
            binding_sha256="binding-hash",
            state_sha256="state-hash",
            tools=tools,
            readonly={},
            environment={},
            backend=SimpleNamespace(prepare_restored_files=Mock(side_effect=copy)),
        )

    def test_nonempty_pg_refused_before_copy_or_restore(self):
        ctx = self.fake_context(empty=False)
        with (
            patch.object(clone, "context", return_value=ctx),
            self.assertRaises(clone.CloneError),
        ):
            clone.prepare_clone(
                self.root,
                self.binding_path,
                self.state_path,
                snapshot=ctx.snapshot,
                recovery_bundle="unused",
            )
        ctx.backend.prepare_restored_files.assert_not_called()
        ctx.tools.run.assert_not_called()

    def test_restore_uses_only_owned_database_no_create_or_cleanup(self):
        ctx = self.fake_context()
        with (
            patch.object(clone, "context", return_value=ctx),
            patch.object(clone, "verify_identity") as identity,
            patch.object(clone, "verify_contents", return_value={"guards_verified": 3}),
        ):
            result = clone.prepare_clone(
                self.root,
                self.binding_path,
                self.state_path,
                snapshot=ctx.snapshot,
                recovery_bundle="unused",
            )
        self.assertEqual(identity.call_count, 6)
        self.assertEqual(ctx.tools.run.call_count, 6)
        for call in ctx.tools.run.call_args_list:
            self.assertEqual(call.args[0], "pg_restore")
            self.assertIn("--dbname=" + self.database, call.args[1])
            self.assertIn("--single-transaction", call.args[1])
            self.assertIn("--no-owner", call.args[1])
        ctx.tools.statement.assert_not_called()
        self.assertFalse(result["database_created_or_dropped"])
        self.assertNotIn("SYNTHETIC", json.dumps(result))

    def test_partial_restore_leaves_no_receipt_and_never_retries_or_cleans(self):
        ctx = self.fake_context(fail_restore=True)
        with (
            patch.object(clone, "context", return_value=ctx),
            patch.object(clone, "verify_identity"),
            self.assertRaises(RuntimeError),
        ):
            clone.prepare_clone(
                self.root,
                self.binding_path,
                self.state_path,
                snapshot=ctx.snapshot,
                recovery_bundle="unused",
            )
        self.assertEqual(ctx.tools.run.call_count, 1)
        self.assertFalse((self.root / "clone/receipt.json").exists())
        ctx.tools.statement.assert_not_called()

    def test_cli_never_prints_foreign_exception_credentials_or_rows(self):
        output = StringIO()
        arguments = ["prepare"]
        for option in ("stage", "binding", "state", "snapshot", "recovery-bundle"):
            arguments.extend(["--" + option, "explicit"])
        with (
            patch.object(
                clone, "prepare_clone", side_effect=RuntimeError("SYNTHETIC_SECRET_ROW")
            ),
            redirect_stdout(output),
        ):
            self.assertEqual(clone.main(arguments), 2)
        self.assertNotIn("SYNTHETIC_SECRET_ROW", output.getvalue())
        self.assertEqual(json.loads(output.getvalue())["status"], "refused")


if __name__ == "__main__":
    unittest.main()
