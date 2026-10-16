"""Operator-run read-only acceptance against an explicitly registered node.

Raw responses stay in memory. The receipt contains selected numeric/status facts
and opaque references, never connection fields, names, paths or log bodies.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import uuid
from collections import Counter
from pathlib import Path

from .protocol import CONTAINER_ID, MAX_INVENTORY, MAX_RESPONSE_BYTES, VERSION, Failure, decode_json, parse_request, utc_now
from .transport import Deadline, run_bounded


def reference(value):
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:32] if value is not None else None


def counts(values):
    return dict(sorted(Counter(value for value in values if value is not None).items()))


class Peer:
    def __init__(self, *, config, node_id, contract_directory, python=sys.executable,
                 executor_root=None, launcher=None, seconds=300, timeout_ms=30000):
        from jsonschema import Draft202012Validator, FormatChecker

        self.node_id = node_id
        self.root = Path(executor_root or Path(__file__).resolve().parents[2])
        if launcher:
            self.command = [python, "-I", "-B", str(Path(launcher).resolve()), "--config", str(Path(config).resolve())]
        else:
            bootstrap = "import runpy,sys;sys.path.insert(0,sys.argv.pop(1));runpy.run_module('ops.infrastructure',run_name='__main__')"
            self.command = [python, "-c", bootstrap, str(self.root), "--config", str(Path(config).resolve())]
        schema = decode_json((Path(contract_directory) / "response.schema.json").read_bytes())
        Draft202012Validator.check_schema(schema)
        self.validator = Draft202012Validator(schema, format_checker=FormatChecker())
        self.deadline = Deadline(seconds)
        self.timeout_ms = timeout_ms
        self.calls = []

    def invoke(self, operation, params=None):
        milliseconds = min(self.timeout_ms, int(self.deadline.remaining * 1000))
        if milliseconds < 500:
            raise Failure("acceptance_deadline_exceeded")
        request = {"version": VERSION, "request_id": "acceptance:" + uuid.uuid4().hex,
                   "node_id": None if operation == "node.catalog" else self.node_id,
                   "operation": operation, "params": params or {}, "timeout_ms": milliseconds}
        parse_request(json.dumps(request).encode())
        started = time.monotonic()
        # The same process invocation used by deployment; no collector bypass.
        result = run_bounded(self.command, (json.dumps(request) + "\n").encode(),
                             Deadline(min(self.deadline.remaining, milliseconds / 1000 + 2)),
                             max_output=MAX_RESPONSE_BYTES + 1)
        if result.reason:
            raise Failure("acceptance_executor_" + result.reason)
        try:
            response = decode_json(result.stdout)
            if next(self.validator.iter_errors(response), None):
                raise ValueError("response schema")
            if any(response.get(key) != request[key] for key in ("version", "request_id", "node_id", "operation")):
                raise ValueError("response identity")
        except (ValueError, TypeError, UnicodeError, RecursionError):
            raise Failure("acceptance_response_invalid") from None
        self.calls.append({"operation": operation, "status": response["status"],
                           "reason_codes": response["reason_codes"], "observed_at": response["observed_at"],
                           "elapsed_ms": round((time.monotonic() - started) * 1000)})
        return response


def data_of(response):
    if response["status"] not in ("ok", "partial") or response["data"] is None:
        raise Failure("acceptance_observation_unavailable")
    return response["data"]


def inventory(peer, operation, key):
    items = []
    cursor = None
    total = None
    seen_cursors = set()
    for _ in range((MAX_INVENTORY + 199) // 200 + 1):
        response = peer.invoke(operation, {"limit": 200, "cursor": cursor})
        rows = data_of(response)[key]
        complete = response["completeness"]
        if complete["returned"] != len(rows) or complete["total"] is None:
            raise Failure("acceptance_page_count_invalid")
        if total is not None and complete["total"] != total:
            raise Failure("acceptance_inventory_changed")
        total = complete["total"]
        items.extend(rows)
        identities = [item["id"] for item in items]
        if len(identities) != len(set(identities)) or len(items) > MAX_INVENTORY:
            raise Failure("acceptance_inventory_duplicates_or_limit")
        cursor = complete["next_cursor"]
        if cursor is None:
            if len(items) != total:
                raise Failure("acceptance_inventory_incomplete")
            return items
        if not rows or cursor in seen_cursors:
            raise Failure("acceptance_cursor_repeated")
        seen_cursors.add(cursor)
    raise Failure("acceptance_page_limit")


def snapshot_summary(response):
    data = data_of(response)
    router = data["openwrt"]
    return {"status": response["status"], "reason_codes": response["reason_codes"],
            "observed_at": response["observed_at"], "kind": data["identity"]["kind"],
            "boot_ref": reference(data["identity"]["boot_id"]), "system": data["system"],
            "cpu": data["cpu"], "memory": data["memory"],
            "filesystems": [{"ref": reference(item["id"]), **{key: item[key] for key in ("total_bytes", "used_bytes", "available_bytes", "source")}} for item in data["filesystems"]],
            "interfaces": [{"ref": reference(item["id"]), **{key: item[key] for key in ("receive_bytes", "transmit_bytes", "receive_bytes_per_second", "transmit_bytes_per_second", "state", "source")}} for item in data["interfaces"]],
            "openwrt": {"board_available": router["board"] is not None, "info_available": router["info"] is not None,
                        "interface_count": len(router["interfaces"]), "per_device_traffic_state": router["per_device_traffic"]["state"],
                        "reason_codes": router["reason_codes"]} if router else None}


def metadata_summary(items):
    rows = [{"ref": reference(item["id"]), "started_at_ref": reference(item["started_at"]),
             "availability": item["availability"], "state": item["state"], "health": item["health"]} for item in items]
    return {"count": len(items), "more_than_64": len(items) > 64,
            "identity_set_digest": hashlib.sha256("\0".join(sorted(item["id"] for item in items)).encode()).hexdigest(),
            "availability_counts": counts(item["availability"] for item in items),
            "state_counts": counts(item["state"] for item in items), "health_counts": counts(item["health"] for item in items),
            "compose_project_count": len({item["compose"]["project"] for item in items if item["compose"] and item["compose"]["project"]}),
            "reason_counts": counts(reason for item in items for reason in item["reason_codes"]), "identities": rows}


def metric_samples(peer, identities):
    result = {}
    for start in range(0, len(identities), 200):
        selected = identities[start:start + 200]
        response = peer.invoke("docker.stats", {"container_ids": selected})
        rows = data_of(response)["containers"]
        if len(rows) != len(selected) or {item["id"] for item in rows} != set(selected):
            raise Failure("acceptance_stats_identities_invalid")
        result.update((item["id"], item) for item in rows)
    return result


def metric_summary(items):
    rows = list(items.values())
    return {"count": len(rows), "availability_counts": counts(item["availability"] for item in rows),
            "cpu_state_counts": counts(item["cpu"]["state"] for item in rows if item["cpu"]),
            "cpu_basis_counts": counts(item["cpu"]["basis"] for item in rows if item["cpu"]),
            "memory_raw_count": sum(item["memory"] is not None and item["memory"]["raw_usage_bytes"] is not None for item in rows),
            "memory_adjusted_count": sum(item["memory"] is not None and item["memory"]["cache_adjusted_usage_bytes"] is not None for item in rows),
            "network_attribution_counts": counts(item["network"]["attribution"] for item in rows if item["network"]),
            "reason_counts": counts(reason for item in rows for reason in item["reason_codes"])}


def metric_differences(before, after):
    result = Counter()
    for identity in before.keys() & after.keys():
        old, new = before[identity], after[identity]
        if old["availability"] != "ok" or new["availability"] != "ok":
            result["unavailable"] += 1
            continue
        if new["cpu"] and new["cpu"]["state"] == "counter_reset":
            result["counter_reset"] += 1
            continue
        for field, keys in (("block_io", ("read_bytes", "write_bytes")), ("network", ("receive_bytes", "transmit_bytes"))):
            left, right = old[field], new[field]
            if not left or not right or any(left[key] is None or right[key] is None for key in keys):
                result[field + "_unknown"] += 1
            elif any(right[key] < left[key] for key in keys):
                result[field + "_reset"] += 1
            elif any(right[key] > left[key] for key in keys):
                result[field + "_increased"] += 1
            else:
                result[field + "_unchanged"] += 1
    return dict(sorted(result.items()))


def compare_receipts(before, current):
    old = {item["ref"]: item for item in before.get("docker", {}).get("identities", [])}
    new = {item["ref"]: item for item in current.get("docker", {}).get("identities", [])}
    old_boot = before.get("snapshot_second", {}).get("boot_ref")
    new_boot = current.get("snapshot_first", {}).get("boot_ref")
    return {"same_node_ref": before.get("node_ref") == current.get("node_ref"),
            "boot_identity_same": old_boot == new_boot if old_boot and new_boot else None,
            "new_container_count": len(new.keys() - old.keys()), "retired_container_count": len(old.keys() - new.keys()),
            "restarted_same_identity_count": sum(old[key]["started_at_ref"] != new[key]["started_at_ref"] for key in old.keys() & new.keys())}


def observe(peer, interval):
    catalog = data_of(peer.invoke("node.catalog"))["nodes"]
    registered = next((item for item in catalog if item["id"] == peer.node_id), None)
    if registered is None:
        raise Failure("acceptance_node_not_registered")
    capabilities = registered["capabilities"]
    result = {"capabilities": capabilities, "snapshot_first": snapshot_summary(peer.invoke("node.snapshot"))}
    time.sleep(min(interval, peer.deadline.remaining))
    result["snapshot_second"] = snapshot_summary(peer.invoke("node.snapshot"))
    if "docker.inventory" in capabilities:
        containers = inventory(peer, "docker.inventory", "containers")
        result["docker"] = metadata_summary(containers)
        running = [item["id"] for item in containers if item["availability"] == "ok" and item["running"]]
        if running and "docker.stats" in capabilities:
            first = metric_samples(peer, running)
            time.sleep(min(interval, peer.deadline.remaining))
            second = metric_samples(peer, running)
            result["stats_first"], result["stats_second"] = metric_summary(first), metric_summary(second)
            result["counter_differences"] = metric_differences(first, second)
    if "compose.inventory" in capabilities:
        stacks = inventory(peer, "compose.inventory", "stacks")
        result["compose"] = {"count": len(stacks), "state_counts": counts(item["state"] for item in stacks),
                             "drift_counts": counts(item["drift_status"] for item in stacks),
                             "stacks": [{"ref": reference(item["id"]), "state": item["state"], "drift_status": item["drift_status"],
                                         "reason_codes": item["reason_codes"], "service_count": len(item["services"]),
                                         "finding_counts": counts(code for service in item["services"] for code in service["findings"]),
                                         "runtime_only_service_count": len(item["runtime_only_services"])} for item in stacks]}
    return result


def limited_log(peer, identity):
    if not CONTAINER_ID.fullmatch(identity):
        raise Failure("acceptance_log_identity_invalid")
    response = peer.invoke("docker.logs", {"container_id": identity, "tail": 200, "since_seconds": 3600, "max_bytes": 65536})
    data = data_of(response)
    raw = data["stdout"].encode() + data["stderr"].encode()
    lines = len(data["stdout"].splitlines()) + len(data["stderr"].splitlines())
    if data["container_id"] != identity or len(raw) != data["returned_bytes"] or len(raw) > 65536 or lines > 200 or data["tail"] > 200 or data["since_seconds"] > 3600 or data["max_bytes"] > 65536:
        raise Failure("acceptance_log_bounds_invalid")
    return {"target_ref": reference(identity), "status": response["status"], "reason_codes": response["reason_codes"],
            "returned_bytes": len(raw), "line_count": lines,
            "truncated": data["truncated"], "tail": data["tail"], "since_seconds": data["since_seconds"],
            "body_sha256": hashlib.sha256(raw).hexdigest(), "body_recorded": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--node-id", required=True)
    parser.add_argument("--contract-dir", required=True)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--launcher", type=Path, help="Actual unpacked run_node.py for package/image verification")
    parser.add_argument("--mode", choices=("observe", "denied", "logs"), default="observe")
    parser.add_argument("--log-container-id")
    parser.add_argument("--denied-operation", choices=("node.snapshot", "docker.inventory"), default="docker.inventory")
    parser.add_argument("--before", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--seconds", type=int, default=300)
    parser.add_argument("--interval", type=float, default=1.0)
    args = parser.parse_args()
    receipt = {"format": "infrastructure-acceptance.v1", "mode": args.mode, "node_ref": reference(args.node_id),
               "verified_at": utc_now(), "remote_write_operations": 0, "log_body_recorded": False}
    try:
        if not 10 <= args.seconds <= 600 or not 0.2 <= args.interval <= 10:
            raise Failure("acceptance_budget_invalid")
        peer = Peer(config=args.config, node_id=args.node_id, contract_directory=args.contract_dir, python=args.python, launcher=args.launcher, seconds=args.seconds)
        if args.mode == "logs":
            if not args.log_container_id:
                raise Failure("acceptance_log_identity_required")
            receipt["logs"] = limited_log(peer, args.log_container_id)
        elif args.mode == "denied":
            response = peer.invoke(args.denied_operation)
            expected = {"permission_denied", "ssh_auth_failed", "ssh_identity_unavailable", "ssh_host_key_failed"}
            receipt["denied"] = {"operation": args.denied_operation, "status": response["status"], "reason_codes": response["reason_codes"]}
            if response["status"] not in ("unavailable", "unsupported") or not expected.intersection(response["reason_codes"]):
                raise Failure("acceptance_expected_denial_not_observed")
        else:
            receipt.update(observe(peer, args.interval))
        if args.before:
            before = decode_json(args.before.read_bytes())
            if not isinstance(before, dict) or before.get("format") != receipt["format"] or before.get("node_ref") != receipt["node_ref"]:
                raise Failure("acceptance_prior_receipt_invalid")
            receipt["comparison"] = compare_receipts(before, receipt)
        receipt["verification"] = "passed"
    except (Failure, OSError, ValueError, ImportError):
        error = sys.exc_info()[1]
        receipt["verification"] = "failed"
        receipt["failure_code"] = error.code if isinstance(error, Failure) else "acceptance_local_input_or_dependency_unavailable"
    receipt["calls"] = peer.calls if "peer" in locals() else []
    raw = json.dumps(receipt, ensure_ascii=True, indent=2) + "\n"
    if args.output:
        args.output.write_text(raw, encoding="utf-8")
    sys.stdout.write(raw)
    return 0 if receipt["verification"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
