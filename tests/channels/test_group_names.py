"""Group names use authorized scene metadata, without delivery or configuration writes."""

from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tianshu.channels.contracts import ChannelError
from tianshu.channels.group_names import check_metadata_target, group_names
from tianshu.channels.operations import Operation
from tianshu.runtime.errors import AppError
from tianshu.types import Action, OriginKind, Receipt, TrustedContext


def directory(*, adapter="onebot", cached=None, data=None):
    context = TrustedContext("manager", "actor", None, "group-a", ("scope",), (), 1,
        OriginKind.HUMAN, frozenset({"read"}), delivery_allowed=False, conversation_kind="group")
    connection = {"id": "bot", "adapter": adapter, "provider": "snowluma", "revision": 3}
    target = "group:-123:thread:5" if adapter == "telegram" else "group:123"
    scene = {"connection_id": "bot", "kind": "group", "target_ref": target}

    @asynccontextmanager
    async def transaction(**kwargs):
        yield object()

    async def authorize(current, conversation_id):
        if conversation_id != "group-a":
            raise AppError("conversation_forbidden", status=403)
        return replace(current, conversation_id=conversation_id)

    receipt = Receipt("receipt", "action", "attempt", "succeeded", "none", "not_requested",
        result={"data": data if data is not None else {"group_id": 123, "group_name": "真实群名"}})
    service = SimpleNamespace(
        runtime=SimpleNamespace(database=SimpleNamespace(transaction=transaction)),
        identity=SimpleNamespace(authorize_conversation=AsyncMock(side_effect=authorize),
            archive_scene_on=AsyncMock(return_value=(scene,))),
        repository=SimpleNamespace(connection=AsyncMock(return_value=connection),
            observation=AsyncMock(return_value=cached), observe=AsyncMock()),
        actions=SimpleNamespace(invoke=AsyncMock(return_value=receipt)),
    )
    return service, context, connection


async def test_group_name_lookup_keeps_observation_only_context_and_existing_ledger():
    service, context, _ = directory()
    assert await group_names(service, context, ["group-a"]) == {"names": {"group-a": "真实群名"}}
    action = service.actions.invoke.await_args.args[0]
    assert action.capability_id == "channels.read" and action.origin_kind == OriginKind.OBSERVATION
    assert not action.context.delivery_allowed and action.context.permissions == context.permissions
    assert action.arguments == {"connection_id": "bot", "api_name": "get_group_info", "params": {"group_id": "123"}}


async def test_fresh_cache_does_not_contact_transport_but_still_checks_authority():
    service, context, _ = directory(cached={"age_seconds": 12,
        "body": {"connection_revision": 3, "name": "已有群名", "error_code": None}})
    assert await group_names(service, context, ["group-a", "group-a"]) == {"names": {"group-a": "已有群名"}}
    service.actions.invoke.assert_not_awaited()
    service.identity.authorize_conversation.assert_awaited_once()
    with pytest.raises(AppError, match="conversation_forbidden"):
        await group_names(service, context, ["other-role-group"])


@pytest.mark.parametrize("refresh,revision,age", [(True, 3, 1), (False, 2, 1), (False, 3, 301)])
async def test_refresh_expiry_and_changed_connection_read_current_name(refresh, revision, age):
    service, context, _ = directory(cached={"age_seconds": age,
        "body": {"connection_revision": revision, "name": "旧群名", "error_code": None}})
    assert await group_names(service, context, ["group-a"], refresh=refresh) == {"names": {"group-a": "真实群名"}}
    service.actions.invoke.assert_awaited_once()


@pytest.mark.parametrize("data", [{"group_id": 999, "group_name": "其他群"}, {"group_id": 123, "group_name": ""}])
async def test_wrong_target_or_missing_name_keeps_last_known_name(data):
    service, context, _ = directory(data=data, cached={"age_seconds": 301,
        "body": {"connection_revision": 3, "name": "原群名", "error_code": None}})
    assert await group_names(service, context, ["group-a"]) == {"names": {"group-a": "原群名"}}
    assert service.repository.observe.await_args.args[-1]["error_code"]


async def test_unavailable_transport_returns_no_invented_name_and_negatively_caches():
    service, context, _ = directory()
    service.actions.invoke.side_effect = TimeoutError()
    assert await group_names(service, context, ["group-a"]) == {"names": {}}
    assert service.repository.observe.await_args.args[-1]["error_code"] == "group_name_timeout"


async def test_telegram_title_uses_actual_chat_and_keeps_thread_out_of_lookup():
    service, context, _ = directory(adapter="telegram", data={"id": -123, "title": "Telegram 群"})
    assert await group_names(service, context, ["group-a"]) == {"names": {"group-a": "Telegram 群"}}
    assert service.actions.invoke.await_args.args[0].arguments["params"] == {"chat_id": "-123"}


async def test_metadata_read_accepts_normalized_id_and_rejects_unrelated_targets():
    service, context, connection = directory()
    await check_metadata_target(service, context, connection, "get_group_info", {"group_id": 123})
    for connection_id, name, params in [("other", "get_group_info", {"group_id": 123}),
        ("bot", "get_group_info", {"group_id": 456}), ("bot", "get_group_list", {})]:
        with pytest.raises(ChannelError, match="channel_target_mismatch"):
            await check_metadata_target(service, context, {**connection, "id": connection_id}, name, params)


async def test_read_operation_rejects_other_group_before_preparing_any_call():
    service, context, _ = directory()
    context = replace(context, origin_kind=OriginKind.OBSERVATION)
    action = Action("action", "key", "channels.read", context.actor_id, context,
        {"connection_id": "bot", "api_name": "get_group_info", "params": {"group_id": "456"}},
        OriginKind.OBSERVATION)
    service.repository.prepare_call = AsyncMock()
    with pytest.raises(ChannelError, match="channel_target_mismatch"):
        await Operation(service, "read").execute(action, SimpleNamespace(attempt_id="attempt"))
    service.repository.prepare_call.assert_not_awaited()
