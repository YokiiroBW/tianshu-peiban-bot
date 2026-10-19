"""Standalone loopback SSH + real control CLI + synthetic Docker fixture.

All keys, Compose files and durable state are generated under --directory.
It never uses a real Docker daemon or a configured NAS. Close stdin to stop SSH.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from helpers import ROOT, LoopbackSSH, inspect_value

from ops.infrastructure.protocol import VERSION as OBSERVATION_VERSION
from ops.infrastructure.sampling import stable_id
from ops.infrastructure.transport import CommandResult


class Fixture:
    def command(self, argv, payload=b"", **kwargs):
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(ROOT) + os.pathsep + environment.get("PYTHONPATH", "")
        result = subprocess.run(argv, input=payload, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                cwd=ROOT, env=environment, timeout=40)
        return CommandResult(stdout=result.stdout, stderr=result.stderr, exit_code=result.returncode)


def setup(directory):
    directory.mkdir(parents=True, exist_ok=False)
    stacks = directory / "stacks"
    stack = stacks / "fixture-web"
    stack.mkdir(parents=True)
    compose = stack / "compose.yaml"
    compose.write_text("name: fixture-web\nservices:\n  web:\n    image: fixture-local:latest\n    pull_policy: never\n    environment:\n      PASSWORD: synthetic-fixture-password\n", encoding="utf-8")
    (stack / ".env").write_text("TOKEN=synthetic-fixture-dotenv\n", encoding="utf-8")
    item = inspect_value(project="fixture-web")
    item[1], item[11], item[12], item[14], item[15] = "/fixture-web", str(stack), str(compose), {}, []
    daemon = directory / "synthetic-daemon.json"
    daemon.write_text(json.dumps({"containers": {item[0]: item}, "calls": []}), encoding="utf-8")
    command = [sys.executable, str(Path(__file__).with_name("control_fixture.py")), str(daemon)]
    remote = {"version": "infrastructure-control.remote.v1", "node_id": "fixture-nas", "state_dir": str(directory / "persistent-node"),
              "stacks_root": str(stacks), "docker_command": command, "docker_socket": str(directory / "synthetic.sock"),
              "compose_command": [*command, "compose"], "platform_stacks": ["fixture-web"], "protected_projects": {}, "protected_container_names": {}, "operation_timeout_seconds": 30}
    remote_path = directory / "remote-config.json"
    remote_path.write_text(json.dumps(remote), encoding="utf-8")
    return remote_path, item[0], stable_id("stack", "fixture-nas", "fixture-web")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--ready-file", type=Path, required=True)
    args = parser.parse_args()
    directory = args.directory.resolve()
    remote_path, identity, stack_id = setup(directory)
    with LoopbackSSH(directory, Fixture()) as ssh:
        observation = {"version": OBSERVATION_VERSION, "state_dir": str(directory / "sampling"), "ssh_command": [shutil.which("ssh")],
                       "nodes": {"fixture-nas": {"label": "Synthetic control fixture", "kind": "linux", "host": "127.0.0.1", "port": ssh.port, "user": "synthetic",
                           "identity_file": str(ssh.key_path), "known_hosts_file": str(ssh.known_hosts)}}}
        observation_path = directory / "observation-config.json"
        observation_path.write_text(json.dumps(observation), encoding="utf-8")
        control = {"version": "infrastructure-control.config.v1", "observation_config": str(observation_path), "state_dir": str(directory / "persistent-relay"),
                   "nodes": {"fixture-nas": {"command": [sys.executable, "-B", "-m", "ops.infrastructure.control.remote", "--config", str(remote_path)]}}}
        control_path = directory / "control-config.json"
        control_path.write_text(json.dumps(control), encoding="utf-8")
        ready = {"scope": "loopback_synthetic_docker", "control_config_path": str(control_path), "observation_config_path": str(observation_path),
                 "node_id": "fixture-nas", "container_id": identity, "stack_id": stack_id,
                 "control_command": [sys.executable, "-B", "-m", "ops.infrastructure.control", "--config", str(control_path)]}
        args.ready_file.resolve().write_text(json.dumps(ready), encoding="utf-8")
        sys.stdin.buffer.read()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
