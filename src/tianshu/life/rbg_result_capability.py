"""Recognize explicit recoverable parse receipts without inferring success."""
from .continuation import known_read_failure, recoverable_action as existing_recovery


def recoverable_action(capability_id, result):
    existing = existing_recovery(capability_id, result)
    if existing:
        return existing
    body = result.result
    if (capability_id == "video.observe" and result.action_id and result.attempt_id and result.receipt_id
            and known_read_failure(capability_id, result) and result.execution == "failed"
            and body.get("phase") == "parse" and body.get("outcome") == "failed"
            and body.get("recovery") == "resume_parse" and body.get("parser_version") == 2
            and isinstance(body.get("model_attempt_id"), str) and body["model_attempt_id"]
            and isinstance(body.get("segment"), str) and body["segment"]):
        return "resume_parse"
    return None
