"""A full durable ledger must still consume known replay without losing new work."""

import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

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

    def test_pending_search_pages_collection_time_and_preserves_unseen_records(self):
        now = (int(time.time() // 3600) + 0.75) * 3600
        collected = int((now - 3 * 3600) * 1e9)
        calls = []

        class Backend:
            def range(inner, selector, start, end, *, limit, deadline):
                calls.append((selector, start, end))
                self.assertLessEqual(end - start, 3602 * 10**9)
                self.assertIn('service="tianshu_platform"', selector)
                self.assertLessEqual(deadline.remaining(), 10)
                return [
                    (collected, canonical(item).decode())
                    for item in (self.first, self.second)
                    if start <= collected <= end and item["event_id"] in selector
                ]

        with patch("monitor.time.time", return_value=now):
            backend = Backend()
            self.ledger.reconcile(backend)
            self.assertEqual(self.ledger.metrics()["pending_events"], 2)
            self.assertEqual(self.ledger.metrics()["landed_events"], 2)
            for _ in range(20):
                self.ledger.reconcile(backend)
                if self.ledger.metrics()["pending_events"] == 0:
                    break
            self.assertEqual(self.ledger.metrics()["pending_events"], 0)
        self.assertTrue(any(start <= collected <= end for _, start, end in calls))
        self.assertEqual(self.ledger.db.execute(
            "SELECT count(*) FROM retired_streams"
        ).fetchone()[0], 0)

    def test_confirmed_recheck_uses_recorded_collection_and_missing_becomes_pending(self):
        now = time.time()
        collected = now - 20 * 86400
        with self.ledger.db:
            self.ledger.db.execute(
                "UPDATE events SET verified=?,collected=?,last_checked=0", (now - 7200, collected)
            )
        calls = []

        class Backend:
            def range(inner, selector, start, end, **kwargs):
                calls.append((start, end))
                return []

        with patch("monitor.time.time", return_value=now):
            self.ledger.reconcile(Backend())
        self.assertTrue(all(start <= int(collected * 1e9) <= end for start, end in calls))
        self.assertTrue(all(end - start <= 3602 * 10**9 for start, end in calls))
        self.assertEqual(self.ledger.metrics()["pending_events"], 2)
        self.assertEqual(self.ledger.metrics()["landed_events"], 2)

    def test_legacy_confirmation_survives_partial_misses_and_restart_until_window_complete(self):
        now = (int(time.time() // 3600) + 0.75) * 3600
        verified = now - 7200
        collected = int((now - 2 * 3600) * 1e9)
        with self.ledger.db:
            self.ledger.db.execute(
                "UPDATE events SET first_seen=?,verified=?,collected=NULL,last_checked=0",
                (now - 100, verified),
            )
        calls = []

        class Backend:
            def range(inner, selector, start, end, **kwargs):
                calls.append((start, end))
                self.assertLessEqual(end - start, 3602 * 10**9)
                # One old confirmation exists in Loki, the other's whole window
                # is absent. Neither partial miss is evidence of central loss.
                return [(collected, canonical(self.first).decode())] if (
                    start <= collected <= end and self.first["event_id"] in selector
                ) else []

        with patch("monitor.time.time", return_value=now):
            self.ledger.reconcile(Backend(), retention_seconds=3 * 3600)
            self.assertEqual(self.ledger.metrics()["pending_events"], 0)
            self.assertEqual(self.ledger.db.execute(
                "SELECT verified,collected FROM events ORDER BY sequence"
            ).fetchall(), [(verified, None), (verified, None)])
            self.ledger.close()
            self.ledger = Ledger(self.root / "ledger.sqlite", Policy(SNAPSHOT), max_events=2)
            self.addCleanup(self.ledger.close)
            self.ledger.reconcile(Backend(), retention_seconds=3 * 3600)
            self.assertEqual(self.ledger.metrics()["pending_events"], 0)
            for _ in range(10):
                self.ledger.reconcile(Backend(), retention_seconds=3 * 3600)
                if self.ledger.metrics()["pending_events"] == 1:
                    break
            self.assertEqual(self.ledger.metrics()["pending_events"], 1)
            rows = self.ledger.db.execute(
                "SELECT verified,collected FROM events ORDER BY sequence"
            ).fetchall()
            self.assertEqual(rows, [(now, collected / 1e9), (0, None)])
            self.assertTrue(any(start <= int((now - 3 * 3600) * 1e9) <= end
                                for start, end in calls))
            self.assertEqual(self.ledger.metrics()["landed_events"], 2)
            self.assertEqual(self.ledger.db.execute(
                "SELECT count(*) FROM retired_streams"
            ).fetchone()[0], 0)


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
