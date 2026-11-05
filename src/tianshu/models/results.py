"""Read an existing model result without preparation, capacity admission or provider I/O."""

from types import SimpleNamespace

from tianshu.runtime.errors import AppError
from tianshu.runtime.recovery_policy import RecoveryWait
from tianshu.runtime.serialization import wire
from tianshu.types import SourceRef
from .codec import result_from_wire
from .ledger import context_fingerprint, receipt
from .generation import current_generation_on, generation_id, require_generation
from .lookup import missing_receipt
from .types import ModelReceipt, ModelResult


async def check_attempt_context(broker, row, context):
    if (row["principal_id"] != context.principal_id
            or row["context_fingerprint"] != context_fingerprint(context)):
        raise AppError("model_result_context_forbidden", status=403)
    await broker.recheck(SimpleNamespace(context=context, source_refs=()))
    return tuple(SourceRef(**item) for item in row["source_refs"] or ())


async def read_result(broker, attempt_id, *, context):
    row = await broker.ledger.get(attempt_id)
    if row is None:
        return None
    selected = await check_attempt_context(broker, row, context)
    try:
        await broker.recheck(SimpleNamespace(context=context, source_refs=selected))
    except RecoveryWait:
        raise
    except AppError as exc:
        if exc.status not in {401, 403, 409}:
            raise
        return ModelResult((), receipt(row))
    if row["protocol"] == "external" or row["result"] is None or row["status"] not in {"succeeded", "failed"}:
        return ModelResult((), receipt(row))
    return result_from_wire({**row["result"], "receipt": wire(receipt(row))})


def validate_reusable_receipt(original, attempt_id, *, expected_request_digest=None,
                              expected_data_generation=None):
    """Do not turn missing, cancelled, active or unknown work into reusable success."""
    if not isinstance(original, ModelReceipt) or original.attempt_id != attempt_id:
        raise AppError("model_attempt_identity_conflict", status=409, details={"attempt_id": attempt_id})
    details = {"attempt_id": attempt_id, "status": original.status, "dispatch": original.dispatch}
    if original.cancellation_reason == "explicit_cancel":
        raise AppError("model_attempt_cancelled", status=409, details=details)
    if original.status in {"queued", "running"}:
        raise AppError("model_result_pending", status=409, details=details)
    if original.status == "unknown":
        raise AppError("model_result_unknown", status=409, details=details)
    if original.status != "succeeded":
        raise AppError("model_result_not_succeeded", status=409, details=details)
    if expected_request_digest is not None:
        if not isinstance(expected_request_digest, str) or not expected_request_digest:
            raise AppError("invalid_model_result_reuse")
        if original.request_digest != expected_request_digest:
            raise AppError("attempt_identity_conflict", status=409, details=details)
    if expected_data_generation is not None:
        if not isinstance(expected_data_generation, str):
            raise AppError("invalid_model_result_reuse")
        require_generation(original.data_generation, generation_id(expected_data_generation))


async def read_reusable_result(broker, attempt_id, *, context, expected_request_digest=None,
                               expected_data_generation=None):
    """Read an existing success only; caller still owns its business CAS/commit."""
    row = await broker.ledger.get(attempt_id)
    if row is None:
        raise missing_receipt(attempt_id)
    selected = await check_attempt_context(broker, row, context)
    await broker.recheck(SimpleNamespace(context=context, source_refs=selected))
    original = receipt(row)
    validate_reusable_receipt(original, attempt_id,
        expected_request_digest=expected_request_digest, expected_data_generation=expected_data_generation)
    if expected_data_generation is not None:
        async with broker.database.transaction(control=True) as connection:
            current = await current_generation_on(connection)
            require_generation(expected_data_generation, current)
    if row["protocol"] == "external" or row["result"] is None:
        raise AppError("model_result_unavailable", status=409, details={"attempt_id": attempt_id})
    return result_from_wire({**row["result"], "receipt": wire(original)})
