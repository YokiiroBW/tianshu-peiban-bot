"""Model failures retain their original physical result and bounded cause evidence."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiohttp
import pytest

import tianshu.models.broker as broker_module
from test_rbf_prestart_waits import route, terminal_row
from test_rbf_start_ownership import broker_with_fixture_transport, original_request
from test_rbf_model_receipts import create_attempt, models_port
from tianshu.models.catalog import QuotaPolicy
from tianshu.models.codec import receipt_from_wire
from tianshu.models.ledger import submission_digest
from tianshu.models.rbi_failure_diagnostic import failure_diagnostic
from tianshu.models.scheduler import QuotaScheduler
from tianshu.models.types import ModelReceipt
from tianshu.runtime.errors import AppError
from tianshu.runtime.serialization import wire


def response_post(body, *, status=200, headers=None, stream_error=None):
    async def chunks(_size):
        if body:
            yield body
        if stream_error:
            raise stream_error

    @asynccontextmanager
    async def post(_url, **_kwargs):
        yield SimpleNamespace(status=status, headers=headers or {},
                              content=SimpleNamespace(iter_chunked=chunks))

    return post


async def run_attempt(database, post, *, streaming=False, configure=None, request=None, plan=None):
    original = request or original_request()
    plan = plan or route()
    broker = broker_with_fixture_transport(database, plan, post)
    if configure:
        configure(broker)
    await broker.ledger.create(original, plan, {"tokens": 2053}, frozen=original, streaming=streaming)
    accepted = await broker._start_driver(original, streaming=streaming, resume=True)
    await asyncio.wait_for(accepted, 5)
    row = await terminal_row(broker.ledger, original.attempt_id)
    if original.attempt_id in broker.drivers:
        await asyncio.wait_for(broker.drivers[original.attempt_id][0], 5)
    return broker, original, row, await broker.status(original.attempt_id)


@pytest.mark.parametrize("body,expected_type", [
    (b'{"secret":"body-value",', "JSONDecodeError"),
    (b'\xffsecret-body-value', "UnicodeDecodeError"),
])
async def test_complete_but_undecodable_body_is_protocol_failure(database, body, expected_type):
    broker, original, row, receipt = await run_attempt(database, response_post(body))
    assert (receipt.attempt_id, receipt.status, receipt.dispatch, receipt.error_code) == (
        original.attempt_id, "failed", "submitted", "invalid_upstream_response")
    assert receipt.diagnostic["failure_stage"] == "protocol_decode"
    assert receipt.diagnostic["error_type"] == expected_type
    assert receipt.diagnostic["original_output_reusable"] is None
    assert receipt.diagnostic["transport"]["evidence"] == "sent_or_unknown"
    assert receipt.http_timing["points_ms"]["read_complete"] is not None
    assert row["result"] is None and receipt.usage.metering == "unknown"
    assert "secret" not in json.dumps(receipt.diagnostic).lower()
    assert receipt_from_wire(wire(receipt)) == receipt
    with pytest.raises(AppError, match="model_result_not_succeeded"):
        await broker.reusable_result(original.attempt_id, context=original.context)


async def test_explicit_native_protocol_validation_preserves_its_original_code(database):
    body = json.dumps({"choices": None, "private": "secret-body-value"}).encode()
    _, _, row, receipt = await run_attempt(database, response_post(body))
    assert (receipt.status, receipt.dispatch, receipt.error_code) == (
        "failed", "submitted", "invalid_native_chat_response")
    assert receipt.diagnostic["failure_stage"] == "protocol_decode"
    assert receipt.diagnostic["error_type"] == "AppError"
    assert receipt.diagnostic["original_output_reusable"] is None
    assert row["result"] is None and "secret" not in json.dumps(receipt.diagnostic).lower()


async def test_known_embedding_dimension_failure_is_domain_validation(database):
    request = replace(original_request(), function="embed")
    plan = replace(route(), protocol="openai.embedding")
    body = json.dumps({"data": [{"embedding": [0.2]}]}).encode()
    _, original, row, receipt = await run_attempt(database, response_post(body), request=request, plan=plan)
    assert (receipt.attempt_id, receipt.status, receipt.dispatch, receipt.error_code) == (
        original.attempt_id, "failed", "submitted", "embedding_dimension_mismatch")
    assert receipt.diagnostic["failure_stage"] == "domain_validation"
    assert receipt.diagnostic["original_output_reusable"] is None and row["result"] is None


async def test_local_usage_exception_after_complete_read_does_not_become_bad_upstream_output(database, monkeypatch):
    body = json.dumps({"id": "original-provider-handle", "choices": [{"index": 0,
        "message": {"role": "assistant", "content": "private-output"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 8}}).encode()

    def broken_usage(*_args, **_kwargs):
        raise RuntimeError("private-usage-error")

    monkeypatch.setattr(broker_module, "normalize_usage", broken_usage)
    _, original, row, receipt = await run_attempt(database, response_post(body))
    assert (receipt.attempt_id, receipt.status, receipt.dispatch, receipt.error_code) == (
        original.attempt_id, "failed", "submitted", "model_local_error")
    assert receipt.diagnostic["category"] == "local_failure"
    assert receipt.diagnostic["failure_stage"] == "local_program"
    assert receipt.diagnostic["error_type"] == "RuntimeError"
    assert receipt.diagnostic["original_output_reusable"] is None
    assert receipt.provider_handle == "original-provider-handle" and receipt.first_output_ms is not None
    assert row["result"] is None and receipt.usage.metering == "unknown"
    assert row["charged_units"] == {"tokens": 2053}
    assert "private" not in json.dumps(receipt.diagnostic).lower()


async def test_ambiguous_interpreter_exception_after_read_does_not_assert_provider_fault(database, monkeypatch):
    body = json.dumps({"choices": [{"index": 0,
        "message": {"role": "assistant", "content": "example"}, "finish_reason": "stop"}]}).encode()

    def ambiguous(_self, _value):
        raise TypeError("private-implementation-detail")

    monkeypatch.setattr(broker_module.ResponseCollector, "feed", ambiguous)
    _, _, row, receipt = await run_attempt(database, response_post(body))
    assert (receipt.status, receipt.dispatch, receipt.error_code) == ("failed", "submitted", "model_processing_unknown")
    assert receipt.diagnostic["category"] == "processing_unknown"
    assert receipt.diagnostic["failure_stage"] == "unknown" and receipt.diagnostic["error_type"] == "TypeError"
    assert row["result"] is None and "private" not in json.dumps(receipt.diagnostic).lower()


async def test_truncated_body_is_unknown_transport_not_a_complete_bad_response(database):
    _, _, row, receipt = await run_attempt(database, response_post(
        b'{"choices":', stream_error=aiohttp.ClientPayloadError("private provider detail")))
    assert (receipt.status, receipt.dispatch, receipt.error_code) == ("unknown", "unknown", "transport_unknown")
    assert receipt.diagnostic["failure_stage"] == "transport"
    assert receipt.diagnostic["error_type"] == "ClientPayloadError"
    assert receipt.diagnostic["original_output_reusable"] is None
    assert receipt.http_timing["points_ms"]["read_complete"] is None and row["result"] is None


async def test_incomplete_stream_preserves_unknown_original_and_cannot_claim_reusable_output(database):
    _, _, row, receipt = await run_attempt(database, response_post(
        b'data: {"choices":[{"delta":{"content":"private-prefix"}}]}'), streaming=True)
    assert (receipt.status, receipt.dispatch, receipt.error_code) == (
        "unknown", "unknown", "incomplete_native_stream")
    assert receipt.diagnostic["category"] == "incomplete_output"
    assert receipt.diagnostic["failure_stage"] == "protocol_decode"
    assert receipt.diagnostic["original_output_reusable"] is None and row["result"] is None


async def test_invalid_stream_event_does_not_make_the_unfinished_result_known(database):
    _, _, row, receipt = await run_attempt(database, response_post(
        b'data: {"secret":"stream-body",\n\n'), streaming=True)
    assert (receipt.status, receipt.dispatch, receipt.error_code) == ("unknown", "unknown", "transport_unknown")
    assert receipt.diagnostic["failure_stage"] == "protocol_decode"
    assert receipt.diagnostic["error_type"] == "JSONDecodeError"
    assert receipt.http_timing["points_ms"]["read_complete"] is None and row["result"] is None
    assert "secret" not in json.dumps(receipt.diagnostic).lower()


def test_old_receipt_keeps_original_diagnostic_without_inventing_a_stage():
    original = ModelReceipt("historical", "intent", "failed", "submitted", "route", 1,
        "provider", "model", "openai.chat", "quota", error_code="invalid_upstream_response",
        diagnostic={"category": "known_invalid", "part_count": 0})
    saved = receipt_from_wire(wire(original))
    assert saved == original and "failure_stage" not in saved.diagnostic
    assert "original_output_reusable" not in saved.diagnostic


def test_untrusted_exception_type_cannot_inject_diagnostic_text():
    error = type("private_token_in_type", (Exception,), {})("private-message")
    observed = failure_diagnostic(error, site="local", http_phase="finished")
    assert observed == {"failure_stage": "local_program", "error_type": "OtherError",
                        "original_output_reusable": None}


async def test_saved_success_can_be_reparsed_locally_without_a_new_model_request(models_port):
    broker, client, _state = models_port
    original = await create_attempt(broker, "saved-original", "queued")
    await broker.ledger.started(original.attempt_id, None, deadline_at=datetime.now(UTC) + timedelta(seconds=30))
    text = '{"notes":[{"kind":"example"}]}'
    result = {"parts": [{"kind": "text", "text": text}],
        "native_response": {"id": "saved-provider-handle", "choices": [{"index": 0,
            "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}]},
        "completion": {"state": "complete", "stop_reasons": ["stop"], "provider_status": None}}
    await broker.ledger.finish(original.attempt_id, "succeeded", result=result)
    broker.submit = AsyncMock(side_effect=AssertionError("reuse must not submit a model request"))
    options = {"context": original.context, "expected_request_digest": submission_digest(original),
               "expected_data_generation": original.data_generation}
    first = await client.reusable_result(original.attempt_id, **options)
    second = await client.reusable_result(original.attempt_id, **options)
    assert first == second and first.receipt.attempt_id == original.attempt_id
    assert json.loads(first.parts[0].text) == {"notes": [{"kind": "example"}]}
    broker.submit.assert_not_awaited()


async def test_release_failure_after_committed_success_keeps_original_receipt_and_charge(database):
    body = json.dumps({"id": "saved-provider-handle", "choices": [{"index": 0,
        "message": {"role": "assistant", "content": "saved-output"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 8}}).encode()

    def fail_first_release(broker):
        broker.scheduler.release = AsyncMock(side_effect=[RuntimeError("local release failed"), None])

    broker, original, row, receipt = await run_attempt(database, response_post(body), configure=fail_first_release)
    assert receipt.status == "succeeded" and receipt.dispatch == "submitted" and receipt.error_code is None
    assert receipt.diagnostic["category"] == "complete"
    assert row["result"]["native_response"]["id"] == "saved-provider-handle"
    assert row["charged_units"] == {"tokens": 20}
    calls = broker.scheduler.release.await_args_list
    assert len(calls) == 2
    assert all(call.kwargs == {"units": {"tokens": 20}} for call in calls)
    result = await broker.reusable_result(original.attempt_id, context=original.context)
    assert result.parts[0].text == "saved-output" and result.receipt == receipt


async def test_commit_acknowledgement_failure_does_not_rewrite_already_succeeded_receipt(database):
    body = json.dumps({"id": "saved-provider-handle", "choices": [{"index": 0,
        "message": {"role": "assistant", "content": "saved-output"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 8}}).encode()
    before = []

    def fail_after_commit(broker):
        finish = broker.ledger.finish

        async def commit_then_lose_ack(attempt_id, status, **kwargs):
            result = await finish(attempt_id, status, **kwargs)
            if status == "succeeded":
                before.append(await broker.status(attempt_id))
                raise OSError("synthetic connection close after commit")
            return result

        broker.ledger.finish = commit_then_lose_ack

    broker, original, row, receipt = await run_attempt(database, response_post(body), configure=fail_after_commit)
    assert len(before) == 1 and receipt == before[0]
    assert receipt.status == "succeeded" and receipt.diagnostic["category"] == "complete"
    assert row["result"]["native_response"]["id"] == "saved-provider-handle"
    assert row["charged_units"] == {"tokens": 20}
    assert broker.scheduler.release.await_args.kwargs == {"units": {"tokens": 20}}
    assert any(call.args[:2] == ("model.finished", "succeeded") for call in broker.event.await_args_list)
    result = await broker.reusable_result(original.attempt_id, context=original.context)
    assert result.parts[0].text == "saved-output" and result.receipt == receipt


async def test_failure_observation_cannot_overwrite_a_different_running_owner(database):
    original = original_request()
    broker = broker_with_fixture_transport(database, route(), response_post(b""))
    await broker.ledger.create(original, route(), {"tokens": 2053}, frozen=original)
    await broker.ledger.started(original.attempt_id, None, deadline_at=datetime.now(UTC) + timedelta(seconds=30))
    assert await broker.ledger.save_observation(original.attempt_id, diagnostic={"category": "initial"},
                                                only_running=True) is True
    async with database.transaction() as connection:
        await connection.execute("UPDATE models.attempts SET broker_instance=%s WHERE attempt_id=%s",
                                 ("replacement-owner", original.attempt_id))
    assert await broker.ledger.save_observation(original.attempt_id, diagnostic={"category": "stale"},
                                                only_running=True) is False
    row = await broker.ledger.get(original.attempt_id)
    assert row["status"] == "running" and row["broker_instance"] == "replacement-owner"
    assert row["diagnostic"] == {"category": "initial"}


async def test_stale_broker_does_not_finish_or_refund_the_new_owners_running_attempt(database):
    original = original_request()
    body = json.dumps({"id": "provider-handle", "choices": [{"index": 0,
        "message": {"role": "assistant", "content": "output"}, "finish_reason": "stop"}]}).encode()
    broker = broker_with_fixture_transport(database, route(), response_post(body))

    async def replace_owner_then_fail(_attempt_id, **_kwargs):
        async with database.transaction() as connection:
            await connection.execute("UPDATE models.attempts SET broker_instance=%s WHERE attempt_id=%s",
                                     ("replacement-owner", original.attempt_id))
        raise RuntimeError("stale execution")

    broker.ledger.checkpoint = replace_owner_then_fail
    await broker.ledger.create(original, route(), {"tokens": 2053}, frozen=original)
    accepted = await broker._start_driver(original, streaming=False, resume=True)
    driver = broker.drivers[original.attempt_id][0]
    await asyncio.wait_for(accepted, 5)
    await asyncio.wait_for(driver, 5)
    row = await broker.ledger.get(original.attempt_id)
    assert row["status"] == "running" and row["broker_instance"] == "replacement-owner"
    assert row["diagnostic"] is None and row["result"] is None and row["error_code"] is None
    assert broker.scheduler.release.await_args.kwargs == {"not_started": False}
    assert not any(call.args[0] == "model.finished" for call in broker.event.await_args_list)


async def seed_route(database):
    async with database.transaction() as connection:
        await connection.execute("INSERT INTO models.quota_groups "
            "(quota_group,concurrency,interactive_reserve,background_max) VALUES ('quota',2,0,2)")
        await connection.execute("INSERT INTO models.providers "
            "(provider_id,name,base_url,credential_ref,protocol) "
            "VALUES ('provider','fixture','https://fixture.invalid','fixture','openai.chat')")
        await connection.execute("INSERT INTO models.routes (route_id,provider_id,model,quota_group) "
            "VALUES ('route','provider','model','quota')")


@pytest.mark.parametrize("http_status,expected_status,expected_dispatch", [
    (429, "failed", "submitted"), (502, "unknown", "unknown"), (503, "unknown", "unknown"),
])
async def test_http_failures_keep_original_attempt_cost_uncertainty_and_cooldown(
    database, http_status, expected_status, expected_dispatch
):
    await seed_route(database)
    scheduler = QuotaScheduler({"quota": QuotaPolicy("quota", 2, 0, 2)})
    await scheduler.start()
    before = datetime.now(UTC)
    try:
        broker, original, row, receipt = await run_attempt(database,
            response_post(b"unread-private-error-body", status=http_status, headers={"Retry-After": "120"}),
            configure=lambda value: setattr(value, "scheduler", scheduler))
        assert (receipt.attempt_id, receipt.status, receipt.dispatch, receipt.error_code) == (
            original.attempt_id, expected_status, expected_dispatch, "upstream_http_" + str(http_status))
        assert receipt.request_digest and receipt.data_generation and receipt.finished_at is not None
        assert receipt.diagnostic["transport"]["evidence"] == "sent_or_unknown"
        assert receipt.diagnostic["failure_stage"] == "unknown"
        assert receipt.diagnostic["original_output_reusable"] is None
        assert receipt.usage.metering == "unknown" and receipt.usage.input_tokens is None
        assert receipt.hold_until is None and row["charged_units"] == {"tokens": 2053}
        assert row["result"] is None and "private" not in json.dumps(receipt.diagnostic).lower()
        assert len(scheduler.groups["quota"].window) == 1
        assert scheduler.groups["quota"].window[0][1] == original.attempt_id
        async with database.transaction(read_only=True) as connection:
            group = await (await connection.execute("SELECT blocked_until FROM models.quota_cooldowns "
                "WHERE quota_group='quota'")).fetchone()
            selected_route = await (await connection.execute("SELECT blocked_until FROM models.route_cooldowns "
                "WHERE route_id='route'")).fetchone()
        if http_status == 429:
            assert selected_route is None and group["blocked_until"] >= before + timedelta(seconds=120)
            assert scheduler.groups["quota"].blocked_until == pytest.approx(group["blocked_until"].timestamp())
        elif http_status == 503:
            assert group is None and selected_route["blocked_until"] >= before + timedelta(seconds=120)
            assert scheduler.groups["quota"].route_blocked["route"] == pytest.approx(
                selected_route["blocked_until"].timestamp())
        else:
            assert group is None and selected_route is None
        assert (await broker.status(original.attempt_id)).error_code == receipt.error_code
    finally:
        await scheduler.close()
