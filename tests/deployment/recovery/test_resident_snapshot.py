import copy
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
