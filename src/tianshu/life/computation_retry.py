"""Life's pure-generation observations and source-bound recipe audit, without scheduling."""

from datetime import datetime

from tianshu.conversation.codec import document
from tianshu.models.types import ModelReceipt
from tianshu.models.retry import transient_model_failure
from tianshu.runtime.errors import AppError


def finished_unknown(receipt, attempt_id, data_generation=None):
    """An actual Models receipt proves that this local pure attempt ended."""
    return bool(isinstance(receipt, ModelReceipt) and receipt.attempt_id == attempt_id and receipt.status == "unknown"
        and isinstance(receipt.finished_at, datetime) and receipt.finished_at.tzinfo is not None
        and receipt.finished_at.utcoffset() is not None
        and (data_generation is None or receipt.data_generation == data_generation)
        and receipt.cancellation_reason != "explicit_cancel")


def validate_original_receipt(frozen, receipt, data_generation):
    """Bind an authoritative Models receipt to the existing frozen request."""
    from tianshu.conversation.generation import GenerationRequests

    if frozen is None or not isinstance(receipt,ModelReceipt):
        raise AppError("recovery_model_receipt_mismatch",status=409)
    if frozen.data_generation != data_generation or receipt.data_generation != data_generation:
        raise AppError("job_generation_expired",status=409)
    if receipt.intent_id != frozen.intent_id:
        raise AppError("recovery_model_receipt_mismatch",status=409)
    try:
        GenerationRequests.validate_observed(frozen,receipt)
    except AppError as error:
        if error.code != "attempt_identity_conflict":
            raise
        raise AppError("recovery_model_receipt_mismatch",status=409) from error


async def outcome(models, attempt_id, *, error=None):
    # Always refresh cancellation before selecting a successor. An immutable
    # unknown result can predate a later explicit author cancellation.
    # A missing control-plane observation does not prove that Models finished
    # the physical attempt, even if a caller has an older partial result.
    receipt = await models.status(attempt_id)
    status = receipt.status if receipt else "absent"
    code = (receipt.error_code if receipt else None) or getattr(error, "code", None) or (
        error if isinstance(error, str) else type(error).__name__ if error is not None else "model_outcome_unknown")
    if receipt and getattr(receipt, "cancellation_reason", None) == "explicit_cancel":
        return status, "explicit_cancel", False, receipt
    return status, code, transient_model_failure(code), receipt


async def freeze_recipe(life, connection, kind, item_id, generation_id, context, recipe, refs, *, previous=None, known_prepared=False):
    present = await (await connection.execute("SELECT generation_id FROM life.generation_recipes WHERE generation_id=%s",
        (generation_id,))).fetchone()
    if present:
        if known_prepared:
            # A retry recipe can predate preparation of its own new request.
            # Promote only the request just prepared while the local domain
            # state has not advanced. Models still owns admission evidence.
            await connection.execute("UPDATE life.generation_recipes AS recipe SET model_submission_confirmed=false "
                "FROM conversation.generation_requests AS request WHERE recipe.generation_id=%s "
                "AND recipe.model_submission_confirmed IS NULL AND request.attempt_id=recipe.generation_id "
                "AND request.state='prepared'",
                (generation_id,))
        return
    if not await life.sources.validate_persistence(connection, context, refs):
        raise AppError("source_unavailable", status=409)
    request = await (await connection.execute(
        "SELECT state FROM conversation.generation_requests WHERE attempt_id=%s", (generation_id,))).fetchone()
    unsubmitted = False if request and request["state"] == "prepared" else None
    await connection.execute("INSERT INTO life.generation_recipes(generation_id,kind,item_id,previous_generation_id,context,recipe,source_refs,model_submission_confirmed) "
        "VALUES(%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
        (generation_id, kind, item_id, previous, document(context), document(recipe), document(refs),unsubmitted))
