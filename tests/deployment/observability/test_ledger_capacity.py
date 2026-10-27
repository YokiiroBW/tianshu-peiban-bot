"""A full durable ledger must still consume known replay without losing new work."""

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from helpers import SNAPSHOT, emit, event

# isort: split

from alerts import AlertStore, conditions
from configs import ALERTS, alert_rules
from monitor import Ledger
from policy import Policy, canonical


class CapacityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.first, self.second, self.third = event(1), event(2), event(3)
        self.ledger = Ledger(
            self.root / "ledger.sqlite", Policy(SNAPSHOT), max_events=2
        )
        self.addCleanup(self.ledger.close)
        self.roots = {"platform": self.root}
        emit(self.root / "a.jsonl", [self.first, self.second])
        self.ledger.scan(self.roots)

    def rows(self, table):
        return self.ledger.db.execute(
            "SELECT * FROM " + table + " ORDER BY 1"
        ).fetchall()

    def test_full_replay_and_conflicts_advance_until_first_real_new_event(self):
        replay = [self.first, {**self.first, "outcome": "failed"}, event(2)]
        path = self.root / "z-replay.jsonl"
        emit(path, [*replay, self.third])
        before = self.rows("events")
        result = self.ledger.scan(self.roots)
        self.assertEqual(result["scan_budget_exhausted"], 1)
        self.assertEqual(self.rows("events"), before)
        self.assertEqual(
            self.ledger.db.execute(
                "SELECT offset FROM files WHERE name='platform/z-replay.jsonl'"
            ).fetchone()[0],
            sum(len(canonical(row)) + 1 for row in replay),
        )
        values = self.ledger.metrics()
        self.assertEqual(
            (values["source_duplicates_total"], values["identity_conflicts_total"]),
            (2, 2),
        )
        self.assertEqual(
            (
                values["ledger_max_events"],
                values["ledger_capacity_rejected_current"],
                values["ledger_capacity_rejected_total"],
            ),
            (2, 1, 1),
        )
        self.ledger.scan(self.roots)
        self.assertEqual(self.ledger.metrics()["source_duplicates_total"], 2)
        self.assertEqual(self.ledger.metrics()["ledger_capacity_rejected_total"], 2)
        self.ledger.max_events = 4
        self.ledger.scan(self.roots)
        values = self.ledger.metrics()
        self.assertEqual(
            (
                values["landed_events"],
                values["ledger_max_events"],
                values["ledger_capacity_rejected_current"],
                values["ledger_capacity_rejected_total"],
            ),
            (3, 4, 0, 2),
        )
        self.assertEqual(
            self.ledger.db.execute(
                "SELECT offset FROM files WHERE name='platform/z-replay.jsonl'"
            ).fetchone()[0],
            path.stat().st_size,
        )
        self.assertFalse(conditions(values)["ledger_capacity"])

    def test_full_duplicate_replay_is_budgeted_and_survives_reopen(self):
        path = self.root / "z.jsonl"
        emit(path, [self.first, self.second])
        self.ledger.scan(self.roots, line_budget=1)
        self.assertEqual(self.ledger.metrics()["source_duplicates_total"], 1)
        self.assertEqual(self.ledger.metrics()["ledger_capacity_rejected_current"], 0)
        self.ledger.close()
        self.ledger = Ledger(
            self.root / "ledger.sqlite", Policy(SNAPSHOT), max_events=2
        )
        self.addCleanup(self.ledger.close)
        self.ledger.scan(self.roots)
        self.assertEqual(self.ledger.metrics()["source_duplicates_total"], 2)
        self.assertNotIn("ledger_capacity_rejected_total", self.ledger.metrics())
        self.assertEqual(
            self.ledger.db.execute(
                "SELECT offset FROM files WHERE name='platform/z.jsonl'"
            ).fetchone()[0],
            path.stat().st_size,
        )

    def test_full_invalid_and_partial_lines_do_not_become_capacity_rejections(self):
        path = self.root / "z.jsonl"
        path.write_bytes(
            b'{"secret":"SYNTHETIC_PRIVATE_CANARY"}\n' + canonical(self.third)[:-1]
        )
        self.ledger.scan(self.roots)
        values = self.ledger.metrics()
        self.assertEqual(values["invalid_source_lines_total"], 1)
        self.assertEqual(values["ledger_capacity_rejected_current"], 0)
        self.assertNotIn("ledger_capacity_rejected_total", values)
        self.assertNotIn(
            "SYNTHETIC_PRIVATE_CANARY", "\n".join(self.ledger.db.iterdump())
        )

    def test_full_replay_checkpoint_failure_rolls_back_counts_and_offsets(self):
        emit(self.root / "z.jsonl", [self.first, self.third])
        before = {
            table: self.rows(table)
            for table in ("events", "files", "counters", "totals", "streams")
        }
        self.ledger.db.executescript(
            "CREATE TRIGGER synthetic_failure BEFORE INSERT ON files WHEN NEW.name='platform/z.jsonl' BEGIN SELECT RAISE(ABORT,'synthetic checkpoint failure'); END;"
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.ledger.scan(self.roots)
        for table, rows in before.items():
            self.assertEqual(self.rows(table), rows)
        self.ledger.db.execute("DROP TRIGGER synthetic_failure")
        self.ledger.scan(self.roots)
        self.assertEqual(self.ledger.metrics()["source_duplicates_total"], 1)
        self.assertEqual(self.ledger.metrics()["ledger_capacity_rejected_total"], 1)

    def test_normal_new_event_fast_path_has_no_duplicate_or_sequence_read(self):
        self.ledger.max_events = 10
        emit(self.root / "z.jsonl", [self.third])
        queries = []
        self.ledger.db.set_trace_callback(queries.append)
        self.ledger.scan(self.roots)
        self.ledger.db.set_trace_callback(None)
        self.assertFalse(
            any(
                sql.startswith(
                    (
                        "SELECT hash FROM events WHERE id=",
                        "SELECT 1 FROM events WHERE service=",
                    )
                )
                for sql in queries
            )
        )
        self.assertEqual(self.ledger.metrics()["landed_events"], 3)


class CapacityAlertTests(unittest.TestCase):
    def test_configured_ratio_and_current_rejection_resolve_without_resetting_history(
        self,
    ):
        values = {
            "monitor_success": 1,
            "landed_events": 1_000_000,
            "ledger_max_events": 5_000_000,
            "ledger_capacity_rejected_total": 36116,
            "ledger_capacity_rejected_current": 0,
        }
        self.assertFalse(conditions(values)["ledger_capacity"])
        self.assertFalse(
            conditions({**values, "landed_events": 4_500_000})["ledger_capacity"]
        )
        self.assertFalse(
            conditions({**values, "landed_events": 4_499_999})["ledger_capacity"]
        )
        with (
            tempfile.TemporaryDirectory() as temporary,
            closing(AlertStore(Path(temporary) / "alerts.sqlite")) as store,
        ):
            self.assertEqual(
                store.update({**values, "ledger_capacity_rejected_current": 1})[
                    "alert_ledger_capacity"
                ],
                1,
            )
            self.assertEqual(store.update(values)["alert_ledger_capacity"], 0)
        self.assertEqual(values["ledger_capacity_rejected_total"], 36116)

    def test_original_fifteen_rules_keep_capacity_identity_and_use_live_budget(self):
        rules = alert_rules()["groups"][0]["rules"]
        self.assertEqual(len(rules), 15)
        rule = next(row for row in rules if row["title"] == "LedgerCapacity")
        expression = ALERTS["LedgerCapacity"][0]
        self.assertNotIn("tianshu_obs_ledger_max_events", expression)
        self.assertIn("tianshu_obs_source_retention_failures", expression)
        self.assertIn("tianshu_obs_retention_delete_failures_current", expression)
        self.assertIn("tianshu_obs_ledger_capacity_rejected_current", expression)
        self.assertNotIn("900000", expression)
        self.assertNotIn("ledger_capacity_rejected_total", expression)
        self.assertEqual(rule["uid"], "obs-ledgercapacity")
        self.assertEqual(rule["noDataState"], "Alerting")


if __name__ == "__main__":
    unittest.main()
