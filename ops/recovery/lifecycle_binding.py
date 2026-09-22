"""Immutable binding of a private synthetic deployment to its two Compose projects."""

import re
from pathlib import Path

from .manifest import identifier
from .safety import (
    canonical,
    child,
    digest,
    file_hash,
    read_json,
    require,
    safe_path,
    write_new,
)


def service_map(manifest):
    services = {s["id"]: s for s in manifest["services"]}
    return services


def describe(recovery, directory, project, compose_files, backend):
    directory = safe_path(directory)
    require(
        directory.parent == child(recovery.root, "deployments"),
        "deployment_directory_mismatch",
    )
    name = identifier(directory.name)
    _, marker, (manifest, _, _) = recovery.deployment(name)
    require(marker["role"] == "authority", "current_authority_required")
    require(backend in {"local-process", "compose"}, "unsupported_lifecycle_backend")
    identifier(project)
    require(project.startswith("tianshu-synthetic-"), "synthetic_project_required")
    require(0 < len(compose_files) <= 4, "compose_files_required")
    documents, pins, origins = {}, [], {}
    for name in compose_files:
        path = child(directory, name)
        value = read_json(path)
        require(isinstance(value, dict), "invalid_compose_document")
        compose_directory = path.parent
        is_observability = compose_directory == directory / "observability"
        require(
            compose_directory == directory or is_observability,
            "compose_directory_mismatch",
        )
        compose_project = project + ("-obs" if is_observability else "")
        require(isinstance(value.get("services"), dict), "compose_services_required")
        for service, definition in value["services"].items():
            identifier(service)
            require(isinstance(definition, dict), "invalid_compose_service")
            require(service not in documents, "compose_override_forbidden")
            documents[service] = definition
            require(
                (
                    service_map(manifest).get(service, {}).get("product")
                    == "observability"
                )
                == is_observability,
                "compose_service_project_mismatch",
            )
            origins[service] = (compose_project, compose_directory)
        pins.append(
            {"path": name, "sha256": file_hash(path), "project": compose_project}
        )
    require(len({p["path"] for p in pins}) == len(pins), "duplicate_compose_file")
    require(
        set(documents) == set(service_map(manifest)), "compose_service_set_mismatch"
    )
    services = {}
    volumes = [v for v in manifest["volumes"] if v["mount"]]
    for service, definition in documents.items():
        compose_project, compose_directory = origins[service]
        require(
            definition.get("restart", "no") in {"no", "unless-stopped"},
            "unsupported_restart_policy",
        )
        require(
            not definition.get("privileged") and not definition.get("volumes_from"),
            "unbounded_container_ownership",
        )
        require(
            not definition.get("pid") and not definition.get("devices"),
            "unbounded_container_ownership",
        )
        require(
            not definition.get("deploy") and not definition.get("profiles"),
            "implicit_service_topology",
        )
        require(definition.get("scale", 1) == 1, "single_owner_required")
        mounts = []
        for mount in definition.get("volumes", []):
            require(
                isinstance(mount, dict) and mount.get("type") == "bind",
                "bind_mount_required",
            )
            source = mount["source"]
            path = (
                safe_path(source)
                if Path(source).is_absolute()
                else child(compose_directory, source.removeprefix("./"))
            )
            require(
                path.is_relative_to(directory) and path != directory,
                "mount_outside_deployment",
            )
            readonly = mount.get("read_only", False)
            require(type(readonly) is bool, "invalid_mount_mode")
            relative_source = path.relative_to(directory).as_posix()
            if not readonly:
                require(
                    any(
                        v["host_path"] == relative_source
                        and v["owner_service"] == service
                        and v["container_path"] == mount["target"]
                        for v in volumes
                    ),
                    "unregistered_writable_mount",
                )
            mounts.append(
                {
                    "source": relative_source,
                    "target": mount["target"],
                    "read_only": readonly,
                }
            )
        require(
            len({m["target"] for m in mounts}) == len(mounts), "duplicate_mount_target"
        )
        for volume in volumes:
            if volume["owner_service"] == service:
                require(
                    {
                        "source": volume["host_path"],
                        "target": volume["container_path"],
                        "read_only": False,
                    }
                    in mounts,
                    "missing_owner_mount",
                )
        image = definition.get("image")
        require(isinstance(image, str) and image, "image_required")
        if backend == "compose":
            require(
                re.fullmatch(r"[^@\s]+@sha256:[0-9a-f]{64}", image),
                "pinned_image_required",
            )
            product = service_map(manifest)[service]["product"]
            if product != "observability":
                info = manifest["products"][product]["image"]
                require(
                    info["digest"] is not None
                    and image == info["reference"] + "@" + info["digest"],
                    "release_image_mismatch",
                )
        services[service] = {
            "image": image,
            "mounts": sorted(mounts, key=lambda m: m["target"]),
            "project": compose_project,
            "compose_directory": str(compose_directory),
            "compose_files": [
                p["path"] for p in pins if p["project"] == compose_project
            ],
        }
    return {
        "schema_version": "lifecycle/1",
        "environment": "synthetic",
        "backend": backend,
        "scope_id": recovery.scope_id,
        "deployment_id": marker["deployment_id"],
        "directory": str(directory),
        "project": project,
        "release_manifest_sha256": file_hash(child(directory, "release-manifest.json")),
        "inventory_sha256": file_hash(child(directory, "recovery-inventory.json")),
        "compose_files": pins,
        "services": services,
        "versions": {
            key: value["source"]["commit"]
            for key, value in manifest["products"].items()
        }
        | (
            {"observability": manifest["observability"]["source"]["commit"]}
            if "observability" in manifest
            else {}
        ),
    }


def initialize(recovery, directory, project, compose_files, backend, *, execute=False):
    binding = describe(recovery, directory, project, compose_files, backend)
    home = child(safe_path(directory), ".lifecycle", exists=False)
    require(not home.exists(), "lifecycle_must_be_new")
    if execute:
        with recovery.exclusive():
            home.mkdir(mode=0o700)
            write_new(home / "binding.json", canonical(binding))
            write_new(home / "action.lock", b"0")
            (home / "owners").mkdir()
            for service in binding["services"]:
                write_new(home / "owners" / (service + ".lock"), b"0")
    return {
        "operation": "lifecycle-init",
        "mode": "execute" if execute else "plan",
        "binding_sha256": digest(canonical(binding)),
        "activation": "disabled",
    }


def load(recovery, directory, project, expected_sha256):
    directory = safe_path(directory)
    home = child(directory, ".lifecycle")
    binding = read_json(child(home, "binding.json"))
    require(
        file_hash(child(home, "binding.json")) == expected_sha256,
        "lifecycle_binding_mismatch",
    )
    require(
        binding["project"] == project and binding["directory"] == str(directory),
        "deployment_identity_mismatch",
    )
    expected = describe(
        recovery,
        directory,
        project,
        [p["path"] for p in binding["compose_files"]],
        binding["backend"],
    )
    require(binding == expected, "deployment_binding_drift")
    return directory, home, binding
