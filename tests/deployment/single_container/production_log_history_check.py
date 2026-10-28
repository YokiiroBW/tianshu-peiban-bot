"""Read-only, aggregate two-window history/backfill diagnostic for the running host.

Run inside the existing container as UID 10001. Only GETs, file reads and one
mode=ro/query_only SQLite snapshot per window are used. No second app, Ledger,
schema initialization, migration, retention action, business body, event ID,
instance UUID or credential is emitted. JSON goes only to stdout.
"""

import argparse
from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import sqlite3
import sys
import time

sys.dont_write_bytecode = True


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host-source", type=Path, default=Path("/opt/tianshu/application"))
    parser.add_argument("--guard-config", type=Path, default=Path("/config/observability/guard.json"))
    parser.add_argument("--host-config", type=Path, default=Path("/config/host.json"))
    parser.add_argument("--interval-seconds", type=float, default=45)
    options = parser.parse_args()
    if not 0 < options.interval_seconds <= 60:
        parser.error("interval-seconds must be in (0,60]")
    return options


def read(path):
    return json.loads(Path(path).read_bytes())


def utc(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat() if value is not None else None


def age(now, value):
    return round(now - value, 3) if value else None


def opaque(*values):
    return hashlib.sha256("\0".join(map(str, values)).encode()).hexdigest()[:20]


def aggregate_prometheus(raw):
    """Keep only numbers and known component names, never arbitrary labels."""
    result = defaultdict(list)
    for line in raw.decode("utf-8", "replace").splitlines():
        if line.startswith("#"):
            continue
        match = re.fullmatch(r"([a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{([^}]*)\})?\s+([-+0-9.eE]+)(?:\s+\d+)?", line)
        if not match:
            continue
        try:
            value = float(match[3])
        except ValueError:
            continue
        if not math.isfinite(value):
            continue
        labels = dict(re.findall(r'([a-zA-Z_][a-zA-Z0-9_]*)="((?:\\.|[^"])*)"', match[2] or ""))
        result[match[1]].append((labels.get("component_id"), value))
    return result


def metric_sum(samples, name, component=None):
    values = [value for owner, value in samples.get(name, ()) if component is None or owner == component]
    return sum(values) if values else None


def observations(cfg, host_cfg, vector_cfg):
    from query import LokiClient
    result = {}
    guard_origin = host_cfg.get("modules", {}).get("platform", {}).get("flow_query", {}).get("url", "https://obs-guard:8445")
    token = Path(cfg["tokens"]["metrics"]).read_text().strip()
    client = LokiClient(guard_origin, cfg["ca"], token=token)
    try:
        for path, label in (("/metrics", "guard"), ("/ready", "ready"), ("/backend-metrics", "backend")):
            try:
                status, body, _ = client.request(path)
                result[label + "_http"] = status
                if status != 200 or label == "ready":
                    continue
                parsed = aggregate_prometheus(body)
                if label == "guard":
                    result["guard"] = {name.removeprefix("tianshu_obs_"): metric_sum(parsed, name) for name in parsed if name.startswith("tianshu_obs_")}
                else:
                    names = (
                        "loki_compactor_pending_delete_requests_count", "loki_compactor_delete_requests_received_total",
                        "loki_compactor_delete_requests_processed_total", "loki_ingester_memory_chunks",
                        "loki_ingester_memory_streams", "loki_distributor_lines_received_total",
                    )
                    result["backend"] = {name: metric_sum(parsed, name) for name in names if name in parsed}
            except Exception as error:
                result[label + "_error_type"] = type(error).__name__
    finally:
        client.close()
    vector = {"acknowledgements_enabled": vector_cfg.get("sinks", {}).get("loki", {}).get("acknowledgements", {}).get("enabled") is True}
    sink = vector_cfg.get("sinks", {}).get("loki", {})
    vector["buffer_type"] = sink.get("buffer", {}).get("type")
    vector["buffer_when_full"] = sink.get("buffer", {}).get("when_full")
    address = vector_cfg.get("sinks", {}).get("metrics", {}).get("address", "127.0.0.1:9598")
    port = int(address.rsplit(":", 1)[-1])
    client = LokiClient("https://obs-vector:" + str(port), cfg["ca"])
    try:
        status, body, _ = client.request("/metrics")
        vector["http"] = status
        if status == 200:
            parsed = aggregate_prometheus(body)
            for label, name, component in (
                ("buffer_bytes", "vector_buffer_size_bytes", "loki"),
                ("buffer_events", "vector_buffer_events", "loki"),
                ("buffer_max_bytes", "vector_buffer_max_size_bytes", "loki"),
                ("sink_sent_events", "vector_component_sent_events_total", "loki"),
                ("discarded_events", "vector_component_discarded_events_total", None),
                ("buffer_discarded_events", "vector_buffer_discarded_events_total", None),
                ("component_errors", "vector_component_errors_total", None),
            ):
                vector[label] = metric_sum(parsed, name, component)
            vector["sources_sent_events"] = {
                name: metric_sum(parsed, "vector_component_sent_events_total", name)
                for name in ("platform", "companion", "memory", "knowledge", "gateway")
            }
    except Exception as error:
        vector["error_type"] = type(error).__name__
    finally:
        client.close()
    result["vector"] = vector
    return result


def ledger_snapshot(path):
    # Do not use immutable=1: the live writer's WAL must be visible.
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=3)) as db:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        try:
            return {
                "schema": db.execute("PRAGMA user_version").fetchone()[0],
                "totals": db.execute("SELECT landed,pending FROM totals WHERE singleton=1").fetchone(),
                "events": db.execute("SELECT id,service,instance,sequence,hash,first_seen,verified,last_checked,occurred FROM events").fetchall(),
                "files": {name: (identity, offset) for name, identity, offset in db.execute("SELECT name,identity,offset FROM files")},
                "receipts": {(name, identity): (size, coverage) for name, identity, size, coverage in db.execute("SELECT name,identity,size,coverage FROM source_receipts")},
                "retired": {(service, instance): through for service, instance, through in db.execute("SELECT service,instance,through FROM retired_streams")},
                "streams": db.execute("SELECT service,instance,landed,maximum FROM streams").fetchall(),
                "deletes": db.execute("SELECT service,instance,through,submitted FROM retention_deletes").fetchall(),
                "control": db.execute("SELECT clear_before,scan_after FROM retention_control WHERE singleton=1").fetchone(),
                "counters": dict(db.execute("SELECT name,value FROM counters")),
                "sequence_conflicts": db.execute("SELECT count(*) FROM sequence_conflicts").fetchone()[0],
            }
        finally:
            db.rollback()


def generation(instance, instances):
    return "current" if instance in set(instances.values()) else "prior" if instances else "unknown"


def source_snapshot(cfg, state, instances, policy, now):
    from policy import event_time, segments
    from source_retention import closed, native_segment
    internal, sources = [], {}
    for service, root in cfg["log_roots"].items():
        counts = Counter()
        files = []
        try:
            paths = list(segments(root))
        except (OSError, ValueError) as error:
            sources[service] = {"enumeration_error_type": type(error).__name__}
            continue
        highest = {}
        for path in paths:
            segment = native_segment(service, path.name)
            if segment is not None:
                highest[segment[0]] = max(highest.get(segment[0], -1), segment[1])
        oldest_unread = None
        for path in paths:
            try:
                with path.open("rb") as stream:
                    stat = os.fstat(stream.fileno())
                    first = stream.readline(4097)
                    identity = f"{stat.st_dev}:{stat.st_ino}:" + hashlib.sha256(first).hexdigest()
                    name = service + "/" + path.name
                    checkpoint = state["files"].get(name)
                    matches = checkpoint is not None and checkpoint[0] == identity and 0 <= checkpoint[1] <= stat.st_size
                    offset = checkpoint[1] if matches else 0
                    row = {"source": service, "name": name, "identity": identity, "size": stat.st_size, "offset": offset}
                    segment = native_segment(service, path.name)
                    sealed = segment is not None and closed(service, segment, highest, instances)
                    counts["file_count"] += 1
                    counts["source_bytes"] += stat.st_size
                    counts["effective_scan_offset_bytes"] += offset
                    counts["unscanned_bytes"] += stat.st_size - offset
                    counts["matched_checkpoints"] += matches
                    counts["missing_checkpoints"] += checkpoint is None
                    counts["mismatched_or_truncated_checkpoints"] += checkpoint is not None and not matches
                    counts["closed_files"] += sealed
                    counts["closed_unscanned_bytes"] += (stat.st_size - offset) if sealed else 0
                    counts["non_native_segments"] += segment is None
                    counts["non_single_link_files"] += stat.st_nlink != 1
                    if stat.st_size > offset:
                        stream.seek(offset)
                        raw = stream.readline(4097)
                        try:
                            pending = policy.line(raw)
                            occurred = event_time(pending)
                            oldest_unread = occurred if oldest_unread is None else min(oldest_unread, occurred)
                            counts[generation(pending["instance_id"], instances) + "_unscanned_first_lines"] += 1
                        except ValueError:
                            counts["partial_or_invalid_next_lines"] += 1
                    receipt = state["receipts"].get((name, identity))
                    row["receipt"] = receipt
                    if receipt and receipt[0] <= offset:
                        counts["receipted_prefix_bytes"] += receipt[0]
                        counts["fully_receipted_files"] += receipt[0] == stat.st_size
                    row["sealed"] = bool(sealed)
                    internal.append(row)
                    files.append({"file_key": opaque(name), "bytes": stat.st_size, "offset": offset, "unscanned_bytes": stat.st_size - offset, "closed": bool(sealed), "mtime_age_seconds": age(now, stat.st_mtime)})
            except (OSError, ValueError):
                counts["file_read_races_or_errors"] += 1
        disk = shutil.disk_usage(root)
        budget = cfg["log_budgets"].get(service)
        sources[service] = {
            **dict(counts), "configured_byte_budget": budget,
            "quota_ratio": counts["source_bytes"] / budget if budget else None,
            "filesystem_free_bytes": disk.free,
            "oldest_first_unscanned_event_age_seconds": age(now, oldest_unread),
            "files": files,
        }
    return internal, sources


def summarize_ledger(state, instances, now):
    groups = defaultdict(list)
    stream_events = defaultdict(list)
    for event in state["events"]:
        stream_events[event[1], event[2]].append(event)
        if event[6] == 0:
            groups[event[1], event[2]].append(event)
    pending = []
    for (service, instance), events in sorted(groups.items()):
        first = min(event[5] for event in events)
        occurred = [event[8] for event in events if event[8] is not None]
        checked = [event[7] for event in events if event[7] > 0]
        pending.append({
            "service": service, "stream_key": opaque(service, instance), "generation": generation(instance, instances),
            "events": len(events), "never_checked": sum(event[7] == 0 for event in events),
            "oldest_first_seen_age_seconds": age(now, first),
            "oldest_original_event_age_seconds": age(now, min(occurred) if occurred else None),
            "oldest_last_checked_age_seconds": age(now, min(checked) if checked else None),
        })
    combined = defaultdict(list)
    for _, encoded in state["receipts"].values():
        for service, instance, spans in json.loads(encoded):
            combined[service, instance].extend(spans)
    coverage = dict(state["retired"])
    for key, spans in combined.items():
        end = coverage.get(key, 0)
        for start, last in sorted(spans):
            if start > end + 1:
                break
            end = max(end, last)
        coverage[key] = end
    heads = Counter()
    for key, events in stream_events.items():
        through = state["retired"].get(key, 0)
        head = min((event for event in events if event[3] > through), key=lambda event: event[3], default=None)
        if head is None:
            continue
        reason = "sequence_gap" if head[3] != through + 1 else "pending" if head[6] <= 0 else "unproved_source_prefix" if head[3] > coverage.get(key, through) else "eligible_confirmed_prefix"
        heads[reason] += 1
    retired = defaultdict(lambda: {"streams": 0, "watermark_sequence_sum": 0})
    for (service, instance), through in state["retired"].items():
        item = retired[service + ":" + generation(instance, instances)]
        item["streams"] += 1
        item["watermark_sequence_sum"] += through
    landed, pending_total = state["totals"]
    return {
        "schema": state["schema"], "landed_events": landed, "pending_events": pending_total,
        "aggregate_matches_rows": landed == len(state["events"]) and pending_total == sum(event[6] == 0 for event in state["events"]),
        "pending_groups": pending, "retired_by_service_generation": dict(retired),
        "retention_prefix_heads": dict(heads), "source_receipt_rows": len(state["receipts"]),
        "retention_delete_unsubmitted_streams": sum(through > submitted for _, _, through, submitted in state["deletes"]),
        "retention_delete_unsubmitted_watermark_distance": sum(max(0, through - submitted) for _, _, through, submitted in state["deletes"]),
        "sequence_conflicts": state["sequence_conflicts"], "clear_before_utc": utc(state["control"][0]) if state["control"][0] else None,
        "counters": state["counters"],
    }


def snapshot(options, cfg, host_cfg, vector_cfg, policy):
    from source_retention import active_instances
    started = time.time()
    host_path = cfg.get("host_state_path", host_cfg.get("state_file"))
    instances = active_instances(host_path, started)
    host_state = read(host_path) if host_path else {}
    state = ledger_snapshot(cfg["ledger"])
    files, sources = source_snapshot(cfg, state, instances, policy, started)
    observed = observations(cfg, host_cfg, vector_cfg)
    finished = time.time()
    public = {
        "started_utc": utc(started), "finished_utc": utc(finished), "duration_seconds": round(finished - started, 3),
        "host": {"state": host_state.get("status"), "registry_valid": bool(instances), "current_instances": len(instances), "state_age_seconds": age(finished, host_state.get("updated_at"))},
        "sources": sources, "ledger": summarize_ledger(state, instances, finished), "observations": observed,
        "ledger_physical_bytes": {suffix or "database": Path(str(cfg["ledger"]) + suffix).stat().st_size if Path(str(cfg["ledger"]) + suffix).exists() else 0 for suffix in ("", "-wal", "-shm")},
    }
    return {"public": public, "state": state, "files": files, "instances": instances, "finished": finished}


def comparison(first, second, cfg):
    old, new = first["state"], second["state"]
    current = {event[0]: event for event in new["events"]}
    events = Counter()
    old_pending_outcomes = Counter()
    clear_unchanged = old["control"][0] == new["control"][0]
    for event in old["events"]:
        found = current.get(event[0])
        if found is not None:
            state = "hash_or_stream_mismatch" if found[1:5] != event[1:5] else "verified" if found[6] > 0 else "pending"
        else:
            retired = new["retired"].get((event[1], event[2]), 0) >= event[3]
            state = "normally_retired" if retired and clear_unchanged else "operator_clear_changed" if retired else "unaccounted"
        events[state] += 1
        if event[6] == 0:
            old_pending_outcomes[state] += 1
    by_identity = defaultdict(list)
    for row in second["files"]:
        by_identity[row["source"], row["identity"]].append(row)
    frontier = Counter()
    for row in first["files"]:
        matches = by_identity.get((row["source"], row["identity"]), ())
        if row["size"] == 0 or any(item["offset"] >= row["size"] for item in matches):
            frontier["consumed_files"] += 1
            frontier["consumed_bytes"] += row["size"]
            continue
        receipt = row["receipt"]
        covered = receipt and receipt[0] == row["size"] and all(last <= new["retired"].get((service, instance), 0) for service, instance, spans in json.loads(receipt[1]) for _, last in spans)
        if not matches and covered and clear_unchanged:
            frontier["receipt_and_retirement_proved_removed_files"] += 1
            frontier["receipt_and_retirement_proved_removed_bytes"] += row["size"]
        elif matches:
            frontier["still_unconsumed_files"] += 1
            frontier["remaining_baseline_bytes"] += min(max(0, row["size"] - item["offset"]) for item in matches)
        else:
            frontier["identity_changed_or_unproved_removed_files"] += 1
    historical_pending = [event for event in new["events"] if event[6] == 0 and (event[5] <= first["finished"] or event[8] is None or event[8] <= first["finished"])]
    prior_pending = [event for event in new["events"] if event[6] == 0 and generation(event[2], second["instances"]) != "current"]
    a = first["public"]["observations"].get("guard", {})
    b = second["public"]["observations"].get("guard", {})
    va = first["public"]["observations"]["vector"]
    vb = second["public"]["observations"]["vector"]
    counter_delta = {name: new["counters"].get(name, 0) - old["counters"].get(name, 0) for name in set(old["counters"]) | set(new["counters"])}
    vector_deltas = {key: vb[key] - va[key] for key in ("sink_sent_events", "discarded_events", "buffer_discarded_events", "component_errors") if vb.get(key) is not None and va.get(key) is not None}
    fatal_counters = ("invalid_source_lines", "identity_conflicts", "source_changed", "retrieval_conflicts", "invalid_retrieved_lines")
    interval = cfg.get("interval_seconds", 30)
    conditions = {
        "both_host_registries_valid": bool(first["instances"]) and bool(second["instances"]),
        "clear_cutoff_unchanged": clear_unchanged,
        "baseline_file_frontier_consumed_or_proved_retired": not frontier["still_unconsumed_files"] and not frontier["identity_changed_or_unproved_removed_files"],
        "baseline_ledger_all_verified_or_normally_retired": sum(events.values()) == len(old["events"]) and not any(events[key] for key in ("hash_or_stream_mismatch", "pending", "operator_clear_changed", "unaccounted")),
        "no_pending_at_or_before_first_window": not historical_pending,
        "no_prior_or_unknown_instance_pending": not prior_pending,
        "both_ledger_aggregates_match": first["public"]["ledger"]["aggregate_matches_rows"] and second["public"]["ledger"]["aggregate_matches_rows"],
        "logical_event_cap_held": len(new["events"]) <= cfg.get("log_max_events", cfg.get("ledger_max_events", 10000)),
        "no_source_read_errors": all(not value.get("enumeration_error_type") and not value.get("file_read_races_or_errors") for value in first["public"]["sources"].values()) and all(not value.get("enumeration_error_type") and not value.get("file_read_races_or_errors") for value in second["public"]["sources"].values()),
        "guard_ready_and_recent_success": second["public"]["observations"].get("ready_http") == 200 and b.get("monitor_success") == 1 and 0 <= second["finished"] - b.get("monitor_last_success_timestamp", 0) <= 120,
        "latest_scan_not_budget_exhausted": b.get("scan_budget_exhausted") == 0,
        "no_current_capacity_retention_or_gap_fault": all(b.get(key) == 0 for key in ("ledger_capacity_rejected_current", "source_retention_failures", "retention_delete_failures_current", "sequence_gaps")),
        "query_failures_did_not_increase": b.get("query_failures_total") is not None and a.get("query_failures_total") is not None and b["query_failures_total"] == a["query_failures_total"],
        "integrity_counters_zero": not any(new["counters"].get(key, 0) for key in fatal_counters) and new["sequence_conflicts"] == 0,
        "vector_ack_configured_and_metrics_available": vb.get("acknowledgements_enabled") is True and vb.get("http") == 200 and vb.get("buffer_bytes") is not None,
        "vector_error_and_discard_counters_did_not_increase_or_reset": all(value == 0 for key, value in vector_deltas.items() if key != "sink_sent_events") and vector_deltas.get("sink_sent_events", 0) >= 0,
    }
    complete = all(conditions.values())
    unread = sum(value.get("unscanned_bytes", 0) for value in second["public"]["sources"].values())
    old_pending_resolved = clear_unchanged and not historical_pending and not prior_pending and not any(old_pending_outcomes[key] for key in ("hash_or_stream_mismatch", "pending", "operator_clear_changed", "unaccounted"))
    old_identities = {(row["source"], row["identity"]) for row in first["files"]}
    new_identities = {(row["source"], row["identity"]) for row in second["files"]}
    source_deltas = {}
    for source in set(first["public"]["sources"]) | set(second["public"]["sources"]):
        before = first["public"]["sources"].get(source, {})
        after = second["public"]["sources"].get(source, {})
        source_deltas[source] = {key: after.get(key, 0) - before.get(key, 0) for key in ("file_count", "source_bytes", "effective_scan_offset_bytes", "unscanned_bytes", "closed_unscanned_bytes")}
    return {
        "historical_valid_source_frontier_complete": complete,
        "history_frontier_consumed": conditions["baseline_file_frontier_consumed_or_proved_retired"] and conditions["no_source_read_errors"],
        "old_pending_resolved": old_pending_resolved,
        "old_pending_outcomes": dict(old_pending_outcomes),
        "conditions": conditions, "baseline_ledger_outcomes": dict(events), "baseline_file_frontier": dict(frontier),
        "second_window_historical_pending_events": len(historical_pending), "second_window_prior_or_unknown_pending_events": len(prior_pending),
        "counter_deltas": counter_delta, "vector_counter_deltas": vector_deltas,
        "two_window_deltas": {
            "landed_events": new["totals"][0] - old["totals"][0],
            "pending_events": new["totals"][1] - old["totals"][1],
            "retired_watermark_sequence_sum": sum(new["retired"].values()) - sum(old["retired"].values()),
            "new_source_file_identities": len(new_identities - old_identities),
            "disappeared_source_file_identities": len(old_identities - new_identities),
            "new_file_identity_unscanned_bytes": sum(row["size"] - row["offset"] for row in second["files"] if (row["source"], row["identity"]) not in old_identities),
            "recent_current_pending_events": sum(event[6] == 0 and event[5] > first["finished"] and event[8] is not None and event[8] > first["finished"] and generation(event[2], second["instances"]) == "current" for event in new["events"]),
            "source_deltas": source_deltas,
        },
        "completely_idle_at_second_window": complete and not new["totals"][1] and not unread and vb.get("buffer_bytes") == 0,
        "interval_seconds": interval,
        "notes": [
            "Completion applies to valid policy-admitted source bytes present in window A and retained/normal-retired ledger facts. The operator clear cutoff and its counters are reported separately.",
            "New window-B active-tail bytes, recent current-instance pending and nonempty Vector buffers can be real-time traffic. They do not by themselves prove historical backfill remains.",
            "Native source offset is scan progress, not ACK. Verified ledger hashes reflect real Loki query matches; source receipts and normal retired prefixes preserve later reclamation evidence.",
            "Vector sent counters or ACK-enabled configuration alone are not delivery proof. Logical buffers and counter deltas are reported alongside the ledger evidence.",
            "Loki delete submission is asynchronous; submitted watermarks or retained-query cap do not imply chunks/files have already been physically removed.",
            "A vanished file without an already-recorded exact prefix receipt is inconclusive in this pair, even when ordinary pruning may explain it; repeat a pair instead of inventing proof.",
        ],
    }


def main():
    options = arguments()
    sys.path.insert(0, str(options.host_source / "deploy/observability"))
    from policy import Policy
    cfg = read(options.guard_config)
    host_cfg = read(options.host_config)
    vector_cfg = read(options.guard_config.with_name("vector.json"))
    policy = Policy(read(cfg["snapshot"]))
    policy.verify_contract(Path(cfg["contract"]))
    first = snapshot(options, cfg, host_cfg, vector_cfg, policy)
    time.sleep(options.interval_seconds)
    second = snapshot(options, cfg, host_cfg, vector_cfg, policy)
    result = {
        "diagnostic": "read_only_two_window_log_catchup", "uid": os.geteuid() if hasattr(os, "geteuid") else None,
        "sample_separation_seconds": round(second["finished"] - first["finished"], 3),
        "configured_interval_seconds": cfg.get("interval_seconds", 30),
        "configured_max_events": cfg.get("log_max_events", cfg.get("ledger_max_events", 10000)), "configured_retention_days": cfg.get("retention_days", 30),
        "policy_contract_verified": True, "database_open_mode": "ro/query_only/consistent_WAL_read", "database_writes_requested": False,
        "windows": [first["public"], second["public"]], "comparison": comparison(first, second, cfg),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Exception messages/tracebacks could contain a URL, path or source body.
        print(json.dumps({"diagnostic": "read_only_two_window_log_catchup", "diagnostic_error_type": type(error).__name__, "database_writes_requested": False}))
        raise SystemExit(1)
