"""Permit and isolated-input validation. No execution and no implicit permit creation."""

import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from .manifest import PRODUCTS, fields, sha256
from .runtime_identity import schema_check
from .safety import child, file_hash, files, read_json, require, safe_path
from .snapshot import state_fingerprints, verify_guards

OWNERS = PRODUCTS | {
    "obs-" + n for n in ("vector", "loki", "grafana", "prometheus", "guard")
}


def restoration_facts(recovery, source, target, authority_id):
    source, marker, (manifest, inventory, resources) = recovery.deployment(
        source.name, authority=True, expected_id=authority_id
    )
    target, tm, (manifest2, inventory2, resources2) = recovery.deployment(target.name)
    require(
        tm["role"] == "restored" and tm["status"] == "restored_disabled",
        "restored_target_required",
    )
    require(
        manifest == manifest2 and inventory == inventory2, "restore_metadata_mismatch"
    )
    verify_guards(source, inventory, resources)
    verify_guards(target, inventory2, resources2)
    original = state_fingerprints(source, resources, recovery.control.check)
    require(
        original == state_fingerprints(target, resources2, recovery.control.check),
        "current_authority_diverged",
    )
    recovery.inputs(source, manifest, resources)
    recovery.inputs(target, manifest2, resources2)
    receipt = read_json(child(target, "RESTORE.json"))
    snapshot, _, _ = recovery.inspect_backup(
        receipt["backup"], receipt["snapshot_sha256"]
    )
    require(
        receipt["authority_deployment_id"] == authority_id
        and snapshot["source_deployment_id"] == authority_id,
        "drill_backup_authority_mismatch",
    )
    for entry in snapshot["entries"]:
        require(
            file_hash(child(target, entry["path"])) == entry["sha256"],
            "drill_restored_payload_changed",
        )
    ignored = {r["path"] for r in resources.values() if r["kind"] == "owner_lock"}
    ignored.update(
        r["path"] + suffix
        for r in resources.values()
        if r["kind"] == "sqlite"
        for suffix in ("-wal", "-shm")
    )
    require(
        set(files(target, max_files=recovery.max_files)) - ignored
        == {e["path"] for e in snapshot["entries"]}
        | {".deployment.json", "RESTORE.json"},
        "drill_restored_file_set_changed",
    )
    content = {
        name: file_hash(child(target, name))
        for name in files(target, max_files=recovery.max_files)
        if name not in ignored
    }
    return {
        "scope_id": recovery.scope_id,
        "authority_id": marker["deployment_id"],
        "restored_id": tm["deployment_id"],
        "states": original,
        "restored_files": content,
    }


def permit(recovery, path, checksum, *, now=None):
    require(
        file_hash(safe_path(path)) == sha256(checksum), "drill_permit_digest_mismatch"
    )
    value = read_json(path)
    schema_check(value, read_json(Path(__file__).with_name("drill-permit.schema.json")))
    for name in ("permit_id", "scope_id", "authority_id"):
        require(str(uuid.UUID(value[name])) == value[name], "invalid_drill_uuid")
    require(value["scope_id"] == recovery.scope_id, "drill_scope_mismatch")
    now = time.time() if now is None else now
    require(
        value["issued_at"] <= now < value["expires_at"]
        and 0 < value["expires_at"] - value["issued_at"] <= 900
        and value["max_runtime_seconds"] <= value["expires_at"] - value["issued_at"],
        "drill_permit_expired_or_unbounded",
    )
    paths = {}
    for key in ("source_directory", "restored_directory", "drill_directory"):
        paths[key] = safe_path(value[key], exists=key != "drill_directory")
        require(
            paths[key].parent == child(recovery.root, "deployments"),
            "drill_deployment_scope_mismatch",
        )
    require(len(set(paths.values())) == 3, "drill_paths_overlap")
    require(not paths["drill_directory"].exists(), "drill_must_be_new")
    inputs = safe_path(value["inputs_directory"])
    require(
        inputs.parent == child(recovery.root, "drill-inputs"),
        "drill_inputs_scope_mismatch",
    )
    core = value["projects"]["core"]
    require(
        value["projects"]["observability"] == core + "-obs",
        "drill_project_pair_mismatch",
    )
    return value


def _strings(value):
    if isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)
    elif isinstance(value, str):
        yield value


def isolated_inputs(value, manifest, *, resource_profile=None):
    inputs = safe_path(value["inputs_directory"])
    index_path = child(inputs, "inputs.json")
    require(
        file_hash(index_path) == value["inputs_sha256"], "drill_inputs_digest_mismatch"
    )
    index = read_json(index_path)
    fields(index, "schema_version files assertions")
    require(
        index["schema_version"] == "dep-j-drill-inputs/1"
        and isinstance(index["files"], dict),
        "drill_inputs_invalid",
    )
    require(
        set(files(inputs)) == set(index["files"]) | {"inputs.json"},
        "drill_inputs_file_set_mismatch",
    )
    for name, checksum in index["files"].items():
        require(
            name.startswith(
                (
                    "config/",
                    "private/",
                    "contracts/",
                    "tools/",
                    "observability/config/",
                    "observability/code/",
                    "observability/private/",
                    "observability-input/",
                )
            )
            or name in {"compose.json", "observability/compose.yaml"},
            "drill_input_path_forbidden",
        )
        require(
            file_hash(child(inputs, name)) == sha256(checksum), "drill_input_changed"
        )
    config = {
        k: v
        for k, v in index["files"].items()
        if k.startswith(
            (
                "config/",
                "private/",
                "observability/config/",
                "observability/private/",
                "observability-input/",
            )
        )
    }
    require(config == value["config_sha256"], "drill_configuration_mismatch")
    root = Path(value["drill_directory"])
    services, documents, endpoints, aliases = {}, {}, {}, set(OWNERS)
    for group, filename in (
        ("core", "compose.json"),
        ("observability", "observability/compose.yaml"),
    ):
        require(
            index["files"].get(filename) == value["compose_sha256"][group],
            "drill_compose_digest_mismatch",
        )
        document = read_json(child(inputs, filename))
        require(
            all("$" not in text for text in _strings(document)),
            "drill_interpolation_forbidden",
        )
        fields(document, "name services networks")
        require(
            document["name"] == value["projects"][group],
            "drill_compose_project_mismatch",
        )
        expected = PRODUCTS if group == "core" else OWNERS - PRODUCTS
        require(set(document["services"]) == expected, "drill_owner_set_mismatch")
        require(
            document["networks"]
            and all(
                isinstance(n, dict)
                and n.get("internal") is True
                and not n.get("external")
                and set(n) <= {"internal", "driver", "ipam"}
                and n.get("driver", "bridge") == "bridge"
                for n in document["networks"].values()
            ),
            "drill_internal_network_required",
        )
        for service, definition in document["services"].items():
            require(
                set(definition)
                <= set(
                    "image platform user read_only init cap_drop security_opt pids_limit mem_limit cpus cpuset restart scale stop_signal stop_grace_period entrypoint command environment env_file tmpfs volumes networks healthcheck logging labels depends_on ports pull_policy".split()
                ),
                "drill_unknown_service_field",
            )
            require(
                not any(
                    k in definition
                    for k in (
                        "build",
                        "container_name",
                        "network_mode",
                        "pid",
                        "ipc",
                        "devices",
                        "volumes_from",
                        "extends",
                        "external_links",
                        "extra_hosts",
                        "profiles",
                        "deploy",
                    )
                ),
                "drill_unsafe_service",
            )
            require(
                definition.get("restart") == "no"
                and definition.get("user") == "10001:10001"
                and definition.get("read_only") is True
                and not definition.get("privileged")
                and definition.get("cap_drop") == ["ALL"]
                and not definition.get("cap_add")
                and definition.get("scale", 1) == 1
                and definition.get("pull_policy") == "never",
                "drill_service_policy_required",
            )
            require(
                definition.get("image") == value["image_ids"][service],
                "drill_local_image_pin_required",
            )
            from .nas_resources import check_definition

            nas_profile = check_definition(definition, resource_profile)
            require(
                nas_profile
                or (
                    definition.get("pids_limit", 0) in range(1, 257)
                    and definition.get("mem_limit")
                    and float(definition.get("cpus", 0)) in (0.5, 0.75, 1.0, 1.5, 2.0)
                ),
                "drill_resource_limits_required",
            )
            networks = definition.get("networks", {})
            require(
                networks and set(networks) <= set(document["networks"]),
                "drill_network_mismatch",
            )
            if isinstance(networks, dict):
                for settings in networks.values():
                    if settings:
                        aliases.update(settings.get("aliases", []))
            mounts = definition.get("volumes", [])
            for mount in mounts:
                require(
                    isinstance(mount, dict)
                    and mount.get("type") == "bind"
                    and type(mount.get("read_only")) is bool,
                    "drill_bind_required",
                )
                path = Path(mount["source"])
                require(
                    path.is_absolute()
                    and ".." not in path.parts
                    and path.is_relative_to(root)
                    and path != root,
                    "drill_mount_outside_clone",
                )
                relative = path.relative_to(root).as_posix()
                if not mount["read_only"]:
                    require(
                        any(
                            v["mount"]
                            and v["owner_service"] == service
                            and v["host_path"] == relative
                            and v["container_path"] == mount["target"]
                            for v in manifest["volumes"]
                        ),
                        "drill_unknown_writer_mount",
                    )
                else:
                    require(
                        relative in index["files"]
                        or any(p.startswith(relative + "/") for p in index["files"])
                        or relative.startswith("logs/"),
                        "drill_read_mount_unregistered",
                    )
            for volume in manifest["volumes"]:
                if volume["mount"] and volume["owner_service"] == service:
                    require(
                        any(
                            m["source"] == str(root / volume["host_path"])
                            and m["target"] == volume["container_path"]
                            and m["read_only"] is False
                            for m in mounts
                        ),
                        "drill_owner_mount_missing",
                    )
            for env_file in definition.get("env_file", []):
                require(isinstance(env_file, str), "drill_env_file_invalid")
                full = Path(env_file)
                require(
                    full.is_absolute()
                    and full.is_relative_to(root)
                    and ".." not in full.parts
                    and full.relative_to(root).as_posix() in index["files"],
                    "drill_secret_outside_clone",
                )
            for port in definition.get("ports", []):
                require(
                    isinstance(port, dict)
                    and port.get("host_ip") == "127.0.0.1"
                    and port.get("protocol", "tcp") == "tcp"
                    and str(port.get("published", "")).isdigit(),
                    "drill_loopback_port_required",
                )
                public = int(port["published"])
                require(
                    1024 <= public <= 65535 and public not in endpoints,
                    "drill_port_conflict",
                )
                endpoints[public] = service
            services[service] = definition
        documents[group] = document
    for name in index["files"]:
        if name.endswith(".json") and "/config/" in "/" + name:
            for text in _strings(read_json(child(inputs, name))):
                if "://" in text:
                    url = urlsplit(text)
                    require(
                        url.scheme in {"http", "https"}
                        and url.hostname in aliases
                        and url.username is None
                        and url.password is None,
                        "drill_external_endpoint_forbidden",
                    )
    require(
        isinstance(index["assertions"], list) and 1 <= len(index["assertions"]) <= 32,
        "drill_assertions_required",
    )
    for assertion in index["assertions"]:
        fields(
            assertion, "id service url ca_file token_file expected_status expected_json"
        )
        require(
            assertion["id"]
            in {
                "data_readback",
                "source_revoked",
                "model_revoked",
                "forgotten",
                "unknown_no_resend",
            }
            and assertion["service"] in PRODUCTS,
            "drill_assertion_kind_invalid",
        )
        url = urlsplit(assertion["url"])
        require(
            url.scheme == "https"
            and url.hostname == "127.0.0.1"
            and url.port in endpoints
            and endpoints[url.port] == assertion["service"]
            and not url.username
            and not url.password
            and not url.fragment,
            "drill_assertion_endpoint_forbidden",
        )
        require(
            assertion["ca_file"] in index["files"]
            and assertion["token_file"] in index["files"]
            and type(assertion["expected_status"]) is int
            and 200 <= assertion["expected_status"] <= 599
            and isinstance(assertion["expected_json"], dict)
            and assertion["expected_json"],
            "drill_assertion_invalid",
        )
        require(
            url.path not in {"/health/live", "/health/ready", "/health"},
            "health_is_not_functional_assertion",
        )
    return index, documents
