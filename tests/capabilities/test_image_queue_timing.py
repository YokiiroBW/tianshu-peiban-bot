"""ComfyUI consumes the original Models clock and never resubmits on observation.

The port double implements the agreed clock contract; Models' own tests cover
its persisted timer and quota implementation. These cases exercise real image
work/Action persistence with a synthetic provider, without network or generation.
"""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio

from tianshu.capabilities.images import service as image_module
from tianshu.capabilities.images.provider import ImageResult
from tianshu.capabilities.images.service import Images
from tianshu.models.types import ModelReceipt
from tianshu.types import RequestBudget
from test_image_protocol import image_recovery


HANDLE = "synthetic-provider-handle"
COMPLETED = ImageResult("completed", [{"filename": "synthetic.png"}])


@pytest_asyncio.fixture
async def remote_image(image_recovery, monkeypatch):
    value = image_recovery
    port = SimpleNamespace(seconds=.05, records={}, waiting=asyncio.Event(), release=asyncio.Event())
    physical_request = image_module.request

    def request(*args, **kwargs):
        original = physical_request(*args, **kwargs)
        return replace(original, execution_timeout_seconds=port.seconds,
            budget=RequestBudget(datetime.now(UTC)+timedelta(seconds=port.seconds)))

    async def pause(_seconds):
        port.waiting.set()
        await port.release.wait()
        await asyncio.sleep(0)

    # Replace only this module's polling dependency, not asyncio globally.
    monkeypatch.setattr(image_module, "request", request)
    monkeypatch.setattr(image_module, "asyncio", SimpleNamespace(sleep=pause, timeout=asyncio.timeout))

    async def observe(record, state, *, provider_handle):
        assert provider_handle == record.receipt.provider_handle == HANDLE
        if state in {"running", "completed"} and record.receipt.execution_started_at is None:
            now = datetime.now(UTC)
            deadline = now+timedelta(seconds=record.request.execution_timeout_seconds)
            if record.request.business_deadline_at is not None:
                deadline = min(deadline, record.request.business_deadline_at)
            record.receipt = replace(record.receipt, execution_started_at=now, deadline_at=deadline)
        record.observations.append((state, record.receipt.deadline_at))
        if record.active and record.receipt.deadline_at is not None:
            remaining = max(0, (record.receipt.deadline_at-datetime.now(UTC)).total_seconds())
            record.timer.reschedule(asyncio.get_running_loop().time()+remaining)
        return record.receipt

    @asynccontextmanager
    async def resource(request):
        assert request.execution_start == "provider_running"
        value.resources.append(request)
        expiry = request.business_deadline_at
        timeout = max(0, (expiry-datetime.now(UTC)).total_seconds()) if expiry else None
        record = SimpleNamespace(request=request, observations=[], active=True, done=False,
            timer=asyncio.timeout(timeout), receipt=ModelReceipt(request.attempt_id, request.intent_id,
                "running", "submitted", "synthetic-resource", 1, request.provider_id,
                request.function, "external", request.quota_group, deadline_at=expiry))
        port.records[request.attempt_id] = record

        async def submitted(provider_handle=None):
            record.receipt = replace(record.receipt, provider_handle=provider_handle)

        async def settle(status, *, error_code=None):
            record.done = True
            record.receipt = replace(record.receipt, status=status, error_code=error_code)
            return record.receipt

        async def observe_provider(state, *, provider_handle):
            return await observe(record, state, provider_handle=provider_handle)

        async def finish(**_kwargs):
            return await settle("succeeded")

        async def rejected(code, **_kwargs):
            return await settle("failed", error_code=code)

        async def unknown(code):
            return await settle("unknown", error_code=code)

        permit = SimpleNamespace(submitted=AsyncMock(side_effect=submitted),
            observe_provider=AsyncMock(side_effect=observe_provider), finish=AsyncMock(side_effect=finish),
            rejected=AsyncMock(side_effect=rejected), unknown=AsyncMock(side_effect=unknown))
        record.permit = permit
        try:
            async with record.timer:
                try:
                    yield permit
                except BaseException:
                    if not record.done:
                        await permit.unknown("external_interrupted")
                    raise
        finally:
            record.active = False

    async def observe_external(attempt_id, *, context, provider_handle, state):
        record = port.records[attempt_id]
        assert context == record.request.context
        return await observe(record, state, provider_handle=provider_handle)

    value.broker.resource = resource
    value.broker.observe_external = AsyncMock(side_effect=observe_external)
    value.adapter.poll = AsyncMock(return_value=ImageResult("queued"))
    return value, port


async def accept_image(value, *, deadline=None):
    action = replace(value.action, action_id=str(uuid4()), intent_id=str(uuid4()), deadline_at=deadline)
    await value.owner.invoke(action, background=True)
    return action


async def interrupt_original(value, port, *, started=False):
    port.seconds = 5
    value.adapter.poll.return_value = ImageResult("running" if started else "queued")
    action = await accept_image(value)
    task = asyncio.create_task(value.owner.run(action.action_id))
    await asyncio.wait_for(port.waiting.wait(), 3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    _, attempt, _ = await value.owner._load(action.action_id)
    record = port.records[attempt.attempt_id]
    assert record.receipt.status == "unknown" and not record.active
    return action, attempt, record


def restart(value):
    previous = value.images
    images = Images(previous.config, previous.broker, previous.artifacts, previous.life, previous.authority)
    images.store_images = previous.store_images
    return images


@pytest.mark.asyncio
async def test_remote_queue_outlasts_execution_allowance_and_repeated_running_keeps_one_clock(remote_image):
    value, port = remote_image
    value.adapter.poll.side_effect = [ImageResult("queued"), ImageResult("running"), ImageResult("running"), COMPLETED]
    action = await accept_image(value)
    task = asyncio.create_task(value.owner.run(action.action_id))
    await asyncio.wait_for(port.waiting.wait(), 3)
    record = next(iter(port.records.values()))
    await asyncio.sleep(port.seconds*2)
    assert not task.done() and record.request.budget.deadline_at < datetime.now(UTC)
    assert record.receipt.execution_started_at is record.receipt.deadline_at is None
    port.release.set()
    done = await asyncio.wait_for(task, 3)
    assert done.execution == "succeeded", done.error_code
    states, deadlines = zip(*record.observations)
    assert states == ("queued", "running", "running", "completed")
    assert deadlines[0] is None and len(set(deadlines[1:])) == 1
    assert len(value.resources) == 1 and record.permit.finish.await_count == 1
    value.adapter.submit.assert_awaited_once()
    assert all(call.args[0] == HANDLE for call in value.adapter.poll.await_args_list)


@pytest.mark.asyncio
async def test_unknown_remote_state_does_not_claim_queue_or_start_execution(remote_image):
    value, port = remote_image
    value.adapter.poll.return_value = ImageResult("unknown")
    action = await accept_image(value)
    done = await value.owner.run(action.action_id)
    record = next(iter(port.records.values()))
    assert (done.execution, done.effect) == ("unknown", "unknown")
    assert not record.observations and record.receipt.execution_started_at is None
    record.permit.unknown.assert_awaited_once_with("image_outcome_unknown")
    value.adapter.image.assert_not_awaited()
    value.adapter.submit.assert_awaited_once()


@pytest.mark.asyncio
async def test_business_expiry_still_cancels_only_the_original_remote_handle(remote_image):
    value, port = remote_image
    action = await accept_image(value, deadline=datetime.now(UTC)+timedelta(seconds=2))
    task = asyncio.create_task(value.owner.run(action.action_id))
    await asyncio.wait_for(port.waiting.wait(), 3)
    done = await asyncio.wait_for(task, 4)
    record = next(iter(port.records.values()))
    assert (done.execution, done.effect) == ("unknown", "unknown")
    assert done.error_code == "deadline_expired"
    assert record.request.business_deadline_at == action.deadline_at
    assert record.receipt.execution_started_at is None and record.receipt.status == "unknown"
    value.adapter.cancel_pending.assert_awaited_once_with(HANDLE, value.adapter.poll.await_args.args[1])
    value.adapter.image.assert_not_awaited()
    value.adapter.submit.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("started", [False, True])
async def test_restart_observes_the_original_attempt_and_prompt_without_new_submission(remote_image, started):
    value, port = remote_image
    action, attempt, record = await interrupt_original(value, port, started=started)
    original_deadline = record.receipt.deadline_at
    images = restart(value)
    value.adapter.poll.side_effect = [ImageResult("running"), ImageResult("running"), COMPLETED]
    assert (await images.reconcile(action, attempt)).execution == "running"
    deadline = record.receipt.deadline_at
    assert deadline is not None and (original_deadline is None or deadline == original_deadline)
    assert (await images.reconcile(action, attempt)).execution == "running"
    done = await images.reconcile(action, attempt)
    assert done.execution == "succeeded" and done.attempt_id == attempt.attempt_id
    assert record.receipt.deadline_at == deadline and record.receipt.status == "unknown"
    assert not record.active and len(value.resources) == 1
    assert all(call.args == (attempt.attempt_id,) and call.kwargs == {
        "context": action.context, "provider_handle": HANDLE, "state": state,
    } for call, state in zip(value.broker.observe_external.await_args_list, ("running", "running", "completed")))
    value.adapter.submit.assert_awaited_once()
    async with value.database.transaction(read_only=True) as connection:
        work = await (await connection.execute("SELECT provider_handle FROM capabilities.image_work WHERE action_id=%s",
            (action.action_id,))).fetchone()
    assert work["provider_handle"] == HANDLE


@pytest.mark.asyncio
@pytest.mark.parametrize("clock_state", ["expired", "legacy_expired", "expires_during_download"])
async def test_recovery_never_reopens_the_original_execution_allowance(remote_image, clock_state):
    value, port = remote_image
    action, attempt, record = await interrupt_original(value, port)
    now = datetime.now(UTC)
    clock = replace(record.receipt, deadline_at=now+timedelta(seconds=.25) if clock_state == "expires_during_download"
        else now-timedelta(seconds=1), execution_started_at=None if clock_state == "legacy_expired" else now-timedelta(seconds=2))
    value.broker.observe_external = AsyncMock(return_value=clock)
    value.adapter.poll.return_value = COMPLETED
    if clock_state == "expires_during_download":
        async def unfinished(*_args):
            await asyncio.Event().wait()
        value.adapter.image.side_effect = unfinished
    images = restart(value)
    generated = len(value.requests)
    done = await asyncio.wait_for(images.reconcile(action, attempt), 2)
    assert (done.execution, done.effect, done.error_code) == ("unknown", "unknown", "capability_timeout")
    assert done.result["resource_receipt"]["deadline_at"] == clock.deadline_at.isoformat()
    assert done.result["provider_state"] == "completed" and done.result["provider_handle"] == HANDLE
    assert value.adapter.image.await_count == int(clock_state == "expires_during_download")
    images.store_images.assert_not_awaited()
    assert len(value.requests) == generated and len(value.resources) == 1
    value.adapter.submit.assert_awaited_once()
