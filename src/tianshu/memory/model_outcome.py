"""Read one frozen Memory model attempt without inferring its fate from RPC errors."""

from dataclasses import dataclass
from datetime import UTC, datetime

from aiohttp import ClientError

from tianshu.models.retry import transient_model_failure
from tianshu.models.types import ModelReceipt, ModelResult
from tianshu.runtime.errors import AppError
from tianshu.runtime.serialization import wire


CONTROL_ERRORS = (AppError, TimeoutError, OSError, ClientError)


def error_code(error):
    return getattr(error, "code", type(error).__name__)


def rpc_failure(error):
    return {"error_code":error_code(error),"status":getattr(error,"status",None)}


def needs_rpc_observation(error):
    if not isinstance(error,AppError):
        return isinstance(error,(TimeoutError,OSError,ClientError))
    # These opaque RPC wrappers authorize a control read, never a new model
    # attempt. Named permanent configuration/authority errors stay terminal.
    return transient_model_failure(error.code) or error.status>=500 and error.code in {"internal_error","broker_error"}


def _control_failure(error):
    if isinstance(error,AppError) and ("not_configured" in error.code or "configuration" in error.code or error.code=="explicit_cancel"):
        raise error
    if isinstance(error,AppError) and error.status not in {404,408,429} and error.status < 500:
        raise error
    return error_code(error)


@dataclass(frozen=True, slots=True)
class OriginalOutcome:
    result: ModelResult | None = None
    receipt: ModelReceipt | None = None
    control_error: str | None = None

    @property
    def status(self):
        return self.receipt.status if self.receipt is not None else "observing"

    @property
    def explicit_cancel(self):
        return bool(self.receipt and getattr(self.receipt,"cancellation_reason",None)=="explicit_cancel")

    @property
    def code(self):
        if self.explicit_cancel:
            return "explicit_cancel"
        if self.receipt is None:
            return "model_outcome_unknown"
        return self.receipt.error_code or "model_outcome_"+self.receipt.status

    def record(self, *, rpc_error=None):
        value=wire(self.receipt) if self.receipt else {"status":"observing","error_code":self.code}
        value={**value,"observed_at":wire(datetime.now(UTC))}
        if self.receipt is None:
            value["original_observation"]="unavailable" if self.control_error else "missing"
        if self.control_error:
            value["control_error"]=self.control_error
        if rpc_error is not None:
            value["rpc_failure"]=rpc_failure(rpc_error)
        return value

    def exception(self, attempt_id, *, rpc_error=None):
        details={"attempt_id":attempt_id,"status":self.status,"action":"observe_original_attempt",
                 "original_observation":"confirmed" if self.receipt else "unavailable" if self.control_error else "missing"}
        if self.receipt:
            details.update(dispatch=self.receipt.dispatch,
                cancellation_reason=getattr(self.receipt,"cancellation_reason",None))
        if self.control_error:
            details["control_error"]=self.control_error
        if rpc_error is not None:
            details["rpc_failure"]=rpc_failure(rpc_error)
        return AppError(self.code,status=503,details=details)


async def observe_original(models, request):
    """A returned original receipt is evidence; missing/unavailable control is not."""
    control_error=None
    try:
        result=await models.result(request.attempt_id,context=request.context)
    except CONTROL_ERRORS as error:
        control_error=_control_failure(error)
        result=None
    if result is not None:
        if result.receipt.attempt_id != request.attempt_id or result.receipt.intent_id != request.intent_id:
            raise AppError("memory_model_result_identity_conflict",status=409)
        if result.receipt.status=="succeeded":
            return OriginalOutcome(result,result.receipt)
    try:
        receipt=await models.status(request.attempt_id)
    except CONTROL_ERRORS as error:
        control_error=_control_failure(error)
        receipt=None
    if receipt is not None and (receipt.attempt_id != request.attempt_id or receipt.intent_id != request.intent_id):
        raise AppError("memory_model_result_identity_conflict",status=409)
    if receipt is None and result is not None:
        receipt=result.receipt
    # A newer cancellation/running/completed receipt supersedes an earlier
    # non-success result snapshot. An old empty body cannot become new success.
    selected=result if result is not None and result.receipt==receipt else None
    return OriginalOutcome(selected,receipt,control_error)
