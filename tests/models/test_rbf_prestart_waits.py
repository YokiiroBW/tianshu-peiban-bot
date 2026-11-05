"""Durable waits wake the original queued attempt without replaying unknown effects."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from test_rbf_start_ownership import broker_with_fixture_transport, original_request
from tianshu.models.catalog import RoutePlan
from tianshu.runtime.errors import AppError
from tianshu.runtime.recovery_policy import RecoveryWait


def route():
    return RoutePlan("route", 1, "provider", "model", "openai.chat", "", "", "quota",
                     frozenset({"text"}), {})


def dependency_wait(*, remote):
    wait = RecoveryWait("recovery_not_due", datetime.now(UTC) - timedelta(seconds=1),
                        dependency="sources:fixture")
    wait.transaction_rollback_confirmed = True
    wait.remote_rollback_confirmed = remote
    return wait


def successful_transport(sent):
    async def chunks(_size):
        yield json.dumps({"id": "provider-result", "choices": [{"index": 0,
            "message": {"role": "assistant", "content": "original answer"},
            "finish_reason": "stop"}]}).encode()

    @asynccontextmanager
    async def post(_url, **_kwargs):
        sent.append("provider")
        yield SimpleNamespace(status=200, content=SimpleNamespace(iter_chunked=chunks))

    return post


async def terminal_row(ledger, attempt_id):
    async def observe():
        while True:
            row = await ledger.get(attempt_id)
            if row["status"] in {"succeeded", "failed", "cancelled", "unknown"}:
                return row
            await asyncio.sleep(0.01)
    return await asyncio.wait_for(observe(), 5)


async def stop_after_accepted_wait(broker, request, wait):
    calls, recorded = 0, asyncio.Event()
    save = broker.ledger.save_prestart_wait

    async def recheck(_request):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise wait

    async def record(*args, **kwargs):
        try:
            return await save(*args, **kwargs)
        finally:
            recorded.set()

    broker.recheck = recheck
    broker.ledger.save_prestart_wait = record
    accepted = await broker._start_driver(request, streaming=False, resume=True)
    await asyncio.wait_for(accepted, 5)
    await asyncio.wait_for(recorded.wait(), 5)
    # Let the driver finish removing its single-flight entry before a due wake.
    async def finished():
        while request.attempt_id in broker.drivers:
            await asyncio.sleep(0)
    await asyncio.wait_for(finished(), 5)


async def test_safe_accepted_wait_is_observable_and_wakes_same_attempt_once(database):
    request, sent = original_request(), []
    broker = broker_with_fixture_transport(database, route(), successful_transport(sent))
    await broker.ledger.create(request, route(), {"tokens": 2053}, frozen=request)
    wait = dependency_wait(remote=True)
    await stop_after_accepted_wait(broker, request, wait)
    saved = await broker.ledger.get(request.attempt_id)
    assert (saved["status"], saved["dispatch"], saved["recovery_state"]) == ("queued", "not_started", "scheduled")
    assert not sent and broker.scheduler.release.await_count == 1
    with pytest.raises(RecoveryWait) as observed:
        await anext(broker._observe(request, streaming=False))
    assert observed.value.retry_at == wait.retry_at and observed.value.remote_rollback_confirmed is True
    with pytest.raises(RecoveryWait):
        await broker.result(request.attempt_id, context=request.context)

    await asyncio.gather(broker._wake_due_submissions(), broker._wake_due_submissions(), broker.submit(request))
    result = await terminal_row(broker.ledger, request.attempt_id)
    if request.attempt_id in broker.drivers:
        await asyncio.wait_for(broker.drivers[request.attempt_id][0], 5)
    assert result["status"] == "succeeded" and result["dispatch"] == "submitted"
    assert result["result"]["parts"][0]["text"] == "original answer" and sent == ["provider"]
    assert result["recovery_state"] is None
    async with database.transaction(control=True, read_only=True) as connection:
        rows = await (await connection.execute(
            "SELECT attempt_id FROM models.attempts WHERE intent_id=%s", (request.intent_id,))).fetchall()
    assert [row["attempt_id"] for row in rows] == [request.attempt_id]


async def test_remote_denial_remains_observe_only_after_broker_recovery(database):
    request, sent = original_request(), []
    old = broker_with_fixture_transport(database, route(), successful_transport(sent))
    await old.ledger.create(request, route(), {"tokens": 2053}, frozen=request)
    await stop_after_accepted_wait(old, request, dependency_wait(remote=False))
    original = await old.ledger.get(request.attempt_id)
    assert original["recovery_state"] == "observe_only" and original["status"] == "queued"

    new = broker_with_fixture_transport(database, route(), successful_transport(sent))
    await new.ledger.recover()
    assert await new.ledger.pending_submissions() == []
    await new._wake_due_submissions()
    with pytest.raises(RecoveryWait) as caught:
        await new.submit(request)
    assert caught.value.remote_rollback_confirmed is False
    with pytest.raises(RecoveryWait) as observed:
        await anext(new._observe(request, streaming=False))
    assert observed.value.remote_rollback_confirmed is False
    _, claimed, _ = await new.ledger.claim_queued_submission(request)
    assert not claimed and not sent
    assert (await new.ledger.get(request.attempt_id))["recovery_state"] == "observe_only"
    with pytest.raises(AppError, match="model_prestart_observation_required"):
        await old.ledger.started(request.attempt_id, None, deadline_at=datetime.now(UTC) + timedelta(seconds=30))

    async def later_safe_check(_request):
        raise dependency_wait(remote=True)

    new.recheck = later_safe_check
    for operation in (lambda: new.submit(request),
                      lambda: anext(new._observe(request, streaming=False)),
                      lambda: new.result(request.attempt_id, context=request.context)):
        with pytest.raises(RecoveryWait) as later:
            await operation()
        assert later.value.remote_rollback_confirmed is False
    new.recheck = AsyncMock()

    new.scheduler.cancel = AsyncMock()
    cancelled = await new._cancel_attempt(request.attempt_id, reason="explicit_cancel")
    assert cancelled.status == "cancelled" and cancelled.cancellation_reason == "explicit_cancel"
    await new._wake_due_submissions()
    assert not sent


async def test_safe_wait_does_not_dispatch_before_its_due_time(database):
    request, sent = original_request(), []
    broker = broker_with_fixture_transport(database, route(), successful_transport(sent))
    await broker.ledger.create(request, route(), {"tokens": 2053}, frozen=request)
    wait = RecoveryWait("recovery_not_due", datetime.now(UTC) + timedelta(minutes=1), dependency="sources")
    wait.transaction_rollback_confirmed = True
    wait.remote_rollback_confirmed = True
    assert await broker.ledger.save_prestart_wait(request.attempt_id, wait)
    await broker._wake_due_submissions()
    with pytest.raises(RecoveryWait) as caught:
        await broker.submit(request)
    assert caught.value.retry_at == wait.retry_at and not sent
    assert await broker.ledger.pending_submissions() == []


async def test_interrupted_dependency_check_is_not_auto_dispatched_after_restart(database):
    request, sent = original_request(), []
    old = broker_with_fixture_transport(database, route(), successful_transport(sent))
    await old.ledger.create(request, route(), {"tokens": 2053}, frozen=request)
    _, claimed, _ = await old.ledger.claim_queued_submission(request)
    assert claimed and (await old.ledger.get(request.attempt_id))["recovery_state"] == "checking"
    new = broker_with_fixture_transport(database, route(), successful_transport(sent))
    await new.ledger.recover()
    assert await new.ledger.pending_submissions() == []
    with pytest.raises(AppError, match="model_prestart_observation_required"):
        await new.submit(request)
    await new._wake_due_submissions()
    assert not sent

    ordinary = replace(request, intent_id="ordinary", attempt_id="ordinary")
    await old.ledger.create(ordinary, route(), {"tokens": 2053}, frozen=ordinary)
    pending = await new.ledger.pending_submissions()
    assert [item["request"]["attempt_id"] for item in pending] == ["ordinary"]
    await new._wake_due_submissions()
    result = await terminal_row(new.ledger, "ordinary")
    if "ordinary" in new.drivers:
        await asyncio.wait_for(new.drivers["ordinary"][0], 5)
    assert result["status"] == "succeeded" and sent == ["provider"]
    assert (await new.ledger.get(request.attempt_id))["recovery_state"] == "checking"


async def test_scheduled_wait_does_not_cross_installation_generation(database):
    request, sent = original_request(), []
    broker = broker_with_fixture_transport(database, route(), successful_transport(sent))
    await broker.ledger.create(request, route(), {"tokens": 2053}, frozen=request)
    original = await broker.ledger.get(request.attempt_id)
    assert await broker.ledger.save_prestart_wait(request.attempt_id, dependency_wait(remote=True))
    async with database.transaction(control=True) as connection:
        await connection.execute("UPDATE runtime.installation_generation SET id=%s WHERE singleton", (str(uuid4()),))
    await broker._wake_due_submissions()
    result = await broker.ledger.get(request.attempt_id)
    assert result["status"] == "cancelled" and result["error_code"] == "data_generation_expired"
    assert result["data_generation"] == original["data_generation"] and not sent


async def test_wait_after_submitted_stays_original_unknown(database):
    request, sent = original_request(), []

    @asynccontextmanager
    async def interrupted_post(_url, **_kwargs):
        sent.append("provider")
        raise dependency_wait(remote=False)
        yield

    broker = broker_with_fixture_transport(database, route(), interrupted_post)
    await broker.ledger.create(request, route(), {"tokens": 2053}, frozen=request)
    accepted = await broker._start_driver(request, streaming=False, resume=True)
    await asyncio.wait_for(accepted, 5)
    row = await terminal_row(broker.ledger, request.attempt_id)
    if request.attempt_id in broker.drivers:
        await asyncio.wait_for(broker.drivers[request.attempt_id][0], 5)
    assert (row["status"], row["dispatch"], row["recovery_state"]) == ("unknown", "unknown", None)
    assert sent == ["provider"] and await broker.ledger.pending_submissions() == []
