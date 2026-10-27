"""Ledger upgrade, transactional aggregates and bounded indexed revisit queues."""

import json
import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from helpers import SNAPSHOT, emit, event
from ledger_schema import VERSION
from monitor import Ledger
from policy import Policy, canonical, digest, digest_bytes
from query import LokiClient, QueryError

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
        self.assertEqual(ledger.db.execute("PRAGMA user_version").fetchone()[0], VERSION)
        self.assertEqual(ledger.db.execute("SELECT * FROM files").fetchall(),
                         [("platform/source.jsonl", "identity", 123)])
        self.assertEqual(ledger.db.execute("SELECT * FROM counters").fetchall(),
                         [("identity_conflicts", 7)])
        actual = ledger.db.execute("SELECT id,service,instance,sequence,hash,first_seen,verified,last_checked FROM events ORDER BY first_seen").fetchall()
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
        with (patch.object(self.policy, "line", side_effect=[records[0], RuntimeError("io")]),
              self.assertRaises(RuntimeError)):
            ledger.scan({"platform": self.root})
        self.assertEqual(ledger.db.execute("SELECT landed,pending FROM totals").fetchone(), (0, 0))
        self.assertEqual(ledger.db.execute("SELECT * FROM streams").fetchall(), [])
        self.assertEqual(ledger.db.execute("SELECT * FROM files").fetchall(), [])
        ledger.scan({"platform": self.root})
        self.assertEqual(ledger.metrics()["sequence_gaps"], 2)

    def test_v1_upgrade_preserves_evidence_and_removes_old_insert_maintenance(self):
        records = [event(1), event(3)]
        emit(self.root / "source.jsonl", records)
        ledger = self.open()
        ledger.scan({"platform": self.root})
        with ledger.db:
            ledger.db.execute("UPDATE events SET verified=100,last_checked=200 WHERE sequence=1")
            # v1 has the same tables/indexes and these original insert-trigger bytes.
            ledger.db.executescript("""
                ALTER TABLE events DROP COLUMN occurred;
                ALTER TABLE events DROP COLUMN collected;
                DROP TABLE retired_streams;
                DROP TABLE source_receipts;
                DROP TABLE retention_control;
                DROP TABLE retention_deletes;
                DROP TABLE sequence_conflicts;
                CREATE TRIGGER event_insert AFTER INSERT ON events BEGIN
                    UPDATE totals SET landed=landed+1,pending=pending+(NEW.verified=0) WHERE singleton=1;
                    INSERT INTO streams VALUES(NEW.service,NEW.instance,1,NEW.sequence)
                        ON CONFLICT(service,instance) DO UPDATE SET
                            landed=landed+1,maximum=max(maximum,excluded.maximum);
                END;
                PRAGMA user_version=1;
            """)
        before = {name: ledger.db.execute(f"SELECT * FROM {name}").fetchall()
                  for name in ("events", "files", "counters", "totals", "streams")}
        ledger.close()
        reopened = self.open()
        self.assertEqual(reopened.db.execute("PRAGMA user_version").fetchone()[0], VERSION)
        self.assertIsNone(reopened.db.execute(
            "SELECT name FROM sqlite_master WHERE name='event_insert'").fetchone())
        for name, rows in before.items():
            actual = reopened.db.execute(f"SELECT * FROM {name}").fetchall()
            self.assertEqual([row[:8] for row in actual] if name == "events" else actual, rows)
        emit(self.root / "source.jsonl", [event(2)])
        reopened.scan({"platform": self.root})
        metrics = reopened.metrics()
        self.assertEqual((metrics["landed_events"], metrics["pending_events"],
                          metrics["sequence_gaps"]), (3, 2, 0))

    def test_early_scan_returns_commit_only_accepted_events_and_replay_offsets(self):
        for reason in ("budget", "capacity"):
            with self.subTest(reason=reason):
                root = self.root / reason
                root.mkdir()
                emit(root / "a.jsonl", [event(1), event(2)])
                emit(root / "b.jsonl", [event(3)])
                with closing(Ledger(root / "ledger.sqlite", self.policy,
                                    max_events=1 if reason == "capacity" else 10)) as ledger:
                    result = ledger.scan({"platform": root},
                                         line_budget=1 if reason == "budget" else 20_000)
                    self.assertEqual(result["scan_budget_exhausted"], 1)
                    self.assertEqual(ledger.db.execute("SELECT landed,pending FROM totals").fetchone(),
                                     (1, 1))
                    self.assertEqual(ledger.db.execute("SELECT landed,maximum FROM streams").fetchall(),
                                     [(1, 1)])
                    ledger.max_events = 10
                    ledger.scan({"platform": root})
                    self.assertEqual(ledger.metrics()["landed_events"], 3)
                    self.assertEqual(ledger.metrics()["sequence_gaps"], 0)

    def test_summary_failure_rolls_back_events_offsets_and_counters_together(self):
        record = event()
        emit(self.root / "source.jsonl", [record, record])
        ledger = self.open()
        ledger.db.executescript("""
            CREATE TRIGGER summary_failure BEFORE INSERT ON streams BEGIN
                SELECT RAISE(ABORT,'synthetic summary failure');
            END;
        """)
        with self.assertRaises(sqlite3.IntegrityError):
            ledger.scan({"platform": self.root})
        for table in ("events", "files", "counters", "streams"):
            self.assertEqual(ledger.db.execute(f"SELECT * FROM {table}").fetchall(), [])
        self.assertEqual(ledger.db.execute("SELECT landed,pending FROM totals").fetchone(), (0, 0))
        ledger.db.execute("DROP TRIGGER summary_failure")
        ledger.scan({"platform": self.root})
        self.assertEqual(ledger.metrics()["landed_events"], 1)
        self.assertEqual(ledger.metrics()["source_duplicates_total"], 1)

    def test_batch_summary_uses_actual_memory_service_and_instance(self):
        other = "00000000-0000-4000-8000-000000000002"
        emit(self.root / "source.jsonl",
             [event(1, "memory"), event(3, "memory-knowledge"),
              event(2, "memory-knowledge", instance=other)])
        ledger = self.open()
        ledger.scan({"memory": self.root})
        self.assertEqual(ledger.metrics()["sequence_gaps"], 3)
        self.assertEqual(ledger.db.execute(
            "SELECT service,instance,landed,maximum FROM streams ORDER BY service,instance").fetchall(),
            [("memory", "00000000-0000-4000-8000-000000000001", 1, 1),
             ("memory-knowledge", "00000000-0000-4000-8000-000000000001", 1, 3),
             ("memory-knowledge", other, 1, 2)])

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

    def test_id_and_sequence_conflicts_keep_original_id_classification(self):
        first, second = event(1), event(2)
        emit(self.root / "source.jsonl",
             [first, second, {**first, "sequence": 2}, event(2)])
        ledger = self.open()
        ledger.scan({"platform": self.root})
        metrics = ledger.metrics()
        self.assertEqual((metrics["landed_events"], metrics["pending_events"],
                          metrics["sequence_gaps"]), (2, 2, 0))
        self.assertEqual(metrics["source_duplicates_total"], 1)
        self.assertEqual(metrics["identity_conflicts_total"], 2)

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
            ("SELECT id FROM events WHERE verified=0 ORDER BY CASE WHEN last_checked=0 THEN first_seen ELSE last_checked END,first_seen LIMIT ?", (2,), "event_pending"),
            ("SELECT id FROM events WHERE verified>0 AND last_checked<? AND first_seen>? ORDER BY last_checked LIMIT ?", (now - 3600, now - 2592000, 2), "event_confirmed"),
            ("SELECT first_seen FROM events WHERE verified=0 ORDER BY first_seen LIMIT 1", (), "event_pending_age"),
        ):
            plan = " ".join(row[-1] for row in ledger.db.execute("EXPLAIN QUERY PLAN " + sql, parameters))
            self.assertIn(name, plan)
            # Pending priority is an age expression over at most the retention cap.
            if name != "event_pending":
                self.assertNotIn("TEMP B-TREE", plan)

    def test_full_reconcile_batch_uses_one_complete_page_and_keeps_integrity(self):
        for count, case in ((1000, "complete"), (1024, "complete"),
                            (1024, "missing_changed"), (1024, "saturated")):
            with self.subTest(count=count, case=case):
                root = self.root / (str(count) + case)
                root.mkdir()
                records = [event(index) for index in range(1, count + 1)]
                emit(root / "source.jsonl", records)
                ledger = Ledger(root / "state.sqlite", self.policy)
                self.addCleanup(ledger.close)
                ledger.scan({"platform": root})
                now = time.time()
                with ledger.db:
                    ledger.db.execute(
                        "UPDATE events SET verified=?,last_checked=? WHERE sequence>?",
                        (now, now - 7200, count // 2),
                    )
                returned = list(records)
                if case == "missing_changed":
                    returned = [{**records[1], "outcome": "failed"}, *records[2:]]
                elif case == "saturated":
                    returned.append(records[0])
                stamp = int((now - 1) * 1e9)
                calls = []

                def request(path, *, deadline):
                    deadline.remaining()
                    args = parse_qs(urlsplit(path).query)
                    limit = int(args["limit"][0])
                    calls.append(limit)
                    in_window = int(args["start"][0]) <= stamp <= int(args["end"][0])
                    values = [
                        [str(stamp), canonical(record).decode()]
                        for record in returned[:limit]
                    ] if in_window else []
                    body = {"status": "success", "data": {
                        "resultType": "streams", "result": [{"values": values}],
                    }}
                    return 200, json.dumps(body).encode(), "application/json"

                client = object.__new__(LokiClient)
                client.request = request
                if case == "saturated":
                    with self.assertRaisesRegex(QueryError, "timestamp_bucket_saturated"):
                        ledger.reconcile(client, batch=count)
                    self.assertEqual(ledger.metrics()["pending_events"], count // 2)
                    self.assertGreater(len(calls), 1)
                else:
                    ledger.reconcile(client, batch=count)
                    self.assertEqual(calls, [count + 1])
                    self.assertEqual(ledger.metrics()["pending_events"],
                                     2 if case == "missing_changed" else 0)


    def _pending_age_fixture(self, *, count=3, checked=1):
        records = [event(index) for index in range(1, count + 1)]
        emit(self.root / "fairness.jsonl", records)
        ledger = self.open()
        ledger.scan({"platform": self.root})
        now = time.time()
        with ledger.db:
            ledger.db.execute(
                "UPDATE events SET first_seen=?+sequence*0.000001,last_checked=0",
                (now - 100,),
            )
            ledger.db.execute(
                "UPDATE events SET first_seen=?,last_checked=? WHERE sequence<=?",
                (now - 3000, now - 2000, checked),
            )
        return ledger, records, now

    def test_checked_pending_is_revisited_amid_a_full_cap_of_new_unchecked_events(self):
        ledger, records, now = self._pending_age_fixture(count=10000, checked=83)
        client = Mock()

        def available(selector, start, end, **kwargs):
            return [
                (int(now * 1e9), canonical(record).decode())
                for record in records
                if record["event_id"] in selector
            ]

        client.range.side_effect = available
        with patch("monitor.time.time", return_value=now):
            ledger.reconcile(client, batch=1024)
        selector = client.range.call_args.args[0]
        self.assertEqual(sum(record["event_id"] in selector for record in records), 512)
        self.assertTrue(all(record["event_id"] in selector for record in records[:83]))
        self.assertEqual(
            ledger.db.execute(
                "SELECT count(*) FROM events WHERE sequence<=83 AND verified>0 AND last_checked=?",
                (now,),
            ).fetchone()[0],
            83,
        )
        self.assertEqual(
            (ledger.metrics()["landed_events"], ledger.metrics()["pending_events"]),
            (10000, 10000 - 512),
        )
        self.assertEqual(client.range.call_args.kwargs["limit"], 1000)

    def test_unmatched_checked_pending_requeues_behind_new_events_then_retries(self):
        ledger, records, now = self._pending_age_fixture()
        old, first, second = records
        available_ids = {first["event_id"], second["event_id"]}
        client = Mock()

        def available(selector, start, end, **kwargs):
            return [
                (int(now * 1e9), canonical(record).decode())
                for record in records
                if record["event_id"] in selector and record["event_id"] in available_ids
            ]

        client.range.side_effect = available
        for step, expected in enumerate((old, first, second, old)):
            if step == 3:
                available_ids.add(old["event_id"])
            with patch("monitor.time.time", return_value=now + step):
                ledger.reconcile(client, batch=2)
            selector = client.range.call_args.args[0]
            self.assertEqual(
                [record["event_id"] for record in records if record["event_id"] in selector],
                [expected["event_id"]],
            )
            if step == 0:
                self.assertEqual(
                    ledger.db.execute(
                        "SELECT verified,last_checked FROM events WHERE id=?", (old["event_id"],)
                    ).fetchone(),
                    (0, now),
                )
        self.assertEqual(ledger.metrics()["pending_events"], 0)
        self.assertEqual(
            ledger.db.execute("SELECT last_checked FROM events WHERE id=?", (old["event_id"],)).fetchone()[0],
            now + 3,
        )

    def test_query_error_does_not_change_fairness_evidence_or_claim_verification(self):
        ledger, records, now = self._pending_age_fixture()
        before = ledger.db.execute(
            "SELECT id,first_seen,verified,last_checked,collected FROM events ORDER BY sequence"
        ).fetchall()
        client = Mock()
        client.range.side_effect = QueryError("query_deadline_exceeded")
        with patch("monitor.time.time", return_value=now):
            with self.assertRaisesRegex(QueryError, "query_deadline_exceeded"):
                ledger.reconcile(client, batch=2)
        self.assertIn(records[0]["event_id"], client.range.call_args.args[0])
        self.assertEqual(
            ledger.db.execute(
                "SELECT id,first_seen,verified,last_checked,collected FROM events ORDER BY sequence"
            ).fetchall(),
            before,
        )


if __name__ == "__main__":
    unittest.main()
