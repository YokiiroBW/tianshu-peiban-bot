"""A provider queue keeps its original permit without spending engine execution time."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from aiohttp import web
import pytest

import tianshu.models.ledger as ledger_module
from tianshu.models.broker import ModelBroker
from tianshu.models.client import ModelClient
from tianshu.models.http import create_broker_app
from tianshu.models.types import ExternalRequest
from tianshu.runtime.config import Secrets
from tianshu.runtime.errors import AppError
from tianshu.runtime.files import FileStore
from tianshu.types import OriginKind, RequestBudget, ServiceClass, SourceRef, TrustedContext


def request(identity="provider-clock", **changes):
    value = ExternalRequest(identity, identity,
        TrustedContext("fixture", None, None, None, (), (), 1, OriginKind.HUMAN),
        RequestBudget(datetime.now(UTC) - timedelta(seconds=1)), "fixture-account", "image",
        ServiceClass.ELASTIC, "comfy-fixture", execution_timeout_seconds=1,
        execution_start="provider_running")
    return replace(value, **changes)


async def seed(database):
    async with database.transaction() as conn:
        await conn.execute("INSERT INTO models.quota_groups(quota_group,concurrency,interactive_reserve,"
            "background_max,unknown_hold_seconds) VALUES('fixture-account',2,1,1,30)")


async def serve(app):
    runner = web.AppRunner(app, handler_cancellation=True)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    return runner, "http://127.0.0.1:" + str(site._server.sockets[0].getsockname()[1])


@asynccontextmanager
async def running_broker(database, tmp_path, **options):
    broker = ModelBroker(database, FileStore(tmp_path), Secrets({}), **options)
    await broker.open()
    runner, origin = await serve(create_broker_app(broker, "fixture-token"))
    client = ModelClient(origin, "fixture-token")
    await client.open()
    try:
        yield broker, client
    finally:
        await client.close()
        await runner.cleanup()
        await broker.close()


async def test_local_provider_queue_keeps_reservation_and_freezes_one_server_execution_clock(database, tmp_path):
    await seed(database)
    state, submissions = {"value": "queued"}, []

    async def submit(_incoming):
        submissions.append("prompt-original")
        return web.json_response({"prompt_id": "prompt-original"})

    async def poll(_incoming):
        return web.json_response({"state": state["value"]})

    provider = web.Application()
    provider.router.add_post("/prompt", submit)
    provider.router.add_get("/queue", poll)
    provider_runner, origin = await serve(provider)
    original = request()
    try:
        async with running_broker(database, tmp_path) as (broker, client):
            async with client.resource(original) as permit:
                assert (await client.status(original.attempt_id)).deadline_at is None
                await permit.submitted("submission-original")
                async with broker.session.post(origin + "/prompt", json={"synthetic": True}) as response:
                    handle = (await response.json())["prompt_id"]
                await permit.submitted(handle)

                async def observe():
                    async with broker.session.get(origin + "/queue") as response:
                        observed = (await response.json())["state"]
                    return await permit.observe_provider(observed, provider_handle=handle)

                first = await observe()
                assert first.status == "running" and first.dispatch == "submitted"
                assert first.deadline_at is first.execution_started_at is None
                await asyncio.sleep(1.05)  # Longer than the complete execution allowance.
                queued = await observe()
                assert queued.deadline_at is queued.execution_started_at is None
                assert queued.started_at == first.started_at
                assert broker.scheduler.snapshot()["fixture-account"]["active"] == 1
                assert broker.scheduler.snapshot()["fixture-account"]["unknown_holds"] == 0

                state["value"] = "running"
                before_running = datetime.now(UTC)
                running = await observe()
                assert before_running <= running.execution_started_at <= datetime.now(UTC)
                assert running.deadline_at == running.execution_started_at + timedelta(seconds=1)
                assert permit.request.budget.deadline_at == running.deadline_at
                assert permit.request.budget.cancellation_id == original.budget.cancellation_id
                state["value"] = "completed"
                completed = await observe()
                assert (completed.execution_started_at, completed.deadline_at) == (
                    running.execution_started_at, running.deadline_at)
                settled = await permit.finish(result={"synthetic": True})
                assert settled.status == "succeeded" and settled.deadline_at == running.deadline_at
            assert broker.scheduler.snapshot()["fixture-account"]["active"] == 0
            assert submissions == ["prompt-original"]
            persisted = await broker.ledger.get(original.attempt_id)
            assert persisted["provider_state"] == "completed"
            assert persisted["provider_handle"] == "prompt-original"
            assert original.budget.deadline_at < persisted["execution_started_at"]
    finally:
        await provider_runner.cleanup()


@pytest.mark.parametrize("boundary", ["execution", "business", "cancel"])
async def test_deferred_external_work_still_obeys_execution_expiry_business_expiry_and_cancel(
    database, tmp_path, boundary
):
    await seed(database)
    entered = asyncio.Event()
    async with running_broker(database, tmp_path) as (broker, client):
        original = request(execution_timeout_seconds=0.3 if boundary == "execution" else 1,
            business_deadline_at=datetime.now(UTC) + timedelta(seconds=0.5) if boundary == "business" else None)

        async def execute():
            async with client.resource(original) as permit:
                await permit.submitted("prompt-original")
                await permit.observe_provider("running" if boundary == "execution" else "queued",
                    provider_handle="prompt-original")
                entered.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(execute())
        try:
            await asyncio.wait_for(entered.wait(), 5)
            if boundary == "cancel":
                await client.cancel(original.attempt_id)
            done, _pending = await asyncio.wait({task}, timeout=2)
            assert task in done  # The request's own clock/cancellation must finish it.
            with pytest.raises(asyncio.CancelledError if boundary == "cancel" else TimeoutError):
                await task
            persisted = await broker.ledger.get(original.attempt_id)
            assert persisted["status"] == persisted["dispatch"] == "unknown"
            assert (persisted["deadline_at"] is not None) == (boundary == "execution")
            assert (persisted["execution_started_at"] is not None) == (boundary == "execution")
            assert persisted["provider_handle"] == "prompt-original"
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("phase", ["queued", "running", "expired"])
async def test_restart_observes_original_handle_without_readmission_terminal_change_or_clock_refresh(
    database, tmp_path, monkeypatch, phase
):
    await seed(database)
    original = request(execution_timeout_seconds=0.05 if phase == "expired" else 2)
    async with running_broker(database, tmp_path) as (broker, _client):
        permit = await broker.begin_external(original)
        await permit.submitted("prompt-original")
        before = await permit.observe_provider("queued" if phase == "queued" else "running",
            provider_handle="prompt-original")
        if phase == "expired":
            await asyncio.sleep(0.06)
    async with running_broker(database, tmp_path) as (broker, client):
        unknown = await broker.ledger.get(original.attempt_id)
        assert unknown["status"] == unknown["dispatch"] == "unknown"
        active = dict(broker.scheduler.groups["fixture-account"].active)

        async def must_not_admit(*_args, **_kwargs):
            pytest.fail("observation cannot acquire a new external permit")

        monkeypatch.setattr(broker, "begin_external", must_not_admit)
        monkeypatch.setattr(broker.scheduler, "acquire", must_not_admit)
        observed = await client.observe_external(original.attempt_id, context=original.context,
            provider_handle="prompt-original", state="completed" if phase == "queued" else "running")
        assert observed.status == observed.dispatch == "unknown"
        assert observed.started_at == before.started_at
        if phase != "queued":
            assert observed.deadline_at == before.deadline_at
            assert observed.execution_started_at == before.execution_started_at
        else:
            assert before.deadline_at is None and observed.deadline_at is not None
        repeated = await client.observe_external(original.attempt_id, context=original.context,
            provider_handle="prompt-original", state="completed")
        assert (repeated.execution_started_at, repeated.deadline_at) == (
            observed.execution_started_at, observed.deadline_at)
        persisted = await broker.ledger.get(original.attempt_id)
        assert persisted["hold_until"] == unknown["hold_until"]
        assert persisted["finished_at"] == unknown["finished_at"]
        assert persisted["error_code"] == unknown["error_code"]
        assert broker.scheduler.groups["fixture-account"].active == active
        assert not broker.external
        if phase == "expired":
            assert repeated.deadline_at < datetime.now(UTC)


@pytest.mark.parametrize("execution", [None, 2])
async def test_legacy_and_admitted_observations_preserve_the_original_deadline(database, tmp_path, execution):
    await seed(database)
    original = request(execution_start="admitted", execution_timeout_seconds=execution,
        budget=RequestBudget(datetime.now(UTC) + timedelta(seconds=5)))
    async with running_broker(database, tmp_path) as (broker, client):
        permit = await broker.begin_external(original)
        await permit.submitted("prompt-original")
        before = await broker.ledger.get(original.attempt_id)
        # Pre-0005 historical admitted rows have no execution_started_at value.
        async with database.transaction() as connection:
            await connection.execute("UPDATE models.attempts SET execution_started_at=NULL WHERE attempt_id=%s",
                (original.attempt_id,))
        observed = await client.observe_external(original.attempt_id, context=original.context,
            provider_handle="prompt-original", state="completed")
        assert observed.deadline_at == before["deadline_at"]
        assert observed.execution_started_at is None
        assert (await broker.ledger.get(original.attempt_id))["provider_state"] is None
        await permit.rejected("synthetic_cleanup")


async def test_provider_clock_observation_rechecks_identity_exact_handle_and_sources_transactionally(database, tmp_path):
    await seed(database)
    available = {"submission": True, "persistence": True}

    async def validate_submission(_context, refs):
        if refs and not available["submission"]:
            raise AppError("source_unavailable", status=403)

    async def validate_persistence(_connection, _context, _refs):
        return available["persistence"]

    original = request(source_refs=(SourceRef("synthetic-source", 1),))
    async with running_broker(database, tmp_path, validate_submission=validate_submission,
            validate_persistence=validate_persistence) as (broker, client):
        permit = await broker.begin_external(original)
        await permit.submitted("prompt-original")
        async def observe(**changes):
            values = {"context": original.context, "provider_handle": "prompt-original", "state": "running"}
            return await client.observe_external(original.attempt_id, **(values | changes))

        with pytest.raises(AppError, match="model_result_context_forbidden"):
            await observe(context=replace(original.context, principal_id="other"))
        with pytest.raises(AppError, match="external_provider_handle_changed"):
            await observe(provider_handle="different-prompt")
        with pytest.raises(AppError, match="invalid_external_provider_observation"):
            await observe(state="unknown")
        available["submission"] = False
        with pytest.raises(AppError, match="source_unavailable"):
            await observe()
        available["submission"], available["persistence"] = True, False
        with pytest.raises(AppError, match="source_unavailable"):
            await observe()
        persisted = await broker.ledger.get(original.attempt_id)
        assert persisted["deadline_at"] is persisted["execution_started_at"] is None
        assert persisted["provider_state"] is None
        assert persisted["status"] == "running" and persisted["dispatch"] == "submitted"
        assert broker.scheduler.snapshot()["fixture-account"]["active"] == 1


async def test_provider_clock_uses_one_broker_utc_only_after_the_original_row_lock(database, tmp_path, monkeypatch):
    await seed(database)
    validating = asyncio.Event()
    calls = []
    expected = datetime(2037, 1, 2, 3, 4, 5, tzinfo=UTC)
    clock = {"value": expected - timedelta(minutes=1)}

    class ObservedClock:
        @staticmethod
        def now(zone):
            assert zone is UTC
            calls.append(clock["value"])
            return clock["value"]

    async def validate_persistence(_connection, _context, _refs):
        validating.set()
        return True

    original = request(execution_timeout_seconds=600)
    async with running_broker(database, tmp_path, validate_persistence=validate_persistence) as (broker, client):
        permit = await broker.begin_external(original)
        await permit.submitted("prompt-original")
        monkeypatch.setattr(ledger_module, "datetime", ObservedClock)
        observation = None
        try:
            async with database.transaction() as connection:
                await connection.execute("SELECT attempt_id FROM models.attempts WHERE attempt_id=%s FOR UPDATE",
                    (original.attempt_id,))
                observation = asyncio.create_task(client.observe_external(original.attempt_id,
                    context=original.context, provider_handle="prompt-original", state="running"))
                await asyncio.wait_for(validating.wait(), 3)
                assert not observation.done() and not calls
                clock["value"] = expected
            observed = await asyncio.wait_for(observation, 3)
            assert calls == [expected]
            assert observed.execution_started_at == expected
            assert observed.deadline_at == expected + timedelta(seconds=600)
            persisted = await broker.ledger.get(original.attempt_id)
            assert persisted["provider_observed_at"] == expected
            clock["value"] = expected + timedelta(seconds=60)
            repeated = await client.observe_external(original.attempt_id, context=original.context,
                provider_handle="prompt-original", state="completed")
            assert repeated.execution_started_at == expected
            assert repeated.deadline_at == observed.deadline_at
            assert (await broker.ledger.get(original.attempt_id))["provider_observed_at"] == clock["value"]
            await permit.rejected("synthetic_cleanup")
        finally:
            if observation is not None:
                observation.cancel()
                await asyncio.gather(observation, return_exceptions=True)
