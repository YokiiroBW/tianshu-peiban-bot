"""Read an existing model result without preparation, capacity admission or provider I/O."""

from types import SimpleNamespace

from tianshu.runtime.errors import AppError
from tianshu.runtime.serialization import wire
from tianshu.types import SourceRef
from .codec import result_from_wire
from .ledger import context_fingerprint, receipt
from .types import ModelResult


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
    except AppError as exc:
        if exc.status not in {401, 403, 409}:
            raise
        return ModelResult((), receipt(row))
    if row["protocol"] == "external" or row["result"] is None or row["status"] not in {"succeeded", "failed"}:
        return ModelResult((), receipt(row))
    return result_from_wire({**row["result"], "receipt": wire(receipt(row))})
