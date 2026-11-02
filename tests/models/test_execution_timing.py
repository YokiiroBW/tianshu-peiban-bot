"""Admission owns execution time; business expiry and explicit queue limits remain real."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from tianshu.models.catalog import QuotaPolicy, RoutePlan
from tianshu.models.codec import external_from_wire, request_from_wire
from tianshu.models.ledger import AttemptLedger, digest
from tianshu.models.scheduler import QuotaScheduler
from tianshu.models.timing import execution_budget, waiting_timeout, waiting
from tianshu.models.types import ExternalRequest, ModelRequest
from tianshu.runtime.errors import AppError
from tianshu.runtime.serialization import wire
from tianshu.types import OriginKind, RequestBudget, ServiceClass, TrustedContext


def request(identity="queued", **changes):
    value = ModelRequest(identity, identity, "reflect", ServiceClass.ELASTIC,
        TrustedContext("fixture", "actor", None, None, (), (), 1, OriginKind.ACTOR),
        RequestBudget(datetime.now(UTC) - timedelta(seconds=1)), (),
        execution_timeout_seconds=0.1)
    return replace(value, **changes)


def test_new_and_legacy_frozen_requests_keep_distinct_timing_contracts():
    original = request()
    restored = request_from_wire(wire(original))
    assert waiting_timeout(restored) is None
    assert execution_budget(restored).remaining() > 0.09
    assert restored.budget.deadline_at == original.budget.deadline_at
    assert execution_budget(restored).cancellation_id == original.budget.cancellation_id
    legacy = wire(replace(original, execution_timeout_seconds=None, max_queue_wait=30))
    legacy.pop("execution_timeout_seconds")
    legacy.pop("business_deadline_at")
    restored_legacy = request_from_wire(legacy)
    assert restored_legacy.max_queue_wait == 30
    with pytest.raises(TimeoutError):
        waiting_timeout(restored_legacy)
    with pytest.raises(TimeoutError):
        execution_budget(restored_legacy)
    external = ExternalRequest("external", "external", original.context, original.budget,
        "quota", "image", ServiceClass.ELASTIC, "fixture", execution_timeout_seconds=10)
    assert waiting_timeout(external_from_wire(wire(external))) is None


async def test_queue_wait_may_outlast_execution_window_and_stays_cancellable_and_bounded():
    scheduler = QuotaScheduler({"quota": QuotaPolicy("quota", 2, 1, 1, max_queue=1)})
    await scheduler.start()
    pending = None
    try:
        await scheduler.acquire(request("held"), "quota", {})
        pending = asyncio.create_task(scheduler.acquire(request("waiting"), "quota", {}))
        await asyncio.sleep(0.15)
        assert not pending.done()
        assert scheduler.snapshot()["quota"]["waiting"] == 1
        with pytest.raises(AppError, match="model_queue_full"):
            await scheduler.acquire(request("overflow"), "quota", {})
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert scheduler.snapshot()["quota"]["waiting"] == 0
        assert "waiting" not in scheduler.groups["quota"].active
        # Free the bounded waiting slot before checking the separate capacity
        # reservation. A reserve never permits bypassing a full hard queue.
        await scheduler.acquire(request("human", service_class=ServiceClass.INTERACTIVE), "quota", {})
        assert scheduler.snapshot()["quota"]["active"] == 2
        assert "held" in scheduler.groups["quota"].active
    finally:
        if pending and not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        await scheduler.close()


@pytest.mark.parametrize("limit", ["business", "request", "author", "immediate"])
async def test_queue_still_honors_business_expiry_and_explicit_queue_limits(limit):
    scheduler = QuotaScheduler({"quota": QuotaPolicy("quota", 1, 0, 1,
        max_wait_seconds=0.03 if limit == "author" else None)})
    await scheduler.start()
    try:
        await scheduler.acquire(request("held"), "quota", {})
        candidate = request("limited", execution_timeout_seconds=10,
            max_queue_wait=0.03 if limit == "request" else 0 if limit == "immediate" else None,
            business_deadline_at=datetime.now(UTC) + timedelta(seconds=0.03) if limit == "business" else None)
        with pytest.raises(AppError, match="business_deadline_expired" if limit == "business" else "queue_wait_expired") as caught:
            await scheduler.acquire(candidate, "quota", {})
        assert caught.value.details == {"status": "cancelled", "dispatch": "not_started"}
        assert scheduler.snapshot()["quota"]["waiting"] == 0
        assert scheduler.snapshot()["quota"]["active"] == 1
    finally:
        await scheduler.close()


def test_execution_is_capped_by_the_business_deadline_without_renewing_it():
    deadline = datetime.now(UTC) + timedelta(seconds=0.5)
    original = request(execution_timeout_seconds=300, business_deadline_at=deadline)
    admitted = execution_budget(original)
    assert admitted.deadline_at == deadline
    assert original.business_deadline_at == deadline


async def test_waiting_business_expiry_does_not_relabel_an_independent_phase_timeout():
    with pytest.raises(TimeoutError, match="independent_database_timeout"):
        async with waiting(request()):
            raise TimeoutError("independent_database_timeout")
    original = request(business_deadline_at=datetime.now(UTC) + timedelta(seconds=0.03))
    with pytest.raises(AppError, match="business_deadline_expired"):
        async with waiting(original):
            await asyncio.Event().wait()


@pytest.mark.parametrize("constraint", ["business", "request", "author"])
async def test_queue_timer_preserves_its_frozen_constraint_even_before_wall_clock_expiry(monkeypatch, constraint):
    from tianshu.models import scheduler as module
    scheduler=QuotaScheduler({"quota":QuotaPolicy("quota",1,0,1,
        max_wait_seconds=1 if constraint=="author" else None)})
    await scheduler.start()
    try:
        await scheduler.acquire(request("held"),"quota",{})
        original_limit=module.queue_limit
        code="business_deadline_expired" if constraint=="business" else "queue_wait_expired"
        def early_timer(candidate,policy):
            seconds,reason=original_limit(candidate,policy)
            assert seconds>0 and reason==code
            return 0,reason  # Reproduce a timer callback before its wall-clock boundary.
        monkeypatch.setattr(module,"queue_limit",early_timer)
        candidate=request("early",business_deadline_at=datetime.now(UTC)+timedelta(hours=1),
            max_queue_wait=1 if constraint=="request" else None)
        with pytest.raises(AppError,match=code):
            await scheduler.acquire(candidate,"quota",{})
        assert datetime.now(UTC)<candidate.business_deadline_at
        assert scheduler.snapshot()["quota"]["waiting"]==0
    finally:
        await scheduler.close()


async def test_ledger_freezes_duration_before_admission_and_never_restarts_an_attempt(database):
    ledger = AttemptLedger(database, "instance")
    plan = RoutePlan("route", 1, "provider", "model", "openai.chat", "", "", "quota", frozenset(), {})
    original = request(execution_timeout_seconds=300)
    row, created = await ledger.create(original, plan, {"tokens": 1})
    assert created and row["status"] == "queued" and row["dispatch"] == "not_started"
    assert row["deadline_at"] is None and row["execution_timeout_seconds"] == 300
    assert row["business_deadline_at"] is None and row["max_queue_wait"] is None
    with pytest.raises(AppError, match="attempt_identity_conflict"):
        await ledger.create(replace(original, execution_timeout_seconds=301), plan, {})
    with pytest.raises(AppError, match="attempt_identity_conflict"):
        await ledger.create(replace(original, max_queue_wait=10), plan, {})
    actual = execution_budget(original)
    started = await ledger.started(original.attempt_id, 30, deadline_at=actual.deadline_at)
    assert started["deadline_at"] == actual.deadline_at
    with pytest.raises(AppError, match="attempt_cancelled_before_start"):
        await ledger.started(original.attempt_id, 30, deadline_at=actual.deadline_at + timedelta(minutes=1))
    await ledger.recover()
    unknown = await ledger.get(original.attempt_id)
    assert unknown["status"] == unknown["dispatch"] == "unknown"
    same, created = await ledger.create(original, plan, {})
    assert not created and same["status"] == "unknown" and same["deadline_at"] == actual.deadline_at

    legacy = replace(request("legacy"), execution_timeout_seconds=None, max_queue_wait=30,
        budget=RequestBudget(datetime.now(UTC) + timedelta(minutes=1)))
    old_wire = wire(legacy)
    old_wire.pop("execution_timeout_seconds")
    old_wire.pop("business_deadline_at")
    old_semantic = {key: value for key, value in old_wire.items() if key not in {"budget", "max_queue_wait"}}
    row, created = await ledger.create(request_from_wire(old_wire), plan, {})
    assert created and row["request_digest"] == digest(old_semantic)
    assert row["deadline_at"] == legacy.budget.deadline_at
    await ledger.recover()
    assert (await ledger.get("legacy"))["status"] == "cancelled"


async def test_new_ledger_identity_canonicalizes_only_its_unordered_contract_inputs(database):
    class ReverseSet(frozenset):
        def __iter__(self):
            return iter(sorted(super().__iter__(),reverse=True))
    ledger=AttemptLedger(database,"instance")
    plan=RoutePlan("route",1,"provider","model","openai.chat","","","quota",frozenset(),{})
    original=request("canonical",context=replace(request().context,
        permissions=ReverseSet({"memory.read","models.read","*"})),
        required_capabilities=ReverseSet({"text","stream","tools"}))
    semantic=wire(original)
    semantic.pop("budget")
    semantic.pop("business_deadline_at")
    semantic["context"]["permissions"].sort()
    semantic["required_capabilities"].sort()
    row,created=await ledger.create(original,plan,{})
    assert created and row["request_digest"]==digest(semantic)
    incoming=wire(original)
    incoming["context"]["permissions"].reverse()
    incoming["required_capabilities"].reverse()
    restored=request_from_wire(incoming)
    assert restored==original
    duplicate,created=await ledger.create(restored,plan,{})
    assert not created and duplicate["request_digest"]==row["request_digest"]
    legacy=replace(original,intent_id="legacy-order",attempt_id="legacy-order",execution_timeout_seconds=None,
        budget=RequestBudget(datetime.now(UTC)+timedelta(seconds=60)))
    legacy_semantic=wire(legacy)
    for name in ("budget","max_queue_wait","execution_timeout_seconds","business_deadline_at"):
        legacy_semantic.pop(name)
    unchanged,created=await ledger.create(legacy,plan,{})
    assert created and unchanged["request_digest"]==digest(legacy_semantic)
