"""Synthetic Docker/Compose subprocess for control tests; never uses a daemon."""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

import yaml


def write(path, value):
    temporary = path.with_suffix(".pending")
    temporary.write_text(json.dumps(value), encoding="utf-8")
    os.replace(temporary, path)


def main():
    path = Path(sys.argv[1])
    argv = sys.argv[2:]
    state = json.loads(path.read_text(encoding="utf-8"))
    if argv[:1] == ["--host"]:
        argv = argv[2:]
    if argv[0] == "ps":
        print("\n".join(sorted(state["containers"])))
        return 0
    if argv[0] == "inspect":
        identities = argv[argv.index("--") + 1:]
        failed = False
        for identity in identities:
            item = state["containers"].get(identity) or next((entry for entry in state["containers"].values() if entry[1].lstrip("/") == identity), None)
            if item:
                print(json.dumps(item[0] if argv[argv.index("--format") + 1] == "{{json .Id}}" else item))
            else:
                print("No such container", file=sys.stderr)
                failed = True
        return int(failed)
    if argv[:2] == ["image", "inspect"]:
        if state.get("missing_image"):
            return 1
        reference = argv[-1]
        fallback = "sha256:" + ("c" if state.get("pull_success") else "b") * 64
        identity = reference if reference.startswith("sha256:") else state.get("image_ids", {}).get(reference, fallback)
        print(identity)
        return 0
    if argv[0] in ("start", "stop", "restart"):
        action, identity = argv[0], argv[-1]
        state["calls"].append([action, identity])
        if state.get("delay"):
            write(path, state)
            time.sleep(state["delay"])
            state = json.loads(path.read_text())
        item = state["containers"].get(identity)
        if not item:
            return 1
        item[6], item[7] = ("exited", False) if action == "stop" else ("running", True)
        write(path, state)
        return 0
    if argv[0] == "compose":
        argv = argv[1:]
    directory = Path(argv[argv.index("--project-directory") + 1])
    project = argv[argv.index("--project-name") + 1]
    inputs = [argv[index + 1] for index, item in enumerate(argv) if item == "--file"]
    stdin = sys.stdin.buffer.read()
    action = next(item for item in argv if item in ("config", "up", "pull", "start", "stop", "restart"))
    raw = stdin if inputs[0] == "-" else Path(inputs[0]).read_bytes()
    value = yaml.safe_load(raw)
    env_path = Path(argv[argv.index("--env-file") + 1]) if "--env-file" in argv else directory / ".env"
    env = dict(line.split("=", 1) for line in env_path.read_text().splitlines() if "=" in line and not line.startswith("#")) if env_path.is_file() else {}
    def interpolate(item):
        if isinstance(item, str):
            return re.sub(r"\$\{([A-Z_]+)\}", lambda match: env.get(match[1], ""), item)
        if isinstance(item, list):
            return [interpolate(child) for child in item]
        if isinstance(item, dict):
            return {key: interpolate(child) for key, child in item.items()}
        return item
    value = interpolate(value)
    if len(inputs) > 1:
        extra = yaml.safe_load(stdin)
        for name, fields in extra["services"].items():
            value["services"][name].update(fields)
    if action == "config":
        if state.get("invalid_config"):
            print("synthetic validation secret must not escape", file=sys.stderr)
            return 1
        for service in value["services"].values():
            ports = []
            for port in service.get("ports", []):
                if isinstance(port, str):
                    published, target = port.rsplit(":", 1)
                    ports.append({"published": published, "target": int(target), "protocol": "tcp"})
                else:
                    ports.append(port)
            service["ports"] = ports
            volumes = []
            for volume in service.get("volumes", []):
                if isinstance(volume, str):
                    parts = volume.split(":")
                    if len(parts) >= 3 and len(parts[0]) == 1 and parts[0].isalpha():
                        parts = [parts[0] + ":" + parts[1], *parts[2:]]
                    source, target, *mode = parts
                    kind = "bind" if source.startswith(("/", ".")) or re.match(r"^[A-Z]:", source) else "volume"
                    source = str((directory / source).resolve()) if source.startswith(".") else source
                    volumes.append({"type": kind, "source": source, "target": target, "read_only": "ro" in mode})
                else:
                    volumes.append(volume)
            service["volumes"] = volumes
        print(json.dumps({**value, "name": project}))
        return 0
    state["calls"].append(["compose." + action, project])
    write(path, state)
    if action == "pull":
        tokens = argv[argv.index("pull") + 1:]
        policy, selected = None, []
        while tokens:
            token, *tokens = tokens
            if token == "--policy":
                policy, *tokens = tokens
            elif token.startswith("--policy="):
                policy = token.split("=", 1)[1]
            else:
                selected.append(token)
        selected = selected or list(value["services"])
        attempted, skipped = [], []
        for name in selected:
            service = value["services"][name]
            effective = policy or service.get("pull_policy", "")
            repository = service["image"].rsplit("/", 1)[-1]
            tag = repository.rsplit(":", 1)[1] if ":" in repository else "latest"
            local_tag = not state.get("missing_image") and "@" not in repository and tag != "latest"
            if effective in ("never", "build") or effective in ("missing", "if_not_present") and local_tag:
                skipped.append(name)
            else:
                attempted.append(name)
        invocation = {"argv": argv, "services": selected, "policy": policy, "attempted": attempted, "skipped": skipped, "pulled": []}
        state.setdefault("pull_invocations", []).append(invocation)
        write(path, state)
        if not attempted:
            return 0
        if state.get("pull_failure"):
            print("synthetic registry password should not escape", file=sys.stderr)
            return 1
        if state.get("flood"):
            while True:
                os.write(1, b"synthetic-sensitive-output" * 4096)
        for name in attempted:
            state.setdefault("image_ids", {})[value["services"][name]["image"]] = "sha256:" + "c" * 64
        invocation["pulled"] = attempted
        state["pull_success"] = True
        write(path, state)
        return 0
    if action == "up" and state.get("delay"):
        time.sleep(state["delay"])
        state = json.loads(path.read_text())
    if state.get("flood"):
        while True:
            os.write(1, b"synthetic-sensitive-output" * 4096)
    for index, (name, service) in enumerate(value["services"].items(), 1):
        existing = next((item for item in state["containers"].values() if item[9] == project and item[10] == name), None)
        if action in ("stop", "restart", "start"):
            if existing:
                existing[6], existing[7] = ("exited", False) if action == "stop" else ("running", True)
        elif action == "up":
            if state.get("partial_start") and index > 1:
                continue
            identity = existing[0] if existing else __import__("hashlib").sha256((project + name).encode()).hexdigest()
            fallback = "sha256:" + ("c" if state.get("updated") or state.get("pull_success") else "b") * 64
            reference = service["image"]
            image_id = reference if reference.startswith("sha256:") else state.get("image_ids", {}).get(reference, fallback)
            ports = {}
            for port in service.get("ports", []):
                if isinstance(port, str):
                    host, target = port.rsplit(":", 1)
                    ports[target + "/tcp"] = [{"HostIp": "127.0.0.1", "HostPort": host}]
            mounts = []
            for volume in service.get("volumes", []):
                if isinstance(volume, dict):
                    mounts.append({"Type": volume.get("type"), "Source": volume.get("source"), "Name": volume.get("source"), "Destination": volume.get("target"), "RW": not volume.get("read_only", False)})
            state["containers"][identity] = [identity, "/" + project + "-" + name, service["image"], image_id,
                "2026-10-19T00:00:00Z", "2026-10-19T00:00:00Z", "running", True, "bridge", project, name,
                str(directory), inputs[0] + (",-" if len(inputs) > 1 else ""), "False", ports, mounts, state.get("health", "none")]
    write(path, state)
    return 1 if state.get("partial_start") and action == "up" else 0


if __name__ == "__main__":
    raise SystemExit(main())
