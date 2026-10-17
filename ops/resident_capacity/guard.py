"""Fail-closed capacity guard for the fixed TianShu resident deployment.

Only filesystem metadata and selected Docker metadata are read. No log or secret
content is opened. The systemd unit is responsible for calling ``fail-close``
when this process exits unexpectedly or misses its watchdog deadline.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
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
from dataclasses import dataclass, replace
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
DOCKER_INSPECT_BATCH_SIZE = 32
DOCKER_SNAPSHOT_SECONDS = DOCKER_CALL_SECONDS + DOCKER_BATCH_INSPECT_SECONDS
MAX_SNAPSHOT_ATTEMPTS = 2
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
# Keep the deployed resident image-ID and external-media contract. These
# adaptations predate TS-175 and are used by the accepted music deployment.
IMAGE = re.compile(r"(?:.+@)?sha256:[0-9a-f]{64}\Z")
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

    def __init__(self, reason: str, *, docker_diagnostics=()):
        super().__init__(reason)
        self.docker_diagnostics = tuple(docker_diagnostics)


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
    external_media_binds: tuple[dict, ...] = ()

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
                **({"external_media_binds": self.external_media_binds}
                   if service == "platform" and self.external_media_binds else {}),
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
    media_binds = {
        (item["source"], item["target"], item["read_only"])
        for item in signature.get("external_media_binds", ())
        if container.project == CORE and container.service == "platform"
        and item["target"].startswith("/media/")
    }
    return (
        bool(actual) and actual == expected
        and all(_within(source, root) or (source, target, read_only) in media_binds
                for source, target, read_only in actual)
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
        # Docker24 cannot update these creation fields in place. Do not retain
        # status/restart observations for unrelated containers, or cache any
        # resident project's metadata. Locked IDs are always inspected afresh.
        self._foreign_creation = {}

    def _run(
        self, *args, stage, diagnostics, batch_size=1, attempt=1,
        timeout=DOCKER_CALL_SECONDS, deadline=None,
    ):
        started = time.monotonic()
        remaining = timeout if deadline is None else min(timeout, deadline - started)
        event = {
            "stage": stage,
            "attempt": attempt,
            "batch_size": batch_size,
            "returncode": None,
            "timed_out": False,
        }

        def record(outcome):
            event["outcome"] = outcome
            event["elapsed_seconds"] = round(max(0, time.monotonic() - started), 6)
            diagnostics.append(event)

        if remaining <= 0:
            event["timed_out"] = True
            record("deadline_exhausted")
            raise Unsafe("docker_unavailable", docker_diagnostics=diagnostics)
        try:
            result = subprocess.run(
                [*self.prefix, *args], capture_output=True, timeout=remaining, check=False
            )
        except subprocess.TimeoutExpired as error:
            event["timed_out"] = True
            record("timeout")
            raise Unsafe("docker_unavailable", docker_diagnostics=diagnostics) from error
        except OSError as error:
            event["os_error_number"] = error.errno
            record("spawn_failed")
            raise Unsafe("docker_unavailable", docker_diagnostics=diagnostics) from error
        event["returncode"] = result.returncode
        if deadline is not None and time.monotonic() > deadline:
            event["timed_out"] = True
            record("deadline_exhausted")
            raise Unsafe("docker_unavailable", docker_diagnostics=diagnostics)
        if result.returncode:
            record("nonzero_exit")
            raise Unsafe("docker_command_failed", docker_diagnostics=diagnostics)
        try:
            output = result.stdout.decode("utf-8", "strict").strip()
        except UnicodeError as error:
            record("invalid_output")
            raise Unsafe("docker_metadata_invalid", docker_diagnostics=diagnostics) from error
        record("ok")
        return output

    def snapshot(self, *, diagnostics=None, deadline=None, fresh_ids=()):
        diagnostics = [] if diagnostics is None else diagnostics
        fresh_ids = set(fresh_ids)
        # A retry shares the original ps + inspect allowance, including the
        # failed attempt. Never retry an individual ID or accept partial output.
        snapshot_deadline = time.monotonic() + DOCKER_SNAPSHOT_SECONDS
        deadline = snapshot_deadline if deadline is None else min(deadline, snapshot_deadline)
        for attempt in range(1, MAX_SNAPSHOT_ATTEMPTS + 1):
            try:
                raw = self._run(
                    "ps", "-aq", "--no-trunc", stage="ps", diagnostics=diagnostics,
                    batch_size=0, attempt=attempt, deadline=deadline,
                )
                ids = raw.splitlines() if raw else []
                if (
                    len(ids) > 4096 or len(set(ids)) != len(ids)
                    or any(not ID.fullmatch(item) for item in ids)
                ):
                    raise Unsafe("docker_inventory_invalid")
                if not ids:
                    self._foreign_creation = {}
                    return []
                collected = {item: self._foreign_creation[item] for item in ids
                             if item in self._foreign_creation and item not in fresh_ids}
                needed = [item for item in ids if item not in collected]
                for offset in range(0, len(needed), DOCKER_INSPECT_BATCH_SIZE):
                    batch = needed[offset:offset + DOCKER_INSPECT_BATCH_SIZE]
                    lines = self._run(
                        "inspect", "--format", INSPECT_FORMAT, *batch,
                        stage="batch_inspect", diagnostics=diagnostics, batch_size=len(batch),
                        attempt=attempt, deadline=deadline, timeout=DOCKER_BATCH_INSPECT_SECONDS,
                    ).splitlines()
                    if len(lines) != len(batch):
                        raise Unsafe("docker_inventory_changed")
                    try:
                        current = [Container.parse(json.loads(line)) for line in lines]
                    except (ValueError, TypeError) as error:
                        raise Unsafe("docker_metadata_invalid") from error
                    if {item.id for item in current} != set(batch):
                        raise Unsafe("docker_inventory_changed")
                    collected.update((item.id, item) for item in current)
                containers = [collected[item] for item in ids]
                # Publish a new cache only after a complete, valid enumeration.
                self._foreign_creation = {
                    item.id: replace(item, status=None, restart=None) for item in containers
                    if item.project not in SERVICES and item.id not in fresh_ids
                }
                return containers
            except Unsafe as error:
                error.docker_diagnostics = tuple(diagnostics)
                # A nonzero inspect does not prove a disappearing container:
                # only a new complete inventory can establish a safe sample.
                retryable = error.args[0] == "docker_inventory_changed" or (
                    error.args[0] == "docker_command_failed"
                    and diagnostics[-1]["stage"] == "batch_inspect"
                ) or (
                    error.args[0] == "docker_unavailable" and diagnostics
                    and diagnostics[-1]["stage"] == "batch_inspect"
                    and diagnostics[-1]["outcome"] == "timeout"
                )
                if not retryable or attempt == MAX_SNAPSHOT_ATTEMPTS:
                    raise

    def inspect_one(self, container_id: str, *, diagnostics=None):
        if not ID.fullmatch(container_id):
            raise Unsafe("docker_id_invalid")
        diagnostics = [] if diagnostics is None else diagnostics
        try:
            return Container.parse(json.loads(self._run(
                "inspect", "--format", INSPECT_FORMAT, container_id,
                stage="inspect_one", diagnostics=diagnostics,
            )))
        except (ValueError, TypeError) as error:
            raise Unsafe("docker_metadata_invalid", docker_diagnostics=diagnostics) from error
        except Unsafe as error:
            error.docker_diagnostics = tuple(diagnostics)
            raise

    def disable_restart(self, container_id: str, *, diagnostics=None):
        if not ID.fullmatch(container_id):
            raise Unsafe("docker_id_invalid")
        self._run(
            "update", "--restart=no", container_id, stage="update",
            diagnostics=[] if diagnostics is None else diagnostics,
        )

    def terminate(self, container_id: str, *, diagnostics=None):
        if not ID.fullmatch(container_id):
            raise Unsafe("docker_id_invalid")
        self._run(
            "kill", "--signal=TERM", container_id, stage="term",
            diagnostics=[] if diagnostics is None else diagnostics,
        )


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


def _bind_entry(entry):
    if not isinstance(entry, dict) or set(entry) != {"source", "target", "read_only"}:
        raise ValueError()
    source = _safe_path(entry["source"])
    target = entry["target"]
    if (
        not isinstance(target, str) or not target.startswith("/")
        or posixpath.normpath(target) != target or type(entry["read_only"]) is not bool
    ):
        raise ValueError()
    return {"source": str(source), "target": target, "read_only": entry["read_only"]}


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
        required = {
            "deployment_root", "state_dir", "docker_binary", "compose",
            "platform_first_compose", "images", "binds", "free_paths",
            "min_free_bytes", "max_deployment_bytes", "poll_seconds", "term_timeout_seconds",
        }
        if (
            not isinstance(value, dict) or not required <= set(value)
            or set(value) - required - {"service_profile", "external_media_binds"}
        ):
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
        media_entries = value.get("external_media_binds", [])
        if not isinstance(media_entries, list):
            raise ValueError()
        external_media_binds = tuple(_bind_entry(entry) for entry in media_entries)
        if any(
            _within(entry["source"], str(root)) or not Path(entry["source"]).is_dir()
            or entry["source"] != raw_entry["source"] or not entry["target"].startswith("/media/")
            for entry, raw_entry in zip(external_media_binds, media_entries)
        ) or len({entry["target"] for entry in external_media_binds}) != len(external_media_binds):
            raise ValueError()
        binds = {}
        for service, entries in value["binds"].items():
            if not isinstance(entries, list) or not entries:
                raise ValueError()
            normalized = []
            for entry in entries:
                item = _bind_entry(entry)
                if (
                    not _within(item["source"], str(root))
                    and (service != "platform" or item not in external_media_binds)
                ):
                    raise ValueError()
                normalized.append(item)
            if len({entry["target"] for entry in normalized}) != len(normalized):
                raise ValueError()
            binds[service] = normalized
        if any(entry not in binds["platform"] for entry in external_media_binds):
            raise ValueError()
        _validate_limits(value)
        config = Config(
            root, state_dir, docker, compose, platform_first, value["images"], binds,
            (root, Path(compose[CORE]["workdir"]), Path(compose[OBS]["workdir"]),
             Path(platform_first["workdir"]), *free_paths,
             *(Path(entry["source"]) for entry in external_media_binds)),
            value["min_free_bytes"], value["max_deployment_bytes"],
            value["poll_seconds"], value["term_timeout_seconds"],
            profile,
            external_media_binds,
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


def _write(path: Path, value: dict, *, exclusive=False, deadline=None):
    raw = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        if deadline is not None and time.monotonic() > deadline:
            raise Unsafe("capacity_sample_timeout")
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


def _failure_value(value):
    """Validate a bounded first-cause record, including its limited diagnostics."""
    required = {"reason", "time", "docker_diagnostics"}
    if (
        not isinstance(value, dict) or set(value) != required
        or not isinstance(value["reason"], str) or not re.fullmatch(r"[a-z_]{1,48}", value["reason"])
        or type(value["time"]) not in (int, float) or not 0 < value["time"] < 2**64 or not math.isfinite(value["time"])
        or not isinstance(value["docker_diagnostics"], list)
    ):
        raise Unsafe("failure_receipt_invalid")
    fields = {"stage", "attempt", "batch_size", "returncode", "timed_out", "outcome", "elapsed_seconds"}
    for event in value["docker_diagnostics"]:
        if (
            not isinstance(event, dict) or not fields <= set(event) or set(event) - fields - {"os_error_number"}
            or not isinstance(event["stage"], str) or event["stage"] not in {"ps", "batch_inspect", "inspect_one", "update", "term"}
            or type(event["attempt"]) is not int or not 1 <= event["attempt"] <= MAX_SNAPSHOT_ATTEMPTS
            or type(event["batch_size"]) is not int or not 0 <= event["batch_size"] <= 4096
            or (event["returncode"] is not None and type(event["returncode"]) is not int)
            or type(event["timed_out"]) is not bool
            or not isinstance(event["outcome"], str) or event["outcome"] not in {"ok", "nonzero_exit", "timeout", "deadline_exhausted", "spawn_failed", "invalid_output"}
            or type(event["elapsed_seconds"]) not in (int, float)
            or not 0 <= event["elapsed_seconds"] <= MAX_FAIL_CLOSE_SECONDS or not math.isfinite(event["elapsed_seconds"])
            or (event.get("os_error_number") is not None and type(event["os_error_number"]) is not int)
        ):
            raise Unsafe("failure_receipt_invalid")
    return value


def _read_failure(path: Path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or (
        os.name == "posix" and (info.st_uid != path.parent.stat().st_uid or info.st_mode & 0o077)
    ):
        raise Unsafe("failure_receipt_owner_or_mode_invalid")
    return _failure_value(_read_json(path))


def _pending_failure(state: Path):
    """Find a completed private latch write interrupted before publication."""
    pending = []
    for path in state.glob("failure.json.*.tmp"):
        if not re.fullmatch(r"failure\.json\.[0-9a-f]{32}\.tmp", path.name):
            continue
        try:
            pending.append(_read_failure(path))
        except (Unsafe, OSError):
            # A partial write, symlink or wrong owner/mode is not evidence.
            continue
    return min(pending, key=lambda item: item["time"]) if pending else None


def _failure_present(state: Path):
    return os.path.lexists(state / "failure.json") or _pending_failure(state) is not None


def _latch_failure(state: Path, reason: str, diagnostics=()):
    """Keep the first cause even if watchdog interrupted fsync before publish."""
    destination = state / "failure.json"
    if os.path.lexists(destination):
        return _read_failure(destination), False
    pending = _pending_failure(state)
    value = pending if pending is not None else _failure_value({
        "reason": reason, "time": time.time(), "docker_diagnostics": list(diagnostics),
    })
    try:
        _write(destination, value, exclusive=True)
    except FileExistsError:
        return _read_failure(destination), False
    return value, pending is not None


def arm(config: Config, config_sha256: str, docker: Docker):
    state = _state_dir(config.state_dir, create=True)
    if any(state.iterdir()):
        raise Unsafe("capacity_guard_already_armed_or_latched")
    diagnostics = []
    try:
        owned = assess(docker.snapshot(diagnostics=diagnostics), config.signatures(), str(config.root))
        used, free = capacity(config.root, config.free_paths)
        if used >= config.max_deployment_bytes or free <= config.min_free_bytes:
            raise Unsafe("capacity_threshold_reached")
    except Unsafe as error:
        error.docker_diagnostics = tuple(diagnostics)
        raise
    marker = {
        "config_sha256": config_sha256,
        "root": str(config.root),
        "docker": str(config.docker),
        "ids": {key: item.id for key, item in owned.items()},
        "signatures": config.signatures(),
        "term_timeout_seconds": config.term_timeout_seconds,
    }
    _write(state / "armed.json", marker, exclusive=True)
    return {
        "status": "armed", "services": len(owned), "deployment_bytes": used,
        "minimum_free_bytes": free, "docker_diagnostics": diagnostics,
    }


def sample(config: Config, marker: dict, docker: Docker, *, deadline=None):
    diagnostics = []
    try:
        owned = assess(
            docker.snapshot(diagnostics=diagnostics, deadline=deadline, fresh_ids=marker["ids"].values()),
            marker["signatures"], marker["root"], marker["ids"],
        )
        used, free = capacity(config.root, config.free_paths)
        if used >= config.max_deployment_bytes:
            raise Unsafe("deployment_budget_reached")
        if free <= config.min_free_bytes:
            raise Unsafe("host_free_floor_reached")
        if deadline is not None and time.monotonic() > deadline:
            raise Unsafe("capacity_sample_timeout")
    except Unsafe as error:
        error.docker_diagnostics = tuple(diagnostics)
        raise
    return {
        "status": "healthy", "services": len(owned), "deployment_bytes": used,
        "minimum_free_bytes": free, "docker_diagnostics": diagnostics,
    }


def fail_close(state_dir: Path, reason: str, docker: Docker | None = None, *, diagnostics=()):
    """Latch before effects, then disable restart, TERM, and verify exact owned IDs."""
    state = _state_dir(state_dir)
    if not (state / "armed.json").exists():
        return {"status": "unarmed", "stopped": 0}
    marker = _armed(state)
    latch_write_failed = False
    recovered = False
    invoked_by = reason
    try:
        failure, recovered = _latch_failure(state, reason, diagnostics)
        reason, diagnostics = failure["reason"], failure["docker_diagnostics"]
    except (OSError, Unsafe):
        # Continue stopping even when the evidence filesystem is unavailable.
        latch_write_failed = True
    docker = docker or Docker(marker["docker"])
    errors, disabled, terminated, exited = (
        ["failure_receipt_write_failed"] if latch_write_failed else [], [], [], []
    )
    targets = set(marker["ids"].values())
    stop_diagnostics = []
    try:
        inventory = docker.snapshot(diagnostics=stop_diagnostics, fresh_ids=targets)
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
            docker.disable_restart(container_id, diagnostics=stop_diagnostics)
            current = docker.inspect_one(container_id, diagnostics=stop_diagnostics)
            if current.id != container_id or current.restart != "no":
                raise Unsafe("restart_readback_failed")
            disabled.append(container_id)
        except Unsafe as error:
            errors.append(error.args[0])
    # No TERM is sent until every reachable locked ID had its restart policy
    # disabled and read back. A failed update remains an explicit uncertainty.
    for container_id in disabled:
        try:
            current = docker.inspect_one(container_id, diagnostics=stop_diagnostics)
            if current.id != container_id or current.restart != "no":
                raise Unsafe("restart_readback_failed")
            if current.status == "running":
                docker.terminate(container_id, diagnostics=stop_diagnostics)
                terminated.append(container_id)
        except Unsafe as error:
            errors.append(error.args[0])
    deadline = time.monotonic() + marker["term_timeout_seconds"]
    while time.monotonic() < deadline:
        try:
            inventory = docker.snapshot(diagnostics=stop_diagnostics, fresh_ids=targets)
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
        "invoked_by": invoked_by,
        "pending_failure_recovered": recovered,
        "target_ids": sorted(targets),
        "restart_disabled_ids": sorted(disabled),
        "term_sent_ids": sorted(terminated),
        "exited_or_absent_ids": sorted(exited),
        "errors": sorted(set(errors)),
        "trigger_docker_diagnostics": list(diagnostics),
        "docker_diagnostics": stop_diagnostics,
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


def _systemd_stop_post():
    # The shipped Type=notify / WatchdogSec=30s unit supplies both variables and
    # already has ExecStopPost. A standalone CLI keeps synchronous fail-close.
    return bool(os.environ.get("NOTIFY_SOCKET")) and os.environ.get("WATCHDOG_USEC") == "30000000"


def _run_failure(config: Config, reason: str, docker: Docker, diagnostics=()):
    if _systemd_stop_post():
        # Do not consume the main process's remaining watchdog interval doing
        # up to ten update/readbacks, TERMs and a 120-second terminal wait.
        # ExecStopPost uses the armed Docker identity and the first-cause latch.
        try:
            _latch_failure(config.state_dir, reason, diagnostics)
        except (Unsafe, OSError):
            return 3  # ExecStopPost still stops, recovering any valid pending record.
        return 2
    stopped = fail_close(config.state_dir, reason, docker, diagnostics=diagnostics)
    return 2 if stopped["status"] == "stopped" else 3


def run(config: Config, config_sha256: str, docker: Docker):
    marker = _armed(_state_dir(config.state_dir))
    if marker["config_sha256"] != config_sha256:
        return _run_failure(config, "capacity_config_changed_after_arm", Docker(marker["docker"]))
    if _failure_present(config.state_dir):
        if _systemd_stop_post():
            return 0  # ExecStopPost remains the single stop writer for this run.
        result = fail_close(config.state_dir, "latched_failure", docker)
        return 0 if result["status"] == "stopped" else 3
    ready = False
    # The existing 25-second allowance leaves five seconds before the unit's
    # 30-second watchdog. Polling, the previous notify call, measurement and
    # persistence all spend this one cycle budget; a retry cannot reset it.
    sample_deadline = time.monotonic() + DOCKER_SNAPSHOT_SECONDS
    while True:
        diagnostics = []
        try:
            result = sample(config, marker, docker, deadline=sample_deadline)
            diagnostics = result["docker_diagnostics"]
            if any(item["attempt"] > 1 for item in diagnostics):
                # Keep the last completed resample after normal heartbeats
                # overwrite the current sample. One bounded private receipt.
                _write(config.state_dir / "docker-resample.json", {
                    "status": result["status"], "time": time.time(),
                    "docker_diagnostics": diagnostics,
                }, deadline=sample_deadline)
            _write(
                config.state_dir / "heartbeat.json", {**result, "time": time.time()},
                deadline=sample_deadline,
            )
            notify_started = time.monotonic()
            if notify_started > sample_deadline:
                raise Unsafe("capacity_sample_timeout")
            notify("WATCHDOG=1" + ("\nREADY=1" if not ready else ""))
            sample_deadline = notify_started + DOCKER_SNAPSHOT_SECONDS
            ready = True
        except (Unsafe, OSError) as error:
            reason = error.args[0] if isinstance(error, Unsafe) else "capacity_guard_io_failure"
            if isinstance(error, Unsafe):
                diagnostics = error.docker_diagnostics or diagnostics
            return _run_failure(config, reason, docker, diagnostics)
        time.sleep(config.poll_seconds)


def status(config: Config, config_sha256: str, docker: Docker):
    state = _state_dir(config.state_dir)
    marker = _armed(state)
    if marker["config_sha256"] != config_sha256 or _failure_present(state):
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
        print(json.dumps({
            "status": "refused", "reason": error.args[0],
            "docker_diagnostics": list(error.docker_diagnostics),
        }, sort_keys=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
