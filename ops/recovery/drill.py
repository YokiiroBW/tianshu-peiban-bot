"""One-use synthetic clone, fixed Compose startup and public readback; originals stay off."""

import os
import sqlite3
import time
from contextlib import ExitStack
from pathlib import Path

from .compose_backend import ComposeBackend, DockerCLI
from .drill_http import check as http_check
from .drill_inputs import isolated_inputs, permit, restoration_facts
from .lifecycle import Deadline, stop_order
from .linux_recovery import registered
from .runtime_identity import load_identity, runtime_lease
from .safety import (
    RecoveryError,
    canonical,
    child,
    copy_new,
    digest,
    file_hash,
    files,
    read_json,
    require,
    sync_tree,
    walk_tree,
    write_new,
)


def clone_binding(root, documents, manifest, value):
    services = {}
    for group, document in documents.items():
        directory = root if group == "core" else root / "observability"
        filename = "compose.json" if group == "core" else "observability/compose.yaml"
        for service, definition in document["services"].items():
            services[service] = {
                "image": value["image_ids"][service],
                "image_id": value["image_ids"][service],
                "repo_digests": [],
                "user": "10001:10001",
                "project": value["projects"][group],
                "compose_directory": str(directory),
                "compose_files": [filename],
                "mounts": sorted(
                    [
                        {
                            "source": Path(m["source"]).relative_to(root).as_posix(),
                            "target": m["target"],
                            "read_only": m["read_only"],
                        }
                        for m in definition["volumes"]
                    ],
                    key=lambda m: m["target"],
                ),
            }
            from .nas_resources import limits

            if limits(definition) is not None:
                services[service]["resource_limits"] = limits(definition)
    return {"services": services}


def _input_provenance(source, runtime, documents, index, clone_root):
    original = read_json(child(source, "bundle-integrity.json"))["files"]
    for group, document in documents.items():
        baseline = runtime["projects"][group]["compose_json"]
        for name, definition in document["services"].items():
            prior = baseline["services"][name]
            original_directory = Path(runtime["projects"][group]["directory"])
            expected_mounts = []
            for mount in prior.get("volumes", []):
                path = Path(mount["source"])
                if not path.is_absolute():
                    path = original_directory / path
                expected_mounts.append(
                    (
                        path.relative_to(source).as_posix(),
                        mount["target"],
                        mount.get("read_only", False),
                    )
                )
            # Clone source paths may change only by replacing the deployment root.
            require(
                sorted(expected_mounts)
                == sorted(
                    (
                        Path(m["source"]).relative_to(clone_root).as_posix(),
                        m["target"],
                        m["read_only"],
                    )
                    for m in definition.get("volumes", [])
                ),
                "drill_mount_provenance_mismatch",
            )
            if "cpuset" in prior:
                from .nas_resources import limits

                require(
                    limits(definition) == limits(prior),
                    "drill_resource_provenance_mismatch",
                )
            for field in (
                "entrypoint",
                "command",
                "environment",
                "healthcheck",
                "security_opt",
            ):
                require(
                    definition.get(field) == prior.get(field),
                    "drill_command_provenance_mismatch",
                )
    for name, checksum in index["files"].items():
        if name.startswith(("tools/", "contracts/", "observability/code/")):
            require(original.get(name) == checksum, "drill_code_provenance_mismatch")
        if name.startswith(
            ("private/", "observability/private/", "observability-input/secrets/")
        ) or name.endswith((".key", ".env")):
            require(
                original.get(name) != checksum, "drill_requires_distinct_private_inputs"
            )


def _unused(docker, projects, root):
    ids = docker.run("container", "ls", "--all", "--quiet", "--no-trunc").split()
    import json

    for offset in range(0, len(ids), 25):
        for c in json.loads(
            docker.run("container", "inspect", *ids[offset : offset + 25])
        ):
            require(
                (c["Config"].get("Labels") or {}).get("com.docker.compose.project")
                not in projects,
                "drill_project_in_use",
            )
            for mount in c["Mounts"]:
                if mount["Type"] == "bind":
                    path = Path(mount["Source"])
                    require(
                        not (path.is_relative_to(root) or root.is_relative_to(path)),
                        "drill_path_already_mounted",
                    )
    for project in projects:
        require(
            not docker.run(
                "network",
                "ls",
                "--filter",
                "label=com.docker.compose.project=" + project,
                "--quiet",
            ).strip(),
            "drill_network_in_use",
        )


def _stop(backend):
    # Partial compose-up failure may leave fewer than nine containers. Validate every
    # present owner first, then signal precise IDs. Never discard a malformed owner.
    backend.inspect(allow_missing=True)
    present = list(backend.current)
    for name in present:
        backend.docker.run(
            "container", "update", "--restart=no", backend.identities[name][0]
        )
    backend.restart_disabled = True
    for name in stop_order(backend.binding):
        if name not in present:
            continue
        backend.inspect(allow_missing=True)
        state = backend.current[name]["State"]
        require(
            not state["Paused"] and not state["Restarting"], "container_not_stoppable"
        )
        if state["Running"]:
            backend.docker.run(
                "container", "kill", "--signal=SIGTERM", backend.identities[name][0]
            )
    while True:
        backend.budget.check()
        backend.inspect(allow_missing=True)
        require(set(backend.current) == set(present), "drill_owner_disappeared")
        if not any(c["State"]["Running"] for c in backend.current.values()):
            break
        time.sleep(0.05)
    for c in backend.current.values():
        state = c["State"]
        require(
            state["Status"] == "exited"
            and state["ExitCode"] == 0
            and not any(
                state[k] for k in ("OOMKilled", "Dead", "Error", "Paused", "Restarting")
            ),
            "owner_exit_unconfirmed",
        )


def run(
    recovery,
    permit_path,
    permit_sha256,
    *,
    execute=False,
    docker_executable=None,
    cancel=None,
):
    value = permit(recovery, permit_path, permit_sha256)
    source, restored, clone = (
        Path(value[k])
        for k in ("source_directory", "restored_directory", "drill_directory")
    )
    registration, runtime, binding = registered(
        recovery, source, value["registration_sha256"]
    )
    require(
        registration["authority_id"] == value["authority_id"]
        and registration["runtime_identity"]["sha256"]
        == value["runtime_identity_sha256"],
        "drill_authority_binding_mismatch",
    )
    require(
        not set(value["projects"].values())
        & {p["name"] for p in runtime["projects"].values()},
        "drill_original_project_forbidden",
    )
    manifest = read_json(child(source, "release-manifest.json"))
    require(
        value["versions"]
        == {p: v["source"]["commit"] for p, v in manifest["products"].items()},
        "drill_source_version_mismatch",
    )
    require(
        value["image_ids"]
        == {s: v["image_id"] for s, v in runtime["services"].items()},
        "drill_image_identity_mismatch",
    )
    from .nas_resources import profile_for, check_host

    resource_profile = profile_for(source, runtime)
    index, documents = isolated_inputs(
        value, manifest, resource_profile=resource_profile
    )
    _input_provenance(source, runtime, documents, index, clone)
    marker = read_json(child(restored, "RESTORE.json"))
    require(
        marker.get("snapshot_sha256") == value["snapshot_sha256"],
        "drill_snapshot_binding_mismatch",
    )
    require(
        marker.get("authority_deployment_id") == value["authority_id"],
        "drill_authority_binding_mismatch",
    )
    claim = child(
        recovery.root, "drill-claims/" + value["permit_id"] + ".json", exists=False
    )
    require(not claim.exists(), "drill_permit_already_claimed")
    result = {
        "operation": "drill-clone",
        "mode": "execute" if execute else "plan",
        "drill_only": True,
        "release_ready": False,
        "original_restore_activation": False,
        "activation": "disabled",
        "functional_assertions": [],
        "service_coverage": {
            "mode": "staged",
            "all_nine_simultaneous": False,
            "observability": {
                "owners": sorted(documents["observability"]["services"]),
                "healthy_before_core_start": False,
                "stopped_exit_code_zero_before_core_start": False,
            },
            "core": {
                "owners": sorted(documents["core"]["services"]),
                "healthy_during_functional_readback": False,
            },
        },
        "linux_executed": False,
    }
    if not execute:
        return result | {"status": "planned", "actual_owners_and_facts": "not_checked"}
    remaining = min(value["max_runtime_seconds"], value["expires_at"] - time.time())
    require(remaining > 65, "drill_shutdown_reserve_required")
    total_end = time.monotonic() + remaining
    cleanup_reserve = 90 if resource_profile is not None else 60
    require(remaining > cleanup_reserve, "drill_insufficient_cleanup_budget")
    work = Deadline(remaining - cleanup_reserve, cancel)
    docker = DockerCLI(docker_executable, "unix:///var/run/docker.sock", work)
    check_host(docker, resource_profile)
    original = ComposeBackend(source, source / ".lifecycle", binding, work, docker)
    original.identities = {
        s: tuple(i) for s, i in registration["initial_owner_ids"].items()
    }
    original.restart_disabled = True
    backend = None
    failure = None
    with runtime_lease(source), ExitStack() as clone_leases:
        permit(recovery, permit_path, permit_sha256)
        pin = registration["runtime_identity"]
        load_identity(source, child(source, pin["path"]), pin["sha256"], observed=True)
        original.assert_stopped()
        facts = restoration_facts(recovery, source, restored, value["authority_id"])
        require(
            digest(canonical(facts)) == value["verification_sha256"],
            "drill_restoration_facts_mismatch",
        )
        _unused(docker, set(value["projects"].values()), clone)
        _unused(docker, set(), restored)
        restored_names = files(restored, max_files=recovery.max_files)
        require(
            len(set(restored_names) | set(index["files"])) <= recovery.max_files,
            "file_count_limit",
        )
        require(
            sum(child(restored, n).stat().st_size for n in restored_names)
            + sum(
                child(Path(value["inputs_directory"]), n).stat().st_size
                for n in index["files"]
            )
            <= recovery.max_bytes,
            "total_size_limit",
        )
        # Bound one-use admission is durable before clone creation or any startup.
        write_new(
            claim,
            canonical(
                {
                    "permit_sha256": permit_sha256,
                    "status": "claimed",
                    "activation": "disabled",
                }
            ),
        )
        clone.mkdir(mode=0o700)
        try:
            clone_leases.enter_context(runtime_lease(clone))
            owner_paths = {
                r["path"]
                for r in read_json(child(restored, "recovery-inventory.json"))[
                    "resources"
                ]
                if r["kind"] == "owner_lock"
            }
            sqlite_transients = {
                r["path"] + suffix
                for r in read_json(child(restored, "recovery-inventory.json"))[
                    "resources"
                ]
                if r["kind"] == "sqlite"
                for suffix in ("-wal", "-shm")
            }
            for directory, dirs, _ in walk_tree(restored):
                for name in dirs:
                    relative = (Path(directory) / name).relative_to(restored).as_posix()
                    child(clone, relative, exists=False).mkdir(
                        parents=True, exist_ok=True
                    )
            for name in files(restored, max_files=recovery.max_files):
                work.check()
                if (
                    name == ".deployment.json"
                    or name in owner_paths
                    or name in sqlite_transients
                ):
                    continue
                copy_new(
                    child(restored, name),
                    child(clone, name, exists=False),
                    limit=recovery.max_bytes,
                    check_cancel=work.check,
                )
            for volume in manifest["volumes"]:
                if volume["kind"] == "directory":
                    child(clone, volume["host_path"], exists=False).mkdir(
                        parents=True, exist_ok=True
                    )
            for name in owner_paths:
                write_new(child(clone, name, exists=False), b"0")
            inputs = Path(value["inputs_directory"])
            for name in index["files"]:
                target = child(clone, name, exists=False)
                if target.exists():
                    require(
                        file_hash(target) == index["files"][name],
                        "drill_input_overwrites_state",
                    )
                else:
                    copy_new(
                        child(inputs, name),
                        target,
                        limit=recovery.max_bytes,
                        check_cancel=work.check,
                    )
            require(
                isolated_inputs(value, manifest, resource_profile=resource_profile)
                == (index, documents),
                "drill_inputs_changed_during_copy",
            )
            for name, expected in index["files"].items():
                require(
                    file_hash(child(clone, name)) == expected,
                    "drill_copied_input_mismatch",
                )
            # Only new clone descendants get runtime ownership; no original path is changed.
            for directory, dirs, names in walk_tree(clone):
                for path in [Path(directory), *(Path(directory) / n for n in names)]:
                    if path.name == ".runtime-owner.lock" and path.parent == clone:
                        continue
                    os.chown(path, 10001, 10001)
                    os.chmod(path, 0o750 if path.is_dir() else 0o640)
            write_new(
                clone / ".drill.json",
                canonical(
                    {
                        "role": "drill_only",
                        "authority": False,
                        "permit_sha256": permit_sha256,
                    }
                ),
            )
            sync_tree(clone)
            original.assert_stopped()
            require(
                restoration_facts(recovery, source, restored, value["authority_id"])
                == facts,
                "drill_original_changed",
            )
            clone_owner = clone_binding(clone, documents, manifest, value)
            backend = ComposeBackend(clone, clone, clone_owner, work, docker)
            for group, filename in (
                ("observability", "observability/compose.yaml"),
                ("core", "compose.json"),
            ):
                work.check()
                docker.run(
                    "compose",
                    "--project-name",
                    value["projects"][group],
                    "--project-directory",
                    str((clone / filename).parent),
                    "--file",
                    str(clone / filename),
                    "up",
                    "--detach",
                    "--no-build",
                    "--pull",
                    "never",
                )
                services = set(documents[group]["services"])
                while True:
                    work.check()
                    backend.inspect(allow_missing=True)
                    require(services <= set(backend.current), "missing_project_container")
                    require(
                        all(backend.current[s]["State"]["Running"] for s in services),
                        "drill_product_not_running",
                    )
                    if all(
                        backend.current[s]["State"].get("Health", {}).get("Status")
                        == "healthy"
                        for s in services
                        if documents[group]["services"][s].get("healthcheck")
                    ):
                        break
                    time.sleep(0.1)
                if group == "observability":
                    result["service_coverage"]["observability"][
                        "healthy_before_core_start"
                    ] = True
                    for service in stop_order(backend.binding):
                        if service in services:
                            backend.stop(service, allow_missing=True)
                    while True:
                        work.check()
                        backend.inspect(allow_missing=True)
                        require(services <= set(backend.current), "drill_owner_disappeared")
                        states = [backend.current[s]["State"] for s in services]
                        if not any(
                            state["Running"]
                            or state["Restarting"]
                            or state["Paused"]
                            for state in states
                        ):
                            require(
                                all(
                                    state["Status"] == "exited"
                                    and state["ExitCode"] == 0
                                    and not state["OOMKilled"]
                                    and not state["Dead"]
                                    and not state["Error"]
                                    for state in states
                                ),
                                "owner_exit_unconfirmed",
                            )
                            break
                        time.sleep(0.05)
                    result["service_coverage"]["observability"][
                        "stopped_exit_code_zero_before_core_start"
                    ] = True
                else:
                    require(
                        set(backend.current) == set(backend.binding["services"]),
                        "missing_project_container",
                    )
                    result["service_coverage"]["core"][
                        "healthy_during_functional_readback"
                    ] = True
            for assertion in index["assertions"]:
                original.assert_stopped()
                _unused(docker, set(), restored)
                result["functional_assertions"].append(
                    http_check(clone, assertion, work)
                )
        except (RecoveryError, OSError, ValueError, KeyError, TypeError, sqlite3.Error):
            failure = "drill_failed_or_cancelled"
        finally:
            cleanup = Deadline(
                max(0.1, min(cleanup_reserve, total_end - time.monotonic()))
            )
            cleanup_docker = DockerCLI(
                docker_executable, "unix:///var/run/docker.sock", cleanup
            )
            if backend is not None:
                backend.budget, backend.docker = cleanup, cleanup_docker
                try:
                    _stop(backend)
                except (
                    RecoveryError,
                    OSError,
                    ValueError,
                    KeyError,
                    TypeError,
                    sqlite3.Error,
                ):
                    failure = "stop_unconfirmed"
            original.budget, original.docker = cleanup, cleanup_docker
            try:
                original.assert_stopped()
                _unused(cleanup_docker, set(), restored)
                require(
                    restoration_facts(recovery, source, restored, value["authority_id"])
                    == facts,
                    "drill_original_changed",
                )
            except (
                RecoveryError,
                OSError,
                ValueError,
                KeyError,
                TypeError,
                sqlite3.Error,
            ):
                failure = "original_state_unconfirmed"
            kinds = {r["id"] for r in result["functional_assertions"]}
            missing = sorted(
                {
                    "data_readback",
                    "source_revoked",
                    "model_revoked",
                    "forgotten",
                    "unknown_no_resend",
                }
                - kinds
            )
            products = {r["service"] for r in result["functional_assertions"]}
            result.update(
                status=failure
                or (
                    "partial_functional_coverage"
                    if missing
                    or products != set(manifest["products"])
                    or not all(
                        result["service_coverage"]["observability"][key]
                        for key in (
                            "healthy_before_core_start",
                            "stopped_exit_code_zero_before_core_start",
                        )
                    )
                    or not result["service_coverage"]["core"][
                        "healthy_during_functional_readback"
                    ]
                    else "drill_passed"
                ),
                linux_executed=True,
                missing_semantics=missing,
                missing_products=sorted(set(manifest["products"]) - products),
                activation="stop_unconfirmed"
                if failure in {"stop_unconfirmed", "original_state_unconfirmed"}
                else "disabled",
            )
            write_new(clone / "drill-result.json", canonical(result))
    return result
