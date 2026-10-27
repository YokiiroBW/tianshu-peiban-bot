"""Compare exact ledger sources on identical synthetic writes, including summaries.

Pass --version name=/absolute/observability for each frozen source directory.
All data is synthetic, no network is used, and an existing output is refused.
Alternating orders retain every round, including unfavorable measurements.
"""

import argparse
import hashlib
import importlib.util
import json
import math
import platform
import sqlite3
import statistics
import sys
import time
import uuid
from contextlib import nullcontext
from pathlib import Path

from benchmark_ledger import storage, timed
from helpers import SNAPSHOT, emit, event


def source_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def implementation(name, directory):
    """Load actual monitor/policy/schema bytes, without replacing their logic."""
    dependencies = {"policy": source_module(name + "_policy", directory / "policy.py")}
    if (directory / "ledger_schema.py").is_file():
        dependencies["ledger_schema"] = source_module(name + "_schema", directory / "ledger_schema.py")
    previous = {key: sys.modules.get(key) for key in dependencies}
    try:
        sys.modules.update(dependencies)
        monitor = source_module(name + "_monitor", directory / "monitor.py")
    finally:
        for key, value in previous.items():
            if value is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = value
    return {
        "name": name,
        "directory": str(directory),
        "ledger": monitor.Ledger,
        "policy": dependencies["policy"].Policy(SNAPSHOT),
        "summary": getattr(dependencies.get("ledger_schema"), "insert_summary", None),
        "binary": "digest_bytes" in monitor.__dict__,
        "source_sha256": {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                          for path in (directory / "monitor.py", directory / "policy.py", directory / "ledger_schema.py")
                          if path.is_file()},
    }


def elapsed(call):
    start, cpu = time.perf_counter(), time.process_time()
    result = call()
    return {"wall_ms": (time.perf_counter() - start) * 1000,
            "cpu_ms": (time.process_time() - cpu) * 1000}, result


def bulk_seed(ledger, version, count, now):
    summary = version["summary"]
    scope = summary(ledger.db) if summary else nullcontext(None)
    with ledger.db, scope as accepted:
        def rows():
            for index in range(count):
                ident = str(uuid.UUID(int=index + 1))
                instance = str(uuid.UUID(int=index % 16 + 1))
                sequence = index // 16 + 1
                value = hashlib.sha256(ident.encode()).digest()
                verified = 0 if index % 20 == 0 else now - 7200
                yield (ident, "platform", instance, sequence,
                       value if version["binary"] else value.hex(),
                       now - 86400 + index / count, verified,
                       now - 7200 + index % 3600)
                # executemany resumes this generator after the preceding INSERT
                # succeeds. Summary writes are included in the measured scope.
                if accepted:
                    accepted("platform", instance, sequence, verified)
        ledger.db.executemany("INSERT INTO events(id,service,instance,sequence,hash,first_seen,verified,last_checked) VALUES (?,?,?,?,?,?,?,?)", rows())


def digest_rows(ledger):
    result = hashlib.sha256()
    for ident, value in ledger.db.execute("SELECT id,hash FROM events ORDER BY id"):
        result.update(ident.encode())
        result.update(bytes.fromhex(value) if isinstance(value, str) else value)
    return result.hexdigest()


def values(ledger):
    return {key: value for key, value in ledger.metrics().items()
            if key in {"landed_events", "pending_events", "sequence_gaps"}}


def read_costs(ledger, now):
    query = ("SELECT id,hash FROM events WHERE verified>0 AND last_checked<? "
             "AND first_seen>? ORDER BY last_checked LIMIT ?")
    parameters = (now - 3600, now - 30 * 86400, 64)
    return {
        "metrics": timed(ledger.metrics),
        "confirmed_queue": timed(lambda: ledger.db.execute(query, parameters).fetchall()),
        "idle_scan": timed(lambda: ledger.scan({})),
        "confirmed_plan": [row[-1] for row in ledger.db.execute("EXPLAIN QUERY PLAN " + query, parameters)],
    }


def real_scan(ledger, source, count):
    batches = []
    for _ in range(math.ceil(count / 20_000)):
        sample, result = elapsed(lambda: ledger.scan({"platform": source}))
        batches.append({**sample, **result})
    return {"wall_ms": sum(row["wall_ms"] for row in batches),
            "cpu_ms": sum(row["cpu_ms"] for row in batches), "batches": batches}


def run_version(version, output, source, append, args, now, round_number):
    prefix = output / (str(round_number) + "-" + version["name"])
    ledger = version["ledger"](str(prefix) + "-bulk.sqlite", version["policy"],
                               max_events=args.events + args.append_events)
    try:
        measurement, _ = elapsed(lambda: bulk_seed(ledger, version, args.events, now))
        result = {"bulk": measurement, "bulk_values": values(ledger),
                  "bulk_digest_sha256": digest_rows(ledger), "bulk_storage": storage(ledger),
                  "reads": read_costs(ledger, now)}
        result["append"] = real_scan(ledger, append, args.append_events)
        result["append_values"] = values(ledger)
        result["append_digest_sha256"] = digest_rows(ledger)
        result["integrity_check"] = ledger.db.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        ledger.close()
    ledger = version["ledger"](str(prefix) + "-scan.sqlite", version["policy"])
    try:
        result["scan"] = real_scan(ledger, source, args.scan_events)
        result["scan_values"] = values(ledger)
        result["scan_digest_sha256"] = digest_rows(ledger)
        result["scan_storage"] = storage(ledger)
    finally:
        ledger.close()
    reopened = version["ledger"](str(prefix) + "-scan.sqlite", version["policy"])
    try:
        if values(reopened) != result["scan_values"]:
            raise AssertionError("reopen changed committed metrics")
    finally:
        reopened.close()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--events", type=int, default=1_000_000)
    parser.add_argument("--scan-events", type=int, default=200_000)
    parser.add_argument("--append-events", type=int, default=20_000)
    parser.add_argument("--rounds", type=int, default=3)
    args = parser.parse_args()
    if not all(value > 0 for value in (args.events, args.scan_events, args.append_events, args.rounds)):
        parser.error("counts must be positive")
    versions = []
    for item in args.version:
        name, directory = item.split("=", 1)
        if not name.isidentifier() or name in {version["name"] for version in versions}:
            parser.error("version names must be distinct Python identifiers")
        versions.append(implementation(name, Path(directory).resolve(strict=True)))
    args.output.mkdir(parents=True, exist_ok=False)
    source, append = args.output / "source", args.output / "append"
    source.mkdir()
    append.mkdir()
    def records(count, start, streams):
        for index in range(count):
            record = event(index // streams + 1,
                           instance=str(uuid.UUID(int=index % streams + 100)),
                           event_id=str(uuid.UUID(int=start + index + 1)))
            record["timestamp"] = "2026-10-27T00:00:00+00:00"
            yield record
    emit(source / "synthetic.jsonl", records(args.scan_events, 0, 16))
    emit(append / "synthetic.jsonl", records(args.append_events, args.events, 16))
    report = {"boundary": "synthetic local SQLite; no Loki/network/container/production",
              "python": sys.version, "platform": platform.platform(), "sqlite": sqlite3.sqlite_version,
              "events": args.events, "scan_events": args.scan_events, "append_events": args.append_events,
              "normal_scan_batch": 20_000, "stream_count": 16, "bulk_pending_fraction": 0.05,
              "versions": [{key: version[key] for key in ("name", "directory", "source_sha256")}
                           for version in versions], "rounds": []}
    now = time.time()
    for round_number in range(args.rounds):
        order = versions[round_number % len(versions):] + versions[:round_number % len(versions)]
        results = {}
        for version in order:
            result = run_version(version, args.output, source, append, args, now, round_number)
            results[version["name"]] = result
            print(json.dumps({"round": round_number, "version": version["name"],
                              **{key: result[key]["wall_ms"] for key in ("bulk", "scan", "append")}}), flush=True)
        for key in ("bulk_values", "bulk_digest_sha256", "append_values", "append_digest_sha256",
                    "scan_values", "scan_digest_sha256"):
            if len({json.dumps(result[key], sort_keys=True) for result in results.values()}) != 1:
                raise AssertionError("versions differ: " + key)
        report["rounds"].append({"order": [version["name"] for version in order], "results": results})
        (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    report["median_wall_ms"] = {
        version["name"]: {key: statistics.median(round_["results"][version["name"]][key]["wall_ms"]
                                               for round_ in report["rounds"])
                          for key in ("bulk", "scan", "append")}
        for version in versions}
    (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["median_wall_ms"], indent=2), flush=True)


if __name__ == "__main__":
    main()
