import copy
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from ops.recovery import resident_snapshot, safety
from ops.recovery.resident_snapshot import OWNERS, RecoveryError, require_stopped, snapshot


def owners():
    return [{"id": str(index).zfill(64), "image_id": "sha256:" + "a" * 64,
             "service": service,
             "project": "tianshu-v2-resident-obs" if service.startswith("obs-") else "tianshu-v2-resident",
             "mounts": [], "state": "exited", "exit": 0, "restart": "no",
             **{key: False for key in ("running", "paused", "restarting", "dead", "oom", "error")}}
            for index, service in enumerate(sorted(OWNERS), 1)]


class ResidentSnapshotTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="resident-cold-test-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name); self.root = self.base / "source"
        self.root.mkdir(); (self.root / "empty").mkdir()
        (self.root / "checkpoint.json").write_bytes(b'{"synthetic_guard_revision":2}\n')
        with closing(sqlite3.connect(self.root / "state.sqlite")) as database:
            database.execute("CREATE TABLE facts(id INTEGER PRIMARY KEY, state TEXT)")
            database.execute("INSERT INTO facts VALUES (1,'synthetic_unknown_no_resend')")
            database.commit()
        self.rows = owners()

    def test_full_cold_copy_and_disabled_restore_preserve_state(self):
        result = snapshot(self.root, self.base / "operation", self.rows, lambda: copy.deepcopy(self.rows))
        self.assertEqual("offline_restore_verified", result["status"])
        self.assertEqual(1, result["sqlite_databases_verified"])
        self.assertFalse(result["functional_restore_test"])
        self.assertTrue((self.base / "operation/restored-disabled/empty").is_dir())
        self.assertEqual((self.root / "state.sqlite").read_bytes(),
                         (self.base / "operation/restored-disabled/state.sqlite").read_bytes())

    def test_missing_replaced_or_abnormally_stopped_owner_is_rejected(self):
        changes = [{"exit": 143}, {"exit": 137}, {"running": True}, {"restart": "unless-stopped"},
                   {"oom": True}, {"image_id": "sha256:" + "b" * 64}, {"id": "f" * 64},
                   {"mounts": [{"Destination": "/different"}]}]
        for change in changes:
            with self.subTest(change=change):
                rows = copy.deepcopy(self.rows); rows[0].update(change)
                with self.assertRaises(RecoveryError):
                    require_stopped(self.rows, rows)
        with self.assertRaises(RecoveryError):
            require_stopped(self.rows, self.rows[:-1])

    def test_docker_mount_order_is_ignored_but_all_fields_and_duplicates_are_checked(self):
        expected = copy.deepcopy(self.rows)
        expected[0]["mounts"] = [{"Source": "/synthetic/a", "RW": True},
                                 {"Source": "/synthetic/b", "RW": False}]
        observed = copy.deepcopy(expected)
        observed[0]["mounts"].reverse()
        require_stopped(expected, observed)
        observed[0]["mounts"][0]["RW"] = True
        with self.assertRaises(RecoveryError):
            require_stopped(expected, observed)
        observed = copy.deepcopy(expected)
        observed[0]["mounts"].append(copy.deepcopy(observed[0]["mounts"][0]))
        with self.assertRaises(RecoveryError):
            require_stopped(expected, observed)

    def test_authority_progress_during_copy_prevents_published_receipt(self):
        calls = 0
        def observe():
            nonlocal calls
            calls += 1
            if calls == 2:
                (self.root / "checkpoint.json").write_bytes(b'{"synthetic_guard_revision":3}\n')
            return copy.deepcopy(self.rows)
        operation = self.base / "operation"
        with self.assertRaises(RecoveryError):
            snapshot(self.root, operation, self.rows, observe)
        self.assertFalse((operation / "receipt.json").exists())

    def test_restore_cannot_overwrite_or_nest_inside_authority(self):
        for operation in (self.root, self.root / "nested", self.base):
            with self.subTest(operation=operation):
                with self.assertRaises(RecoveryError):
                    snapshot(self.root, operation, self.rows, lambda: self.rows)
        rows = copy.deepcopy(self.rows)
        rows[0]["mounts"] = [{"RW": True, "Source": str(self.base / "outside") }]
        with self.assertRaises(RecoveryError):
            snapshot(self.root, self.base / "outside-mount", rows, lambda: rows)

    def test_corrupt_sqlite_prevents_published_receipt(self):
        (self.root / "bad.sqlite").write_bytes(b"SQLite format 3\0" + b"not a database" * 20)
        operation = self.base / "operation"
        with self.assertRaises((RecoveryError, sqlite3.Error)):
            snapshot(self.root, operation, self.rows, lambda: self.rows)
        self.assertFalse((operation / "receipt.json").exists())

    def test_memory_guard_requires_exact_database_pair(self):
        with closing(sqlite3.connect(self.root / "state.sqlite")) as database:
            database.execute("CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT)")
            database.executemany("INSERT INTO metadata VALUES (?,?)", [
                ("schema", "3"), ("source_instance", "synthetic-instance"),
                ("source_revision", "7"), ("source_recovery", "verified")])
            database.commit()
        guard = self.root / "state.sqlite.source-guard.json"
        paired = {"schema": 3, "instance": "synthetic-instance", "revision": 7,
                  "recovery": "verified"}
        guard.write_text(json.dumps(paired), encoding="utf-8")
        result = snapshot(self.root, self.base / "paired", self.rows, lambda: self.rows,
                          memory_guards=[guard.name])
        self.assertEqual(1, result["memory_guards_verified"])
        paired["revision"] = 6
        guard.write_text(json.dumps(paired), encoding="utf-8")
        with self.assertRaises(RecoveryError):
            snapshot(self.root, self.base / "mismatch", self.rows, lambda: self.rows,
                     memory_guards=[guard.name])
        self.assertFalse((self.base / "mismatch/receipt.json").exists())

    def test_wal_is_verified_without_changing_original_or_disabled_restore(self):
        database = sqlite3.connect(self.root / "wal.sqlite")
        try:
            database.execute("PRAGMA journal_mode=WAL")
            database.execute("PRAGMA wal_autocheckpoint=0")
            database.execute("CREATE TABLE wal_only(id INTEGER PRIMARY KEY)")
            database.execute("INSERT INTO wal_only VALUES (1)")
            database.commit()
            wal = self.root / "wal.sqlite-wal"
            original = wal.read_bytes()
            result = snapshot(self.root, self.base / "wal-test", self.rows, lambda: self.rows)
            self.assertEqual(2, result["sqlite_databases_verified"])
            self.assertEqual(original, wal.read_bytes())
            self.assertEqual(original,
                             (self.base / "wal-test/restored-disabled/wal.sqlite-wal").read_bytes())
        finally:
            database.close()

    def test_external_controls_are_restored_and_drift_prevents_receipt(self):
        control = self.base / "compose.json"
        control.write_bytes(b'{"synthetic_image":"sha256:fixed"}\n')
        state = self.base / "capacity-state"
        state.mkdir(); (state / "failure.json").write_bytes(b'{"latched":true}\n')
        result = snapshot(self.root, self.base / "controls", self.rows, lambda: self.rows,
                          controls=[control, state])
        self.assertEqual(2, result["external_controls_verified"])
        self.assertEqual(control.read_bytes(),
                         (self.base / "controls/controls-restored-disabled/0").read_bytes())
        self.assertEqual((state / "failure.json").read_bytes(),
                         (self.base / "controls/controls-restored-disabled/1/failure.json").read_bytes())
        calls = 0
        def observe():
            nonlocal calls
            calls += 1
            if calls == 2:
                control.write_bytes(b'{"synthetic_image":"sha256:changed"}\n')
            return self.rows
        with self.assertRaises(RecoveryError):
            snapshot(self.root, self.base / "drift", self.rows, observe, controls=[control])
        self.assertFalse((self.base / "drift/receipt.json").exists())

    def _media_fixture(self, *, hardlinks=False):
        media = self.base / "external-media"
        media.mkdir()
        video = media / "video.mp4"
        video.write_bytes(b"synthetic media content")
        staging = self.root / "data/platform/platform.media-staging"
        staging.mkdir(parents=True)
        if hardlinks:
            os.link(video, staging / "first.mp4")
            os.link(video, staging / "second.mp4")
        entry = {"source": str(media.resolve()), "target": "/media/Bilibili", "read_only": False}
        platform = next(row for row in self.rows if row["service"] == "platform")
        platform["mounts"].append({"Type": "bind", "Source": entry["source"],
                                    "Destination": entry["target"], "RW": True})
        return media, video, staging, entry

    def test_media_defaults_remain_strict(self):
        _, _, staging, _ = self._media_fixture(hardlinks=True)
        with self.assertRaisesRegex(RecoveryError, "writable_mount_outside_authority"):
            snapshot(self.root, self.base / "default-mount", self.rows, lambda: self.rows)
        next(row for row in self.rows if row["service"] == "platform")["mounts"] = []
        with self.assertRaisesRegex(RecoveryError, "hardlinked_file"):
            snapshot(self.root, self.base / "default-hardlink", self.rows, lambda: self.rows)
        self.assertGreater((staging / "first.mp4").stat().st_nlink, 1)

    def test_explicit_media_scope_copies_hardlinks_as_independent_content_and_excludes_library(self):
        media, video, staging, entry = self._media_fixture(hardlinks=True)
        operation = self.base / "media-snapshot"
        result = snapshot(self.root, operation, self.rows, lambda: self.rows,
                          external_media_binds=[entry], media_staging_roots=[staging])
        self.assertEqual([entry], result["excluded_external_media"])
        self.assertEqual([str(staging.resolve())], result["media_staging_roots"])
        self.assertEqual("independent_content_files", result["media_staging_restore"])
        manifest = json.loads((operation / "manifest.json").read_text())
        self.assertEqual([entry], manifest["excluded_external_media"])
        self.assertTrue(all(not name.startswith("external-media") for name in manifest["inventory"]["entries"]))
        for tree in ("backup", "restored-disabled"):
            for name in ("first.mp4", "second.mp4"):
                restored = operation / tree / staging.relative_to(self.root) / name
                self.assertEqual(video.read_bytes(), restored.read_bytes())
                self.assertEqual(1, restored.stat().st_nlink)
            self.assertFalse((operation / tree / media.name).exists())
        self.assertEqual(3, video.stat().st_nlink)

    def test_external_media_binding_must_match_only_platform_in_full(self):
        media, _, _, entry = self._media_fixture()
        platform = next(row for row in self.rows if row["service"] == "platform")
        original_mount = copy.deepcopy(platform["mounts"][0])
        for change in ({"Source": str(self.base)}, {"Destination": "/media/Other"},
                       {"RW": False}, {"Type": "volume"}):
            with self.subTest(change=change):
                platform["mounts"] = [{**original_mount, **change}]
                with self.assertRaises(RecoveryError):
                    snapshot(self.root, self.base / "mismatched", self.rows, lambda: self.rows,
                             external_media_binds=[entry])
        platform["mounts"] = [copy.deepcopy(original_mount)]
        companion = next(row for row in self.rows if row["service"] == "companion")
        companion["mounts"] = [copy.deepcopy(original_mount)]
        with self.assertRaisesRegex(RecoveryError, "external_media_mount_mismatch"):
            snapshot(self.root, self.base / "wrong-owner", self.rows, lambda: self.rows,
                     external_media_binds=[entry])
        companion["mounts"] = []
        platform["mounts"].append(copy.deepcopy(original_mount))
        with self.assertRaisesRegex(RecoveryError, "external_media_mount_mismatch"):
            snapshot(self.root, self.base / "duplicate", self.rows, lambda: self.rows,
                     external_media_binds=[entry])
        platform["mounts"] = []
        with self.assertRaisesRegex(RecoveryError, "external_media_mount_mismatch"):
            snapshot(self.root, self.base / "missing", self.rows, lambda: self.rows,
                     external_media_binds=[entry])
        self.assertTrue(media.is_dir())

    def test_media_scope_refuses_invalid_ranges_and_control_overlap(self):
        media, _, staging, entry = self._media_fixture()
        for scope in (self.root, media, self.root / "missing", self.root / "checkpoint.json"):
            with self.subTest(scope=scope):
                with self.assertRaises(RecoveryError):
                    snapshot(self.root, self.base / "scope", self.rows, lambda: self.rows,
                             external_media_binds=[entry], media_staging_roots=[scope])
        with self.assertRaisesRegex(RecoveryError, "external_media_control_overlap"):
            snapshot(self.root, self.base / "overlap", self.rows, lambda: self.rows,
                     external_media_binds=[entry], controls=[media])
        result = snapshot(self.root, self.base / "without-staging", self.rows, lambda: self.rows,
                          external_media_binds=[entry], media_staging_roots=[])
        self.assertEqual([], result["media_staging_roots"])
        self.assertTrue(staging.exists())

    def test_credentials_outside_media_staging_still_refuse_hardlinks(self):
        _, _, staging, entry = self._media_fixture(hardlinks=True)
        credential = self.root / "synthetic-credential"
        credential.write_bytes(b"opaque synthetic fixture")
        os.link(credential, self.root / "credential-alias")
        with self.assertRaisesRegex(RecoveryError, "hardlinked_file"):
            snapshot(self.root, self.base / "credential-link", self.rows, lambda: self.rows,
                     external_media_binds=[entry], media_staging_roots=[staging])

    def test_media_source_change_during_hash_is_not_bypassed(self):
        _, video, staging, entry = self._media_fixture(hardlinks=True)
        original_hash = resident_snapshot.file_hash
        changed = False

        def hash_and_change(path, **kwargs):
            nonlocal changed
            value = original_hash(path, **kwargs)
            if path == staging / "first.mp4" and not changed:
                video.write_bytes(b"changed synthetic media content")
                changed = True
            return value

        operation = self.base / "hash-change"
        with patch.object(resident_snapshot, "file_hash", hash_and_change), self.assertRaisesRegex(
            RecoveryError, "authority_changed"
        ):
            snapshot(self.root, operation, self.rows, lambda: self.rows,
                     external_media_binds=[entry], media_staging_roots=[staging])
        self.assertFalse((operation / "receipt.json").exists())

    def test_media_source_change_during_copy_is_not_bypassed(self):
        _, video, staging, entry = self._media_fixture(hardlinks=True)
        original_copy = safety.copy_new
        changed = False

        def copy_with_change(source, target, *, check_cancel, **kwargs):
            def change_during_read():
                nonlocal changed
                check_cancel()
                if source == staging / "first.mp4" and not changed:
                    video.write_bytes(b"changed synthetic media content")
                    changed = True
            return original_copy(source, target, check_cancel=change_during_read, **kwargs)

        operation = self.base / "copy-change"
        with patch.object(resident_snapshot, "copy_new", copy_with_change), self.assertRaisesRegex(
            RecoveryError, "source_changed_during_copy"
        ):
            snapshot(self.root, operation, self.rows, lambda: self.rows,
                     external_media_binds=[entry], media_staging_roots=[staging])
        self.assertFalse((operation / "receipt.json").exists())

    def test_external_media_directory_replacement_prevents_receipt(self):
        media, _, _, entry = self._media_fixture()
        calls = 0

        def observe():
            nonlocal calls
            calls += 1
            if calls == 2:
                media.rename(self.base / "previous-media")
                media.mkdir()
            return self.rows

        operation = self.base / "media-replaced"
        with self.assertRaisesRegex(RecoveryError, "external_media_directory_changed"):
            snapshot(self.root, operation, self.rows, observe, external_media_binds=[entry])
        self.assertFalse((operation / "receipt.json").exists())


if __name__ == "__main__":
    unittest.main()
