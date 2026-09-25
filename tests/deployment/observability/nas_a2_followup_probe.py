"""Isolated NAS-A2 recovery diagnosis and Vector disk-buffer acceptance run."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import secrets
import shutil
import time
import urllib.request
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

import nas_a2_probe as base

BASE_SCOPE = Path("/volume2/tianshu-v2-validation-wave1/accept-20260925-a2")
RUN_ROOT = BASE_SCOPE / "runs" / "recovery-vector-20260925-r11"
PROJECT = "tianshu-accept-a2-vfull-20260925-r11"
NETWORK = PROJECT
SUBNET = ipaddress.ip_network("10.205.20.0/24")
VECTOR_NAME = PROJECT + "-vector"
LOKI_NAME = PROJECT + "-loki"
VECTOR_IP = "10.205.20.11"
LOKI_IP = "10.205.20.10"
GUARD_PORT = 19524
VECTOR_METRICS_PORT = 9598
TMPFS_BYTES = 1024 * 1024**2
VECTOR_BUFFER_BYTES = base.VECTOR_MINIMUM_BUFFER_BYTES
VECTOR_BUFFER_FULL_TIMEOUT_SECONDS = 600
VECTOR_REPLAY_DRAIN_TIMEOUT_SECONDS = 1800
VECTOR_REQUEST_RATE_LIMIT_PER_SECOND = 1
VECTOR_PADDING_BYTES = 2500
VECTOR_RECORDS = 120_000
VECTOR_TIMESTAMP_STEP_NS = 2_500_000
VECTOR_SERVICE = "nas-a2-vector-r11-" + uuid.uuid4().hex[:12]
RECOVERY_SERVICE = "nas-a2-recovery-r11-" + uuid.uuid4().hex[:12]
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


def _configure_scope() -> None:
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


def _write_vector_backlog(source: Path, base_ns: int) -> tuple[list, str]:
    expected = []
    padding = "x" * VECTOR_PADDING_BYTES

    def rows():
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
            expected.append((row["event_id"], base.digest(enriched)))
            yield row

    base._write_synthetic(source, rows(), append=True)
    os.chown(source, 10001, 10001)
    return expected, base.sha_file(source)


def _vector_prometheus_samples() -> dict[str, list[tuple[dict[str, str], float]]]:
    request = urllib.request.Request(
        f"http://{VECTOR_IP}:{VECTOR_METRICS_PORT}/metrics"
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            body = response.read(8 * 1024**2 + 1)
    except Exception:
        raise base.ProbeError("vector_metrics_unavailable") from None
    base.require(len(body) <= 8 * 1024**2, "vector_metrics_over_budget")
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


def _vector_metrics_snapshot() -> dict:
    samples = _vector_prometheus_samples()
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
    }


def _try_vector_metrics_snapshot() -> dict | None:
    try:
        return _vector_metrics_snapshot()
    except base.ProbeError:
        return None


def _backpressure_verified(
    source_progress: list[int],
    buffer_progress: list[int],
    source_sent_total: int,
    buffer_max_bytes: int,
) -> bool:
    source_stalled = (
        len(source_progress) >= 3
        and len(set(source_progress[-3:])) == 1
        and source_progress[-1] < source_sent_total
    )
    buffer_remained_full = (
        len(buffer_progress) >= 3
        and all(value >= buffer_max_bytes * 0.98 for value in buffer_progress[-3:])
    )
    return source_stalled and buffer_remained_full


def _query_all_events(
    client, selector: str, start_ns: int, end_ns: int, expected: Counter
) -> dict:
    actual = Counter()
    result_lines = 0
    requests = 0
    errors = []
    bucket_ns = 10**9
    low = (start_ns // bucket_ns) * bucket_ns
    while low <= end_ns:
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
        "identity_hash_multiset_match": actual == expected,
        "request_count": requests,
        "query_errors": errors[:20],
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
) -> None:
    source = paths["vector_source"] / "events.jsonl"
    base_ns = time.time_ns() - 330 * 10**9
    start_ns = base_ns - 10**9
    bulk_expected, source_hash_before_pressure = _write_vector_backlog(
        source, base_ns
    )
    expected_pairs = Counter(bulk_expected)
    source_size = source.stat().st_size
    source_sent_total = VECTOR_RECORDS
    config = _vector_config(paths["config"] / "vector.json", paths["tls"])
    report["config_sha256"]["vector"] = base.sha(config)
    dimension = {
        "status": "running",
        "filesystem": "dedicated_tmpfs",
        "tmpfs_limit_bytes": TMPFS_BYTES,
        "configured_buffer_bytes": VECTOR_BUFFER_BYTES,
        "vector_minimum_buffer_bytes": base.VECTOR_MINIMUM_BUFFER_BYTES,
        "padding_bytes_added_by_transform": VECTOR_PADDING_BYTES,
        "synthetic_record_count": source_sent_total,
        "source_bytes": source_size,
        "source_sha256_before_pressure": source_hash_before_pressure,
        "source_preserved_at_full": False,
        "source_progress_samples": [],
        "buffer_progress_samples": [],
        "backpressure_verified": False,
        "sink_stopped_before_vector_start": False,
        "sink_available_during_fill": True,
        "sink_rate_limited_during_fill": True,
        "replay": {"status": "not_run"},
    }
    report["dimensions"]["vector_buffer_full"] = dimension
    _checkpoint(checkpoint_path, report)
    vector_id = base.docker(*_vector_args(paths, cpu)).strip()
    report["containers"]["vector"] = {
        "id": vector_id,
        "name": VECTOR_NAME,
        "image_id": VECTOR_IMAGE_ID,
    }
    _checkpoint(checkpoint_path, report)
    base._check_owned(base._container_by_id(vector_id), VECTOR_NAME, VECTOR_IMAGE_ID)

    peak = {"buffer_bytes": 0, "buffer_max_bytes": 0}
    last_snapshot = {}

    def full_observed():
        nonlocal last_snapshot
        snapshot = _try_vector_metrics_snapshot()
        if snapshot is None:
            return False
        last_snapshot = snapshot
        peak["buffer_bytes"] = max(peak["buffer_bytes"], last_snapshot["buffer_bytes"])
        peak["buffer_max_bytes"] = max(
            peak["buffer_max_bytes"], last_snapshot["buffer_max_bytes"]
        )
        return (
            last_snapshot["buffer_max_bytes"] >= VECTOR_BUFFER_BYTES * 0.99
            and last_snapshot["buffer_bytes"]
            >= last_snapshot["buffer_max_bytes"] * 0.98
        )

    try:
        base._wait(
            full_observed,
            timeout=VECTOR_BUFFER_FULL_TIMEOUT_SECONDS,
            interval=1,
            code="vector_buffer_not_full",
        )
        progress = []
        buffer_progress = []
        progress_deadline = time.monotonic() + 30
        while time.monotonic() < progress_deadline:
            snapshot = _try_vector_metrics_snapshot()
            if snapshot is None:
                time.sleep(2)
                continue
            last_snapshot = snapshot
            progress.append(snapshot["source_sent_events"])
            buffer_progress.append(snapshot["buffer_bytes"])
            if snapshot["source_sent_events"] >= source_sent_total:
                break
            time.sleep(2)
        if not progress:
            raise base.ProbeError("vector_metrics_unavailable")
        source_hash_at_full = base.sha_file(source)
        source_stalled = len(progress) >= 3 and len(set(progress[-3:])) == 1
        buffer_remained_full = (
            len(buffer_progress) >= 3
            and all(
                value >= last_snapshot["buffer_max_bytes"] * 0.98
                for value in buffer_progress[-3:]
            )
        )
        backpressure = _backpressure_verified(
            progress,
            buffer_progress,
            source_sent_total,
            last_snapshot["buffer_max_bytes"],
        )
        dimension.update(
            peak_buffer_bytes=peak["buffer_bytes"],
            buffer_max_bytes=peak["buffer_max_bytes"],
            peak_ratio=(
                round(peak["buffer_bytes"] / peak["buffer_max_bytes"], 5)
                if peak["buffer_max_bytes"]
                else 0
            ),
            source_events_sent_at_full=progress[-1],
            source_progress_samples=progress,
            buffer_progress_samples=buffer_progress,
            source_progress_stalled=source_stalled,
            buffer_remained_full_during_progress_window=buffer_remained_full,
            source_sha256_at_full=source_hash_at_full,
            source_preserved_at_full=source_hash_at_full == source_hash_before_pressure,
            source_file_bytes_at_full=source.stat().st_size,
            tmpfs_used_bytes_at_full=shutil.disk_usage(paths["scratch"]).used,
            tmpfs_free_bytes_at_full=shutil.disk_usage(paths["scratch"]).free,
            discarded_events_at_full=last_snapshot["discarded_events"],
            component_errors_at_full=last_snapshot["component_errors"],
            backpressure_verified=backpressure,
        )
    except base.ProbeError as exc:
        dimension.update(
            status="failed",
            reason=str(exc),
            last_metrics=last_snapshot,
            peak_buffer_bytes=peak["buffer_bytes"],
            buffer_max_bytes=peak["buffer_max_bytes"],
            source_sha256_at_full=base.sha_file(source),
            source_preserved_at_full=base.sha_file(source)
            == source_hash_before_pressure,
            source_file_bytes_at_full=source.stat().st_size,
        )
    _checkpoint(checkpoint_path, report)

    dimension["loki_started_after_buffer_full"] = False
    dimension["sink_already_available_after_full"] = True
    _checkpoint(checkpoint_path, report)

    def replay_drained():
        nonlocal last_snapshot
        snapshot = _try_vector_metrics_snapshot()
        if snapshot is None:
            return False
        last_snapshot = snapshot
        return (
            last_snapshot["source_sent_events"] >= source_sent_total
            and last_snapshot["buffer_max_bytes"] > 0
            and last_snapshot["buffer_bytes"]
            <= last_snapshot["buffer_max_bytes"] * 0.05
        )

    try:
        base._wait(
            replay_drained,
            timeout=VECTOR_REPLAY_DRAIN_TIMEOUT_SECONDS,
            interval=2,
            code="vector_replay_not_drained",
        )
        dimension["source_replay_complete"] = True
    except base.ProbeError as exc:
        dimension["source_replay_complete"] = False
        dimension["replay_drain_error"] = str(exc)
    dimension["metrics_after_reconnect"] = last_snapshot
    dimension["source_sha256_after_reconnect"] = base.sha_file(source)
    dimension["source_preserved_after_reconnect"] = (
        dimension["source_sha256_after_reconnect"] == source_hash_before_pressure
    )
    _checkpoint(checkpoint_path, report)

    flush_status, _, _ = clients["loki"].request("/flush", "POST", b"")
    dimension["loki_flush_status"] = flush_status
    vector_stop = base._stop_owned(vector_id, VECTOR_NAME, VECTOR_IMAGE_ID, timeout=90)
    report["containers"]["vector"].update(vector_stop)
    dimension["source_sha256_after_vector_stop"] = base.sha_file(source)

    _switch_loki_mode(paths, report, loki_id, query_store_only=True)
    base._wait(
        lambda: base._loki_ready(clients["loki"]),
        timeout=90,
        code="loki_vector_store_restart_timeout",
    )
    selector = f'{{stack="tianshu",service="{VECTOR_SERVICE}"}}'
    end_ns = base_ns + (VECTOR_RECORDS - 1) * VECTOR_TIMESTAMP_STEP_NS + 10**9
    store_index_readiness = _wait_for_series_routes(
        {"store_only_direct_loki": clients["loki"]},
        selector,
        start_ns,
        end_ns,
        timeout=120,
    )
    dimension["replay"] = {
        "status": "running",
        "query_store_only": True,
        "store_index_readiness": store_index_readiness,
    }
    _checkpoint(checkpoint_path, report)
    replay = _query_all_events(
        clients["loki"], selector, start_ns, end_ns, expected_pairs
    )
    replay["store_index_readiness"] = store_index_readiness
    replay.update(
        {
            "query_store_only": True,
            "query_bounds_ns": {"start": start_ns, "end": end_ns},
            "service_label": VECTOR_SERVICE,
            "flush_status": flush_status,
            "discarded_events_after_reconnect": last_snapshot.get("discarded_events"),
        }
    )
    replay["store_only_identity_status"] = (
        "passed"
        if replay["identity_hash_multiset_match"]
        and replay["store_index_readiness"]["ready"]
        else "failed"
    )
    if replay["store_only_identity_status"] != "passed":
        _switch_loki_mode(paths, report, loki_id, query_store_only=False)
        base._wait(
            lambda: base._loki_ready(clients["loki"]),
            timeout=90,
            code="loki_vector_ingester_fallback_timeout",
        )
        replay["ingester_fallback_reconciliation"] = _query_all_events(
            clients["loki"], selector, start_ns, end_ns, expected_pairs
        )
    ingester_fallback = replay.get("ingester_fallback_reconciliation", {})
    identity_reconciled = replay[
        "identity_hash_multiset_match"
    ] or ingester_fallback.get("identity_hash_multiset_match", False)
    replay["buffer_replay_identity_status"] = (
        "passed" if identity_reconciled else "failed"
    )
    safety_passed = (
        dimension.get("backpressure_verified")
        and dimension.get("source_preserved_at_full")
        and dimension.get("source_replay_complete")
        and dimension.get("source_preserved_after_reconnect")
        and last_snapshot.get("discarded_events") == 0
    )
    replay["status"] = "passed" if identity_reconciled and safety_passed else "partial"
    dimension["replay"] = replay
    dimension["status"] = (
        "passed"
        if replay["status"] == "passed"
        and replay["store_only_identity_status"] == "passed"
        else "partial"
        if replay["buffer_replay_identity_status"] == "passed"
        else "failed"
    )
    _checkpoint(checkpoint_path, report)


def _run_probe(root: Path) -> dict:
    _configure_scope()
    report = base._initial_report(root)
    report["project"] = PROJECT
    report["run_id"] = RUN_ROOT.name
    report["application_reclamation_authorized"] = False
    report["release_ready"] = False
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
            "historical_mounted_tmpfs": historical_tmpfs,
            "currently_running_a2_container_count": host_resources[
                "a2_running_container_count"
            ],
            **resource_budget,
        }
        if not resource_budget["within_budget"]:
            raise base.ProbeError("concurrent_a2_resource_budget_exceeded")
        if host_resources["host_bytes"]["MemAvailable"] < 2 * 1024**3:
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
        if "network" in report:
            vector = report.get("containers", {}).get("vector")
            loki = report.get("containers", {}).get("loki")
            if vector and loki:
                try:
                    network_state = json.loads(
                        base.docker("network", "inspect", NETWORK)
                    )[0]
                    attached = network_state.get("Containers") or {}
                    attached_names = {item.get("Name") for item in attached.values()}
                    if (
                        VECTOR_NAME in attached_names
                        and LOKI_NAME not in attached_names
                    ):
                        base.docker(
                            "network",
                            "connect",
                            "--alias",
                            "obs-loki",
                            "--ip",
                            LOKI_IP,
                            NETWORK,
                            loki["id"],
                        )
                except Exception:
                    report["cleanup_error_code"] = "network_reattach_unconfirmed"
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
                except Exception:
                    report["cleanup_error_code"] = "network_state_unconfirmed"
                    report["status"] = "failed"
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
    return 0 if report.get("nas_verified") else 1


def main() -> int:
    return _run_probe(RUN_ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
