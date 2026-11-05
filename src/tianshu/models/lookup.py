"""Distinguish a pre-submission lookup from observing an original receipt."""

from tianshu.runtime.errors import AppError


def lookup_phase(value):
    if not isinstance(value, str) or value not in {"pre_submit", "observe"}:
        raise AppError("invalid_model_lookup_phase")
    return value


def missing_receipt(attempt_id):
    # Absence is an observation, not evidence that no provider call happened.
    return AppError("attempt_not_found", status=404, details={
        "attempt_id": attempt_id,
        "phase": "observe",
        "original_observation": "missing",
        "action": "observe_original_attempt",
    })
