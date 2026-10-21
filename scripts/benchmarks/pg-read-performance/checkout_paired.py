"""Alternate exact old/new checkout methods over one unchanged native PG fixture.

This isolates protocol cost from separate container/fixture execution order. The
baseline runtime is an exported Git blob, checked against its package manifest.
Only the two changed methods are swapped; the same pool, schema, guard, product
runtime and permission/source checked application methods serve both sides.
"""

from contextlib import closing
import hashlib
import importlib.util
import statistics
import time

from common import arguments, emit, settings
from memory_sql import signature
from memory_write_fixture import contract_files, open_fixture, seed_fixture
from tianshu_storage.runtime import Database, StorageRuntime


def main():
    args, private, _ = arguments()
    assert args.backend == "postgresql" and args.variant == "optimized"
    path = args.config.parent / "code/baseline_runtime.py"
    assert (
        hashlib.sha256(path.read_bytes()).hexdigest()
        == private["baseline_checkout_sha256"]
    )
    spec = importlib.util.spec_from_file_location(
        "tianshu_storage._baseline_runtime", path
    )
    baseline = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(baseline)
    methods = {
        "original": (baseline.Database.connect, baseline.StorageRuntime._checkout),
        "optimized": (Database.connect, StorageRuntime._checkout),
    }
    contracts = contract_files(args.config.parent)
    directory = args.config.parent / "runs" / f"checkout-paired-{args.trial}"
    directory.mkdir(parents=True)
    fixture = seed_fixture(
        directory, contracts, "local", size=20 if args.smoke else 300
    )
    config = dict(
        database_path=fixture.store.path,
        **settings(private, f"pair{args.trial}", args.backend),
    )
    fixture.store.close()
    fixture = open_fixture(fixture.state(), contracts, config)

    def checkout():
        with closing(fixture.store.database.connect(isolation_level=None)) as db:
            return tuple(db.native("SELECT current_schema()").fetchone())

    operations = {
        "checkout_and_select": checkout,
        "authorized_exact": lambda: fixture.selection("field:bench-0"),
        "authorized_zero_budget": lambda: fixture.selection("Python", budget=0),
    }

    def result_signature(case, result):
        return result if case == "checkout_and_select" else signature(result)

    guard_before = hashlib.sha256(fixture.store.recovery_path.read_bytes()).hexdigest()
    reports = {}
    try:
        for case, operation in operations.items():
            expected = result_signature(case, operation())
            rows = []
            for block in range(3):
                samples = []
                for index in range(-4, 8 if args.smoke else 100):
                    order = (
                        ("original", "optimized")
                        if (index + block) % 2 == 0
                        else ("optimized", "original")
                    )
                    values = {}
                    for side in order:
                        Database.connect, StorageRuntime._checkout = methods[side]
                        cpu_start, start = time.thread_time_ns(), time.perf_counter_ns()
                        result = operation()
                        elapsed = time.perf_counter_ns() - start
                        cpu = time.thread_time_ns() - cpu_start
                        assert result_signature(case, result) == expected
                        values[side] = dict(wall_ns=elapsed, thread_cpu_ns=cpu)
                    if index >= 0:
                        samples.append(
                            dict(index=index, order=list(order), samples=values)
                        )
                rows.append(
                    dict(
                        block=block + 1,
                        pairs=samples,
                        p50_ms={
                            side: statistics.median(
                                p["samples"][side]["wall_ns"] for p in samples
                            )
                            / 1e6
                            for side in methods
                        },
                    )
                )
            reports[case] = rows
        assert (
            hashlib.sha256(fixture.store.recovery_path.read_bytes()).hexdigest()
            == guard_before
        )
        with fixture.store.transaction() as db:
            assert db.native("SHOW synchronous_commit").fetchone()[0] == "on"
    finally:
        Database.connect, StorageRuntime._checkout = methods["optimized"]
        fixture.store.close()
    emit(
        args,
        "checkout_paired",
        [],
        paired_native_study=True,
        same_pool_schema_guard_and_product=True,
        unchanged_guard_verified=True,
        source_and_permission_checks_preserved=True,
        hook_assignment_outside_timer=True,
        warmup_pairs_per_case_block=4,
        cases=reports,
        models_called=0,
    )


if __name__ == "__main__":
    main()
