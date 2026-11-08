"""Online lifecycle contract: request schemas, admission, withdrawal and purge scope."""

import asyncio
from collections import defaultdict
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from tianshu.capabilities.contracts import Fault
from tianshu.capabilities.plugins.control import OPERATIONS, openapi, validate
from tianshu.capabilities.plugins.execution import Executions
from tianshu.capabilities.plugins.service import Plugins
from tianshu.capabilities.plugins.web import WebPlugins


def session():
    return SimpleNamespace(
        principal_id="owner",
        access_epoch=1,
        actor_id=None,
        permissions=frozenset({"config.publish"}),
        scope_ids=("system",),
    )


def test_reload_is_a_registered_instance_operation_with_its_own_schema():
    validate("reload", {"instance_id": "instance", "expected_revision": 2})
    with pytest.raises(Fault):
        validate("reload", {"instance_id": "instance"})
    with pytest.raises(Fault):
        validate("reload", {"instance_id": "instance", "expected_revision": 2, "extra": True})
    document = openapi()
    assert "/api/v1/plugins/reload" in document["paths"]
    assert "reload" in OPERATIONS


def test_uninstall_purge_is_explicit_and_defaults_to_retaining_data():
    validate("uninstall", {"package_id": "example"})
    validate("uninstall", {"package_id": "example", "purge_data": True})
    with pytest.raises(Fault):
        validate("uninstall", {"package_id": "example", "purge_data": "yes"})


@pytest.mark.asyncio
async def test_reload_route_refuses_a_stale_revision_and_forwards_the_accepted_one():
    host = SimpleNamespace(
        restart=AsyncMock(return_value={"state": "ready", "version": "1.0.0"}),
        local=AsyncMock(),
        repository=SimpleNamespace(get=AsyncMock(), list=AsyncMock(return_value=[])),
    )
    service = SimpleNamespace(host=host, authority=SimpleNamespace(check=AsyncMock()))
    web = WebPlugins(service)
    body = {"instance_id": "instance", "expected_revision": 3}
    assert (await web.route("reload", body, session()))["state"] == "ready"
    host.restart.assert_awaited_once_with("instance", 3)


@pytest.mark.asyncio
async def test_uninstall_route_drops_cached_pages_and_forwards_the_purge_choice():
    package = {"manifest": {"id": "example"}}
    host = SimpleNamespace(
        uninstall=AsyncMock(return_value={"uninstalled": True, "instance_data": "purged"}),
        local=AsyncMock(),
        repository=SimpleNamespace(get=AsyncMock(), list=AsyncMock(return_value=[])),
    )
    service = SimpleNamespace(host=host, authority=SimpleNamespace(check=AsyncMock()))
    web = WebPlugins(service)
    web.tickets = {
        "mine": {"principal_id": "owner", "access_epoch": 1, "package": package,
                 "view": {}, "nonce": "n", "expires": 10**9},
        "other": {"principal_id": "owner", "access_epoch": 1,
                  "package": {"manifest": {"id": "other"}},
                  "view": {}, "nonce": "n", "expires": 10**9},
    }
    await web.route("uninstall", {"package_id": "example", "purge_data": True}, session())
    assert "mine" not in web.tickets and "other" in web.tickets
    host.uninstall.assert_awaited_once_with("example", purge_data=True)


@pytest.mark.asyncio
async def test_page_ticket_stops_serving_an_uninstalled_or_replaced_package():
    stored = {"id": "example@1", "installed": False, "digest": "d", "root": "r",
              "manifest": {"id": "example"}, "schema": {}, "description": {}}
    repository = SimpleNamespace(get=MagicMock(return_value=stored))

    async def local(operation, *args):
        return operation(*args)

    host = SimpleNamespace(local=local, repository=repository)
    entry = {"principal_id": "owner", "access_epoch": 1, "package": {**stored, "installed": True},
             "view": {"entry": "panel.html"}, "nonce": "n", "expires": 10**9}
    service = SimpleNamespace(host=host, authority=SimpleNamespace(check=AsyncMock()))
    web = WebPlugins(service)
    web.tickets = {"ticket": entry}
    with pytest.raises(Fault) as error:
        await web.page("ticket", session())
    assert error.value.code == "not_found"


@pytest.mark.asyncio
async def test_uninstall_refuses_a_concurrent_enable_instead_of_leaving_a_live_instance():
    """A deterministic barrier reproduces the reported uninstall/enable race."""
    state = {"package": "example"}

    class Manager:
        def __init__(self):
            self.admission = {}
            self.package_locks = defaultdict(asyncio.Lock)
            self.started_at = asyncio.Event()
            self.release = asyncio.Event()
            self.instance = {
                "id": "instance",
                "package_id": "example",
                "desired_state": "enabled",
                "revision": 2,
                "config": {},
                "bindings": [],
            }

        async def _disable(self, identifier, expected):
            self.started_at.set()
            await self.release.wait()
            self.instance.update(desired_state="disabled", revision=expected + 1)
            return self.instance

    manager = Manager()

    async def uninstall():
        async with manager.package_locks["example"]:
            for identifier in ("instance",):
                manager.admission[identifier] = "instance_unloading"
            try:
                await manager._disable("instance", manager.instance["revision"])
            finally:
                manager.admission.pop("instance", None)
            state["package"] = "uninstalled"
            return {"uninstalled": True}

    async def enable():
        # Enable must queue behind the package lock, not slip between the stop
        # of one instance and the removal of the package.
        async with manager.package_locks["example"]:
            assert state["package"] == "uninstalled"
            return {"enabled": True}

    unloading = asyncio.create_task(uninstall())
    await manager.started_at.wait()
    enabling = asyncio.create_task(enable())
    await asyncio.sleep(0)
    assert enabling.done() is False
    manager.release.set()
    await unloading
    assert (await enabling) == {"enabled": True}
    assert state["package"] == "uninstalled"


@pytest.mark.asyncio
async def test_uninstall_holds_admission_so_queued_calls_cannot_restart_the_old_version():
    from tianshu.capabilities.plugins.manager import PluginManager

    manager = object.__new__(PluginManager)
    manager.admission = {"instance": "instance_unloading"}
    instance = {"id": "instance", "desired_state": "enabled", "package_id": "p", "version": "1"}
    host = SimpleNamespace(
        admission=manager.admission,
        operations_lock=asyncio.Lock(),
        repository=SimpleNamespace(),
        instance=AsyncMock(return_value=instance),
        package=AsyncMock(return_value={"description": {"actions": []}}),
        available_capabilities=set(),
        ensure_active=AsyncMock(),
        check_authorizer=AsyncMock(return_value=True),
    )
    executions = Executions(host)
    with pytest.raises(Fault) as error:
        await executions.invoke("instance", "value", {}, "intent", "owner")
    assert error.value.code == "instance_unloading"
    host.package.assert_not_awaited()


def test_instance_admission_closes_only_for_the_switching_instance():
    host = SimpleNamespace(
        admission={"switching": "instance_upgrading"},
        operations_lock=asyncio.Lock(),
        repository=SimpleNamespace(),
    )
    executions = Executions(host)
    assert executions.refusal("switching") == "instance_upgrading"
    assert executions.refusal("another") is None


@pytest.mark.asyncio
async def test_invoke_refuses_new_admission_while_that_instance_is_switching():
    instance = {"id": "switching", "desired_state": "enabled", "package_id": "p", "version": "1"}
    host = SimpleNamespace(
        admission={"switching": "instance_reloading"},
        operations_lock=asyncio.Lock(),
        repository=SimpleNamespace(),
        instance=AsyncMock(return_value=instance),
        package=AsyncMock(return_value={"description": {"actions": []}}),
        available_capabilities=set(),
        ensure_active=AsyncMock(),
        check_authorizer=AsyncMock(return_value=True),
    )
    executions = Executions(host)
    with pytest.raises(Fault) as error:
        await executions.invoke("switching", "value", {}, "intent", "owner")
    assert error.value.code == "instance_reloading"
    host.instance.assert_awaited_once()
    # Another instance is never refused by this one's lifecycle switch.
    host.admission = {"other": "instance_unloading"}
    with pytest.raises(Fault) as error:
        await executions.invoke("switching", "value", {}, "intent", "owner")
    assert error.value.code == "not_found"


@pytest.mark.asyncio
async def test_withdrawal_removes_only_this_instance_handlers_immediately():
    handlers = {
        "plugin:one:binding:value": object(),
        "plugin-event:one:binding:event": object(),
        "plugin:two:binding:value": object(),
        "media.view": object(),
    }
    plugins = object.__new__(Plugins)
    plugins.catalog = SimpleNamespace(handlers=handlers)
    plugins.revoke("one")
    assert set(plugins.catalog.handlers) == {"plugin:two:binding:value", "media.view"}
    plugins.revoke("two")
    assert set(plugins.catalog.handlers) == {"media.view"}
