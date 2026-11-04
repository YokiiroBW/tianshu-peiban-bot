"""Independent plugin bindings keep their original timer and granted scope."""

import time
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from tianshu.capabilities.contracts import Fault
from tianshu.capabilities.plugins.actor_bridge import PluginActorBridge, binding_visible
from tianshu.capabilities.plugins.scheduler import Scheduler
from tianshu.capabilities.plugins.service import PluginEventHandler
from tianshu.capabilities.plugins.web import WebPlugins
from tianshu.runtime.errors import AppError
from tianshu.runtime.serialization import wire
from tianshu.types import OriginKind, TrustedContext


def context():
    return TrustedContext("owner", None, None, None, ("system-owner",), ("system-owner",), 1,
                          OriginKind.HUMAN, frozenset({"config.publish"}))


def binding():
    return {"enabled": True, "binding_id": "binding", "scope": {
        "kind": "workspace", "workspace_id": "system-owner"},
        "entries": [{"kind": "action", "id": "refresh"}]}


@pytest.mark.asyncio
async def test_workspace_binding_uses_existing_authorized_scope_and_local_declarations_only():
    scoped = binding()
    assert binding_visible(context(), scoped)
    assert not binding_visible(replace(context(), scope_ids=("other",)), scoped)
    assert not binding_visible(replace(context(), actor_id="some-bot"), scoped)

    instance = {"id": "i", "bindings": [scoped], "package_id": "pkg", "version": "1", "revision": 3}
    description = {"actions": [{"id": "refresh", "title": "Refresh", "effect": "read",
        "input_schema": {"type": "object"}, "output_schema": {"type": "object"},
        "timeout_seconds": 1}], "tools": [], "commands": []}
    repository = SimpleNamespace(list=MagicMock(return_value=[instance]))

    async def local(operation, *args):
        return operation(*args)

    host = SimpleNamespace(repository=repository, local=local,
        package=AsyncMock(return_value={"description": description}),
        projection=AsyncMock(side_effect=AssertionError("external probe must not run")))
    rows = await PluginActorBridge(host).discover(context())
    assert len(rows) == 1 and rows[0]["scope"] == scoped["scope"]
    assert rows[0]["availability"] == "not_verified"
    host.projection.assert_not_awaited()


@pytest.mark.asyncio
async def test_workspace_binding_cannot_be_created_with_an_ungranted_scope():
    host = SimpleNamespace(repository=SimpleNamespace(), bindings=AsyncMock(return_value={"ok": True}))
    service = SimpleNamespace(host=host, authority=SimpleNamespace(check=AsyncMock()))
    web = WebPlugins(service)
    body = {"instance_id": "instance", "expected_revision": 1, "bindings": [binding()]}
    assert await web.route("bindings", body, context()) == {"ok": True}
    bad = {**body, "bindings": [{**binding(), "scope": {"kind": "workspace", "workspace_id": "other"}}]}
    with pytest.raises(Fault):
        await web.route("bindings", bad, context())
    host.bindings.assert_awaited_once()


@pytest.mark.asyncio
async def test_workspace_event_rechecks_the_binding_before_delivery():
    event_binding = {**binding(), "entries": [{"kind": "event", "id": "updated"}]}
    instance = {"id": "instance", "revision": 3, "desired_state": "enabled", "bindings": [event_binding]}
    host = SimpleNamespace(instance=AsyncMock(return_value=instance))
    handler = PluginEventHandler(SimpleNamespace(host=host), instance, event_binding,
                                 {"id": "updated", "event_type": "source.updated"})
    assert (await handler.availability(context())).can_execute
    assert not (await handler.availability(replace(context(), scope_ids=("other",)))).can_execute
    host.instance.return_value = {**instance, "bindings": [{**event_binding, "enabled": False}]}
    assert not (await handler.availability(context())).can_execute


def scheduled_host(*, existing=None, previous=None):
    due = time.time()
    saved = {"id": "schedule", "instance_id": "instance", "enabled": True, "revision": 1,
             "action": "refresh", "input": {}, "context": wire(context()), "next_run": due,
             "last_invocation": previous, "data_generation": "generation",
             "trigger": {"kind": "interval", "seconds": 300, "misfire": "run_once", "overlap": "queue_one"}}
    repo = SimpleNamespace(get=MagicMock(return_value=saved), save_schedule=MagicMock(),
                           wait_schedule=MagicMock(), list=MagicMock(return_value=[saved]))

    async def local(operation, *args):
        return operation(*args)

    refreshed = replace(context(), access_epoch=2, origin_kind=OriginKind.TIMER)
    authority = SimpleNamespace(refresh_scheduled_context=AsyncMock(return_value=refreshed), check=AsyncMock())
    actions = SimpleNamespace(find=AsyncMock(return_value=existing), invoke=AsyncMock(),
        get=AsyncMock(return_value={"receipt": {"execution": "unknown"}}))
    ports = SimpleNamespace(authority=authority, actions=actions, discover=AsyncMock())
    host = SimpleNamespace(repository=repo, local=local, domain_ports=ports,
        instance=AsyncMock(return_value={"id": "instance", "desired_state": "enabled", "bindings": [binding()]}))
    return host, saved, due, refreshed


@pytest.mark.asyncio
async def test_timer_renews_grant_and_observes_original_admission_after_crash():
    existing = {"receipt": {"action_id": "original-action", "execution": "pending"}}
    host, schedule, due, refreshed = scheduled_host(existing=existing)
    await Scheduler(host).execute("schedule", 1, due)
    host.domain_ports.authority.refresh_scheduled_context.assert_awaited_once_with(
        replace(context(), origin_kind=OriginKind.TIMER), permission=None, require_actor=False)
    host.domain_ports.actions.invoke.assert_not_awaited()
    assert schedule["last_invocation"] == "original-action"
    assert schedule["context"] == wire(refreshed)
    host.repository.save_schedule.assert_called_once()


@pytest.mark.asyncio
async def test_unknown_previous_invocation_and_lost_scope_never_start_a_new_action():
    host, schedule, due, _ = scheduled_host(previous="unresolved")
    await Scheduler(host).execute("schedule", 1, due)
    assert schedule["pending"] is True
    host.domain_ports.actions.invoke.assert_not_awaited()

    host, schedule, due, _ = scheduled_host(previous="unresolved")
    host.domain_ports.actions.get.return_value = {"receipt": {"execution": "failed", "effect": "unknown"}}
    await Scheduler(host).execute("schedule", 1, due)
    assert schedule["pending"] is True
    host.domain_ports.actions.invoke.assert_not_awaited()

    host, schedule, due, _ = scheduled_host()
    host.domain_ports.authority.refresh_scheduled_context.side_effect = AppError(
        "scheduled_scope_unavailable", status=403)
    await Scheduler(host).execute("schedule", 1, due)
    assert schedule["state"] == "blocked" and schedule["code"] == "scheduled_scope_unavailable"
    host.repository.wait_schedule.assert_called_once()
    host.domain_ports.actions.invoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_unbound_timer_is_preserved_and_only_its_instance_update_wakes_it():
    host, schedule, due, _ = scheduled_host()
    host.instance.return_value["bindings"] = []
    scheduler = Scheduler(host)
    await scheduler.execute("schedule", 1, due)
    assert schedule["state"] == "waiting_binding"
    assert schedule["next_run"] == due
    host.domain_ports.actions.invoke.assert_not_awaited()
    await scheduler.instance_changed("another-instance")
    host.repository.save_schedule.assert_not_called()
    await scheduler.instance_changed("instance")
    assert schedule["state"] == "scheduled" and schedule["next_run"] == due
    assert schedule["wake_generation"] == 1
    host.repository.save_schedule.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("admitted", [False, True])
async def test_binding_edit_after_crash_keeps_the_reserved_action_identity(admitted):
    existing = {"receipt": {"action_id": "original-action", "execution": "unknown"}} if admitted else None
    host, schedule, due, _ = scheduled_host(existing=existing)
    schedule["dispatch"] = {"due": due, "capability_id": "plugin:instance:binding:refresh",
                            "action_key": "original-action", "admitted": admitted,
                            "context": wire(context()), "required_permission": "config.publish"}
    schedule["last_invocation"] = "original-action"
    host.instance.return_value["bindings"] = [{**binding(), "binding_id": "replacement"}]
    await Scheduler(host).execute("schedule", 1, due)
    host.domain_ports.actions.find.assert_awaited_once_with(
        "plugin:instance:binding:refresh", "original-action", replace(context(), access_epoch=2, origin_kind=OriginKind.TIMER))
    host.domain_ports.actions.invoke.assert_not_awaited()
    if admitted:
        assert schedule["last_invocation"] == "original-action"
    else:
        assert schedule["state"] == "waiting_binding" and schedule["dispatch"]["action_key"] == "original-action"
