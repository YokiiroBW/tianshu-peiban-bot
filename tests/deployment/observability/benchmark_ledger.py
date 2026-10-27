"""Compare an explicit old monitor.py with the current ledger on synthetic data.

No network, Docker, business data or source deletion. Output lives in a supplied
new directory; the old source is supplied explicitly so this is not a stored
second implementation or an automatic performance gate.
"""

import argparse
import hashlib
import importlib.util
import json
import statistics
import sys
import time
import uuid
from pathlib import Path

from helpers import SNAPSHOT, emit, event
from ledger_schema import insert_summary
from monitor import Ledger
from policy import Policy


def load_baseline(path):
    spec = importlib.util.spec_from_file_location("ledger_baseline", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Ledger


def timed(call, repetitions=9):
    samples = []
    for _ in range(repetitions):
        start, cpu = time.perf_counter(), time.process_time()
        call()
        samples.append({"wall_ms": (time.perf_counter() - start) * 1000,
                        "cpu_ms": (time.process_time() - cpu) * 1000})
    return {"median_wall_ms": statistics.median(x["wall_ms"] for x in samples),
            "median_cpu_ms": statistics.median(x["cpu_ms"] for x in samples),
            "samples": samples}


def storage(ledger):
    ledger.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    return {
        "page_count": ledger.db.execute("PRAGMA page_count").fetchone()[0],
        "freelist_pages": ledger.db.execute("PRAGMA freelist_count").fetchone()[0],
        "page_size": ledger.db.execute("PRAGMA page_size").fetchone()[0],
        "objects": [dict(zip(("name", "bytes", "payload_bytes"), row)) for row in
                    ledger.db.execute("SELECT name,sum(pgsize),sum(payload) FROM dbstat GROUP BY name")],
    }


def seed(ledger, count, now, binary):
    def rows():
        for index in range(count):
            stream, sequence = index % 16, index // 16 + 1
            ident = str(uuid.UUID(int=index + 1))
            value = hashlib.sha256(ident.encode()).digest()
            # Deterministic 5% pending, the rest due for hourly rechecking.
            pending = index % 20 == 0
            instance = str(uuid.UUID(int=stream + 1))
            yield (ident, "platform", instance, sequence,
                   value if binary else value.hex(), now - 86400 + index / count,
                   0 if pending else now - 7200, now - 7200 + (index % 3600))
            if binary:
                inserted("platform", instance, sequence, 0 if pending else now - 7200)
    started = time.perf_counter()
    with ledger.db, insert_summary(ledger.db) as inserted:
        ledger.db.executemany("INSERT INTO events VALUES (?,?,?,?,?,?,?,?)", rows())
    return time.perf_counter() - started


def measurements(ledger, now, source):
    query = ("SELECT id,hash FROM events WHERE verified>0 AND last_checked<? "
             "AND first_seen>? ORDER BY last_checked LIMIT ?")
    parameters = (now - 3600, now - 30 * 86400, 64)
    ledger.metrics()
    ledger.db.execute(query, parameters).fetchall()
    metrics_before = ledger.metrics()
    calls = 0

    def progress():
        nonlocal calls
        calls += 1
        return 0

    ledger.db.set_progress_handler(progress, 1000)
    ledger.metrics()
    metric_steps = calls * 1000
    calls = 0
    ledger.db.execute(query, parameters).fetchall()
    query_steps = calls * 1000
    ledger.db.set_progress_handler(None, 0)
    result = {
        "metrics": timed(ledger.metrics),
        "confirmed_queue": timed(lambda: ledger.db.execute(query, parameters).fetchall()),
        "idle_scan": timed(lambda: ledger.scan({})),
        "sqlite_vm_steps_floor": {"metrics": metric_steps, "confirmed_queue": query_steps},
        "confirmed_plan": [row[-1] for row in ledger.db.execute("EXPLAIN QUERY PLAN " + query, parameters)],
        "metrics_values": {k: metrics_before[k] for k in ("landed_events", "pending_events", "sequence_gaps")},
    }
    result["append_1000"] = timed(lambda: ledger.scan({"platform": source}), 1)
    result["metrics_after_append"] = {k: ledger.metrics()[k] for k in
                                     ("landed_events", "pending_events", "sequence_gaps")}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--events", type=int, default=1_000_000)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    source = args.output / "sources"
    source.mkdir()
    emit(source / "synthetic.jsonl", [event(i, instance="00000000-0000-4000-8000-ffffffffffff")
                                     for i in range(1, 1001)])
    baseline = load_baseline(args.baseline_source)
    now = time.time()
    report = {"events": args.events, "stream_count": 16, "pending_fraction": 0.05,
              "baseline_source_sha256": hashlib.sha256(args.baseline_source.read_bytes()).hexdigest(),
              "boundary": "synthetic local SQLite; no Loki/network/container/production measurements",
              "ledger_capacity": "synthetic events+1000; production default remains one million",
              "python": sys.version}
    for name, implementation in (("baseline", baseline), ("optimized", Ledger)):
        path = args.output / (name + ".sqlite")
        ledger = implementation(path, Policy(SNAPSHOT), max_events=args.events + 1000)
        report[name] = {"seed_seconds": seed(ledger, args.events, now, name == "optimized")}
        report[name]["before_append_storage"] = storage(ledger)
        report[name].update(measurements(ledger, now, source))
        report[name]["after_append_storage"] = storage(ledger)
        ledger.close()
        report[name]["file_bytes"] = path.stat().st_size
    # Upgrade the old database in place: no VACUUM or evidence removal.
    start = time.perf_counter()
    upgraded = Ledger(args.output / "baseline.sqlite", Policy(SNAPSHOT), max_events=args.events + 1000)
    report["upgrade"] = {"seconds": time.perf_counter() - start, "storage": storage(upgraded),
                         "metrics_values": {k: upgraded.metrics()[k] for k in
                                            ("landed_events", "pending_events", "sequence_gaps")}}
    upgraded.close()
    if report["baseline"]["metrics_values"] != report["optimized"]["metrics_values"]:
        raise AssertionError("seeded metrics differ")
    if report["baseline"]["metrics_after_append"] != report["optimized"]["metrics_after_append"]:
        raise AssertionError("append metrics differ")
    if report["upgrade"]["metrics_values"] != report["baseline"]["metrics_after_append"]:
        raise AssertionError("upgraded metrics differ")
    (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({name: {k: value[k] for k in ("file_bytes", "seed_seconds", "metrics", "confirmed_queue", "idle_scan")}
                      for name, value in report.items() if name in ("baseline", "optimized")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
