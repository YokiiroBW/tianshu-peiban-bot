"""Control references the observation registry; no duplicate SSH credentials."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..config import Config, _absolute_local, _command, load_config
from ..protocol import Failure, NODE_ID, decode_json, integer
from .protocol import PROJECT, matches


def load_json(path):
    try:
        with open(path, "rb") as stream:
            raw = stream.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise Failure("config_too_large")
        return decode_json(raw)
    except Failure:
        raise
    except (OSError, ValueError, UnicodeError, RecursionError):
        raise Failure("config_unavailable") from None


@dataclass(frozen=True)
class RelayConfig:
    observation: Config
    state_dir: Path
    commands: dict[str, tuple[str, ...]]


def load_relay(path):
    value = load_json(path)
    if not isinstance(value, dict) or set(value) != {"version", "observation_config", "state_dir", "nodes"} or value["version"] != "infrastructure-control.config.v1":
        raise Failure("invalid_config")
    if not _absolute_local(value["observation_config"]) or not _absolute_local(value["state_dir"]) or not isinstance(value["nodes"], dict):
        raise Failure("invalid_config")
    observation = load_config(value["observation_config"])
    if Path(value["state_dir"]).resolve() == observation.state_dir.resolve():
        raise Failure("control_state_must_be_persistent")
    commands = {}
    for node_id, record in value["nodes"].items():
        if node_id not in observation.nodes or not isinstance(record, dict) or set(record) != {"command"} or not _command(record["command"]):
            raise Failure("invalid_config")
        commands[node_id] = tuple(record["command"])
    return RelayConfig(observation, Path(value["state_dir"]), commands)


@dataclass(frozen=True)
class RemoteConfig:
    path: Path
    node_id: str
    state_dir: Path
    stacks_root: Path
    docker_command: tuple[str, ...]
    docker_socket: str
    compose_command: tuple[str, ...]
    platform_stacks: tuple[str, ...]
    protected_projects: dict
    protected_container_names: dict
    operation_timeout_seconds: int = 1800
    compose_path_root: Path | None = None
    protected_bind_roots: tuple[dict, ...] = ()


def load_remote(path):
    value = load_json(path)
    required = {"version", "node_id", "state_dir", "stacks_root", "docker_command", "docker_socket", "compose_command", "protected_projects", "protected_container_names"}
    if not isinstance(value, dict) or not required <= set(value) or set(value) - required - {"platform_stacks", "operation_timeout_seconds", "compose_path_root", "protected_bind_roots"}:
        raise Failure("invalid_config")
    if value["version"] != "infrastructure-control.remote.v1" or not matches(NODE_ID, value["node_id"]):
        raise Failure("invalid_config")
    for name in ("state_dir", "stacks_root", "docker_socket"):
        if not _absolute_local(value[name]):
            raise Failure("invalid_config")
    if "compose_path_root" in value and not _absolute_local(value["compose_path_root"]):
        raise Failure("invalid_config")
    if not _command(value["docker_command"]) or not _command(value["compose_command"]):
        raise Failure("invalid_config")
    timeout = value.get("operation_timeout_seconds", 1800)
    owned = value.get("platform_stacks", [])
    if not integer(timeout, 5, 3600) or not isinstance(owned, list) or len(owned) > 10000 or not all(matches(PROJECT, item) for item in owned):
        raise Failure("invalid_config")
    for key in ("protected_projects", "protected_container_names"):
        if not isinstance(value[key], dict):
            raise Failure("invalid_config")
        for name, entry in value[key].items():
            if not isinstance(name, str) or not name or not isinstance(entry, dict) or set(entry) != {"owner", "reason", "maintenance_ref"}:
                raise Failure("invalid_config")
            if not all(isinstance(entry[field], str) and 0 < len(entry[field]) <= 512 for field in ("owner", "reason")) or not (entry["maintenance_ref"] is None or isinstance(entry["maintenance_ref"], str) and len(entry["maintenance_ref"]) <= 512):
                raise Failure("invalid_config")
    protected_roots = value.get("protected_bind_roots", [])
    if not isinstance(protected_roots, list) or len(protected_roots) > 128:
        raise Failure("invalid_config")
    for entry in protected_roots:
        if not isinstance(entry, dict) or set(entry) != {"path", "owner", "reason"} or not _absolute_local(entry["path"]) or not all(isinstance(entry[field], str) and 0 < len(entry[field]) <= 128 for field in ("owner", "reason")):
            raise Failure("invalid_config")
    return RemoteConfig(Path(path).resolve(), value["node_id"], Path(value["state_dir"]), Path(value["stacks_root"]),
                        tuple(value["docker_command"]), value["docker_socket"], tuple(value["compose_command"]), tuple(owned),
                        value["protected_projects"], value["protected_container_names"], timeout,
                        Path(value["compose_path_root"]) if value.get("compose_path_root") else None, tuple(protected_roots))
