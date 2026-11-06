"""Build isolated Models ledger facts for Life recovery tests.

The Life test broker supplies model responses without performing provider I/O.
It does not own Models' database, so recovery tests explicitly create and
settle the corresponding physical attempt through the real Models ledger.
"""
from datetime import UTC, datetime, timedelta

from tianshu.models.catalog import RoutePlan
from tianshu.models.http_timing import HttpTiming
from tianshu.models.ledger import AttemptLedger, receipt, submission_digest


async def record_outcome(database, broker, attempt_id, *, status, error_code,
                         cancellation_reason=None, dispatch="submitted", headers_sent=None):
    request = broker.requests[attempt_id]
    queued = broker.receipts[attempt_id]
    plan = RoutePlan(queued.route_id, queued.route_revision, queued.provider_id,
        queued.model, queued.protocol, "", "", queued.quota_group, frozenset({"text"}), {})
    ledger = AttemptLedger(database, "life-rbi-fixture")
    await ledger.create(request, plan, {"tokens": 10}, frozen=request,
        data_generation=request.data_generation)
    if dispatch == "not_started":
        assert status == "failed" and cancellation_reason is None
        queued_row = await ledger.get(attempt_id)
        timing = HttpTiming(queued_row["queued_at"])
        timing.connection_reused = False
        timing.mark("connect_start")
        if headers_sent is not None:
            timing.points["headers_sent"] = headers_sent
        diagnostic = {"category":"not_sent","part_count":0,"text_bytes":0,
            "part_kinds":[],"native_completed":False}
        await ledger.finish_before_start(attempt_id,status,error_code=error_code,
            timing=timing.record(),diagnostic=diagnostic)
    else:
        await ledger.started(attempt_id, None, deadline_at=datetime.now(UTC) + timedelta(minutes=5))
        if cancellation_reason:
            # A later explicit cancellation must win even if the consumer was
            # already interrupted and its original attempt had finished.
            await ledger.request_cancel(attempt_id, reason="consumer_interrupted")
        await ledger.finish(attempt_id,status,error_code=error_code,dispatch=dispatch)
        if cancellation_reason == "explicit_cancel":
            await ledger.request_cancel(attempt_id, reason="explicit_cancel")
    observed = receipt(await ledger.get(attempt_id))
    assert observed.request_digest == submission_digest(request)
    assert observed.status == status and observed.error_code == error_code and observed.dispatch == dispatch
    assert observed.cancellation_reason == cancellation_reason
    assert observed.data_generation == request.data_generation
    broker.receipts[attempt_id] = observed
    return ledger, observed
