"""The plugin's own clock excludes only the owning model host call."""
import asyncio
from collections import defaultdict
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tianshu.capabilities.contracts import Fault
from tianshu.capabilities.plugins.process import Process
from tianshu.capabilities.plugins.execution import Executions


def process(callback):
    value = Process({}, callback)
    value.process = SimpleNamespace(returncode=None)
    value.reader = asyncio.get_running_loop().create_future()
    value.send = AsyncMock()
    value._write = AsyncMock()
    return value


async def invoke(value, reference, *, timeout=.1):
    task = asyncio.create_task(value.call("plugin.invoke", {"call": {"context_ref": reference}}, timeout=timeout))
    await asyncio.sleep(0)
    assert reference in value.invocation_timers
    return task


def model_call(identifier, reference):
    return {"id": identifier, "method": "host.capability", "params": {"context_ref": reference,
        "capability": "models.generate", "major": 1, "operation": "generate", "input": {}}}


@pytest.mark.asyncio
async def test_write_queue_is_outside_execution_time_and_the_clock_exists_before_dispatch():
    value = process(AsyncMock())
    await value.write_lock.acquire()

    async def write(document):
        assert "owner" in value.invocation_timers
        value.pending[document["id"]].set_result({"finished": True})

    value._write.side_effect = write
    task = asyncio.create_task(value.call("plugin.invoke", {"call": {"context_ref": "owner"}}, timeout=.01))
    await asyncio.sleep(.03)
    assert not task.done() and not value.invocation_timers
    value._write.assert_not_awaited()
    value.write_lock.release()
    assert await task == {"finished": True}
    assert not value.pending and not value.invocation_timers


@pytest.mark.asyncio
async def test_model_wait_outlives_a_plugin_execution_budget_without_suspending_another_context():
    entered, release = asyncio.Event(), asyncio.Event()

    async def callback(_method, _params):
        entered.set()
        await release.wait()
        return {"actual": "model result"}

    value = process(callback)
    owning = await invoke(value, "owner", timeout=.05)
    reply = asyncio.create_task(value.reply(model_call("model", "owner")))
    await asyncio.wait_for(entered.wait(), 1)
    unrelated = await invoke(value, "other", timeout=.1)
    with pytest.raises(TimeoutError):
        await unrelated
    assert not owning.done() and value.invocation_timers["owner"].timeout.when() is None
    assert "other" not in value.invocation_timers
    release.set()
    await reply
    assert value.invocation_timers["owner"].timeout.when() > asyncio.get_running_loop().time()
    value.pending["h1"].set_result({"finished": True})
    assert await owning == {"finished": True}
    assert not value.pending and not value.invocation_timers


@pytest.mark.asyncio
async def test_parallel_model_callbacks_keep_the_clock_paused_until_both_settle_and_errors_restore_it():
    entered = {key: asyncio.Event() for key in ("first", "second")}
    release = {key: asyncio.Event() for key in entered}

    async def callback(_method, params):
        key = params["input"]["key"]
        entered[key].set()
        await release[key].wait()
        if key == "second":
            raise Fault("model_outcome_unknown", 503)
        return {"actual": "first result"}

    value = process(callback)
    owning = await invoke(value, "owner")
    replies = []
    for key in entered:
        document = model_call(key, "owner")
        document["params"]["input"] = {"key": key}
        replies.append(asyncio.create_task(value.reply(document)))
    await asyncio.wait_for(asyncio.gather(*(event.wait() for event in entered.values())), 1)
    clock = value.invocation_timers["owner"]
    assert clock.waits == 2 and clock.timeout.when() is None
    release["first"].set()
    await replies[0]
    assert clock.waits == 1 and clock.timeout.when() is None
    release["second"].set()
    await replies[1]
    assert clock.waits == 0 and clock.timeout.when() > asyncio.get_running_loop().time()
    assert value.send.await_args.args[0]["error"]["data"]["code"] == "model_outcome_unknown"
    value.pending["h1"].set_result({"finished": True})
    await owning
    assert not value.invocation_timers and not value.pending


@pytest.mark.asyncio
async def test_cancelled_invocation_cleans_its_clock_before_a_late_model_callback_finishes():
    entered, release = asyncio.Event(), asyncio.Event()

    async def callback(_method, _params):
        entered.set()
        await release.wait()
        return {"actual": "late result"}

    value = process(callback)
    owning = await invoke(value, "owner")
    reply = asyncio.create_task(value.reply(model_call("model", "owner")))
    await asyncio.wait_for(entered.wait(), 1)
    owning.cancel()
    with pytest.raises(asyncio.CancelledError):
        await owning
    assert not value.invocation_timers and not value.pending
    release.set()
    await reply
    assert not value.invocation_timers


@pytest.mark.asyncio
@pytest.mark.parametrize("cancelled", [False, True])
async def test_serial_wait_precedes_plugin_execution_and_active_handles_are_revoked_on_settlement(cancelled):
    entered, release, references = asyncio.Event(), asyncio.Event(), []
    instance = {"id": "plugin", "package_id": "package", "version": "1", "revision": 1,
        "config": {}, "secret_snapshot": {}, "desired_state": "enabled"}
    document = {"id": "call", "snapshot": instance, "package_version": "1", "action": "read",
        "input": {}, "write": False, "intent_key": "original", "execution": "accepted",
        "effect": "none", "dispatch": "not_sent"}
    package = {"manifest": {"capabilities": {"required": [], "optional": []}},
        "description": {"actions": [{"id": "read", "effect": "read", "timeout_seconds": .01, "output_schema": {}}]}}

    async def local(operation, *args, **kwargs):
        return await operation(*args, **kwargs)

    async def update_call(identifier, **changes):
        assert identifier == document["id"]
        document.update(changes)

    async def call(method, params, *, timeout):
        if method == "plugin.cancel":
            return {}
        assert timeout == .01
        references.append(params["call"]["context_ref"])
        entered.set()
        await release.wait()
        return {"effect": "none", "result": {}}

    child = SimpleNamespace(call=call, runtime_id="call-child")
    host = SimpleNamespace(repository=SimpleNamespace(call=AsyncMock(return_value=document), update_call=AsyncMock(side_effect=update_call)),
        local=local, instance=AsyncMock(return_value=instance), package=AsyncMock(return_value=package),
        worker=AsyncMock(return_value=child), locks=defaultdict(asyncio.Lock), workers={"plugin": (child, 1)},
        available_capabilities=set())
    value = Executions(host)
    await host.locks["plugin"].acquire()
    task = asyncio.create_task(value.execute("call"))
    await asyncio.sleep(.02)
    assert not task.done() and not value.contexts
    host.worker.assert_not_awaited()
    host.locks["plugin"].release()
    await asyncio.wait_for(entered.wait(), 1)
    assert value.contexts[references[0]]["expires"] == float("inf")
    if cancelled:
        task.cancel()
    else:
        release.set()
    await task
    assert document["execution"] == ("cancelled" if cancelled else "succeeded")
    assert document["effect"] == "none"
    assert document["error"] == ({"code": "cancelled", "retriable": False} if cancelled else None)
    assert not value.contexts and not value.authorizers
    with pytest.raises(Fault, match="permission_denied"):
        await value.callback(
            "plugin", "host.log", {"context_ref": references[0]}, generation="call-child"
        )
