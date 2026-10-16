"""Actual Docker identity/labels, Engine counters, and bounded recent CLI logs."""

from __future__ import annotations

import http.client
import io
import re
import shlex
import time

from .linux import counter, text
from .paging import page
from .protocol import CONTAINER_ID, Failure, MAX_INVENTORY, decode_json
from .sampling import sample_condition, save_sample
from .transport import parse_sections, section_script

# Selected metadata only: Config.Env, arbitrary labels, health output and command
# strings never enter the process response or the inspect transport payload.
INSPECT_FIELDS = (
    ".Id", ".Name", ".Config.Image", ".Image", ".Created", ".State.StartedAt",
    ".State.Status", ".State.Running", ".HostConfig.NetworkMode",
    '(index .Config.Labels "com.docker.compose.project")',
    '(index .Config.Labels "com.docker.compose.service")',
    '(index .Config.Labels "com.docker.compose.project.working_dir")',
    '(index .Config.Labels "com.docker.compose.project.config_files")',
    '(index .Config.Labels "com.docker.compose.oneoff")',
    ".NetworkSettings.Ports", ".Mounts",
)
INSPECT_FORMAT = "[" + ",".join("{{json " + field + "}}" for field in INSPECT_FIELDS) + ',{{if .State.Health}}{{json .State.Health.Status}}{{else}}"none"{{end}}]'


def unavailable_container(identity, reason):
    return {"id": identity, "name": None, "image_reference": None, "image_id": None,
            "created_at": None, "started_at": None, "state": "unknown", "running": None,
            "health": "unknown", "network_mode": None, "compose": None, "ports": [],
            "mounts": [], "availability": "unavailable", "reason_codes": [reason]}


def parse_container(value):
    if not isinstance(value, list) or len(value) != 17 or not isinstance(value[0], str) or not CONTAINER_ID.fullmatch(value[0]):
        raise ValueError("invalid container identity")
    identity, name, image, image_id, created, started, state, running, network, project, service, workdir, files, oneoff, raw_ports, raw_mounts, health = value
    item = unavailable_container(identity, "inspect_invalid")
    item.update(name=text(name.lstrip("/") if isinstance(name, str) else None),
                image_reference=text(image), image_id=text(image_id), created_at=text(created),
                started_at=text(started), state=state if state in ("created", "restarting", "running", "removing", "paused", "exited", "dead") else "unknown",
                running=running if type(running) is bool else None,
                health=health if health in ("none", "healthy", "unhealthy", "starting") else "unknown",
                network_mode=text(network), availability="ok", reason_codes=[])
    item["compose"] = {"project": text(project), "service": text(service), "working_dir": text(workdir, 4096),
                       "config_files": text(files, 4096), "oneoff": oneoff.lower() == "true" if isinstance(oneoff, str) and oneoff.lower() in ("true", "false") else None} if project or service else None
    if item["state"] == "unknown" or item["running"] is None or item["health"] == "unknown":
        item["reason_codes"].append("container_state_unknown")
    if not isinstance(raw_ports, dict) and raw_ports is not None:
        item["reason_codes"].append("ports_unavailable")
    for key, bindings in (raw_ports or {}).items() if isinstance(raw_ports, dict) else []:
        match = re.fullmatch(r"(\d+)/(tcp|udp|sctp)", key)
        if not match or not 1 <= int(match[1]) <= 65535 or not (bindings is None or isinstance(bindings, list)):
            item["reason_codes"].append("ports_invalid")
            continue
        for binding in bindings if bindings else [None]:
            host_ip = None
            host_port = None
            if binding is not None:
                try:
                    host_ip = text(binding.get("HostIp"))
                    host_port = int(binding.get("HostPort"))
                    if not 1 <= host_port <= 65535:
                        raise ValueError("port range")
                except (ValueError, TypeError, AttributeError):
                    item["reason_codes"].append("ports_invalid")
                    continue
            item["ports"].append({"container_port": int(match[1]), "protocol": match[2], "host_ip": host_ip, "host_port": host_port})
    if not isinstance(raw_mounts, list):
        item["reason_codes"].append("mounts_unavailable")
    else:
        for mount in raw_mounts:
            if not isinstance(mount, dict) or not text(mount.get("Type")) or not text(mount.get("Destination"), 4096):
                item["reason_codes"].append("mounts_invalid")
                continue
            kind = mount["Type"]
            source = mount.get("Name") if kind == "volume" else mount.get("Source")
            item["mounts"].append({"kind": kind, "source": text(source, 4096), "target": mount["Destination"],
                                   "read_only": not mount["RW"] if type(mount.get("RW")) is bool else None})
    if len(item["mounts"]) > 1024 or len(item["ports"]) > 1024:
        item["reason_codes"].append("container_metadata_limit_reached")
        item["mounts"] = item["mounts"][:1024]
        item["ports"] = item["ports"][:1024]
    item["reason_codes"] = list(dict.fromkeys(item["reason_codes"]))
    return item


def _optional_counter(value):
    try:
        return counter(value)
    except ValueError:
        return None


def project_stats(raw, item, previous, now, stale_after):
    identity = item["id"]
    if not isinstance(raw, dict) or raw.get("id") != identity:
        raise ValueError("stats identity mismatch")
    reasons = []
    cpu_stats = raw.get("cpu_stats") or {}
    usage = cpu_stats.get("cpu_usage") or {}
    total = _optional_counter(usage.get("total_usage"))
    system = _optional_counter(cpu_stats.get("system_cpu_usage"))
    online = cpu_stats.get("online_cpus")
    if online is None and isinstance(usage.get("percpu_usage"), list):
        online = len(usage["percpu_usage"])
    online = online if type(online) is int and 1 <= online <= 65536 else None
    cpu = {"usage_ratio": None, "basis": "one_core", "total_usage_nanoseconds": total,
           "system_usage_nanoseconds": system, "online_cpus": online, "state": "unavailable",
           "reason_codes": ["cpu_counters_unavailable"], "source": "docker_engine_stats"}
    sample = None
    if total is not None and system is not None and online:
        sample_identity = [identity, item["started_at"], online]
        state, delta = sample_condition(previous, [total, system], now, stale_after, identity=sample_identity)
        if delta:
            if delta[1][1] > 0:
                cpu["usage_ratio"] = delta[1][0] / delta[1][1] * online
            else:
                state = "unavailable"
        cpu["state"] = state
        cpu["reason_codes"] = [] if state == "ok" else [state]
        sample = save_sample([total, system], now, sample_identity)
    else:
        reasons.append("cpu_counters_unavailable")
    memory_stats = raw.get("memory_stats") or {}
    memory = {"raw_usage_bytes": _optional_counter(memory_stats.get("usage")), "limit_bytes": _optional_counter(memory_stats.get("limit")),
              "cache_bytes": None, "cache_source": None, "cache_adjusted_usage_bytes": None, "source": "docker_engine_stats"}
    cache_stats = memory_stats.get("stats") or {}
    for key in ("total_inactive_file", "inactive_file", "cache"):
        value = _optional_counter(cache_stats.get(key))
        if value is not None:
            memory["cache_bytes"], memory["cache_source"] = value, key
            break
    if memory["raw_usage_bytes"] is None or memory["limit_bytes"] is None:
        reasons.append("memory_counters_unavailable")
    if memory["raw_usage_bytes"] is not None and memory["cache_bytes"] is not None:
        if memory["cache_bytes"] <= memory["raw_usage_bytes"]:
            memory["cache_adjusted_usage_bytes"] = memory["raw_usage_bytes"] - memory["cache_bytes"]
        else:
            reasons.append("memory_cache_invalid")
    else:
        reasons.append("memory_cache_unavailable")
    io_stats = raw.get("blkio_stats") or {}
    entries = io_stats.get("io_service_bytes_recursive")
    block_io = {"read_bytes": None, "write_bytes": None, "source": "docker_engine_stats"}
    if isinstance(entries, list):
        totals = {"read": 0, "write": 0}
        for entry in entries:
            if not isinstance(entry, dict):
                reasons.append("block_io_invalid")
                continue
            op = str(entry.get("op", "")).lower()
            if op in totals:
                value = _optional_counter(entry.get("value"))
                if value is None or _optional_counter(totals[op] + value) is None:
                    reasons.append("block_io_invalid")
                else:
                    totals[op] += value
        if "block_io_invalid" not in reasons:
            block_io.update(read_bytes=totals["read"], write_bytes=totals["write"])
    else:
        reasons.append("block_io_unavailable")
    mode = item["network_mode"] or ""
    unattributable = mode == "host" or mode.startswith("container:")
    network = {"receive_bytes": None, "transmit_bytes": None,
               "attribution": "not_attributable" if unattributable else "unknown", "source": "docker_engine_stats"}
    networks = raw.get("networks")
    if not unattributable:
        if isinstance(networks, dict) and networks:
            try:
                received = counter(sum(counter(entry["rx_bytes"]) for entry in networks.values()))
                sent = counter(sum(counter(entry["tx_bytes"]) for entry in networks.values()))
                network.update(receive_bytes=received, transmit_bytes=sent, attribution="container")
            except (ValueError, TypeError, KeyError):
                reasons.append("network_counters_invalid")
        else:
            reasons.append("network_counters_unavailable")
    return {"id": identity, "availability": "ok", "reason_codes": list(dict.fromkeys(reasons)),
            "sampled_at": text(raw.get("read"), 64), "cpu": cpu, "memory": memory,
            "block_io": block_io, "network": network}, sample


class _MemorySocket:
    def __init__(self, raw):
        self.raw = raw

    def makefile(self, _):
        return io.BytesIO(self.raw)


def parse_http(raw):
    try:
        response = http.client.HTTPResponse(_MemorySocket(raw))
        response.begin()
        body = response.read(512 * 1024 + 1)
        if len(body) > 512 * 1024:
            raise ValueError("HTTP body limit")
        return response.status, decode_json(body)
    except (ValueError, UnicodeError, http.client.HTTPException, OSError, RecursionError):
        raise Failure("docker_engine_response_invalid") from None


class DockerCollector:
    def __init__(self, transport, node):
        self.transport = transport
        self.node = node
        if not node.docker_command:
            raise Failure("docker_not_configured", "unsupported")
        self.command = [*node.docker_command, "--host", "unix://" + node.docker_socket]

    def identities(self):
        result = self.transport.command([*self.command, "ps", "-a", "--no-trunc", "--format", "{{.ID}}"], max_output=1024 * 1024)
        if result.reason:
            raise Failure(result.reason)
        try:
            ids = result.stdout.decode("ascii").splitlines()
        except UnicodeError:
            raise Failure("docker_inventory_invalid") from None
        if any(not CONTAINER_ID.fullmatch(identity) for identity in ids) or len(set(ids)) != len(ids):
            raise Failure("docker_inventory_invalid")
        if len(ids) > MAX_INVENTORY:
            raise Failure("inventory_limit_exceeded")
        return sorted(ids)

    def inspect(self, identities):
        collected = {}
        for start in range(0, len(identities), 32):
            batch = identities[start:start + 32]
            if not self.transport.deadline.remaining:
                for identity in identities[start:]:
                    collected[identity] = unavailable_container(identity, "deadline_exceeded")
                break
            result = self.transport.command([*self.command, "inspect", "--type", "container", "--format", INSPECT_FORMAT, "--", *batch])
            for line in result.stdout.splitlines():
                try:
                    item = parse_container(decode_json(line))
                    if item["id"] in batch and item["id"] not in collected:
                        collected[item["id"]] = item
                except (ValueError, TypeError, UnicodeError, RecursionError):
                    continue
            missing = [identity for identity in batch if identity not in collected]
            vanished = b"no such" in result.stderr.lower()
            if result.reason and not vanished:
                for identity in batch:
                    if identity in collected:
                        collected[identity]["reason_codes"].append(result.reason)
            for identity in missing:
                reason = result.reason or "inspect_response_invalid"
                if vanished and len(batch) > 1 and self.transport.deadline.remaining:
                    retry = self.transport.command([*self.command, "inspect", "--type", "container", "--format", INSPECT_FORMAT, "--", identity])
                    try:
                        item = parse_container(decode_json(retry.stdout.strip()))
                        if item["id"] == identity and not retry.reason:
                            collected[identity] = item
                            continue
                    except (ValueError, TypeError, UnicodeError, RecursionError):
                        pass
                    reason = "container_disappeared" if b"no such" in retry.stderr.lower() else retry.reason or "inspect_response_invalid"
                elif vanished:
                    reason = "container_disappeared"
                collected[identity] = unavailable_container(identity, reason)
        return [collected[identity] for identity in identities]

    def inventory(self, params):
        identities = self.identities()
        selected, cursor = page(identities, self.node.id, "docker.inventory", params)
        items = self.inspect(selected)
        reasons = [reason for item in items for reason in item["reason_codes"]]
        return {"containers": items}, list(dict.fromkeys(reasons)), len(identities), cursor

    def engine_version(self):
        result = self.transport.command([*self.command, "system", "dial-stdio"], b"GET /version HTTP/1.1\r\nHost: docker\r\nConnection: close\r\n\r\n", max_output=65536)
        if result.reason:
            raise Failure(result.reason)
        status, value = parse_http(result.stdout)
        version = value.get("ApiVersion") if isinstance(value, dict) else None
        if status != 200 or not isinstance(version, str) or not re.fullmatch(r"1\.\d{1,3}", version):
            raise Failure("docker_engine_version_invalid")
        version = min((1, 41), tuple(int(item) for item in version.split(".")))
        if version < (1, 24):
            raise Failure("docker_engine_version_unsupported", "unsupported")
        return ".".join(str(item) for item in version)

    def stats(self, identities, store, stale_after):
        metadata = self.inspect(identities)
        version = self.engine_version()
        commands = {}
        for item in metadata:
            if item["availability"] == "ok" and item["running"]:
                query = "stream=false" + ("&one-shot=true" if tuple(map(int, version.split("."))) >= (1, 41) else "")
                http = f"GET /v{version}/containers/{item['id']}/stats?{query} HTTP/1.1\r\nHost: docker\r\nConnection: close\r\n\r\n"
                commands[item["id"]] = "printf %s " + shlex.quote(http) + " | " + shlex.join([*self.command, "system", "dial-stdio"])
        result = self.transport.script(section_script(commands), max_output=3 * 1024 * 1024) if commands else None
        sections = parse_sections(result.stdout) if result else {}
        previous = store.state.setdefault("docker", {})
        items = []
        reasons = list(store.reasons)
        if result and result.reason:
            reasons.append(result.reason)
        for metadata_item in metadata:
            identity = metadata_item["id"]
            reason = None
            projected = None
            if metadata_item["availability"] != "ok":
                reason = metadata_item["reason_codes"][0]
            elif metadata_item["running"] is not True:
                reason = "container_not_running"
            else:
                code, body = sections.get(identity, (1, b""))
                if code != 0:
                    reason = result.reason if result and result.reason else "docker_stats_unavailable"
                else:
                    try:
                        status, raw = parse_http(body)
                        if status == 404:
                            reason = "container_disappeared"
                        elif status != 200:
                            reason = "docker_engine_unavailable"
                        else:
                            projected, sample = project_stats(raw, metadata_item, previous.get(identity), time.monotonic(), stale_after)
                            if sample:
                                previous[identity] = sample
                    except (Failure, ValueError, TypeError, AttributeError):
                        reason = "docker_stats_invalid"
            if reason:
                projected = {"id": identity, "availability": "unavailable", "reason_codes": [reason], "sampled_at": None,
                             "cpu": None, "memory": None, "block_io": None, "network": None}
            reasons.extend(projected["reason_codes"])
            items.append(projected)
        return {"containers": items}, list(dict.fromkeys(reasons))

    def logs(self, params):
        identity = params["container_id"]
        result = self.transport.command([*self.command, "logs", "--timestamps", "--tail", str(params["tail"]),
                                         "--since", f"{params['since_seconds']}s", "--", identity],
                                        max_output=params["max_bytes"], stderr_limit=params["max_bytes"])
        if result.reason and not result.truncated:
            reason = "container_disappeared" if b"no such" in result.stderr.lower() else result.reason
            raise Failure(reason)
        # Canonical UTF-8 output remains inside the requested byte budget even
        # when the source contains invalid UTF-8 or a split multi-byte sequence.
        stdout = result.stdout.decode("utf-8", "replace").encode("utf-8")[:params["max_bytes"]].decode("utf-8", "ignore")
        remaining = params["max_bytes"] - len(stdout.encode("utf-8"))
        stderr = result.stderr.decode("utf-8", "replace").encode("utf-8")[:remaining].decode("utf-8", "ignore")
        canonical_size = len(result.stdout.decode("utf-8", "replace").encode("utf-8")) + len(result.stderr.decode("utf-8", "replace").encode("utf-8"))
        truncated = result.truncated or canonical_size > params["max_bytes"]
        return {"container_id": identity, "stdout": stdout, "stderr": stderr, "truncated": truncated,
                "returned_bytes": len(stdout.encode("utf-8")) + len(stderr.encode("utf-8")), "tail": params["tail"],
                "since_seconds": params["since_seconds"], "max_bytes": params["max_bytes"], "source": "docker_cli_logs"}, ["output_limit_exceeded"] if truncated else []
