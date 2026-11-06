"""The public cancellation response uses the complete original receipt projection."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from test_rbf_model_receipts import create_attempt, models_port
from test_rbf_prestart_waits import route
from test_rbf_start_ownership import broker_with_fixture_transport, original_request
from tianshu.models.async_jobs import completion_record
from tianshu.models.types import ExternalRequest
from tianshu.runtime.jobs import JobSpec
from tianshu.types import RequestBudget, ServiceClass


@pytest.mark.parametrize("state", ["failed", "unknown", "succeeded"])
async def test_http_cancel_receipt_preserves_frozen_submission_and_terminal_result(models_port, state):
    broker, client, _state = models_port
    original = await create_attempt(broker, "cancel-" + state, state)
    completion = JobSpec("companion.growth.observe", "wake:" + state, {"attempt_id": original.attempt_id},
                         data_generation=original.data_generation)
    await broker.ledger.existing_submission(original, completion=completion_record(completion))
    broker.scheduler = SimpleNamespace(cancel=AsyncMock())
    before = await client.status(original.attempt_id)
    row_before = await broker.ledger.get(original.attempt_id)
    assert before.request_digest and before.completion_digest and before.completion_state == "pending"

    cancelled = await client.cancel(original.attempt_id)
    current = await client.status(original.attempt_id)
    row_after = await broker.ledger.get(original.attempt_id)
    assert cancelled == current and cancelled.cancellation_reason == "explicit_cancel"
    assert replace(cancelled, cancellation_reason=before.cancellation_reason) == before
    for key in ("status", "dispatch", "error_code", "finished_at", "request_digest",
                "data_generation", "result", "usage", "charged_tokens", "charged_units"):
        assert row_after[key] == row_before[key]
    broker.scheduler.cancel.assert_awaited_once_with(original.attempt_id)


async def test_queued_cancel_returns_full_status_after_the_original_attempt_is_cancelled(models_port):
    broker, client, _state = models_port
    original = await create_attempt(broker, "cancel-queued", "queued")
    broker.scheduler = SimpleNamespace(cancel=AsyncMock())
    before = await client.status(original.attempt_id)
    cancelled = await client.cancel(original.attempt_id)
    assert cancelled == await client.status(original.attempt_id)
    assert cancelled.status == "cancelled" and cancelled.dispatch == "not_started"
    assert cancelled.error_code == "cancelled_before_start" and cancelled.cancellation_reason == "explicit_cancel"
    assert cancelled.request_digest == before.request_digest and cancelled.completion_state == before.completion_state


@pytest.mark.parametrize("state", ["queued", "failed"])
async def test_cloud_observer_interruption_returns_the_same_status_without_cancelling(models_port, state):
    broker, _client, _state = models_port
    original = await create_attempt(broker, "observer-" + state, state)
    broker.scheduler = SimpleNamespace(cancel=AsyncMock())
    before = await broker.status(original.attempt_id)
    returned = await broker.cancel(original.attempt_id, reason="consumer_interrupted")
    assert returned == before == await broker.status(original.attempt_id)
    broker.scheduler.cancel.assert_not_awaited()


async def test_cloud_observer_interruption_preserves_prestart_reconciliation_projection(models_port):
    broker, _client, _state = models_port
    original = await create_attempt(broker, "observer-checking", "queued")
    broker.scheduler = SimpleNamespace(cancel=AsyncMock())
    _row, claimed, _request = await broker.ledger.claim_queued_submission(original)
    assert claimed
    before = await broker.status(original.attempt_id)
    assert before.recovery["state"] == "checking" and before.recovery["requires_reconciliation"] is True
    returned = await broker.cancel(original.attempt_id, reason="consumer_interrupted")
    assert returned == before == await broker.status(original.attempt_id)
    broker.scheduler.cancel.assert_not_awaited()


async def test_unsubmitted_external_owner_cancel_returns_its_current_receipt(database):
    broker = broker_with_fixture_transport(database, route(), None)
    broker.scheduler.cancel = AsyncMock()
    original = ExternalRequest("external-intent", "external-attempt", original_request().context,
        RequestBudget(datetime.now(UTC) + timedelta(minutes=2)), "quota", "fixture.external",
        ServiceClass.INTERACTIVE, "fixture")
    permit = await broker.begin_external(original)
    assert permit.submitted_at is None
    returned = await broker.cancel_external_owner(original.attempt_id)
    assert returned == await broker.status(original.attempt_id)
    assert returned.status == "cancelled" and returned.dispatch == "not_started"
    assert returned.cancellation_reason == "consumer_interrupted"
    assert returned.error_code == "cancelled_before_external_submit"
    assert permit.done and original.attempt_id not in broker.external
