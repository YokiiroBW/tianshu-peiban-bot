"""Stop, confirm, snapshot. All operations leave a persistent disabled maintenance gate."""

import math
import time
import uuid

from .compose_backend import ComposeBackend, DockerCLI
from .engine import Control
from .lifecycle_binding import load
from .process_backend import ProcessBackend
from .safety import canonical, child, lease, read_json, require, write_new


class Deadline(Control):
    def __init__(self, seconds, cancel=None):
        require(
            type(seconds) in {float, int}
            and math.isfinite(seconds)
            and 0 < seconds <= 3600,
            "invalid_lifecycle_deadline",
        )
        super().__init__(cancel=cancel)
        self.ends = time.monotonic() + seconds

    def check(self, point=None):
        super().check(point)
        require(time.monotonic() < self.ends, "lifecycle_timeout")


def stop_order(binding):
    # Producers first, then collector/guard, then stores. No query/204 is a deletion grant.
    preferred = [
        "platform",
        "companion",
        "memory",
        "gateway",
        "obs-vector",
        "obs-guard",
        "obs-grafana",
        "obs-prometheus",
        "obs-loki",
    ]
    require(set(binding["services"]) <= set(preferred), "unknown_stop_order")
    return [s for s in preferred if s in binding["services"]]


def operate(
    recovery,
    directory,
    project,
    binding_sha256,
    operation,
    *,
    execute=False,
    timeout=60,
    cancel=None,
    docker_executable=None,
    docker_endpoint=None,
    backup=None,
    snapshot_sha256=None,
    target=None,
    authority_id=None,
    candidate=None,
    compatibility=None,
    update_id=None,
):
    budget = Deadline(timeout, cancel)
    directory, home, binding = load(recovery, directory, project, binding_sha256)
    require(
        operation
        in {"backup", "restore", "verify-restored", "prepare-update", "rollback-code"},
        "unsupported_lifecycle_operation",
    )
    order = stop_order(binding)
    plan = {
        "operation": "lifecycle-" + operation,
        "mode": "plan",
        "environment": "synthetic_only",
        "binding_sha256": binding_sha256,
        "projects": sorted({s["project"] for s in binding["services"].values()}),
        "stop_order": order,
        "activation": "disabled",
        "restart": "disabled_until_separate_acceptance",
        "data_restore": operation == "restore",
    }
    if operation in {"restore", "verify-restored"}:
        require(authority_id == binding["deployment_id"], "current_authority_required")
        require(
            target is not None and target != directory.name,
            "new_restore_target_required",
        )
        if operation == "restore":
            require(
                not child(
                    recovery.root, "deployments/" + target, exists=False
                ).exists(),
                "destination_exists",
            )
    # Validate names, receipts and compatibility before stopping healthy writers.
    if operation == "backup":
        plan["recovery_plan"] = recovery.backup(directory.name, backup)
    elif operation == "restore":
        plan["recovery_plan"] = recovery.restore(
            backup, snapshot_sha256, target, directory.name, authority_id
        )
    elif operation == "verify-restored":
        _, marker, _ = recovery.deployment(target)
        require(marker["role"] == "restored", "restored_target_required")
    else:
        plan["recovery_plan"] = recovery.select_code(
            directory.name,
            candidate,
            compatibility,
            update_id,
            backup,
            rollback=operation == "rollback-code",
        )
    if not execute:
        return plan
    budget.check()
    backend = (
        ProcessBackend(directory, home, binding, budget)
        if binding["backend"] == "local-process"
        else ComposeBackend(
            directory,
            home,
            binding,
            budget,
            DockerCLI(docker_executable, docker_endpoint, budget),
        )
    )
    prior_control, prior_guard = recovery.control, recovery.lifecycle_guard
    event_id = uuid.uuid4().hex
    try:
        with lease(child(home, "action.lock")):
            budget.check()
            backend.inspect()  # Entire owner set must validate before the first mutation.
            # Re-check immutable files inside the exclusive admission boundary.
            load(recovery, directory, project, binding_sha256)
            gate = home / "MAINTENANCE.json"
            if gate.exists():
                require(
                    read_json(gate)
                    == {"binding_sha256": binding_sha256, "activation": "disabled"},
                    "maintenance_binding_mismatch",
                )
            else:
                write_new(
                    gate,
                    canonical(
                        {"binding_sha256": binding_sha256, "activation": "disabled"}
                    ),
                )
            write_new(
                home / "events" / (event_id + ".started.json"),
                canonical(plan | {"mode": "execute"}),
            )
            backend.disable_restart()
            for service in order:
                budget.check()
                backend.stop(service)
                while not backend.stopped(service):
                    budget.check()
                    time.sleep(0.02)
            backend.reserve()

            def guard():
                budget.check()
                load(recovery, directory, project, binding_sha256)
                backend.assert_stopped()

            recovery.control, recovery.lifecycle_guard = budget, guard
            guard()
            if operation == "backup":
                result = recovery.backup(directory.name, backup, execute=True)
            elif operation == "restore":
                result = recovery.restore(
                    backup,
                    snapshot_sha256,
                    target,
                    directory.name,
                    authority_id,
                    execute=True,
                )
                result["verification"] = recovery.verify_restored(
                    target, directory.name, authority_id
                )
            elif operation == "verify-restored":
                result = recovery.verify_restored(target, directory.name, authority_id)
            else:
                result = recovery.select_code(
                    directory.name,
                    candidate,
                    compatibility,
                    update_id,
                    backup,
                    rollback=operation == "rollback-code",
                    execute=True,
                )
            guard()
            write_new(
                home / "events" / (event_id + ".complete.json"),
                canonical(
                    {
                        "binding_sha256": binding_sha256,
                        "result": result,
                        "activation": "disabled",
                    }
                ),
            )
            return plan | {
                "mode": "execute",
                "status": "complete",
                "result": result,
                "runtime_owners_stopped": len(order),
                "backend": binding["backend"],
                "linux_container_verified": False,
            }
    finally:
        recovery.control, recovery.lifecycle_guard = prior_control, prior_guard
        backend.close()
