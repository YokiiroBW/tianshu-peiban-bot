"""Failure policy for explicitly retryable, side-effect-free model computations.

Domain owners persist attempts, check live results and schedule their own work.
This policy never authorizes replay of publishing, delivery or external actions.
"""

import re
import random


_TRANSIENT = frozenset({
    "timeout_unknown", "transport_unknown", "timeouterror", "serverdisconnectederror",
    "transport_not_sent",
    "serverconnectionerror", "clientconnectionerror", "clientconnectorerror", "clientoserror",
    "clientpayloaderror", "connectionreseterror", "connectionabortederror", "brokenpipeerror",
    "servertimeouterror", "sockettimeouterror", "connectiontimeouterror",
    "model_stream_interrupted", "model_stream_incomplete", "generation_stream_interrupted",
    "incomplete_native_stream", "upstream_response_incomplete", "broker_unavailable",
    "broker_stopping", "broker_restarted_before_start", "broker_restarted_after_submit",
    "model_queue_full", "model_preparation_queue_full", "queue_wait_expired",
    "execution_deadline_expired", "deadline_exceeded", "model_outcome_unknown",
    "reflection_outcome_unknown", "rate_limit_exceeded", "rate_limit_error",
    "consumer_interrupted", "transport_interrupted",
    "overloaded_error", "server_error", "internal_server_error", "service_unavailable",
})
_PERMANENT = (
    "source", "permission", "authorization", "forbidden", "access_changed", "withdraw",
    "retract", "supersed", "cancel", "business_deadline", "not_configured", "configuration",
    "invalid_input", "identity_conflict", "context_changed",
)


def transient_model_failure(error_code: str | None, *, cancelled: bool = False) -> bool:
    """Classify a known error, after the owner has excluded queued/running work.

    ``cancelled`` means a known explicit cancellation, not an execution timeout
    merely represented by a cancelled receipt. Unknown/unclassified codes stay
    terminal; domain owners must not guess that arbitrary invalid output is safe.
    """
    if cancelled or not error_code:
        return False
    code = error_code.strip().lower()
    if any(marker in code for marker in _PERMANENT):
        return False
    http = re.fullmatch(r"(?:upstream_http_|http_)(\d{3})", code)
    if http:
        status = int(http[1])
        return status == 429 or 500 <= status <= 599
    return code in _TRANSIENT or code.endswith("_timeout")


def retry_delay_seconds(attempt_number: int) -> float:
    """Jittered exponential delay; callers persist the chosen due time once."""
    if type(attempt_number) is not int or attempt_number < 1:
        raise ValueError("positive_attempt_number_required")
    return float(min(900, 30 * 2 ** min(attempt_number - 1, 5) * random.uniform(0.8, 1.2)))
