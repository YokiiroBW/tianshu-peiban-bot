"""Filesystem-only fault injection through real lifecycle stop/backup/restore paths."""

import json
import os
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import test_lifecycle as lifecycle_cases

from ops.recovery.engine import Recovery
from ops.recovery.lifecycle import operate
from ops.recovery.safety import RecoveryError, files, sync_tree
from ops.recovery.snapshot import volume_directories


@contextmanager
def inaccessible(predicate, error_type, *, mode="open"):
    actual = os.scandir
    hits = []

    def scan(path):
        if not predicate(Path(path)):
            return actual(path)
        hits.append(str(path))
        error = error_type("private-path-and-content-must-not-reach-output")
        if mode == "open":
            raise error

        class Scan:
            def __enter__(self):
                self.inner = actual(path)
                return self

            def __exit__(self, *_):
                self.inner.close()

            def __iter__(self):
                return self

            def __next__(self):
                entry = next(self.inner)
                if mode == "iterate":
                    raise error

                class Entry:
                    name, path = entry.name, entry.path

                    def is_dir(self, *, follow_symlinks=True):
                        raise error

                return Entry()

        return Scan()

    with patch("os.scandir", side_effect=scan):
        yield hits


class EnumerationFailureTests(unittest.TestCase):
    def setUp(self):
        self.case = lifecycle_cases.LifecycleTests()
        self.case.setUp()
        self.root, self.source = self.case.root, self.case.source

    def tearDown(self):
        self.case.tearDown()

    def action(self, operation="backup", **options):
        return operate(
            Recovery(self.root, self.case.scope),
            self.source,
            lifecycle_cases.PROJECT,
            self.case.binding_hash,
            operation,
            execute=True,
            **options,
        )

    def completions(self):
        path = self.source / ".lifecycle/events"
        return set(path.glob("*.complete.json")) if path.exists() else set()

    def locks(self):
        home = self.source / ".lifecycle"
        paths = [self.root / ".recovery-owner", home / "action.lock"]
        paths.extend((home / "owners").glob("*.lock"))
        # Windows mandatory byte locks forbid reading active lease contents.
        return {str(p): (p.stat().st_ino, p.stat().st_size) for p in paths}

    def assert_stopped_and_gated(self, locks, completions):
        self.assertTrue(all(p.poll() == 0 for p in self.case.processes))
        gate = self.source / ".lifecycle/MAINTENANCE.json"
        self.assertEqual(
            json.loads(gate.read_bytes()),
            {"binding_sha256": self.case.binding_hash, "activation": "disabled"},
        )
        self.assertEqual(self.locks(), locks)
        self.assertEqual(self.completions(), completions)

    def test_source_enumeration_fault_rejected_before_stopping(self):
        self.case.start()
        blocked = self.source / "observability/data/loki/empty-subdirectory"
        (blocked / "must-retain.bin").write_bytes(b"synthetic-required-record")
        locks = self.locks()
        for error in (PermissionError, OSError):
            with self.subTest(error=error.__name__):
                with inaccessible(lambda path: path == blocked, error) as hits:
                    with self.assertRaisesRegex(
                        RecoveryError, "^directory_enumeration_failed$"
                    ):
                        self.action(backup="unreadable-copy")
                self.assertTrue(hits)
                self.assertTrue(all(p.poll() is None for p in self.case.processes))
                self.assertFalse((self.root / "backups/unreadable-copy").exists())
                self.assertFalse((self.source / ".lifecycle/MAINTENANCE.json").exists())
                self.assertEqual(self.completions(), set())
                self.assertEqual(self.locks(), locks)

    def test_new_source_fault_after_nine_writers_exit_keeps_gate(self):
        self.case.start()
        blocked = self.source / "observability/data/loki/empty-subdirectory"
        record = blocked / "must-retain.bin"
        record.write_bytes(b"synthetic-required-record")
        locks = self.locks()
        for error in (PermissionError, OSError):
            with self.subTest(error=error.__name__):
                with inaccessible(
                    lambda path: path == blocked
                    and all(p.poll() == 0 for p in self.case.processes),
                    error,
                ) as hits:
                    with self.assertRaisesRegex(
                        RecoveryError, "^directory_enumeration_failed$"
                    ):
                        self.action(backup="unreadable-copy")
                self.assertTrue(hits)
                self.assertFalse((self.root / "backups/unreadable-copy").exists())
                self.assertEqual(record.read_bytes(), b"synthetic-required-record")
                self.assert_stopped_and_gated(locks, set())

    def test_source_entry_metadata_failure_cannot_hide_a_directory(self):
        self.case.start()
        folder = self.source / "observability/data/loki"
        (folder / "empty-subdirectory/must-retain.bin").write_bytes(b"synthetic")
        locks = self.locks()
        with inaccessible(
            lambda path: path == folder
            and all(p.poll() == 0 for p in self.case.processes),
            OSError,
            mode="metadata",
        ) as hits:
            with self.assertRaisesRegex(
                RecoveryError, "^directory_enumeration_failed$"
            ):
                self.action(backup="metadata-fault")
        self.assertTrue(hits)
        self.assertFalse((self.root / "backups/metadata-fault").exists())
        self.assert_stopped_and_gated(locks, set())

    def test_payload_fault_cannot_hide_extra_file_from_verify_or_restore(self):
        self.case.start()
        checksum = self.case.action("backup", "--backup", "good")["result"][
            "snapshot_sha256"
        ]
        blocked = (
            self.root
            / "backups/good/payload/observability/data/loki/empty-subdirectory"
        )
        (blocked / "undeclared.bin").write_bytes(b"synthetic-undeclared-payload")
        locks, completed = self.locks(), self.completions()
        for error in (PermissionError, OSError):
            with self.subTest(error=error.__name__):
                with inaccessible(lambda path: path == blocked, error) as hits:
                    with self.assertRaisesRegex(
                        RecoveryError, "^directory_enumeration_failed$"
                    ):
                        Recovery(self.root, self.case.scope).verify("good", checksum)
                    with self.assertRaisesRegex(
                        RecoveryError, "^directory_enumeration_failed$"
                    ):
                        self.action(
                            "restore",
                            backup="good",
                            snapshot_sha256=checksum,
                            target="restored",
                            authority_id=self.case.authority,
                        )
                self.assertTrue(hits)
                self.assertFalse((self.root / "deployments/restored").exists())
                self.assert_stopped_and_gated(locks, completed)

    def test_partial_scandir_iteration_aborts_all_enumeration_consumers(self):
        folder = self.source / "observability/data/loki"
        manifest = json.loads((self.source / "release-manifest.json").read_bytes())
        consumers = (
            lambda: files(self.source),
            lambda: volume_directories(self.source, manifest, max_directories=10000),
            lambda: sync_tree(self.source),
        )
        for error in (PermissionError, OSError):
            for consumer in consumers:
                with self.subTest(error=error.__name__, consumer=consumer):
                    with inaccessible(
                        lambda path: path == folder, error, mode="iterate"
                    ) as hits:
                        with self.assertRaisesRegex(
                            RecoveryError, "^directory_enumeration_failed$"
                        ):
                            consumer()
                    self.assertTrue(hits)

    def test_backup_stage_enumeration_fault_cannot_publish(self):
        self.case.start()
        locks = self.locks()
        for error in (PermissionError, OSError):
            with self.subTest(error=error.__name__):

                def staged(path):
                    relative = (
                        path.relative_to(self.root)
                        if path.is_relative_to(self.root)
                        else None
                    )
                    return (
                        relative is not None
                        and relative.parts[0] == "backups"
                        and relative.parts[1].startswith(".pending-")
                        and path.name == "empty-subdirectory"
                    )

                with inaccessible(staged, error) as hits:
                    with self.assertRaisesRegex(
                        RecoveryError, "^directory_enumeration_failed$"
                    ):
                        self.action(backup="failed-stage")
                self.assertTrue(hits)
                self.assertFalse((self.root / "backups/failed-stage").exists())
                self.assert_stopped_and_gated(locks, set())
        self.assertTrue(list((self.root / "backups").glob(".pending-*/ABORTED.json")))

    def test_restore_stage_enumeration_fault_cannot_publish_target(self):
        self.case.start()
        checksum = self.case.action("backup", "--backup", "good")["result"][
            "snapshot_sha256"
        ]
        locks, completed = self.locks(), self.completions()
        for error in (PermissionError, OSError):
            with self.subTest(error=error.__name__):

                def staged(path):
                    relative = (
                        path.relative_to(self.root)
                        if path.is_relative_to(self.root)
                        else None
                    )
                    return (
                        relative is not None
                        and relative.parts[0] == "deployments"
                        and relative.parts[1].startswith(".pending-")
                        and path.name == "empty-subdirectory"
                    )

                with inaccessible(staged, error) as hits:
                    with self.assertRaisesRegex(
                        RecoveryError, "^directory_enumeration_failed$"
                    ):
                        self.action(
                            "restore",
                            backup="good",
                            snapshot_sha256=checksum,
                            target="restored",
                            authority_id=self.case.authority,
                        )
                self.assertTrue(hits)
                self.assertFalse((self.root / "deployments/restored").exists())
                self.assert_stopped_and_gated(locks, completed)
        self.assertTrue(
            list((self.root / "deployments").glob(".pending-*/ABORTED.json"))
        )
