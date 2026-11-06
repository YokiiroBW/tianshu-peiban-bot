"""Models facts and cancellation serialize on the original attempt."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from test_rbf_prestart_waits import route
from test_rbf_start_ownership import broker_with_fixture_transport, original_request
from tianshu.models.rbi_outcome_guard import lock_recovery_facts_on


async def original_outcome(database, status):
    request = original_request()
    broker = broker_with_fixture_transport(database, route(), None)
    broker.scheduler.cancel = AsyncMock()
    await broker.ledger.create(request, route(), {"tokens": 2053}, frozen=request)
    if status != "queued":
        await broker.ledger.started(request.attempt_id, None, deadline_at=datetime.now(UTC) + timedelta(seconds=30))
    if status in {"failed", "unknown", "succeeded", "cancelled"}:
        await broker.ledger.finish(request.attempt_id, status, error_code=(
            "transport_unknown" if status == "unknown" else "invalid_upstream_response" if status == "failed" else None))
    return broker, request, await broker.status(request.attempt_id)


async def locked_facts(database, attempt_id):
    async with database.transaction() as connection:
        return await lock_recovery_facts_on(connection, attempt_id)


@pytest.mark.parametrize("status", ["queued", "running", "failed", "unknown", "succeeded", "cancelled"])
async def test_current_original_facts_are_bounded_and_preserve_the_actual_state(database, status):
    broker, request, saved = await original_outcome(database, status)
    facts = await locked_facts(database, request.attempt_id)
    assert facts == {
        "attempt_id": saved.attempt_id, "intent_id": saved.intent_id,
        "status": saved.status, "dispatch": saved.dispatch, "error_code": saved.error_code,
        "finished_at": saved.finished_at, "data_generation": saved.data_generation,
        "request_digest": saved.request_digest, "cancellation_reason": saved.cancellation_reason,
    }
    assert facts["request_digest"] and facts["data_generation"]
    assert (facts["finished_at"] is not None) == (status not in {"queued", "running"})
    broker.scheduler.acquire.assert_not_awaited()


@pytest.mark.parametrize("status", ["failed", "unknown", "succeeded"])
async def test_late_explicit_cancel_is_visible_without_rewriting_the_original_result(database, status):
    broker, request, before = await original_outcome(database, status)
    cancelled = await broker.cancel(request.attempt_id)
    assert cancelled.status == status and cancelled.cancellation_reason == "explicit_cancel"
    assert replace(cancelled, cancellation_reason=None) == before
    facts = await locked_facts(database, request.attempt_id)
    assert facts["status"] == before.status and facts["error_code"] == before.error_code
    assert facts["request_digest"] == before.request_digest and facts["finished_at"] == before.finished_at
    assert facts["cancellation_reason"] == "explicit_cancel"


async def test_consumer_interruption_and_explicit_cancel_remain_distinct(database):
    broker, request, _before = await original_outcome(database, "unknown")
    await broker.ledger.request_cancel(request.attempt_id, reason="consumer_interrupted")
    first = await locked_facts(database, request.attempt_id)
    assert first["cancellation_reason"] == "consumer_interrupted" and first["status"] == "unknown"
    await broker.cancel(request.attempt_id)
    current = await locked_facts(database, request.attempt_id)
    assert current["cancellation_reason"] == "explicit_cancel" and current["status"] == "unknown"


async def test_digest_is_the_frozen_submission_digest_not_the_attempts_digest(database):
    _broker, request, snapshot = await original_outcome(database, "failed")
    async with database.transaction() as connection:
        await connection.execute("UPDATE models.attempts SET request_digest=%s WHERE attempt_id=%s",
                                 ("different-attempt-digest", request.attempt_id))
    facts = await locked_facts(database, request.attempt_id)
    assert facts["request_digest"] == snapshot.request_digest
    assert facts["request_digest"] != "different-attempt-digest"


async def test_missing_submission_digest_is_not_reconstructed_from_attempt_row(database):
    _broker, request, _snapshot = await original_outcome(database, "failed")
    async with database.transaction() as connection:
        await connection.execute("DELETE FROM models.submissions WHERE attempt_id=%s", (request.attempt_id,))
    facts = await locked_facts(database, request.attempt_id)
    assert facts["status"] == "failed" and facts["request_digest"] is None


async def test_unknown_resolved_after_snapshot_returns_the_new_authoritative_state(database):
    broker, request, old = await original_outcome(database, "unknown")
    resolved = await broker.ledger.finish(request.attempt_id, "failed", error_code="known_failure")
    assert resolved["status"] == "failed"
    facts = await locked_facts(database, request.attempt_id)
    assert old.status == "unknown" and facts["status"] == "failed"
    assert facts["error_code"] == "known_failure" and facts["request_digest"] == old.request_digest


async def try_admission_lock(database, attempt_id):
    async with database.transaction() as connection:
        row = await (await connection.execute(
            "SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0)) AS acquired",
            ("models-admission:" + attempt_id,))).fetchone()
        return row["acquired"]


async def test_absent_attempt_keeps_admission_lock_until_the_caller_finishes(database):
    async with database.transaction() as connection:
        assert await lock_recovery_facts_on(connection, "missing-attempt") is None
        assert not await asyncio.create_task(try_admission_lock(database, "missing-attempt"))
    assert await try_admission_lock(database, "missing-attempt")


async def test_guard_holds_the_same_cancel_lock_until_the_callers_transaction_commits(database):
    broker, request, snapshot = await original_outcome(database, "failed")
    pending = None
    try:
        async with database.transaction() as connection:
            current = await lock_recovery_facts_on(connection, request.attempt_id)
            assert current["status"] == "failed" and current["cancellation_reason"] is None
            assert not await asyncio.create_task(try_admission_lock(database, request.attempt_id))
            pending = asyncio.create_task(broker.cancel(request.attempt_id))
            await asyncio.sleep(0)
            assert not pending.done()
        cancelled = await asyncio.wait_for(pending, 5)
        assert cancelled.status == "failed" and cancelled.cancellation_reason == "explicit_cancel"
        after = await locked_facts(database, request.attempt_id)
        assert after["cancellation_reason"] == "explicit_cancel" and after["request_digest"] == snapshot.request_digest
    finally:
        if pending is not None and not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
