"""Local SQLite protocol smoke for the independent Knowledge reference study.

Requires an explicit immutable source export and public synthetic contracts.
Does not call common.arguments, an image, a PG URL, NAS or production config.
"""

import argparse
import importlib.util
import sys
import tempfile
import threading
import unittest
from pathlib import Path


class ReferenceChecks(unittest.TestCase):
    contracts = None

    def test_native_200_blocks_16_state_20_package_and_cpu_protocol(self):
        import knowledge_reference as study

        native_reference = study.KnowledgeApplication._reference
        native_read = study.knowledge_sources.read_file
        with tempfile.TemporaryDirectory(
            prefix="ts292-knowledge-reference-"
        ) as temporary:
            results, details = study.run_study(
                Path(temporary),
                self.contracts,
                {},
                "sqlite",
                "krcheck",
                count=2,
                warmup=1,
                cpu_clocks=True,
                concurrency=4,
            )
        self.assertIs(study.KnowledgeApplication._reference, native_reference)
        self.assertIs(study.knowledge_sources.read_file, native_read)
        self.assertEqual([row["operations"] for row in results], [2, 2])
        for row in results:
            self.assertEqual(len(row["latencies_ms"]), 2)
            self.assertEqual(len(row["process_cpu_ms"]), 2)
            self.assertEqual(len(row["thread_cpu_ms"]), 2)
            self.assertFalse(row["cpu_clock_protocol"]["profiling"])
        self.assertEqual(details["seed_blocks"], 200)
        self.assertEqual(details["state_evidence_refs"], 16)
        self.assertEqual(details["recovered_refs"], 20)
        self.assertEqual(details["query_extra_refs"], 4)
        self.assertEqual(details["storage_before"], details["storage_after"])
        self.assertEqual(
            details["storage_before"]["counts"]["knowledge_state_history"], 1
        )
        self.assertEqual(details["storage_before"]["durability"]["journal_mode"], "wal")
        self.assertEqual(details["storage_before"]["durability"]["synchronous"], 2)
        counts = details["untimed_call_counts"]
        self.assertEqual(counts["recover"]["reference_calls"], 72)
        self.assertEqual(counts["check"]["reference_calls"], 40)
        self.assertEqual(counts["recover"]["source_file_reads"], 4)
        self.assertEqual(counts["check"]["source_file_reads"], 4)
        concurrent = details["concurrent_results"]
        self.assertEqual(len(concurrent), 2)
        for row in concurrent:
            self.assertEqual(row["operations"], 2)
            self.assertEqual(row["logical_request_count"], 8)
            self.assertEqual(row["warmup_logical_request_count"], 4)
            self.assertEqual(len(row["process_cpu_ms"]), 2)
            self.assertEqual(len(row["worker_request_timings"]), 2)
            self.assertEqual(len(row["warmup_worker_request_timings"]), 1)
            for slots in (
                row["worker_request_timings"] + row["warmup_worker_request_timings"]
            ):
                self.assertEqual([slot["slot"] for slot in slots], [0, 1, 2, 3])
                for slot in slots:
                    self.assertIsNone(slot["error"])
                    self.assertGreaterEqual(slot["wall_ms"], 0)
                    self.assertGreaterEqual(slot["thread_cpu_ms"], 0)
                    self.assertNotIn("process_cpu_ms", slot)
        proof = details["native_negative_proofs"]
        self.assertEqual(proof["wrong_project"], "forbidden")
        self.assertEqual(proof["revoked_permission"], "forbidden")
        self.assertEqual(proof["wrong_credential"], "unauthorized")
        self.assertFalse(proof["other_client_seal"]["valid"])
        self.assertFalse(proof["changed_source"]["valid"])
        self.assertFalse(proof["changed_state_seal"]["valid"])
        self.assertFalse(proof["reordered_package"]["valid"])
        self.assertEqual(
            proof["rejected_first_reference"],
            dict(
                missing_document="not_found",
                stale_version="stale_evidence",
                wrong_hash="stale_evidence",
                malformed="invalid_input",
            ),
        )

    def test_four_slots_overlap_and_failed_slot_retains_the_entire_batch(self):
        from knowledge_reference_concurrent import BatchFailure, measure_four

        gate = threading.Barrier(4)
        checked = []

        def operation():
            gate.wait(timeout=5)
            return "synthetic-ok"

        result = measure_four(
            "concurrency-check",
            operation,
            2,
            warmup=1,
            check=checked.append,
        )
        self.assertEqual(len(checked), 12)
        self.assertNotIn("process_cpu_ms", result)
        self.assertTrue(
            all(
                slot["thread_cpu_ms"] is None
                for batch in result["worker_request_timings"]
                for slot in batch
            )
        )
        calls = []
        lock = threading.Lock()

        def one_failed():
            with lock:
                calls.append(len(calls))
                index = calls[-1]
            if index == 0:
                raise ValueError("synthetic-private-error-text")
            return index

        with self.assertRaises(BatchFailure) as raised:
            measure_four("failed-batch", one_failed, 1, warmup=0, check=checked.append)
        self.assertEqual(len(calls), 4)
        self.assertEqual(len(raised.exception.slots), 4)
        self.assertEqual(
            sum(slot["error"] is not None for slot in raised.exception.slots), 1
        )
        self.assertNotIn("synthetic-private-error-text", str(raised.exception))

    def test_untimed_count_methods_restore_on_exception(self):
        import knowledge_reference as study

        native_reference = study.KnowledgeApplication._reference
        native_read = study.knowledge_sources.read_file

        def failed():
            raise RuntimeError("synthetic count failure")

        with self.assertRaisesRegex(RuntimeError, "synthetic count failure"):
            study.untimed_counts(failed)
        self.assertIs(study.KnowledgeApplication._reference, native_reference)
        self.assertIs(study.knowledge_sources.read_file, native_read)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--contract-directory", type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(args.source_root / "src"))
    spec = importlib.util.spec_from_file_location(
        "knowledge_fixture", args.source_root / "tests/test_knowledge.py"
    )
    fixture = importlib.util.module_from_spec(spec)
    sys.modules["knowledge_fixture"] = fixture
    spec.loader.exec_module(fixture)
    # The controller already AST-exports this same native body without its pytest
    # decorator. Local loading leaves the decorator intact, so unwrap only it.
    fixture.knowledge = fixture.knowledge.__wrapped__
    from tianshu_memory.contracts import Contracts

    ReferenceChecks.contracts = Contracts(args.contract_directory)
    unittest.main(argv=[sys.argv[0]], verbosity=2)


if __name__ == "__main__":
    main()
