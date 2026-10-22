"""Bounded local checks; no installation proof, PG URL, container or NAS access.

The source root must be an explicitly exported immutable product tree. This is a
functional SQLite check of the benchmark, not a performance result or PG proof.
"""

import argparse
import copy
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import common


class Clock:
    def __init__(self):
        self.wall = self.process = self.thread = 0

    def perf_counter_ns(self):
        return self.wall

    def perf_counter(self):
        return self.wall / 1e9

    def process_time_ns(self):
        return self.process

    def thread_time_ns(self):
        return self.thread

    def operation(self, index):
        self.wall += 3_000_000
        self.process += 2_000_000
        self.thread += 1_000_000
        return index

    def check(self, value):
        self.wall += 10_000_000
        self.process += 10_000_000
        self.thread += 10_000_000


class ClockChecks(unittest.TestCase):
    def test_clock_samples_exclude_checks_and_warmup(self):
        clock = Clock()
        with patch.object(common, "time", clock):
            result = common.measure(
                "synthetic",
                clock.operation,
                3,
                warmup=2,
                check=clock.check,
                cpu_clocks=True,
            )
        self.assertEqual(result["latencies_ms"], [3.0] * 3)
        self.assertEqual(result["process_cpu_ms"], [2.0] * 3)
        self.assertEqual(result["thread_cpu_ms"], [1.0] * 3)
        self.assertAlmostEqual(result["operation_and_validation_wall_s"], 0.039)
        self.assertFalse(result["cpu_clock_protocol"]["profiling"])

    def test_default_path_reads_no_cpu_clock_and_keeps_output(self):
        clock = Clock()
        clock.process_time_ns = clock.thread_time_ns = lambda: self.fail(
            "CPU clock read"
        )
        with patch.object(common, "time", clock):
            result = common.measure(
                "synthetic", clock.operation, 3, warmup=2, check=clock.check
            )
        self.assertEqual(result["latencies_ms"], [3.0] * 3)
        self.assertFalse(any("cpu" in key for key in result))


class NativeChecks(unittest.TestCase):
    contract_directory = None

    def test_native_two_stage_authority_and_signatures(self):
        import memory_retrieval as workload
        from jsonschema.exceptions import ValidationError
        from tianshu_memory.service import MemoryService
        from tianshu_memory.source_authority import SourceAuthority

        contracts = workload.Contracts(self.contract_directory)
        contracts.load_sources()
        with tempfile.TemporaryDirectory(prefix="ts286-native-") as temporary:
            directory = Path(temporary)
            fixture = workload.prepare(
                directory, contracts, {}, "sqlite", "rcheck", smoke=True
            )
            try:
                revisions, verifications = [], []
                original_revision = SourceAuthority.revision
                original_verify = MemoryService._verify_retrieval

                def revision(db):
                    revisions.append(1)
                    return original_revision(db)

                def verify(service, db, candidates):
                    verifications.append(len(candidates))
                    return original_verify(service, db, candidates)

                owners = fixture.owners.calls
                with (
                    patch.object(SourceAuthority, "revision", staticmethod(revision)),
                    patch.object(MemoryService, "_verify_retrieval", verify),
                ):
                    first = workload.select(fixture)
                self.assertEqual(len(revisions), 12)
                self.assertEqual(verifications, [6])
                self.assertEqual(fixture.owners.calls - owners, 6)
                self.assertEqual(len(first["selected_units"]), 8)
                self.assertEqual(len(first["dependency_groups"]), 6)
                expected = workload.signature(first, contracts, fixture.h.person)
                self.assertEqual(
                    workload.signature(
                        workload.select(fixture), contracts, fixture.h.person
                    ),
                    expected,
                )

                # Every source/reference/visibility/envelope field remains signed.
                changed = copy.deepcopy(first)
                changed["selected_units"][0]["sources"][0]["source"]["receipt_id"] += (
                    "-changed"
                )
                self.assertNotEqual(
                    workload.signature(changed, contracts, fixture.h.person), expected
                )
                changed = copy.deepcopy(first)
                changed["scope_version"] += 1
                self.assertNotEqual(
                    workload.signature(changed, contracts, fixture.h.person), expected
                )
                changed = copy.deepcopy(first)
                changed["dependency_groups"][0]["complete"] = False
                with self.assertRaises((AssertionError, ValidationError)):
                    workload.signature(changed, contracts, fixture.h.person)

                # Only generated record/group/person identities are normalized.
                changed = copy.deepcopy(first)
                identities = {}
                for index, unit in enumerate(changed["selected_units"]):
                    identities[unit["record_id"]] = f"record-normalized-{index}"
                    identities.setdefault(
                        unit["semantic_group_id"], f"group-normalized-{index}"
                    )
                identities[fixture.h.person] = "person-normalized"

                def replace(value):
                    if isinstance(value, str):
                        return identities.get(value, value)
                    if isinstance(value, list):
                        return [replace(item) for item in value]
                    if isinstance(value, dict):
                        return {key: replace(item) for key, item in value.items()}
                    return value

                changed = replace(changed)
                self.assertEqual(
                    workload.signature(changed, contracts, "person-normalized"),
                    expected,
                )

                reranks = fixture.backend.calls["rerank"]
                self.assertEqual(
                    workload.expect_fault(
                        lambda: workload.select(
                            fixture, scope=dict(fixture.scope, actor_id="actor:b")
                        ),
                        "forbidden",
                    ),
                    "forbidden",
                )
                self.assertEqual(fixture.backend.calls["rerank"], reranks)
                fixture.backend.hook = lambda: workload.withdraw(fixture)
                self.assertEqual(
                    workload.expect_fault(
                        lambda: workload.select(fixture), "scope_changed"
                    ),
                    "scope_changed",
                )
                remaining = workload.select(fixture)
                self.assertGreater(remaining["scope_version"], first["scope_version"])
                self.assertEqual(len(remaining["dependency_groups"]), 5)
                self.assertEqual(len(remaining["selected_units"]), 7)
                self.assertNotEqual(
                    workload.signature(remaining, contracts, fixture.h.person), expected
                )
                with fixture.store.transaction() as db:
                    self.assertEqual(
                        db.execute("PRAGMA quick_check").fetchone()[0], "ok"
                    )
                    self.assertIsNone(db.execute("PRAGMA foreign_key_check").fetchone())
                before = common.memory_features(fixture.store)
                self.assertEqual(before["automatic_fact_schema"], "2")
                self.assertEqual(
                    common.prepare_memory_schema(fixture.store, directory), before
                )
                # A second call must not recreate any migration backup.
                self.assertEqual(
                    common.prepare_memory_schema(fixture.store, directory), before
                )
            finally:
                fixture.store.close()

    def test_first_negative_transition_and_native_reopen(self):
        import memory_retrieval as workload

        contracts = workload.Contracts(self.contract_directory)
        contracts.load_sources()
        with tempfile.TemporaryDirectory(prefix="ts286-negative-") as temporary:
            result, proof = workload.negative_sync(
                Path(temporary) / "fixture",
                contracts,
                {},
                "sqlite",
                "ncheck",
                smoke=True,
                cpu_clocks=True,
            )
        self.assertEqual(result["operations"], 1)
        self.assertEqual(len(result["process_cpu_ms"]), 1)
        self.assertEqual(proof["original_selectors"], 6)
        self.assertEqual(proof["withdrawn_selectors"], 4)
        self.assertEqual(proof["owner_calls_in_measurement"], 3)
        self.assertTrue(proof["same_native_guard_reopened"])
        self.assertTrue(proof["unchanged_on_repeat_read"])

    def test_context_query_full_32_unit_shape(self):
        import memory_retrieval as workload
        from tianshu_memory.service import MemoryService

        contracts = workload.Contracts(self.contract_directory)
        contracts.load_sources()
        with tempfile.TemporaryDirectory(prefix="ts286-context-") as temporary:
            fixture = workload.prepare(
                Path(temporary), contracts, {}, "sqlite", "rcontext"
            )
            try:
                verifications = []
                native = MemoryService._verify_retrieval

                def verify(service, db, candidates):
                    verifications.append(len(candidates))
                    return native(service, db, candidates)

                before = fixture.owners.calls
                with patch.object(MemoryService, "_verify_retrieval", verify):
                    result = workload.context_query(fixture)
                self.assertEqual(verifications, [24])
                self.assertEqual(fixture.owners.calls - before, 6)
                self.assertEqual(len(result["dependency_groups"]), 24)
                self.assertEqual(len(result["selected_units"]), 32)
                self.assertTrue(result["coverage"]["complete"])
                expected = workload.signature(result, contracts, fixture.h.person)
                self.assertEqual(
                    workload.signature(
                        workload.context_query(fixture), contracts, fixture.h.person
                    ),
                    expected,
                )
                changed = copy.deepcopy(result)
                changed["scope_checks"][0]["scope_version"] += 1
                self.assertNotEqual(
                    workload.signature(changed, contracts, fixture.h.person), expected
                )
            finally:
                fixture.store.close()


def load_fixture(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--contract-directory", type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(args.source_root / "src"))
    load_fixture("memory_fixture", args.source_root / "tests/conftest.py")
    load_fixture("sync_fixture", args.source_root / "tests/source_sync_harness.py")
    NativeChecks.contract_directory = args.contract_directory
    unittest.main(argv=[sys.argv[0]], verbosity=2)


if __name__ == "__main__":
    main()
