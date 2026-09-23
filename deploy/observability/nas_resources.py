"""Explicit NAS QA resource profile; never presents missing PID control as verified."""

import json
import ipaddress


def require(condition, code):
    if not condition:
        raise ValueError(code)


KIND = "nas-cpuset-qa-v1"


def network_plan(root, subnets):
    if subnets is None:
        return None
    require(
        isinstance(subnets, dict) and set(subnets) == {"observe", "storage"},
        "observe_network_shape",
    )
    site = json.loads((root / "deployment.json").read_bytes())["compose_inputs"]
    nets = [
        ipaddress.ip_network(v, strict=True)
        for v in [site["subnet"], *site.get("auxiliary_subnets", {}).values()]
    ]
    for value in subnets.values():
        net = ipaddress.ip_network(value, strict=True)
        require(
            net.version == 4 and net.is_private and 24 <= net.prefixlen <= 28,
            "observe_private_small_subnet_required",
        )
        require(
            not any(net.overlaps(other) for other in nets), "observe_network_overlap"
        )
        nets.append(net)
    return subnets


def validate(profile):
    if profile is None:
        return None
    require(
        isinstance(profile, dict) and set(profile) == {"kind", "cpus", "pid_limit"},
        "resource_profile_shape",
    )
    require(
        profile["kind"] == KIND and profile["pid_limit"] == "unsupported",
        "resource_profile_invalid",
    )
    cpus = profile["cpus"]
    require(
        isinstance(cpus, list)
        and len(cpus) == 2
        and all(type(c) is int and 0 <= c < 4096 for c in cpus),
        "resource_cpu_set_invalid",
    )
    require(cpus == sorted(set(cpus)), "resource_cpu_set_invalid")
    return profile


def constrain(service, profile):
    profile = validate(profile)
    if profile is not None:
        service.pop("cpus", None)
        service.pop("pids_limit", None)
        service["cpuset"] = ",".join(map(str, profile["cpus"]))
    return service


def host_check(profile, info):
    profile = validate(profile)
    require(profile is not None, "resource_profile_required")
    info = {k.casefold(): v for k, v in info.items()}
    require(
        info.get("memorylimit") is True and info.get("cpuset") is True,
        "nas_resource_controller_missing",
    )
    require(
        info.get("pidslimit") is False, "nas_profile_requires_observed_pid_limitation"
    )
    count = info.get("ncpu")
    require(
        type(count) is int and count > max(profile["cpus"]),
        "resource_cpu_set_unavailable",
    )
    return {
        "profile": profile,
        "memory_controller": "supported",
        "cpu_set_controller": "supported",
        "pid_controller": "unsupported",
        "release_ready": False,
    }


def container_check(service, inspected):
    """Called before starting a created container and again before lifecycle actions."""
    if "cpuset" not in service:
        return
    config = inspected.get("HostConfig", {})
    require(config.get("CpusetCpus") == service["cpuset"], "container_cpu_set_mismatch")
    require(
        config.get("NanoCpus", 0) == 0 and config.get("CpuQuota", 0) in (0, -1),
        "container_cpu_quota_unexpected",
    )
    require(config.get("PidsLimit") in (None, 0, -1), "container_pid_profile_mismatch")
    memory = service["mem_limit"]
    if isinstance(memory, str):
        memory = int(memory[:-1]) * {"m": 1024**2, "g": 1024**3}[memory[-1].lower()]
    require(
        type(config.get("Memory")) is int and config["Memory"] == memory,
        "container_memory_limit_mismatch",
    )


def bind(root, profile):
    profile = validate(profile)
    if profile is None:
        if (root / "deployment.json").exists():
            metadata = json.loads((root / "deployment.json").read_bytes())
            require(
                metadata["compose_inputs"].get("resource_profile") is None,
                "nas_profile_core_binding_mismatch",
            )
        return None
    metadata = json.loads((root / "deployment.json").read_bytes())
    require(
        metadata["compose_inputs"].get("resource_profile") == profile,
        "nas_profile_core_binding_mismatch",
    )
    require(
        metadata["project_name"].startswith("tianshu-qa-"), "nas_profile_synthetic_only"
    )
    require(
        metadata["compose_inputs"]["bind_address"] == "127.0.0.1",
        "nas_profile_loopback_only",
    )
    require(
        set(metadata["tls_provenance"])
        == {"platform", "companion", "memory", "gateway"}
        and all(v == "isolated_test" for v in metadata["tls_provenance"].values()),
        "nas_profile_test_tls_only",
    )
    return profile


def check_compose(stack, profile):
    profile = validate(profile)
    if profile is None:
        require(
            all("cpuset" not in s for s in stack["services"].values()),
            "unbound_cpu_set",
        )
        return
    for service in stack["services"].values():
        require(
            service.get("cpuset") == ",".join(map(str, profile["cpus"]))
            and "cpus" not in service
            and "pids_limit" not in service,
            "nas_compose_profile_mismatch",
        )
        require(service.get("mem_limit") is not None, "nas_memory_limit_required")
