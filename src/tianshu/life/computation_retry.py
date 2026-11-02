"""Life's pure-generation observations and source-bound recipe audit, without scheduling."""

from tianshu.conversation.codec import document
from tianshu.models.retry import transient_model_failure
from tianshu.runtime.errors import AppError


async def outcome(models, attempt_id, *, result=None, error=None):
    # Always refresh cancellation before selecting a successor. An immutable
    # unknown result can predate a later explicit author cancellation.
    receipt = await models.status(attempt_id)
    receipt = receipt or (result.receipt if result is not None else None)
    status = receipt.status if receipt else "absent"
    code = (receipt.error_code if receipt else None) or getattr(error, "code", None) or (
        error if isinstance(error, str) else type(error).__name__ if error is not None else "model_outcome_unknown")
    if receipt and getattr(receipt, "cancellation_reason", None) == "explicit_cancel":
        return status, "explicit_cancel", False
    return status, code, transient_model_failure(code)


async def freeze_recipe(life, connection, kind, item_id, generation_id, context, recipe, refs, *, previous=None):
    present = await (await connection.execute("SELECT generation_id FROM life.generation_recipes WHERE generation_id=%s",
        (generation_id,))).fetchone()
    if present:
        return
    if not await life.sources.validate_persistence(connection, context, refs):
        raise AppError("source_unavailable", status=409)
    await connection.execute("INSERT INTO life.generation_recipes(generation_id,kind,item_id,previous_generation_id,context,recipe,source_refs) "
        "VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
        (generation_id, kind, item_id, previous, document(context), document(recipe), document(refs)))
