"""Bind a Life planning retry to locked facts about its original Model attempt."""
from tianshu.models.ledger import submission_digest
from tianshu.models.rbi_outcome_guard import lock_recovery_facts_on
from tianshu.runtime.errors import AppError
from tianshu.runtime.recovery_policy import validate_model_recovery_facts
from .computation_retry import validate_original_receipt


OBSERVE_AGAIN = frozenset({"recovery_model_receipt_unconfirmed", "recovery_model_outcome_changed",
    "recovery_model_cancelled"})


def interrupted(receipt):
    return bool(receipt and receipt.status == "cancelled" and receipt.error_code == "consumer_interrupted"
        and receipt.cancellation_reason == "consumer_interrupted")


def failed_or_interrupted(receipt):
    return bool(receipt and (receipt.status == "failed" or interrupted(receipt)))


async def lock_original_on(connection, frozen, receipt, data_generation):
    if frozen is None:
        raise AppError("recovery_model_receipt_unconfirmed",status=409)
    facts = await lock_recovery_facts_on(connection, frozen.attempt_id)
    if ((facts and facts.get("cancellation_reason") == "explicit_cancel")
            or getattr(receipt,"cancellation_reason",None) == "explicit_cancel"):
        raise AppError("recovery_model_cancelled",status=409)
    validate_original_receipt(frozen, receipt, data_generation)
    if not receipt.request_digest:
        raise AppError("recovery_model_receipt_unconfirmed", status=409)
    if receipt.request_digest != submission_digest(frozen):
        raise AppError("recovery_model_receipt_mismatch", status=409)
    if receipt.status not in {"failed", "unknown", "cancelled"}:
        raise AppError("recovery_model_outcome_changed",status=409)
    validate_model_recovery_facts(facts,receipt=receipt,expected_status=receipt.status,data_generation=data_generation)
    return facts
