"""Real synthetic source files, acknowledged prefix receipts and safe retry."""

import copy
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from helpers import SNAPSHOT, emit, event

# isort: split
from monitor import Ledger
from policy import Policy
from source_retention import active_instances, native_segment, prune_sources

INSTANCE = "00000000-0000-4000-8000-000000000001"
CURRENT = "00000000-0000-4000-8000-000000000002"


class SourceRetentionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.ledger = Ledger(
            self.root / "ledger.sqlite", Policy(SNAPSHOT), max_events=3
        )
        self.addCleanup(self.ledger.close)
        self.roots = {"platform": self.root}
        self.first = self.root / f"platform-{INSTANCE}-0000.jsonl"
        self.next = self.root / f"platform-{INSTANCE}-0001.jsonl"
        self.records = [event(i) for i in range(1, 4)]
        emit(self.first, self.records[:2])
        emit(self.next, self.records[2:])
        self.ledger.scan(self.roots)
        with self.ledger.db:
            self.ledger.db.execute("UPDATE events SET first_seen=100,verified=200")
        for path in (self.first, self.next):
            os.utime(path, (100, 100))

    def prune(self):
        return prune_sources(self.ledger, self.roots, 1, now=200000)

    def test_closed_expired_segment_deletes_active_prefix_is_proved_but_never_unlinked(
        self,
    ):
        metrics, coverage = self.prune()
        self.assertFalse(self.first.exists())
        self.assertTrue(self.next.exists())
        self.assertEqual(metrics["source_retention_deleted_segments"], 1)
        self.assertEqual(coverage, {("platform", INSTANCE): 3})
        self.ledger.expire(1, now=200000, coverage=coverage)
        self.assertEqual(self.ledger.metrics()["landed_events"], 0)
        self.assertEqual(self.ledger.metrics()["sequence_gaps"], 0)
        # Receipt for a removed source is collected only after its prefix commits.
        self.prune()
        self.assertEqual(
            self.ledger.db.execute("SELECT name FROM source_receipts").fetchall(),
            [("platform/" + self.next.name,)],
        )

    def test_unlink_failure_proof_survives_hash_retirement_and_reopen(self):
        with patch.object(
            Path, "unlink", side_effect=PermissionError("synthetic read-only mount")
        ):
            metrics, coverage = self.prune()
        self.assertEqual(metrics["source_retention_failures"], 1)
        self.assertTrue(self.first.exists())
        self.ledger.expire(1, now=200000, coverage=coverage)
        self.assertEqual(self.ledger.metrics()["landed_events"], 0)
        self.ledger.close()
        self.ledger = Ledger(self.root / "ledger.sqlite", Policy(SNAPSHOT))
        self.addCleanup(self.ledger.close)
        self.prune()
        self.assertFalse(self.first.exists())
        self.assertTrue(self.next.exists())

    def test_pending_and_conflicting_content_never_receive_a_receipt(self):
        with self.ledger.db:
            self.ledger.db.execute("UPDATE events SET verified=0 WHERE sequence=2")
        metrics, coverage = self.prune()
        self.assertTrue(self.first.exists())
        self.assertEqual(coverage.get(("platform", INSTANCE)), 1)
        self.ledger.expire(1, now=200000, coverage=coverage)
        self.assertEqual(self.ledger.metrics()["landed_events"], 2)
        self.assertEqual(metrics["source_retention_deleted_segments"], 0)

    def test_new_tail_extends_saved_prefix_after_its_old_hash_has_expired(self):
        _, coverage = self.prune()
        self.ledger.expire(1, now=200000, coverage=coverage)
        emit(self.next, [event(4)])
        self.ledger.scan(self.roots)
        with self.ledger.db:
            self.ledger.db.execute("UPDATE events SET verified=200,first_seen=100")
        _, extended = self.prune()
        self.assertEqual(extended[("platform", INSTANCE)], 4)
        self.assertEqual(self.ledger.metrics()["landed_events"], 1)
        self.assertTrue(self.next.exists())
        self.assertEqual(
            self.ledger.db.execute("SELECT size FROM source_receipts").fetchone()[0],
            self.next.stat().st_size,
        )

    def test_changed_receipted_bytes_are_not_deleted(self):
        with patch.object(Path, "unlink", side_effect=PermissionError("synthetic")):
            _, coverage = self.prune()
        self.ledger.expire(1, now=200000, coverage=coverage)
        original = self.first.read_bytes()
        first, second = original.splitlines(keepends=True)
        altered = first + second.replace(b'"succeeded"', b'"cancelled"', 1)
        self.assertEqual(len(altered), len(original))
        self.first.write_bytes(altered)
        os.utime(self.first, (100, 100))
        metrics, _ = self.prune()
        self.assertTrue(self.first.exists())
        self.assertEqual(metrics["source_retention_failures"], 1)

    def test_partial_scanned_active_prefix_allows_full_ledger_to_make_room(self):
        # Active source has an unread fourth event. Its acknowledged first three
        # are enough proof to evict old hashes without deleting the active file.
        self.first.unlink()
        self.next.unlink()
        path = self.root / f"platform-{CURRENT}-0000.jsonl"
        emit(path, [*self.records, event(4)])
        self.ledger.scan(self.roots)
        _, coverage = self.prune()
        self.ledger.expire(30, now=101, coverage=coverage, reserve=1)
        self.assertEqual(self.ledger.metrics()["landed_events"], 2)
        self.ledger.scan(self.roots)
        self.assertEqual(self.ledger.metrics()["landed_events"], 3)
        self.assertEqual(self.ledger.metrics()["pending_events"], 1)
        self.assertEqual(self.ledger.metrics()["sequence_gaps"], 0)
        self.assertTrue(path.exists())

    def test_native_filename_boundaries(self):
        expected = {
            "platform": f"platform-{INSTANCE}-0000.jsonl",
            "gateway": f"gateway-{INSTANCE}-000000.jsonl",
            "memory": f"{INSTANCE}.000000.jsonl",
            "knowledge": f"{INSTANCE}.000000.jsonl",
            "companion": "companion.jsonl.12",
        }
        for source, name in expected.items():
            self.assertIsNotNone(native_segment(source, name))
        self.assertIsNone(native_segment("platform", "arbitrary.jsonl"))


class RegistryTests(unittest.TestCase):
    def test_only_live_fresh_ready_complete_five_uuid_registry_is_usable(self):
        now = time.time()
        good = {
            "version": 1,
            "status": "ready",
            "pid": os.getpid(),
            "updated_at": now,
            "diagnostics": {
                "complete": True,
                "instances": {
                    role: CURRENT
                    for role in (
                        "platform",
                        "gateway",
                        "memory",
                        "knowledge",
                        "companion",
                    )
                },
            },
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "host.json"
            with patch("source_retention.pid_alive", return_value=True):
                path.write_text(json.dumps(good))
                self.assertEqual(
                    active_instances(path, now), good["diagnostics"]["instances"]
                )
                for mutation in (
                    lambda s: s.update(status="degraded"),
                    lambda s: s.update(status="stopping"),
                    lambda s: s.update(updated_at=now + 1),
                    lambda s: s.update(updated_at=now - 121),
                    lambda s: s["diagnostics"].update(complete=False),
                    lambda s: s["diagnostics"]["instances"].pop("knowledge"),
                ):
                    changed = copy.deepcopy(good)
                    mutation(changed)
                    path.write_text(json.dumps(changed))
                    self.assertEqual(active_instances(path, now), {})
            path.write_text(json.dumps(good))
            with patch("source_retention.pid_alive", return_value=False):
                self.assertEqual(active_instances(path, now), {})


if __name__ == "__main__":
    unittest.main()
