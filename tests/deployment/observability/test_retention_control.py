"""Real TLS operator authorization and durable clear/restart/replay behavior."""

import json
import time

from helpers import SNAPSHOT, emit, event
from test_guard_tls import TransportFixture

# isort: split
from monitor import Ledger
from policy import Policy
from source_retention import prune_sources


class RetentionControlTests(TransportFixture):
    def test_operator_clear_is_writer_only_and_durable(self):
        for role in ("query", "metrics"):
            self.assertEqual(
                self.client(role).request("/retention/clear", "POST", body=b"")[0], 401
            )
        self.assertEqual(
            self.client("writer").request("/retention/clear", "POST", body=b"{}")[0],
            400,
        )
        status, body, _ = self.client("writer").request(
            "/retention/clear", "POST", body=b""
        )
        self.assertEqual(status, 202)
        cutoff = json.loads(body)["before"]
        state = json.loads((self.root / "clear-control.json").read_text())
        self.assertEqual(state["cutoff_ms"], int(cutoff * 1000))

    def test_clear_pending_old_unscanned_and_replayed_records_do_not_return_after_restart(
        self,
    ):
        source = self.root / "logs"
        source.mkdir()
        first = event(1)
        second = event(2)
        native = source / f"platform-{first['instance_id']}-0000.jsonl"
        emit(native, [first, second])
        path = self.root / "ledger.sqlite"
        ledger = Ledger(path, Policy(SNAPSHOT), max_events=10000)
        ledger.scan({"platform": source}, line_budget=1)
        self.assertEqual(ledger.metrics()["pending_events"], 1)
        cutoff = time.time()
        with ledger.db:
            ledger.db.execute("UPDATE retention_control SET clear_before=?", (cutoff,))
        ledger.scan({"platform": source})
        self.assertEqual(ledger.metrics()["landed_events"], 0)
        self.assertEqual(ledger.metrics()["sequence_gaps"], 0)
        self.assertEqual(ledger.metrics()["manual_cleared_events_total"], 1)
        self.assertEqual(ledger.metrics()["manual_cleared_replays_total"], 1)
        ledger.close()
        ledger = Ledger(path, Policy(SNAPSHOT), max_events=10000)
        try:
            # Lost producer/checkpoint state can replay both old records, while
            # the source's genuinely new third event remains usable.
            replay = source / f"platform-{first['instance_id']}-0001.jsonl"
            third = event(3)
            emit(replay, [first, second, third])
            ledger.scan({"platform": source})
            self.assertEqual(
                ledger.db.execute("SELECT id FROM events").fetchall(),
                [(third["event_id"],)],
            )
            self.assertEqual(ledger.metrics()["sequence_gaps"], 0)
            self.assertEqual(ledger.metrics()["pending_events"], 1)
            metrics, _ = prune_sources(ledger, {"platform": source})
            self.assertFalse(native.exists())
            self.assertTrue(replay.exists())
            self.assertEqual(metrics["source_retention_deleted_segments"], 1)
        finally:
            ledger.close()


if __name__ == "__main__":
    __import__("unittest").main()
