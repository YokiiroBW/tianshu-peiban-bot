"""Shared eligibility for a proposed, accepted, or executing Life activity."""
from dataclasses import dataclass
from datetime import datetime

from tianshu.runtime.errors import AppError
from .continuation import action_resolved, resume_step


@dataclass(frozen=True, slots=True)
class Eligibility:
    eligible: bool
    reason: str
    revision: int | None = None
    step: dict | None = None


def background_work(checkpoint):
    """Work with no role-focus requirement can continue during rest."""
    checkpoint = checkpoint or {}
    if checkpoint.get("execution_mode") == "focus":
        return False
    return checkpoint.get("execution_mode") == "background" or bool(checkpoint.get("capability_id"))


def evaluate(*, goal=None, activity=None, expected_revision=None, continuation_key=None,
             available=None, now: datetime | None = None, resume=False):
    if goal is not None and goal.get("state") != "active":
        return Eligibility(False, "goal_not_active", goal.get("revision"))
    if activity is None:
        return Eligibility(True, "eligible")
    version = activity.get("revision")
    if expected_revision is not None and version != expected_revision:
        return Eligibility(False, "activity_revision_changed", version)
    if resume:
        if (activity.get("state") != "dormant" or activity.get("waiting_reason") not in
                {"continuation_available", "day_finished", "follow_up_available"}):
            return Eligibility(False, "activity_not_resumable", version)
        if not action_resolved(activity):
            return Eligibility(False, "activity_action_unresolved", version)
        if (now is not None and not background_work(activity.get("checkpoint_data"))
                and activity.get("protected_until") and activity["protected_until"] > now):
            return Eligibility(False, "activity_protected", version)
        if activity.get("waiting_reason") == "day_finished" and not activity.get("action_id"):
            checkpoint = activity.get("checkpoint_data") or {}
            step = {"capability_id": checkpoint.get("capability_id"), "arguments": checkpoint.get("arguments", {})}
        else:
            try:
                step = resume_step(activity, continuation_key)
            except AppError as error:
                return Eligibility(False, error.code, version)
    else:
        if activity.get("state") not in {"planned", "running", "dormant"}:
            return Eligibility(False, "activity_not_executable", version)
        checkpoint = activity.get("checkpoint_data") or {}
        step = {"capability_id": checkpoint.get("capability_id"),
                "arguments": checkpoint.get("arguments", {})}
    if available is not None and step.get("capability_id") and step["capability_id"] not in available:
        return Eligibility(False, "capability_unavailable", version, step)
    return Eligibility(True, "eligible", version, step)
