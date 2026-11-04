"""Memory's provider execution windows and bounded foreground response budget."""

from datetime import UTC, datetime, timedelta
import math

import psycopg

from tianshu.models.retry import transient_model_failure
from tianshu.runtime.errors import AppError
from tianshu.types import RequestBudget


MODEL_EXECUTION_SECONDS = 30.0
PROFILE_READ_SECONDS = 1.0
SQL_READ_SECONDS = 2.0
VECTOR_SQL_SECONDS = 2.0

OPTIONAL_MODEL_UNAVAILABLE = frozenset({
    "embedding_profile_not_configured", "embedding_profile_rebuild_required",
    "model_function_not_configured", "model_route_unavailable", "model_capability_unverified",
    "model_route_binding_changed", "model_route_revision_conflict", "quota_group_not_configured",
})


def optional_failure(error, *, model=False):
    """Only availability failures may discard an optional retrieval lane."""
    if isinstance(error, (TimeoutError, psycopg.errors.QueryCanceled)):
        return True
    if isinstance(error, AppError):
        if error.status in {401, 403}:
            return False
        return transient_model_failure(error.code) or model and error.code in OPTIONAL_MODEL_UNAVAILABLE
    if isinstance(error, psycopg.Error):
        state = error.sqlstate or ""
        return (state.startswith("08") or state in {"40001", "40P01", "55P03", "57P01", "57P02", "57P03"}
                or not state and isinstance(error, psycopg.OperationalError))
    return transient_model_failure(type(error).__name__)


def foreground_seconds(settings):
    explicit = settings.get("request_seconds")
    if explicit is not None:
        return float(explicit)
    # Coverage, lexical selection, and source/version checks before and after
    # ranking remain short local reads, independently of remote model latency.
    seconds = 4 * SQL_READ_SECONDS
    if settings.get("vector", True):
        seconds += (float(settings.get("embedding_seconds", MODEL_EXECUTION_SECONDS))
                    + PROFILE_READ_SECONDS + 2 * float(settings.get("vector_sql_seconds", VECTOR_SQL_SECONDS)))
    if settings.get("rerank", False):
        seconds += float(settings.get("rerank_seconds", MODEL_EXECUTION_SECONDS))
    return seconds


def foreground_budget(settings, parent=None, *, seconds=None, deadline_at=None):
    """An optional foreground response limit, never a provider execution timer."""
    seconds = foreground_seconds(settings) if seconds is None else float(seconds)
    if not math.isfinite(seconds) or seconds <= 0:
        raise AppError("invalid_memory_retrieval_request_budget")
    deadline = datetime.now(UTC) + timedelta(seconds=seconds)
    if deadline_at is not None:
        deadline = min(deadline, deadline_at)
    if parent is None:
        return RequestBudget(deadline)
    return RequestBudget(min(deadline, parent.deadline_at),
        trace_id=parent.trace_id, cancellation_id=parent.cancellation_id)
