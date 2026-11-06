"""Present Runtime's normal and targeted repair eligibility consistently."""

from tianshu.runtime.errors import AppError

REPAIR_CONSUMERS = frozenset({"life_activity", "life_commitment"})


def validate_repair(repair_ref, consumer):
    if repair_ref is None and consumer is None:
        return False
    if (not isinstance(repair_ref, str) or not repair_ref.strip() or len(repair_ref) > 256
            or any(ord(character) < 32 or ord(character) == 127 for character in repair_ref)
            or consumer not in REPAIR_CONSUMERS):
        raise AppError("invalid_result_repair_target", status=400)
    return True


def matching_life_proof(event, consumer, progress, *, generation=None, owner_id=None):
    if not progress or progress.get("disposition") != "done":
        return False
    proof = progress.get("proof") or {}
    owner_key = "activity_id" if consumer == "life_activity" else "commitment_id"
    return ((generation is None or progress.get("data_generation") == generation)
            and progress.get("action_id") == event.get("action_id")
            and progress.get("receipt_id") == event.get("event_id")
            and progress.get("result_revision") == event.get("result_revision")
            and bool(proof.get(owner_key)) and proof.get("last_receipt_id") == event.get("event_id")
            and (owner_id is None or proof.get(owner_key) == owner_id)
            and proof.get("last_result_revision") == event.get("result_revision"))


def eligibility(budget, *, repair_ref=None, consumer=None):
    """Do not let a repair grant change the original normal budget."""
    repair_requested = validate_repair(repair_ref, consumer)
    normal = {key: budget.get(key) for key in (
        "eligible", "reason", "remaining", "failed_executions", "history_complete", "retry_at", "failure_digest")}
    normal["eligible"] = normal["eligible"] is True
    if not normal["eligible"] and not normal["reason"]:
        normal["reason"] = "history_unknown"
    repair = budget.get("repair") if repair_requested else None
    if not isinstance(repair, dict):
        repair = {"eligible": False, "reason": "repair_not_authorized" if repair_requested else "repair_not_requested",
                  "remaining": None, "ref": repair_ref, "consumer": consumer, "retry_at": None}
    else:
        repair = dict(repair)
        repair["eligible"] = repair.get("eligible") is True
        if not repair["eligible"] and not repair.get("reason"):
            repair["reason"] = "repair_not_authorized"
    return {"normal_eligibility": normal, "repair_eligibility": repair,
            "eligible": (repair if repair_requested else normal)["eligible"],
            "reason": (repair if repair_requested else normal).get("reason")}
