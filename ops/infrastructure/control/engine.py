"""Bounded Docker/Compose commands and selected, credential-free readback."""

from __future__ import annotations

import errno
import json
import os
import socket
from pathlib import Path

from ..config import Node
from ..compose import _image
from ..docker import DockerCollector
from ..protocol import CONTAINER_ID, Failure, decode_json, utc_now
from ..transport import run_bounded
from .files import document, dump
from .lifecycle import WRAPPER, exec_prefix, lease_active, prepare_lease


class LocalTransport:
    def __init__(self, deadline):
        self.deadline = deadline

    def command(self, argv, payload=b"", **kwargs):
        environment = {key: value for key, value in os.environ.items() if not key.startswith("COMPOSE_") and key not in ("DOCKER_HOST", "DOCKER_CONTEXT", "SSH_AUTH_SOCK")}
        environment["LC_ALL"] = "C"
        return run_bounded(argv, payload, kwargs.pop("deadline", self.deadline), env=environment, **kwargs)


def runtime(items, *, expected_services=None):
    complete = all(item["availability"] == "ok" and not item["reason_codes"] for item in items)
    containers = [{key: item[key] for key in ("id", "name", "state", "running", "health", "image_id", "reason_codes")} for item in items]
    running = [item["running"] for item in items]
    state = "absent" if not items else "unknown" if not complete else "running" if all(running) else "stopped" if not any(running) else "partial"
    if expected_services and set(expected_services) != {item["compose"]["service"] for item in items if item["compose"]}:
        state = "partial" if complete else "unknown"
    healths = {item["health"] for item in items}
    health = "unknown" if not complete or not healths else "not_configured" if healths == {"none"} else "unhealthy" if "unhealthy" in healths else "starting" if "starting" in healths else "healthy" if healths == {"healthy"} else "mixed" if "unknown" not in healths else "unknown"
    return {"state": state, "health": health, "containers": containers, "observed_at": utc_now()}


class Engine:
    def __init__(self, config, files, deadline, *, transport=None):
        self.config, self.files, self.deadline = config, files, deadline
        self.transport = transport or LocalTransport(deadline)
        self.leases = []
        self.on_lease = lambda lease: None
        node = Node(config.node_id, config.node_id, "linux", "unused.invalid", 1, "unused", "unused", "unused",
                    config.docker_command, config.docker_socket, str(config.stacks_root))
        self.docker = DockerCollector(self.transport, node)

    def inventory(self):
        items = self.docker.inspect(self.docker.identities())
        if any(item["availability"] != "ok" or item["reason_codes"] for item in items):
            raise Failure("docker_inventory_partial")
        return items

    def container(self, container_id):
        item = self.docker.inspect([container_id])[0]
        if item["availability"] != "ok" or item["id"] != container_id or item["reason_codes"]:
            raise Failure("container_identity_unavailable", "conflict")
        return item

    def protection(self, *, project=None, items=()):
        configured = self.config.protected_projects.get(project)
        for item in items:
            compose = item.get("compose")
            configured = configured or self.config.protected_container_names.get(item.get("name")) or (self.config.protected_projects.get(compose.get("project")) if compose else None)
        return {"protected": bool(configured), "owner": configured["owner"] if configured else None,
                "reason": configured["reason"] if configured else None, "maintenance_ref": configured["maintenance_ref"] if configured else None}

    def assert_unprotected(self, *, project=None, items=()):
        if self.protection(project=project, items=items)["protected"]:
            raise Failure("maintenance_owned_target", "unsupported")
        for item in items:
            if self.protected_mount_blockers(item["mounts"]):
                raise Failure("maintenance_owned_mount_target", "unsupported")

    def project_items(self, project, directory, *, inventory=None):
        items = self.inventory() if inventory is None else inventory
        matches = [item for item in items if item["compose"] and item["compose"]["project"] == project and item["compose"]["oneoff"] is not True]
        contexts = {str(self.files.context(directory)), str(directory)}
        expected_files = {str(Path(context) / self.primary(directory)) for context in contexts}
        if any(item["compose"]["working_dir"] not in contexts or not item["compose"]["config_files"] or item["compose"]["config_files"].split(",")[0] not in expected_files or any(part not in ("-", "/dev/stdin", "/proc/self/fd/0") for part in item["compose"]["config_files"].split(",")[1:]) for item in matches):
            raise Failure("compose_project_identity_conflict", "conflict")
        return matches

    @staticmethod
    def primary(directory):
        from ..compose import FILENAMES
        found = [name for name in FILENAMES if (directory / name).exists()]
        if len(found) != 1:
            raise Failure("compose_multiple_files", "unsupported")
        return found[0]

    def compose_argv(self, directory, project, *, stdin_config=False, image_override=False):
        context = self.files.context(directory)
        base = [*self.config.compose_command, "--project-directory", str(context), "--project-name", project,
                "--file", "-" if stdin_config else str(context / self.primary(directory))]
        if image_override:
            base += ["--file", "-"]
        return base

    def validate(self, directory, project, raw, *, snapshot=None):
        with self.files.validation_inputs(directory, snapshot) as staged:
            argv = self.compose_argv(directory, project, stdin_config=True)
            if staged:
                value = document(raw)
                for service in value["services"].values():
                    entries = service.get("env_file", [])
                    entries = [entries] if isinstance(entries, (str, dict)) else entries
                    if entries:
                        service["env_file"] = [dict(entry, path=str(staged / entry["path"])) if isinstance(entry, dict) else str(staged / entry) for entry in entries]
                for kind in ("secrets", "configs"):
                    for resource in value.get(kind, {}).values():
                        if isinstance(resource, dict) and resource.get("file"):
                            resource["file"] = str(staged / resource["file"])
                env_record = snapshot["files"].get(".env")
                if env_record:
                    argv += ["--env-file", str(staged / ".env")]
                raw = dump(value)
            result = self.transport.command([*argv, "config", "--format", "json"], raw, max_output=2 * 1024 * 1024)
        if result.reason:
            raise Failure("compose_validation_failed")
        if b"variable is not set" in result.stderr.lower():
            raise Failure("compose_environment_unresolved", "conflict")
        try:
            value = decode_json(result.stdout)
            services = value["services"]
            if not isinstance(services, dict) or not services or len(services) > 2000 or value.get("name", project) != project:
                raise ValueError("project/services invalid")
            ports, mounts, images = [], [], []
            for name, service in services.items():
                image = service.get("image")
                if not isinstance(name, str) or not isinstance(service, dict) or not isinstance(image, str) or _image(image)["state"] != "resolved" or service.get("build"):
                    raise ValueError("image required, no build")
                if "@" in image:
                    algorithm, _, digest = image.rsplit("@", 1)[1].partition(":")
                    if algorithm != "sha256" or not CONTAINER_ID.fullmatch(digest):
                        raise ValueError("unsupported image digest")
                images.append({"service": name, "from": image, "to": image, "pull": False, "pull_policy": service.get("pull_policy", "missing")})
                for port in service.get("ports", []):
                    if not isinstance(port, dict) or not isinstance(port.get("target"), int):
                        raise ValueError("port unresolved")
                    if port.get("published") is not None and not str(port["published"]).isdigit():
                        raise ValueError("port range unsupported")
                    ports.append({"service": name, "target": port["target"], "published": str(port["published"]) if port.get("published") is not None else None,
                                  "host_ip": port.get("host_ip"), "protocol": port.get("protocol", "tcp")})
                for mount in service.get("volumes", []):
                    if not isinstance(mount, dict):
                        raise ValueError("mount unresolved")
                    source = mount.get("source")
                    logical = self.files.context(directory)
                    if mount.get("type") == "bind" and source and logical != directory and Path(source).is_relative_to(logical):
                        source = str(directory / Path(source).relative_to(logical))
                    mounts.append({"service": name, "kind": mount.get("type"), "source": source, "target": mount.get("target"), "read_only": mount.get("read_only", False)})
            if len(ports) > 4096 or len(mounts) > 4096:
                raise ValueError("binding limit")
            return {"ports": ports, "mounts": mounts, "images": images, "services": list(services)}
        except (KeyError, TypeError, ValueError, UnicodeError, RecursionError, AttributeError):
            raise Failure("compose_normalized_config_invalid") from None

    def protected_mount_blockers(self, mounts):
        blockers = []
        for mount in mounts:
            if mount["kind"] != "bind" or not isinstance(mount["source"], str):
                continue
            source = Path(mount["source"]).resolve(strict=False)
            for registered in self.config.protected_bind_roots:
                root = Path(registered["path"]).resolve(strict=False)
                if source.is_relative_to(root) or root.is_relative_to(source):
                    # Fixed codes name the ownership; raw secrets and command
                    # output never enter this explanation.
                    blockers.extend(["protected_bind_root", registered["reason"]])
        return list(dict.fromkeys(blockers))

    def port_blockers(self, ports, project, directory, *, inventory=None, probe_host=True):
        all_items = self.inventory() if inventory is None else inventory
        own = {item["id"] for item in self.project_items(project, directory, inventory=all_items)} if directory.exists() else set()
        blockers = []
        declared = set()
        for port in ports:
            if port["published"] is None:
                continue
            number = int(port["published"])
            address = port["host_ip"] or "0.0.0.0"
            protocol = port["protocol"]
            key = (address, number, protocol)
            if not 1 <= number <= 65535:
                blockers.append("published_port_invalid")
                continue
            if any(existing_number == number and existing_protocol == protocol and (address in ("0.0.0.0", "::") or existing_address in ("0.0.0.0", "::") or existing_address == address) for existing_address, existing_number, existing_protocol in declared):
                blockers.append("published_port_duplicate")
            declared.add(key)
            occupied_own = False
            for item in all_items:
                if not item["running"]:
                    continue
                for binding in item["ports"]:
                    overlap = address in ("0.0.0.0", "::") or binding["host_ip"] in (None, "0.0.0.0", "::") or address == binding["host_ip"]
                    if overlap and binding["host_port"] == number and binding["protocol"] == protocol:
                        if item["id"] in own:
                            occupied_own = True
                        else:
                            blockers.append("published_port_in_use")
            if probe_host and not occupied_own and protocol in ("tcp", "udp"):
                sock = socket.socket(socket.AF_INET6 if ":" in address else socket.AF_INET, socket.SOCK_STREAM if protocol == "tcp" else socket.SOCK_DGRAM)
                try:
                    sock.bind((address, number))
                except OSError as error:
                    if error.errno == errno.EADDRINUSE:
                        blockers.append("published_port_in_use")
                    elif error.errno == errno.EADDRNOTAVAIL:
                        blockers.append("published_port_host_unavailable")
                    else:
                        blockers.append("published_port_unverifiable")
                finally:
                    sock.close()
        return list(dict.fromkeys(blockers))

    def mutate_container(self, container_id, action):
        return self.transport.command([*self.docker.command, action.split(".")[1], "--", container_id], max_output=65536)

    def pull(self, directory, project, raw, service_names):
        return self.compose_run(directory, [*self.compose_argv(directory, project, stdin_config=True), "pull", "--policy", "always", *service_names], raw)

    def available_image(self, reference):
        result = self.transport.command([*self.docker.command, "image", "inspect", "--format", "{{.Id}}", "--", reference], max_output=4096)
        identity = result.stdout.decode("ascii", errors="replace").strip()
        return identity if not result.reason and identity.startswith("sha256:") and CONTAINER_ID.fullmatch(identity[7:]) else None

    def resolve_images(self, images, service_names):
        resolved = {}
        for image in images:
            if image["service"] not in service_names:
                continue
            identity = self.available_image(image["to"])
            if identity is None:
                raise Failure("image_identity_unavailable")
            resolved[image["service"]] = identity
        return resolved

    def mutate_stack(self, directory, project, action, *, image_ids=None, mounts=()):
        services = {name: {"image": image} for name, image in (image_ids or {}).items()}
        for mount in mounts:
            if mount["kind"] == "bind":
                entry = {"type": "bind", "source": mount["source"], "target": mount["target"], "read_only": mount["read_only"]}
                services.setdefault(mount["service"], {}).setdefault("volumes", []).append(entry)
        override = dump({"services": services}) if services else b""
        argv = self.compose_argv(directory, project, image_override=bool(services))
        if action == "stack.apply":
            argv += ["up", "--detach", "--no-build", "--pull", "never"]
        elif action == "stack.pull":
            argv += ["pull"]
        else:
            argv += [action.split(".")[1]]
        return self.compose_run(directory, argv, override)

    def compose_run(self, directory, argv, payload):
        prefix = exec_prefix(self.config.compose_command)
        if prefix is None:
            return self.transport.command(argv, payload, max_output=131072)
        identity = self.transport.command([*self.docker.command, "inspect", "--type", "container", "--format", "{{json .Id}}", "--", prefix[0][-1]], max_output=4096)
        try:
            container_id = decode_json(identity.stdout)
            if identity.reason or not isinstance(container_id, str) or not CONTAINER_ID.fullmatch(container_id):
                raise ValueError("exec container identity")
        except (ValueError, UnicodeError, TypeError):
            raise Failure("compose_exec_identity_unavailable") from None
        lease = prepare_lease(self.files, directory, self.config.compose_command, container_id)
        self.leases.append(lease)
        # The lease must reach the durable operation journal before docker exec
        # can be sent; a missing marker after a disconnect remains unknown.
        self.on_lease(lease)
        inner = argv[len(prefix[0]):]
        wrapped = [*lease["prefix"], "node", "-e", WRAPPER, lease["context_path"], str(max(.1, self.deadline.remaining)), json.dumps(inner)]
        return self.transport.command(wrapped, payload, max_output=131072)

    def execution_active(self, leases=None):
        states = [lease_active(lease, self.transport) for lease in (self.leases if leases is None else leases)]
        return True if True in states else None if None in states else False

    @staticmethod
    def saved_images(items):
        images = {}
        for item in items:
            if item["compose"] and item["image_id"]:
                name = item["compose"]["service"]
                if name in images and images[name] != item["image_id"]:
                    raise Failure("service_image_identity_conflict", "conflict")
                images[name] = item["image_id"]
        return images
