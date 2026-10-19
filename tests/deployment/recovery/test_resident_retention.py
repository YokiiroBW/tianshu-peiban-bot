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

    def test_relocation_requires_exact_namespace_and_unchanged_proof(self):
        old = self.cold("relocated")
        dedicated = self.base / "dedicated-hdd"
        target = dedicated / "relocated/cold-snapshot"
        target.parent.mkdir(parents=True)
        old.rename(target)
        old.mkdir()
        record = {"schema_version": "resident-cold-relocation/1", "source": old.as_posix(),
                  "target": target.as_posix(),
                  "manifest_sha256": hashlib.sha256((target / "manifest.json").read_bytes()).hexdigest(),
                  "receipt_sha256": hashlib.sha256((target / "receipt.json").read_bytes()).hexdigest()}
        (old / "relocation.json").write_text(json.dumps(record))
        with patch.object(retention, "HDD_UPDATES", dedicated), patch.object(retention, "LEGACY_UPDATES", (self.updates,)):
            self.assertEqual(retention.resolve_snapshot(old), target)
            (target / "receipt.json").write_bytes(b"changed")
            with self.assertRaisesRegex(RuntimeError, "relocation_proof_changed"):
                retention.resolve_snapshot(old)

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
        for proof in new.glob("retention-*.json"):
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

    def test_checkpoint_failure_deletes_nothing_and_interruption_after_delete_keeps_proof(self):
        old, new = self.cold("old"), self.cold("new")
        plan = self.plan(old, new)
        with patch.object(retention, "_write_record", side_effect=OSError("synthetic_checkpoint_io")):
            with self.assertRaises(OSError):
                retention.apply_plan(plan, snapshot=snapshot, containers=[], execute=True)
        self.assertTrue(all((new / name).is_dir() for name in retention.DATA_DIRECTORIES))
        self.assertTrue((old / "backup").is_dir())
        actual_write, actual_delete = retention._write_record, retention.shutil.rmtree

        def write_checkpoint_only(path, value):
            if Path(path).name.startswith("retention-checkpoint-"):
                return actual_write(path, value)
            raise SystemExit("synthetic_power_loss_before_final_receipt")

        def delete_then_interrupt(path):
            actual_delete(path)
            raise SystemExit("synthetic_interruption_after_first_delete")

        with patch.object(retention, "_write_record", side_effect=write_checkpoint_only), \
                patch.object(retention.shutil, "rmtree", side_effect=delete_then_interrupt):
            with self.assertRaises(SystemExit):
                retention.apply_plan(plan, snapshot=snapshot, containers=[], execute=True)
        self.assertFalse((new / "restored-disabled").exists())
        self.assertTrue((new / "backup/state.sqlite").is_file())
        self.assertTrue((old / "backup/state.sqlite").is_file())
        self.assertEqual(1, len(list(new.glob("retention-*.json"))))
        self.assertTrue(retention.verification_copies_available(new))
        checkpoint = next(new.glob("retention-checkpoint-*.json"))
        partial = new / "retention-interrupted-final.json"
        partial.write_bytes(b'{"status":')
        with patch.object(Path, "glob", return_value=[partial, checkpoint]):
            self.assertTrue(retention.verification_copies_available(new))

    def test_public_snapshot_retains_only_after_success_and_cleanup_failure_keeps_success(self):
        old = self.cold("old")
        failed = self.updates / "failed" / "cold-snapshot"
        failed.parent.mkdir()
        broken = self.root / "broken.sqlite"
        broken.write_bytes(b"SQLite format 3\0" + b"synthetic corrupt data" * 20)
        with patch.object(retention, "after_verified_snapshot") as hook:
            with self.assertRaises(sqlite3.Error):
                snapshot.snapshot(self.root, failed, self.rows, lambda: self.rows, controls=[self.control], docker="synthetic")
            hook.assert_not_called()
        self.assertTrue((old / "backup/state.sqlite").is_file())
        broken.unlink()
        fresh = self.updates / "fresh" / "cold-snapshot"
        fresh.parent.mkdir()
        with patch.object(retention, "docker_references", return_value=[]):
            report = snapshot.snapshot(self.root, fresh, self.rows, lambda: self.rows, controls=[self.control], docker="synthetic")
        self.assertEqual("offline_restore_verified", report["status"])
        self.assertEqual("applied", report["retention"]["status"])
        self.assertIn(failed.as_posix(), report["retention"]["skipped_unverified_snapshots"])
        self.assertFalse((old / "backup").exists())
        self.assertTrue(retention.verification_copies_available(fresh))
        another = self.updates / "another" / "cold-snapshot"
        another.parent.mkdir()
        with patch.object(retention, "docker_references", return_value=[]), \
                patch.object(retention, "verify_kept_copies", side_effect=snapshot.RecoveryError("synthetic_backup_check_failure")):
            report = snapshot.snapshot(self.root, another, self.rows, lambda: self.rows, controls=[self.control], docker="synthetic")
        self.assertEqual("offline_restore_verified", report["status"])
        self.assertEqual("requires_inspection", report["retention"]["status"])
        self.assertTrue((fresh / "backup/state.sqlite").is_file())

    def test_deployed_media_registration_and_staging_hardlink_boundaries_are_preserved(self):
        library = self.base / "external-library"
        library.mkdir()
        (library / "media.bin").write_bytes(b"synthetic external media remains authoritative")
        rows = copy.deepcopy(self.rows)
        platform = next(row for row in rows if row["service"] == "platform")
        platform["mounts"].append({"Type": "bind", "Source": str(library), "Destination": "/media/Music", "RW": True})
        media = [{"source": str(library), "target": "/media/Music", "read_only": False}]
        with self.assertRaisesRegex(snapshot.RecoveryError, "writable_mount_outside_authority"):
            snapshot.snapshot(self.root, self.base / "unregistered", rows, lambda: rows)
        staging = self.root / "staging"
        staging.mkdir()
        (staging / "media.bin").hardlink_to(library / "media.bin")
        with self.assertRaisesRegex(snapshot.RecoveryError, "hardlinked_file"):
            snapshot.snapshot(self.root, self.base / "unregistered-hardlink", rows, lambda: rows, external_media_binds=media)
        destination = self.base / "registered-media"
        result = snapshot.snapshot(self.root, destination, rows, lambda: rows,
                                   external_media_binds=media, media_staging_roots=[staging])
        self.assertEqual(media, result["excluded_external_media"])
        self.assertEqual((library / "media.bin").read_bytes(), (destination / "backup/staging/media.bin").read_bytes())
        self.assertEqual(1, (destination / "backup/staging/media.bin").stat().st_nlink)
        self.assertEqual(1, (destination / "restored-disabled/staging/media.bin").stat().st_nlink)
        self.assertEqual(2, (library / "media.bin").stat().st_nlink)


if __name__ == "__main__":
    unittest.main()
