"""Explicit NAS QA resource profile; never presents missing PID control as verified."""

from manifest import require

KIND = "nas-cpuset-qa-v1"
LAN_KIND = "nas-cpuset-lan-qa-v1"


def validate(profile):
    if profile is None:
        return None
    require(
        isinstance(profile, dict) and set(profile) == {"kind", "cpus", "pid_limit"},
        "resource_profile_shape",
    )
    require(
        profile["kind"] in {KIND, LAN_KIND} and profile["pid_limit"] == "unsupported",
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
