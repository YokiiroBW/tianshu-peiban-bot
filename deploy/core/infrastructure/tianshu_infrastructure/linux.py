"""Linux/Synology proc counters and OpenWrt ubus projection, without node Python."""

from __future__ import annotations

import math
import time

from .protocol import Failure, decode_json
from .sampling import sample_condition, save_sample, stable_id
from .transport import parse_sections, section_script

U64 = 2**64 - 1


def counter(value):
    if type(value) is int and 0 <= value <= U64:
        return value
    raise ValueError("invalid counter")


def number(value):
    if type(value) in (int, float) and math.isfinite(value) and value >= 0:
        return value
    return None


def text(value, maximum=512):
    return value if isinstance(value, str) and len(value) <= maximum and "\x00" not in value else None


def parse_cpu(raw: bytes):
    fields = raw.decode("ascii").splitlines()[0].split()
    if len(fields) < 5 or fields[0] != "cpu":
        raise ValueError("invalid CPU aggregate")
    # guest/guest_nice are included in user/nice: only the first eight count.
    values = [counter(int(item)) for item in fields[1:9]]
    total = counter(sum(values))
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    if not total or idle > total:
        raise ValueError("inconsistent CPU aggregate")
    return values, total, idle


def parse_memory(raw: bytes):
    values = {}
    for line in raw.decode("ascii").splitlines():
        fields = line.split()
        if len(fields) == 3 and fields[2] == "kB" and fields[0] in ("MemTotal:", "MemAvailable:"):
            values[fields[0][:-1]] = counter(int(fields[1]) * 1024)
    total = values.get("MemTotal")
    available = values.get("MemAvailable")
    if not total or available is not None and available > total:
        raise ValueError("inconsistent memory")
    return {
        "total_bytes": total, "available_bytes": available,
        "used_bytes": total - available if available is not None else None,
        "state": "ok" if available is not None else "unavailable",
        "reason_codes": [] if available is not None else ["mem_available_missing"],
        "source": "proc_meminfo",
    }


def parse_filesystems(raw: bytes, node_id):
    result = []
    errors = []
    lines = raw.decode("utf-8", "replace").splitlines()
    if not lines or "Filesystem" not in lines[0]:
        raise ValueError("invalid df header")
    for line in lines[1:]:
        if not line.strip():
            continue
        fields = line.split(maxsplit=5)
        try:
            if len(fields) != 6 or not fields[4].endswith("%"):
                raise ValueError("invalid filesystem row")
            device, total, used, available, _, mountpoint = fields
            total, used, available = (counter(int(item) * 1024) for item in (total, used, available))
            if not mountpoint.startswith("/") or used > total or available > total or len(mountpoint) > 4096 or len(device) > 512:
                raise ValueError("invalid filesystem capacity")
            identity = stable_id("fs", node_id, device, mountpoint)
            result.append({"id": identity, "device": device, "mountpoint": mountpoint,
                           "total_bytes": total, "used_bytes": used, "available_bytes": available,
                           "source": "df_posix_1k"})
        except ValueError:
            errors.append("filesystem_row_invalid")
    unique = {item["id"]: item for item in result}
    if len(unique) > 1024:
        errors.append("filesystem_limit_reached")
    return sorted(unique.values(), key=lambda item: item["id"])[:1024], errors


def parse_interfaces(raw: bytes):
    result = {}
    for line in raw.decode("ascii").splitlines():
        if ":" not in line:
            continue
        name, values = line.rsplit(":", 1)
        name = name.strip()
        columns = values.split()
        if not name or len(name) > 128 or len(columns) != 16:
            raise ValueError("invalid network row")
        counters = [counter(int(item)) for item in columns]
        result[name] = [counters[0], counters[8], counters[2], counters[10], counters[3], counters[11]]
    if not result:
        raise ValueError("network counters missing")
    return result


def parse_ifindices(raw: bytes):
    result = {}
    for line in raw.decode("ascii").splitlines():
        parts = line.split("\t")
        if len(parts) == 2:
            result[parts[0]] = counter(int(parts[1]))
    return result


def project_openwrt(board, info, interfaces):
    reasons = []
    projected_board = None
    projected_info = None
    projected_interfaces = []
    if isinstance(board, dict):
        release = board.get("release")
        projected_board = {key: text(board.get(key)) for key in ("model", "board_name", "system")}
        projected_board["release"] = {key: text(release.get(key)) for key in ("distribution", "version", "revision", "target", "description")} if isinstance(release, dict) else None
        if not projected_board["model"] or not projected_board["release"]:
            reasons.append("ubus_board_partial")
    else:
        reasons.append("ubus_board_unavailable")
    if isinstance(info, dict):
        load = info.get("load")
        load = [number(item) for item in load] if isinstance(load, list) and len(load) == 3 else None
        if load is not None and all(item is not None for item in load):
            load = [item / 65536 for item in load]
        else:
            load = None
        memory = info.get("memory")
        projected_info = {"uptime_seconds": number(info.get("uptime")), "load_average": load,
                          "memory": {key + "_bytes": memory.get(key) if type(memory.get(key)) is int and 0 <= memory[key] <= U64 else None for key in ("total", "free", "shared", "buffered", "available")} if isinstance(memory, dict) else None}
        if projected_info["uptime_seconds"] is None or load is None or projected_info["memory"] is None:
            reasons.append("ubus_info_partial")
    else:
        reasons.append("ubus_info_unavailable")
    entries = interfaces.get("interface") if isinstance(interfaces, dict) else None
    if not isinstance(entries, list):
        reasons.append("ubus_interfaces_unavailable")
    else:
        if len(entries) > 1024:
            reasons.append("ubus_interface_limit_reached")
        for entry in entries[:1024]:
            if not isinstance(entry, dict) or not text(entry.get("interface")):
                reasons.append("ubus_interface_invalid")
                continue
            item = {"name": entry["interface"]}
            for key in ("up", "pending", "available", "autostart"):
                item[key] = entry.get(key) if type(entry.get(key)) is bool else None
            for key in ("device", "l3_device"):
                item[key] = text(entry.get(key))
            item["protocol"] = text(entry.get("proto"))
            item["uptime_seconds"] = number(entry.get("uptime"))
            for input_key, output_key in (("ipv4-address", "ipv4_addresses"), ("ipv6-address", "ipv6_addresses")):
                addresses = entry.get(input_key, [])
                item[output_key] = []
                if not isinstance(addresses, list):
                    reasons.append("ubus_address_invalid")
                    continue
                if len(addresses) > 128:
                    reasons.append("ubus_address_limit_reached")
                for address in addresses[:128]:
                    if isinstance(address, dict) and text(address.get("address")):
                        mask = address.get("mask")
                        item[output_key].append({"address": address["address"], "mask": mask if type(mask) is int and 0 <= mask <= 128 else None})
                    else:
                        reasons.append("ubus_address_invalid")
            item["source"] = "ubus_network_interface_dump"
            projected_interfaces.append(item)
    return {"board": projected_board, "info": projected_info, "interfaces": projected_interfaces,
            "per_device_traffic": {"state": "unsupported", "reason_code": "per_device_source_not_configured", "items": []},
            "reason_codes": list(dict.fromkeys(reasons))}


def collect_node(transport, node, store, stale_after):
    commands = {
        "hostname": "cat /proc/sys/kernel/hostname", "kernel": "uname -sr",
        "boot": "cat /proc/sys/kernel/random/boot_id", "uptime": "cat /proc/uptime",
        "load": "cat /proc/loadavg", "cpu": "cat /proc/stat", "memory": "cat /proc/meminfo",
        "interfaces": "cat /proc/net/dev", "filesystems": "df -P -k",
        "ifindices": 'for _p in /sys/class/net/*/ifindex; do [ -r "$_p" ] || continue; _n=${_p#/sys/class/net/}; _n=${_n%/ifindex}; printf "%s\\t%s\\n" "$_n" "$(cat "$_p")"; done',
    }
    if node.kind == "openwrt":
        commands.update(board="ubus call system board", info="ubus call system info", ubus_interfaces="ubus call network.interface dump")
    result = transport.script(section_script(commands))
    sections = parse_sections(result.stdout)
    if not sections:
        raise Failure(result.reason or "node_snapshot_unavailable")
    reasons = list(store.reasons)
    if result.reason:
        reasons.append(result.reason)

    def parse(name, parser, default=None):
        code, raw = sections.get(name, (1, b""))
        if code == 0:
            try:
                value = parser(raw)
                if value is None:
                    raise ValueError("source returned null")
                return value
            except (ValueError, UnicodeError, IndexError, TypeError, RecursionError):
                pass
        reasons.append(name + "_unavailable")
        return default

    hostname = parse("hostname", lambda raw: text(raw.decode("utf-8").strip()))
    kernel = parse("kernel", lambda raw: text(raw.decode("utf-8").strip()))
    boot = parse("boot", lambda raw: text(raw.decode("ascii").strip()))
    uptime = parse("uptime", lambda raw: number(float(raw.split()[0])))
    load = parse("load", lambda raw: [float(item) for item in raw.split()[:3]])
    if load is not None and (len(load) != 3 or any(number(item) is None for item in load)):
        load = None
        reasons.append("load_unavailable")
    now = uptime if uptime is not None else time.monotonic()
    identity = [boot, "uptime" if uptime is not None else "local"]
    previous = store.state.get("host", {})
    new_host = dict(previous)
    parsed_cpu = parse("cpu", parse_cpu)
    cpu = {"usage_ratio": None, "basis": "host_total", "total_ticks": None, "idle_ticks": None,
           "state": "unavailable", "reason_codes": ["cpu_unavailable"], "source": "proc_stat"}
    if parsed_cpu:
        values, total, idle = parsed_cpu
        state, delta = sample_condition(previous.get("cpu"), values, now, stale_after, identity=identity)
        ratio = None
        if delta:
            total_delta = sum(delta[1])
            idle_delta = delta[1][3] + (delta[1][4] if len(delta[1]) > 4 else 0)
            if total_delta and idle_delta <= total_delta:
                ratio = 1 - idle_delta / total_delta
            else:
                state = "unavailable"
        cpu.update(usage_ratio=ratio, total_ticks=total, idle_ticks=idle, state=state,
                   reason_codes=[] if state == "ok" else [state])
        new_host["cpu"] = save_sample(values, now, identity)
    memory = parse("memory", parse_memory, {"total_bytes": None, "available_bytes": None, "used_bytes": None,
                    "state": "unavailable", "reason_codes": ["memory_unavailable"], "source": "proc_meminfo"})
    reasons.extend(memory["reason_codes"])
    filesystems, disk_reasons = parse("filesystems", lambda raw: parse_filesystems(raw, node.id), ([], []))
    reasons.extend(disk_reasons)
    ifindices = parse("ifindices", parse_ifindices, {})
    network = parse("interfaces", parse_interfaces, {})
    if len(network) > 1024:
        reasons.append("interface_limit_reached")
    interfaces = []
    new_interfaces = {}
    previous_interfaces = previous.get("interfaces", {})
    for name, values in sorted(network.items())[:1024]:
        index = ifindices.get(name)
        if index is None:
            reasons.append("interface_identity_partial")
        state, delta = sample_condition(previous_interfaces.get(name), values, now, stale_after, identity=[*identity, index])
        item = dict(zip(("receive_bytes", "transmit_bytes", "receive_errors", "transmit_errors", "receive_drops", "transmit_drops"), values))
        item.update(id=stable_id("if", node.id, name), name=name, ifindex=index,
                    receive_bytes_per_second=delta[1][0] / delta[0] if delta else None,
                    transmit_bytes_per_second=delta[1][1] / delta[0] if delta else None,
                    state=state, reason_codes=[] if state == "ok" else [state], source="proc_net_dev")
        interfaces.append(item)
        new_interfaces[name] = save_sample(values, now, [*identity, index])
    if network:
        new_host["interfaces"] = new_interfaces
    store.state["host"] = new_host
    openwrt = None
    if node.kind == "openwrt":
        openwrt = project_openwrt(parse("board", decode_json), parse("info", decode_json), parse("ubus_interfaces", decode_json))
        reasons.extend(openwrt["reason_codes"])
        observed_devices = {item["name"] for item in interfaces}
        if any(item["l3_device"] and item["l3_device"] not in observed_devices for item in openwrt["interfaces"]):
            reasons.append("ubus_device_counters_unavailable")
    return {"identity": {"node_id": node.id, "kind": node.kind, "hostname": hostname, "kernel": kernel, "boot_id": boot},
            "system": {"uptime_seconds": uptime, "load_average": load, "source": "proc"}, "cpu": cpu, "memory": memory,
            "filesystems": filesystems, "interfaces": interfaces, "openwrt": openwrt}, list(dict.fromkeys(reasons))
