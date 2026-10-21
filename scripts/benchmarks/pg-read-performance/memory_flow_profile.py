"""Untimed attribution over native permission/source/guard checked flows.

This diagnostic never supplies a latency sample to the formal comparison. Nested
phase timers charge only the currently active phase; SQL queueing and pipeline
confirmation are separate. cProfile runs on separate calls with hooks restored.
No argument values, SQL text, results or real records are exported by the hooks.
"""

from collections import Counter
from contextlib import contextmanager
from functools import wraps
import threading
import time

from common import arguments, durability, emit, python_profile, settings
from memory_sql import signature
from memory_write_fixture import contract_files, open_fixture, seed_fixture


class Phases:
    def __init__(self):
        self.thread = threading.get_ident()
        self.stack = []
        self.wall = Counter()
        self.cpu = Counter()
        self.inclusive_wall = Counter()
        self.inclusive_cpu = Counter()
        self.calls = Counter()
        self.patches = []
        self.last_wall = self.last_cpu = None

    def charge(self):
        wall, cpu = time.perf_counter_ns(), time.thread_time_ns()
        if self.stack:
            self.wall[self.stack[-1]] += wall - self.last_wall
            self.cpu[self.stack[-1]] += cpu - self.last_cpu
        self.last_wall, self.last_cpu = wall, cpu

    @contextmanager
    def phase(self, name):
        if threading.get_ident() != self.thread:
            yield
            return
        self.charge()
        started_wall, started_cpu = self.last_wall, self.last_cpu
        self.stack.append(name)
        self.calls[name] += 1
        try:
            yield
        finally:
            self.charge()
            self.inclusive_wall[name] += self.last_wall - started_wall
            self.inclusive_cpu[name] += self.last_cpu - started_cpu
            assert self.stack.pop() == name

    def patch(self, owner, name, phase):
        original = getattr(owner, name)

        @wraps(original)
        def wrapped(*args, **kwargs):
            with self.phase(phase):
                return original(*args, **kwargs)

        self.patches.append((owner, name, original))
        setattr(owner, name, wrapped)

    @contextmanager
    def installed(self):
        import psycopg
        from tianshu_memory.contracts import Contracts
        from tianshu_memory.source_authority import SourceAuthority
        from tianshu_memory import source_recovery
        from tianshu_storage.runtime import StorageRuntime

        try:
            self.patch(StorageRuntime, "_checkout", "connection_checkout")
            self.patch(Contracts, "validate", "document_validation")
            self.patch(SourceAuthority, "_relations", "source_relation_rules")
            self.patch(source_recovery, "verify", "guard_verify")
            self.patch(source_recovery, "persist", "guard_persist")
            self.patch(psycopg.Cursor, "execute", "driver_execute")
            for method in ("fetchone", "fetchmany", "fetchall"):
                self.patch(psycopg.Cursor, method, "driver_fetch")
            self.patch(psycopg.Pipeline, "__exit__", "pipeline_confirmation")
            self.patch(psycopg.Connection, "commit", "driver_commit")
            self.patch(psycopg.Connection, "rollback", "driver_rollback")
            yield
        finally:
            for owner, name, original in reversed(self.patches):
                setattr(owner, name, original)
            self.patches.clear()

    def measure(self, operation):
        with self.installed(), self.phase("application_and_adapter"):
            result = operation()
        assert not self.stack
        phases = [
            dict(
                phase=name,
                calls=self.calls[name],
                exclusive_wall_ms=self.wall[name] / 1e6,
                exclusive_thread_cpu_ms=self.cpu[name] / 1e6,
                inclusive_wall_ms=self.inclusive_wall[name] / 1e6,
                inclusive_thread_cpu_ms=self.inclusive_cpu[name] / 1e6,
            )
            for name in self.calls
        ]
        return result, dict(
            timing_included=False,
            diagnostic_overhead_included=True,
            mutually_exclusive_phases=True,
            inclusive_columns_overlap=True,
            current_thread_only=True,
            thread_cpu_includes_native_driver_work=True,
            attribution_wall_ms=sum(self.wall.values()) / 1e6,
            attribution_thread_cpu_ms=sum(self.cpu.values()) / 1e6,
            phases=phases,
        )


def main():
    args, private, _ = arguments()
    args.profile_only = True
    contracts = contract_files(args.config.parent)
    size = 20 if args.smoke else 300
    reports = {}
    proofs = {}
    for kind in ("local", "source"):
        directory = (
            args.config.parent
            / "runs"
            / (f"flow-profile-{kind}-{args.variant}-{args.trial}-{args.backend}")
        )
        directory.mkdir(parents=True)
        fixture = seed_fixture(directory, contracts, kind, size=size)
        config = dict(
            database_path=fixture.store.path,
            **settings(private, f"fp{kind}{args.namespace}", args.backend),
        )
        fixture.store.close()
        fixture = open_fixture(fixture.state(), contracts, config)
        writes = []
        try:
            assert fixture.store.backend == args.backend
            with fixture.store.transaction() as db:
                sync = durability(db, args.backend)
            for label, query, budget in (
                ("exact", "field:bench-0", 100000),
                ("keyword", "Python", 100000),
                ("zero_budget", "Python", 0),
            ):

                def operation(q=query, b=budget):
                    return fixture.selection(q, budget=b)

                expected = signature(operation())
                result, report = Phases().measure(operation)
                assert signature(result) == expected
                report["python_profile_separate_call"] = python_profile(
                    operation,
                    check=lambda r, s=expected: require_signature(r, s),
                    limit=35,
                )
                reports[f"{kind}_{label}"] = report
            packet = fixture.prepare([10000])
            before = fixture.counts()
            result, report = Phases().measure(lambda: fixture.commit(packet))
            fixture.assert_committed(packet, result, before)
            writes.append((packet, result))
            reports[f"{kind}_commit"] = report
            packet = fixture.prepare([10001])
            before = fixture.counts()

            def commit_for_profile():
                result = fixture.commit(packet)
                writes.append((packet, result))
                return result

            report["python_profile_separate_call"] = python_profile(
                commit_for_profile,
                check=lambda result: fixture.assert_committed(packet, result, before),
                limit=35,
            )

            def full_flow(index):
                packet, result = fixture.full_flow(index)
                assert result["state"] == "committed"
                writes.append((packet, result))
                return result

            _, report = Phases().measure(lambda: full_flow(10002))
            report["python_profile_separate_call"] = python_profile(
                lambda: full_flow(10003), limit=35
            )
            reports[f"{kind}_full_flow"] = report
            counts = fixture.counts()
            assert counts["groups"] == counts["records"] == size + 4
            state = fixture.state()
            owner_reads = fixture.owners.calls if fixture.owners else 0
            fixture.store.close()
            fixture = open_fixture(state, contracts, config)
            assert fixture.counts() == counts
            # Reopened native API rechecks actual authorization, source revisions,
            # complete groups and durable content for all four writes.
            signatures = [fixture.content_signature(packet) for packet, _ in writes]
            proofs[kind] = dict(
                counts=counts,
                native_reopen_verified=True,
                write_content_signatures=signatures,
                owner_reads=owner_reads,
                durability=sync,
            )
        finally:
            fixture.store.close()
    emit(
        args,
        "flow_profile",
        [],
        diagnostic_only=True,
        seed_groups_per_kind=size,
        flow_profiles=reports,
        proofs=proofs,
        owner_transport="published synthetic responses; no real upstream/TLS/model latency",
        models_called=0,
    )


def require_signature(result, expected):
    assert signature(result) == expected


if __name__ == "__main__":
    main()
