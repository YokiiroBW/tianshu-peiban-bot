"""Local SQLite protocol smoke for the independent Knowledge reference study.

Requires an explicit immutable source export and public synthetic contracts.
Does not call common.arguments, an image, a PG URL, NAS or production config.
"""

import argparse
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


class ReferenceChecks(unittest.TestCase):
    contracts = None

    def test_native_200_blocks_16_state_20_package_and_cpu_protocol(self):
        import knowledge_reference as study

        native_reference = study.KnowledgeApplication._reference
        native_read = study.knowledge_sources.read_file
        with tempfile.TemporaryDirectory(
            prefix="ts286-knowledge-reference-"
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
        proof = details["native_negative_proofs"]
        self.assertEqual(proof["wrong_project"], "forbidden")
        self.assertEqual(proof["revoked_permission"], "forbidden")
        self.assertEqual(proof["wrong_credential"], "unauthorized")
        self.assertFalse(proof["other_client_seal"]["valid"])
        self.assertFalse(proof["changed_source"]["valid"])
        self.assertFalse(proof["changed_state_seal"]["valid"])

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
