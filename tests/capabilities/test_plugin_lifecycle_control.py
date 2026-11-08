"""Online lifecycle contract: request schemas, admission, withdrawal and purge scope."""

import asyncio
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


def test_instance_admission_closes_only_for_the_switching_instance():
    host = SimpleNamespace(
        admission={"switching"}, operations_lock=asyncio.Lock(), repository=SimpleNamespace()
    )
    executions = Executions(host)
    assert executions.blocked("switching")
    assert not executions.blocked("another")


@pytest.mark.asyncio
async def test_invoke_refuses_new_admission_while_that_instance_is_switching():
    instance = {"id": "switching", "desired_state": "enabled", "package_id": "p", "version": "1"}
    host = SimpleNamespace(
        admission={"switching"},
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
