"""Non-content evidence explaining why a new Life decision was requested."""
from tianshu.models.ledger import submission_digest


def decision_trace(mark, previous, *, day, material, recovery_material, now,
                   review_at, supersede_reason, repair_ready, frozen):
    if not mark.get("generation_key"):
        trigger = "initial"
    elif supersede_reason == "life_window_expired":
        trigger = "business_window_expired"
    elif supersede_reason:
        trigger = "original_superseded"
    elif (mark.get("last_error") and previous
            and previous.get("recovery_version") == recovery_material):
        trigger = "original_retry_or_correction"
    elif previous and previous.get("day") != day:
        trigger = "local_day_changed"
    elif mark.get("decision_fingerprint") == material and review_at is not None and review_at <= now:
        trigger = "scheduled_review"
    elif mark.get("decision_fingerprint") != material:
        trigger = "semantic_or_lifecycle_change"
    elif repair_ready:
        trigger = "repair"
    else:
        trigger = "fresh_evaluation"
    return {"version":1,"trigger":trigger,"observed_at":now.isoformat(),
        "semantic_fingerprint":material,"recovery_fingerprint":recovery_material,
        "previous_decision_fingerprint":mark.get("decision_fingerprint"),
        "decision_review_at":review_at.isoformat() if review_at is not None else None,
        "supersede_reason":supersede_reason,"model_request_digest":submission_digest(frozen)}
