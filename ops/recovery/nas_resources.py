"""Recovery adapter for the same explicitly bound NAS QA resource policy."""

import json

from .safety import read_json, require


def profile_for(source, runtime):
    stacks = [p["compose_json"] for p in runtime["projects"].values()]
    uses_cpuset = any("cpuset" in s for p in stacks for s in p["services"].values())
    if not uses_cpuset:
        return None
    from deploy.observability.nas_resources import bind, check_compose

    metadata = read_json(source / "deployment.json")
    profile = bind(source, metadata["compose_inputs"].get("resource_profile"))
    require(profile is not None, "recovery_unbound_cpu_set")
    for stack in stacks:
        check_compose(stack, profile)
    return profile


def limits(definition):
    if "cpuset" not in definition:
        return None
    return {
        k: definition[k]
        for k in ("cpuset", "mem_limit", "cpus", "pids_limit")
        if k in definition
    }


def check_definition(definition, profile):
    if profile is None:
        require("cpuset" not in definition, "recovery_unbound_cpu_set")
        return False
    from deploy.observability.nas_resources import check_compose

    check_compose({"services": {"owner": definition}}, profile)
    return True


def check_host(docker, profile):
    if profile is not None:
        from deploy.observability.nas_resources import host_check

        host_check(profile, json.loads(docker.run("info", "--format", "{{json .}}")))


def check_container(expected, row):
    spec = expected.get("resource_limits")
    if spec is not None:
        from deploy.observability.nas_resources import container_check

        container_check(spec, row)
