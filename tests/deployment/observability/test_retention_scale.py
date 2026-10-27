"""One real Monitor load at the requested global 10,000-event boundary."""

import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

from helpers import SNAPSHOT, emit, event

# isort: split
from alerts import conditions
from monitor import Ledger
from policy import Policy
from retained_query import window_filter
from source_retention import prune_sources


class GlobalWindowTests(unittest.TestCase):
    def test_full_primary_scan_and_refill_do_not_starve_other_sources(self):
        for count in (2, 5):
            with (
                self.subTest(sources=count),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = Path(temporary)
                sources, targets, expected, seen = {}, {}, set(), set()
                names = ("platform", "gateway", "memory", "knowledge", "companion")[
                    :count
                ]
                for name in names:
                    directory = root / name
                    directory.mkdir()
                    sources[name] = directory
                    instance = str(uuid.uuid4())
                    filename = (
                        f"platform-{instance}-0000.jsonl"
                        if name == "platform"
                        else f"gateway-{instance}-000000.jsonl"
                        if name == "gateway"
                        else "companion.jsonl"
                        if name == "companion"
                        else f"{instance}.000000.jsonl"
                    )
                    targets[name] = directory / filename
                    records = [
                        event(
                            sequence,
                            "memory-knowledge" if name == "knowledge" else name,
                            instance,
                        )
                        for sequence in range(1, count + 2 if name == "platform" else 2)
                    ]
                    emit(targets[name], records)
                    expected.add(records[0]["event_id"])
                path = root / "ledger.sqlite"
                ledger = Ledger(path, Policy(SNAPSHOT), max_events=count)
                try:
                    ledger.scan(sources)
                    seen.update(
                        row[0] for row in ledger.db.execute("SELECT id FROM events")
                    )
                    platform_instance = ledger.db.execute(
                        "SELECT instance FROM events LIMIT 1"
                    ).fetchone()[0]
                    for iteration in range(10):
                        emit(
                            targets["platform"],
                            [
                                event(
                                    count + 2 + iteration, "platform", platform_instance
                                )
                            ],
                        )
                        ledger.scan(sources)
                        with ledger.db:
                            ledger.db.execute(
                                "UPDATE events SET verified=?,collected=?",
                                (time.time(), time.time()),
                            )
                        _, coverage = prune_sources(ledger, sources)
                        ledger.expire(30, coverage=coverage, reserve=1)
                        free = count - ledger.metrics()["landed_events"]
                        if free:
                            ledger.scan(sources, line_budget=free)
                        seen.update(
                            row[0] for row in ledger.db.execute("SELECT id FROM events")
                        )
                        self.assertLessEqual(ledger.metrics()["landed_events"], count)
                        if iteration == 2:
                            ledger.close()
                            ledger = Ledger(path, Policy(SNAPSHOT), max_events=count)
                    self.assertTrue(expected <= seen)
                    for name in names[1:]:
                        self.assertTrue(
                            ledger.db.execute(
                                "SELECT offset FROM files WHERE name=?",
                                (name + "/" + targets[name].name,),
                            ).fetchone()[0]
                            > 0
                        )
                finally:
                    ledger.close()

    def test_ten_thousand_real_scan_then_automatic_oldest_retirement_and_new_admission(
        self,
    ):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sources, records = {}, []
            for index, source in enumerate(
                ("platform", "gateway", "memory", "knowledge", "companion")
            ):
                directory = root / source
                directory.mkdir()
                sources[source] = directory
                instance = str(uuid.UUID(int=index + 1))
                name = (
                    f"platform-{instance}-0000.jsonl"
                    if source == "platform"
                    else f"gateway-{instance}-000000.jsonl"
                    if source == "gateway"
                    else "companion.jsonl"
                    if source == "companion"
                    else f"{instance}.000000.jsonl"
                )
                batch = [
                    event(
                        sequence,
                        "memory-knowledge" if source == "knowledge" else source,
                        instance,
                    )
                    for sequence in range(1, 2001)
                ]
                emit(directory / name, batch)
                records.extend(batch)
            ledger = Ledger(root / "ledger.sqlite", Policy(SNAPSHOT))
            try:
                ledger.scan(sources)
                self.assertEqual(ledger.metrics()["landed_events"], 10000)
                with ledger.db:
                    ledger.db.execute(
                        "UPDATE events SET verified=?,collected=?",
                        (time.time(), time.time() - 1),
                    )
                incoming = [
                    event(sequence, "platform", records[0]["instance_id"])
                    for sequence in range(2001, 3001)
                ]
                emit(next(sources["platform"].glob("*.jsonl")), incoming)
                ledger.scan(sources)
                self.assertEqual(
                    ledger.metrics()["ledger_capacity_rejected_current"], 1
                )
                _, coverage = prune_sources(ledger, sources)
                result = ledger.expire(30, coverage=coverage, reserve=1000)
                self.assertEqual(result["retention_expired_events"], 1000)
                ledger.scan(sources, line_budget=1000)
                values = ledger.metrics()
                self.assertEqual(
                    (
                        values["landed_events"],
                        values["pending_events"],
                        values["sequence_gaps"],
                        values["ledger_capacity_rejected_current"],
                    ),
                    (10000, 1000, 0, 0),
                )
                self.assertEqual(values["ledger_capacity_rejected_total"], 1)
                self.assertFalse(conditions(values)["ledger_capacity"])
                self.assertTrue(
                    conditions({**values, "source_retention_failures": 1})[
                        "ledger_capacity"
                    ]
                )
                self.assertEqual(
                    {row[0] for row in ledger.db.execute("SELECT id FROM events")},
                    {row["event_id"] for row in records[1000:] + incoming},
                )
                self.assertLess(len(window_filter(ledger.retained_window())), 2000)
                self.assertEqual(
                    ledger.db.execute(
                        "SELECT count(*) FROM events WHERE length(hash)=32"
                    ).fetchone()[0],
                    10000,
                )
                client = Mock()
                client.request.return_value = (503, b"", "application/json")
                with self.assertRaises(RuntimeError):
                    ledger.submit_deletions(client)
                self.assertEqual(
                    ledger.db.execute(
                        "SELECT submitted FROM retention_deletes"
                    ).fetchone()[0],
                    0,
                )
                client.request.return_value = (204, b"", "application/json")
                ledger.submit_deletions(client)
                parameters = parse_qs(urlsplit(client.request.call_args.args[0]).query)
                self.assertGreater(float(parameters["start"][0]), time.time() - 10)
                self.assertIn("sequence<=1000", parameters["query"][0])
                client.reset_mock()
                ledger.submit_deletions(client)
                client.request.assert_not_called()
            finally:
                ledger.close()


if __name__ == "__main__":
    unittest.main()
