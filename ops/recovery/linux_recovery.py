"""DEP-G-bound normal stop and disabled recovery of actual synthetic product volumes."""

import uuid

from .compose_backend import ComposeBackend, DockerCLI
from .lifecycle import Deadline, operate
from .lifecycle_binding import describe, load
from .manifest import release
from .product_inventory import inventory
from .runtime_identity import load_identity, runtime_lease
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

COMPOSE_FILES = ["compose.json", "observability/compose.yaml"]


def identity_pin(directory, identity_path, checksum):
    path = safe_path(identity_path)
    require(path.is_relative_to(directory), "identity_outside_deployment")
    return {"path": path.relative_to(directory).as_posix(), "sha256": checksum}


def prepare(
    recovery,
    directory,
    identity_path,
    identity_sha256,
    authority_id,
    *,
    execute=False,
    docker_executable=None,
    timeout=120,
):
    directory = safe_path(directory)
    require(
        directory.parent == child(recovery.root, "deployments"),
        "deployment_directory_mismatch",
    )
    require(str(uuid.UUID(authority_id)) == authority_id, "invalid_authority_id")
    for name in (
        ".deployment.json",
        "recovery-inventory.json",
        ".lifecycle",
        ".recovery-registration.json",
    ):
        require(
            not child(directory, name, exists=False).exists(), "authority_must_be_new"
        )
    pin = identity_pin(directory, identity_path, identity_sha256)
    runtime = load_identity(directory, identity_path, identity_sha256)
    require(
        runtime["authority"] is None and runtime["recovery_inventory"] is None,
        "initial_synthetic_registration_required",
    )
    manifest = release(read_json(child(directory, "release-manifest.json")))
    document = inventory(directory, max_files=recovery.max_files)
    marker = {
        "schema_version": "1.0.0",
        "scope_id": recovery.scope_id,
        "deployment_id": authority_id,
        "environment": "synthetic",
        "role": "authority",
        "status": "offline",
    }
    binding = describe(
        recovery,
        directory,
        runtime["project_name"],
        COMPOSE_FILES,
        "compose",
        runtime_pin=pin,
        registration=(marker, manifest, digest(canonical(document))),
    )
    result = {
        "operation": "linux-prepare",
        "mode": "execute" if execute else "plan",
        "activation": "disabled",
        "runtime_identity_sha256": identity_sha256,
        "source_authority": "explicit_synthetic_registration_only",
        "runtime_stop_verified": False,
    }
    if not execute:
        return result
    budget = Deadline(timeout)
    with runtime_lease(directory), recovery.exclusive():
        load_identity(directory, identity_path, identity_sha256, observed=True)
        backend = ComposeBackend(
            directory,
            directory / ".lifecycle",
            binding,
            budget,
            DockerCLI(docker_executable, "unix:///var/run/docker.sock", budget),
        )
        backend.inspect()  # All nine actual owners and images before registration writes.
        require(
            inventory(directory, max_files=recovery.max_files) == document,
            "inventory_changed_during_registration",
        )
        for name in (
            ".deployment.json",
            "recovery-inventory.json",
            ".lifecycle",
            ".recovery-registration.json",
        ):
            require(
                not child(directory, name, exists=False).exists(),
                "authority_must_be_new",
            )
        write_new(directory / ".deployment.json", canonical(marker))
        write_new(directory / "recovery-inventory.json", canonical(document))
        # initialize uses the same scope lease, so persist its exact already-validated result here.
        home = directory / ".lifecycle"
        home.mkdir(mode=0o700)
        write_new(home / "binding.json", canonical(binding))
        write_new(home / "action.lock", b"0")
        (home / "owners").mkdir()
        for service in binding["services"]:
            write_new(home / "owners" / (service + ".lock"), b"0")
        registration = {
            "schema_version": "dep-j-registration/1",
            "scope_id": recovery.scope_id,
            "authority_id": authority_id,
            "directory": str(directory),
            "runtime_identity": pin,
            "binding_sha256": digest(canonical(binding)),
            "inventory_sha256": digest(canonical(document)),
            "activation": "disabled",
            "initial_owner_ids": {s: list(i) for s, i in backend.identities.items()},
        }
        write_new(directory / ".recovery-registration.json", canonical(registration))
        return result | {
            "registration_sha256": digest(canonical(registration)),
            "binding_sha256": registration["binding_sha256"],
            "status": "registered",
        }


def registered(recovery, directory, registration_sha256):
    directory = safe_path(directory)
    path = child(directory, ".recovery-registration.json")
    require(file_hash(path) == registration_sha256, "registration_digest_mismatch")
    registration = read_json(path)
    require(
        registration["scope_id"] == recovery.scope_id
        and registration["directory"] == str(directory)
        and registration["activation"] == "disabled",
        "registration_scope_mismatch",
    )
    recovery.deployment(
        directory.name, authority=True, expected_id=registration["authority_id"]
    )
    pin = registration["runtime_identity"]
    runtime = load_identity(directory, child(directory, pin["path"]), pin["sha256"])
    _, _, binding = load(
        recovery, directory, runtime["project_name"], registration["binding_sha256"]
    )
    require(
        binding["runtime_identity"] == pin
        and binding["inventory_sha256"] == registration["inventory_sha256"],
        "registration_binding_mismatch",
    )
    return registration, runtime, binding


def rehearse(
    recovery,
    directory,
    registration_sha256,
    backup,
    target,
    *,
    execute=False,
    timeout=300,
    docker_executable=None,
    cancel=None,
):
    directory = safe_path(directory)
    registration, runtime, binding = registered(
        recovery, directory, registration_sha256
    )
    require(target != directory.name, "new_restore_target_required")
    require(
        not child(recovery.root, "deployments/" + target, exists=False).exists(),
        "destination_exists",
    )
    plan = operate(
        recovery,
        directory,
        runtime["project_name"],
        registration["binding_sha256"],
        "backup",
        backup=backup,
    )
    result = {
        "operation": "linux-rehearse",
        "mode": "execute" if execute else "plan",
        "activation": "disabled",
        "release_ready": False,
        "nas": False,
        "backup_plan": plan,
        "functional_readback": "not_run_requires_drill_permit",
        "authority_semantics": "all_state_fingerprints_and_memory_guard_only",
    }
    if not execute:
        return result
    budget = Deadline(timeout, cancel)
    with runtime_lease(directory):
        pin = registration["runtime_identity"]
        load_identity(
            directory, child(directory, pin["path"]), pin["sha256"], observed=True
        )
        # Pin the original owners even when G had image evidence but no obs container evidence.
        checker = ComposeBackend(
            directory,
            directory / ".lifecycle",
            binding,
            budget,
            DockerCLI(docker_executable, "unix:///var/run/docker.sock", budget),
        )
        checker.identities = {
            s: tuple(i) for s, i in registration["initial_owner_ids"].items()
        }
        checker.inspect()
        common = dict(
            execute=True,
            timeout=max(0.001, budget.ends - __import__("time").monotonic()),
            cancel=cancel,
            docker_executable=docker_executable,
            docker_endpoint="unix:///var/run/docker.sock",
            _runtime_lease_held=True,
            _owner_identities=registration["initial_owner_ids"],
        )
        snapshot = operate(
            recovery,
            directory,
            runtime["project_name"],
            registration["binding_sha256"],
            "backup",
            backup=backup,
            **common,
        )
        checker.assert_stopped()
        budget.check()
        common["timeout"] = max(0.001, budget.ends - __import__("time").monotonic())
        checksum = snapshot["result"]["snapshot_sha256"]
        restored = operate(
            recovery,
            directory,
            runtime["project_name"],
            registration["binding_sha256"],
            "restore",
            backup=backup,
            snapshot_sha256=checksum,
            target=target,
            authority_id=registration["authority_id"],
            **common,
        )
        checker.assert_stopped()
        from .drill_inputs import restoration_facts

        facts = restoration_facts(
            recovery,
            directory,
            recovery.root / "deployments" / target,
            registration["authority_id"],
        )
        checker.assert_stopped()
        return result | {
            "status": "disabled_restore_complete",
            "backup": snapshot,
            "restore": restored,
            "runtime_owners_stopped": 9,
            "linux_stop_backup_restore": True,
            "product_functional_restore": False,
            "verification_sha256": digest(canonical(facts)),
        }
