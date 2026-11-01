"""Executor-owned connection registrations; never sourced from a web request."""

from __future__ import annotations

import hashlib
import os
import posixpath
import re
from dataclasses import dataclass
from pathlib import Path

from .protocol import Failure, NODE_ID, VERSION, bounded_string, decode_json, integer


def _absolute_local(value):
    return bounded_string(value, 4096) and bool(value) and os.path.isabs(value) and "\n" not in value and "\r" not in value


def _absolute_remote(value):
    return bounded_string(value, 4096) and value.startswith("/") and posixpath.normpath(value) == value and not any(c in value for c in "\r\n")


def _command(value):
    return isinstance(value, list) and 1 <= len(value) <= 16 and all(bounded_string(item, 4096) and bool(item) and not any(c in item for c in "\r\n") for item in value)


@dataclass(frozen=True)
class Node:
    id: str
    label: str
    kind: str
    host: str
    port: int
    user: str
    identity_file: str
    known_hosts_file: str
    docker_command: tuple[str, ...] | None = None
    docker_socket: str | None = None
    stacks_root: str | None = None

    @property
    def capabilities(self):
        result = ["node.snapshot"]
        if self.docker_command:
            result += ["docker.inventory", "docker.stats", "docker.logs"]
        if self.stacks_root:
            result.append("compose.inventory")
        return result

    @property
    def sampling_key(self):
        fields = (self.id, self.kind, self.host, self.port, self.user, self.docker_socket)
        return hashlib.sha256(repr(fields).encode()).hexdigest()


@dataclass(frozen=True)
class Config:
    state_dir: Path
    nodes: dict[str, Node]
    ssh_command: tuple[str, ...] = ("ssh",)
    max_concurrency: int = 4
    stale_after_seconds: int = 120


def parse_config(value) -> Config:
    allowed = {"version", "state_dir", "nodes", "ssh_command", "max_concurrency", "stale_after_seconds"}
    if not isinstance(value, dict) or set(value) - allowed or not {"version", "state_dir", "nodes"} <= set(value):
        raise Failure("invalid_config")
    if value["version"] != VERSION or not _absolute_local(value["state_dir"]):
        raise Failure("invalid_config")
    ssh = value.get("ssh_command", ["ssh"])
    concurrency = value.get("max_concurrency", 4)
    stale = value.get("stale_after_seconds", 120)
    registrations = value["nodes"]
    if not _command(ssh) or not integer(concurrency, 1, 16) or not integer(stale, 1, 3600):
        raise Failure("invalid_config")
    if not isinstance(registrations, dict) or len(registrations) > 1000:
        raise Failure("invalid_config")
    nodes = {}
    required = {"label", "kind", "host", "port", "user", "identity_file", "known_hosts_file"}
    for node_id, node in registrations.items():
        if not isinstance(node_id, str) or not NODE_ID.fullmatch(node_id) or not isinstance(node, dict):
            raise Failure("invalid_config")
        if not required <= set(node) or set(node) - required - {"docker", "stacks_root"}:
            raise Failure("invalid_config")
        if not bounded_string(node["label"], 128) or not node["label"] or node["kind"] not in ("linux", "synology", "openwrt"):
            raise Failure("invalid_config")
        if not isinstance(node["host"], str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9.:-]{0,252}", node["host"]):
            raise Failure("invalid_config")
        if not isinstance(node["user"], str) or not re.fullmatch(r"[a-zA-Z0-9_][a-zA-Z0-9_.-]{0,63}", node["user"]) or not integer(node["port"], 1, 65535):
            raise Failure("invalid_config")
        if not _absolute_local(node["identity_file"]) or not _absolute_local(node["known_hosts_file"]):
            raise Failure("invalid_config")
        docker = node.get("docker")
        docker_command = None
        docker_socket = None
        if docker is not None:
            if not isinstance(docker, dict) or set(docker) != {"enabled", "command", "socket_path"}:
                raise Failure("invalid_config")
            if type(docker["enabled"]) is not bool or not _command(docker["command"]) or not _absolute_remote(docker["socket_path"]):
                raise Failure("invalid_config")
            if docker["enabled"]:
                docker_command = tuple(docker["command"])
                docker_socket = docker["socket_path"]
        stacks_root = node.get("stacks_root")
        if stacks_root is not None and not _absolute_remote(stacks_root):
            raise Failure("invalid_config")
        nodes[node_id] = Node(node_id, node["label"], node["kind"], node["host"], node["port"], node["user"], node["identity_file"], node["known_hosts_file"], docker_command, docker_socket, stacks_root)
    return Config(Path(value["state_dir"]), nodes, tuple(ssh), concurrency, stale)


def load_config(path: str) -> Config:
    try:
        with open(path, "rb") as stream:
            raw = stream.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise Failure("config_too_large")
        return parse_config(decode_json(raw))
    except Failure:
        raise
    except (OSError, ValueError, UnicodeError, RecursionError):
        raise Failure("config_unavailable") from None
