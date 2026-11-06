"""Only observed connector failures can prove that a model request was not sent."""

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
import errno
import json
import socket
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiohttp
from aiohttp.client_reqrep import ConnectionKey
from aiohttp import web
import pytest

from test_rbf_prestart_waits import route, terminal_row
from test_rbf_start_ownership import broker_with_fixture_transport, original_request
from tianshu.models.codec import receipt_from_wire
from tianshu.models.client import ModelClient
from tianshu.models.http import create_broker_app
from tianshu.models.http_timing import HttpTiming, TimedResponse, trace_config
from tianshu.models.transport_diagnostic import describe_transport
from tianshu.models.types import ModelReceipt
from tianshu.runtime.errors import AppError
from tianshu.runtime.serialization import wire


def key():
    return ConnectionKey("model.example", 8443, True, True, None, None, None)


class TLSCause(OSError):
    def __init__(self, reason):
        super().__init__(1, "private certificate text")
        self.reason = reason


class UntrustedConnector(aiohttp.ClientConnectorError):
    pass


def connector(kind, connection=None):
    connection = connection or key()
    if kind == "dns":
        return aiohttp.ClientConnectorDNSError(connection, socket.gaierror(-3, "private DNS failure"))
    if kind == "tls":
        return aiohttp.ClientConnectorSSLError(connection, TLSCause("CERTIFICATE_VERIFY_FAILED"))
    if kind == "certificate":
        return aiohttp.ClientConnectorCertificateError(connection, TLSCause("CERTIFICATE_VERIFY_FAILED"))
    return aiohttp.ClientConnectorError(connection, ConnectionRefusedError(errno.ECONNREFUSED, "private socket message"))


def fixture_key():
    return ConnectionKey("fixture.invalid", 443, True, True, None, None, None)


def connecting():
    timing = HttpTiming(None)
    timing.http_phase = "request"
    timing.connection_reused = False
    timing.send_signals_available = True
    timing.mark("connect_start")
    return timing


@pytest.mark.parametrize("kind,phase,error_class", [
    ("dns", "dns", "ClientConnectorDNSError"),
    ("connect", "connect", "ClientConnectorError"),
    ("tls", "tls", "ClientConnectorSSLError"),
    ("certificate", "tls", "ClientConnectorCertificateError"),
])
def test_connector_failure_has_specific_bounded_not_sent_evidence(kind, phase, error_class):
    diagnostic = describe_transport(connector(kind), connecting(),
        "https://private-user:private-password@model.example:8443/v1/secret-path?token=private-query")
    assert diagnostic == {"schema_version": "rbh.transport.v1", "phase": phase, "error_class": error_class,
        "os_errno": -3 if kind == "dns" else 1 if kind in {"tls", "certificate"} else errno.ECONNREFUSED,
        "tls_reason": "CERTIFICATE_VERIFY_FAILED" if kind in {"tls", "certificate"} else None,
        "host": "model.example", "port": 8443, "headers_sent": False, "body_sent": False,
        "evidence": "confirmed_not_sent"}
    serialized = json.dumps(diagnostic)
    for secret in ("private-user", "private-password", "secret-path", "private-query", "private socket message"):
        assert secret not in serialized


@pytest.mark.parametrize("point", ["connect_end", "headers_sent", "body_sent", "response_headers",
                                   "first_body_byte", "first_valid_output", "read_complete"])
def test_any_send_or_output_evidence_prevents_a_not_sent_claim(point):
    timing = connecting()
    timing.mark(point)
    diagnostic = describe_transport(connector("connect"), timing, "https://model.example")
    assert diagnostic["evidence"] == "sent_or_unknown"
    assert diagnostic["headers_sent"] is not False and diagnostic["body_sent"] is not False


@pytest.mark.parametrize("missing", ["connect_start", "send_trace", "reuse", "handle", "output", "first_output"])
def test_missing_or_contradictory_attempt_evidence_remains_unknown(missing):
    timing = connecting()
    values = {}
    if missing == "connect_start":
        timing.points.pop("connect_start")
    elif missing == "send_trace":
        timing.send_signals_available = False
    elif missing == "reuse":
        timing.connection_reused = True
    elif missing == "handle":
        values["provider_handle"] = "original-handle"
    elif missing == "output":
        values["output_observed"] = True
    else:
        values["first_output_ms"] = 0.0
    assert describe_transport(connector("connect"), timing, "https://model.example", **values)["evidence"] == "sent_or_unknown"


@pytest.mark.parametrize("error", [
    aiohttp.ClientProxyConnectionError(key(), OSError(errno.ECONNREFUSED, "private")),
    UntrustedConnector(key(), OSError(errno.ECONNREFUSED, "private")),
    aiohttp.ConnectionTimeoutError("private"), aiohttp.SocketTimeoutError("private"),
    aiohttp.ServerFingerprintMismatch(b"private-expected", b"private-observed", "model.example", 443),
    AppError("fixture", status=503), OSError(errno.ECONNRESET, "private"),
])
def test_proxy_timeout_and_unrelated_errors_do_not_prove_no_send(error):
    assert describe_transport(error, connecting(), "https://model.example")["evidence"] == "sent_or_unknown"


def test_tls_reason_is_allowlisted_and_endpoint_never_contains_url_secrets():
    error = aiohttp.ClientConnectorSSLError(key(), TLSCause("PRIVATE_TOKEN_SHOULD_NOT_APPEAR"))
    diagnostic = describe_transport(error, connecting(), "https://user:secret@model.example/path?token=secret")
    assert diagnostic["tls_reason"] is None and diagnostic["host"] == "model.example"
    assert "secret" not in json.dumps(diagnostic).lower() and "PRIVATE_TOKEN" not in json.dumps(diagnostic)
    bad_key = ConnectionKey("bad_host", 443, True, True, None, None, None)
    bad_error = aiohttp.ClientConnectorSSLError(bad_key, TLSCause("PRIVATE_TOKEN_SHOULD_NOT_APPEAR"))
    bad = describe_transport(bad_error, connecting(), "https://user:secret@bad_host/path?token=secret")
    assert bad["host"] is None and bad["port"] is None


async def test_real_aiohttp_trace_marks_a_synthetic_dns_failure_before_any_send():
    class Resolver(aiohttp.abc.AbstractResolver):
        async def resolve(self, _host, _port=0, family=socket.AF_INET):
            raise socket.gaierror(-3, "private resolver message")

        async def close(self):
            pass

    timing = HttpTiming(None)
    connector = aiohttp.TCPConnector(resolver=Resolver(), use_dns_cache=False)
    async with aiohttp.ClientSession(connector=connector, trace_configs=[trace_config()]) as session:
        with pytest.raises(aiohttp.ClientConnectorDNSError) as caught:
            async with TimedResponse(session.post("https://model.example/private?token=secret", json={},
                                                trace_request_ctx=timing), timing):
                pytest.fail("a failed synthetic resolver must not send a request")
    record = timing.record()["points_ms"]
    assert record["connect_start"] is not None and record["dns_start"] is not None
    assert record["connect_end"] is None and record["headers_sent"] is None and record["body_sent"] is None
    diagnostic = describe_transport(caught.value, timing, "https://model.example/private?token=secret")
    assert diagnostic["phase"] == "dns" and diagnostic["evidence"] == "confirmed_not_sent"
    assert "secret" not in json.dumps(diagnostic)


@pytest.mark.parametrize("original_error,close_error,expected_phase", [
    (None, True, "close"), ("body", False, "response"), ("body", True, "close"),
])
async def test_context_exit_errors_are_distinct_from_response_errors(original_error, close_error, expected_phase):
    timing = connecting()

    class Context:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *_args):
            if close_error:
                raise connector("connect")
            return False

    with pytest.raises((aiohttp.ClientConnectorError, RuntimeError)) as caught:
        async with TimedResponse(Context(), timing):
            timing.mark("response_headers")
            if original_error:
                raise RuntimeError("private body error")
    diagnostic = describe_transport(caught.value, timing, "https://model.example")
    assert diagnostic["phase"] == expected_phase and diagnostic["evidence"] == "sent_or_unknown"


async def run_broker_failure(database, post, *, stream=False, recheck_behaviors=None):
    original = original_request()
    broker = broker_with_fixture_transport(database, route(), post)
    if recheck_behaviors is not None:
        broker.recheck_behaviors = recheck_behaviors
    await broker.ledger.create(original, route(), {"tokens": 2053}, frozen=original, streaming=stream)
    accepted = await broker._start_driver(original, streaming=stream, resume=True)
    await asyncio.wait_for(accepted, 5)
    row = await terminal_row(broker.ledger, original.attempt_id)
    if original.attempt_id in broker.drivers:
        await asyncio.wait_for(broker.drivers[original.attempt_id][0], 5)
    return broker, original, row, await broker.status(original.attempt_id)


@pytest.mark.parametrize("kind", ["dns", "connect", "tls", "certificate"])
async def test_broker_persists_original_failed_receipt_with_confirmed_connector_evidence(database, kind):
    @asynccontextmanager
    async def post(_url, **kwargs):
        timing = kwargs["trace_request_ctx"]
        timing.connection_reused, timing.send_signals_available = False, True
        timing.mark("connect_start")
        raise connector(kind, fixture_key())
        yield

    broker, original, row, saved = await run_broker_failure(database, post)
    assert saved.attempt_id == original.attempt_id and saved.status == "failed" and saved.dispatch == "not_started"
    assert saved.error_code == "transport_not_sent" and saved.finished_at is not None
    assert saved.request_digest and saved.data_generation and saved.provider_handle is None and saved.first_output_ms is None
    assert row["diagnostic"]["category"] == "not_sent"
    assert saved.diagnostic["transport"]["evidence"] == "confirmed_not_sent"
    assert saved.diagnostic["failure_stage"] == "transport"
    assert saved.diagnostic["original_output_reusable"] is False
    assert saved.http_timing["points_ms"]["connect_start"] is not None
    assert saved.http_timing["points_ms"]["connect_end"] is None
    assert receipt_from_wire(wire(saved)) == saved
    assert broker.scheduler.release.await_args.kwargs["not_started"] is True


async def test_connector_error_without_trace_stays_unknown(database):
    @asynccontextmanager
    async def post(_url, **_kwargs):
        raise connector("connect", fixture_key())
        yield

    _broker, _original, _row, saved = await run_broker_failure(database, post)
    assert saved.status == "unknown" and saved.dispatch == "unknown"
    assert saved.diagnostic["transport"]["evidence"] == "sent_or_unknown"


async def test_original_receipt_transport_evidence_survives_models_http_rpc(database):
    @asynccontextmanager
    async def post(_url, **kwargs):
        timing = kwargs["trace_request_ctx"]
        timing.connection_reused, timing.send_signals_available = False, True
        timing.mark("connect_start")
        raise connector("connect", fixture_key())
        yield

    broker, original, _row, expected = await run_broker_failure(database, post)
    runner = web.AppRunner(create_broker_app(broker, "fixture-token"))
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    client = ModelClient("http://127.0.0.1:" + str(site._server.sockets[0].getsockname()[1]), "fixture-token")
    await client.open()
    try:
        observed = await client.status(original.attempt_id)
        assert observed == expected and observed.diagnostic["transport"]["evidence"] == "confirmed_not_sent"
        assert observed.request_digest == expected.request_digest and observed.data_generation == expected.data_generation
    finally:
        await client.close()
        await runner.cleanup()


async def test_output_then_connector_error_stays_unknown_and_preserves_original_handle(database):
    async def chunks(_size):
        yield b'data: {"id":"original-provider-id","choices":[{"index":0,"delta":{"content":"piece"}}]}\n\n'
        raise connector("connect", fixture_key())

    @asynccontextmanager
    async def post(_url, **kwargs):
        timing = kwargs["trace_request_ctx"]
        timing.mark("headers_sent")
        timing.mark("body_sent")
        yield SimpleNamespace(status=200, content=SimpleNamespace(iter_chunked=chunks))

    _broker, _original, _row, saved = await run_broker_failure(database, post, stream=True)
    assert saved.status == "unknown" and saved.dispatch == "unknown"
    assert saved.provider_handle == "original-provider-id" and saved.first_output_ms is not None
    assert saved.diagnostic["transport"]["phase"] == "response"
    assert saved.diagnostic["transport"]["evidence"] == "sent_or_unknown"


async def test_close_error_cannot_reclassify_a_completed_response_as_not_sent(database):
    async def chunks(_size):
        yield json.dumps({"id": "original-provider-id", "choices": [{"index": 0,
            "message": {"role": "assistant", "content": "answer"}, "finish_reason": "stop"}]}).encode()

    class Context:
        async def __aenter__(self):
            return SimpleNamespace(status=200, content=SimpleNamespace(iter_chunked=chunks))

        async def __aexit__(self, *_args):
            raise connector("connect", fixture_key())

    def post(_url, **kwargs):
        kwargs["trace_request_ctx"].mark("headers_sent")
        kwargs["trace_request_ctx"].mark("body_sent")
        return Context()

    _broker, _original, _row, saved = await run_broker_failure(database, post)
    assert saved.status == "failed" and saved.dispatch == "submitted"
    assert saved.error_code == "transport_unknown"
    assert saved.provider_handle == "original-provider-id" and saved.first_output_ms is not None
    assert saved.diagnostic["failure_stage"] == "transport"
    assert saved.diagnostic["transport"]["phase"] == "close"
    assert saved.diagnostic["transport"]["evidence"] == "sent_or_unknown"


async def test_http_response_error_is_known_but_not_a_connector_failure(database):
    @asynccontextmanager
    async def post(_url, **_kwargs):
        yield SimpleNamespace(status=400, headers={})

    _broker, _original, _row, saved = await run_broker_failure(database, post)
    assert saved.status == "failed" and saved.dispatch == "submitted" and saved.error_code == "upstream_http_400"
    assert saved.diagnostic["transport"]["phase"] == "response"
    assert saved.diagnostic["transport"]["evidence"] == "sent_or_unknown"


async def test_pre_network_app_error_keeps_its_code_without_transport_evidence(database):
    @asynccontextmanager
    async def post(_url, **_kwargs):
        pytest.fail("source failure must not open an HTTP connection")
        yield

    behavior = AsyncMock(side_effect=[None, AppError("behavior_dependency_invalid", status=409)])
    _broker, _original, _row, saved = await run_broker_failure(database, post, recheck_behaviors=behavior)
    assert saved.status == "failed" and saved.dispatch == "not_started"
    assert saved.error_code == "behavior_dependency_invalid"
    assert "transport" not in saved.diagnostic


def test_existing_receipt_without_transport_diagnostic_still_round_trips():
    original = ModelReceipt("old-attempt", "intent", "failed", "not_started", "route", 1,
        "provider", "model", "openai.chat", "quota", finished_at=datetime.now(UTC),
        error_code="transport_not_sent", diagnostic={"category": "not_sent", "part_count": 0},
        http_timing={"points_ms": {"connect_start": 1, "connect_end": None}})
    saved = receipt_from_wire(wire(original))
    assert saved == original and "transport" not in saved.diagnostic
