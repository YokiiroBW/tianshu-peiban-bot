"""One boundary between queued waiting and admitted supplier execution."""

from datetime import UTC, datetime, timedelta
from contextlib import asynccontextmanager
import asyncio
import math

from tianshu.runtime.errors import AppError
from tianshu.types import RequestBudget


def validate_timing(request):
    execution = getattr(request, "execution_timeout_seconds", None)
    if execution is not None and (
        type(execution) not in (int, float) or not math.isfinite(execution) or execution <= 0
    ):
        raise AppError("invalid_model_budget")
    queue = getattr(request, "max_queue_wait", None)
    if queue is not None and (
        type(queue) not in (int, float) or not math.isfinite(queue) or queue < 0
    ):
        raise AppError("invalid_model_budget")
    deadline = getattr(request, "business_deadline_at", None)
    if deadline is not None and (
        execution is None or not isinstance(deadline, datetime) or deadline.tzinfo is None
        or deadline.utcoffset() is None
    ):
        raise AppError("invalid_model_business_deadline")


def waiting_budget(request):
    """True expiry during queueing; old frozen requests keep their old deadline."""
    validate_timing(request)
    if getattr(request, "execution_timeout_seconds", None) is None:
        return request.budget
    deadline = request.business_deadline_at
    return (RequestBudget(deadline, cancellation_id=request.budget.cancellation_id,
                          trace_id=request.budget.trace_id) if deadline is not None else None)


def waiting_timeout(request):
    budget = waiting_budget(request)
    if budget is None:
        return None
    if getattr(request, "execution_timeout_seconds", None) is not None:
        remaining = budget.remaining()
        if remaining <= 0:
            raise waiting_expired(request, reason="business_deadline_expired")
        return remaining
    budget.check()
    return budget.remaining()


def waiting_expired(request, *, reason=None):
    deadline = getattr(request, "business_deadline_at", None)
    code = reason or ("business_deadline_expired" if deadline is not None and deadline <= datetime.now(UTC) else "queue_wait_expired")
    return AppError(code, status=408, details={"status": "cancelled", "dispatch": "not_started"})


@asynccontextmanager
async def waiting(request):
    timer = asyncio.timeout(waiting_timeout(request))
    try:
        async with timer:
            yield
    except TimeoutError:
        if timer.expired() and getattr(request, "execution_timeout_seconds", None) is not None:
            raise waiting_expired(request, reason="business_deadline_expired") from None
        raise


def execution_budget(request):
    """Allocate once on admission, preserving cancellation and trace identity."""
    validate_timing(request)
    seconds = getattr(request, "execution_timeout_seconds", None)
    if seconds is None:
        request.budget.check()
        return request.budget
    deadline = datetime.now(UTC) + timedelta(seconds=seconds)
    if request.business_deadline_at is not None:
        deadline = min(deadline, request.business_deadline_at)
    budget = RequestBudget(deadline, cancellation_id=request.budget.cancellation_id,
                           trace_id=request.budget.trace_id)
    budget.check()
    return budget


def queue_limit(request, policy):
    # Freeze the actual limiting constraint before waiting. Timer callbacks can
    # run a clock-resolution tick before wall time reaches an absolute expiry.
    limits = [(seconds, reason) for seconds, reason in (
        (waiting_timeout(request), "business_deadline_expired"),
        (getattr(request, "max_queue_wait", None), "queue_wait_expired"),
        (policy.max_wait_seconds, "queue_wait_expired"),
    ) if seconds is not None]
    return min(limits, key=lambda limit: limit[0]) if limits else (None, None)
