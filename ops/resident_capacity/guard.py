"""Fail-closed capacity guard for the fixed TianShu resident deployment.

Only filesystem metadata and selected Docker metadata are read. No log or secret
content is opened. The systemd unit is responsible for calling ``fail-close``
when this process exits unexpectedly or misses its watchdog deadline.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import posixpath
import re
import shutil
import socket
import stat
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path


CORE = "tianshu-v2-resident"
OBS = "tianshu-v2-resident-obs"
DEPLOYMENT_ROOT = "/volume2/tianshu-v2-resident"
SERVICES = {
    CORE: frozenset({"platform", "companion", "memory", "gateway"}),
    OBS: frozenset({"obs-vector", "obs-loki", "obs-grafana", "obs-prometheus", "obs-guard"}),
}
ALL_KEYS = frozenset(f"{project}/{service}" for project, names in SERVICES.items() for service in names)
KNOWLEDGE_PROFILE = "knowledge-ten"
KNOWLEDGE_SERVICES = {**SERVICES, CORE: SERVICES[CORE] | {"knowledge"}}
KNOWLEDGE_KEYS = frozenset(
    f"{project}/{service}" for project, names in KNOWLEDGE_SERVICES.items() for service in names
)
GIB = 1024**3
DEFAULT_LIMIT = 20 * GIB
DOCKER_CALL_SECONDS = 10
DOCKER_BATCH_INSPECT_SECONDS = 15
MAX_TERM_SECONDS = 120
# Initial inventory; ten update/readbacks; ten readback/TERMs; the terminal
# wait plus one final inventory; and a 60 s allowance for state fsync/scheduling.
MAX_FAIL_CLOSE_SECONDS = (
    DOCKER_CALL_SECONDS + DOCKER_BATCH_INSPECT_SECONDS
    + 10 * 2 * DOCKER_CALL_SECONDS
    + 10 * 2 * DOCKER_CALL_SECONDS
    + MAX_TERM_SECONDS + DOCKER_CALL_SECONDS + DOCKER_BATCH_INSPECT_SECONDS
    + 60
)
ID = re.compile(r"[0-9a-f]{64}\Z")
IMAGE = re.compile(r".+@sha256:[0-9a-f]{64}\Z")
INSPECT_FORMAT = "[" + ",".join(
    "{{json " + field + "}}" for field in (
        ".Id",
        '(index .Config.Labels "com.docker.compose.project")',
        '(index .Config.Labels "com.docker.compose.service")',
        '(index .Config.Labels "com.docker.compose.project.working_dir")',
        '(index .Config.Labels "com.docker.compose.project.config_files")',
        ".Config.Image",
        ".State.Status",
        ".HostConfig.RestartPolicy.Name",
        ".Mounts",
    )
) + "]"


class Unsafe(RuntimeError):
    """A fixed-code refusal; never includes Docker stderr or file contents."""


@dataclass(frozen=True)
class Config:
    root: Path
    state_dir: Path
    docker: Path
    compose: dict
    platform_first_compose: dict
    images: dict
    binds: dict
    free_paths: tuple[Path, ...]
    min_free_bytes: int = DEFAULT_LIMIT
    max_deployment_bytes: int = DEFAULT_LIMIT
    poll_seconds: int = 5
    term_timeout_seconds: int = 120
    service_profile: str = "resident-nine"

    def signatures(self):
        services = _service_map(self.service_profile)
        return {
            f"{project}/{service}": {
                "project": project,
                "service": service,
                "workdir": (self.platform_first_compose if service == "platform" else self.compose[project])["workdir"],
                "compose_file": (self.platform_first_compose if service == "platform" else self.compose[project])["file"],
                "image": self.images[service],
                "binds": self.binds[service],
            }
            for project, names in services.items()
            for service in names
        }


def _service_map(profile: str):
    if profile == "resident-nine":
        return SERVICES
    if profile == KNOWLEDGE_PROFILE:
        return KNOWLEDGE_SERVICES
    raise Unsafe("capacity_config_invalid")


def _expected_keys(signatures: dict):
    if not isinstance(signatures, dict):
        raise Unsafe("resident_service_profile_invalid")
    keys = set(signatures)
    if keys == ALL_KEYS or keys == KNOWLEDGE_KEYS:
        return keys
    raise Unsafe("resident_service_profile_invalid")


@dataclass(frozen=True)
class Container:
    id: str
    project: str | None
    service: str | None
    workdir: str | None
    compose_file: str | None
    image: str | None
    status: str | None
    restart: str | None
    mounts: tuple[tuple[str, str, str, bool], ...]

    @classmethod
    def parse(cls, raw):
        if not isinstance(raw, list) or len(raw) != 9 or not ID.fullmatch(str(raw[0])):
            raise Unsafe("docker_metadata_invalid")
        mounts = raw[8]
        if not isinstance(mounts, list):
            raise Unsafe("docker_metadata_invalid")
        pairs = []
        for mount in mounts:
            if not isinstance(mount, dict):
                raise Unsafe("docker_metadata_invalid")
            if (
                not isinstance(mount.get("Type"), str)
                or not isinstance(mount.get("Source"), str)
                or not isinstance(mount.get("Destination"), str)
                or type(mount.get("RW")) is not bool
            ):
                raise Unsafe("docker_metadata_invalid")
            pairs.append((
                mount.get("Type"), mount.get("Source"),
                mount.get("Destination"), mount.get("RW"),
            ))
        return cls(*raw[:8], tuple(pairs))


def _within(source: str, root: str) -> bool:
    if not isinstance(source, str) or not os.path.isabs(source):
        return False
    try:
        return os.path.commonpath((os.path.normpath(source), root)) == root
    except ValueError:
        return False


def _owned(container: Container, signatures: dict, root: str) -> bool:
    signature = signatures.get(f"{container.project}/{container.service}")
    if signature is None or any(
        getattr(container, field) != signature[field]
        for field in ("project", "service", "workdir", "compose_file", "image")
    ):
        return False
    if any(kind == "bind" and type(writable) is not bool for kind, _, _, writable in container.mounts):
        return False
    actual = sorted(
        (source, target, not writable)
        for kind, source, target, writable in container.mounts if kind == "bind"
    )
    expected = sorted((item["source"], item["target"], item["read_only"]) for item in signature["binds"])
    return (
        bool(actual) and actual == expected
        and all(_within(source, root) for source, _, _ in actual)
        and all(kind in {"bind", "tmpfs"} for kind, _, _, _ in container.mounts)
    )


def classify(containers: list[Container], signatures: dict, root: str):
    owned = {}
    conflicts = []
    for container in containers:
        key = f"{container.project}/{container.service}"
        if container.project in SERVICES:
            if not _owned(container, signatures, root) or key in owned:
                conflicts.append("resident_identity_conflict")
            else:
                owned[key] = container
        elif any(kind == "bind" and _within(source, root) for kind, source, _, _ in container.mounts):
            conflicts.append("foreign_deployment_mount")
    return owned, conflicts


def assess(containers: list[Container], signatures: dict, root: str, locked: dict | None = None):
    expected = _expected_keys(signatures)
    owned, conflicts = classify(containers, signatures, root)
    if conflicts:
        raise Unsafe(conflicts[0])
    if set(owned) != expected:
        raise Unsafe("resident_ten_services_required" if expected == KNOWLEDGE_KEYS else "resident_nine_services_required")
    if locked is not None and {key: item.id for key, item in owned.items()} != locked:
        raise Unsafe("resident_container_identity_changed")
    if any(item.status != "running" for item in owned.values()):
        raise Unsafe("resident_service_not_running")
    if any(item.restart != "unless-stopped" for item in owned.values()):
        raise Unsafe("resident_restart_policy_changed")
    return owned


class Docker:
    def __init__(self, binary: str):
        self.prefix = [binary, "--host", "unix:///var/run/docker.sock"]

    def _run(self, *args, timeout=DOCKER_CALL_SECONDS):
        try:
            result = subprocess.run(
                [*self.prefix, *args], capture_output=True, timeout=timeout, check=False
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise Unsafe("docker_unavailable") from error
        if result.returncode:
            raise Unsafe("docker_command_failed")
        return result.stdout.decode("utf-8", "strict").strip()

    def snapshot(self):
        raw = self._run("ps", "-aq", "--no-trunc")
        ids = raw.splitlines() if raw else []
        if len(ids) > 4096 or any(not ID.fullmatch(item) for item in ids):
            raise Unsafe("docker_inventory_invalid")
        if not ids:
            return []
        lines = self._run("inspect", "--format", INSPECT_FORMAT, *ids, timeout=DOCKER_BATCH_INSPECT_SECONDS).splitlines()
        if len(lines) != len(ids):
            raise Unsafe("docker_inventory_changed")
        try:
            containers = [Container.parse(json.loads(line)) for line in lines]
        except (ValueError, TypeError) as error:
            raise Unsafe("docker_metadata_invalid") from error
        if {item.id for item in containers} != set(ids):
            raise Unsafe("docker_inventory_changed")
        return containers

    def inspect_one(self, container_id: str):
        if not ID.fullmatch(container_id):
            raise Unsafe("docker_id_invalid")
        try:
            return Container.parse(json.loads(self._run("inspect", "--format", INSPECT_FORMAT, container_id)))
        except (ValueError, TypeError) as error:
            raise Unsafe("docker_metadata_invalid") from error

    def disable_restart(self, container_id: str):
        if not ID.fullmatch(container_id):
            raise Unsafe("docker_id_invalid")
        self._run("update", "--restart=no", container_id)

    def terminate(self, container_id: str):
        if not ID.fullmatch(container_id):
            raise Unsafe("docker_id_invalid")
        self._run("kill", "--signal=TERM", container_id)


def _safe_path(raw, *, must_exist=True):
    path = Path(raw)
    if not path.is_absolute() or str(path) != os.path.normpath(str(path)):
        raise Unsafe("absolute_normal_path_required")
    for member in (path, *path.parents):
        try:
            if stat.S_ISLNK(member.lstat().st_mode):
                raise Unsafe("symlink_path_refused")
        except FileNotFoundError:
            continue
    if must_exist and not path.exists():
        raise Unsafe("required_path_missing")
    return path


def _validate_limits(value):
    for name, lower, upper in (
        ("min_free_bytes", DEFAULT_LIMIT, 1024 * GIB),
        ("max_deployment_bytes", GIB, DEFAULT_LIMIT),
        ("poll_seconds", 1, 10),
        ("term_timeout_seconds", 10, MAX_TERM_SECONDS),
    ):
        number = value.get(name)
        if type(number) is not int or not lower <= number <= upper:
            raise Unsafe("capacity_config_invalid")


def _compose_entry(entry, root: Path):
    if not isinstance(entry, dict) or set(entry) != {"workdir", "file"}:
        raise ValueError()
    workdir = _safe_path(entry["workdir"])
    file = _safe_path(entry["file"])
    if workdir != root or not workdir.is_dir() or not file.is_file():
        raise ValueError()
    return {"workdir": str(workdir), "file": str(file)}


def load_config(path: Path):
    path = _safe_path(path)
    if os.name != "posix" or os.geteuid() != 0:
        raise Unsafe("linux_root_required")
    mode = path.stat()
    if mode.st_uid != 0 or mode.st_mode & 0o022:
        raise Unsafe("config_owner_or_mode_invalid")
    raw = path.read_bytes()
    try:
        value = json.loads(raw)
        if not isinstance(value, dict) or set(value) not in ({
            "deployment_root", "state_dir", "docker_binary", "compose",
            "platform_first_compose", "images", "binds", "free_paths",
            "min_free_bytes", "max_deployment_bytes", "poll_seconds", "term_timeout_seconds",
        }, {
            "deployment_root", "state_dir", "docker_binary", "compose",
            "platform_first_compose", "images", "binds", "free_paths",
            "min_free_bytes", "max_deployment_bytes", "poll_seconds", "term_timeout_seconds",
            "service_profile",
        }):
            raise ValueError()
        profile = value.get("service_profile", "resident-nine")
        services = _service_map(profile)
        root = _safe_path(value["deployment_root"])
        state_dir = _safe_path(value["state_dir"], must_exist=False)
        docker = _safe_path(value["docker_binary"])
        if (
            str(root) != DEPLOYMENT_ROOT or not root.is_dir() or not docker.is_file()
            or state_dir == root or _within(str(state_dir), str(root))
        ):
            raise ValueError()
        if not isinstance(value["compose"], dict) or set(value["compose"]) != set(SERVICES):
            raise ValueError()
        compose = {}
        for project in SERVICES:
            compose[project] = _compose_entry(value["compose"][project], root)
        platform_first = _compose_entry(value["platform_first_compose"], root)
        if platform_first == compose[CORE]:
            raise ValueError()
        if not isinstance(value["free_paths"], list) or not value["free_paths"]:
            raise ValueError()
        free_paths = tuple(_safe_path(item) for item in value["free_paths"])
        if any(not item.is_dir() for item in free_paths):
            raise ValueError()
        if not isinstance(value["images"], dict) or set(value["images"]) != {
            service for names in services.values() for service in names
        }:
            raise ValueError()
        if any(not isinstance(image, str) or not IMAGE.fullmatch(image) for image in value["images"].values()):
            raise ValueError()
        if not isinstance(value["binds"], dict) or set(value["binds"]) != set(value["images"]):
            raise ValueError()
        binds = {}
        for service, entries in value["binds"].items():
            if not isinstance(entries, list) or not entries:
                raise ValueError()
            normalized = []
            for entry in entries:
                if not isinstance(entry, dict) or set(entry) != {"source", "target", "read_only"}:
                    raise ValueError()
                source = _safe_path(entry["source"])
                target = entry["target"]
                if (
                    not _within(str(source), str(root)) or not isinstance(target, str)
                    or not target.startswith("/") or posixpath.normpath(target) != target
                    or type(entry["read_only"]) is not bool
                ):
                    raise ValueError()
                normalized.append({"source": str(source), "target": target, "read_only": entry["read_only"]})
            if len({entry["target"] for entry in normalized}) != len(normalized):
                raise ValueError()
            binds[service] = normalized
        _validate_limits(value)
        config = Config(
            root, state_dir, docker, compose, platform_first, value["images"], binds,
            (root, Path(compose[CORE]["workdir"]), Path(compose[OBS]["workdir"]),
             Path(platform_first["workdir"]), *free_paths),
            value["min_free_bytes"], value["max_deployment_bytes"],
            value["poll_seconds"], value["term_timeout_seconds"],
            profile,
        )
    except (KeyError, TypeError, ValueError, OSError) as error:
        raise Unsafe("capacity_config_invalid") from error
    return config, hashlib.sha256(raw).hexdigest()


def capacity(root: Path, free_paths=(), *, max_entries=1_000_000):
    """Count logical bytes and free bytes across deployment filesystems; metadata only."""
    total, count, stack, filesystems = 0, 0, [root], {}
    while stack:
        directory = stack.pop()
        info = directory.stat(follow_symlinks=False)
        if not stat.S_ISDIR(info.st_mode):
            raise Unsafe("deployment_tree_invalid")
        filesystems.setdefault(info.st_dev, directory)
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    count += 1
                    if count > max_entries:
                        raise Unsafe("deployment_scan_limit")
                    try:
                        info = entry.stat(follow_symlinks=False)
                    except FileNotFoundError:
                        # A completed rename/removal is counted by the next pass;
                        # the free-space floor is sampled separately below.
                        continue
                    if stat.S_ISDIR(info.st_mode):
                        stack.append(Path(entry.path))
                    elif stat.S_ISREG(info.st_mode):
                        total += info.st_size
                        filesystems.setdefault(info.st_dev, Path(entry.path))
                    else:
                        raise Unsafe("deployment_tree_invalid")
        except OSError as error:
            raise Unsafe("deployment_scan_failed") from error
    try:
        free = min(shutil.disk_usage(directory).free for directory in (*filesystems.values(), *free_paths))
    except OSError as error:
        raise Unsafe("deployment_free_space_unavailable") from error
    return total, free


def _state_dir(path: Path, *, create=False):
    path = _safe_path(path, must_exist=not create)
    if create:
        path.mkdir(mode=0o700, parents=False, exist_ok=True)
    info = path.stat()
    if not path.is_dir() or info.st_mode & 0o077 or (os.name == "posix" and info.st_uid != 0):
        raise Unsafe("state_dir_owner_or_mode_invalid")
    return path


def _write(path: Path, value: dict, *, exclusive=False):
    raw = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        if exclusive:
            os.link(temporary, path)
            temporary.unlink()
        else:
            os.replace(temporary, path)
        if os.name == "posix":
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def _read_json(path: Path):
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise Unsafe("state_file_invalid")
            raw = stream.read(65537)
        if len(raw) > 65536:
            raise Unsafe("state_file_invalid")
        return json.loads(raw)
    except (OSError, ValueError, TypeError) as error:
        raise Unsafe("state_file_invalid") from error


def _armed(state_dir: Path):
    try:
        value = _read_json(state_dir / "armed.json")
        expected = _expected_keys(value["signatures"])
        if (
            not isinstance(value, dict) or set(value) != {"config_sha256", "root", "docker", "ids", "signatures", "term_timeout_seconds"}
            or set(value["ids"]) != expected
            or len(set(value["ids"].values())) != len(expected)
            or any(not ID.fullmatch(item) for item in value["ids"].values())
        ):
            raise ValueError()
        return value
    except (Unsafe, OSError, ValueError, TypeError, KeyError) as error:
        raise Unsafe("armed_marker_invalid") from error


def arm(config: Config, config_sha256: str, docker: Docker):
    state = _state_dir(config.state_dir, create=True)
    if any(state.iterdir()):
        raise Unsafe("capacity_guard_already_armed_or_latched")
    owned = assess(docker.snapshot(), config.signatures(), str(config.root))
    used, free = capacity(config.root, config.free_paths)
    if used >= config.max_deployment_bytes or free <= config.min_free_bytes:
        raise Unsafe("capacity_threshold_reached")
    marker = {
        "config_sha256": config_sha256,
        "root": str(config.root),
        "docker": str(config.docker),
        "ids": {key: item.id for key, item in owned.items()},
        "signatures": config.signatures(),
        "term_timeout_seconds": config.term_timeout_seconds,
    }
    _write(state / "armed.json", marker, exclusive=True)
    return {"status": "armed", "services": len(owned), "deployment_bytes": used, "minimum_free_bytes": free}


def sample(config: Config, marker: dict, docker: Docker):
    owned = assess(docker.snapshot(), marker["signatures"], marker["root"], marker["ids"])
    used, free = capacity(config.root, config.free_paths)
    if used >= config.max_deployment_bytes:
        raise Unsafe("deployment_budget_reached")
    if free <= config.min_free_bytes:
        raise Unsafe("host_free_floor_reached")
    return {"status": "healthy", "services": len(owned), "deployment_bytes": used, "minimum_free_bytes": free}


def fail_close(state_dir: Path, reason: str, docker: Docker | None = None):
    """Latch before effects, then disable restart, TERM, and verify exact owned IDs."""
    state = _state_dir(state_dir)
    if not (state / "armed.json").exists():
        return {"status": "unarmed", "stopped": 0}
    marker = _armed(state)
    latch_write_failed = False
    try:
        if not (state / "failure.json").exists():
            _write(state / "failure.json", {"reason": reason, "time": time.time()}, exclusive=True)
    except OSError:
        # Continue stopping even when the evidence filesystem is unavailable.
        latch_write_failed = True
    docker = docker or Docker(marker["docker"])
    errors, disabled, terminated, exited = (
        ["failure_receipt_write_failed"] if latch_write_failed else [], [], [], []
    )
    targets = set(marker["ids"].values())
    try:
        inventory = docker.snapshot()
        owned, conflicts = classify(inventory, marker["signatures"], marker["root"])
        errors.extend(conflicts)
        if any(item.id not in targets for item in owned.values()):
            errors.append("new_resident_container_after_arm")
        present = {item.id: item for item in inventory}
    except Unsafe as error:
        errors.append(error.args[0])
        present = {item: None for item in targets}
    for container_id in sorted(targets):
        if container_id not in present:
            exited.append(container_id)
            continue
        try:
            docker.disable_restart(container_id)
            current = docker.inspect_one(container_id)
            if current.id != container_id or current.restart != "no":
                raise Unsafe("restart_readback_failed")
            disabled.append(container_id)
        except Unsafe as error:
            errors.append(error.args[0])
    # No TERM is sent until every reachable locked ID had its restart policy
    # disabled and read back. A failed update remains an explicit uncertainty.
    for container_id in disabled:
        try:
            current = docker.inspect_one(container_id)
            if current.id != container_id or current.restart != "no":
                raise Unsafe("restart_readback_failed")
            if current.status == "running":
                docker.terminate(container_id)
                terminated.append(container_id)
        except Unsafe as error:
            errors.append(error.args[0])
    deadline = time.monotonic() + marker["term_timeout_seconds"]
    while time.monotonic() < deadline:
        try:
            inventory = docker.snapshot()
            owned, conflicts = classify(inventory, marker["signatures"], marker["root"])
            errors.extend(conflicts)
            present = {item.id: item for item in inventory}
            if any(
                item.id not in targets for item in owned.values()
            ):
                errors.append("new_resident_container_after_stop")
            unfinished = [
                item for item in targets
                if item in present and present[item].status not in {"exited", "dead", "created"}
            ]
            if not unfinished:
                exited = sorted(targets)
                break
        except Unsafe as error:
            errors.append(error.args[0])
            break
        time.sleep(1)
    else:
        errors.append("term_exit_timeout")
    result = {
        "status": "stopped" if not errors and len(exited) == len(targets) else "unconfirmed",
        "reason": reason,
        "target_ids": sorted(targets),
        "restart_disabled_ids": sorted(disabled),
        "term_sent_ids": sorted(terminated),
        "exited_or_absent_ids": sorted(exited),
        "errors": sorted(set(errors)),
        "time": time.time(),
    }
    try:
        _write(state / ("stop-" + uuid.uuid4().hex + ".json"), result, exclusive=True)
    except OSError:
        result["status"] = "unconfirmed"
        result["errors"].append("stop_receipt_write_failed")
    return result


def notify(message: str):
    address = os.environ.get("NOTIFY_SOCKET")
    if not address:
        raise Unsafe("systemd_watchdog_missing")
    if address.startswith("@"):
        address = "\0" + address[1:]
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as client:
        client.sendto(message.encode(), address)


def run(config: Config, config_sha256: str, docker: Docker):
    marker = _armed(_state_dir(config.state_dir))
    if marker["config_sha256"] != config_sha256:
        stopped = fail_close(config.state_dir, "capacity_config_changed_after_arm", Docker(marker["docker"]))
        return 2 if stopped["status"] == "stopped" else 3
    if (config.state_dir / "failure.json").exists():
        result = fail_close(config.state_dir, "latched_failure", docker)
        return 0 if result["status"] == "stopped" else 3
    ready = False
    while True:
        try:
            result = sample(config, marker, docker)
            _write(config.state_dir / "heartbeat.json", {**result, "time": time.time()})
            notify("WATCHDOG=1" + ("\nREADY=1" if not ready else ""))
            ready = True
        except (Unsafe, OSError) as error:
            reason = error.args[0] if isinstance(error, Unsafe) else "capacity_guard_io_failure"
            stopped = fail_close(config.state_dir, reason, docker)
            return 2 if stopped["status"] == "stopped" else 3
        time.sleep(config.poll_seconds)


def status(config: Config, config_sha256: str, docker: Docker):
    state = _state_dir(config.state_dir)
    marker = _armed(state)
    if marker["config_sha256"] != config_sha256 or (state / "failure.json").exists():
        raise Unsafe("capacity_guard_not_ready")
    try:
        heartbeat = _read_json(state / "heartbeat.json")
        age = time.time() - heartbeat["time"]
    except (OSError, ValueError, TypeError, KeyError) as error:
        raise Unsafe("capacity_heartbeat_invalid") from error
    if not 0 <= age <= max(15, config.poll_seconds * 3):
        raise Unsafe("capacity_heartbeat_stale")
    current = sample(config, marker, docker)
    return {**current, "status": "ready", "heartbeat_age_seconds": round(age, 3)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("arm", "run", "status", "fail-close"))
    parser.add_argument("--config", type=Path)
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("--reason", default="service_exit")
    args = parser.parse_args(argv)
    try:
        if os.name != "posix" or os.geteuid() != 0:
            raise Unsafe("linux_root_required")
        if args.action == "fail-close":
            if args.state_dir is None or not re.fullmatch(r"[a-z_]{1,48}", args.reason):
                raise Unsafe("fail_close_arguments_invalid")
            result = fail_close(args.state_dir, args.reason)
            print(json.dumps(result, sort_keys=True))
            return 0 if result["status"] in {"stopped", "unarmed"} else 3
        if args.config is None:
            raise Unsafe("capacity_config_required")
        config, digest = load_config(args.config)
        docker = Docker(str(config.docker))
        if args.action == "arm":
            print(json.dumps(arm(config, digest, docker), sort_keys=True))
            return 0
        if args.action == "status":
            print(json.dumps(status(config, digest, docker), sort_keys=True))
            return 0
        return run(config, digest, docker)
    except Unsafe as error:
        print(json.dumps({"status": "refused", "reason": error.args[0]}, sort_keys=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
