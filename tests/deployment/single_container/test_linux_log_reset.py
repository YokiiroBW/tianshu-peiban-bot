"""Only synthetic temporary roots and Docker metadata; no real Docker or data."""

from copy import deepcopy
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import time
import sys
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location(
    "log_reset", Path(__file__).with_name("linux_log_reset.py")
)
tool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tool)


class LogResetTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        buffer_index = list(tool.ROOTS).index(tool.VECTOR_BUFFER_ROOT)
        self.vector_buffer = str(self.base / "logs" / str(buffer_index))
        self.roots = {
            str(self.base / "logs" / str(index)): service
            for index, service in enumerate(tool.ROOTS.values())
        }
        self.addCleanup(patch.stopall)
        patch.object(tool, "ROOTS", self.roots).start()
        patch.object(tool, "VECTOR_BUFFER_ROOT", self.vector_buffer).start()
        patch.object(tool, "mountpoints", return_value=set()).start()
        self.finished = datetime.fromtimestamp(
            time.time() - 60, timezone.utc
        ).isoformat()
        self.owners = {}
        for index, name in enumerate(sorted(tool.CORE_OWNERS | tool.AUX_OWNERS)):
            self.owners[name] = {
                "Id": format(index + 1, "064x"),
                "Image": "sha256:" + "a" * 64,
                "Mounts": [],
                "Config": {
                    "Labels": {
                        "com.docker.compose.project": "synthetic-cold",
                        "com.docker.compose.service": name,
                    }
                },
                "State": {
                    "Running": False,
                    "Status": "exited",
                    "ExitCode": 0,
                    "FinishedAt": self.finished,
                },
                "HostConfig": {"RestartPolicy": {"Name": "no"}},
            }
        for text, service in self.roots.items():
            root = Path(text)
            root.mkdir(parents=True)
            root.chmod(0o750)
            (root / "old.jsonl").write_bytes(b"old")
            (root / "nested").mkdir()
            (root / "nested" / "buffer").write_bytes(b"queue")
            readers = (
                ("obs-guard", "obs-vector") if service in tool.PRODUCERS else (service,)
            )
            for reader in readers:
                self.owners[reader]["Mounts"].append(
                    {
                        "Type": "bind",
                        "Source": text,
                        "Destination": "/var/lib/vector/buffer"
                        if text == self.vector_buffer
                        else "/synthetic/" + root.name,
                        "RW": True,
                    }
                )
        self.observed = deepcopy(self.owners)
        self.stage = self.base / "cold-stage"
        operation = self.stage / "cold-snapshot"
        operation.mkdir(parents=True)
        self.manifest = operation / "manifest.json"
        self.manifest.write_text(
            json.dumps(
                {
                    "schema_version": "resident-cold-snapshot/1",
                    "owners": [
                        {
                            "service": name,
                            "id": row["Id"],
                            "image_id": row["Image"],
                            "project": "synthetic-cold",
                            "mounts": row["Mounts"],
                        }
                        for name, row in self.owners.items()
                        if name in tool.CORE_OWNERS
                    ],
                }
            )
        )
        report = {
            "status": "offline_restore_verified",
            "restored_disabled": True,
            "authority_unchanged": True,
            "manifest_sha256": tool.digest(self.manifest),
        }
        (operation / "receipt.json").write_text(json.dumps(report))
        self.backup = {
            "status": "cold_backup_verified",
            "operation": str(operation),
            "receipt": report,
        }
        auxiliary = self.stage / "auxiliary-manifest.json"
        auxiliary.write_text('{"synthetic_backup":true}')
        self.auxiliary = {
            "status": "auxiliary_backup_verified",
            "coverage": sorted(tool.AUX_COVERAGE),
            "cold_manifest_sha256": report["manifest_sha256"],
            "manifest_path": str(auxiliary),
            "manifest_sha256": tool.digest(auxiliary),
        }
        self.plan = {
            "roots": [
                {"source": root, "service": service}
                for root, service in self.roots.items()
            ]
        }
        self.outside = self.base / "business-data"
        self.outside.mkdir()
        (self.outside / "keep").write_bytes(b"business-must-remain")

    def reset(self, **kwargs):
        return tool.reset(
            self.stage,
            self.owners,
            self.backup,
            self.plan,
            aux_backup_receipt=self.auxiliary,
            inspect=kwargs.get("inspect", lambda _: list(self.observed.values())),
        )

    def assert_untouched(self):
        for root in self.roots:
            self.assertEqual((Path(root) / "old.jsonl").read_bytes(), b"old")
        self.assertEqual((self.outside / "keep").read_bytes(), b"business-must-remain")

    def test_recursive_clear_preserves_root_uid_gid_mode_and_business_files(self):
        before = {root: tool.metadata(Path(root)) for root in self.roots}
        result = self.reset()
        self.assertEqual(result["status"], "runtime_logs_reset")
        self.assertEqual(len(result["roots"]), 10)
        self.assertEqual(sum(row["deleted"]["files"] for row in result["roots"]), 20)
        self.assertEqual(sum(row["deleted"]["bytes"] for row in result["roots"]), 80)
        for root in self.roots:
            self.assertEqual(list(Path(root).iterdir()), [])
            self.assertEqual(tool.metadata(Path(root)), before[root])
        self.assertEqual((self.outside / "keep").read_bytes(), b"business-must-remain")
        with self.assertRaisesRegex(RuntimeError, "new_log_reset_required"):
            self.reset()

    def test_root_escape_and_changed_owner_or_non_stopped_states_refused_before_delete(
        self,
    ):
        valid = deepcopy(self.observed)
        for field, value in (
            ("Running", True),
            ("ExitCode", 1),
            ("OOMKilled", True),
            ("Status", "created"),
        ):
            self.observed = deepcopy(valid)
            self.observed["inputs"]["State"][field] = value
            with self.subTest(field=field), self.assertRaises(RuntimeError):
                self.reset()
            self.assert_untouched()
        for field, value in (
            ("Id", "f" * 64),
            ("Image", "sha256:" + "f" * 64),
            ("Mounts", []),
        ):
            self.observed = deepcopy(valid)
            self.observed["obs-guard"][field] = value
            with self.subTest(field=field), self.assertRaises(RuntimeError):
                self.reset()
            self.assert_untouched()
        self.observed = valid
        self.observed["onebot"]["HostConfig"]["RestartPolicy"]["Name"] = "always"
        with self.assertRaises(RuntimeError):
            self.reset()
        self.observed = deepcopy(self.owners)
        self.plan["roots"][0]["source"] = str(self.outside)
        with self.assertRaisesRegex(RuntimeError, "exact_ten_log_roots_required"):
            self.reset()
        self.assert_untouched()

    def test_fresh_cold_hash_and_all_auxiliary_coverage_are_required(self):
        self.auxiliary["coverage"].pop()
        with self.assertRaisesRegex(
            RuntimeError, "same_epoch_verified_auxiliary_backup_required"
        ):
            self.reset()
        self.auxiliary["coverage"] = sorted(tool.AUX_COVERAGE)
        os.utime(self.manifest, (time.time() - 120, time.time() - 120))
        with self.assertRaisesRegex(RuntimeError, "cold_backup_predates_current_stop"):
            self.reset()
        os.utime(self.manifest, None)
        self.manifest.write_text("{}")
        with self.assertRaisesRegex(
            RuntimeError, "fresh_verified_cold_backup_required"
        ):
            self.reset()
        self.assert_untouched()

    def test_nested_mount_and_permission_failure_preflight_all_ten(self):
        root = Path(list(self.roots)[-1])
        with patch.object(tool, "mountpoints", return_value={root, root / "nested"}):
            with self.assertRaisesRegex(RuntimeError, "nested_log_mount_refused"):
                self.reset()
        with patch.object(
            tool.os, "access", side_effect=lambda path, _: Path(path) != root
        ):
            with self.assertRaisesRegex(
                RuntimeError, "log_directory_permission_denied"
            ):
                self.reset()
        self.assert_untouched()

    def test_log_root_itself_may_be_the_nfs_mountpoint(self):
        root = Path(list(self.roots)[-1])
        with patch.object(tool, "mountpoints", return_value={root}):
            self.assertEqual(self.reset()["status"], "runtime_logs_reset")

    def test_old_nine_root_plan_and_nonwritable_vector_buffer_are_refused(self):
        original = deepcopy(self.plan)
        self.plan["roots"] = [
            row for row in self.plan["roots"] if row["source"] != self.vector_buffer
        ]
        with self.assertRaisesRegex(RuntimeError, "exact_ten_log_roots_required"):
            self.reset()
        self.plan = original
        for owner_map in (self.owners, self.observed):
            mount = next(
                row
                for row in owner_map["obs-vector"]["Mounts"]
                if row["Source"] == self.vector_buffer
            )
            mount["RW"] = False
        with patch.object(
            tool, "backup_verified", return_value="synthetic-frozen-proof"
        ):
            with self.assertRaisesRegex(
                RuntimeError, "original_writable_vector_buffer_bind_required"
            ):
                self.reset()
        self.assert_untouched()

    def test_links_unlink_only_the_link_and_root_symlink_is_refused(self):
        root = Path(next(iter(self.roots)))
        try:
            (root / "outside-file").symlink_to(self.outside / "keep")
            (root / "outside-directory").symlink_to(
                self.outside, target_is_directory=True
            )
            (root / "broken").symlink_to(self.outside / "absent")
        except OSError as exc:
            self.skipTest(
                "host cannot create synthetic symlinks: " + type(exc).__name__
            )
        self.reset()
        self.assertEqual((self.outside / "keep").read_bytes(), b"business-must-remain")
        root.rmdir()
        root.symlink_to(self.outside, target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError, "directory_link_or_kind_refused"):
            tool.safe_directory(root)

    def test_hard_link_unlink_does_not_remove_the_external_business_file(self):
        root = Path(next(iter(self.roots)))
        (root / "alias").hardlink_to(self.outside / "keep")
        self.reset()
        self.assertEqual((self.outside / "keep").read_bytes(), b"business-must-remain")

    def test_partial_io_failure_records_completed_roots_without_automatic_retry(self):
        clear = tool.clear_children
        calls = []

        def fail_second(root, expected):
            calls.append(root)
            if len(calls) == 2:
                raise PermissionError("synthetic-only")
            clear(root, expected)

        with patch.object(tool, "clear_children", side_effect=fail_second):
            with self.assertRaises(PermissionError):
                self.reset()
        result = json.loads((self.stage / "log-reset-failed.json").read_bytes())
        self.assertEqual(result["status"], "log_reset_incomplete_no_automatic_retry")
        self.assertIn("deleted", result["roots"][0])
        self.assertNotIn("deleted", result["roots"][1])
        self.assertNotIn("synthetic-only", json.dumps(result))
        with self.assertRaisesRegex(RuntimeError, "new_log_reset_required"):
            self.reset()

    def test_cold_epoch_change_during_preflight_refuses_all_deletion(self):
        calls = []

        def inspect(_):
            calls.append(True)
            result = deepcopy(self.observed)
            if len(calls) > 1:
                result["platform"]["State"]["FinishedAt"] = datetime.now(
                    timezone.utc
                ).isoformat()
            return list(result.values())

        with self.assertRaisesRegex(RuntimeError, "owner_cold_epoch_changed"):
            self.reset(inspect=inspect)
        self.assert_untouched()


@unittest.skipUnless(
    sys.platform == "linux" and getattr(os, "geteuid", lambda: -1)() == 0,
    "real UID10001 fixture requires an isolated Linux root test process",
)
class LokiNfsUidTests(unittest.TestCase):
    def setUp(self):
        self.original = (os.getresuid(), os.getresgid(), os.getgroups())
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.addCleanup(self.restore)
        self.base = Path(temporary.name).resolve()
        self.base.chmod(0o755)  # Only this synthetic ancestor, never a NAS path.
        self.root = self.base / "loki-chunks"
        self.root.mkdir(mode=0o755)
        self.root.chmod(0o755)
        (self.root / "nested").mkdir(mode=0o755)
        (self.root / "nested").chmod(0o755)
        (self.root / "nested" / "old").write_bytes(b"old-log")
        for path in (self.root, self.root / "nested", self.root / "nested" / "old"):
            os.chown(path, 10001, 10001)
        private = self.base / "private"
        private.mkdir(mode=0o700)
        self.private = private / "root-only"
        self.private.write_bytes(b"private-fixture")
        self.metadata = tool.metadata(self.root)
        setting = patch.object(tool, "LOKI_NFS_ROOT", str(self.root))
        setting.start()
        self.addCleanup(setting.stop)

    def restore(self):
        os.setresuid(*self.original[0])
        os.setresgid(*self.original[1])
        os.setgroups(self.original[2])

    def assert_restored(self):
        self.assertEqual(
            (os.getresuid(), os.getresgid(), os.getgroups()), self.original
        )
        self.assertEqual(tool.metadata(self.root), self.metadata)
        self.assertEqual(self.private.read_bytes(), b"private-fixture")

    def test_actual_owner_inventory_clear_and_metadata_preserved(self):
        with tool._log_root_access(self.root):
            self.assertEqual(os.getresuid(), (10001, 10001, 0))
            self.assertEqual(os.getresgid(), (10001, 10001, 0))
            self.assertEqual(os.getgroups(), [])
            with self.assertRaises(PermissionError):
                self.private.read_bytes()
            self.assertEqual(tool.inventory(self.root, set())["bytes"], 7)
            tool.clear_children(self.root, self.metadata)
            self.assertEqual(list(self.root.iterdir()), [])
            self.assertEqual(tool.metadata(self.root), self.metadata)
        self.assert_restored()

    def test_exception_inside_target_owner_restores_original_identity(self):
        with self.assertRaisesRegex(RuntimeError, "synthetic-nfs-failure"):
            with tool._log_root_access(self.root):
                self.assertEqual(os.getuid(), 10001)
                raise RuntimeError("synthetic-nfs-failure")
        self.assert_restored()

    def test_partial_uid_transition_failure_also_restores_gid_and_groups(self):
        original_setresuid = os.setresuid

        def fail_drop(*values):
            if values == (10001, 10001, 0):
                raise OSError("synthetic-transition-failure")
            original_setresuid(*values)

        with patch.object(tool.os, "setresuid", side_effect=fail_drop):
            with self.assertRaisesRegex(OSError, "synthetic-transition-failure"):
                with tool._log_root_access(self.root):
                    self.fail("UID transition must fail before entering the body")
        self.assert_restored()


if __name__ == "__main__":
    unittest.main()
