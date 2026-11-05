"""Original-attempt observations and successful-result reuse stay distinguishable."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import aiohttp
from aiohttp import web
import pytest
import pytest_asyncio

from tianshu.models.broker import ModelBroker
from tianshu.models.catalog import RoutePlan
from tianshu.models.client import ModelClient
from tianshu.models.http import create_broker_app
from tianshu.models.ledger import submission_digest
from tianshu.models.types import ModelReceipt, ModelRequest, ModelUsage
from tianshu.runtime.errors import AppError
from tianshu.runtime.serialization import wire
from tianshu.types import OriginKind, RequestBudget, ServiceClass, SourceRef, TrustedContext


@pytest_asyncio.fixture
async def models_port(database):
    state = {"revoked": False, "requests": [], "events": []}

    async def validate_submission(_context, refs):
        if refs and state["revoked"]:
            raise AppError("source_unavailable", status=403)

    broker = ModelBroker(database, None, None, validate_submission=validate_submission)
    app = create_broker_app(broker, "synthetic-token")

    class Events:
        async def emit(self, event, outcome, **fields):
            state["events"].append((event, outcome, fields))

    @web.middleware
    async def capture_lookup(request, handler):
        try:
            return await handler(request)
        finally:
            state["requests"].append((request.get("model_attempt_id"),
                request.get("model_lookup_phase"), request.get("model_lookup_state")))

    app["event_log"] = Events()
    app.middlewares.append(capture_lookup)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    client = ModelClient(f"http://127.0.0.1:{port}", "synthetic-token")
    await client.open()
    try:
        yield broker, client, state
    finally:
        await client.close()
        await runner.cleanup()


def context():
    return TrustedContext("fixture", "actor", "person", "conversation", ("scope",),
                          ("scope",), 1, OriginKind.HUMAN)


async def create_attempt(broker, identity, status):
    async with broker.database.transaction(read_only=True) as conn:
        row = await (await conn.execute("SELECT id FROM runtime.installation_generation WHERE singleton")).fetchone()
    generation = str(row["id"])
    original = ModelRequest("intent:" + identity, identity, "reflect", ServiceClass.ELASTIC, context(),
        RequestBudget(datetime.now(UTC) + timedelta(minutes=2)), (),
        source_refs=(SourceRef("source", 1),), data_generation=generation, execution_timeout_seconds=30)
    plan = RoutePlan("route", 1, "provider", "model", "openai.chat", "", "", "quota", frozenset({"text"}), {})
    await broker.ledger.create(original, plan, {"tokens": 10}, frozen=original, data_generation=generation)
    if status != "queued":
        await broker.ledger.started(identity, None, deadline_at=datetime.now(UTC) + timedelta(seconds=30))
    if status in {"succeeded", "unknown", "failed"}:
        body = {"parts": [{"kind": "text", "text": "saved result"}],
                "native_response": {"id": "provider-result"},
                "completion": {"state": "complete", "stop_reasons": ["stop"], "provider_status": None}}
        await broker.ledger.finish(identity, status, result=body if status == "succeeded" else None,
            usage=ModelUsage(metering="unknown"), error_code="transport_unknown" if status == "unknown" else None)
    return original


async def test_expected_absence_is_successful_but_observation_absence_remains_a_404(models_port):
    broker, client, state = models_port
    missing = "attempt:missing"
    value = await client._json("/internal/v1/models/attempts/" + missing + "?phase=pre_submit",
                               control=True, method="GET")
    assert value == {"attempt_id": missing, "lookup_phase": "pre_submit", "lookup_state": "absent",
                     "receipt": None, "cancel_requested": None}
    assert state["requests"][-1] == (missing, "pre_submit", "absent")
    assert state["events"][-1][0:2] == ("http.finished", "succeeded")
    assert await client.status(missing, phase="pre_submit") is None
    assert await broker.status(missing, phase="pre_submit") is None
    original = await create_attempt(broker, "attempt:existing", "queued")
    existing = await client.status(original.attempt_id, phase="pre_submit")
    assert existing.attempt_id == original.attempt_id and existing.status == "queued"
    assert state["requests"][-1] == (original.attempt_id, "pre_submit", "found")

    with pytest.raises(AppError, match="attempt_not_found") as found:
        await client._json("/internal/v1/models/attempts/" + missing, control=True, method="GET")
    assert found.value.status == 404
    assert found.value.details["attempt_id"] == missing and found.value.details["phase"] == "observe"
    assert state["requests"][-1] == (missing, "observe", "absent")
    assert state["events"][-1][0:2] == ("http.finished", "failed")
    assert await client.status(missing) is None  # Compatibility for this exact, observed 404 only.
    with pytest.raises(AppError, match="attempt_not_found") as strict:
        await client.require_receipt(missing)
    assert strict.value.details["original_observation"] == "missing"
    assert strict.value.details["action"] == "observe_original_attempt"
    with pytest.raises(AppError, match="invalid_model_lookup_phase"):
        await client.status(missing, phase="after_submit")
    broker.ledger.get = AsyncMock(side_effect=AppError("broker_unavailable", status=503))
    with pytest.raises(AppError, match="broker_unavailable"):
        await client.status(missing, phase="pre_submit")
    assert state["requests"][-1] == (missing, "pre_submit", "unavailable")
    assert state["events"][-1][0:2] == ("http.finished", "failed")


async def test_client_does_not_turn_unrelated_404_or_malformed_response_into_absence():
    client = ModelClient("http://unused", "synthetic")
    client._json = AsyncMock(side_effect=AppError("route_not_found", status=404))
    with pytest.raises(AppError, match="route_not_found"):
        await client.status("attempt")
    client._json = AsyncMock(return_value={"receipt": None})
    with pytest.raises(AppError, match="invalid_model_status_response"):
        await client.status("attempt", phase="pre_submit")
    other = ModelReceipt("other", "intent", "running", "submitted", "route", 1,
                         "provider", "model", "openai.chat", "quota")
    client._json = AsyncMock(return_value={"receipt": wire(other)})
    with pytest.raises(AppError, match="model_attempt_identity_conflict"):
        await client.status("attempt")
    with pytest.raises(AppError, match="invalid_model_status_response"):
        await client.status("other", phase="pre_submit")


@pytest.mark.parametrize("status", ["queued", "running", "unknown", "succeeded"])
async def test_lost_submit_ack_observes_the_original_attempt_without_resending(status):
    request = ModelRequest("intent", "original", "reflect", ServiceClass.ELASTIC, context(),
        RequestBudget(datetime.now(UTC) + timedelta(minutes=1)), (), execution_timeout_seconds=30)
    original = ModelReceipt(request.attempt_id, request.intent_id, status,
        "unknown" if status == "unknown" else "not_started" if status == "queued" else "submitted",
        "route", 1, "provider", "model",
        "openai.chat", "quota", request_digest=submission_digest(request))
    client = ModelClient("http://unused", "synthetic")
    client._json = AsyncMock(side_effect=aiohttp.ServerDisconnectedError("lost acknowledgement"))
    client.status = AsyncMock(return_value=original)
    assert await client.submit(request) == original
    client._json.assert_awaited_once()
    client.status.assert_awaited_once_with(request.attempt_id)


@pytest.mark.parametrize("observation,expected", [(None, "missing"), (AppError("route_not_found", status=404), "unavailable")])
async def test_missing_or_unavailable_receipt_does_not_prove_the_submit_was_unsent(observation, expected):
    request = ModelRequest("intent", "original", "reflect", ServiceClass.ELASTIC, context(),
        RequestBudget(datetime.now(UTC) + timedelta(minutes=1)), (), execution_timeout_seconds=30)
    client = ModelClient("http://unused", "synthetic")
    client._json = AsyncMock(side_effect=aiohttp.ServerDisconnectedError("lost acknowledgement"))
    client.status = AsyncMock(side_effect=observation) if isinstance(observation, Exception) else AsyncMock(return_value=None)
    with pytest.raises(AppError, match="model_submission_ack_unknown") as error:
        await client.submit(request)
    assert error.value.details["attempt_id"] == request.attempt_id
    assert error.value.details["original_observation"] == expected
    assert error.value.details["action"] == "observe_original_attempt"
    client._json.assert_awaited_once()


async def test_success_reuse_keeps_original_identity_usage_and_current_source_checks(models_port):
    broker, client, state = models_port
    original = await create_attempt(broker, "success", "succeeded")
    broker.submit = AsyncMock(side_effect=AssertionError("reuse cannot submit another attempt"))
    options = {"context": original.context, "expected_request_digest": submission_digest(original),
               "expected_data_generation": original.data_generation}
    first = await client.reusable_result(original.attempt_id, **options)
    async with broker.database.transaction() as conn:
        await conn.execute("CREATE TABLE models.rbf_test_applications (attempt_id text PRIMARY KEY, body text)")
    with pytest.raises(RuntimeError, match="business commit interrupted"):
        async with broker.database.transaction() as conn:
            await conn.execute("INSERT INTO models.rbf_test_applications VALUES(%s,%s)",
                               (first.receipt.attempt_id, first.parts[0].text))
            raise RuntimeError("business commit interrupted")
    second = await client.reusable_result(original.attempt_id, **options)
    assert first == second and first.parts[0].text == "saved result"
    async with broker.database.transaction() as conn:
        await conn.execute("INSERT INTO models.rbf_test_applications VALUES(%s,%s)",
                           (second.receipt.attempt_id, second.parts[0].text))
        saved = await (await conn.execute("SELECT count(*) AS count FROM models.rbf_test_applications")).fetchone()
    assert saved["count"] == 1
    assert first.receipt.attempt_id == original.attempt_id and first.receipt.provider_id == "provider"
    assert first.receipt.usage.metering == "unknown"
    assert first.receipt.usage.input_tokens is None and first.receipt.usage.output_tokens is None
    broker.submit.assert_not_awaited()

    with pytest.raises(AppError, match="attempt_identity_conflict"):
        await client.reusable_result(original.attempt_id, **{**options, "expected_request_digest": "different"})
    with pytest.raises(AppError, match="data_generation_expired"):
        await client.reusable_result(original.attempt_id, **{**options, "expected_data_generation": str(uuid4())})
    with pytest.raises(AppError, match="model_result_context_forbidden"):
        await client.reusable_result(original.attempt_id, **{**options, "context": replace(original.context, conversation_id="other")})
    state["revoked"] = True
    with pytest.raises(AppError, match="source_unavailable"):
        await client.reusable_result(original.attempt_id, **options)
    state["revoked"] = False
    await broker.ledger.request_cancel(original.attempt_id, reason="explicit_cancel")
    with pytest.raises(AppError, match="model_attempt_cancelled"):
        await client.reusable_result(original.attempt_id, **options)
    broker.submit.assert_not_awaited()


@pytest.mark.parametrize("status,code", [("queued", "model_result_pending"), ("running", "model_result_pending"),
                                          ("unknown", "model_result_unknown"), ("failed", "model_result_not_succeeded")])
async def test_only_successful_terminal_originals_are_reusable(models_port, status, code):
    broker, client, _state = models_port
    original = await create_attempt(broker, "attempt-" + status, status)
    with pytest.raises(AppError, match=code):
        await client.reusable_result(original.attempt_id, context=original.context)
    current = await client.require_receipt(original.attempt_id)
    assert current.status == status
    if status == "unknown":
        assert current.dispatch == "unknown" and current.usage.metering == "unknown"


async def test_missing_receipt_and_missing_saved_body_are_not_reusable(models_port):
    broker, client, _state = models_port
    with pytest.raises(AppError, match="attempt_not_found") as missing:
        await client.reusable_result("absent", context=context())
    assert missing.value.details["original_observation"] == "missing"
    original = await create_attempt(broker, "no-body", "succeeded")
    async with broker.database.transaction() as conn:
        await conn.execute("UPDATE models.attempts SET result=NULL WHERE attempt_id=%s", (original.attempt_id,))
    with pytest.raises(AppError, match="model_result_unavailable"):
        await client.reusable_result(original.attempt_id, context=original.context)


async def test_original_success_from_previous_installation_cannot_be_reused(models_port):
    broker, client, _state = models_port
    original = await create_attempt(broker, "previous-generation", "succeeded")
    async with broker.database.transaction() as conn:
        await conn.execute("UPDATE runtime.installation_generation SET id=%s WHERE singleton", (str(uuid4()),))
    with pytest.raises(AppError, match="data_generation_expired"):
        await client.reusable_result(original.attempt_id, context=original.context,
                                     expected_data_generation=original.data_generation)
    assert (await client.require_receipt(original.attempt_id)).status == "succeeded"
