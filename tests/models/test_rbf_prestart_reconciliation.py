"""Reconciliation is explicit, fenced and does not create another model attempt."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from aiohttp import ClientSession, web
import pytest

from test_rbf_prestart_waits import dependency_wait, route, successful_transport, terminal_row
from test_rbf_start_ownership import broker_with_fixture_transport, original_request
from tianshu.models.client import ModelClient
from tianshu.models.http import create_broker_app
from tianshu.models.management import register_routes
from tianshu.runtime.api import error_middleware
from tianshu.runtime.errors import AppError
from tianshu.runtime.recovery_policy import RecoveryWait


def operator(original):
    return replace(original.context, principal_id="authorized-operator", permissions=frozenset({"models.manage"}))


def options(original_receipt, context, *, client_id="reconcile-1", evidence="incident:verified-rollback"):
    return {"context": context, "expected_version": original_receipt.recovery["version"],
        "expected_request_digest": original_receipt.request_digest,
        "expected_data_generation": original_receipt.data_generation,
        "client_request_id": client_id, "evidence_ref": evidence, "decision": "authorized_resume_original"}


async def test_receipt_exposes_wait_and_interrupted_check_without_changing_original_identity(database):
    original, sent = original_request(), []
    old = broker_with_fixture_transport(database, route(), successful_transport(sent))
    await old.ledger.create(original, route(), {"tokens": 2053}, frozen=original)
    future = RecoveryWait("recovery_not_due", datetime.now(UTC) + timedelta(minutes=2), dependency="sources")
    future.transaction_rollback_confirmed = True
    future.remote_rollback_confirmed = True
    assert await old.ledger.save_prestart_wait(original.attempt_id, future)
    queued = await old.status(original.attempt_id)
    assert queued.attempt_id == original.attempt_id and queued.status == "queued"
    assert queued.recovery["state"] == "scheduled" and queued.recovery["requires_reconciliation"] is False
    assert queued.recovery["version"] > 0 and queued.recovery["dependency"] == "sources"

    other = replace(original, attempt_id="interrupted", intent_id="interrupted")
    await old.ledger.create(other, route(), {"tokens": 2053}, frozen=other)
    _, claimed, _ = await old.ledger.claim_queued_submission(other)
    assert claimed
    new = broker_with_fixture_transport(database, route(), successful_transport(sent))
    app = create_broker_app(new, "fixture")
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    client = ModelClient("http://127.0.0.1:" + str(site._server.sockets[0].getsockname()[1]), "fixture")
    await client.open()
    try:
        observed = await client.status(other.attempt_id)
        assert observed.attempt_id == other.attempt_id and observed.dispatch == "not_started"
        assert observed.recovery["state"] == "checking"
        assert observed.recovery["requires_reconciliation"] is True
        assert observed.recovery["version"] > 0 and observed.request_digest
    finally:
        await client.close()
        await runner.cleanup()


async def test_authorized_reconciliation_is_fenced_audited_and_idempotent(database):
    original, sent = original_request(), []
    entered, release = asyncio.Event(), asyncio.Event()

    async def chunks(_size):
        await release.wait()
        yield json.dumps({"id": "provider-result", "choices": [{"index": 0,
            "message": {"role": "assistant", "content": "original answer"}, "finish_reason": "stop"}]}).encode()

    @asynccontextmanager
    async def post(_url, **_kwargs):
        sent.append("provider")
        entered.set()
        yield SimpleNamespace(status=200, content=SimpleNamespace(iter_chunked=chunks))

    old = broker_with_fixture_transport(database, route(), post)
    await old.ledger.create(original, route(), {"tokens": 2053}, frozen=original)
    assert await old.ledger.save_prestart_wait(original.attempt_id, dependency_wait(remote=False))
    new = broker_with_fixture_transport(database, route(), post)
    new.validate_persistence = AsyncMock(return_value=True)
    app = create_broker_app(new, "fixture")
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    client = ModelClient("http://127.0.0.1:" + str(site._server.sockets[0].getsockname()[1]), "fixture")
    await client.open()
    try:
        before = await client.status(original.attempt_id)
        assert before.recovery["state"] == "observe_only" and before.recovery["requires_reconciliation"] is True
        assert before.recovery["rollback_confirmed"] is False
        command = options(before, operator(original))
        with pytest.raises(AppError, match="model_prestart_reconciliation_conflict"):
            await client.reconcile_prestart(original.attempt_id,
                **{**command, "expected_version": command["expected_version"] + 1, "client_request_id": "stale"})
        assert not sent and (await client.status(original.attempt_id)).recovery == before.recovery

        await client.reconcile_prestart(original.attempt_id, **command)
        await asyncio.wait_for(entered.wait(), 5)
        with pytest.raises(AppError, match="attempt_owner_changed"):
            await old.ledger.started(original.attempt_id, None, deadline_at=datetime.now(UTC) + timedelta(seconds=30))
        replay = await client.reconcile_prestart(original.attempt_id, **command)
        assert replay.attempt_id == original.attempt_id and replay.status == "running" and sent == ["provider"]
        with pytest.raises(AppError, match="client_request_id_conflict"):
            await client.reconcile_prestart(original.attempt_id, **{**command, "evidence_ref": "other-evidence"})
        async with database.transaction(control=True, read_only=True) as connection:
            audit = await (await connection.execute(
                "SELECT * FROM models.prestart_reconciliations WHERE attempt_id=%s", (original.attempt_id,))).fetchall()
        assert len(audit) == 1 and audit[0]["operator_principal_id"] == "authorized-operator"
        assert audit[0]["evidence_ref"] == command["evidence_ref"] and audit[0]["prior_state"] == "observe_only"
        assert audit[0]["prior_wait"]["remote_rollback_confirmed"] is False
        release.set()
        result = await terminal_row(new.ledger, original.attempt_id)
        if original.attempt_id in new.drivers:
            await asyncio.wait_for(new.drivers[original.attempt_id][0], 5)
        assert result["status"] == "succeeded" and result["result"]["parts"][0]["text"] == "original answer"
        assert sent == ["provider"] and result["attempt_id"] == original.attempt_id
    finally:
        release.set()
        for task, *_ in list(new.drivers.values()):
            if not task.done():
                task.cancel()
        await asyncio.gather(*(item[0] for item in list(new.drivers.values())), return_exceptions=True)
        await client.close()
        await runner.cleanup()


async def test_reconciliation_requires_current_authority_and_rejects_cancel_or_active_check(database):
    original, sent = original_request(), []
    old = broker_with_fixture_transport(database, route(), successful_transport(sent))
    await old.ledger.create(original, route(), {"tokens": 2053}, frozen=original)
    _, claimed, _ = await old.ledger.claim_queued_submission(original)
    assert claimed
    original_receipt = await old.status(original.attempt_id)
    old.validate_persistence = AsyncMock(return_value=True)
    unauthorized = replace(operator(original), permissions=frozenset())
    with pytest.raises(AppError, match="forbidden"):
        await old.reconcile_prestart(original.attempt_id, **options(original_receipt, unauthorized))
    with pytest.raises(AppError, match="attempt_identity_conflict"):
        await old.reconcile_prestart(original.attempt_id,
            **{**options(original_receipt, operator(original)), "expected_request_digest": "incorrect"})

    old.drivers[original.attempt_id] = (asyncio.current_task(), None, "fixture")
    try:
        with pytest.raises(AppError, match="model_prestart_check_active"):
            await old.reconcile_prestart(original.attempt_id, **options(original_receipt, operator(original)))
    finally:
        old.drivers.pop(original.attempt_id, None)
    old.validate_persistence = AsyncMock(return_value=False)
    with pytest.raises(AppError, match="forbidden"):
        await old.reconcile_prestart(original.attempt_id, **options(original_receipt, operator(original)))

    async def original_source_revoked(_connection, context, _refs):
        return context.principal_id != original.context.principal_id

    old.validate_persistence = original_source_revoked
    with pytest.raises(AppError, match="source_unavailable"):
        await old.reconcile_prestart(original.attempt_id, **options(original_receipt, operator(original)))
    old.validate_persistence = AsyncMock(return_value=True)
    await old.ledger.request_cancel(original.attempt_id, reason="explicit_cancel")
    with pytest.raises(AppError, match="model_prestart_reconciliation_conflict"):
        await old.reconcile_prestart(original.attempt_id, **options(original_receipt, operator(original)))
    assert not sent and (await old.ledger.get(original.attempt_id))["status"] == "queued"


async def test_reconciliation_does_not_reopen_a_submitted_unknown(database):
    original, sent = original_request(), []
    broker = broker_with_fixture_transport(database, route(), successful_transport(sent))
    broker.validate_persistence = AsyncMock(return_value=True)
    await broker.ledger.create(original, route(), {"tokens": 2053}, frozen=original)
    before = await broker.status(original.attempt_id)
    await broker.ledger.started(original.attempt_id, None, deadline_at=datetime.now(UTC) + timedelta(seconds=30))
    await broker.ledger.finish(original.attempt_id, "unknown", error_code="transport_unknown")
    command = {"context": operator(original), "expected_version": 0,
        "expected_request_digest": before.request_digest, "expected_data_generation": before.data_generation,
        "client_request_id": "unknown-must-not-reopen", "evidence_ref": "observation:original",
        "decision": "authorized_resume_original"}
    with pytest.raises(AppError, match="model_prestart_reconciliation_conflict"):
        await broker.reconcile_prestart(original.attempt_id, **command)
    result = await broker.ledger.get(original.attempt_id)
    assert result["status"] == "unknown" and result["dispatch"] == "unknown" and not sent


async def test_inconsistent_queued_receipt_with_start_evidence_cannot_be_reopened(database):
    original, sent = original_request(), []
    broker = broker_with_fixture_transport(database, route(), successful_transport(sent))
    broker.validate_persistence = AsyncMock(return_value=True)
    await broker.ledger.create(original, route(), {"tokens": 2053}, frozen=original)
    assert await broker.ledger.save_prestart_wait(original.attempt_id, dependency_wait(remote=False))
    before = await broker.status(original.attempt_id)
    async with database.transaction(control=True) as connection:
        await connection.execute("UPDATE models.attempts SET started_at=now() WHERE attempt_id=%s", (original.attempt_id,))
    with pytest.raises(AppError, match="model_prestart_reconciliation_conflict"):
        await broker.reconcile_prestart(original.attempt_id, **options(before, operator(original)))
    await broker._wake_due_submissions()
    result = await broker.ledger.get(original.attempt_id)
    assert result["status"] == "queued" and result["started_at"] is not None and not sent


async def test_reconciliation_cannot_cross_the_original_data_generation(database):
    original, sent = original_request(), []
    broker = broker_with_fixture_transport(database, route(), successful_transport(sent))
    broker.validate_persistence = AsyncMock(return_value=True)
    await broker.ledger.create(original, route(), {"tokens": 2053}, frozen=original)
    assert await broker.ledger.save_prestart_wait(original.attempt_id, dependency_wait(remote=False))
    before = await broker.status(original.attempt_id)
    async with database.transaction(control=True) as connection:
        await connection.execute("UPDATE runtime.installation_generation SET id=%s WHERE singleton", (str(uuid4()),))
    with pytest.raises(AppError, match="data_generation_expired"):
        await broker.reconcile_prestart(original.attempt_id, **options(before, operator(original)))
    after = await broker.ledger.get(original.attempt_id)
    assert after["status"] == "queued" and after["recovery_state"] == "observe_only"
    assert str(after["data_generation"]) == before.data_generation and not sent


async def test_management_reconciliation_exposes_receipt_and_audit_not_request_body(database):
    original, sent = original_request(), []
    broker = broker_with_fixture_transport(database, route(), successful_transport(sent))
    broker.validate_persistence = AsyncMock(return_value=True)
    await broker.ledger.create(original, route(), {"tokens": 2053}, frozen=original)
    assert await broker.ledger.save_prestart_wait(original.attempt_id, dependency_wait(remote=False))
    authorized = operator(original)

    @web.middleware
    async def identity(request, handler):
        request["trusted_context"] = (original.context if request.headers.get("X-Unauthorised") else authorized)
        return await handler(request)

    app = web.Application(middlewares=[error_middleware, identity])
    register_routes(app, SimpleNamespace(database=database, models=broker))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    origin = "http://127.0.0.1:" + str(site._server.sockets[0].getsockname()[1])
    path = "/api/v1/models/attempts/" + original.attempt_id
    try:
        async with ClientSession() as client:
            async with client.get(origin + path) as response:
                assert response.status == 200
                receipt = (await response.json())["receipt"]
            assert receipt["recovery"]["requires_reconciliation"] is True
            command = {"expected_version": receipt["recovery"]["version"],
                "expected_request_digest": receipt["request_digest"], "expected_data_generation": receipt["data_generation"],
                "client_request_id": "public-reconcile", "evidence_ref": "observation:verified",
                "decision": "authorized_resume_original"}
            async with client.post(origin + path + "/prestart-reconciliation", json=command,
                                   headers={"X-Unauthorised": "yes"}) as response:
                assert response.status == 403
            async with client.post(origin + path + "/prestart-reconciliation", json=command) as response:
                assert response.status == 200
                result = await response.json()
            assert result["attempt_id"] == original.attempt_id
            async with client.get(origin + path + "/prestart-reconciliations") as response:
                assert response.status == 200
                history = await response.json()
            assert len(history["items"]) == 1 and history["items"][0]["evidence_ref"] == "observation:verified"
            assert "request" not in history["items"][0]
        settled = await terminal_row(broker.ledger, original.attempt_id)
        if original.attempt_id in broker.drivers:
            await asyncio.wait_for(broker.drivers[original.attempt_id][0], 5)
        assert settled["status"] == "succeeded" and sent == ["provider"]
    finally:
        for task, *_ in list(broker.drivers.values()):
            if not task.done():
                task.cancel()
        await asyncio.gather(*(item[0] for item in list(broker.drivers.values())), return_exceptions=True)
        await runner.cleanup()
