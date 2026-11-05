"""Typed waits survive result recovery and the trusted Models transport."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiohttp import web
import pytest

from tianshu.models.client import ModelClient
from tianshu.models.http import create_broker_app
from tianshu.models.ledger import context_fingerprint
from tianshu.models.results import read_result
from tianshu.models.types import ExternalRequest, ModelRequest
from tianshu.runtime.errors import AppError
from tianshu.runtime.recovery_policy import RecoveryWait
from tianshu.types import OriginKind, RequestBudget, ServiceClass, TrustedContext


def context():
    return TrustedContext("fixture", "actor", "person", "conversation", ("scope",),
                          ("scope",), 1, OriginKind.HUMAN)


def envelope(*, rollback_confirmed=False):
    return {"kind": "recovery_wait", "version": 1, "reason": "recovery_not_due",
        "retry_at": (datetime.now(UTC) + timedelta(minutes=1)).isoformat(),
        "dependency": "database:models", "rollback_confirmed": rollback_confirmed}


class JsonResponse:
    def __init__(self, status, value):
        self.status, self.value = status, value

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        pass

    async def json(self):
        return self.value


def client_for_response(status, error):
    client = ModelClient("http://models", "fixture")
    response = JsonResponse(status, {"error": error})
    client.control_session = SimpleNamespace(request=lambda *_args, **_kwargs: response)
    return client


@pytest.mark.parametrize("confirmed", [False, True])
@pytest.mark.parametrize("path", ["/internal/v1/models/attempts/original/result", "/internal/v1/resources"])
async def test_json_reconstructs_explicit_wait_with_remote_rollback_evidence(confirmed, path):
    wait = envelope(rollback_confirmed=confirmed)
    client = client_for_response(409, {"code": wait["reason"], "recovery_wait": wait})
    with pytest.raises(RecoveryWait) as caught:
        await client._json(path, control=True)
    assert caught.value.retry_at == datetime.fromisoformat(wait["retry_at"])
    assert caught.value.dependency == "database:models"
    assert caught.value.remote_rollback_confirmed is confirmed


@pytest.mark.parametrize("error,status", [
    ({"code": "recovery_not_due", "recovery_wait": {"kind": "recovery_wait", "version": 1}}, 409),
    ({"code": "other", "recovery_wait": envelope()}, 409),
    ({"code": "recovery_not_due", "recovery_wait": envelope()}, 503),
    ({"code": "recovery_not_due", "recovery_wait": {**envelope(), "version": 2}}, 409),
    ({"code": "recovery_not_due", "recovery_wait": {**envelope(), "rollback_confirmed": "true"}}, 409),
    ({"code": "recovery_not_due", "recovery_wait": {**envelope(), "retry_at": "2026-11-05T00:00:00"}}, 409),
])
async def test_invalid_typed_envelope_is_a_protocol_error(error, status):
    client = client_for_response(status, error)
    with pytest.raises(AppError, match="broker_protocol_error") as caught:
        await client._json("/internal/v1/models/attempts/original/result", control=True)
    assert caught.value.status == 502


async def test_untyped_409_remains_an_ordinary_application_error():
    client = client_for_response(409, {"code": "recovery_not_due"})
    with pytest.raises(AppError, match="recovery_not_due") as caught:
        await client._json("/internal/v1/models/attempts/original/result", control=True)
    assert type(caught.value) is AppError


async def test_resource_wait_does_not_cancel_the_original_attempt():
    wait = RecoveryWait("recovery_not_due", datetime.now(UTC) + timedelta(minutes=1), dependency="sources")
    client = ModelClient("http://models", "fixture")
    client._json = AsyncMock(side_effect=wait)
    client._cancel_cleanup = AsyncMock()
    request = ExternalRequest("intent", "original", context(),
        RequestBudget(datetime.now(UTC) + timedelta(minutes=1)), "quota", "image",
        ServiceClass.ELASTIC, "provider")
    with pytest.raises(RecoveryWait):
        async with client.resource(request):
            pytest.fail("a wait does not admit external work")
    client._cancel_cleanup.assert_not_awaited()


async def test_result_recheck_preserves_wait_instead_of_returning_empty_success():
    selected_context = context()
    row = {"principal_id": "fixture", "context_fingerprint": context_fingerprint(selected_context),
        "source_refs": [{"source_id": "source", "revision": 1, "locator": {}}],
        "result": {"parts": [{"kind": "text", "text": "saved result"}]},
        "attempt_id": "original", "intent_id": "intent", "status": "succeeded", "dispatch": "submitted",
        "route_id": "route", "route_revision": 1, "provider_id": "provider", "model": "model",
        "protocol": "openai.chat", "quota_group": "quota", "provider_handle": None,
        "queued_at": None, "started_at": None, "finished_at": None, "first_output_ms": None,
        "error_code": None}
    wait = RecoveryWait("recovery_not_due", datetime.now(UTC) + timedelta(minutes=1), dependency="sources")

    async def recheck(request):
        if request.source_refs:
            raise wait

    broker = SimpleNamespace(ledger=SimpleNamespace(get=AsyncMock(return_value=row)), recheck=recheck)
    with pytest.raises(RecoveryWait) as caught:
        await read_result(broker, "original", context=selected_context)
    assert caught.value is wait


async def test_internal_stream_carries_typed_wait_after_headers():
    wait = RecoveryWait("recovery_not_due", datetime.now(UTC) + timedelta(minutes=1), dependency="sources")

    class WaitingBroker:
        async def status(self, _attempt_id):
            return None

        async def stream(self, _request):
            raise wait
            yield  # The transport consumes an async generator.

    app = create_broker_app(WaitingBroker(), "fixture")
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    client = ModelClient("http://127.0.0.1:" + str(site._server.sockets[0].getsockname()[1]), "fixture")
    await client.open()
    request = ModelRequest("intent", "original", "chat", ServiceClass.INTERACTIVE, context(),
        RequestBudget(datetime.now(UTC) + timedelta(minutes=1)), ())
    try:
        with pytest.raises(RecoveryWait) as caught:
            _ = [event async for event in client.stream(request)]
        assert caught.value.reason == wait.reason and caught.value.retry_at == wait.retry_at
        assert caught.value.dependency == "sources" and caught.value.remote_rollback_confirmed is False
    finally:
        await client.close()
        await runner.cleanup()
