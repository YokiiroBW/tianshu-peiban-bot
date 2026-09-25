"""NAS-A2 bounded capacity probe; uses only its fixed isolated scope and images."""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import re
import secrets
import shutil
import socket
import ssl
import subprocess
import sys
import threading
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PACKAGE = ROOT / "deploy/observability"
sys.path.insert(0, str(PACKAGE))
from configs import loki as loki_config  # noqa: E402
from guard import Server, State  # noqa: E402
from helpers import certificates, event  # noqa: E402
from monitor import capacities  # noqa: E402
from policy import canonical, digest  # noqa: E402
from query import LokiClient  # noqa: E402

TASK = "NAS-A2"
EXPECTED_ROOT = Path("/volume2/tianshu-v2-validation-wave1/accept-20260925-a2")
PROJECT = "tianshu-accept-a2-capacity-20260925"
NETWORK = PROJECT
SUBNET = ipaddress.ip_network("10.204.50.0/24")
VECTOR_NAME = PROJECT + "-vector"
LOKI_NAME = PROJECT + "-loki"
VECTOR_IMAGE_REFERENCE = "timberio/vector:0.58.0-debian"
LOKI_IMAGE_REFERENCE = "grafana/loki:3.7.8"
EXPECTED_VECTOR_IMAGE_ID = (
    "sha256:92c275b73d880922a265918a7c3c4f2cc0dd87338447ff357809f2d18a64a48e"
)
EXPECTED_LOKI_IMAGE_ID = (
    "sha256:ceccdbc45e274f08eb23d6ca6e0b648921d580c592ab47b4304225c0f17a406a"
)
VECTOR_MINIMUM_BUFFER_BYTES = 268435488
GUARD_HOST_PORT = 19522
PORTS = (GUARD_HOST_PORT,)
VECTOR_IP = "10.204.50.11"
LOKI_IP = "10.204.50.10"
TMPFS_LIMIT = 256 * 1024**2
VECTOR_BUFFER_LIMIT = 8 * 1024**2
MAX_FILLER_BYTES = 112 * 1024**2
MEMORY_LIMITS = {"vector": 512 * 1024**2, "loki": 768 * 1024**2}
OWNER_LABEL = "org.tianshu.acceptance.owner"
SCOPE_LABEL = "org.tianshu.acceptance.scope"
MEMORY_PLAN = sum(MEMORY_LIMITS.values()) + 256 * 1024**2 + TMPFS_LIMIT
DOCKER = "/volume2/@appstore/ContainerManager/usr/bin/docker"


class ProbeError(RuntimeError):
    """Sanitized acceptance failure code."""


def require(condition: bool, code: str) -> None:
    if not condition:
        raise ProbeError(code)


def validate_root(path: Path) -> Path:
    resolved = path.resolve(strict=True)
    require(resolved == EXPECTED_ROOT, "a2_scope_path_mismatch")
    require(not path.is_symlink(), "a2_scope_symlink_refused")
    return resolved


def validate_tmpfs_size(size: int) -> None:
    require(0 < size <= TMPFS_LIMIT, "tmpfs_budget_exceeded")


def parse_memory_available() -> int:
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) * 1024
    raise ProbeError("memory_headroom_unavailable")


def parse_cpu_list(value: str) -> list[int]:
    cpus = set()
    for part in value.split(","):
        bounds = part.split("-", 1)
        require(
            len(bounds) in (1, 2) and all(bound.isdigit() for bound in bounds),
            "cpu_affinity_unavailable",
        )
        first = int(bounds[0])
        last = int(bounds[-1])
        require(first <= last, "cpu_affinity_unavailable")
        cpus.update(range(first, last + 1))
    require(bool(cpus), "cpu_affinity_unavailable")
    return sorted(cpus)


def _run(command: list[str], *, timeout: int = 30) -> str:
    env = os.environ.copy()
    for key in (
        "DOCKER_HOST",
        "DOCKER_CONTEXT",
        "DOCKER_TLS_VERIFY",
        "DOCKER_CERT_PATH",
    ):
        env.pop(key, None)
    process = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
        env=env,
        check=False,
    )
    if process.returncode:
        raise ProbeError("command_failed_" + Path(command[0]).name)
    return process.stdout


def docker(*args: str, timeout: int = 30) -> str:
    return _run([DOCKER, *args], timeout=timeout)


def docker_logs(container_id: str) -> str:
    env = os.environ.copy()
    for key in (
        "DOCKER_HOST",
        "DOCKER_CONTEXT",
        "DOCKER_TLS_VERIFY",
        "DOCKER_CERT_PATH",
    ):
        env.pop(key, None)
    process = subprocess.run(
        [DOCKER, "logs", "--tail", "30", container_id],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=30,
        env=env,
        check=False,
    )
    if process.returncode:
        raise ProbeError("command_failed_docker_logs")
    return process.stdout + "\n" + process.stderr


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def write_json(path: Path, value) -> None:
    data = (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        directory_flag = getattr(os, "O_DIRECTORY", None)
        if directory_flag is not None:
            fd = os.open(path.parent, directory_flag)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _overlap(left: Path, right: Path) -> bool:
    a, b = os.path.realpath(left), os.path.realpath(right)
    try:
        common = os.path.commonpath((a, b))
    except ValueError:
        return False
    return common == a or common == b


def _inspect_all_containers() -> list[dict]:
    ids = docker("ps", "-aq").split()
    return json.loads(docker("inspect", *ids, timeout=60)) if ids else []


def _inspect_all_networks() -> list[dict]:
    ids = docker("network", "ls", "-q").split()
    return json.loads(docker("network", "inspect", *ids, timeout=60)) if ids else []


def _subnet_collisions(networks: list[dict], routes: str) -> list[str]:
    occupied = []
    for network in networks:
        for config in network.get("IPAM", {}).get("Config") or []:
            if config.get("Subnet"):
                occupied.append(
                    (
                        network.get("Name", "docker"),
                        ipaddress.ip_network(config["Subnet"]),
                    )
                )
    for line in routes.splitlines():
        value = line.split()[0] if line.split() else ""
        if not value or value == "default":
            continue
        try:
            route = ipaddress.ip_network(
                value if "/" in value else value + "/32", strict=False
            )
        except ValueError:
            continue
        occupied.append(("host-route", route))
    return [
        f"{name}:{network}" for name, network in occupied if SUBNET.overlaps(network)
    ]


def _ignore_owned_network_route(routes: str, network_id: str) -> str:
    bridge = "docker-" + network_id[:8]
    retained = []
    for line in routes.splitlines():
        fields = line.split()
        if (
            len(fields) >= 3
            and fields[0] == str(SUBNET)
            and fields[1] == "dev"
            and fields[2] == bridge
        ):
            continue
        retained.append(line)
    return "\n".join(retained)


def preflight(root: Path, vector_image_id: str, loki_image_id: str) -> dict:
    require(sys.platform == "linux", "linux_required")
    require(
        vector_image_id == EXPECTED_VECTOR_IMAGE_ID
        and loki_image_id == EXPECTED_LOKI_IMAGE_ID,
        "image_digest_not_authorized",
    )
    resolved = validate_root(root)
    tmpfs = resolved / "tmpfs"
    mounts = Path("/proc/mounts").read_text().splitlines()
    tmpfs_mount = None
    for line in mounts:
        fields = line.split()
        mountpoint = fields[1].replace("\\040", " ")
        if mountpoint == str(tmpfs):
            tmpfs_mount = fields
            break
    require(
        tmpfs_mount is not None and tmpfs_mount[2] == "tmpfs", "bounded_tmpfs_required"
    )
    tmpfs_total = shutil.disk_usage(tmpfs).total
    validate_tmpfs_size(tmpfs_total)

    available = parse_memory_available()
    require(available >= 2 * 1024**3, "insufficient_memory_headroom")
    require(MEMORY_PLAN <= 4 * 1024**3, "task_memory_budget_exceeded")

    docker_info = json.loads(docker("info", "--format", "{{json .}}"))
    require(docker_info.get("CPUSet") is True, "docker_cpuset_unavailable")
    cpu_match = re.search(
        r"^Cpus_allowed_list:\s*(\S+)$",
        Path("/proc/self/status").read_text(),
        re.MULTILINE,
    )
    require(cpu_match is not None, "cpu_affinity_unavailable")
    allowed_cpus = parse_cpu_list(cpu_match.group(1))
    require(len(allowed_cpus) >= 2, "cpu_affinity_unavailable")
    cpu_affinity = {"vector": str(allowed_cpus[0]), "loki": str(allowed_cpus[1])}

    containers = _inspect_all_containers()
    conflicts = []
    used_ports = set()
    existing_by_name = {}
    for container in containers:
        name = container.get("Name", "").lstrip("/")
        if name in {VECTOR_NAME, LOKI_NAME}:
            existing_by_name[name] = container
        labels = container.get("Config", {}).get("Labels") or {}
        owned_a2 = (
            name in {VECTOR_NAME, LOKI_NAME}
            and labels.get(OWNER_LABEL) == TASK
            and labels.get(SCOPE_LABEL) == PROJECT
        )
        for mount in container.get("Mounts", []):
            if (
                not owned_a2
                and mount.get("Type") == "bind"
                and _overlap(Path(mount["Source"]), resolved)
            ):
                conflicts.append(
                    container.get("Name", "unknown") + ":" + mount["Source"]
                )
        for bindings in (
            container.get("HostConfig", {}).get("PortBindings") or {}
        ).values():
            for binding in bindings or []:
                if binding and binding.get("HostPort"):
                    used_ports.add(int(binding["HostPort"]))
    require(not conflicts, "docker_mount_overlap")
    expected_images = {VECTOR_NAME: vector_image_id, LOKI_NAME: loki_image_id}
    existing_owned_containers = {}
    for name, container in existing_by_name.items():
        image_id = expected_images[name]
        _check_owned(container, name, image_id)
        state = container.get("State", {})
        require(
            not state.get("Running")
            and not state.get("OOMKilled")
            and container.get("RestartCount", 0) == 0,
            "a2_existing_container_not_stopped_cleanly",
        )
        if name == VECTOR_NAME:
            logs = docker_logs(container["Id"])
            minimum_match = re.search(
                r"must be greater than or equal to (\d+) bytes", logs
            )
            require(
                state.get("ExitCode") == 78
                and minimum_match is not None
                and int(minimum_match.group(1)) == 268435488,
                "a2_existing_vector_failure_unrecognized",
            )
            existing_owned_containers["vector"] = {
                "id": container["Id"],
                "name": name,
                "image_id": image_id,
                "running": False,
                "exit_code": 78,
                "oom_killed": False,
                "minimum_buffer_bytes": int(minimum_match.group(1)),
                "startup_error": "configured_buffer_below_vector_minimum",
            }
        else:
            host = container.get("HostConfig", {})
            require(
                state.get("ExitCode") == 0
                and host.get("Memory") == MEMORY_LIMITS["loki"]
                and host.get("CpusetCpus") == cpu_affinity["loki"],
                "a2_existing_loki_state_unrecognized",
            )
            existing_owned_containers["loki"] = {
                "id": container["Id"],
                "name": name,
                "image_id": image_id,
                "running": False,
                "exit_code": 0,
                "oom_killed": False,
                "reusable": True,
            }
    require(not (set(PORTS) & used_ports), "a2_host_port_in_use")
    for port in PORTS:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            raise ProbeError("a2_loopback_port_in_use") from None
        finally:
            sock.close()

    networks = _inspect_all_networks()
    selected_network = next(
        (item for item in networks if item.get("Name") == NETWORK), None
    )
    if selected_network is not None:
        labels = selected_network.get("Labels") or {}
        subnets = [
            config.get("Subnet")
            for config in selected_network.get("IPAM", {}).get("Config") or []
        ]
        require(
            labels.get(OWNER_LABEL) == TASK
            and labels.get(SCOPE_LABEL) == PROJECT
            and selected_network.get("Internal") is True
            and subnets == [str(SUBNET)]
            and not selected_network.get("Containers"),
            "a2_network_identity_or_state_mismatch",
        )
    routes = _run(["/sbin/ip", "-4", "route", "show"], timeout=10)
    if selected_network is not None:
        routes = _ignore_owned_network_route(routes, selected_network["Id"])
    collisions = _subnet_collisions(
        [item for item in networks if item.get("Name") != NETWORK], routes
    )
    require(not collisions, "a2_subnet_overlap")

    images = {}
    for name, image_id, reference in (
        ("vector", vector_image_id, VECTOR_IMAGE_REFERENCE),
        ("loki", loki_image_id, LOKI_IMAGE_REFERENCE),
    ):
        require(
            re.fullmatch(r"sha256:[0-9a-f]{64}", image_id) is not None,
            "image_id_required",
        )
        inspect = json.loads(docker("image", "inspect", image_id))[0]
        require(inspect["Id"] == image_id, "image_id_mismatch")
        images[name] = {
            "reference": reference,
            "id": inspect["Id"],
            "repo_digests": sorted(inspect.get("RepoDigests") or []),
            "configured_user": inspect.get("Config", {}).get("User"),
        }

    return {
        "scope": str(resolved),
        "docker_containers_inspected": len(containers),
        "docker_mount_overlap": False,
        "docker_networks_inspected": len(networks),
        "candidate_subnets_checked": [str(SUBNET)],
        "selected_subnet": str(SUBNET),
        "subnet_conflicts": collisions,
        "selected_network_id": selected_network.get("Id") if selected_network else None,
        "selected_network_reused": selected_network is not None,
        "existing_owned_containers": existing_owned_containers,
        "docker_cpu_cfs_quota_supported": docker_info.get("CpuCfsQuota"),
        "docker_pids_limit_supported": docker_info.get("PidsLimit"),
        "docker_cpuset_supported": docker_info.get("CPUSet"),
        "cpu_affinity": cpu_affinity,
        "candidate_ports_checked": list(range(19520, 19530)),
        "selected_loopback_ports": list(PORTS),
        "selected_ports_available": True,
        "tmpfs_mount": {
            "path": str(tmpfs),
            "filesystem": "tmpfs",
            "total_bytes": tmpfs_total,
        },
        "memory_available_bytes": available,
        "memory_limit_plan_bytes": MEMORY_PLAN,
        "task_memory_budget_bytes": 4 * 1024**3,
        "images": images,
    }


def _labels(scope: str) -> list[str]:
    return ["--label", OWNER_LABEL + "=" + TASK, "--label", SCOPE_LABEL + "=" + scope]


def _container_by_id(container_id: str) -> dict:
    return json.loads(docker("inspect", container_id))[0]


def _check_owned(container: dict, expected_name: str, expected_image: str) -> None:
    labels = container.get("Config", {}).get("Labels") or {}
    require(
        container.get("Id") and container.get("Name", "").lstrip("/") == expected_name,
        "container_identity_mismatch",
    )
    require(
        labels.get(OWNER_LABEL) == TASK and labels.get(SCOPE_LABEL) == PROJECT,
        "container_owner_mismatch",
    )
    require(container.get("Image") == expected_image, "container_image_mismatch")
    require(
        container.get("HostConfig", {}).get("RestartPolicy", {}).get("Name") == "no",
        "container_restart_policy_mismatch",
    )
    for mount in container.get("Mounts", []):
        if mount.get("Type") == "bind":
            require(
                _overlap(Path(mount["Source"]), EXPECTED_ROOT),
                "container_mount_out_of_scope",
            )


def _stop_owned(container_id: str, name: str, image_id: str, timeout: int = 30) -> dict:
    container = _container_by_id(container_id)
    _check_owned(container, name, image_id)
    if container["State"].get("Running"):
        docker("kill", "--signal", "TERM", container_id, timeout=10)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            container = _container_by_id(container_id)
            _check_owned(container, name, image_id)
            if not container["State"].get("Running"):
                break
            time.sleep(0.25)
    container = _container_by_id(container_id)
    _check_owned(container, name, image_id)
    require(not container["State"].get("Running"), "term_exit_unconfirmed")
    require(container["State"].get("ExitCode") == 0, "container_exit_not_zero")
    require(not container["State"].get("OOMKilled"), "container_oom")
    require(container.get("RestartCount", 0) == 0, "container_restarted")
    host_config = container["HostConfig"]
    return {
        "id": container_id,
        "name": name,
        "image_id": image_id,
        "running": False,
        "exit_code": container["State"].get("ExitCode"),
        "oom_killed": container["State"].get("OOMKilled"),
        "restart_count": container.get("RestartCount", 0),
        "restart_policy": host_config.get("RestartPolicy", {}).get("Name"),
        "resource_controls": {
            "memory_limit_bytes": host_config.get("Memory"),
            "memory_swap_limit_bytes": host_config.get("MemorySwap"),
            "cpu_affinity": host_config.get("CpusetCpus"),
            "cpu_quota_micros": host_config.get("CpuQuota"),
            "pids_limit": host_config.get("PidsLimit"),
        },
    }


def _start_existing(container_id: str, name: str, image_id: str) -> None:
    container = _container_by_id(container_id)
    _check_owned(container, name, image_id)
    require(
        not container["State"].get("Running"), "container_must_be_stopped_before_start"
    )
    docker("start", container_id)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        container = _container_by_id(container_id)
        _check_owned(container, name, image_id)
        if container["State"].get("Running"):
            return
        time.sleep(0.25)
    raise ProbeError("container_start_unconfirmed")


def _base_dirs(root: Path) -> dict[str, Path]:
    tmpfs = root / "tmpfs"
    paths = {
        "config": root / "config",
        "tls": root / "tls",
        "secrets": root / "secrets",
        "evidence": root / "evidence",
        "scratch": tmpfs,
        "source": tmpfs / "source",
        "vector_source": tmpfs / "vector-source",
        "vector_data": tmpfs / "vector",
        "loki_data": tmpfs / "loki",
        "guard_data": tmpfs / "guard",
        "capacity": tmpfs / "capacity",
        "contract": root / "code/contracts/diagnostics/v1",
        "snapshot": root / "code/deploy/observability/vocabulary.json",
    }
    for name, path in paths.items():
        if name not in {
            "config",
            "tls",
            "secrets",
            "evidence",
            "scratch",
            "contract",
            "snapshot",
        }:
            path.mkdir(parents=True, exist_ok=True)
            os.chown(path, 10001, 10001)
            os.chmod(path, 0o700)
    for path in (paths["config"], paths["tls"], paths["secrets"], paths["evidence"]):
        path.mkdir(parents=True, exist_ok=True)
    return paths


def _write_loki_config(path: Path, retention: str) -> bytes:
    config = loki_config(retention_hours=48)
    config["server"].update(http_listen_address="0.0.0.0", http_listen_port=3100)
    config["ingester"].update(chunk_idle_period="5s", max_chunk_age="1m")
    config["compactor"].update(
        compaction_interval="5s",
        retention_delete_delay="1s",
        apply_retention_interval="5s",
        retention_delete_worker_count=1,
    )
    config["limits_config"]["retention_period"] = retention
    config["querier"] = {"query_store_only": True, "query_ingesters_within": "3h"}
    config["storage_config"]["tsdb_shipper"]["resync_interval"] = "5s"
    config["chunk_store_config"] = {
        "chunk_cache_config": {"embedded_cache": {"enabled": False}}
    }
    config["query_range"] = {"cache_results": False}
    data = (json.dumps(config, sort_keys=True, indent=2) + "\n").encode()
    path.write_bytes(data)
    os.chmod(path, 0o644)
    return data


def _write_vector_config(path: Path, tls: Path) -> bytes:
    config = {
        "data_dir": "/var/lib/vector",
        "api": {"enabled": True, "address": "0.0.0.0:8686"},
        "sources": {
            "a2_file": {
                "type": "file",
                "include": ["/sources/events.jsonl"],
                "read_from": "beginning",
                "ignore_checkpoints": False,
                "max_line_bytes": 4095,
                "fingerprint": {"strategy": "checksum", "lines": 1},
                "oldest_first": True,
                "glob_minimum_cooldown_ms": 250,
            },
            "internal_metrics": {"type": "internal_metrics", "scrape_interval_secs": 1},
        },
        "transforms": {
            "a2_json": {
                "type": "remap",
                "inputs": ["a2_file"],
                "source": ". = parse_json!(.message)",
            }
        },
        "sinks": {
            "loki": {
                "type": "loki",
                "inputs": ["a2_json"],
                "endpoint": "https://obs-loki:3100",
                "tenant_id": "tianshu",
                "tls": {
                    "ca_file": "/run/tls/ca.pem",
                    "crt_file": "/run/tls/client.pem",
                    "key_file": "/run/tls/client.key",
                    "verify_certificate": True,
                    "verify_hostname": True,
                },
                "labels": {"stack": "tianshu", "service": "nas-a2"},
                "encoding": {"codec": "json"},
                "acknowledgements": {"enabled": True},
                "buffer": {
                    "type": "disk",
                    "max_size": VECTOR_BUFFER_LIMIT,
                    "when_full": "block",
                },
                "batch": {"max_bytes": 262144, "timeout_secs": 1},
                "request": {
                    "timeout_secs": 3,
                    "retry_initial_backoff_secs": 1,
                    "retry_max_duration_secs": 30,
                },
                "healthcheck": {"enabled": False},
            },
            "metrics": {
                "type": "prometheus_exporter",
                "inputs": ["internal_metrics"],
                "address": "0.0.0.0:9598",
            },
        },
    }
    data = (json.dumps(config, sort_keys=True, indent=2) + "\n").encode()
    path.write_bytes(data)
    os.chmod(path, 0o644)
    return data


def _container_common(name: str, memory: int, cpu_set: str, data: Path) -> list[str]:
    return [
        "run",
        "-d",
        "--name",
        name,
        *_labels(PROJECT),
        "--restart=no",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt",
        "no-new-privileges:true",
        "--memory",
        str(memory),
        "--memory-swap",
        str(memory),
        "--cpuset-cpus",
        cpu_set,
        "--user",
        "10001:10001",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,size=16m,uid=10001,gid=10001,mode=0700",
        "--mount",
        f"type=bind,source={data},target=/var/lib/{'loki' if name == LOKI_NAME else 'vector'}",
    ]


def _write_synthetic(path: Path, rows: list[dict], append: bool = False) -> None:
    mode = "ab" if append else "xb"
    with path.open(mode) as stream:
        for row in rows:
            stream.write(canonical(row) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())


def _record(seq: int, service: str = "gateway", *, pad: bool = False) -> dict:
    row = event(seq, service=service)
    if pad:
        # Pressure-only synthetic field; never written into application source logs.
        row["a2_buffer_fixture"] = "x" * 2300
    return row


def _loki_client(paths: dict[str, Path]) -> LokiClient:
    return LokiClient(
        f"https://{LOKI_IP}:3100",
        paths["tls"] / "ca.pem",
        certificate=paths["tls"] / "client.pem",
        key=paths["tls"] / "client.key",
    )


def _guard_client(paths: dict[str, Path], role: str) -> LokiClient:
    return LokiClient(
        f"https://127.0.0.1:{GUARD_HOST_PORT}",
        paths["tls"] / "ca.pem",
        token=(paths["secrets"] / f"{role}_token").read_text().strip(),
        certificate=paths["tls"] / "client.pem",
        key=paths["tls"] / "client.key",
    )


def _wait(predicate, *, timeout: int = 120, interval: float = 0.5, code: str) -> object:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(interval)
    raise ProbeError(code)


def _loki_ready(client: LokiClient) -> bool:
    try:
        return client.request("/ready")[0] == 200
    except Exception:
        return False


def _query(
    client: LokiClient, selector: str, start_ns: int, end_ns: int
) -> list[tuple[int, str]]:
    return client.range(selector, start_ns, end_ns, limit=5000, max_requests=64)


def _prometheus_samples() -> dict[str, list[tuple[dict[str, str], float]]]:
    import urllib.request

    request = urllib.request.Request(f"http://{VECTOR_IP}:9598/metrics")
    with urllib.request.urlopen(request, timeout=5) as response:
        text = response.read(8 * 1024**2 + 1)
    require(len(text) <= 8 * 1024**2, "vector_metrics_over_budget")
    samples: dict[str, list[tuple[dict[str, str], float]]] = {}
    for line in text.decode("utf-8", "replace").splitlines():
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
                    r'([a-zA-Z_][a-zA-Z0-9_]*)="((?:\\.|[^"])*)"', match.group(2)
                )
            }
        try:
            value = float(match.group(3))
        except ValueError:
            continue
        samples.setdefault(match.group(1), []).append((labels, value))
    return samples


def _metric_sum(samples, metric: str, component: str | None = None) -> float:
    return sum(
        value
        for labels, value in samples.get(metric, [])
        if component is None or labels.get("component_id") == component
    )


def _start_guard_probe(root: Path, paths: dict[str, Path], backend: LokiClient):
    source_roots = {}
    for service in ("platform", "companion", "memory", "gateway"):
        path = paths["source"] / service
        path.mkdir(parents=True, exist_ok=True)
        source_roots[service] = str(path)
    settings = {
        "loki_url": f"https://{LOKI_IP}:3100",
        "ca": str(paths["tls"] / "ca.pem"),
        "client_cert": str(paths["tls"] / "client.pem"),
        "client_key": str(paths["tls"] / "client.key"),
        "server_cert": str(paths["tls"] / "guard.pem"),
        "server_key": str(paths["tls"] / "guard.key"),
        "ledger": str(paths["guard_data"] / "integrity.sqlite"),
        "snapshot": str(paths["snapshot"]),
        "contract": str(paths["contract"]),
        "tokens": {
            role: str(paths["secrets"] / f"{role}_token")
            for role in ("writer", "query", "metrics")
        },
        "log_roots": source_roots,
        "log_budgets": {service: 16 * 1024 for service in source_roots},
        "storage_roots": {"a2": str(paths["capacity"])},
        "reserve_bytes": 16 * 1024**2,
        "interval_seconds": 1,
        "ledger_max_events": 10000,
    }
    require(
        paths["snapshot"].is_file() and paths["contract"].is_dir(),
        "a2_contract_input_missing",
    )
    write_json(root / "config/guard-test.json", settings)
    state = State(settings, backend)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(settings["server_cert"], settings["server_key"])
    server = Server(("127.0.0.1", GUARD_HOST_PORT), state, context)
    server_thread = threading.Thread(
        target=server.serve_forever,
        kwargs={"poll_interval": 0.05},
        name="nas-a2-guard-test",
    )
    monitor_thread = threading.Thread(
        target=state.run_monitor, name="nas-a2-monitor-test"
    )
    server_started = False
    try:
        server_thread.start()
        server_started = True
        monitor_thread.start()
    except BaseException:
        state.stop.set()
        if server_started:
            server.shutdown()
            server_thread.join(timeout=5)
        server.server_close()
        if monitor_thread.ident is not None:
            monitor_thread.join(timeout=5)
        raise
    return settings, state, server, server_thread, monitor_thread


def _guard_metrics(client: LokiClient) -> dict[str, float]:
    status, body, _ = client.request("/metrics")
    require(status == 200, "guard_metrics_unavailable")
    values = {}
    for line in body.decode().splitlines():
        parts = line.split()
        if len(parts) == 2:
            try:
                values[parts[0]] = float(parts[1])
            except ValueError:
                pass
    return values


def _payload_for_loki(row: dict) -> bytes:
    timestamp = datetime.fromisoformat(
        row["timestamp"].replace("Z", "+00:00")
    ).timestamp()
    body = {
        "streams": [
            {
                "stream": {"stack": "tianshu", "service": "guard_capacity_fixture"},
                "values": [
                    [str(int(timestamp * 1_000_000_000)), canonical(row).decode()]
                ],
            }
        ]
    }
    return json.dumps(body, separators=(",", ":")).encode()


def _run_loki_retention_only(
    paths: dict[str, Path], report: dict, clients: dict, loki_id: str, image_id: str
) -> None:
    client = clients["loki"]
    client.request("/flush", "POST", b"")
    chunk_root = paths["loki_data"] / "chunks" / "tianshu"
    before_chunks = (
        {
            p.relative_to(chunk_root).as_posix()
            for p in chunk_root.rglob("*")
            if p.is_file()
        }
        if chunk_root.exists()
        else set()
    )
    ttl_ns = time.time_ns() - 25 * 3600 * 10**9
    ttl_dt = datetime.fromtimestamp(ttl_ns / 10**9, timezone.utc)
    ttl_event = _record(900001)
    ttl_event["timestamp"] = ttl_dt.isoformat().replace("+00:00", "Z")
    ttl_service = f"nas-a2-ttl-{time.time_ns()}"
    ttl_stream = f'{{stack="tianshu",service="{ttl_service}"}}'
    payload = {
        "streams": [
            {
                "stream": {"stack": "tianshu", "service": ttl_service},
                "values": [[str(ttl_ns), canonical(ttl_event).decode()]],
            }
        ]
    }
    status, _, _ = client.request(
        "/loki/api/v1/push", "POST", json.dumps(payload).encode(), "application/json"
    )
    report["dimensions"]["accelerated_ttl"] = {
        "status": "running" if status == 204 else "failed",
        "fixture_service": ttl_service,
        "fixture_age_hours": 25,
        "push_status_code": status,
    }
    require(status == 204, "ttl_fixture_push_refused")
    ttl_start, ttl_end = ttl_ns - 10**9, ttl_ns + 10**9
    _wait(
        lambda: _query(client, ttl_stream, ttl_start, ttl_end),
        timeout=60,
        code="ttl_fixture_not_visible",
    )
    client.request("/flush", "POST", b"")
    after_push_chunks = set()
    _wait(
        lambda: (
            after_push_chunks.update(
                p.relative_to(chunk_root).as_posix()
                for p in chunk_root.rglob("*")
                if p.is_file()
            )
            or bool(after_push_chunks - before_chunks)
        ),
        timeout=60,
        interval=1,
        code="ttl_chunk_not_flushed",
    )
    fixture_chunks = after_push_chunks - before_chunks
    require(bool(fixture_chunks), "ttl_fixture_chunk_untracked")
    client.close()

    lowered = _write_loki_config(paths["config"] / "loki.json", "24h")
    report["config_sha256"]["loki_after_ttl_reduction"] = sha(lowered)
    report["dimensions"]["accelerated_ttl"] = {
        "status": "running",
        "policy_before": "48h",
        "policy_after": "24h",
        "fixture_age_hours": 25,
        "fixture_service": ttl_service,
        "push_status_code": status,
        "query_path_before_and_after": "same_store_only_Loki_query_range",
        "production_default_query_path_verified": False,
        "elapsed_24_hours": False,
        "initially_visible": True,
        "initial_chunk_paths": sorted(fixture_chunks),
        "config_before_sha256": report["config_sha256"]["loki_initial"],
        "config_after_sha256": sha(lowered),
        "timeline": [],
    }
    stopped = _stop_owned(loki_id, LOKI_NAME, image_id)
    report["containers"]["loki"].setdefault("transitions", []).append(stopped)
    _start_existing(loki_id, LOKI_NAME, image_id)
    clients["loki"] = _loki_client(paths)
    _wait(lambda: _loki_ready(clients["loki"]), timeout=90, code="loki_restart_timeout")
    deadline = time.monotonic() + 180
    expired = False
    while time.monotonic() < deadline:
        rows = _query(clients["loki"], ttl_stream, ttl_start, ttl_end)
        current_chunks = (
            {
                p.relative_to(chunk_root).as_posix()
                for p in chunk_root.rglob("*")
                if p.is_file()
            }
            if chunk_root.exists()
            else set()
        )
        missing = fixture_chunks - current_chunks
        timeline = {
            "elapsed_seconds": round(180 - max(0, deadline - time.monotonic()), 2),
            "query_visible": bool(rows),
            "initial_chunks_remaining": len(fixture_chunks & current_chunks),
            "ready": _loki_ready(clients["loki"]),
        }
        report["dimensions"]["accelerated_ttl"]["timeline"].append(timeline)
        if not rows and missing == fixture_chunks and timeline["ready"]:
            expired = True
            break
        time.sleep(2)
    require(expired, "accelerated_ttl_not_observed")
    report["dimensions"]["accelerated_ttl"].update(
        status="passed",
        query_visible_after_reduction=False,
        initial_chunks_deleted=len(fixture_chunks),
        loki_ready_after_deletion=True,
    )
    clients["loki"].close()


def _run_buffer_scenario(
    root: Path,
    paths: dict[str, Path],
    image_ids: dict[str, str],
    report: dict,
    clients: dict,
    *,
    run_vector: bool,
) -> None:
    if run_vector:
        baseline = _record(1)
        source = paths["vector_source"] / "events.jsonl"
        if source.exists():
            prior = source.read_bytes().splitlines()
            require(len(prior) == 1, "existing_vector_source_unexpected")
            try:
                baseline = json.loads(prior[0])
            except (TypeError, ValueError):
                raise ProbeError("existing_vector_source_unexpected") from None
            require(
                baseline.get("service") == "gateway"
                and baseline.get("event") == "runtime.started"
                and "a2_buffer_fixture" not in baseline,
                "existing_vector_source_unexpected",
            )
        else:
            _write_synthetic(source, [baseline])
        os.chown(source, 10001, 10001)
        os.chmod(source, 0o600)
        baseline_ns = int(
            datetime.fromisoformat(
                baseline["timestamp"].replace("Z", "+00:00")
            ).timestamp()
            * 10**9
        )
        start_ns = baseline_ns - 60 * 10**9

    loki = _write_loki_config(paths["config"] / "loki.json", "48h")
    report["config_sha256"] = {"loki_initial": sha(loki)}
    if run_vector:
        vector = _write_vector_config(paths["config"] / "vector.json", paths["tls"])
        report["config_sha256"]["vector"] = sha(vector)

    net_id = report["preflight"]["selected_network_id"]
    if net_id is None:
        net_id = docker(
            "network",
            "create",
            "--driver",
            "bridge",
            "--internal",
            "--subnet",
            str(SUBNET),
            "--label",
            OWNER_LABEL + "=" + TASK,
            "--label",
            SCOPE_LABEL + "=" + PROJECT,
            NETWORK,
        ).strip()
    report["network"] = {
        "name": NETWORK,
        "id": net_id,
        "subnet": str(SUBNET),
        "internal": True,
        "reused": report["preflight"]["selected_network_reused"],
    }

    loki_args = _container_common(
        LOKI_NAME,
        MEMORY_LIMITS["loki"],
        report["preflight"]["cpu_affinity"]["loki"],
        paths["loki_data"],
    )
    loki_args.extend(
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
        loki_args.extend(
            [
                "--mount",
                f"type=bind,source={paths['tls'] / filename},target=/run/tls/{filename},readonly",
            ]
        )
    # The image's declared scratch volume is masked to avoid an anonymous persistent volume.
    loki_args.extend(
        [
            "--tmpfs",
            "/loki:rw,noexec,nosuid,size=1m,uid=10001,gid=10001,mode=0700",
            image_ids["loki"],
            "-config.file=/etc/tianshu/loki.json",
        ]
    )
    existing_loki = report["preflight"]["existing_owned_containers"].get("loki")
    if existing_loki:
        loki_id = existing_loki["id"]
        report["containers"]["loki"] = {
            "id": loki_id,
            "name": LOKI_NAME,
            "image_id": image_ids["loki"],
            "reused": True,
        }
        _start_existing(loki_id, LOKI_NAME, image_ids["loki"])
    else:
        loki_id = docker(*loki_args).strip()
        report["containers"]["loki"] = {
            "id": loki_id,
            "name": LOKI_NAME,
            "image_id": image_ids["loki"],
            "reused": False,
        }
        _check_owned(_container_by_id(loki_id), LOKI_NAME, image_ids["loki"])

    clients["loki"] = _loki_client(paths)
    _wait(lambda: _loki_ready(clients["loki"]), timeout=90, code="loki_ready_timeout")

    if not run_vector:
        existing_vector = report["preflight"]["existing_owned_containers"].get("vector")
        report["dimensions"]["vector_buffer_full"] = {
            "status": "not_run",
            "reason": "vector_minimum_disk_buffer_exceeds_bounded_tmpfs",
            "configured_test_buffer_bytes": VECTOR_BUFFER_LIMIT,
            "vector_minimum_buffer_bytes": VECTOR_MINIMUM_BUFFER_BYTES,
            "tmpfs_total_bytes": report["preflight"]["tmpfs_mount"]["total_bytes"],
            "existing_startup_failure": existing_vector is not None,
        }
        if existing_vector:
            report["containers"]["vector"] = {
                **existing_vector,
                "expected_stopped_failure": True,
            }
        _run_loki_retention_only(paths, report, clients, loki_id, image_ids["loki"])
        return

    vector_args = _container_common(
        VECTOR_NAME,
        MEMORY_LIMITS["vector"],
        report["preflight"]["cpu_affinity"]["vector"],
        paths["vector_data"],
    )
    vector_args.extend(
        [
            "--network",
            NETWORK,
            "--mount",
            f"type=bind,source={paths['config'] / 'vector.json'},target=/etc/tianshu/vector.json,readonly",
            "--mount",
            f"type=bind,source={paths['vector_source']},target=/sources,readonly",
        ]
    )
    for filename in ("ca.pem", "client.pem", "client.key"):
        vector_args.extend(
            [
                "--mount",
                f"type=bind,source={paths['tls'] / filename},target=/run/tls/{filename},readonly",
            ]
        )
    vector_args.extend([image_ids["vector"], "--config", "/etc/tianshu/vector.json"])
    vector_id = docker(*vector_args).strip()
    report["containers"]["vector"] = {
        "id": vector_id,
        "name": VECTOR_NAME,
        "image_id": image_ids["vector"],
    }
    _check_owned(_container_by_id(vector_id), VECTOR_NAME, image_ids["vector"])

    def baseline_seen():
        rows = _query(
            clients["loki"],
            '{stack="tianshu",service="nas-a2"}',
            start_ns,
            time.time_ns() + 30 * 10**9,
        )
        return (
            rows
            if any(
                json.loads(line).get("event_id") == baseline["event_id"]
                for _, line in rows
            )
            else None
        )

    _wait(baseline_seen, timeout=60, code="vector_baseline_not_delivered")
    clients["loki"].close()

    # Create substantial synthetic backlog only after the sink is disconnected.
    docker("network", "disconnect", "--force", NETWORK, loki_id)
    bulk = [_record(n + 2, pad=True) for n in range(8000)]
    _write_synthetic(source, bulk, append=True)
    os.chown(source, 10001, 10001)
    expected = [baseline, *bulk]
    source_digest = sha_file(source)
    buffer_peak = 0.0
    buffer_max = 0.0
    discarded_peak = 0.0

    def full_buffer_observed():
        nonlocal buffer_peak, buffer_max, discarded_peak
        samples = _prometheus_samples()
        size = _metric_sum(samples, "vector_buffer_size_bytes", "loki")
        maximum = _metric_sum(samples, "vector_buffer_max_size_bytes", "loki")
        discarded = _metric_sum(samples, "vector_component_discarded_events_total")
        buffer_peak = max(buffer_peak, size)
        buffer_max = max(buffer_max, maximum)
        discarded_peak = max(discarded_peak, discarded)
        return maximum > 0 and size >= maximum * 0.98

    _wait(full_buffer_observed, timeout=120, interval=1, code="vector_buffer_not_full")
    report["dimensions"]["vector_buffer_full"] = {
        "status": "passed",
        "buffer_policy": "disk/block",
        "configured_max_bytes": VECTOR_BUFFER_LIMIT,
        "observed_max_bytes": buffer_max,
        "peak_bytes": buffer_peak,
        "peak_ratio": round(buffer_peak / buffer_max, 4),
        "discarded_events_total": discarded_peak,
        "synthetic_records": len(expected),
        "source_bytes": source.stat().st_size,
        "source_sha256": source_digest,
        "tmpfs_total_bytes": shutil.disk_usage(paths["scratch"]).total,
        "actual_4gib_buffer_full": False,
        "simulated_small_buffer_boundary": True,
    }

    # Reattach the same Loki container at its previously pinned address.
    docker(
        "network", "connect", "--alias", "obs-loki", "--ip", LOKI_IP, NETWORK, loki_id
    )
    clients["loki"] = _loki_client(paths)
    _wait(
        lambda: _loki_ready(clients["loki"]), timeout=90, code="loki_reconnect_timeout"
    )

    selector = '{stack="tianshu",service="nas-a2"}'
    records = _wait(
        lambda: (
            rows
            if (
                rows := _query(
                    clients["loki"], selector, start_ns, time.time_ns() + 60 * 10**9
                )
            )
            and len(rows) >= len(expected)
            else None
        ),
        timeout=180,
        interval=2,
        code="vector_replay_incomplete",
    )
    expected_pairs = Counter((row["event_id"], digest(row)) for row in expected)
    received = []
    for _, line in records:
        try:
            row = json.loads(line)
            if "event_id" in row:
                received.append((row["event_id"], digest(row)))
        except (TypeError, ValueError):
            continue
    require(Counter(received) == expected_pairs, "vector_replay_mismatch")
    samples = _prometheus_samples()
    final_buffer = _metric_sum(samples, "vector_buffer_size_bytes", "loki")
    discards = _metric_sum(samples, "vector_component_discarded_events_total")
    require(discards == 0, "vector_discard_observed")
    require(final_buffer < buffer_max * 0.1, "vector_buffer_did_not_drain")
    report["dimensions"]["vector_buffer_full"]["recovery"] = {
        "received_records": len(received),
        "expected_records": len(expected),
        "identity_hash_set_match": True,
        "final_buffer_bytes": final_buffer,
        "discarded_events_total": discards,
        "recovered_after_loki_reconnect": True,
    }
    report["artifacts"]["vector_source_sha256"] = source_digest

    container_state = _stop_owned(vector_id, VECTOR_NAME, image_ids["vector"])
    report["containers"]["vector"].update(container_state)

    # A real 48h -> 24h retention transition on an already-flushed synthetic record.
    clients["loki"].request("/flush", "POST", b"")
    chunk_root = paths["loki_data"] / "chunks" / "tianshu"
    before_chunks = (
        {
            p.relative_to(chunk_root).as_posix()
            for p in chunk_root.rglob("*")
            if p.is_file()
        }
        if chunk_root.exists()
        else set()
    )
    ttl_ns = time.time_ns() - 25 * 3600 * 10**9
    ttl_dt = datetime.fromtimestamp(ttl_ns / 10**9, timezone.utc)
    ttl_event = _record(900001)
    ttl_event["timestamp"] = ttl_dt.isoformat().replace("+00:00", "Z")
    ttl_service = f"nas-a2-ttl-{time.time_ns()}"
    ttl_stream = f'{{stack="tianshu",service="{ttl_service}"}}'
    payload = {
        "streams": [
            {
                "stream": {"stack": "tianshu", "service": ttl_service},
                "values": [[str(ttl_ns), canonical(ttl_event).decode()]],
            }
        ]
    }
    status, _, _ = clients["loki"].request(
        "/loki/api/v1/push", "POST", json.dumps(payload).encode(), "application/json"
    )
    report["dimensions"]["accelerated_ttl"] = {
        "status": "running" if status == 204 else "failed",
        "fixture_service": ttl_service,
        "fixture_age_hours": 25,
        "push_status_code": status,
    }
    require(status == 204, "ttl_fixture_push_refused")
    ttl_start, ttl_end = ttl_ns - 10**9, ttl_ns + 10**9
    _wait(
        lambda: _query(clients["loki"], ttl_stream, ttl_start, ttl_end),
        timeout=60,
        code="ttl_fixture_not_visible",
    )
    clients["loki"].request("/flush", "POST", b"")
    after_push_chunks = set()
    _wait(
        lambda: (
            after_push_chunks.update(
                p.relative_to(chunk_root).as_posix()
                for p in chunk_root.rglob("*")
                if p.is_file()
            )
            or bool(after_push_chunks - before_chunks)
        ),
        timeout=60,
        interval=1,
        code="ttl_chunk_not_flushed",
    )
    fixture_chunks = after_push_chunks - before_chunks
    require(bool(fixture_chunks), "ttl_fixture_chunk_untracked")
    clients["loki"].close()

    lowered = _write_loki_config(paths["config"] / "loki.json", "24h")
    report["config_sha256"]["loki_after_ttl_reduction"] = sha(lowered)
    report["dimensions"]["accelerated_ttl"] = {
        "status": "running",
        "policy_before": "48h",
        "policy_after": "24h",
        "fixture_age_hours": 25,
        "fixture_service": ttl_service,
        "push_status_code": status,
        "query_path_before_and_after": "same_store_only_Loki_query_range",
        "production_default_query_path_verified": False,
        "elapsed_24_hours": False,
        "initially_visible": True,
        "initial_chunk_paths": sorted(fixture_chunks),
        "config_before_sha256": report["config_sha256"]["loki_initial"],
        "config_after_sha256": sha(lowered),
        "timeline": [],
    }
    stopped = _stop_owned(loki_id, LOKI_NAME, image_ids["loki"])
    report["containers"]["loki"].setdefault("transitions", []).append(stopped)
    _start_existing(loki_id, LOKI_NAME, image_ids["loki"])
    clients["loki"] = _loki_client(paths)
    _wait(lambda: _loki_ready(clients["loki"]), timeout=90, code="loki_restart_timeout")
    deadline = time.monotonic() + 180
    expired = False
    while time.monotonic() < deadline:
        rows = _query(clients["loki"], ttl_stream, ttl_start, ttl_end)
        current_chunks = (
            {
                p.relative_to(chunk_root).as_posix()
                for p in chunk_root.rglob("*")
                if p.is_file()
            }
            if chunk_root.exists()
            else set()
        )
        missing = fixture_chunks - current_chunks
        timeline = {
            "elapsed_seconds": round(180 - max(0, deadline - time.monotonic()), 2),
            "query_visible": bool(rows),
            "initial_chunks_remaining": len(fixture_chunks & current_chunks),
            "ready": _loki_ready(clients["loki"]),
        }
        report["dimensions"]["accelerated_ttl"]["timeline"].append(timeline)
        if not rows and missing == fixture_chunks and timeline["ready"]:
            expired = True
            break
        time.sleep(2)
    require(expired, "accelerated_ttl_not_observed")
    report["dimensions"]["accelerated_ttl"].update(
        status="passed",
        query_visible_after_reduction=False,
        initial_chunks_deleted=len(fixture_chunks),
        loki_ready_after_deletion=True,
    )
    clients["loki"].close()


def _run_guard_capacity(
    root: Path, paths: dict[str, Path], report: dict, clients: dict
) -> None:
    backend = _loki_client(paths)
    settings, state, server, server_thread, monitor_thread = _start_guard_probe(
        root, paths, backend
    )
    clients["guard_writer"] = _guard_client(paths, "writer")
    clients["guard_metrics"] = _guard_client(paths, "metrics")
    filler_file = None
    try:
        _wait(
            lambda: (
                _guard_metrics(clients["guard_metrics"]).get(
                    "tianshu_obs_monitor_success"
                )
                == 1
            ),
            timeout=45,
            code="guard_monitor_not_ready",
        )

        platform_root = paths["source"] / "platform"
        app_source = platform_root / "a2-budget-fixture.jsonl"
        instance = "00000000-0000-4000-8000-00000000a2a2"
        fixture_reused = app_source.exists()
        if fixture_reused:
            try:
                source_rows = [
                    json.loads(line) for line in app_source.read_bytes().splitlines()
                ]
            except (TypeError, ValueError):
                raise ProbeError("existing_application_fixture_unexpected") from None
            require(
                len(source_rows) == 79
                and all(
                    row.get("service") == "platform"
                    and row.get("instance_id") == instance
                    and row.get("sequence") == seq
                    for seq, row in enumerate(source_rows, 1)
                ),
                "existing_application_fixture_unexpected",
            )
        else:
            source_rows = [event(seq, "platform", instance) for seq in range(1, 80)]
            _write_synthetic(app_source, source_rows)
        app_hash_before = sha_file(app_source)
        capacities_now = capacities(
            {"platform": platform_root}, {"platform": 16 * 1024}
        )["platform"]
        require(
            capacities_now["ratio"] > 0.8, "application_budget_fixture_below_watermark"
        )

        app_alert = _wait(
            lambda: (
                metrics
                if (metrics := _guard_metrics(clients["guard_metrics"])).get(
                    "tianshu_obs_alert_application_capacity"
                )
                == 1
                and metrics.get("tianshu_obs_source_platform_ratio", 0) > 0.8
                else None
            ),
            timeout=30,
            code="application_capacity_alert_missing",
        )

        capacity_dir = paths["capacity"]
        reserve = settings["reserve_bytes"]
        before_free = shutil.disk_usage(capacity_dir).free
        require(
            before_free > reserve + 8 * 1024**2,
            "tmpfs_headroom_too_low_for_capacity_probe",
        )
        filler_path = capacity_dir / "a2-disk-watermark-fixture.bin"
        written = 0
        block = secrets.token_bytes(256 * 1024)
        with filler_path.open("xb", buffering=0) as stream:
            filler_file = filler_path
            while shutil.disk_usage(capacity_dir).free > reserve:
                require(
                    written + len(block) <= MAX_FILLER_BYTES,
                    "watermark_fill_safety_ceiling",
                )
                stream.write(block)
                written += len(block)
        at_watermark = shutil.disk_usage(capacity_dir).free
        require(at_watermark <= reserve, "watermark_not_reached")
        require(not state.space_available(), "guard_watermark_not_refusing")

        denied = _record(910001)
        denied["event_id"] = "00000000-0000-4000-8000-00000000a201"
        denied_body = _payload_for_loki(denied)
        last_success_before = state.metrics.get("last_push_success_timestamp", 0)
        status, body, _ = clients["guard_writer"].request(
            "/loki/api/v1/push", "POST", denied_body, "application/json"
        )
        require(
            status == 503 and json.loads(body).get("error") == "storage_capacity",
            "guard_capacity_refusal_mismatch",
        )
        rejected_total = state.metrics.get("push_rejected_total", 0)
        require(rejected_total == 1, "guard_capacity_rejection_counter_missing")
        require(
            state.metrics.get("last_push_success_timestamp", 0) == last_success_before,
            "guard_refusal_claimed_success",
        )

        disk_alert = _wait(
            lambda: (
                metrics
                if (metrics := _guard_metrics(clients["guard_metrics"])).get(
                    "tianshu_obs_alert_disk_capacity"
                )
                == 1
                else None
            ),
            timeout=30,
            code="disk_capacity_alert_missing",
        )
        filler_file.unlink()
        filler_file = None
        after_free = shutil.disk_usage(capacity_dir).free
        require(after_free > reserve, "watermark_space_not_recovered")
        accepted = _record(910002)
        accepted["event_id"] = "00000000-0000-4000-8000-00000000a202"
        accepted_body = _payload_for_loki(accepted)
        accepted_status, _, _ = clients["guard_writer"].request(
            "/loki/api/v1/push", "POST", accepted_body, "application/json"
        )
        require(accepted_status == 204, "guard_push_did_not_recover")
        flush_status, _, _ = backend.request("/flush", "POST", b"")
        require(flush_status in (200, 204), "guard_recovery_flush_failed")
        disk_clear = _wait(
            lambda: (
                metrics
                if (metrics := _guard_metrics(clients["guard_metrics"])).get(
                    "tianshu_obs_alert_disk_capacity"
                )
                == 0
                and metrics.get("tianshu_obs_alert_application_capacity") == 1
                else None
            ),
            timeout=30,
            code="disk_alert_recovery_or_app_alert_retention_failed",
        )
        app_hash_after = sha_file(app_source)
        require(app_hash_after == app_hash_before, "application_source_changed")
        report["dimensions"]["disk_watermark_rejection"] = {
            "status": "partial",
            "filesystem": "isolated_tmpfs",
            "tmpfs_total_bytes": shutil.disk_usage(paths["scratch"]).total,
            "reserve_bytes": reserve,
            "free_before_bytes": before_free,
            "filler_written_bytes": written,
            "free_at_refusal_bytes": at_watermark,
            "free_after_recovery_bytes": after_free,
            "guard_response_status": status,
            "guard_response_code": "storage_capacity",
            "push_rejected_total": rejected_total,
            "last_success_unchanged_on_refusal": True,
            "recovery_response_status": accepted_status,
            "recovery_flush_status": flush_status,
            "recovered_event_queryable": False,
            "recovery_query_status": "pending",
            "disk_alert_active_at_watermark": disk_alert.get(
                "tianshu_obs_alert_disk_capacity"
            )
            == 1,
            "disk_alert_cleared_after_recovery": disk_clear.get(
                "tianshu_obs_alert_disk_capacity"
            )
            == 0,
            "actual_host_volume_enospc": False,
        }
        report["dimensions"]["application_capacity_alert"] = {
            "status": "passed",
            "synthetic_application_log_bytes": app_source.stat().st_size,
            "synthetic_log_budget_bytes": settings["log_budgets"]["platform"],
            "observed_source_ratio": app_alert.get("tianshu_obs_source_platform_ratio"),
            "alert_active": app_alert.get("tianshu_obs_alert_application_capacity")
            == 1,
            "still_active_after_disk_recovery": disk_clear.get(
                "tianshu_obs_alert_application_capacity"
            )
            == 1,
            "source_sha256_before": app_hash_before,
            "source_sha256_after": app_hash_after,
            "source_preserved": True,
            "synthetic_log_fixture_reused": fixture_reused,
        }
        timestamp = datetime.fromisoformat(
            accepted["timestamp"].replace("Z", "+00:00")
        ).timestamp()
        try:
            found = _wait(
                lambda: _query(
                    backend,
                    '{stack="tianshu",service="guard_capacity_fixture"}',
                    int((timestamp - 2) * 1e9),
                    int((timestamp + 2) * 1e9),
                ),
                timeout=30,
                code="recovered_push_not_queryable",
            )
            queryable = any(
                json.loads(line).get("event_id") == accepted["event_id"]
                for _, line in found
            )
        except ProbeError as exc:
            if str(exc) != "recovered_push_not_queryable":
                raise
            queryable = False
        report["dimensions"]["disk_watermark_rejection"].update(
            status="passed" if queryable else "partial",
            recovered_event_queryable=queryable,
            recovery_query_status=(
                "event_identity_found"
                if queryable
                else "accepted_but_not_visible_after_30_seconds"
            ),
        )
    finally:
        state.stop.set()
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)
        monitor_thread.join(timeout=5)
        require(
            not server_thread.is_alive() and not monitor_thread.is_alive(),
            "guard_probe_thread_exit_unconfirmed",
        )
        for client_name in ("guard_writer", "guard_metrics"):
            if client_name in clients:
                clients[client_name].close()
        backend.close()
        fixture_cleanup_failed = False
        if filler_file is not None:
            try:
                filler_file.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                fixture_cleanup_failed = True
        require(not fixture_cleanup_failed, "capacity_fixture_cleanup_failed")


def _initial_report(root: Path) -> dict:
    return {
        "schema_version": "tianshu-nas-a2/1",
        "task": TASK,
        "project": PROJECT,
        "status": "running",
        "scope": str(root),
        "release_ready": False,
        "application_reclamation_authorized": False,
        "source_logs_deleted": False,
        "dimensions": {
            "accelerated_ttl": {"status": "not_run"},
            "disk_watermark_rejection": {"status": "not_run"},
            "vector_buffer_full": {"status": "not_run"},
            "application_capacity_alert": {"status": "not_run"},
            "application_reclamation_gate": {
                "status": "blocked",
                "reason": "no_frozen_application_log_reclamation_contract",
            },
            "physical_enospc": {"status": "not_run"},
            "production_30_day_retention": {"status": "not_run"},
        },
        "artifacts": {},
        "containers": {},
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--vector-image-id", required=True)
    parser.add_argument("--loki-image-id", required=True)
    args = parser.parse_args(argv)
    report = _initial_report(args.root)
    clients: dict[str, LokiClient] = {}
    image_ids = {"vector": args.vector_image_id, "loki": args.loki_image_id}
    root = args.root
    paths = None
    try:
        preflight_report = preflight(root, args.vector_image_id, args.loki_image_id)
        report["preflight"] = preflight_report
        paths = _base_dirs(root.resolve())
        certificates(paths["tls"], loki_ip=LOKI_IP)
        for filename in ("*.pem", "*.key"):
            for path in paths["tls"].glob(filename):
                os.chown(path, 10001, 10001)
                os.chmod(path, 0o440 if path.suffix == ".key" else 0o444)
        for role in ("writer", "query", "metrics"):
            path = paths["secrets"] / f"{role}_token"
            path.write_text(secrets.token_urlsafe(32))
            os.chmod(path, 0o600)
        contract_hashes = {}
        for path in sorted(paths["contract"].glob("*")):
            if path.is_file():
                contract_hashes[path.name] = sha_file(path)
        report["artifacts"]["contract_sha256"] = contract_hashes
        report["artifacts"]["ca_pem_sha256"] = sha_file(paths["tls"] / "ca.pem")
        code_paths = [
            PACKAGE / name
            for name in (
                "alerts.py",
                "configs.py",
                "guard.py",
                "monitor.py",
                "policy.py",
                "query.py",
                "transport.py",
                "vocabulary.json",
                "configure.py",
                "compose.py",
            )
        ] + [Path(__file__).resolve(), Path(__file__).with_name("helpers.py")]
        report["code_sha256"] = {
            str(path.relative_to(ROOT)): sha_file(path) for path in code_paths
        }

        _run_buffer_scenario(
            root.resolve(),
            paths,
            image_ids,
            report,
            clients,
            run_vector=VECTOR_BUFFER_LIMIT >= VECTOR_MINIMUM_BUFFER_BYTES,
        )
        _run_guard_capacity(root.resolve(), paths, report, clients)

        report["dimensions"]["application_reclamation_gate"] = {
            "status": "blocked",
            "reason": "no_frozen_application_log_reclamation_contract_preserve_sources_stop_admission_and_receipt_watermark",
            "source_deletion_performed": False,
        }
        report["status"] = "partial"
        report["nas_verified"] = True
    except Exception as exc:
        report["status"] = "failed"
        report["nas_verified"] = False
        report["error_code"] = (
            str(exc) if isinstance(exc, ProbeError) else "probe_failed_details_withheld"
        )
        for client in clients.values():
            try:
                client.close()
            except Exception:
                pass
    finally:
        for key, name, image in (
            ("vector", VECTOR_NAME, image_ids["vector"]),
            ("loki", LOKI_NAME, image_ids["loki"]),
        ):
            entry = report.get("containers", {}).get(key)
            if not entry or not entry.get("id"):
                continue
            if entry.get("expected_stopped_failure"):
                try:
                    container = _container_by_id(entry["id"])
                    _check_owned(container, name, image)
                    state = container.get("State", {})
                    require(
                        not state.get("Running")
                        and state.get("ExitCode") == entry.get("exit_code")
                        and not state.get("OOMKilled"),
                        "expected_vector_failure_state_changed",
                    )
                    entry["retained_stopped"] = True
                except Exception as exc:
                    report["status"] = "failed"
                    report["cleanup_error_code"] = (
                        str(exc)
                        if isinstance(exc, ProbeError)
                        else "cleanup_unconfirmed"
                    )
                continue
            try:
                stop = _stop_owned(entry["id"], name, image)
                entry.update(stop)
            except Exception as exc:
                report["status"] = "failed"
                report["cleanup_error_code"] = (
                    str(exc) if isinstance(exc, ProbeError) else "cleanup_unconfirmed"
                )
        if paths is not None:
            report["tmpfs_used_bytes_after_stop"] = shutil.disk_usage(
                paths["scratch"]
            ).used
        report["application_reclamation_authorized"] = False
        report["release_ready"] = False
        report_path = root / "evidence/nas-a2-report.json"
        try:
            report_path.parent.mkdir(parents=True, exist_ok=True)
            write_json(report_path, report)
        except Exception:
            report["status"] = "failed"
            report["error_code"] = "evidence_write_failed"
        print(
            json.dumps(
                {
                    "status": report["status"],
                    "nas_verified": report.get("nas_verified", False),
                    "report": str(report_path),
                }
            )
        )
    return 0 if report.get("nas_verified") and report["status"] == "partial" else 1


if __name__ == "__main__":
    raise SystemExit(main())
