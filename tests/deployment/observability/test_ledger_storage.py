"""Ledger upgrade, transactional aggregates and bounded indexed revisit queues."""

from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from helpers import SNAPSHOT, emit, event
from monitor import Ledger
from policy import Policy, canonical, digest, digest_bytes


LEGACY_SCHEMA = """
    CREATE TABLE files (name TEXT PRIMARY KEY, identity TEXT, offset INTEGER);
    CREATE TABLE events (id TEXT PRIMARY KEY, service TEXT, instance TEXT,
        sequence INTEGER, hash TEXT, first_seen REAL, verified REAL DEFAULT 0,
        last_checked REAL DEFAULT 0);
    CREATE INDEX event_pending ON events(verified,last_checked,first_seen);
    CREATE UNIQUE INDEX event_sequence ON events(service,instance,sequence);
    CREATE TABLE counters (name TEXT PRIMARY KEY,value INTEGER);
"""


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.path = self.root / "ledger.sqlite"
        self.policy = Policy(SNAPSHOT)

    def open(self):
        ledger = Ledger(self.path, self.policy)
        self.addCleanup(ledger.close)
        return ledger

    def legacy(self, records):
        with closing(sqlite3.connect(self.path)) as db:
            db.executescript(LEGACY_SCHEMA)
            db.execute("INSERT INTO files VALUES ('platform/source.jsonl','identity',123)")
            db.execute("INSERT INTO counters VALUES ('identity_conflicts',7)")
            for index, record in enumerate(records):
                db.execute(
                    "INSERT INTO events VALUES (?,?,?,?,?,?,?,?)",
                    (record["event_id"], record["service"], record["instance_id"],
                     record["sequence"], digest(record), 100.0 + index,
                     0 if index == 0 else 200.0, 300.0 + index),
                )
            db.commit()

    def test_upgrade_preserves_all_evidence_and_backfills_aggregates(self):
        records = [event(2), event(4), event(1, "gateway")]
        self.legacy(records)
        ledger = self.open()
        self.assertEqual(ledger.db.execute("PRAGMA user_version").fetchone()[0], 1)
        self.assertEqual(ledger.db.execute("SELECT * FROM files").fetchall(),
                         [("platform/source.jsonl", "identity", 123)])
        self.assertEqual(ledger.db.execute("SELECT * FROM counters").fetchall(),
                         [("identity_conflicts", 7)])
        actual = ledger.db.execute("SELECT * FROM events ORDER BY first_seen").fetchall()
        for index, row in enumerate(actual):
            self.assertEqual(row, (records[index]["event_id"], records[index]["service"],
                                  records[index]["instance_id"], records[index]["sequence"],
                                  digest_bytes(records[index]), 100.0 + index,
                                  0 if index == 0 else 200.0, 300.0 + index))
        metrics = ledger.metrics()
        self.assertEqual((metrics["landed_events"], metrics["pending_events"],
                          metrics["sequence_gaps"]), (3, 1, 2))
        ledger.close()
        reopened = self.open()
        self.assertEqual(reopened.db.execute("SELECT landed,pending FROM totals").fetchone(),
                         (3, 1))
        self.assertEqual(reopened.db.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_invalid_old_hash_rolls_back_entire_upgrade(self):
        self.legacy([event(1), event(2)])
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("UPDATE events SET hash='invalid' WHERE sequence=2")
            db.commit()
            before = db.execute("SELECT * FROM events ORDER BY sequence").fetchall()
        with self.assertRaises(ValueError):
            Ledger(self.path, self.policy)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT * FROM events ORDER BY sequence").fetchall(), before)
            self.assertIsNone(db.execute(
                "SELECT name FROM sqlite_master WHERE name='totals'").fetchone())
            self.assertIn("verified", db.execute(
                "SELECT sql FROM sqlite_master WHERE name='event_pending'").fetchone()[0])

    def test_scan_rollback_does_not_publish_partial_aggregates(self):
        records = [event(2), event(4)]
        emit(self.root / "source.jsonl", records)
        ledger = self.open()
        with patch.object(self.policy, "line", side_effect=[records[0], RuntimeError("io")]):
            with self.assertRaises(RuntimeError):
                ledger.scan({"platform": self.root})
        self.assertEqual(ledger.db.execute("SELECT landed,pending FROM totals").fetchone(), (0, 0))
        self.assertEqual(ledger.db.execute("SELECT * FROM streams").fetchall(), [])
        self.assertEqual(ledger.db.execute("SELECT * FROM files").fetchall(), [])
        ledger.scan({"platform": self.root})
        self.assertEqual(ledger.metrics()["sequence_gaps"], 2)

    def test_duplicate_conflict_and_out_of_order_gap_fill_do_not_inflate_totals(self):
        first, later, gap_fill = event(1), event(3), event(2)
        conflict = {**first, "outcome": "failed"}
        emit(self.root / "first.jsonl", [first, later, first, conflict])
        ledger = self.open()
        ledger.scan({"platform": self.root})
        self.assertEqual(ledger.metrics()["landed_events"], 2)
        self.assertEqual(ledger.metrics()["sequence_gaps"], 1)
        self.assertEqual(ledger.metrics()["source_duplicates_total"], 2)
        self.assertEqual(ledger.metrics()["identity_conflicts_total"], 1)
        emit(self.root / "second.jsonl", [gap_fill, event(3)])
        ledger.scan({"platform": self.root})
        self.assertEqual(ledger.metrics()["landed_events"], 3)
        self.assertEqual(ledger.metrics()["sequence_gaps"], 0)
        self.assertEqual(ledger.metrics()["identity_conflicts_total"], 2)

    def test_binary_hashes_verify_retrieval_and_central_loss_updates_pending(self):
        record = event()
        emit(self.root / "source.jsonl", [record])
        ledger = self.open()
        ledger.scan({"platform": self.root})
        self.assertEqual(ledger.db.execute("SELECT typeof(hash),length(hash) FROM events").fetchone(),
                         ("blob", 32))
        client = Mock()
        client.range.return_value = [(1, canonical(record).decode())]
        ledger.reconcile(client)
        self.assertEqual(ledger.metrics()["pending_events"], 0)
        ledger.db.execute("UPDATE events SET last_checked=?", (time.time() - 3601,))
        ledger.db.commit()
        client.range.return_value = []
        ledger.reconcile(client)
        self.assertEqual(ledger.metrics()["pending_events"], 1)
        client.range.return_value = [(1, canonical(record).decode())]
        ledger.reconcile(client)
        self.assertEqual(ledger.metrics()["pending_events"], 0)

    def test_confirmed_and_pending_queues_remain_fair_and_indexed(self):
        records = [event(i) for i in range(1, 7)]
        emit(self.root / "source.jsonl", records)
        ledger = self.open()
        ledger.scan({"platform": self.root})
        now = time.time()
        with ledger.db:
            for index, record in enumerate(records):
                ledger.db.execute("UPDATE events SET verified=?,last_checked=? WHERE id=?",
                                  (0 if index < 2 else now, now - 7200 + index,
                                   record["event_id"]))
        client = Mock()
        client.range.return_value = []
        ledger.reconcile(client, batch=4)
        first_query = client.range.call_args.args[0]
        self.assertTrue(all(record["event_id"] in first_query for record in records[:4]))
        ledger.reconcile(client, batch=4)
        second_query = client.range.call_args.args[0]
        self.assertTrue(all(record["event_id"] in second_query for record in records[4:]))
        for sql, parameters, name in (
            ("SELECT id FROM events WHERE verified=0 ORDER BY last_checked,first_seen LIMIT ?", (2,), "event_pending"),
            ("SELECT id FROM events WHERE verified>0 AND last_checked<? AND first_seen>? ORDER BY last_checked LIMIT ?", (now - 3600, now - 2592000, 2), "event_confirmed"),
            ("SELECT first_seen FROM events WHERE verified=0 ORDER BY first_seen LIMIT 1", (), "event_pending_age"),
        ):
            plan = " ".join(row[-1] for row in ledger.db.execute("EXPLAIN QUERY PLAN " + sql, parameters))
            self.assertIn(name, plan)
            self.assertNotIn("TEMP B-TREE", plan)


if __name__ == "__main__":
    unittest.main()
