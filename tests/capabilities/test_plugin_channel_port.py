"""Plugin channel calls retain the parent authority, sources and behavior facts."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tianshu.capabilities.plugins.manager import AVAILABLE
from tianshu.capabilities.plugins.port_schemas import OPERATIONS
from tianshu.capabilities.plugins.ports import capability
from tianshu.capabilities.plugins.service import Plugins
from tianshu.types import Action, OriginKind, Receipt, SourceRef, TrustedContext


@pytest.mark.asyncio
async def test_registered_plugin_channel_port_reaches_shared_facade():
    assert "channels.native" in AVAILABLE
    assert all(("channels.native", name) in OPERATIONS for name in ("describe", "invoke", "get"))
    value = {
        "connection_id": "snow",
        "api_name": "get_group_msg_history",
        "params": {"group_id": 123},
        "request_id": "history",
    }
    ports = SimpleNamespace(host_port=AsyncMock(return_value={"execution": "succeeded"}))
    executions = SimpleNamespace(host=SimpleNamespace(domain_ports=ports), repo=object())
    assert await capability(
        executions, {"instance": "i", "call": "parent"}, "channels.native", "invoke", value
    ) == {"execution": "succeeded"}
    ports.host_port.assert_awaited_once()


@pytest.mark.asyncio
async def test_plugin_native_call_preserves_parent_origin_and_does_not_duplicate_expression():
    context = TrustedContext(
        "owner",
        "actor",
        "conversation",
        None,
        ("scope",),
        ("scope",),
        1,
        OriginKind.HUMAN,
        frozenset({"external.manage"}),
    )
    action = Action(
        "parent",
        "request",
        "plugin:i:b:a",
        "actor",
        context,
        {},
        OriginKind.HUMAN,
        source_refs=(SourceRef("original", 3),),
    )
    receipt = Receipt(
        "receipt",
        "child",
        "attempt",
        "succeeded",
        "confirmed",
        "delivered",
        result={"expression_id": "expression"},
    )
    actions = SimpleNamespace(
        load_for_execution=AsyncMock(return_value=(action, "attempt", None)),
        saved_generation=AsyncMock(return_value="generation"),
        saved_behaviors=AsyncMock(return_value=(("behavior",), "conversation")),
        check_behaviors=AsyncMock(),
        execution_class=AsyncMock(return_value="interactive"),
    )

    async def local(function, *args, **kwargs):
        return await function(*args, **kwargs)

    host = SimpleNamespace(
        local=local,
        repository=SimpleNamespace(
            call=AsyncMock(return_value={"explicit_expression_ids": []}), update_call=AsyncMock()
        ),
    )
    plugins = object.__new__(Plugins)
    plugins.actions, plugins.authority, plugins.host = (
        actions,
        SimpleNamespace(check=AsyncMock()),
        host,
    )
    plugins.channels = SimpleNamespace(invoke=AsyncMock(return_value=receipt))
    value = {
        "connection_id": "snow",
        "api_name": "send_custom_face",
        "params": {"group_id": 123, "emoji_id": "e"},
        "request_id": "publish",
    }
    result = await plugins.host_port(
        {"call": "parent", "instance": "i"}, "channels.native", "invoke", value
    )
    assert result["delivery"] == "delivered"
    kwargs = plugins.channels.invoke.await_args.kwargs
    assert kwargs["origin_kind"] == OriginKind.HUMAN
    assert kwargs["source_refs"] == action.source_refs
    assert kwargs["behavior_refs"] == ("behavior",)
    assert host.repository.update_call.await_args.kwargs["explicit_expression_ids"] == [
        "expression"
    ]
