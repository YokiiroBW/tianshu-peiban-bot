"""Line-oriented, single-call worker for an independent paired SQLite control.

The driver alone orders old/new calls. Beyond three untimed readiness checks,
each command runs once; the worker adds no warmups, network requests, migrations,
or fixture repair. Parsing and validation are outside the timer; native request
building/select is the measured operation.
"""

import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from common import arguments, durability, memory_features
from memory_sql import signature
from sqlite_same_fixture import (
    ExistingHarness,
    check,
    fixture_config,
    fixture_hashes,
    valid_field,
)
from tianshu_memory.contracts import Contracts
from tianshu_memory.service import MemoryService
from tianshu_memory.sources import LocalFixtureSources
from tianshu_memory.store import Store


def reply(event, **values):
    print(json.dumps({"event": event, **values}, ensure_ascii=False), flush=True)


def main():
    args, private, _ = arguments()
    config, database, guard, contract_path = fixture_config(args, private)
    before = fixture_hashes(database, guard)
    store = Store.from_config(config)
    calls = 0
    try:
        after_constructor = fixture_hashes(database, guard)
        assert after_constructor == before, "Store preparation changed shared fixture bytes"
        assert store.backend == "sqlite"
        assert Path(store.path) == database and store.recovery_path == guard
        features = memory_features(store)
        with store.transaction() as db:
            schema = db.execute("SELECT value FROM metadata WHERE key='schema'").fetchone()[0]
            groups = db.execute("SELECT COUNT(*) FROM groups").fetchone()[0]
            sync = durability(db, "sqlite")
        assert schema == "3" and groups == 365
        contracts = Contracts(contract_path)
        contracts.load_sources()
        h = ExistingHarness(config)
        service = MemoryService(
            store,
            contracts,
            source_authority=LocalFixtureSources(),
            clock=lambda: datetime(2026, 9, 14, 1, tzinfo=UTC),
        )
        context = config["origins"]["origin-private"]
        field = valid_field(store, h.private)

        def select(query="Python", budget=100000):
            return service.select(h.selection(query=query, budget=budget), context)

        operations = {
            "exact": lambda: select("field:" + field),
            "zero": lambda: select(budget=0),
            "keyword": lambda: select(),
        }
        expected = {case: operation() for case, operation in operations.items()}
        counts = {"exact": 1, "zero": 0, "keyword": 10}
        for case, value in expected.items():
            assert len(value["dependency_groups"]) == len(value["selected_units"]) == counts[case]
        assert expected["zero"]["budget_used"] == {"tokens": 0, "bytes": 0}
        assert expected["zero"]["omissions"] == ["budget"]
        assert {unit["statement"] for unit in expected["keyword"]["selected_units"]} == {
            f"合成主题 {index} Python fixed fact" for index in range(200, 300, 10)
        }
        assert len({value["scope_version"] for value in expected.values()}) == 1
        signatures = {case: signature(value) for case, value in expected.items()}
        reply(
            "ready",
            suite="sqlite_paired_control",
            backend="sqlite",
            trial=args.trial,
            variant=args.variant,
            verified_revision=args.verified_revision,
            field_key=field,
            logical_result_signatures=signatures,
            schema=schema,
            active_memory_features=features,
            durability=sync,
            shared_fixture={
                "database": str(database),
                "guard": str(guard),
                "settings_namespace": "moriginal43",
                "pool_max_size": 4,
                "before_constructor": before,
                "after_constructor": after_constructor,
                "constructor_noop_verified": True,
            },
            excluded_from_formal_summary=True,
            python_hash_seed=0,
        )
        for line in sys.stdin:
            command = json.loads(line)
            assert isinstance(command, dict) and set(command) == {"case"}
            case = command["case"]
            if case == "stop":
                break
            assert case in operations, "unknown paired control case"
            operation = operations[case]
            cpu_started = time.process_time_ns()
            started = time.perf_counter_ns()
            result = operation()
            latency_ns = time.perf_counter_ns() - started
            cpu_ns = time.process_time_ns() - cpu_started
            check(result, signatures[case], counts[case])
            assert len(result["selected_units"]) == counts[case]
            if case == "zero":
                assert result["budget_used"] == {"tokens": 0, "bytes": 0}
                assert result["omissions"] == ["budget"]
            calls += 1
            reply("sample", case=case, latency_ns=latency_ns, cpu_ns=cpu_ns)
        else:
            raise RuntimeError("paired worker input ended before stop")
    finally:
        store.close()
        after = fixture_hashes(database, guard)
        assert after == before, "paired selection changed shared fixture bytes"
    reply("closed", calls=calls, shared_fixture=after, byte_unchanged=True)


if __name__ == "__main__":
    main()
