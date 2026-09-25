"""One bounded NAS-A2 store-only read probe using an aged synthetic source."""

from __future__ import annotations

import ipaddress
import json
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import nas_a2_followup_probe as prior

base = prior.base
RUN_ROOT = prior.BASE_SCOPE / "runs" / "recovery-vector-20260925-r13"
PROJECT = "tianshu-accept-a2-store-20260925-r13"
SUBNET = ipaddress.ip_network("10.205.22.0/24")
VECTOR_IP = "10.205.22.11"
LOKI_IP = "10.205.22.10"
GUARD_PORT = 19524
EVENT_BASE_AGE_SECONDS = 3600
STORE_EXCLUSION_SECONDS = 1635
QUERY_INGESTERS_WITHIN_SECONDS = 3 * 3600
RETENTION_SECONDS = 48 * 3600
REJECT_OLD_SECONDS = 720 * 3600
ONLINE_SEND_TIMEOUT_SECONDS = 600
MIXED_QUERY_TIMEOUT_SECONDS = 180
R12_REPORT_SHA256 = "3eb58cf653f73583b59e211bd5eacc2a122908461353bb3f41533db6b147f09c"
R12_ARCHIVE_SHA256 = "5c591348c57a33446d713254913fd262939cb6cb0d3f72996707c18281d7b94e"


def _utc_from_ns(value: int) -> str:
    return datetime.fromtimestamp(value / 10**9, timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )


def _store_route_evidence(now_ns: int, query_start_ns: int, query_end_ns: int,
                          first_event_ns: int, latest_event_ns: int) -> dict:
    """Check the complete query window against the fixed Loki all/filesystem split."""
    cutoff_ns = now_ns - STORE_EXCLUSION_SECONDS * 10**9
    oldest_age = (now_ns - first_event_ns) / 10**9
    latest_age = (now_ns - latest_event_ns) / 10**9
    checks = {
        "ordered_event_and_query_bounds": (
            query_start_ns <= first_event_ns <= latest_event_ns <= query_end_ns
        ),
        "all_query_buckets_older_than_store_cutoff": query_end_ns <= cutoff_ns,
        "all_events_inside_three_hour_ingester_query_policy": (
            0 < latest_age <= oldest_age < QUERY_INGESTERS_WITHIN_SECONDS
        ),
        "all_events_inside_retention": oldest_age < RETENTION_SECONDS,
        "all_events_inside_reject_old_limit": oldest_age < REJECT_OLD_SECONDS,
    }
    return {
        "checked_at_utc": _utc_from_ns(now_ns),
        "timezone": "UTC",
        "source_first_event_utc": _utc_from_ns(first_event_ns),
        "source_latest_event_utc": _utc_from_ns(latest_event_ns),
        "query_start_utc": _utc_from_ns(query_start_ns),
        "query_end_utc": _utc_from_ns(query_end_ns),
        "store_exclusion_seconds": STORE_EXCLUSION_SECONDS,
        "store_cutoff_utc": _utc_from_ns(cutoff_ns),
        "oldest_event_age_seconds": round(oldest_age, 3),
        "latest_event_age_seconds": round(latest_age, 3),
        "nonempty_store_interval": query_start_ns <= min(query_end_ns, cutoff_ns),
        "checks": checks,
        "all_query_buckets_on_store_route": all(checks.values()),
    }


def _check_prior_r12() -> dict:
    root = prior.BASE_SCOPE / "runs" / "recovery-vector-20260925-r12"
    report_path = root / "evidence" / prior.REPORT_NAME
    archive_path = root / "evidence" / "tmpfs-snapshot-after-probe.tar.gz"
    base.require(base.sha_file(report_path) == R12_REPORT_SHA256,
                 "prior_r12_report_changed")
    base.require(base.sha_file(archive_path) == R12_ARCHIVE_SHA256,
                 "prior_r12_archive_changed")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    base.require(report["archive"]["tmpfs_unmounted_after_verification"],
                 "prior_r12_archive_unconfirmed")
    mountpoint = str(root / "tmpfs")
    base.require(all(line.split()[1] != mountpoint for line in
                     Path("/proc/mounts").read_text().splitlines()),
                 "prior_r12_tmpfs_still_mounted")
    for role in ("vector", "loki"):
        entry = report["containers"][role]
        container = base._container_by_id(entry["id"])
        state = container["State"]
        base.require(not state["Running"] and state["ExitCode"] == 0
                     and not state["OOMKilled"], "prior_r12_container_not_released")
    network = json.loads(base.docker("network", "inspect", report["network"]["name"]))[0]
    base.require(network["Internal"] is True and not network.get("Containers"),
                 "prior_r12_network_not_empty")
    return {"report_sha256": R12_REPORT_SHA256,
            "archive_sha256": R12_ARCHIVE_SHA256,
            "containers_stopped": True, "network_internal_empty": True,
            "tmpfs_unmounted": True}


def _query_until_exact(client, selector: str, start_ns: int, end_ns: int,
                       expected: Counter, deadline: float, attempts: list,
                       *, route_evidence=None, paths=None, hash_cache=None,
                       checkpoint=None) -> dict:
    next_query_at = time.monotonic()
    while time.monotonic() < deadline:
        query_now_ns = time.time_ns()
        routing = None
        if route_evidence is not None:
            routing = route_evidence(query_now_ns, start_ns, end_ns)
            if not routing["all_query_buckets_on_store_route"]:
                attempts.append({"started_at": prior._stamp(), "route": routing,
                                 "configuration_failure": "empty_or_invalid_store_interval"})
                if checkpoint is not None:
                    checkpoint()
                break
        attempt = {"started_at": prior._stamp()}
        if routing is not None:
            attempt["route"] = routing
            attempt["inventory"] = prior._loki_file_inventory(paths, hash_cache)
            attempt["loki_metrics"] = prior._loki_metrics_evidence(client)
        attempt["query"] = prior._query_all_events(
            client, selector, start_ns, end_ns, expected,
            deadline_monotonic=deadline,
        )
        attempt["finished_at"] = prior._stamp()
        attempts.append(attempt)
        if checkpoint is not None:
            checkpoint()
        if attempt["query"]["identity_hash_multiset_match"]:
            break
        next_query_at += 15
        time.sleep(min(max(0, next_query_at - time.monotonic()),
                       max(0, deadline - time.monotonic())))
    return attempts[-1] if attempts else {}


def _run_store_probe(paths: dict[str, Path], report: dict, clients: dict,
                     loki_id: str, cpu: str, checkpoint_path: Path,
                     run_start_monotonic: float) -> None:
    report["code_sha256"][Path(__file__).relative_to(base.ROOT).as_posix()] = (
        base.sha_file(Path(__file__))
    )
    report["preflight"]["prior_r12_fixed_state"] = _check_prior_r12()
    first_ns = time.time_ns() - EVENT_BASE_AGE_SECONDS * 10**9
    last_ns = first_ns + (prior.VECTOR_RECORDS - 1) * prior.VECTOR_TIMESTAMP_STEP_NS
    start_ns, end_ns = first_ns - 10**9, last_ns + 10**9
    projected = _store_route_evidence(
        time.time_ns() + prior.TOTAL_RUN_TIMEOUT_SECONDS * 10**9,
        start_ns, end_ns, first_ns, last_ns,
    )
    base.require(projected["all_query_buckets_on_store_route"],
                 "projected_store_route_invalid")
    source = paths["vector_source"] / "events.jsonl"
    expected, source_hash, fixture = prior._write_vector_backlog(source, first_ns)
    expected_pairs = Counter(expected)
    base.require(len(expected_pairs) == prior.VECTOR_RECORDS,
                 "synthetic_id_count_mismatch")
    config = prior._vector_config(paths["config"] / "vector.json", paths["tls"])
    report["config_sha256"]["vector"] = base.sha(config)
    selector = f'{{stack="tianshu",service="{prior.VECTOR_SERVICE}"}}'
    dimension = {
        "status": "running",
        "scenario": "aged_synthetic_store_only_read",
        "record_count": prior.VECTOR_RECORDS,
        "fixture": fixture,
        "source_sha256_before_send": source_hash,
        "source_first_event_utc": _utc_from_ns(first_ns),
        "source_latest_event_utc": _utc_from_ns(last_ns),
        "projected_latest_query_route": projected,
        "online_send": {"status": "running", "samples": [], "sample_errors": []},
        "mixed_query": {"status": "not_run", "attempts": []},
        "store_only": {"status": "not_run", "attempts": [], "snapshots": []},
    }
    report["dimensions"]["store_only_persistence"] = dimension
    report["dimensions"]["vector_buffer_full"] = {
        "status": "not_run", "reason": "R12 functional stall already captured"
    }
    prior._checkpoint(checkpoint_path, report)

    vector_id = base.docker(*prior._vector_args(paths, cpu)).strip()
    report["containers"]["vector"] = {
        "id": vector_id, "name": prior.VECTOR_NAME,
        "image_id": prior.VECTOR_IMAGE_ID,
    }
    base._check_owned(base._container_by_id(vector_id),
                      prior.VECTOR_NAME, prior.VECTOR_IMAGE_ID,
                      expected_id=vector_id)
    prior._checkpoint(checkpoint_path, report)
    send = dimension["online_send"]
    send_deadline = min(time.monotonic() + ONLINE_SEND_TIMEOUT_SECONDS,
                        run_start_monotonic + 800)
    while time.monotonic() < send_deadline:
        try:
            sample = prior._timestamped_vector_sample(
                paths, source, vector_id, loki_id
            )
        except base.ProbeError as exc:
            send["sample_errors"].append({"observed_at": prior._stamp(),
                                          "error": str(exc)})
            time.sleep(min(5.1, max(0, send_deadline - time.monotonic())))
            continue
        send["samples"].append(sample)
        metrics = sample["metrics"]
        prior._checkpoint(checkpoint_path, report)
        if (sample["source_sha256"] != source_hash or
                metrics["discarded_events"] > 0 or
                metrics["component_errors"] > 0 or
                not sample["vector_running"] or
                not sample["sink_running"]):
            send["failure"] = "source_changed_discard_error_or_container_exited"
            break
        if (metrics["source_sent_events"] >= prior.VECTOR_RECORDS and
                metrics["sink_sent_events"] >= prior.VECTOR_RECORDS and
                metrics["buffer_bytes"] == 0):
            send["drained"] = True
            break
        time.sleep(min(5.1, max(0, send_deadline - time.monotonic())))
    send["finished_at"] = prior._stamp()
    send["last_metrics"] = send["samples"][-1]["metrics"] if send["samples"] else None
    logs = prior._vector_logs(vector_id)
    prior._write_evidence_text(paths, "vector-online-logs.txt", logs)
    send["logs_sha256"] = base.sha(logs.encode())
    send["log_classification"] = prior._classify_vector_error_logs(
        logs, sink_offline=False
    )
    stop = base._stop_owned(vector_id, prior.VECTOR_NAME,
                            prior.VECTOR_IMAGE_ID, timeout=90)
    report["containers"]["vector"].update(stop)
    send["source_sha256_after_stop"] = base.sha_file(source)
    send["status"] = (
        "passed" if send.get("drained") and not send.get("failure")
        and send["source_sha256_after_stop"] == source_hash
        and send["log_classification"]["unexpected_error_count"] == 0
        else "failed"
    )
    prior._checkpoint(checkpoint_path, report)
    base.require(send["status"] == "passed", "online_send_failed")

    mixed = dimension["mixed_query"]
    mixed["status"] = "running"
    mixed_deadline = min(time.monotonic() + MIXED_QUERY_TIMEOUT_SECONDS,
                         run_start_monotonic + 950)
    final_mixed = _query_until_exact(
        clients["loki"], selector, start_ns, end_ns, expected_pairs,
        mixed_deadline, mixed["attempts"],
        checkpoint=lambda: prior._checkpoint(checkpoint_path, report),
    )
    mixed["status"] = (
        "passed" if final_mixed.get("query", {}).get("identity_hash_multiset_match")
        else "failed"
    )
    prior._checkpoint(checkpoint_path, report)
    base.require(mixed["status"] == "passed", "mixed_query_not_exact")

    store = dimension["store_only"]
    store["status"] = "running"
    hash_cache = {}
    store["snapshots"].append({
        "phase": "before_flush",
        "inventory": prior._loki_file_inventory(paths, hash_cache),
        "metrics": prior._loki_metrics_evidence(clients["loki"]),
    })
    flush_at = prior._stamp()
    flush_status, _, _ = clients["loki"].request("/flush", "POST", b"")
    store["flush"] = {"called_at": flush_at, "http_status": flush_status,
                       "completion_proven_by_status": False}
    store["snapshots"].append({
        "phase": "after_flush_response",
        "inventory": prior._loki_file_inventory(paths, hash_cache),
        "metrics": prior._loki_metrics_evidence(clients["loki"]),
    })
    prior._checkpoint(checkpoint_path, report)
    base.require(flush_status == 204, "unexpected_flush_http_status")
    transition = base._stop_owned(loki_id, prior.LOKI_NAME,
                                  prior.LOKI_IMAGE_ID, timeout=90)
    report["containers"]["loki"].setdefault("transitions", []).append(transition)
    store["snapshots"].append({
        "phase": "after_loki_stop",
        "inventory": prior._loki_file_inventory(paths, hash_cache),
    })
    store_config = prior._write_loki_config(
        paths["config"] / "loki.json", query_store_only=True
    )
    report["config_sha256"]["loki_store_only"] = base.sha(store_config)
    base._start_existing(loki_id, prior.LOKI_NAME, prior.LOKI_IMAGE_ID)
    store_deadline = min(time.monotonic() + prior.STORE_ONLY_QUERY_TIMEOUT_SECONDS,
                         run_start_monotonic + prior.WORK_TIMEOUT_SECONDS)
    remaining = store_deadline - time.monotonic()
    base.require(remaining > 0, "store_only_time_budget_exceeded")
    base._wait(lambda: base._loki_ready(clients["loki"]),
               timeout=min(90, remaining), code="loki_store_only_ready_timeout")
    store["snapshots"].append({
        "phase": "after_store_restart",
        "inventory": prior._loki_file_inventory(paths, hash_cache),
        "metrics": prior._loki_metrics_evidence(clients["loki"]),
    })
    prior._checkpoint(checkpoint_path, report)
    route = lambda now, start, end: _store_route_evidence(
        now, start, end, first_ns, last_ns
    )
    final_store = _query_until_exact(
        clients["loki"], selector, start_ns, end_ns, expected_pairs,
        store_deadline, store["attempts"], route_evidence=route,
        paths=paths, hash_cache=hash_cache,
        checkpoint=lambda: prior._checkpoint(checkpoint_path, report),
    )
    store["finished_at"] = prior._stamp()
    store["status"] = (
        "passed" if final_store.get("route", {}).get("all_query_buckets_on_store_route")
        and final_store.get("query", {}).get("identity_hash_multiset_match")
        else "failed"
    )
    dimension["status"] = "passed" if store["status"] == "passed" else "failed"
    prior._checkpoint(checkpoint_path, report)


def main() -> int:
    base.require(SUBNET.subnet_of(ipaddress.ip_network("10.205.16.0/20")),
                 "subnet_outside_assigned_range")
    prior.RUN_ROOT = RUN_ROOT
    prior.PROJECT = PROJECT
    prior.NETWORK = PROJECT
    prior.SUBNET = SUBNET
    prior.VECTOR_NAME = PROJECT + "-vector"
    prior.LOKI_NAME = PROJECT + "-loki"
    prior.VECTOR_IP = VECTOR_IP
    prior.LOKI_IP = LOKI_IP
    prior.GUARD_PORT = GUARD_PORT
    prior.VECTOR_SERVICE = "nas-a2-vector-r13-" + uuid.uuid4().hex[:12]
    prior.RECOVERY_SERVICE = "nas-a2-recovery-r13-" + uuid.uuid4().hex[:12]
    prior.VECTOR_BUFFER_FULL_TIMEOUT_SECONDS = 0
    prior._run_vector_buffer = _run_store_probe
    return prior._run_probe(RUN_ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
