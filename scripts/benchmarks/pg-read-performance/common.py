"""Measurements only: product code owns every storage operation.

The controller supplies a new database owned by a restricted temporary role and a
private directory. Never point this runner at a deployment settings document.
"""

import argparse
import json
import math
import os
import statistics
import time
from collections import Counter
from pathlib import Path


def arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--backend", choices=("sqlite", "postgresql"), required=True)
    parser.add_argument("--trial", type=int, required=True)
    parser.add_argument("--variant", choices=("original", "optimized"), required=True)
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    config = json.loads(args.config.read_bytes())
    assert config["database"].startswith("ts_pgopt_")
    assert not config.get("redis_url")
    args.namespace = f"{args.variant}{args.trial}"
    if args.variant == "optimized":
        import hashlib
        import importlib.util

        proof = json.loads(Path("/usr/share/ts249-candidate.json").read_bytes())
        assert proof["revision"] == config["candidate_revision"]
        for name, key in (
            ("tianshu_memory", "package_files"),
            ("tianshu_storage", "shared_files"),
        ):
            package = Path(importlib.util.find_spec(name).origin).parent
            actual = {
                name + "/" + p.relative_to(package).as_posix(): hashlib.sha256(
                    p.read_bytes()
                ).hexdigest()
                for p in package.rglob("*")
                if p.is_file() and "__pycache__" not in p.parts
            }
            assert actual == proof[key]
    root = args.config.parent / "runs" / f"{Path(__file__).stem}-{args.trial}"
    return args, config, root


def settings(config, namespace, backend):
    return {
        "storage_backends": {
            "postgresql_url": config["postgresql_url"]
            if backend == "postgresql"
            else "",
            "redis_url": "",
            "namespace": namespace,
            "pool_max_size": 4,
        }
    }


def prepare_memory_schema(store, directory):
    """Equal active Memory features on both backends, using native migrations.

    Fixtures start at schema 3. Production also has source lookup, automatic facts
    and continuity enabled; their indexed reads must be timed on SQLite as well.
    PostgreSQL imports this exact synthetic SQLite authority before measurement.
    """
    migrations = {
        "source_lookup_schema": "migrate_source_lookup",
        "automatic_fact_schema": "migrate_facts",
        "memory_context_schema": "migrate_context",
    }
    with store.transaction() as db:
        installed = dict(db.execute("SELECT key,value FROM metadata"))
    for key, method in migrations.items():
        if installed.get(key) != "1":
            getattr(store, method)(directory / (key + ".backup"))
    return memory_features(store)


def memory_features(store):
    with store.transaction() as db:
        installed = dict(db.execute("SELECT key,value FROM metadata"))
    features = {
        key: installed.get(key)
        for key in (
            "source_lookup_schema",
            "automatic_fact_schema",
            "memory_context_schema",
        )
    }
    assert all(value == "1" for value in features.values())
    return features


def sqlite_calls(operation, backend):
    """Untimed trace, including source readonly connections; no bound values leave.

    Trigger callback repetitions are retained. Counts describe this one synthetic
    operation's trace, not physical I/O or a latency measurement.
    """
    if backend != "sqlite":
        return None
    import hashlib
    import re
    import sqlite3

    connect = sqlite3.connect
    commands, shapes = Counter(), Counter()
    connections = []
    active = True

    def trace(statement):
        if not active:
            return
        normalized = re.sub(r"'(?:[^']|'')*'", "?", statement)
        normalized = re.sub(r"\b\d+(?:\.\d+)?\b", "?", normalized)
        normalized = " ".join(normalized.split())
        if normalized:
            commands[normalized.split(None, 1)[0].upper()] += 1
            shapes[hashlib.sha256(normalized.encode()).hexdigest()] += 1

    def counted(*args, **kwargs):
        connection = connect(*args, **kwargs)
        connection.set_trace_callback(trace)
        connections.append(connection)
        return connection

    sqlite3.connect = counted
    try:
        operation()
    finally:
        sqlite3.connect = connect
        active = False
        for connection in connections:
            try:
                connection.set_trace_callback(None)
            except sqlite3.ProgrammingError:
                # Transactions own and normally close these before returning.
                # HTTP workers may also have created them on another thread.
                pass
    return {
        "connections_opened": len(connections),
        "traced_statements": sum(commands.values()),
        "commands": dict(commands),
        "normalized_shape_counts": dict(sorted(shapes.items())),
        "includes_trigger_callback_repetitions": True,
        "timing_included": False,
    }


def summarize(name, values, wall, **details):
    ordered = sorted(values)

    def percentile(p):
        return ordered[max(0, math.ceil(len(ordered) * p) - 1)]

    return dict(
        case=name,
        operations=len(values),
        elapsed_s=wall,
        ops_s=len(values) / wall,
        p50_ms=statistics.median(ordered),
        p95_ms=percentile(0.95),
        p99_ms=percentile(0.99),
        mean_ms=statistics.mean(ordered),
        latencies_ms=values,
        errors=0,
        **details,
    )


def measure(name, operation, count, *, warmup=5, check=None, **details):
    for index in range(-warmup, 0):
        result = operation(index)
        if check:
            check(result)
    values = []
    start = time.perf_counter()
    for index in range(count):
        before = time.perf_counter_ns()
        result = operation(index)
        values.append((time.perf_counter_ns() - before) / 1e6)
        if check:
            check(result)
    wall = time.perf_counter() - start
    return summarize(
        name,
        values,
        sum(values) / 1000,
        operation_and_validation_wall_s=wall,
        **details,
    )


async def measure_async(name, operation, count, *, before=None, check=None, **details):
    values, wall = [], 0.0
    for index in range(count):
        if before:
            await before(index)
        start = time.perf_counter_ns()
        result = await operation(index)
        elapsed = (time.perf_counter_ns() - start) / 1e6
        values.append(elapsed)
        wall += elapsed / 1000
        if check:
            check(result)
    return summarize(name, values, wall, **details)


def durability(connection, backend):
    if backend == "postgresql":
        return {
            name: connection.native("SHOW " + name).fetchone()[0]
            for name in (
                "server_version",
                "fsync",
                "synchronous_commit",
                "full_page_writes",
            )
        }
    return {
        name: connection.execute("PRAGMA " + name).fetchone()[0]
        for name in ("journal_mode", "synchronous", "busy_timeout")
    }


def pg_calls(operation, backend):
    """One untimed call; counts native adapter statements, never their values.

    This is diagnostic only: driver startup and implicit transaction messages
    are not intercepted, and timing samples never run under this wrapper.
    """
    if backend != "postgresql":
        return None
    from tianshu_storage.postgres import PostgresConnection

    original = PostgresConnection.native
    counts = Counter()
    elapsed = Counter()

    def traced(self, sql, parameters=(), **kwargs):
        counts[str(sql).strip().split(None, 1)[0].upper()] += 1
        start = time.perf_counter_ns()
        try:
            return original(self, sql, parameters, **kwargs)
        finally:
            # Statement shapes contain placeholders, never bind values. Keep a
            # short prefix solely to distinguish guard/catalog/business work.
            shape = " ".join(str(sql).split())[:120]
            elapsed[shape] += (time.perf_counter_ns() - start) / 1e6

    PostgresConnection.native = traced
    try:
        operation()
    finally:
        PostgresConnection.native = original
    return {
        "statements": sum(counts.values()),
        "by_command": dict(counts),
        "native_elapsed_ms": sum(elapsed.values()),
        "slowest_statement_shapes_ms": elapsed.most_common(5),
    }


def emit(args, suite, results, **details):
    print(
        json.dumps(
            dict(
                suite=suite,
                backend=args.backend,
                trial=args.trial,
                variant=args.variant,
                correctness="passed",
                cpu_count=os.cpu_count(),
                cpu_affinity=sorted(os.sched_getaffinity(0)),
                loadavg=os.getloadavg(),
                results=results,
                **details,
            ),
            ensure_ascii=False,
        )
    )
