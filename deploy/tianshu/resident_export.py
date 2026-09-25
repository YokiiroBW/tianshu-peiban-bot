"""Offline Dockge export of an already configured resident bundle; never contacts Docker."""

import argparse
import copy
import ipaddress
import json
import re
import subprocess
from pathlib import Path, PurePosixPath

from bundle import preflight
from manifest import PRODUCTS, Refused, digest, fresh_target, inside, load_manifest, no_links, read_json, require, write_json
from observability_contract import COMPONENTS
from observability_release import verify_layout
from resource_profile import RESIDENT_KIND, validate

CORE_PROJECT = "tianshu-v2-resident"
OBS_PROJECT = "tianshu-v2-resident-obs"
OBS_COMMIT = "a194fa7b527ac2da0f13b8c3e95e76a1b836b4b3"
OBS_PREFIX = "deploy/observability"
IMAGE_PIN = re.compile(r"^.+@sha256:[a-f0-9]{64}$")


def deployment_path(value):
    require(isinstance(value, str) and value.startswith("/"), "absolute_deployment_root_required")
    path = PurePosixPath(value)
    require(
        str(path) == value and len(path.parts) >= 3
        and all(part not in {".", ".."} for part in value.split("/"))
        and not any(ch in value for ch in ("\\", ":", "$", "{", "}", "*", "?", "\n")),
        "unsafe_deployment_root",
    )
    return path


def pinned_source(repository, bundle, binding):
    """Compare every exported OBS byte to the fixed Git tree, not the checkout."""
    require(binding.get("source") == {"repo": "tianshu-peiban-bot", "commit": OBS_COMMIT}, "observability_source_mismatch")
    require(binding.get("status") == "configured_not_started", "observability_binding_status_invalid")
    repository = no_links(repository)
    require(repository.is_dir(), "source_repository_required")
    names = subprocess.check_output(
        ["git", "-C", str(repository), "ls-tree", "-r", "--name-only", OBS_COMMIT, OBS_PREFIX],
        timeout=30,
    ).decode().splitlines()
    require(bool(names), "observability_source_missing")
    expected = {}
    for name in names:
        require(name.startswith(OBS_PREFIX + "/"), "observability_source_path_invalid")
        relative = name[len(OBS_PREFIX) + 1:]
        raw = subprocess.check_output(
            ["git", "-C", str(repository), "show", OBS_COMMIT + ":" + name], timeout=30
        )
        expected[relative] = digest(raw)
        require(digest(inside(bundle, "tools/observability/" + relative).read_bytes()) == expected[relative], "observability_source_bytes_mismatch")
    require(binding.get("code_sha256") == expected, "observability_code_lock_mismatch")
    actual = {
        p.relative_to(bundle / "tools/observability").as_posix()
        for p in (bundle / "tools/observability").rglob("*") if p.is_file()
    }
    require(actual == set(expected), "observability_source_inventory_mismatch")
    return expected


def absolute_source(bundle, deployment_root, source):
    require(isinstance(source, str), "bind_source_invalid")
    if source.startswith("./"):
        relative = source[2:]
    else:
        path = Path(source)
        require(path.is_absolute(), "bind_source_invalid")
        require(no_links(path).is_relative_to(bundle), "external_bind_refused")
        relative = path.relative_to(bundle).as_posix()
    local = inside(bundle, relative)
    require(local.exists(), "bind_source_missing")
    require(local.is_file() or local.is_dir(), "bind_source_type_invalid")
    return str(deployment_root.joinpath(*PurePosixPath(relative).parts)), relative


def rewrite_stack(bundle, deployment_root, stack, project, services):
    result = copy.deepcopy(stack)
    require(set(result.get("services", {})) == services, "stack_services_mismatch")
    result["name"] = project
    mounted = []
    for name, service in result["services"].items():
        require(service.get("restart") == "unless-stopped", "resident_restart_policy_required")
        require(service.get("cpuset") == "6,7" and service.get("mem_limit"), "resident_resource_limit_missing")
        require("cpus" not in service and "pids_limit" not in service, "resident_unsupported_quota_present")
        require(IMAGE_PIN.fullmatch(service.get("image", "")), "image_digest_required")
        require("build" not in service, "build_refused")
        for volume in service.get("volumes", []):
            require(volume.get("type") == "bind" and volume.get("bind") == {"create_host_path": False}, "unlocked_mount_refused")
            source, relative = absolute_source(bundle, deployment_root, volume["source"])
            volume["source"] = source
            mounted.append({
                "stack": project, "service": name, "source": source,
                "relative": relative, "target": volume["target"],
                "sha256": digest(inside(bundle, relative).read_bytes()) if inside(bundle, relative).is_file() else None,
                "read_only": volume.get("read_only") is True,
            })
        if "env_file" in service:
            require(project == CORE_PROJECT and service["env_file"] == [f"./private/{name}.env"], "external_secret_file_refused")
            source, relative = absolute_source(bundle, deployment_root, service["env_file"][0])
            secret = inside(bundle, relative)
            require(secret.is_file() and relative == f"private/{name}.env", "external_secret_file_refused")
            service["env_file"] = [source]
            mounted.append({
                "stack": project, "service": name, "source": source,
                "relative": relative, "sha256": digest(secret.read_bytes()),
                "private_env_file": True,
            })
    return result, mounted


def validate_observability_networks(stack):
    require(set(stack.get("networks", {})) == {"observe", "storage", "access"}, "observability_three_networks_required")
    require(
        stack["networks"]["observe"].get("internal") is True
        and stack["networks"]["storage"].get("internal") is True
        and stack["networks"]["access"].get("internal") is False,
        "observability_network_boundary_mismatch",
    )
    for name, service in stack["services"].items():
        nets = service.get("networks", [])
        require(("access" in nets) == (name in {"obs-guard", "obs-grafana"}), "observability_access_membership_mismatch")
        ports = service.get("ports", [])
        require(bool(ports) == (name in {"obs-guard", "obs-grafana"}), "observability_published_ports_mismatch")
        require(all(isinstance(p, str) and p.startswith("127.0.0.1:") for p in ports), "observability_loopback_port_required")
    published = [p for service in stack["services"].values() for p in service.get("ports", [])]
    require(len(published) == 2 and len(set(p.split(":")[1] for p in published)) == 2, "observability_port_conflict")


def locked_subnets(core, obs):
    require(set(core.get("networks", {})) == {"core", "egress", "frontend"}, "resident_core_networks_required")
    result, seen = {}, []
    for project, stack in ((CORE_PROJECT, core), (OBS_PROJECT, obs)):
        for name, definition in stack["networks"].items():
            config = definition.get("ipam", {}).get("config", [])
            require(type(config) is list and len(config) == 1 and set(config[0]) == {"subnet"}, "resident_explicit_subnet_required")
            subnet = ipaddress.ip_network(config[0]["subnet"], strict=True)
            require(subnet.version == 4 and subnet.is_private and 24 <= subnet.prefixlen <= 28, "resident_private_subnet_required")
            require(not any(subnet.overlaps(other) for other in seen), "resident_network_overlap")
            seen.append(subnet)
            result[project + "/" + name] = str(subnet)
    return result


def capacity_contract(bundle):
    gib = 1024**3
    platform = read_json(bundle / "config/platform/settings.json")
    gateway = read_json(bundle / "config/gateway/settings.json")
    guard = read_json(bundle / "observability/config/guard.json")
    vector = read_json(bundle / "observability/config/vector.json")
    require(platform["diagnostics"].get("log_directory_bytes", gib) == gib, "resident_source_log_budget_mismatch")
    require(gateway["observability"].get("max_directory_bytes", gib) == gib, "resident_source_log_budget_mismatch")
    require(guard.get("log_budgets") == dict.fromkeys(PRODUCTS, gib), "resident_observability_log_budget_mismatch")
    require(guard.get("reserve_bytes") == gib, "resident_guard_reserve_mismatch")
    buffer = vector["sinks"]["loki"]["buffer"]
    require(buffer == {"type": "disk", "max_size": 4 * gib, "when_full": "block"}, "resident_vector_buffer_mismatch")
    return {
        "source_log_bytes_each": gib,
        "vector_buffer_bytes": 4 * gib,
        "guard_storage_reserve_bytes": gib,
        "host_capacity_protection": "pending_live_acceptance",
    }


def export(bundle, repository, deployment_root, output):
    bundle = no_links(Path(bundle).absolute())
    require(bundle.is_dir(), "bundle_required")
    deployment_root = deployment_path(deployment_root)
    output = fresh_target(output)
    require(not output.is_relative_to(bundle) and not bundle.is_relative_to(output), "output_bundle_overlap")
    report = preflight(bundle)
    require(report["status"] == "resident_candidate", "resident_bundle_required")
    manifest = load_manifest(bundle / "release-manifest.json")
    require(manifest["schema_version"] == "1.1.0" and manifest["status"] == "candidate", "resident_candidate_manifest_required")
    require(manifest["observability"]["source"] == {"repo": "tianshu-peiban-bot", "commit": OBS_COMMIT}, "observability_source_mismatch")
    metadata = read_json(bundle / "deployment.json")
    profile = validate(metadata["compose_inputs"].get("resource_profile"))
    require(profile is not None and profile["kind"] == RESIDENT_KIND and metadata["project_name"] == CORE_PROJECT, "resident_project_required")
    require((bundle / "observability-release.json").is_file(), "observability_binding_required")
    binding = read_json(bundle / "observability-release.json")
    require(binding.get("project_name") == OBS_PROJECT, "observability_project_mismatch")
    require(binding.get("release_manifest_sha256") == digest((bundle / "release-manifest.json").read_bytes()), "observability_manifest_binding_mismatch")
    source_hashes = pinned_source(repository, bundle, binding)
    core = read_json(bundle / "compose.json")
    obs = verify_layout(bundle, manifest)
    validate_observability_networks(obs)
    subnets = locked_subnets(core, obs)
    capacity = capacity_contract(bundle)
    require(
        binding.get("source") == manifest["observability"]["source"],
        "observability_source_mismatch",
    )
    require(
        read_json(bundle / "observability/binding.json").get("images")
        == {name.removeprefix("obs-"): service["image"] for name, service in obs["services"].items()},
        "observability_image_binding_mismatch",
    )
    require(core.get("name") == CORE_PROJECT, "resident_project_required")
    require(obs.get("name") == "tianshu-observability", "observability_template_name_mismatch")
    core_out, core_mounts = rewrite_stack(bundle, deployment_root, core, CORE_PROJECT, set(PRODUCTS))
    obs_out, obs_mounts = rewrite_stack(bundle, deployment_root, obs, OBS_PROJECT, {"obs-" + c for c in COMPONENTS})
    require(
        all(mount["relative"].startswith("private/") for mount in core_mounts if mount.get("private_env_file")),
        "external_secret_file_refused",
    )
    inventory = read_json(bundle / "bundle-integrity.json")["files"]
    require(all(IMAGE_PIN.fullmatch(core_out["services"][p]["image"]) for p in PRODUCTS), "image_digest_required")
    lock = {
        "schema_version": "nas-a3-r1-resident-export/1",
        "status": "resident_candidate",
        "acceptance": "pending_live_acceptance",
        "release_ready": False,
        "deployment_root": str(deployment_root),
        "projects": [CORE_PROJECT, OBS_PROJECT],
        "manifest_sha256": digest((bundle / "release-manifest.json").read_bytes()),
        "bundle_integrity_sha256": digest((bundle / "bundle-integrity.json").read_bytes()),
        "observability_source": {"commit": OBS_COMMIT, "package_path": OBS_PREFIX, "files": source_hashes},
        "product_sources": {p: manifest["products"][p]["source"] for p in PRODUCTS},
        "images": {**{p: core_out["services"][p]["image"] for p in PRODUCTS}, **{n: s["image"] for n, s in obs_out["services"].items()}},
        "config_and_private_sha256": {n: h for n, h in inventory.items() if n.startswith(("config/", "private/", "observability/config/", "observability-input/"))},
        "mounts": core_mounts + obs_mounts,
        "network_subnets": subnets,
        "capacity_contract": capacity,
        "published_ports": {
            "platform": core_out["services"]["platform"]["ports"],
            "guard": obs_out["services"]["obs-guard"]["ports"],
            "grafana": obs_out["services"]["obs-grafana"]["ports"],
        },
        "restart_policy": "unless-stopped",
        "maintenance_stop": "disable restart on exact project container IDs, verify policy, then SIGTERM and wait; restore policy only after maintenance",
    }
    output.mkdir(mode=0o700)
    (output / "INCOMPLETE").write_text("Dockge export in progress; do not import.\n", encoding="utf-8")
    compose_hashes = {}
    for project, stack in ((CORE_PROJECT, core_out), (OBS_PROJECT, obs_out)):
        directory = output / project
        directory.mkdir(mode=0o700)
        path = directory / "compose.yaml"
        write_json(path, stack)
        compose_hashes[project] = digest(path.read_bytes())
    lock["compose_sha256"] = compose_hashes
    write_json(output / "resident-export.lock.json", lock)
    (output / "resident-export.lock.json").chmod(0o600)
    (output / "INCOMPLETE").unlink()
    return {"status": "resident_candidate", "acceptance": "pending_live_acceptance", "release_ready": False, "started_services": False, "projects": [CORE_PROJECT, OBS_PROJECT]}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Offline resident Dockge export; starts nothing")
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--repository", required=True, type=Path)
    parser.add_argument("--deployment-root", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        report = export(args.bundle, args.repository, args.deployment_root, args.output)
        print(json.dumps(report, sort_keys=True))
        return 0
    except Refused as exc:
        code = str(exc)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        code = "invalid_or_unavailable_input"
    print(json.dumps({"status": "refused", "code": code, "release_ready": False}, sort_keys=True))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
