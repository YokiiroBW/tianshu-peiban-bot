"""Isolated NAS-A2 recovery diagnosis and Vector disk-buffer acceptance run."""

from __future__ import annotations

import hashlib
import gzip
import ipaddress
import json
import math
import os
import re
import secrets
import shutil
import subprocess
import tarfile
import time
import urllib.request
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

import nas_a2_probe as base

BASE_SCOPE = Path("/volume2/tianshu-v2-validation-wave1/accept-20260925-a2")
RUN_ROOT = BASE_SCOPE / "runs" / "recovery-vector-20260925-r12"
PROJECT = "tianshu-accept-a2-vfull-20260925-r12"
NETWORK = PROJECT
SUBNET = ipaddress.ip_network("10.205.21.0/24")
VECTOR_NAME = PROJECT + "-vector"
LOKI_NAME = PROJECT + "-loki"
VECTOR_IP = "10.205.21.11"
LOKI_IP = "10.205.21.10"
GUARD_PORT = 19525
VECTOR_METRICS_PORT = 9598
TMPFS_BYTES = 512 * 1024**2
VECTOR_BUFFER_BYTES = base.VECTOR_MINIMUM_BUFFER_BYTES
VECTOR_DATA_FILE_BYTES = 128 * 1024**2
VECTOR_INTERNAL_BUFFER_BYTES = VECTOR_BUFFER_BYTES - VECTOR_DATA_FILE_BYTES
VECTOR_BUFFER_FULL_TIMEOUT_SECONDS = 300
VECTOR_REPLAY_DRAIN_TIMEOUT_SECONDS = 600
STORE_ONLY_QUERY_TIMEOUT_SECONDS = 300
TOTAL_RUN_TIMEOUT_SECONDS = 1500
WORK_TIMEOUT_SECONDS = 1200
VECTOR_REQUEST_RATE_LIMIT_PER_SECOND = 4
VECTOR_PADDING_BYTES = 2500
VECTOR_RECORDS = 60_000
VECTOR_TIMESTAMP_STEP_NS = 2_500_000
VECTOR_SERVICE = "nas-a2-vector-r12-" + uuid.uuid4().hex[:12]
RECOVERY_SERVICE = "nas-a2-recovery-r12-" + uuid.uuid4().hex[:12]
VECTOR_IMAGE_ID = base.EXPECTED_VECTOR_IMAGE_ID
LOKI_IMAGE_ID = base.EXPECTED_LOKI_IMAGE_ID
REPORT_NAME = "nas-a2-followup-report.json"
R4_SNAPSHOT_SHA256 = "03ce158cb5e3139ce46610418c6c846ece2c4751d02d67a3e33c5c1b441604dc"
R5_SNAPSHOT_SHA256 = "63eab69566cb7f291d32da5b7fdce2778c452c0a84a9226695ee2437f14c7042"
R6_SNAPSHOT_SHA256 = "f01556c65d445e82dc6de938c18d11bb1a0366fa244187f17de49db11f0174fd"
R7_SNAPSHOT_SHA256 = "8ecf8838fea69cb0a3030c72ade3c70b127c59d21f276eb60c2c2aa3c718bcdf"
R8_SNAPSHOT_SHA256 = "b0181f5d500e93f0f2c72ce1a8268b020515273095003b455ebb8d5cb5d48b2b"
R9_SNAPSHOT_SHA256 = "0d9687eab99c1eb7f4712f150a29cef46630f61080f3e8259a55fce740901bca"
R10_SNAPSHOT_SHA256 = "bbd5ec351d2a983f447e2c96f50a1c4c9fb7d412289f7690d8b15b52c8fd057d"
R11_SNAPSHOT_SHA256 = "d0d7c9601bd232c6e338c09d4122075a42f535c55ef265b4b268dfd203704b83"


def _configure_scope() -> None:
    base.require(
        SUBNET.subnet_of(ipaddress.ip_network("10.205.16.0/20")),
        "subnet_outside_assigned_range",
    )
    base.EXPECTED_ROOT = RUN_ROOT
    base.PROJECT = PROJECT
    base.NETWORK = NETWORK
    base.SUBNET = SUBNET
    base.VECTOR_NAME = VECTOR_NAME
    base.LOKI_NAME = LOKI_NAME
    base.VECTOR_IP = VECTOR_IP
    base.LOKI_IP = LOKI_IP
    base.GUARD_HOST_PORT = GUARD_PORT
    base.PORTS = (GUARD_PORT,)
    base.TMPFS_LIMIT = TMPFS_BYTES
    base.VECTOR_BUFFER_LIMIT = VECTOR_BUFFER_BYTES
    base.MEMORY_PLAN = sum(base.MEMORY_LIMITS.values()) + 256 * 1024**2 + TMPFS_BYTES


def _resource_snapshot() -> dict:
    volume = shutil.disk_usage(BASE_SCOPE)
    memory = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith(("MemTotal:", "MemAvailable:", "SwapTotal:", "SwapFree:")):
            key, value, *_ = line.split()
            memory[key.rstrip(":")] = int(value) * 1024

    factors = {
        "B": 1,
        "kB": 1000,
        "MB": 1000**2,
        "GB": 1000**3,
        "TB": 1000**4,
        "KiB": 1024,
        "MiB": 1024**2,
        "GiB": 1024**3,
        "TiB": 1024**4,
    }

    def bytes_from(value: str) -> int | None:
        match = re.fullmatch(
            r"\s*([0-9.]+)\s*(B|kB|MB|GB|TB|KiB|MiB|GiB|TiB)\s*", value
        )
        if not match:
            return None
        return int(float(match.group(1)) * factors[match.group(2)])

    output = base.docker("stats", "--no-stream", "--format", "{{.Name}}|{{.MemUsage}}")
    rows = []
    for line in output.splitlines():
        if "|" not in line:
            continue
        name, usage = line.split("|", 1)
        used = bytes_from(usage.split("/", 1)[0])
        if used is not None:
            rows.append((name.strip(), used))
    a2 = [row for row in rows if row[0].startswith("tianshu-accept-a2-")]
    other = [row for row in rows if row not in a2]
    return {
        "host_bytes": memory,
        "task_volume_bytes": {
            "total": volume.total,
            "used": volume.used,
            "free": volume.free,
        },
        "loadavg": Path("/proc/loadavg").read_text().strip(),
        "running_container_count": len(rows),
        "other_running_container_count": len(other),
        "other_current_mem_bytes": sum(row[1] for row in other),
        "largest_other_container_current_mem_bytes": max(
            (row[1] for row in other), default=0
        ),
        "a2_running_container_count": len(a2),
        "a2_current_mem_bytes": sum(row[1] for row in a2),
    }


def _tmpfs_mount_usage(path: Path) -> dict:
    mountpoint = str(path.resolve())
    for line in Path("/proc/mounts").read_text().splitlines():
        fields = line.split()
        if len(fields) < 3 or fields[1].replace("\\040", " ") != mountpoint:
            continue
        filesystem = fields[2]
        if filesystem != "tmpfs":
            raise base.ProbeError("historical_tmpfs_mount_type_unrecognized")
        usage = shutil.disk_usage(path)
        return {
            "mountpoint": mountpoint,
            "mounted": True,
            "filesystem": filesystem,
            "used_bytes": usage.used,
            "total_bytes": usage.total,
        }
    return {
        "mountpoint": mountpoint,
        "mounted": False,
        "filesystem": None,
        "used_bytes": 0,
        "total_bytes": 0,
    }


def _concurrent_a2_resource_budget(
    new_run_plan_bytes: int, current_a2_resident_bytes: int, historical_tmpfs: dict
) -> dict:
    historical_used_bytes = sum(
        usage["used_bytes"] for usage in historical_tmpfs.values()
    )
    total = new_run_plan_bytes + current_a2_resident_bytes + historical_used_bytes
    budget = 4 * 1024**3
    return {
        "historical_mounted_tmpfs_used_bytes": historical_used_bytes,
        "currently_running_a2_resident_bytes": current_a2_resident_bytes,
        "new_run_plan_bytes": new_run_plan_bytes,
        "total_concurrent_resources_bytes": total,
        "task_budget_bytes": budget,
        "within_budget": total <= budget,
    }


def _write_loki_config(path: Path, *, query_store_only: bool) -> bytes:
    config = base.loki_config(retention_hours=48)
    config["server"].update(http_listen_address="0.0.0.0", http_listen_port=3100)
    config["ingester"].update(chunk_idle_period="5s", max_chunk_age="1m")
    config["compactor"].update(
        compaction_interval="5s",
        retention_delete_delay="1s",
        apply_retention_interval="5s",
        retention_delete_worker_count=1,
    )
    config["limits_config"].update(
        retention_period="48h", reject_old_samples_max_age="720h"
    )
    config["querier"] = {
        "query_store_only": query_store_only,
        "query_ingesters_within": "3h",
    }
    config["storage_config"]["tsdb_shipper"]["resync_interval"] = "5s"
    config["chunk_store_config"] = {
        "chunk_cache_config": {"embedded_cache": {"enabled": False}}
    }
    config["query_range"] = {"cache_results": False}
    data = (json.dumps(config, sort_keys=True, indent=2) + "\n").encode()
    path.write_bytes(data)
    os.chmod(path, 0o644)
    return data


def _loki_args(paths: dict[str, Path], cpu: str) -> list[str]:
    args = base._container_common(
        LOKI_NAME, base.MEMORY_LIMITS["loki"], cpu, paths["loki_data"]
    )
    args.extend(
        [
            "--network",
            NETWORK,
            "--network-alias",
            "obs-loki",
            "--ip",
            LOKI_IP,
            "--mount",
            f"type=bind,source={paths['config'] / 'loki.json'},target=/etc/tianshu/loki.json,readonly",
        ]
    )
    for filename in ("loki.pem", "loki.key", "client-ca.pem"):
        args.extend(
            [
                "--mount",
                f"type=bind,source={paths['tls'] / filename},target=/run/tls/{filename},readonly",
            ]
        )
    args.extend(
        [
            "--tmpfs",
            "/loki:rw,noexec,nosuid,size=1m,uid=10001,gid=10001,mode=0700",
            LOKI_IMAGE_ID,
            "-config.file=/etc/tianshu/loki.json",
        ]
    )
    return args


def _vector_args(paths: dict[str, Path], cpu: str) -> list[str]:
    args = base._container_common(
        VECTOR_NAME, base.MEMORY_LIMITS["vector"], cpu, paths["vector_data"]
    )
    args.extend(
        [
            "--network",
            NETWORK,
            "--ip",
            VECTOR_IP,
            "--mount",
            f"type=bind,source={paths['config'] / 'vector.json'},target=/etc/tianshu/vector.json,readonly",
            "--mount",
            f"type=bind,source={paths['vector_source']},target=/sources,readonly",
        ]
    )
    for filename in ("ca.pem", "client.pem", "client.key"):
        args.extend(
            [
                "--mount",
                f"type=bind,source={paths['tls'] / filename},target=/run/tls/{filename},readonly",
            ]
        )
    args.extend([VECTOR_IMAGE_ID, "--config", "/etc/tianshu/vector.json"])
    return args


def _checkpoint(path: Path, report: dict) -> None:
    base.write_json(path, report)


def _query_once(client, selector: str, start_ns: int, end_ns: int) -> dict:
    params = urlencode(
        {
            "query": selector,
            "start": str(start_ns),
            "end": str(end_ns),
            "direction": "forward",
            "limit": "20",
        }
    )
    try:
        status, body, _ = client.request("/loki/api/v1/query_range?" + params)
    except Exception:
        return {"http_status": None, "transport_error": True, "result_count": 0}
    result = {"http_status": status, "transport_error": False, "result_count": 0}
    if status != 200:
        return result
    try:
        payload = json.loads(body)
        if payload.get("status") != "success":
            return {**result, "response_error": "loki_status_not_success"}
        streams = payload["data"]["result"]
        entries = [
            (int(timestamp), line)
            for stream in streams
            for timestamp, line in stream["values"]
        ]
    except (ValueError, KeyError, TypeError):
        return {**result, "response_error": "invalid_query_response"}
    return {
        **result,
        "result_count": len(entries),
        "entries": entries,
    }


def _query_evidence(
    client, selector: str, start_ns: int, end_ns: int, row: dict
) -> dict:
    response = _query_once(client, selector, start_ns, end_ns)
    entries = response.pop("entries", [])
    expected_line = base.canonical(row).decode()
    expected_id = row["event_id"]
    id_matches = [line for _, line in entries if _event_id(line) == expected_id]
    exact_matches = [line for line in id_matches if line == expected_line]
    semantic_matches = []
    for line in id_matches:
        try:
            semantic_matches.append(json.loads(line) == row)
        except (TypeError, ValueError):
            semantic_matches.append(False)
    report = {
        **response,
        "expected_event_id": expected_id,
        "expected_line_sha256": hashlib.sha256(expected_line.encode()).hexdigest(),
        "event_id_match_count": len(id_matches),
        "exact_line_match_count": len(exact_matches),
        "semantic_payload_match_count": sum(semantic_matches),
        "matched_lines": exact_matches,
    }
    return report


def _event_id(line: str) -> str | None:
    try:
        value = json.loads(line)
    except (TypeError, ValueError):
        return None
    event_id = value.get("event_id")
    return event_id if isinstance(event_id, str) else None


def _wait_for_event(
    client, selector: str, start_ns: int, end_ns: int, row: dict
) -> dict:
    observations = []
    deadline = time.monotonic() + 12
    latest = {}
    while time.monotonic() < deadline:
        latest = _query_evidence(client, selector, start_ns, end_ns, row)
        observations.append(
            {
                "elapsed_seconds": round(12 - max(0, deadline - time.monotonic()), 2),
                "http_status": latest.get("http_status"),
                "result_count": latest.get("result_count", 0),
                "event_id_match_count": latest.get("event_id_match_count", 0),
            }
        )
        if (
            latest.get("exact_line_match_count") == 1
            or latest.get("semantic_payload_match_count") == 1
        ):
            break
        time.sleep(1)
    return {"observations": observations, "final": latest}


def _series_evidence(client, selector: str, start_ns: int, end_ns: int) -> dict:
    service_match = re.search(r'(?:^|,)service="([^"]+)"', selector)
    expected_service = service_match.group(1) if service_match else None
    params = urlencode(
        {
            "match[]": selector,
            "start": str(start_ns),
            "end": str(end_ns),
        }
    )
    try:
        status, body, _ = client.request("/loki/api/v1/series?" + params)
        payload = json.loads(body) if status == 200 else {}
        series = payload.get("data", [])
        matching = [
            entry
            for entry in series
            if entry.get("stack") == "tianshu"
            and entry.get("service") == expected_service
        ]
        return {
            "http_status": status,
            "success": payload.get("status") == "success",
            "series_count": len(series),
            "contains_selector_series": bool(matching),
            "contains_unique_service": any(
                entry.get("service") == RECOVERY_SERVICE for entry in series
            ),
        }
    except Exception:
        return {"http_status": None, "transport_error": True}


def _label_evidence(client, selector: str, start_ns: int, end_ns: int) -> dict:
    result = {}
    for name, path in (
        ("labels", "/loki/api/v1/labels"),
        ("service_values", "/loki/api/v1/label/service/values"),
    ):
        try:
            status, body, _ = client.request(path)
            payload = json.loads(body) if status == 200 else {}
            data = payload.get("data", [])
            result[name] = {
                "http_status": status,
                "success": payload.get("status") == "success",
                "contains_service_label": "service" in data
                if name == "labels"
                else None,
                "contains_unique_service": RECOVERY_SERVICE in data
                if name == "service_values"
                else None,
            }
        except Exception:
            result[name] = {"http_status": None, "transport_error": True}
    result["series"] = _series_evidence(client, selector, start_ns, end_ns)
    return result


def _wait_for_series_routes(
    routes: dict[str, object],
    selector: str,
    start_ns: int,
    end_ns: int,
    *,
    timeout: int,
) -> dict:
    observations = []
    latest = {}
    deadline = time.monotonic() + timeout
    while True:
        latest = {
            name: _series_evidence(client, selector, start_ns, end_ns)
            for name, client in routes.items()
        }
        observations.append(
            {
                "elapsed_seconds": round(
                    timeout - max(0, deadline - time.monotonic()), 2
                ),
                "routes": latest,
            }
        )
        if all(
            item.get("contains_selector_series") is True for item in latest.values()
        ):
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(1, remaining))
    return {
        "ready": bool(latest)
        and all(
            item.get("contains_selector_series") is True for item in latest.values()
        ),
        "observations": observations,
        "final": latest,
    }


def _exact_match_count(evidence: dict) -> int:
    if isinstance(evidence.get("final"), dict):
        evidence = evidence["final"]
    return evidence.get("exact_line_match_count", 0)


def _start_loki(paths: dict[str, Path], report: dict, cpu: str) -> str:
    config = _write_loki_config(paths["config"] / "loki.json", query_store_only=False)
    report["config_sha256"] = {"loki_ingester_path": base.sha(config)}
    container_id = base.docker(*_loki_args(paths, cpu)).strip()
    report["containers"]["loki"] = {
        "id": container_id,
        "name": LOKI_NAME,
        "image_id": LOKI_IMAGE_ID,
        "reused": False,
    }
    base._check_owned(base._container_by_id(container_id), LOKI_NAME, LOKI_IMAGE_ID)
    return container_id


def _switch_loki_mode(
    paths: dict[str, Path], report: dict, container_id: str, query_store_only: bool
) -> None:
    transition = base._stop_owned(container_id, LOKI_NAME, LOKI_IMAGE_ID)
    report["containers"]["loki"].setdefault("transitions", []).append(transition)
    config = _write_loki_config(
        paths["config"] / "loki.json", query_store_only=query_store_only
    )
    mode = "store_only" if query_store_only else "ingester_enabled"
    report["config_sha256"]["loki_" + mode] = base.sha(config)
    base._start_existing(container_id, LOKI_NAME, LOKI_IMAGE_ID)


def _run_recovery_diagnostic(
    root: Path,
    paths: dict[str, Path],
    report: dict,
    clients: dict,
    loki_id: str,
    cpu: str,
    checkpoint_path: Path,
) -> None:
    row = base._record(990001, service="gateway")
    row["event_id"] = str(uuid.uuid4())
    timestamp_ns = time.time_ns()
    row["timestamp"] = (
        datetime.fromtimestamp(timestamp_ns / 1e9, timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )
    selector = f'{{stack="tianshu",service="{RECOVERY_SERVICE}"}}'
    expected_line = base.canonical(row).decode()
    payload = {
        "streams": [
            {
                "stream": {"stack": "tianshu", "service": RECOVERY_SERVICE},
                "values": [[str(timestamp_ns), expected_line]],
            }
        ]
    }
    writer = base._guard_client(paths, "writer")
    reader = base._guard_client(paths, "query")
    clients["guard_writer"] = writer
    clients["guard_query"] = reader
    start_ns, end_ns = timestamp_ns - 5 * 10**9, timestamp_ns + 5 * 10**9
    recovery = {
        "status": "running",
        "service_label": RECOVERY_SERVICE,
        "expected_event_id": row["event_id"],
        "expected_line": expected_line,
        "expected_line_sha256": hashlib.sha256(expected_line.encode()).hexdigest(),
        "query_bounds_ns": {"start": start_ns, "end": end_ns},
        "configured_query_ingesters_within": "3h",
        "phases": {},
    }
    report["dimensions"]["recovery_query_diagnostics"] = recovery
    status, body, _ = writer.request(
        "/loki/api/v1/push", "POST", json.dumps(payload).encode(), "application/json"
    )
    recovery["guard_push_status"] = status
    recovery["guard_push_error"] = None
    if status != 204:
        try:
            recovery["guard_push_error"] = json.loads(body).get("error")
        except (TypeError, ValueError):
            recovery["guard_push_error"] = "unparseable_error_body"
        recovery["status"] = "failed"
        _checkpoint(checkpoint_path, report)
        return

    for phase, direct, guarded in (
        ("ingester_before_flush", clients["loki"], reader),
        ("ingester_after_flush", clients["loki"], reader),
    ):
        if phase == "ingester_after_flush":
            chunk_root = paths["loki_data"] / "chunks" / "tianshu"
            before = (
                {
                    path.relative_to(chunk_root).as_posix()
                    for path in chunk_root.rglob("*")
                    if path.is_file()
                }
                if chunk_root.exists()
                else set()
            )
            flush_status, _, _ = clients["loki"].request("/flush", "POST", b"")
            recovery["flush_status"] = flush_status
            base._wait(
                lambda: (
                    {
                        path.relative_to(chunk_root).as_posix()
                        for path in chunk_root.rglob("*")
                        if path.is_file()
                    }
                    - before
                ),
                timeout=30,
                interval=1,
                code="recovery_fixture_chunk_not_flushed",
            )
            recovery["flushed_chunk_paths"] = sorted(
                {
                    path.relative_to(chunk_root).as_posix()
                    for path in chunk_root.rglob("*")
                    if path.is_file()
                }
            )
        recovery["phases"][phase] = {
            "direct_loki": _wait_for_event(direct, selector, start_ns, end_ns, row),
            "guard_query_route": _wait_for_event(
                guarded, selector, start_ns, end_ns, row
            ),
        }
        _checkpoint(checkpoint_path, report)

    _switch_loki_mode(paths, report, loki_id, query_store_only=True)
    base._wait(
        lambda: base._loki_ready(clients["loki"]),
        timeout=90,
        code="loki_store_only_restart_timeout",
    )
    store_phase = {
        "direct_loki": _query_evidence(
            clients["loki"], selector, start_ns, end_ns, row
        ),
        "guard_query_route": _query_evidence(reader, selector, start_ns, end_ns, row),
        "index_and_labels": _label_evidence(
            clients["loki"], selector, start_ns, end_ns
        ),
    }
    store_phase["index_readiness"] = _wait_for_series_routes(
        {"direct_loki": clients["loki"], "guard_query_route": reader},
        selector,
        start_ns,
        end_ns,
        timeout=45,
    )
    store_phase["direct_loki_after_index_ready"] = _wait_for_event(
        clients["loki"], selector, start_ns, end_ns, row
    )
    store_phase["guard_after_index_ready"] = _wait_for_event(
        reader, selector, start_ns, end_ns, row
    )
    recovery["phases"]["store_only_after_restart"] = store_phase
    _checkpoint(checkpoint_path, report)

    _switch_loki_mode(paths, report, loki_id, query_store_only=False)
    base._wait(
        lambda: base._loki_ready(clients["loki"]),
        timeout=90,
        code="loki_ingester_restart_timeout",
    )
    recovery["phases"]["ingester_after_restart"] = {
        "direct_loki": _query_evidence(
            clients["loki"], selector, start_ns, end_ns, row
        ),
        "guard_query_route": _query_evidence(reader, selector, start_ns, end_ns, row),
    }
    route_names = ("direct_loki", "guard_query_route")
    ingester_after_flush = recovery["phases"]["ingester_after_flush"]
    store_after_index = {
        "direct_loki": store_phase["direct_loki_after_index_ready"],
        "guard_query_route": store_phase["guard_after_index_ready"],
    }
    route_summary = {
        route: {
            "ingester_before_flush": _exact_match_count(
                recovery["phases"]["ingester_before_flush"][route]
            ),
            "ingester_after_flush": _exact_match_count(ingester_after_flush[route]),
            "store_only_after_index_ready": _exact_match_count(
                store_after_index[route]
            ),
            "ingester_after_restart": _exact_match_count(
                recovery["phases"]["ingester_after_restart"][route]
            ),
        }
        for route in route_names
    }
    found_before = any(
        route_summary[route][phase] == 1
        for route in route_names
        for phase in ("ingester_before_flush", "ingester_after_flush")
    )
    store_route_found = {
        route: route_summary[route]["store_only_after_index_ready"] == 1
        for route in route_names
    }
    store_phase_found = any(store_route_found.values())
    restart_ingester_found = any(
        route_summary[route]["ingester_after_restart"] == 1 for route in route_names
    )
    route_disagreement = any(
        (route_summary["direct_loki"][phase] == 1)
        != (route_summary["guard_query_route"][phase] == 1)
        for phase in (
            "ingester_after_flush",
            "store_only_after_index_ready",
            "ingester_after_restart",
        )
    )
    recovery["route_exact_match_summary"] = route_summary
    recovery["direct_vs_guard_disagreement"] = route_disagreement
    if route_disagreement:
        diagnosis = "guard_and_direct_query_route_disagree"
        status = "partial"
    elif all(store_route_found.values()) and all(
        route_summary[route]["ingester_after_flush"] == 1 for route in route_names
    ):
        diagnosis = "store_and_ingester_paths_return_exact_event"
        status = "passed"
    elif found_before and restart_ingester_found and not store_phase_found:
        diagnosis = "store_only_query_missing_while_ingester_wal_returns_event"
        status = "partial"
    elif found_before and not store_phase_found and not restart_ingester_found:
        diagnosis = "event_seen_before_restart_but_missing_after_restart"
        status = "failed"
    elif not found_before and (store_phase_found or restart_ingester_found):
        diagnosis = "query_probe_or_index_refresh_timing_issue"
        status = "partial"
    else:
        diagnosis = "accepted_push_not_returned_by_direct_or_guard_queries"
        status = "failed"
    recovery["diagnosis"] = diagnosis
    recovery["status"] = status
    recovery["store_only_exact_event_found"] = store_phase_found
    recovery["store_only_exact_event_found_by_route"] = store_route_found
    recovery["ingester_exact_event_found_before_restart"] = found_before
    recovery["ingester_exact_event_found_after_restart"] = restart_ingester_found
    _checkpoint(checkpoint_path, report)


def _vector_config(path: Path, tls: Path) -> bytes:
    data = base._write_vector_config(path, tls)
    config = json.loads(data)
    config["log_schema"] = {"timestamp_key": "timestamp"}
    config["transforms"]["a2_json"]["source"] = (
        ". = parse_json!(.message)\n"
        '.timestamp = parse_timestamp!(string!(.timestamp), format: "%+")\n'
        ".a2_buffer_fixture = " + json.dumps("x" * VECTOR_PADDING_BYTES)
    )
    config["sinks"]["loki"]["labels"]["service"] = VECTOR_SERVICE
    config["sinks"]["loki"]["remove_timestamp"] = False
    config["sinks"]["loki"]["buffer"]["max_size"] = VECTOR_BUFFER_BYTES
    config["sinks"]["loki"]["request"].update(
        concurrency="none",
        rate_limit_duration_secs=1,
        rate_limit_num=VECTOR_REQUEST_RATE_LIMIT_PER_SECOND,
        timeout_secs=60,
    )
    data = (json.dumps(config, sort_keys=True, indent=2) + "\n").encode()
    path.write_bytes(data)
    os.chmod(path, 0o644)
    return data


def _write_vector_backlog(source: Path, base_ns: int) -> tuple[list, str, dict]:
    expected = []
    padding = "x" * VECTOR_PADDING_BYTES
    max_loki_line_bytes = 0
    logical_loki_json_bytes = 0

    def rows():
        nonlocal max_loki_line_bytes, logical_loki_json_bytes
        for index in range(VECTOR_RECORDS):
            row = base._record(index + 2, service="gateway")
            row["event_id"] = str(uuid.uuid4())
            timestamp_ns = base_ns + index * VECTOR_TIMESTAMP_STEP_NS
            row["timestamp"] = (
                datetime.fromtimestamp(timestamp_ns / 1e9, timezone.utc)
                .isoformat()
                .replace("+00:00", "Z")
            )
            enriched = {**row, "a2_buffer_fixture": padding}
            encoded_line_bytes = len(base.canonical(enriched))
            base.require(encoded_line_bytes < 4096, "synthetic_loki_line_too_long")
            max_loki_line_bytes = max(max_loki_line_bytes, encoded_line_bytes)
            logical_loki_json_bytes += encoded_line_bytes + 1
            expected.append((row["event_id"], base.digest(enriched)))
            yield row

    base._write_synthetic(source, rows(), append=True)
    os.chown(source, 10001, 10001)
    base.require(
        len({event_id for event_id, _ in expected}) == VECTOR_RECORDS,
        "synthetic_event_id_collision",
    )
    return expected, base.sha_file(source), {
        "max_projected_loki_line_bytes": max_loki_line_bytes,
        "logical_loki_json_bytes": logical_loki_json_bytes,
        "source_file_bytes": source.stat().st_size,
    }


def _vector_prometheus_body() -> bytes:
    request = urllib.request.Request(
        f"http://{VECTOR_IP}:{VECTOR_METRICS_PORT}/metrics"
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            body = response.read(8 * 1024**2 + 1)
    except Exception:
        raise base.ProbeError("vector_metrics_unavailable") from None
    base.require(len(body) <= 8 * 1024**2, "vector_metrics_over_budget")
    return body


def _vector_prometheus_samples(
    body: bytes | None = None,
) -> dict[str, list[tuple[dict[str, str], float]]]:
    if body is None:
        body = _vector_prometheus_body()
    samples: dict[str, list[tuple[dict[str, str], float]]] = {}
    for line in body.decode("utf-8", "replace").splitlines():
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(
            r"([a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{([^}]*)\})?\s+([-+0-9.eE]+)(?:\s+\d+)?",
            line,
        )
        if not match:
            continue
        labels = {}
        if match.group(2):
            labels = {
                key: value
                for key, value in re.findall(
                    r'([a-zA-Z_][a-zA-Z0-9_]*)="((?:\\.|[^"])*)"',
                    match.group(2),
                )
            }
        try:
            value = float(match.group(3))
        except ValueError:
            continue
        samples.setdefault(match.group(1), []).append((labels, value))
    return samples


def _vector_metrics_snapshot(body: bytes | None = None) -> dict:
    samples = _vector_prometheus_samples(body)
    required_components = (
        ("vector_buffer_size_bytes", "loki"),
        ("vector_buffer_max_size_bytes", "loki"),
        ("vector_component_sent_events_total", "a2_file"),
    )
    for metric, component in required_components:
        if not any(
            labels.get("component_id") == component
            for labels, _ in samples.get(metric, [])
        ):
            raise base.ProbeError("vector_required_metric_missing")
    return {
        "buffer_bytes": base._metric_sum(samples, "vector_buffer_size_bytes", "loki"),
        "buffer_max_bytes": base._metric_sum(
            samples, "vector_buffer_max_size_bytes", "loki"
        ),
        "source_sent_events": base._metric_sum(
            samples, "vector_component_sent_events_total", "a2_file"
        ),
        "sink_sent_events": base._metric_sum(
            samples, "vector_component_sent_events_total", "loki"
        ),
        "discarded_events": base._metric_sum(
            samples, "vector_component_discarded_events_total"
        ),
        "component_errors": base._metric_sum(samples, "vector_component_errors_total"),
        "component_error_series": [
            {"labels": labels, "value": value}
            for labels, value in samples.get("vector_component_errors_total", [])
        ],
    }


def _try_vector_metrics_snapshot() -> dict | None:
    try:
        return _vector_metrics_snapshot()
    except base.ProbeError:
        return None


def _stamp() -> dict:
    return {
        "utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "monotonic_ns": time.monotonic_ns(),
    }


def _timestamped_vector_sample(
    paths: dict[str, Path], source: Path, vector_id: str, loki_id: str
) -> dict:
    scrape_started_ns = time.monotonic_ns()
    body = _vector_prometheus_body()
    observed = _stamp()
    metrics = _vector_metrics_snapshot(body)
    selected_names = (
        "vector_buffer_size_bytes",
        "vector_buffer_max_size_bytes",
        "vector_component_sent_events_total",
        "vector_component_discarded_events_total",
        "vector_component_errors_total",
    )
    raw_lines = [
        line
        for line in body.decode("utf-8", "replace").splitlines()
        if line.startswith(selected_names)
    ]
    buffer_root = paths["vector_data"] / "buffer" / "v2" / "loki"
    data_files = [
        {"name": path.name, "bytes": path.stat().st_size}
        for path in sorted(buffer_root.glob("buffer-data-*.dat"))
        if path.is_file() and not path.is_symlink()
    ]
    return {
        "scrape_started_monotonic_ns": scrape_started_ns,
        "observed_at": observed,
        "raw_prometheus_selected_lines": raw_lines,
        "raw_prometheus_sha256": base.sha(body),
        "raw_prometheus_bytes": len(body),
        "metrics": metrics,
        "vector_running": base._container_by_id(vector_id)["State"]["Running"],
        "sink_running": base._container_by_id(loki_id)["State"]["Running"],
        "source_file_bytes": source.stat().st_size,
        "source_sha256": base.sha_file(source),
        "buffer_data_files": data_files,
    }


def _empirical_encoded_event_bytes(samples: list[dict]) -> int | None:
    estimates = []
    for previous, current in zip(samples, samples[1:]):
        source_delta = (
            current["metrics"]["source_sent_events"]
            - previous["metrics"]["source_sent_events"]
        )
        buffer_delta = (
            current["metrics"]["buffer_bytes"]
            - previous["metrics"]["buffer_bytes"]
        )
        if source_delta > 0 and buffer_delta > 0:
            estimates.append(math.ceil(buffer_delta / source_delta))
    return max(estimates, default=None)


def _backpressure_evidence(
    samples: list[dict], source_sent_total: int, encoded_event_bytes: int | None,
    batch_max_bytes: int = 262144,
) -> dict:
    tail = samples[-3:]
    allowance = (
        batch_max_bytes + encoded_event_bytes
        if encoded_event_bytes is not None
        else None
    )
    spacing_ok = len(tail) == 3 and all(
        current["observed_at"]["monotonic_ns"]
        - previous["observed_at"]["monotonic_ns"] >= 5_000_000_000
        for previous, current in zip(tail, tail[1:])
    )
    source_counts = [sample["metrics"]["source_sent_events"] for sample in tail]
    buffer_sizes = [sample["metrics"]["buffer_bytes"] for sample in tail]
    gaps = [VECTOR_INTERNAL_BUFFER_BYTES - value for value in buffer_sizes]
    source_stalled_before_eof = (
        len(tail) == 3
        and len(set(source_counts)) == 1
        and source_counts[0] < source_sent_total
    )
    queue_near_internal_limit = (
        len(tail) == 3
        and allowance is not None
        and all(0 <= gap <= allowance for gap in gaps)
        and max(buffer_sizes) - min(buffer_sizes) <= allowance
    )
    source_unchanged = len(tail) == 3 and len(
        {sample["source_sha256"] for sample in tail}
    ) == 1 and len({sample["source_file_bytes"] for sample in tail}) == 1
    sink_offline = len(tail) == 3 and all(not item["sink_running"] for item in tail)
    vector_running = len(tail) == 3 and all(item["vector_running"] for item in tail)
    no_discards = len(tail) == 3 and all(
        item["metrics"]["discarded_events"] == 0 for item in tail
    )
    no_unexpected_metric_errors = len(tail) == 3 and all(
        all(
            series["labels"].get("component_id") == "loki"
            and series["labels"].get("error_type") == "request_failed"
            for series in item["metrics"]["component_error_series"]
            if series["value"] > 0
        )
        for item in tail
    )
    checks = {
        "three_spaced_samples": spacing_ok,
        "source_stalled_before_eof": source_stalled_before_eof,
        "sink_offline": sink_offline,
        "vector_running": vector_running,
        "queue_near_internal_limit": queue_near_internal_limit,
        "source_unchanged": source_unchanged,
        "no_discards": no_discards,
        "no_unexpected_metric_errors": no_unexpected_metric_errors,
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "configured_buffer_bytes": VECTOR_BUFFER_BYTES,
        "internal_buffer_limit_bytes": VECTOR_INTERNAL_BUFFER_BYTES,
        "data_file_reservation_bytes": VECTOR_DATA_FILE_BYTES,
        "empirical_metric_bytes_per_source_event": encoded_event_bytes,
        "batch_max_bytes": batch_max_bytes,
        "explainable_headroom_bytes": allowance,
        "observed_headroom_bytes": gaps,
        "sample_monotonic_ns": [
            sample["observed_at"]["monotonic_ns"] for sample in tail
        ],
    }


def _vector_logs(container_id: str) -> str:
    env = os.environ.copy()
    for key in ("DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH"):
        env.pop(key, None)
    process = subprocess.run(
        [base.DOCKER, "logs", "--timestamps", container_id],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=30,
        check=False,
        env=env,
    )
    base.require(process.returncode == 0, "vector_logs_unavailable")
    return process.stdout + process.stderr


def _classify_vector_error_logs(
    logs: str, *, sink_offline: bool, allow_recovery_transport_retry: bool = False
) -> dict:
    expected_transport = []
    unexpected = []
    for line in logs.splitlines():
        if "Internal log [" in line and ("suppressed" in line or "suppression" in line):
            continue
        if "error=" not in line and " ERROR " not in line:
            continue
        transport = any(
            phrase in line.lower()
            for phrase in (
                "failed to lookup address information",
                "temporary failure in name resolution",
                "connection refused",
                "error trying to connect",
            )
        )
        response_error = any(
            phrase in line.lower()
            for phrase in (
                "server responded with an error",
                "http status",
                "status code",
            )
        )
        if (
            (sink_offline or allow_recovery_transport_retry)
            and transport
            and not response_error
            and "component_id=loki" in line
        ):
            expected_transport.append(line)
        else:
            unexpected.append(line)
    return {
        "expected_transport_retry_count": len(expected_transport),
        "unexpected_error_count": len(unexpected),
        "expected_transport_retry_samples": expected_transport[:10],
        "unexpected_error_samples": unexpected[:20],
    }


def _loki_file_inventory(paths: dict[str, Path], hash_cache: dict) -> dict:
    data = paths["loki_data"]
    roots = {
        "chunks": data / "chunks" / "tianshu",
        "object_index": data / "chunks" / "index",
        "active_index": data / "index",
        "index_cache": data / "index-cache",
    }
    inventory = {"observed_at": _stamp(), "categories": {}}
    for category, root in roots.items():
        files = []
        if root.exists():
            for path in sorted(root.rglob("*")):
                if path.is_symlink() or not path.is_file():
                    continue
                try:
                    stat = path.stat()
                    relative = path.relative_to(data).as_posix()
                    cache_key = (relative, stat.st_size, stat.st_mtime_ns)
                    digest = hash_cache.get(cache_key)
                    if digest is None:
                        digest = base.sha_file(path)
                    after = path.stat()
                    if (
                        after.st_size != stat.st_size
                        or after.st_mtime_ns != stat.st_mtime_ns
                    ):
                        files.append(
                            {
                                "path": relative,
                                "changed_during_snapshot": True,
                                "size_before": stat.st_size,
                                "size_after": after.st_size,
                                "mtime_ns_before": stat.st_mtime_ns,
                                "mtime_ns_after": after.st_mtime_ns,
                            }
                        )
                        continue
                    hash_cache[cache_key] = digest
                    files.append(
                        {
                            "path": relative,
                            "bytes": stat.st_size,
                            "mtime_ns": stat.st_mtime_ns,
                            "mtime_utc": datetime.fromtimestamp(
                                stat.st_mtime, timezone.utc
                            ).isoformat().replace("+00:00", "Z"),
                            "sha256": digest,
                        }
                    )
                except OSError:
                    files.append({"path": path.relative_to(data).as_posix(), "unreadable_or_changed": True})
        inventory["categories"][category] = files
    return inventory


def _loki_metrics_evidence(client) -> dict:
    observed = _stamp()
    try:
        status, body, _ = client.request("/metrics")
    except Exception:
        return {"observed_at": observed, "transport_error": True}
    lines = []
    for line in body.decode("utf-8", "replace").splitlines():
        name = line.split("{", 1)[0].split(" ", 1)[0].lower()
        if name.startswith("loki_") and any(
            part in name
            for part in ("flush", "shipper", "upload", "download", "chunk", "index")
        ):
            lines.append(line)
    return {
        "observed_at": observed,
        "http_status": status,
        "raw_metrics_sha256": base.sha(body),
        "raw_metrics_bytes": len(body),
        "selected_raw_metric_lines": lines[:2500],
        "selected_lines_truncated": len(lines) > 2500,
    }


def _archive_and_unmount_new_scope(paths: dict[str, Path], report: dict) -> None:
    scratch = paths["scratch"]
    base.require(scratch.resolve() == (RUN_ROOT / "tmpfs").resolve(), "tmpfs_scope_mismatch")
    archive = paths["evidence"] / "tmpfs-snapshot-after-probe.tar.gz"
    temporary = paths["evidence"] / "tmpfs-snapshot-after-probe.tar.gz.tmp"
    with tarfile.open(temporary, "w:gz") as bundle:
        bundle.add(scratch, arcname=".")
    os.replace(temporary, archive)
    with gzip.open(archive, "rb") as stream:
        while stream.read(1024 * 1024):
            pass
    source_digest = None
    with tarfile.open(archive, "r:gz") as bundle:
        members = bundle.getmembers()
        source_member = next(
            (item for item in members if item.name.lstrip("./") == "vector-source/events.jsonl"),
            None,
        )
        if source_member is not None:
            source_stream = bundle.extractfile(source_member)
            base.require(source_stream is not None, "archive_source_unreadable")
            digest = hashlib.sha256()
            with source_stream:
                while chunk := source_stream.read(1024 * 1024):
                    digest.update(chunk)
            source_digest = digest.hexdigest()
    source = paths["vector_source"] / "events.jsonl"
    expected_source_digest = base.sha_file(source) if source.is_file() else None
    base.require(source_digest == expected_source_digest, "archive_source_hash_mismatch")
    report["archive"] = {
        "path": str(archive),
        "bytes": archive.stat().st_size,
        "sha256": base.sha_file(archive),
        "member_count": len(members),
        "gzip_verified": True,
        "source_sha256": source_digest,
        "source_matches_live": True,
    }
    process = subprocess.run(
        ["umount", str(scratch)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=30,
        check=False,
    )
    base.require(process.returncode == 0, "new_scope_tmpfs_unmount_failed")
    report["archive"]["tmpfs_unmounted_after_verification"] = True


def _query_all_events(
    client,
    selector: str,
    start_ns: int,
    end_ns: int,
    expected: Counter,
    *,
    deadline_monotonic: float | None = None,
) -> dict:
    actual = Counter()
    result_lines = 0
    requests = 0
    errors = []
    max_actual_line_bytes = 0
    oversized_line_count = 0
    bucket_ns = 10**9
    low = (start_ns // bucket_ns) * bucket_ns
    while low <= end_ns:
        if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
            errors.append({"start_ns": low, "error": "query_deadline_exceeded"})
            break
        high = min(end_ns, low + bucket_ns - 1)
        try:
            rows = client.range(selector, low, high, limit=1000, max_requests=8)
        except Exception:
            errors.append({"start_ns": low, "end_ns": high, "error": "query_failed"})
            low = high + 1
            requests += 1
            continue
        requests += 1
        for _, line in rows:
            result_lines += 1
            line_bytes = len(line.encode("utf-8"))
            max_actual_line_bytes = max(max_actual_line_bytes, line_bytes)
            oversized_line_count += line_bytes >= 4096
            try:
                row = json.loads(line)
                actual[(row["event_id"], base.digest(row))] += 1
            except (ValueError, KeyError, TypeError):
                errors.append({"start_ns": low, "error": "invalid_log_line"})
        low = high + 1
    reconciliation = _reconcile_event_identities(actual, expected)
    return {
        "result_lines": result_lines,
        **reconciliation,
        "identity_hash_multiset_match": (
            low > end_ns and actual == expected and not errors and not oversized_line_count
        ),
        "request_count": requests,
        "query_errors": errors[:20],
        "query_complete": low > end_ns,
        "max_actual_loki_line_bytes": max_actual_line_bytes,
        "oversized_loki_line_count": oversized_line_count,
    }


def _reconcile_event_identities(actual: Counter, expected: Counter) -> dict:
    actual_ids = {event_id for event_id, _ in actual}
    expected_ids = {event_id for event_id, _ in expected}
    actual_by_id = {}
    expected_by_id = {}
    actual_count_by_id = Counter()
    expected_count_by_id = Counter()
    for (event_id, payload_hash), count in actual.items():
        actual_by_id.setdefault(event_id, set()).add(payload_hash)
        actual_count_by_id[event_id] += count
    for (event_id, payload_hash), count in expected.items():
        expected_by_id.setdefault(event_id, set()).add(payload_hash)
        expected_count_by_id[event_id] += count

    missing_ids = sorted(expected_ids - actual_ids)
    unexpected_ids = sorted(actual_ids - expected_ids)
    payload_mismatch_ids = sorted(
        event_id
        for event_id in actual_ids & expected_ids
        if actual_by_id[event_id] != expected_by_id[event_id]
    )
    duplicate_ids = sorted(
        event_id
        for event_id in actual_ids & expected_ids
        if actual_count_by_id[event_id] > expected_count_by_id[event_id]
        and any(
            actual[(event_id, payload_hash)] > expected[(event_id, payload_hash)]
            for payload_hash in expected_by_id[event_id]
        )
    )
    duplicate_occurrences = sum(
        max(count - expected[identity], 0)
        for identity, count in actual.items()
        if identity in expected
    )
    unexpected_identity_hashes = sorted(
        identity for identity in actual if identity not in expected
    )
    missing_identity_hashes = sum((expected - actual).values())
    extra_identity_occurrences = sum((actual - expected).values())
    sample_limit = 200

    return {
        "expected_unique_event_count": sum(expected.values()),
        "expected_unique_event_id_count": len(expected_ids),
        "actual_unique_event_identity_hash_count": len(actual),
        "actual_unique_event_id_count": len(actual_ids),
        "matching_identity_hash_count": sum((actual & expected).values()),
        "missing_identity_hash_count": missing_identity_hashes,
        "missing_event_id_count": len(missing_ids),
        "missing_event_ids_sample": missing_ids[:sample_limit],
        "unexpected_unique_identity_hash_count": len(unexpected_identity_hashes),
        "unexpected_event_id_count": len(unexpected_ids),
        "unexpected_event_ids_sample": unexpected_ids[:sample_limit],
        "duplicate_identity_occurrence_count": duplicate_occurrences,
        "duplicate_event_id_count": len(duplicate_ids),
        "duplicate_event_ids_sample": duplicate_ids[:sample_limit],
        "payload_mismatch_event_id_count": len(payload_mismatch_ids),
        "payload_mismatch_event_ids_sample": payload_mismatch_ids[:sample_limit],
        "same_event_id_with_different_payload_count": len(payload_mismatch_ids),
        "unexpected_identity_occurrence_count": extra_identity_occurrences,
        "identity_id_samples_truncated": any(
            len(values) > sample_limit
            for values in (
                missing_ids,
                unexpected_ids,
                duplicate_ids,
                payload_mismatch_ids,
            )
        ),
    }


def _run_vector_buffer(
    paths: dict[str, Path],
    report: dict,
    clients: dict,
    loki_id: str,
    cpu: str,
    checkpoint_path: Path,
    run_start_monotonic: float,
) -> None:
    source = paths["vector_source"] / "events.jsonl"
    base_ns = time.time_ns() - 330 * 10**9
    start_ns = base_ns - 10**9
    expected, source_hash, fixture_bytes = _write_vector_backlog(source, base_ns)
    expected_pairs = Counter(expected)
    base.require(len(expected_pairs) == VECTOR_RECORDS, "synthetic_id_count_mismatch")
    config = _vector_config(paths["config"] / "vector.json", paths["tls"])
    report["config_sha256"]["vector"] = base.sha(config)
    selector = f'{{stack="tianshu",service="{VECTOR_SERVICE}"}}'
    end_ns = base_ns + (VECTOR_RECORDS - 1) * VECTOR_TIMESTAMP_STEP_NS + 10**9
    work_deadline = run_start_monotonic + WORK_TIMEOUT_SECONDS
    fill_deadline = min(run_start_monotonic + VECTOR_BUFFER_FULL_TIMEOUT_SECONDS, work_deadline)
    dimension = {
        "status": "running",
        "filesystem": "dedicated_tmpfs",
        "tmpfs_limit_bytes": TMPFS_BYTES,
        "configured_buffer_bytes": VECTOR_BUFFER_BYTES,
        "internal_buffer_limit_bytes": VECTOR_INTERNAL_BUFFER_BYTES,
        "reserved_data_file_bytes": VECTOR_DATA_FILE_BYTES,
        "synthetic_record_count": VECTOR_RECORDS,
        "padding_bytes_added_by_transform": VECTOR_PADDING_BYTES,
        "fixture_bytes": fixture_bytes,
        "source_sha256_before_pressure": source_hash,
        "source_progress_samples": [],
        "fill_samples": [],
        "fill_sample_errors": [],
        "backpressure_verified": False,
        "sink_stopped_before_vector_start": False,
        "replay": {"status": "not_run"},
        "store_only": {"status": "not_run"},
    }
    report["dimensions"]["vector_buffer_full"] = dimension
    _checkpoint(checkpoint_path, report)

    loki_stop = base._stop_owned(loki_id, LOKI_NAME, LOKI_IMAGE_ID, timeout=30)
    report["containers"]["loki"].setdefault("transitions", []).append(loki_stop)
    dimension["sink_stopped_before_vector_start"] = True
    dimension["sink_offline_at"] = _stamp()
    _checkpoint(checkpoint_path, report)
    vector_id = base.docker(*_vector_args(paths, cpu)).strip()
    report["containers"]["vector"] = {
        "id": vector_id,
        "name": VECTOR_NAME,
        "image_id": VECTOR_IMAGE_ID,
    }
    base._check_owned(base._container_by_id(vector_id), VECTOR_NAME, VECTOR_IMAGE_ID)
    _checkpoint(checkpoint_path, report)

    fill_samples = dimension["fill_samples"]
    fill_gate = _backpressure_evidence([], VECTOR_RECORDS, None)
    fill_failure = None
    peak_buffer_bytes = 0
    while time.monotonic() < fill_deadline:
        try:
            sample = _timestamped_vector_sample(paths, source, vector_id, loki_id)
        except base.ProbeError as exc:
            dimension["fill_sample_errors"].append({"observed_at": _stamp(), "error": str(exc)})
            time.sleep(min(5.1, max(0, fill_deadline - time.monotonic())))
            continue
        fill_samples.append(sample)
        metrics = sample["metrics"]
        peak_buffer_bytes = max(peak_buffer_bytes, metrics["buffer_bytes"])
        estimate = _empirical_encoded_event_bytes(fill_samples)
        fill_gate = _backpressure_evidence(fill_samples, VECTOR_RECORDS, estimate)
        dimension["fill_gate"] = fill_gate
        dimension["source_progress_samples"].append(metrics["source_sent_events"])
        dimension["peak_buffer_bytes"] = peak_buffer_bytes
        _checkpoint(checkpoint_path, report)
        unexpected_series = [
            series
            for series in metrics["component_error_series"]
            if series["value"] > 0
            and not (
                series["labels"].get("component_id") == "loki"
                and series["labels"].get("error_type") == "request_failed"
            )
        ]
        if sample["source_sha256"] != source_hash or sample["source_file_bytes"] != fixture_bytes["source_file_bytes"]:
            fill_failure = "synthetic_source_changed"
            break
        if not sample["vector_running"]:
            fill_failure = "vector_exited_during_fill"
            break
        if sample["sink_running"]:
            fill_failure = "sink_running_during_offline_fill"
            break
        if metrics["discarded_events"] > 0:
            fill_failure = "vector_events_discarded"
            break
        if unexpected_series:
            fill_failure = "unexpected_vector_component_error"
            dimension["unexpected_component_error_series"] = unexpected_series
            break
        if fill_gate["passed"]:
            break
        if metrics["source_sent_events"] >= VECTOR_RECORDS:
            fill_failure = "source_reached_eof_before_backpressure"
            break
        time.sleep(min(5.1, max(0, fill_deadline - time.monotonic())))
    dimension["fill_finished_at"] = _stamp()
    dimension["fill_failure"] = fill_failure
    dimension["peak_buffer_bytes"] = peak_buffer_bytes
    dimension["gap_to_internal_limit_bytes"] = VECTOR_INTERNAL_BUFFER_BYTES - peak_buffer_bytes
    dimension["source_sha256_after_fill"] = base.sha_file(source)
    dimension["source_preserved_at_fill"] = dimension["source_sha256_after_fill"] == source_hash
    fill_logs = _vector_logs(vector_id)
    (paths["evidence"] / "vector-fill-logs.txt").write_text(fill_logs, encoding="utf-8")
    dimension["fill_logs_sha256"] = base.sha(fill_logs.encode())
    dimension["fill_log_classification"] = _classify_vector_error_logs(fill_logs, sink_offline=True)
    dimension["backpressure_verified"] = (
        fill_gate["passed"]
        and fill_failure is None
        and dimension["source_preserved_at_fill"]
        and dimension["fill_log_classification"]["unexpected_error_count"] == 0
    )
    _checkpoint(checkpoint_path, report)

    replay_deadline = min(time.monotonic() + VECTOR_REPLAY_DRAIN_TIMEOUT_SECONDS,
                          run_start_monotonic + 900, work_deadline)
    replay = {
        "status": "running",
        "sink_restored_at": _stamp(),
        "samples": [],
        "mixed_query_attempts": [],
        "source_replay_complete": False,
    }
    dimension["replay"] = replay
    base._start_existing(loki_id, LOKI_NAME, LOKI_IMAGE_ID)
    ready_remaining = replay_deadline - time.monotonic()
    base.require(ready_remaining > 0, "replay_time_budget_exceeded")
    base._wait(
        lambda: base._loki_ready(clients["loki"]),
        timeout=min(90, ready_remaining),
        code="loki_recovery_ready_timeout",
    )
    replay["loki_ready_at"] = _stamp()
    try:
        ready_sample = _timestamped_vector_sample(paths, source, vector_id, loki_id)
        replay["samples"].append(ready_sample)
    except base.ProbeError as exc:
        replay["sample_errors"] = [{"observed_at": _stamp(), "error": str(exc)}]
    while time.monotonic() < replay_deadline:
        try:
            sample = _timestamped_vector_sample(paths, source, vector_id, loki_id)
        except base.ProbeError as exc:
            replay.setdefault("sample_errors", []).append({"observed_at": _stamp(), "error": str(exc)})
            time.sleep(min(5.1, max(0, replay_deadline - time.monotonic())))
            continue
        replay["samples"].append(sample)
        metrics = sample["metrics"]
        _checkpoint(checkpoint_path, report)
        if sample["source_sha256"] != source_hash or metrics["discarded_events"] > 0:
            replay["failure"] = "source_changed_or_events_discarded"
            break
        unexpected_series = [
            series for series in metrics["component_error_series"]
            if series["value"] > 0 and not (
                series["labels"].get("component_id") == "loki"
                and series["labels"].get("error_type") == "request_failed"
            )
        ]
        if unexpected_series:
            replay["failure"] = "unexpected_vector_component_error"
            replay["unexpected_component_error_series"] = unexpected_series
            break
        if metrics["source_sent_events"] >= VECTOR_RECORDS and metrics["buffer_bytes"] == 0:
            replay["source_replay_complete"] = True
            break
        time.sleep(min(5.1, max(0, replay_deadline - time.monotonic())))
    replay["finished_at"] = _stamp()
    replay["last_metrics"] = replay["samples"][-1]["metrics"] if replay["samples"] else None
    replay["source_sha256_after_replay"] = base.sha_file(source)
    replay["source_preserved"] = replay["source_sha256_after_replay"] == source_hash
    all_logs = _vector_logs(vector_id)
    recovery_logs = all_logs[len(fill_logs):] if all_logs.startswith(fill_logs) else all_logs
    (paths["evidence"] / "vector-recovery-logs.txt").write_text(recovery_logs, encoding="utf-8")
    replay["recovery_logs_sha256"] = base.sha(recovery_logs.encode())
    replay["recovery_log_classification"] = _classify_vector_error_logs(
        recovery_logs, sink_offline=False, allow_recovery_transport_retry=True
    )
    _checkpoint(checkpoint_path, report)

    vector_stop = base._stop_owned(vector_id, VECTOR_NAME, VECTOR_IMAGE_ID, timeout=90)
    report["containers"]["vector"].update(vector_stop)
    replay["source_sha256_after_vector_stop"] = base.sha_file(source)
    replay["source_preserved_after_vector_stop"] = replay["source_sha256_after_vector_stop"] == source_hash
    replay["buffer_data_files_after_vector_stop"] = [
        {"name": path.name, "bytes": path.stat().st_size, "sha256": base.sha_file(path)}
        for path in sorted((paths["vector_data"] / "buffer" / "v2" / "loki").glob("buffer-data-*.dat"))
        if path.is_file() and not path.is_symlink()
    ]
    _checkpoint(checkpoint_path, report)

    next_query_at = time.monotonic()
    while time.monotonic() < replay_deadline:
        attempt = _query_all_events(
            clients["loki"], selector, start_ns, end_ns, expected_pairs,
            deadline_monotonic=replay_deadline,
        )
        attempt["observed_at"] = _stamp()
        replay["mixed_query_attempts"].append(attempt)
        _checkpoint(checkpoint_path, report)
        if attempt["identity_hash_multiset_match"]:
            break
        next_query_at += 15
        time.sleep(min(max(0, next_query_at - time.monotonic()),
                       max(0, replay_deadline - time.monotonic())))
    mixed = replay["mixed_query_attempts"][-1] if replay["mixed_query_attempts"] else {}
    replay["mixed_route_identity_status"] = "passed" if mixed.get("identity_hash_multiset_match") else "failed"
    replay["status"] = (
        "passed" if replay["source_replay_complete"]
        and replay["mixed_route_identity_status"] == "passed"
        and replay["source_preserved_after_vector_stop"]
        and replay["recovery_log_classification"]["unexpected_error_count"] == 0
        and replay["last_metrics"] is not None
        and replay["last_metrics"]["discarded_events"] == 0
        and not replay.get("failure")
        else "failed"
    )
    _checkpoint(checkpoint_path, report)

    hash_cache = {}
    store = {
        "status": "running",
        "query_store_only": True,
        "snapshots": [],
        "query_attempts": [],
    }
    dimension["store_only"] = store
    store["snapshots"].append({"phase": "before_flush", "inventory": _loki_file_inventory(paths, hash_cache),
                               "metrics": _loki_metrics_evidence(clients["loki"])})
    flush_at = _stamp()
    flush_status, _, _ = clients["loki"].request("/flush", "POST", b"")
    store["flush"] = {"called_at": flush_at, "http_status": flush_status,
                       "completion_proven_by_status": False}
    store["snapshots"].append({"phase": "after_flush_response", "inventory": _loki_file_inventory(paths, hash_cache),
                               "metrics": _loki_metrics_evidence(clients["loki"])})
    _checkpoint(checkpoint_path, report)

    transition = base._stop_owned(loki_id, LOKI_NAME, LOKI_IMAGE_ID, timeout=90)
    report["containers"]["loki"].setdefault("transitions", []).append(transition)
    store["snapshots"].append({"phase": "after_loki_stop", "inventory": _loki_file_inventory(paths, hash_cache)})
    store_config = _write_loki_config(paths["config"] / "loki.json", query_store_only=True)
    report["config_sha256"]["loki_store_only"] = base.sha(store_config)
    base._start_existing(loki_id, LOKI_NAME, LOKI_IMAGE_ID)
    store_deadline = min(time.monotonic() + STORE_ONLY_QUERY_TIMEOUT_SECONDS,
                         run_start_monotonic + WORK_TIMEOUT_SECONDS)
    store_ready_remaining = store_deadline - time.monotonic()
    base.require(store_ready_remaining > 0, "store_only_time_budget_exceeded")
    base._wait(
        lambda: base._loki_ready(clients["loki"]),
        timeout=min(90, store_ready_remaining),
        code="loki_store_only_ready_timeout",
    )
    store["snapshots"].append({"phase": "after_store_restart", "inventory": _loki_file_inventory(paths, hash_cache),
                               "metrics": _loki_metrics_evidence(clients["loki"])})
    _checkpoint(checkpoint_path, report)
    next_query_at = time.monotonic()
    while time.monotonic() < store_deadline:
        attempt_started = _stamp()
        inventory = _loki_file_inventory(paths, hash_cache)
        metrics_evidence = _loki_metrics_evidence(clients["loki"])
        query = _query_all_events(
            clients["loki"], selector, start_ns, end_ns, expected_pairs,
            deadline_monotonic=store_deadline,
        )
        store["query_attempts"].append({
            "started_at": attempt_started,
            "finished_at": _stamp(),
            "inventory": inventory,
            "loki_metrics": metrics_evidence,
            "query": query,
        })
        _checkpoint(checkpoint_path, report)
        if query["identity_hash_multiset_match"]:
            break
        next_query_at += 15
        time.sleep(min(max(0, next_query_at - time.monotonic()),
                       max(0, store_deadline - time.monotonic())))
    final_store_query = store["query_attempts"][-1]["query"] if store["query_attempts"] else {}
    store["finished_at"] = _stamp()
    store["identity_status"] = "passed" if final_store_query.get("identity_hash_multiset_match") else "failed"
    store["status"] = (
        "passed" if store["identity_status"] == "passed" and flush_status == 204
        else "failed"
    )
    dimension["status"] = (
        "passed" if dimension["backpressure_verified"]
        and replay["status"] == "passed"
        and store["status"] == "passed"
        else "failed" if fill_failure or replay.get("failure") else "partial"
    )
    _checkpoint(checkpoint_path, report)


def _run_probe(root: Path) -> int:
    run_start_monotonic = time.monotonic()
    _configure_scope()
    report = base._initial_report(root)
    report["project"] = PROJECT
    report["run_id"] = RUN_ROOT.name
    report["application_reclamation_authorized"] = False
    report["release_ready"] = False
    report["bounded_run"] = {
        "started_at": _stamp(),
        "total_timeout_seconds": TOTAL_RUN_TIMEOUT_SECONDS,
        "work_timeout_seconds": WORK_TIMEOUT_SECONDS,
        "fill_timeout_seconds": VECTOR_BUFFER_FULL_TIMEOUT_SECONDS,
        "replay_timeout_seconds": VECTOR_REPLAY_DRAIN_TIMEOUT_SECONDS,
        "store_only_timeout_seconds": STORE_ONLY_QUERY_TIMEOUT_SECONDS,
    }
    report["dimensions"].update(
        {
            "recovery_query_diagnostics": {"status": "not_run"},
            "vector_buffer_full": {"status": "not_run"},
        }
    )
    clients = {}
    paths = None
    guard_state = server = server_thread = monitor_thread = None
    report_path = root / "evidence" / REPORT_NAME
    try:
        preflight = base.preflight(root, VECTOR_IMAGE_ID, LOKI_IMAGE_ID)
        if (
            preflight["selected_network_reused"]
            or preflight["existing_owned_containers"]
        ):
            raise base.ProbeError("followup_instance_must_be_new")
        paths = base._base_dirs(root.resolve())
        base.validate_tmpfs_size(shutil.disk_usage(paths["scratch"]).total)
        host_resources = _resource_snapshot()
        preflight["host_resource_snapshot"] = host_resources
        r2_report_path = (
            BASE_SCOPE
            / "runs"
            / "recovery-vector-20260925-r2"
            / "evidence"
            / REPORT_NAME
        )
        r2_report = json.loads(r2_report_path.read_text(encoding="utf-8"))
        r2_network_name = "tianshu-accept-a2-vfull-20260925-r2"
        r2_network = json.loads(base.docker("network", "inspect", r2_network_name))[0]
        r2_expected = {
            "vector": (r2_network_name + "-vector", VECTOR_IMAGE_ID),
            "loki": (r2_network_name + "-loki", LOKI_IMAGE_ID),
        }
        r2_containers_stopped = set(r2_report.get("containers", {})) == set(r2_expected)
        for key, (name, image_id) in r2_expected.items():
            entry = r2_report.get("containers", {}).get(key, {})
            if not entry.get("id"):
                r2_containers_stopped = False
                continue
            container = base._container_by_id(entry["id"])
            container_state = container.get("State", {})
            container_labels = container.get("Config", {}).get("Labels") or {}
            mounts_in_scope = all(
                mount.get("Type") != "bind"
                or base._overlap(
                    Path(mount["Source"]),
                    BASE_SCOPE / "runs" / "recovery-vector-20260925-r2",
                )
                for mount in container.get("Mounts", [])
            )
            r2_containers_stopped &= (
                container.get("Name", "").lstrip("/") == name
                and container.get("Image") == image_id
                and container_labels.get(base.OWNER_LABEL) == base.TASK
                and container_labels.get(base.SCOPE_LABEL) == r2_network_name
                and container.get("HostConfig", {}).get("RestartPolicy", {}).get("Name")
                == "no"
                and container.get("RestartCount", 0) == 0
                and container_state.get("Status") == "exited"
                and container_state.get("ExitCode") == 0
                and not container_state.get("Running")
                and not container_state.get("OOMKilled")
                and entry.get("running") is False
                and entry.get("exit_code") == 0
                and mounts_in_scope
            )
        r2_network_containers = r2_network.get("Containers") or {}
        r2_network_labels = r2_network.get("Labels") or {}
        r2_network_empty = (
            r2_network.get("Name") == r2_network_name
            and r2_network.get("Id") == r2_report.get("network", {}).get("id")
            and r2_network_labels.get(base.OWNER_LABEL) == base.TASK
            and r2_network_labels.get(base.SCOPE_LABEL) == r2_network_name
            and r2_network.get("Internal") is True
            and r2_network.get("IPAM", {}).get("Config", [{}])[0].get("Subnet")
            == "10.204.51.0/24"
            and not r2_network_containers
            and r2_report.get("network", {}).get("attached_container_count_after_stop")
            == 0
        )
        if not r2_containers_stopped or not r2_network_empty:
            raise base.ProbeError("prior_followup_instance_not_released")
        r3_root = BASE_SCOPE / "runs" / "recovery-vector-20260925-r3"
        r3_report = json.loads(
            (r3_root / "evidence" / REPORT_NAME).read_text(encoding="utf-8")
        )
        r3_network_name = "tianshu-accept-a2-vfull-20260925-r3"
        r3_network = json.loads(base.docker("network", "inspect", r3_network_name))[0]
        r3_network_labels = r3_network.get("Labels") or {}
        r3_loki_name = r3_network_name + "-loki"
        r3_loki = base._container_by_id(r3_loki_name)
        r3_labels = r3_loki.get("Config", {}).get("Labels") or {}
        r3_state = r3_loki.get("State", {})
        r3_network_empty = (
            r3_network.get("Id") == r3_report.get("network", {}).get("id")
            and r3_network_labels.get(base.OWNER_LABEL) == base.TASK
            and r3_network_labels.get(base.SCOPE_LABEL) == r3_network_name
            and r3_network.get("Internal") is True
            and r3_network.get("IPAM", {}).get("Config", [{}])[0].get("Subnet")
            == "10.204.52.0/24"
            and not r3_network.get("Containers")
            and r3_labels.get(base.OWNER_LABEL) == base.TASK
            and r3_labels.get(base.SCOPE_LABEL) == r3_network_name
        )
        r3_mounts_in_scope = all(
            mount.get("Type") != "bind" or base._overlap(Path(mount["Source"]), r3_root)
            for mount in r3_loki.get("Mounts", [])
        )
        r3_container_unstarted = (
            r3_loki.get("Name", "").lstrip("/") == r3_loki_name
            and r3_loki.get("Image") == LOKI_IMAGE_ID
            and r3_loki.get("HostConfig", {}).get("RestartPolicy", {}).get("Name")
            == "no"
            and r3_state.get("Status") == "created"
            and r3_state.get("ExitCode") == 128
            and not r3_state.get("Running")
            and not r3_state.get("OOMKilled")
            and r3_mounts_in_scope
            and "does not belong to any of this network's subnets"
            in r3_state.get("Error", "")
        )
        if not r3_network_empty or not r3_container_unstarted:
            raise base.ProbeError("prior_r3_failure_state_unrecognized")
        r4_root = BASE_SCOPE / "runs" / "recovery-vector-20260925-r4"
        r4_report = json.loads(
            (r4_root / "evidence" / REPORT_NAME).read_text(encoding="utf-8")
        )
        r4_network_name = "tianshu-accept-a2-vfull-20260925-r4"
        r4_network = json.loads(base.docker("network", "inspect", r4_network_name))[0]
        r4_expected = {
            "vector": (r4_network_name + "-vector", VECTOR_IMAGE_ID),
            "loki": (r4_network_name + "-loki", LOKI_IMAGE_ID),
        }
        r4_containers_stopped = True
        r4_names = set()
        for key, (name, image_id) in r4_expected.items():
            entry = r4_report.get("containers", {}).get(key, {})
            container = base._container_by_id(entry.get("id", name))
            labels = container.get("Config", {}).get("Labels") or {}
            container_state = container.get("State", {})
            mounts_in_scope = all(
                mount.get("Type") != "bind"
                or base._overlap(Path(mount["Source"]), r4_root)
                for mount in container.get("Mounts", [])
            )
            r4_containers_stopped &= (
                container.get("Name", "").lstrip("/") == name
                and container.get("Image") == image_id
                and labels.get(base.OWNER_LABEL) == base.TASK
                and labels.get(base.SCOPE_LABEL) == r4_network_name
                and container.get("HostConfig", {}).get("RestartPolicy", {}).get("Name")
                == "no"
                and container_state.get("Status") == "exited"
                and container_state.get("ExitCode") == 0
                and not container_state.get("Running")
                and not container_state.get("OOMKilled")
                and mounts_in_scope
            )
            r4_names.add(name)
        r4_network_containers = r4_network.get("Containers") or {}
        r4_network_labels = r4_network.get("Labels") or {}
        r4_network_names = {item.get("Name") for item in r4_network_containers.values()}
        r4_network_owned = (
            r4_network.get("Name") == r4_network_name
            and r4_network.get("Id") == r4_report.get("network", {}).get("id")
            and r4_network_labels.get(base.OWNER_LABEL) == base.TASK
            and r4_network_labels.get(base.SCOPE_LABEL) == r4_network_name
            and r4_network.get("Internal") is True
            and r4_network.get("IPAM", {}).get("Config", [{}])[0].get("Subnet")
            == "10.204.53.0/24"
            and r4_network_names.issubset(r4_names)
            and 0
            <= len(r4_network_names)
            <= r4_report.get("network", {}).get("attached_container_count_after_stop")
            <= len(r4_names)
        )
        archive_path = r4_root / "evidence" / "tmpfs-snapshot.tar.gz"
        r4_archive_sha256 = (
            base.sha_file(archive_path) if archive_path.is_file() else None
        )
        r4_mountpoint = str(r4_root / "tmpfs")
        r4_tmpfs_unmounted = all(
            len(fields := line.split()) < 2 or fields[1] != r4_mountpoint
            for line in Path("/proc/mounts").read_text().splitlines()
        )
        if not (
            r4_containers_stopped
            and r4_network_owned
            and r4_tmpfs_unmounted
            and r4_archive_sha256 == R4_SNAPSHOT_SHA256
        ):
            raise base.ProbeError("prior_r4_evidence_or_resources_unverified")

        r5_root = BASE_SCOPE / "runs" / "recovery-vector-20260925-r5"
        r5_report = json.loads(
            (r5_root / "evidence" / REPORT_NAME).read_text(encoding="utf-8")
        )
        r5_network_name = "tianshu-accept-a2-vfull-20260925-r5"
        r5_network = json.loads(base.docker("network", "inspect", r5_network_name))[0]
        r5_expected = {
            "vector": (r5_network_name + "-vector", VECTOR_IMAGE_ID),
            "loki": (r5_network_name + "-loki", LOKI_IMAGE_ID),
        }
        r5_containers_stopped = True
        r5_names = set()
        for key, (name, image_id) in r5_expected.items():
            entry = r5_report.get("containers", {}).get(key, {})
            if not entry.get("id"):
                r5_containers_stopped = False
                continue
            container = base._container_by_id(entry["id"])
            labels = container.get("Config", {}).get("Labels") or {}
            container_state = container.get("State", {})
            mounts_in_scope = all(
                mount.get("Type") != "bind"
                or base._overlap(Path(mount["Source"]), r5_root)
                for mount in container.get("Mounts", [])
            )
            r5_containers_stopped &= (
                container.get("Name", "").lstrip("/") == name
                and container.get("Image") == image_id
                and labels.get(base.OWNER_LABEL) == base.TASK
                and labels.get(base.SCOPE_LABEL) == r5_network_name
                and container.get("HostConfig", {}).get("RestartPolicy", {}).get("Name")
                == "no"
                and container.get("RestartCount", 0) == 0
                and container_state.get("Status") == "exited"
                and container_state.get("ExitCode") == 0
                and not container_state.get("Running")
                and not container_state.get("OOMKilled")
                and entry.get("running") is False
                and entry.get("exit_code") == 0
                and mounts_in_scope
            )
            r5_names.add(name)
        r5_network_containers = r5_network.get("Containers") or {}
        r5_network_labels = r5_network.get("Labels") or {}
        r5_network_names = {
            item.get("Name") for item in r5_network_containers.values()
        }
        r5_network_empty = (
            r5_network.get("Name") == r5_network_name
            and r5_network.get("Id") == r5_report.get("network", {}).get("id")
            and r5_network_labels.get(base.OWNER_LABEL) == base.TASK
            and r5_network_labels.get(base.SCOPE_LABEL) == r5_network_name
            and r5_network.get("Internal") is True
            and r5_network.get("IPAM", {}).get("Config", [{}])[0].get("Subnet")
            == "10.204.54.0/24"
            and not r5_network_names
            and r5_report.get("network", {}).get("attached_container_count_after_stop")
            == 0
            and r5_names == {name for name, _ in r5_expected.values()}
        )
        r5_archive_path = (
            r5_root
            / "evidence"
            / "tmpfs-snapshot-after-drain-continuation.tar.gz"
        )
        r5_archive_sha256 = (
            base.sha_file(r5_archive_path) if r5_archive_path.is_file() else None
        )
        r5_tmpfs_usage = _tmpfs_mount_usage(r5_root / "tmpfs")
        if not (
            r5_containers_stopped
            and r5_network_empty
            and not r5_tmpfs_usage["mounted"]
            and r5_archive_sha256 == R5_SNAPSHOT_SHA256
        ):
            raise base.ProbeError("prior_r5_evidence_or_resources_unverified")

        r6_root = BASE_SCOPE / "runs" / "recovery-vector-20260925-r6"
        r6_report = json.loads(
            (r6_root / "evidence" / REPORT_NAME).read_text(encoding="utf-8")
        )
        r6_network_name = "tianshu-accept-a2-vfull-20260925-r6"
        r6_network = json.loads(base.docker("network", "inspect", r6_network_name))[0]
        r6_expected = {
            "vector": (r6_network_name + "-vector", VECTOR_IMAGE_ID),
            "loki": (r6_network_name + "-loki", LOKI_IMAGE_ID),
        }
        r6_containers_stopped = r6_report.get("nas_verified") is True
        r6_names = set()
        for key, (name, image_id) in r6_expected.items():
            entry = r6_report.get("containers", {}).get(key, {})
            if not entry.get("id"):
                r6_containers_stopped = False
                continue
            container = base._container_by_id(entry["id"])
            labels = container.get("Config", {}).get("Labels") or {}
            container_state = container.get("State", {})
            mounts_in_scope = all(
                mount.get("Type") != "bind"
                or base._overlap(Path(mount["Source"]), r6_root)
                for mount in container.get("Mounts", [])
            )
            r6_containers_stopped &= (
                container.get("Name", "").lstrip("/") == name
                and container.get("Image") == image_id
                and labels.get(base.OWNER_LABEL) == base.TASK
                and labels.get(base.SCOPE_LABEL) == r6_network_name
                and container.get("HostConfig", {}).get("RestartPolicy", {}).get("Name")
                == "no"
                and container.get("RestartCount", 0) == 0
                and container_state.get("Status") == "exited"
                and container_state.get("ExitCode") == 0
                and not container_state.get("Running")
                and not container_state.get("OOMKilled")
                and entry.get("running") is False
                and entry.get("exit_code") == 0
                and mounts_in_scope
            )
            r6_names.add(name)
        r6_network_containers = r6_network.get("Containers") or {}
        r6_network_labels = r6_network.get("Labels") or {}
        r6_network_empty = (
            r6_report.get("status") == "partial"
            and r6_network.get("Name") == r6_network_name
            and r6_network.get("Id") == r6_report.get("network", {}).get("id")
            and r6_network_labels.get(base.OWNER_LABEL) == base.TASK
            and r6_network_labels.get(base.SCOPE_LABEL) == r6_network_name
            and r6_network.get("Internal") is True
            and r6_network.get("IPAM", {}).get("Config", [{}])[0].get("Subnet")
            == "10.204.55.0/24"
            and not r6_network_containers
            and r6_report.get("network", {}).get("attached_container_count_after_stop")
            == 0
            and r6_names == {name for name, _ in r6_expected.values()}
        )
        r6_archive_path = (
            r6_root / "evidence" / "tmpfs-snapshot-after-probe.tar.gz"
        )
        r6_archive_sha256 = (
            base.sha_file(r6_archive_path) if r6_archive_path.is_file() else None
        )
        r6_tmpfs_usage = _tmpfs_mount_usage(r6_root / "tmpfs")
        if not (
            r6_containers_stopped
            and r6_network_empty
            and not r6_tmpfs_usage["mounted"]
            and r6_archive_sha256 == R6_SNAPSHOT_SHA256
        ):
            raise base.ProbeError("prior_r6_evidence_or_resources_unverified")

        r7_root = BASE_SCOPE / "runs" / "recovery-vector-20260925-r7"
        r7_report = json.loads(
            (r7_root / "evidence" / REPORT_NAME).read_text(encoding="utf-8")
        )
        r7_network_name = "tianshu-accept-a2-vfull-20260925-r7"
        r7_network = json.loads(base.docker("network", "inspect", r7_network_name))[0]
        r7_expected = {
            "vector": (r7_network_name + "-vector", VECTOR_IMAGE_ID),
            "loki": (r7_network_name + "-loki", LOKI_IMAGE_ID),
        }
        r7_containers_stopped = r7_report.get("nas_verified") is True
        r7_names = set()
        for key, (name, image_id) in r7_expected.items():
            entry = r7_report.get("containers", {}).get(key, {})
            if not entry.get("id"):
                r7_containers_stopped = False
                continue
            container = base._container_by_id(entry["id"])
            labels = container.get("Config", {}).get("Labels") or {}
            container_state = container.get("State", {})
            mounts_in_scope = all(
                mount.get("Type") != "bind"
                or base._overlap(Path(mount["Source"]), r7_root)
                for mount in container.get("Mounts", [])
            )
            r7_containers_stopped &= (
                container.get("Name", "").lstrip("/") == name
                and container.get("Image") == image_id
                and labels.get(base.OWNER_LABEL) == base.TASK
                and labels.get(base.SCOPE_LABEL) == r7_network_name
                and container.get("HostConfig", {}).get("RestartPolicy", {}).get("Name")
                == "no"
                and container.get("RestartCount", 0) == 0
                and container_state.get("Status") == "exited"
                and container_state.get("ExitCode") == 0
                and not container_state.get("Running")
                and not container_state.get("OOMKilled")
                and entry.get("running") is False
                and entry.get("exit_code") == 0
                and mounts_in_scope
            )
            r7_names.add(name)
        r7_network_containers = r7_network.get("Containers") or {}
        r7_network_labels = r7_network.get("Labels") or {}
        r7_network_empty = (
            r7_report.get("status") == "partial"
            and r7_network.get("Name") == r7_network_name
            and r7_network.get("Id") == r7_report.get("network", {}).get("id")
            and r7_network_labels.get(base.OWNER_LABEL) == base.TASK
            and r7_network_labels.get(base.SCOPE_LABEL) == r7_network_name
            and r7_network.get("Internal") is True
            and r7_network.get("IPAM", {}).get("Config", [{}])[0].get("Subnet")
            == "10.205.16.0/24"
            and not r7_network_containers
            and r7_report.get("network", {}).get("attached_container_count_after_stop")
            == 0
            and r7_names == {name for name, _ in r7_expected.values()}
        )
        r7_archive_path = (
            r7_root / "evidence" / "tmpfs-snapshot-after-probe.tar.gz"
        )
        r7_archive_sha256 = (
            base.sha_file(r7_archive_path) if r7_archive_path.is_file() else None
        )
        r7_tmpfs_usage = _tmpfs_mount_usage(r7_root / "tmpfs")
        if not (
            r7_containers_stopped
            and r7_network_empty
            and not r7_tmpfs_usage["mounted"]
            and r7_archive_sha256 == R7_SNAPSHOT_SHA256
        ):
            raise base.ProbeError("prior_r7_evidence_or_resources_unverified")

        r8_root = BASE_SCOPE / "runs" / "recovery-vector-20260925-r8"
        r8_report = json.loads(
            (r8_root / "evidence" / REPORT_NAME).read_text(encoding="utf-8")
        )
        r8_network_name = "tianshu-accept-a2-vfull-20260925-r8"
        r8_network = json.loads(base.docker("network", "inspect", r8_network_name))[0]
        r8_expected = {
            "vector": (r8_network_name + "-vector", VECTOR_IMAGE_ID),
            "loki": (r8_network_name + "-loki", LOKI_IMAGE_ID),
        }
        r8_containers_stopped = r8_report.get("nas_verified") is True
        r8_names = set()
        for key, (name, image_id) in r8_expected.items():
            entry = r8_report.get("containers", {}).get(key, {})
            if not entry.get("id"):
                r8_containers_stopped = False
                continue
            container = base._container_by_id(entry["id"])
            labels = container.get("Config", {}).get("Labels") or {}
            container_state = container.get("State", {})
            mounts_in_scope = all(
                mount.get("Type") != "bind"
                or base._overlap(Path(mount["Source"]), r8_root)
                for mount in container.get("Mounts", [])
            )
            r8_containers_stopped &= (
                container.get("Name", "").lstrip("/") == name
                and container.get("Image") == image_id
                and labels.get(base.OWNER_LABEL) == base.TASK
                and labels.get(base.SCOPE_LABEL) == r8_network_name
                and container.get("HostConfig", {}).get("RestartPolicy", {}).get(
                    "Name"
                )
                == "no"
                and container.get("RestartCount", 0) == 0
                and container_state.get("Status") == "exited"
                and container_state.get("ExitCode") == 0
                and not container_state.get("Running")
                and not container_state.get("OOMKilled")
                and entry.get("running") is False
                and entry.get("exit_code") == 0
                and mounts_in_scope
            )
            r8_names.add(name)
        r8_network_containers = r8_network.get("Containers") or {}
        r8_network_labels = r8_network.get("Labels") or {}
        r8_network_empty = (
            r8_report.get("status") == "partial"
            and r8_network.get("Name") == r8_network_name
            and r8_network.get("Id") == r8_report.get("network", {}).get("id")
            and r8_network_labels.get(base.OWNER_LABEL) == base.TASK
            and r8_network_labels.get(base.SCOPE_LABEL) == r8_network_name
            and r8_network.get("Internal") is True
            and r8_network.get("IPAM", {}).get("Config", [{}])[0].get("Subnet")
            == "10.205.17.0/24"
            and not r8_network_containers
            and r8_report.get("network", {}).get("attached_container_count_after_stop")
            == 0
            and r8_names == {name for name, _ in r8_expected.values()}
        )
        r8_archive_path = (
            r8_root / "evidence" / "tmpfs-snapshot-after-probe.tar.gz"
        )
        r8_archive_sha256 = (
            base.sha_file(r8_archive_path) if r8_archive_path.is_file() else None
        )
        r8_tmpfs_usage = _tmpfs_mount_usage(r8_root / "tmpfs")
        if not (
            r8_containers_stopped
            and r8_network_empty
            and not r8_tmpfs_usage["mounted"]
            and r8_archive_sha256 == R8_SNAPSHOT_SHA256
        ):
            raise base.ProbeError("prior_r8_evidence_or_resources_unverified")

        r9_root = BASE_SCOPE / "runs" / "recovery-vector-20260925-r9"
        r9_report = json.loads(
            (r9_root / "evidence" / REPORT_NAME).read_text(encoding="utf-8")
        )
        r9_network_name = "tianshu-accept-a2-vfull-20260925-r9"
        r9_network = json.loads(base.docker("network", "inspect", r9_network_name))[0]
        r9_expected = {
            "vector": (r9_network_name + "-vector", VECTOR_IMAGE_ID),
            "loki": (r9_network_name + "-loki", LOKI_IMAGE_ID),
        }
        r9_containers_stopped = r9_report.get("nas_verified") is True
        r9_names = set()
        for key, (name, image_id) in r9_expected.items():
            entry = r9_report.get("containers", {}).get(key, {})
            if not entry.get("id"):
                r9_containers_stopped = False
                continue
            container = base._container_by_id(entry["id"])
            labels = container.get("Config", {}).get("Labels") or {}
            container_state = container.get("State", {})
            mounts_in_scope = all(
                mount.get("Type") != "bind"
                or base._overlap(Path(mount["Source"]), r9_root)
                for mount in container.get("Mounts", [])
            )
            r9_containers_stopped &= (
                container.get("Name", "").lstrip("/") == name
                and container.get("Image") == image_id
                and labels.get(base.OWNER_LABEL) == base.TASK
                and labels.get(base.SCOPE_LABEL) == r9_network_name
                and container.get("HostConfig", {}).get("RestartPolicy", {}).get(
                    "Name"
                )
                == "no"
                and container.get("RestartCount", 0) == 0
                and container_state.get("Status") == "exited"
                and container_state.get("ExitCode") == 0
                and not container_state.get("Running")
                and not container_state.get("OOMKilled")
                and entry.get("running") is False
                and entry.get("exit_code") == 0
                and mounts_in_scope
            )
            r9_names.add(name)
        r9_network_containers = r9_network.get("Containers") or {}
        r9_network_labels = r9_network.get("Labels") or {}
        r9_network_empty = (
            r9_report.get("status") == "partial"
            and r9_network.get("Name") == r9_network_name
            and r9_network.get("Id") == r9_report.get("network", {}).get("id")
            and r9_network_labels.get(base.OWNER_LABEL) == base.TASK
            and r9_network_labels.get(base.SCOPE_LABEL) == r9_network_name
            and r9_network.get("Internal") is True
            and r9_network.get("IPAM", {}).get("Config", [{}])[0].get("Subnet")
            == "10.205.18.0/24"
            and not r9_network_containers
            and r9_report.get("network", {}).get("attached_container_count_after_stop")
            == 0
            and r9_names == {name for name, _ in r9_expected.values()}
        )
        r9_archive_path = (
            r9_root / "evidence" / "tmpfs-snapshot-after-probe.tar.gz"
        )
        r9_archive_sha256 = (
            base.sha_file(r9_archive_path) if r9_archive_path.is_file() else None
        )
        r9_tmpfs_usage = _tmpfs_mount_usage(r9_root / "tmpfs")
        if not (
            r9_containers_stopped
            and r9_network_empty
            and not r9_tmpfs_usage["mounted"]
            and r9_archive_sha256 == R9_SNAPSHOT_SHA256
        ):
            raise base.ProbeError("prior_r9_evidence_or_resources_unverified")

        r10_root = BASE_SCOPE / "runs" / "recovery-vector-20260925-r10"
        r10_report = json.loads(
            (r10_root / "evidence" / REPORT_NAME).read_text(encoding="utf-8")
        )
        r10_network_name = "tianshu-accept-a2-vfull-20260925-r10"
        r10_network = json.loads(base.docker("network", "inspect", r10_network_name))[0]
        r10_expected = {
            "vector": (r10_network_name + "-vector", VECTOR_IMAGE_ID),
            "loki": (r10_network_name + "-loki", LOKI_IMAGE_ID),
        }
        r10_containers_stopped = r10_report.get("nas_verified") is True
        r10_names = set()
        for key, (name, image_id) in r10_expected.items():
            entry = r10_report.get("containers", {}).get(key, {})
            if not entry.get("id"):
                r10_containers_stopped = False
                continue
            container = base._container_by_id(entry["id"])
            labels = container.get("Config", {}).get("Labels") or {}
            container_state = container.get("State", {})
            mounts_in_scope = all(
                mount.get("Type") != "bind"
                or base._overlap(Path(mount["Source"]), r10_root)
                for mount in container.get("Mounts", [])
            )
            r10_containers_stopped &= (
                container.get("Name", "").lstrip("/") == name
                and container.get("Image") == image_id
                and labels.get(base.OWNER_LABEL) == base.TASK
                and labels.get(base.SCOPE_LABEL) == r10_network_name
                and container.get("HostConfig", {}).get("RestartPolicy", {}).get(
                    "Name"
                )
                == "no"
                and container.get("RestartCount", 0) == 0
                and container_state.get("Status") == "exited"
                and container_state.get("ExitCode") == 0
                and not container_state.get("Running")
                and not container_state.get("OOMKilled")
                and entry.get("running") is False
                and entry.get("exit_code") == 0
                and mounts_in_scope
            )
            r10_names.add(name)
        r10_network_containers = r10_network.get("Containers") or {}
        r10_network_labels = r10_network.get("Labels") or {}
        r10_network_empty = (
            r10_report.get("status") == "partial"
            and r10_network.get("Name") == r10_network_name
            and r10_network.get("Id") == r10_report.get("network", {}).get("id")
            and r10_network_labels.get(base.OWNER_LABEL) == base.TASK
            and r10_network_labels.get(base.SCOPE_LABEL) == r10_network_name
            and r10_network.get("Internal") is True
            and r10_network.get("IPAM", {}).get("Config", [{}])[0].get("Subnet")
            == "10.205.19.0/24"
            and not r10_network_containers
            and r10_report.get("network", {}).get("attached_container_count_after_stop")
            == 0
            and r10_names == {name for name, _ in r10_expected.values()}
        )
        r10_archive_path = (
            r10_root / "evidence" / "tmpfs-snapshot-after-probe.tar.gz"
        )
        r10_archive_sha256 = (
            base.sha_file(r10_archive_path) if r10_archive_path.is_file() else None
        )
        r10_tmpfs_usage = _tmpfs_mount_usage(r10_root / "tmpfs")
        if not (
            r10_containers_stopped
            and r10_network_empty
            and not r10_tmpfs_usage["mounted"]
            and r10_archive_sha256 == R10_SNAPSHOT_SHA256
        ):
            raise base.ProbeError("prior_r10_evidence_or_resources_unverified")

        r11_root = BASE_SCOPE / "runs" / "recovery-vector-20260925-r11"
        r11_report = json.loads(
            (r11_root / "evidence" / REPORT_NAME).read_text(encoding="utf-8")
        )
        r11_network_name = "tianshu-accept-a2-vfull-20260925-r11"
        r11_network = json.loads(base.docker("network", "inspect", r11_network_name))[0]
        r11_expected = {
            "vector": (r11_network_name + "-vector", VECTOR_IMAGE_ID),
            "loki": (r11_network_name + "-loki", LOKI_IMAGE_ID),
        }
        r11_containers_stopped = r11_report.get("nas_verified") is True
        r11_names = set()
        for key, (name, image_id) in r11_expected.items():
            entry = r11_report.get("containers", {}).get(key, {})
            if not entry.get("id"):
                r11_containers_stopped = False
                continue
            container = base._container_by_id(entry["id"])
            labels = container.get("Config", {}).get("Labels") or {}
            container_state = container.get("State", {})
            mounts_in_scope = all(
                mount.get("Type") != "bind"
                or base._overlap(Path(mount["Source"]), r11_root)
                for mount in container.get("Mounts", [])
            )
            r11_containers_stopped &= (
                container.get("Name", "").lstrip("/") == name
                and container.get("Image") == image_id
                and labels.get(base.OWNER_LABEL) == base.TASK
                and labels.get(base.SCOPE_LABEL) == r11_network_name
                and container.get("HostConfig", {}).get("RestartPolicy", {}).get(
                    "Name"
                )
                == "no"
                and container.get("RestartCount", 0) == 0
                and container_state.get("Status") == "exited"
                and container_state.get("ExitCode") == 0
                and not container_state.get("Running")
                and not container_state.get("OOMKilled")
                and entry.get("running") is False
                and entry.get("exit_code") == 0
                and mounts_in_scope
            )
            r11_names.add(name)
        r11_network_containers = r11_network.get("Containers") or {}
        r11_network_labels = r11_network.get("Labels") or {}
        r11_network_empty = (
            r11_report.get("status") == "partial"
            and r11_network.get("Name") == r11_network_name
            and r11_network.get("Id") == r11_report.get("network", {}).get("id")
            and r11_network_labels.get(base.OWNER_LABEL) == base.TASK
            and r11_network_labels.get(base.SCOPE_LABEL) == r11_network_name
            and r11_network.get("Internal") is True
            and r11_network.get("IPAM", {}).get("Config", [{}])[0].get("Subnet")
            == "10.205.20.0/24"
            and not r11_network_containers
            and r11_report.get("network", {}).get("attached_container_count_after_stop")
            == 0
            and r11_names == {name for name, _ in r11_expected.values()}
        )
        r11_archive_path = (
            r11_root / "evidence" / "tmpfs-snapshot-after-probe.tar.gz"
        )
        r11_archive_sha256 = (
            base.sha_file(r11_archive_path) if r11_archive_path.is_file() else None
        )
        r11_tmpfs_usage = _tmpfs_mount_usage(r11_root / "tmpfs")
        if not (
            r11_containers_stopped
            and r11_network_empty
            and not r11_tmpfs_usage["mounted"]
            and r11_archive_sha256 == R11_SNAPSHOT_SHA256
        ):
            raise base.ProbeError("prior_r11_evidence_or_resources_unverified")

        if host_resources["a2_running_container_count"] != 0:
            raise base.ProbeError("another_a2_instance_running")
        historical_tmpfs = {
            run: _tmpfs_mount_usage(BASE_SCOPE / "runs" / run / "tmpfs")
            for run in (
                "recovery-vector-20260925-r2",
                "recovery-vector-20260925-r3",
                "recovery-vector-20260925-r4",
                "recovery-vector-20260925-r5",
                "recovery-vector-20260925-r6",
                "recovery-vector-20260925-r7",
                "recovery-vector-20260925-r8",
                "recovery-vector-20260925-r9",
                "recovery-vector-20260925-r10",
                "recovery-vector-20260925-r11",
            )
        }
        resource_budget = _concurrent_a2_resource_budget(
            base.MEMORY_PLAN,
            host_resources["a2_current_mem_bytes"],
            historical_tmpfs,
        )
        preflight["concurrent_a2_resource_budget"] = {
            "r2_containers_stopped": r2_containers_stopped,
            "r2_network_empty_after_stop": r2_network_empty,
            "r3_failed_loki_container_unstarted": r3_container_unstarted,
            "r3_network_empty": r3_network_empty,
            "r4_containers_stopped": r4_containers_stopped,
            "r4_network_owned_stopped": r4_network_owned,
            "r4_snapshot_archive_bytes": archive_path.stat().st_size,
            "r4_snapshot_archive_sha256": r4_archive_sha256,
            "r5_containers_stopped": r5_containers_stopped,
            "r5_network_empty_after_stop": r5_network_empty,
            "r5_snapshot_archive_bytes": r5_archive_path.stat().st_size,
            "r5_snapshot_archive_sha256": r5_archive_sha256,
            "r6_containers_stopped": r6_containers_stopped,
            "r6_network_empty_after_stop": r6_network_empty,
            "r6_snapshot_archive_bytes": r6_archive_path.stat().st_size,
            "r6_snapshot_archive_sha256": r6_archive_sha256,
            "r7_containers_stopped": r7_containers_stopped,
            "r7_network_empty_after_stop": r7_network_empty,
            "r7_snapshot_archive_bytes": r7_archive_path.stat().st_size,
            "r7_snapshot_archive_sha256": r7_archive_sha256,
            "r8_containers_stopped": r8_containers_stopped,
            "r8_network_empty_after_stop": r8_network_empty,
            "r8_snapshot_archive_bytes": r8_archive_path.stat().st_size,
            "r8_snapshot_archive_sha256": r8_archive_sha256,
            "r9_containers_stopped": r9_containers_stopped,
            "r9_network_empty_after_stop": r9_network_empty,
            "r9_snapshot_archive_bytes": r9_archive_path.stat().st_size,
            "r9_snapshot_archive_sha256": r9_archive_sha256,
            "r10_containers_stopped": r10_containers_stopped,
            "r10_network_empty_after_stop": r10_network_empty,
            "r10_snapshot_archive_bytes": r10_archive_path.stat().st_size,
            "r10_snapshot_archive_sha256": r10_archive_sha256,
            "r11_containers_stopped": r11_containers_stopped,
            "r11_network_empty_after_stop": r11_network_empty,
            "r11_snapshot_archive_bytes": r11_archive_path.stat().st_size,
            "r11_snapshot_archive_sha256": r11_archive_sha256,
            "historical_mounted_tmpfs": historical_tmpfs,
            "currently_running_a2_container_count": host_resources[
                "a2_running_container_count"
            ],
            **resource_budget,
        }
        if not resource_budget["within_budget"]:
            raise base.ProbeError("concurrent_a2_resource_budget_exceeded")
        if host_resources["host_bytes"]["MemAvailable"] < 4 * 1024**3:
            raise base.ProbeError("host_memory_headroom_insufficient")
        if host_resources["task_volume_bytes"]["free"] < 512 * 1024**2:
            raise base.ProbeError("task_volume_headroom_insufficient")
        preflight["allocation_range"] = "10.205.16.0/20"
        preflight["candidate_subnets_checked"] = [str(SUBNET)]
        report["preflight"] = preflight
        base.certificates(paths["tls"], loki_ip=LOKI_IP)
        for path in paths["tls"].glob("*.pem"):
            os.chown(path, 10001, 10001)
            os.chmod(path, 0o444)
        for path in paths["tls"].glob("*.key"):
            os.chown(path, 10001, 10001)
            os.chmod(path, 0o440)
        for role in ("writer", "query", "metrics"):
            token_path = paths["secrets"] / f"{role}_token"
            token_path.write_text(secrets.token_urlsafe(32), encoding="utf-8")
            os.chmod(token_path, 0o600)

        contract_hashes = {
            path.name: base.sha_file(path)
            for path in sorted(paths["contract"].glob("*"))
            if path.is_file()
        }
        report["artifacts"] = {
            "contract_sha256": contract_hashes,
            "ca_pem_sha256": base.sha_file(paths["tls"] / "ca.pem"),
        }
        code_paths = [
            base.PACKAGE / name
            for name in (
                "alerts.py",
                "configs.py",
                "guard.py",
                "monitor.py",
                "policy.py",
                "query.py",
                "transport.py",
                "vocabulary.json",
            )
        ] + [
            Path(__file__).resolve(),
            Path(__file__).with_name("nas_a2_probe.py").resolve(),
            Path(__file__).with_name("helpers.py").resolve(),
        ]
        report["code_sha256"] = {
            path.relative_to(base.ROOT).as_posix(): base.sha_file(path)
            for path in code_paths
        }

        network_id = base.docker(
            "network",
            "create",
            "--driver",
            "bridge",
            "--internal",
            "--subnet",
            str(SUBNET),
            "--label",
            base.OWNER_LABEL + "=" + base.TASK,
            "--label",
            base.SCOPE_LABEL + "=" + PROJECT,
            NETWORK,
        ).strip()
        report["network"] = {
            "name": NETWORK,
            "id": network_id,
            "subnet": str(SUBNET),
            "internal": True,
            "reused": False,
        }
        checkpoint = paths["evidence"] / REPORT_NAME
        _checkpoint(checkpoint, report)

        loki_id = _start_loki(paths, report, preflight["cpu_affinity"]["loki"])
        _checkpoint(checkpoint, report)
        clients["loki"] = base._loki_client(paths)
        base._wait(
            lambda: base._loki_ready(clients["loki"]),
            timeout=90,
            code="loki_initial_ready_timeout",
        )
        report["dimensions"]["recovery_query_diagnostics"] = {
            "status": "not_run",
            "reason": "isolated_vector_full_buffer_probe",
        }
        _run_vector_buffer(
            paths,
            report,
            clients,
            loki_id,
            preflight["cpu_affinity"]["vector"],
            checkpoint,
            run_start_monotonic,
        )
        report["dimensions"]["application_reclamation_gate"] = {
            "status": "blocked",
            "reason": "no_frozen_application_log_reclamation_contract",
            "source_deletion_performed": False,
        }
        report["dimensions"]["physical_enospc"] = {"status": "not_run"}
        report["dimensions"]["production_30_day_retention"] = {"status": "not_run"}
        report["status"] = "partial"
        report["nas_verified"] = True
    except Exception as exc:
        report["status"] = "failed"
        report["nas_verified"] = False
        report["error_code"] = (
            str(exc)
            if isinstance(exc, base.ProbeError)
            else "probe_failed_details_withheld"
        )
        for client in clients.values():
            try:
                client.close()
            except Exception:
                pass
    finally:
        if guard_state is not None:
            guard_state.stop.set()
        if server is not None:
            try:
                server.shutdown()
                server.server_close()
            except Exception:
                report["cleanup_error_code"] = "guard_server_shutdown_unconfirmed"
                report["status"] = "failed"
        for thread in (server_thread, monitor_thread):
            if thread is not None:
                thread.join(timeout=5)
                if thread.is_alive():
                    report["cleanup_error_code"] = "guard_thread_exit_unconfirmed"
                    report["status"] = "failed"
        for client in clients.values():
            try:
                client.close()
            except Exception:
                pass
        vector = report.get("containers", {}).get("vector")
        loki = report.get("containers", {}).get("loki")
        if vector and loki and vector.get("id") and loki.get("id"):
            try:
                vector_state = base._container_by_id(vector["id"])["State"]
                loki_state = base._container_by_id(loki["id"])["State"]
                if vector_state.get("Running"):
                    rescue = {"started_at": _stamp(), "samples": []}
                    report["cleanup_recovery"] = rescue
                    if not loki_state.get("Running"):
                        base._start_existing(loki["id"], LOKI_NAME, LOKI_IMAGE_ID)
                        rescue["loki_restarted_for_drain"] = True
                    rescue_client = base._loki_client(paths)
                    try:
                        base._wait(
                            lambda: base._loki_ready(rescue_client),
                            timeout=60,
                            code="cleanup_loki_ready_timeout",
                        )
                    finally:
                        rescue_client.close()
                    rescue_deadline = min(
                        time.monotonic() + 150,
                        run_start_monotonic + TOTAL_RUN_TIMEOUT_SECONDS - 120,
                    )
                    while time.monotonic() < rescue_deadline:
                        snapshot = _try_vector_metrics_snapshot()
                        if snapshot is not None:
                            rescue["samples"].append({"observed_at": _stamp(), "metrics": snapshot})
                            if (
                                snapshot["source_sent_events"] >= VECTOR_RECORDS
                                and snapshot["buffer_bytes"] == 0
                            ):
                                rescue["drained"] = True
                                break
                        time.sleep(min(5, max(0, rescue_deadline - time.monotonic())))
                    rescue["finished_at"] = _stamp()
            except Exception as exc:
                report["cleanup_error_code"] = (
                    str(exc) if isinstance(exc, base.ProbeError)
                    else "cleanup_recovery_unconfirmed"
                )
                report["status"] = "failed"
        for key, name, image_id in (
            ("vector", VECTOR_NAME, VECTOR_IMAGE_ID),
            ("loki", LOKI_NAME, LOKI_IMAGE_ID),
        ):
            entry = report.get("containers", {}).get(key)
            if not entry or not entry.get("id"):
                continue
            try:
                stop_timeout = 90 if key == "vector" else 30
                stop = base._stop_owned(
                    entry["id"], name, image_id, timeout=stop_timeout
                )
                entry.update(stop)
            except Exception as exc:
                report["status"] = "failed"
                report["cleanup_error_code"] = (
                    str(exc)
                    if isinstance(exc, base.ProbeError)
                    else "cleanup_unconfirmed"
                )
        if paths is not None:
            report["tmpfs_used_bytes_after_stop"] = shutil.disk_usage(
                paths["scratch"]
            ).used
            report["application_reclamation_authorized"] = False
            report["release_ready"] = False
            if "network" in report:
                try:
                    network = json.loads(base.docker("network", "inspect", NETWORK))[0]
                    report["network"]["internal"] = network.get("Internal") is True
                    report["network"]["attached_container_count_after_stop"] = len(
                        network.get("Containers") or {}
                    )
                    if (
                        report["network"]["internal"] is not True
                        or report["network"]["attached_container_count_after_stop"] != 0
                    ):
                        report["cleanup_error_code"] = "network_not_internal_and_empty"
                        report["status"] = "failed"
                except Exception:
                    report["cleanup_error_code"] = "network_state_unconfirmed"
                    report["status"] = "failed"
            all_stopped = True
            for entry in report.get("containers", {}).values():
                if entry.get("id"):
                    try:
                        all_stopped &= not base._container_by_id(entry["id"])[
                            "State"
                        ].get("Running")
                    except Exception:
                        all_stopped = False
            if all_stopped:
                try:
                    _archive_and_unmount_new_scope(paths, report)
                except Exception as exc:
                    report["cleanup_error_code"] = (
                        str(exc)
                        if isinstance(exc, base.ProbeError)
                        else "new_scope_archive_or_unmount_failed"
                    )
                    report["status"] = "failed"
            else:
                report["cleanup_error_code"] = "containers_still_running_at_archive"
                report["status"] = "failed"
            report["bounded_run"]["finished_at"] = _stamp()
            report["bounded_run"]["elapsed_seconds"] = round(
                time.monotonic() - run_start_monotonic, 3
            )
            report["bounded_run"]["deadline_exceeded"] = (
                report["bounded_run"]["elapsed_seconds"]
                > TOTAL_RUN_TIMEOUT_SECONDS
            )
            if report["bounded_run"]["deadline_exceeded"]:
                report["status"] = "failed"
                report["cleanup_error_code"] = "total_run_deadline_exceeded"
            if report["status"] == "failed":
                report["nas_verified"] = False
            try:
                _checkpoint(paths["evidence"] / REPORT_NAME, report)
            except Exception:
                report["status"] = "failed"
                report["error_code"] = "evidence_write_failed"
        print(
            json.dumps(
                {
                    "status": report["status"],
                    "nas_verified": report.get("nas_verified", False),
                    "error_code": report.get("error_code"),
                    "cleanup_error_code": report.get("cleanup_error_code"),
                    "report": str(report_path),
                }
            )
        )
    return 0 if report.get("nas_verified") and report["status"] != "failed" else 1


def main() -> int:
    return _run_probe(RUN_ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
