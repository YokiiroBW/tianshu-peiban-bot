import copy
import hashlib
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from ops.recovery import resident_retention as retention
from ops.recovery import resident_snapshot as snapshot


class ResidentRetentionTests(unittest.TestCase):
    def setUp(self):
        # All actual deletion tests are confined to this test's private tree.
        temporary = tempfile.TemporaryDirectory(prefix="resident-retention-test-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base / "resident"
        self.root.mkdir()
        (self.root / "config").mkdir()
        with closing(sqlite3.connect(self.root / "state.sqlite")) as database:
            database.execute("CREATE TABLE facts(id INTEGER PRIMARY KEY, value TEXT)")
            database.execute("INSERT INTO facts VALUES(1,'synthetic_preserve')")
            database.commit()
        self.control = self.base / "guard"
        self.control.mkdir()
        (self.control / "guard.py").write_bytes(b"# synthetic old guard\n")
        self.rows = [{"id": str(index).zfill(64), "image_id": "sha256:" + "a" * 64,
                      "service": name, "project": "tianshu-v2-resident-obs" if name.startswith("obs-") else "tianshu-v2-resident",
                      "mounts": [{"Type": "bind", "Source": (self.root / "config").as_posix(), "RW": True}],
                      "state": "exited", "exit": 0, "restart": "no",
                      **{key: False for key in ("running", "paused", "restarting", "dead", "oom", "error")}}
                     for index, name in enumerate(sorted(snapshot.OWNERS), 1)]
        self.updates = self.base / "resident-updates"
        self.updates.mkdir()

    def cold(self, name):
        path = self.base / ("temporary-" + name)
        snapshot.snapshot(self.root, path, self.rows, lambda: copy.deepcopy(self.rows), controls=[self.control])
        target = self.updates / name / "cold-snapshot"
        target.parent.mkdir()
        path.rename(target)
        return target

    def plan(self, old, new):
        records = [retention.read_evidence(path, self.root) for path in (old, new)]
        return retention.make_plan(updates_root=self.updates.as_posix(), authority_root=self.root.as_posix(),
                                   keep=new.as_posix(), snapshots=records, containers=[],
                                   protected_images=["sha256:" + "a" * 64, "sha256:" + "b" * 64])

    def test_dry_run_then_prune_keeps_exact_backup_controls_and_original_proof(self):
        old, new = self.cold("old"), self.cold("new")
        plan = self.plan(old, new)
        original = {path: (path / "manifest.json").read_bytes() for path in (old, new)}
        before = retention.read_evidence(new, self.root)
        report = retention.apply_plan(plan, snapshot=snapshot, containers=[], execute=False)
        self.assertEqual("planned", report["status"])
        self.assertEqual(0, len(list(new.glob("retention-*.json"))))
        self.assertTrue((old / "backup").is_dir())
        report = retention.apply_plan(plan, snapshot=snapshot, containers=lambda: [], execute=True)
        self.assertEqual("applied", report["status"])
        self.assertEqual(8, len(report["removed"]))
        self.assertTrue((new / "backup/state.sqlite").is_file())
        self.assertTrue((new / "controls-backup/0/guard.py").is_file())
        self.assertEqual(before["receipt_sha256"], hashlib.sha256((new / "receipt.json").read_bytes()).hexdigest())
        for path in (old, new):
            self.assertEqual(original[path], (path / "manifest.json").read_bytes())
        self.assertFalse((old / "backup").exists())
        self.assertTrue(retention.verification_copies_available(new))
        proof = next(new.glob("retention-*.json"))
        proof.unlink()
        self.assertFalse(retention.verification_copies_available(new))

    def test_corrupt_unique_keep_or_unverified_new_snapshot_never_deletes_old(self):
        old, new = self.cold("old"), self.cold("new")
        plan = self.plan(old, new)
        (new / "backup/state.sqlite").write_bytes(b"changed")
        with self.assertRaisesRegex(RuntimeError, "retention_kept_backup_changed"):
            retention.apply_plan(plan, snapshot=snapshot, containers=[], execute=True)
        self.assertTrue((old / "backup/state.sqlite").is_file())
        (new / "receipt.json").unlink()
        with self.assertRaises(FileNotFoundError):
            self.plan(old, new)
        self.assertTrue((old / "backup/state.sqlite").is_file())

    def test_stopped_container_mounts_or_changed_metadata_prevent_any_delete(self):
        old, new = self.cold("old"), self.cold("new")
        plan = self.plan(old, new)
        containers = [{"id": "f" * 64, "running": False,
                       "mounts": [{"Type": "bind", "Source": (old / "backup").as_posix()}]}]
        with self.assertRaisesRegex(RuntimeError, "retention_container_references_data"):
            retention.apply_plan(plan, snapshot=snapshot, containers=containers, execute=True)
        self.assertTrue((new / "restored-disabled").is_dir())
        with (old / "manifest.json").open("ab") as stream:
            stream.write(b" ")
        with self.assertRaisesRegex(RuntimeError, "retention_manifest_hash_changed"):
            retention.apply_plan(plan, snapshot=snapshot, containers=[], execute=True)
        self.assertTrue((new / "restored-disabled").is_dir())

    def test_bind_is_reloaded_after_slow_keep_verification_before_delete(self):
        old, new = self.cold("old"), self.cold("new")
        plan = self.plan(old, new)
        verified = False
        actual_verify = retention.verify_kept_copies

        def verify(*args):
            nonlocal verified
            actual_verify(*args)
            verified = True

        def containers():
            return ([{"mounts": [{"Type": "bind", "Source": (old / "backup").as_posix()}]}]
                    if verified else [])

        with patch.object(retention, "verify_kept_copies", side_effect=verify):
            with self.assertRaisesRegex(RuntimeError, "retention_container_references_data"):
                retention.apply_plan(plan, snapshot=snapshot, containers=containers, execute=True)
        self.assertTrue((new / "restored-disabled").is_dir())
        self.assertTrue((old / "backup").is_dir())

    def test_success_hook_skips_partial_history_and_records_cleanup_failure(self):
        old, new = self.cold("old"), self.cold("new")
        partial = self.updates / "partial" / "cold-snapshot"
        partial.mkdir(parents=True)
        (partial / "backup").mkdir()
        with patch.object(retention, "docker_references", return_value=[]):
            report = retention.after_verified_snapshot(self.root, new, snapshot=snapshot, docker="synthetic")
        self.assertEqual("applied", report["status"])
        self.assertIn(partial.as_posix(), report["skipped_unverified_snapshots"])
        self.assertTrue((partial / "backup").is_dir())
        self.assertFalse((old / "backup").exists())
        with patch.object(retention, "docker_references", side_effect=RuntimeError("synthetic_read_failure")):
            report = retention.after_verified_snapshot(self.root, new, snapshot=snapshot, docker="synthetic")
        self.assertEqual("requires_inspection", report["status"])
        self.assertEqual("synthetic_read_failure", report["code"])
        self.assertTrue((new / "backup/state.sqlite").is_file())
        self.assertEqual("offline_restore_verified", json.loads((new / "receipt.json").read_bytes())["status"])


if __name__ == "__main__":
    unittest.main()
