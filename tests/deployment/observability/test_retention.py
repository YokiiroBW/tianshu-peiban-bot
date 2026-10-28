"""Configured retention must reclaim confirmed prefixes without inventing gaps."""

import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from alerts import AlertStore
from configs import alert_rules, loki, vector
from configure import prepare
from helpers import SNAPSHOT, deployment_fixture, emit, event
from monitor import Ledger
from policy import Policy
from retention import apply_retention, days


class LedgerRetentionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.path = self.root / "ledger.sqlite"
        self.ledger = Ledger(self.path, Policy(SNAPSHOT), max_events=20)
        self.addCleanup(lambda: self.ledger.close())
        self.roots = {"platform": self.root}
        self.records = [event(i) for i in range(1, 7)]
        emit(self.root / "a.jsonl", self.records)
        self.ledger.scan(self.roots)
        with self.ledger.db:
            self.ledger.db.execute("UPDATE events SET first_seen=100,verified=200")

    def rows(self):
        return {
            name: self.ledger.db.execute(
                "SELECT * FROM " + name + " ORDER BY 1"
            ).fetchall()
            for name in (
                "files",
                "events",
                "totals",
                "streams",
                "retired_streams",
                "counters",
            )
        }

    def test_expired_prefix_reopen_replay_and_new_records_keep_real_gaps(self):
        before_files = self.rows()["files"]
        self.assertEqual(
            self.ledger.expire(1, now=86400 + 101, event_budget=4),
            {"retention_expired_events": 4},
        )
        values = self.ledger.metrics()
        self.assertEqual(
            (
                values["landed_events"],
                values["pending_events"],
                values["retired_events"],
                values["sequence_gaps"],
            ),
            (2, 0, 4, 0),
        )
        self.assertEqual(self.rows()["files"], before_files)
        self.ledger.close()
        self.ledger = Ledger(self.path, Policy(SNAPSHOT), max_events=2)
        emit(self.root / "z.jsonl", [*self.records[:4], event(8)])
        self.ledger.scan(self.roots)
        self.assertEqual(self.ledger.metrics()["retired_replays_total"], 4)
        self.assertEqual(self.ledger.metrics()["ledger_capacity_rejected_current"], 1)
        self.ledger.expire(1, now=86400 + 101)
        self.assertEqual(self.ledger.metrics()["retired_events"], 6)
        self.ledger.scan(self.roots)
        self.assertEqual(self.ledger.metrics()["landed_events"], 1)
        self.assertEqual(self.ledger.metrics()["sequence_gaps"], 1)
        self.assertEqual(
            self.ledger.db.execute("SELECT sequence FROM events").fetchall(), [(8,)]
        )
        emit(self.root / "zz.jsonl", [event(7)])
        self.ledger.scan(self.roots)
        self.assertEqual(self.ledger.metrics()["sequence_gaps"], 0)

    def test_pending_gap_and_retained_event_each_stop_prefix(self):
        for blocker in ("pending", "gap", "recent"):
            with self.subTest(blocker=blocker):
                copy = self.root / (blocker + ".sqlite")
                with closing(sqlite3.connect(copy)) as db:
                    self.ledger.db.backup(db)
                with closing(Ledger(copy, Policy(SNAPSHOT))) as trial:
                    with trial.db:
                        if blocker == "pending":
                            trial.db.execute(
                                "UPDATE events SET verified=0 WHERE sequence=3"
                            )
                        elif blocker == "gap":
                            trial.db.execute("DELETE FROM events WHERE sequence=3")
                        else:
                            trial.db.execute(
                                "UPDATE events SET first_seen=86401 WHERE sequence=3"
                            )
                    gaps = trial.metrics()["sequence_gaps"]
                    trial.expire(1, now=86400 + 101)
                    self.assertEqual(trial.metrics()["retired_events"], 2)
                    self.assertEqual(trial.metrics()["sequence_gaps"], gaps)
                    self.assertTrue(
                        trial.db.execute(
                            "SELECT 1 FROM events WHERE sequence=4"
                        ).fetchone()
                    )

    def test_expiry_failure_rolls_back_watermark_events_counts_and_offsets(self):
        before = self.rows()
        self.ledger.db.executescript("""
            CREATE TRIGGER synthetic_failure BEFORE DELETE ON events WHEN OLD.sequence=3
            BEGIN SELECT RAISE(ABORT,'synthetic delete failure'); END;
        """)
        with self.assertRaises(sqlite3.IntegrityError):
            self.ledger.expire(1, now=86400 + 101)
        self.assertEqual(self.rows(), before)
        self.ledger.db.execute("DROP TRIGGER synthetic_failure")
        self.ledger.expire(1, now=86400 + 101)
        self.assertEqual(self.ledger.metrics()["landed_events"], 0)
        self.assertEqual(self.ledger.metrics()["sequence_gaps"], 0)
        self.assertEqual(self.ledger.metrics()["expired_events_total"], 6)

    def test_retention_starts_at_first_landing_not_original_event_timestamp(self):
        with self.ledger.db:
            self.ledger.db.execute("UPDATE events SET first_seen=86400")
        self.assertEqual(
            self.ledger.expire(1, now=86400 + 100)["retention_expired_events"], 0
        )
        self.assertEqual(self.ledger.metrics()["landed_events"], 6)

    def test_v2_upgrade_preserves_all_evidence_before_enabling_retirement(self):
        before = self.rows()
        before["events"] = [(*row[:8], None, None) for row in before["events"]]
        with self.ledger.db:
            self.ledger.db.execute("ALTER TABLE events DROP COLUMN occurred")
            self.ledger.db.execute("ALTER TABLE events DROP COLUMN collected")
            for name in (
                "retired_streams",
                "source_receipts",
                "retention_control",
                "retention_deletes",
                "sequence_conflicts",
            ):
                self.ledger.db.execute("DROP TABLE " + name)
            self.ledger.db.execute("PRAGMA user_version=2")
        self.ledger.close()
        self.ledger = Ledger(self.path, Policy(SNAPSHOT))
        self.assertEqual(self.ledger.db.execute("PRAGMA user_version").fetchone()[0], 4)
        self.assertEqual(self.rows(), before)
        self.ledger.expire(1, now=86400 + 101)
        self.assertEqual(self.ledger.metrics()["retired_events"], 6)

    def test_later_stream_can_expire_while_first_stream_is_blocked(self):
        other = self.root / "gateway"
        other.mkdir()
        emit(other / "a.jsonl", [event(1, "gateway")])
        self.ledger.scan({"gateway": other})
        with self.ledger.db:
            self.ledger.db.execute(
                "UPDATE events SET verified=0 WHERE service='platform'"
            )
            self.ledger.db.execute(
                "UPDATE events SET first_seen=100,verified=200 WHERE service='gateway'"
            )
        self.ledger.expire(1, now=86400 + 101)
        self.assertEqual(self.ledger.metrics()["retired_events"], 1)
        self.assertEqual(self.ledger.metrics()["pending_events"], 6)


class PolicyRetentionTests(unittest.TestCase):
    def test_one_policy_projects_captured_configs_and_preserves_unrelated_fields(self):
        guard = {
            "tokens": {"query": "PRIVATE_LITERAL"},
            "ledger": "/data/ledger.sqlite",
        }
        central = loki()
        rules = alert_rules()
        for value in (1, 7, 30, 90):
            projected_guard, projected_loki = apply_retention(guard, central, value)
            self.assertEqual(projected_guard["retention_days"], value)
            self.assertEqual(
                projected_loki["limits_config"]["retention_period"], f"{24 * value}h"
            )
            self.assertEqual(projected_loki, loki(retention_days=value))
            self.assertEqual(projected_guard["tokens"], guard["tokens"])
            self.assertEqual(projected_loki["server"], central["server"])
        self.assertNotIn("retention_days", guard)
        self.assertEqual(central, loki())
        self.assertEqual(rules, alert_rules())
        self.assertEqual(len(rules["groups"][0]["rules"]), 15)
        self.assertNotIn("remove_after_secs", json.dumps(vector(SNAPSHOT)))
        for bad in (0, -1, 1.5, "7", True, None):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                days(bad)

    def test_alert_history_uses_same_days_without_deleting_current_state(self):
        with (
            tempfile.TemporaryDirectory() as temporary,
            closing(
                AlertStore(Path(temporary) / "alerts.sqlite", retention_days=2)
            ) as store,
        ):
            store.update({"monitor_success": 0}, now=1)
            store.update({"monitor_success": 1}, now=2 * 86400 + 2)
            self.assertFalse(
                store.db.execute("SELECT 1 FROM history WHERE time=1").fetchone()
            )
            self.assertTrue(
                store.db.execute(
                    "SELECT 1 FROM current WHERE name='monitor_unavailable'"
                ).fetchone()
            )

    def test_existing_bundle_settings_is_the_single_config_entry(self):
        contract = os.environ.get("DEP_B_CONTRACT")
        if not contract:
            self.skipTest("DEP_B_CONTRACT required")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest, settings = deployment_fixture(root, Path(contract))
            settings["retention_days"] = 7
            bundle = prepare(
                root, settings, manifest, "bundle", SNAPSHOT, candidate=True
            )
            self.assertEqual(
                json.loads((bundle / "config/guard.json").read_bytes())[
                    "retention_days"
                ],
                7,
            )
            self.assertEqual(
                json.loads((bundle / "config/loki.json").read_bytes())["limits_config"][
                    "retention_period"
                ],
                "168h",
            )
            self.assertTrue((bundle / "code/retention.py").is_file())
            settings["retention_days"] = 0
            with self.assertRaises(ValueError):
                prepare(root, settings, manifest, "bad", SNAPSHOT, candidate=True)
            self.assertFalse((root / "bad").exists())


if __name__ == "__main__":
    unittest.main()
