"""Persist explicit Models waits without upgrading independent rollback evidence."""

from tianshu.runtime.errors import AppError
from tianshu.runtime.recovery_policy import (
    recovery_wait_from_wire,
    recovery_wait_retryable,
    recovery_wait_to_wire,
)


def encode_wait(wait):
    local = getattr(wait, "transaction_rollback_confirmed", None)
    remote = wait.remote_rollback_confirmed
    if (local is not None and type(local) is not bool) or (remote is not None and type(remote) is not bool):
        raise ValueError("invalid_recovery_wait_evidence")
    return {"envelope": recovery_wait_to_wire(wait),
            "local_rollback_confirmed": local, "remote_rollback_confirmed": remote}


def decode_wait(value):
    if not isinstance(value, dict) or set(value) != {
        "envelope", "local_rollback_confirmed", "remote_rollback_confirmed"
    }:
        raise ValueError("invalid_recovery_wait_evidence")
    local, remote = value["local_rollback_confirmed"], value["remote_rollback_confirmed"]
    if (local is not None and type(local) is not bool) or (remote is not None and type(remote) is not bool):
        raise ValueError("invalid_recovery_wait_evidence")
    wait = recovery_wait_from_wire(value["envelope"])
    wait.remote_rollback_confirmed = remote
    if local is not None:
        wait.transaction_rollback_confirmed = local
    if recovery_wait_to_wire(wait) != value["envelope"]:
        raise ValueError("invalid_recovery_wait_evidence")
    return wait


def wait_error(row):
    """Return a nonterminal observation, never a fabricated model receipt."""
    if row.get("status") != "queued":
        return None
    state = row.get("recovery_state")
    if state not in {"scheduled", "observe_only"}:
        return None
    try:
        wait = decode_wait(row.get("recovery_wait"))
        if state == "observe_only" and recovery_wait_retryable(wait):
            raise ValueError("observation_required")
        return wait
    except (TypeError, ValueError):
        return AppError("model_prestart_observation_required", status=503,
                        details={"attempt_id": row["attempt_id"], "action": "observe_original_attempt"})


def safely_retryable(value):
    try:
        return recovery_wait_retryable(decode_wait(value))
    except (TypeError, ValueError):
        return False


def preserve_wait_safety(row, wait):
    """An unrelated later check cannot erase the original negative evidence."""
    if row.get("status") != "queued" or row.get("recovery_state") != "observe_only":
        return
    try:
        original = decode_wait(row.get("recovery_wait"))
    except (TypeError, ValueError):
        original = None
    if original is not None and original.remote_rollback_confirmed is False:
        wait.remote_rollback_confirmed = False
        return
    raise AppError("model_prestart_observation_required", status=503,
                   details={"attempt_id": row["attempt_id"], "action": "observe_original_attempt"})
