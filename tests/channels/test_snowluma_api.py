"""Provider contracts and public entry points; no live QQ account is used."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tianshu.channels.contracts import ChannelError, TransportResult
from tianshu.channels.delivery import compile_calls
from tianshu.channels.native import ChannelNativeApi
from tianshu.channels.onebot.registry import APIS, matrix, resolve_api, validate_call
from tianshu.channels.operations import Operation, expression_api
from tianshu.channels.service import Channels
from tianshu.channels.snowluma.catalog import ACTIONS, ALIASES, UNAVAILABLE
from tianshu.channels.transports import onebot_result
from tianshu.runtime.serialization import wire
from tianshu.types import Action, ArtifactRef, MediaRef, OriginKind, SourceRef, TrustedContext

CONNECTION = {
    "id": "snow",
    "provider": "snowluma",
    "adapter": "onebot",
    "revision": 7,
    "configured": True,
    "self_external_id": "12345",
}


def context():
    return TrustedContext(
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


def test_provider_catalog_is_complete_and_reports_real_unavailability():
    assert len(ACTIONS) == 222
    assert len(APIS) == 38
    rows = matrix(CONNECTION)["apis"]
    assert {x["name"] for x in rows if x["public"]} == set(ACTIONS)
    assert {x["name"] for x in rows if not x["implemented"]} == UNAVAILABLE
    for alias, canonical in ALIASES.items():
        assert resolve_api(alias, CONNECTION).name == canonical
    assert resolve_api("get_cookies", CONNECTION).sensitive
    assert resolve_api("request_decrypt_key", CONNECTION).sensitive
    assert len(matrix({"provider": "generic"})["apis"]) == 54
    assert "title" in resolve_api("send_tuwen_ark", CONNECTION).input_schema["properties"]
    assert "description" in resolve_api("set_qzone_profile", CONNECTION).input_schema["properties"]


@pytest.mark.parametrize("name", sorted(UNAVAILABLE))
def test_placeholder_handlers_cannot_return_fake_success(name):
    with pytest.raises(ChannelError) as error:
        validate_call(name, {}, CONNECTION)
    assert error.value.code == "provider_api_not_implemented"


def test_provider_schema_and_false_values_and_existing_extension_boundary():
    spec, params = validate_call(
        "set_group_admin_permissions",
        {"group_id": "123", "user_id": "456", "kick_member": False, "recall_member_message": False},
        CONNECTION,
    )
    assert params == {
        "group_id": 123,
        "user_id": 456,
        "kick_member": False,
        "recall_member_message": False,
    }
    assert spec.effect == "moderate"
    _, message = validate_call("get_msg", {"message_id": "-8"}, CONNECTION)
    assert message["message_id"] == -8
    for bad in (
        {"group_id": 123, "user_id": 456},
        {"group_id": 123, "user_id": 456, "kick_member": True},
        {"group_id": 123, "user_id": 456, "kick_member": "false"},
    ):
        with pytest.raises(ChannelError):
            validate_call(spec.name, bad, CONNECTION)
    with pytest.raises(ChannelError) as error:
        validate_call("get_qzone_feeds", {}, {**CONNECTION, "provider": "generic"})
    assert error.value.code == "provider_extension_not_declared"
    with pytest.raises(ChannelError):
        validate_call(
            "set_group_ban",
            {"group_id": 123, "user_id": 456},
            {**CONNECTION, "api_support": {"set_group_ban": False}},
        )


def test_native_read_upload_and_qzone_publish_have_distinct_semantics():
    for name in (
        "send_ark_share",
        "send_group_ark_share",
        "share_peer",
        "upload_forward_msg",
        "send_qzone_msg",
        "send_poke",
        "get_group_msg_history",
    ):
        assert not expression_api(name, CONNECTION)
    for name in ("send_custom_face", "send_flash_msg", "send_group_ai_record", "upload_group_file"):
        assert expression_api(name, CONNECTION)
    assert (
        onebot_result({"retcode": 0, "status": "ok", "data": {"message_id": 0}}).message_ids == ()
    )
    assert onebot_result({"retcode": False, "status": "ok"}).state == "unknown"


@pytest.mark.asyncio
async def test_facade_keeps_request_identity_sources_and_ledger():
    service = SimpleNamespace(
        repository=SimpleNamespace(connection=AsyncMock(return_value=CONNECTION)),
        configuration=SimpleNamespace(seal_command=AsyncMock(side_effect=lambda c, i, a: a)),
        actions=SimpleNamespace(invoke=AsyncMock(return_value="receipt")),
    )
    api = ChannelNativeApi(service)
    refs = (SourceRef("source", 4),)
    for _ in range(2):
        assert (
            await api.invoke(
                context(),
                "snow",
                "get_group_msg_history",
                {"group_id": 123},
                request_id="history-1",
                source_refs=refs,
            )
            == "receipt"
        )
    actions = [x.args[0] for x in service.actions.invoke.await_args_list]
    assert actions[0].action_id == actions[1].action_id
    assert actions[0].source_refs == refs
    assert actions[0].capability_id == "channels.api"
    assert service.actions.invoke.await_args.kwargs["background"] is False
    description = await api.describe(context(), "snow", api_name="get_record_text")
    assert [x["name"] for x in description["apis"]] == ["fetch_ptt_text"]
    assert description["connection_revision"] == 7


@pytest.mark.asyncio
async def test_specialized_chat_delivery_checks_target_and_cq_at_all():
    target = {"target_ref": "group:123", "connection_id": "snow"}
    service = SimpleNamespace(verify_target=Channels.verify_target)
    # Bind the real verifier without constructing a database owner.
    service.verify_target = lambda t, p, c: Channels.verify_target(service, t, p, c)
    part = {
        "kind": "channel_action",
        "channel": "onebot.v11",
        "api_name": "send_custom_face",
        "params": {"group_id": 123, "emoji_id": "emoji"},
    }
    assert await compile_calls(service, context(), target, CONNECTION, [part]) == [
        ("send_custom_face", {"group_id": 123, "emoji_id": "emoji"})
    ]
    with pytest.raises(ChannelError):
        await compile_calls(
            service, context(), {**target, "target_ref": "private:123"}, CONNECTION, [part]
        )
    with pytest.raises(ChannelError):
        await compile_calls(
            service, replace(context(), permissions=frozenset()), target, CONNECTION, [part]
        )
    part = {
        **part,
        "api_name": "send_group_msg",
        "params": {"group_id": 123, "message": "[CQ:at,qq=all]"},
    }
    calls = await compile_calls(service, context(), target, CONNECTION, [part])
    assert calls[0][1]["message"] == [{"type": "at", "data": {"qq": "all"}}]
    part["params"]["message"] = [{"type": "json", "data": '{"app":"card"}'}]
    calls = await compile_calls(service, context(), target, CONNECTION, [part])
    assert calls[0][1]["message"][0]["data"]["data"] == '{"app":"card"}'


@pytest.mark.asyncio
async def test_qzone_publish_stays_native_and_preserves_existing_human_authority():
    service = SimpleNamespace(
        repository=SimpleNamespace(
            connection=AsyncMock(return_value=CONNECTION),
            prepare_call=AsyncMock(return_value="call"),
        ),
        execute_call=AsyncMock(),
        expression=SimpleNamespace(send=AsyncMock()),
    )
    handler = Operation(service, "api")
    handler.receipt = AsyncMock(return_value="receipt")
    action = Action(
        "action",
        "request",
        "channels.api",
        "actor",
        context(),
        {"connection_id": "snow", "api_name": "send_qzone_msg", "params": {"content": "hello"}},
        OriginKind.HUMAN,
    )
    assert await handler.execute(action, SimpleNamespace(attempt_id="attempt")) == "receipt"
    service.expression.send.assert_not_awaited()
    service.execute_call.assert_awaited_once_with("call")
    with pytest.raises(ChannelError) as error:
        await handler.execute(
            replace(action, origin_kind=OriginKind.TIMER), SimpleNamespace(attempt_id="next")
        )
    assert error.value.code == "channel_human_operation_required"


@pytest.mark.asyncio
async def test_download_receipt_exposes_typed_artifact():
    artifact = ArtifactRef("file", MediaRef("artifact:file", "sha256:x", "text/plain", 5, "x"))
    service = SimpleNamespace(
        repository=SimpleNamespace(
            calls_for=AsyncMock(
                return_value=[
                    {
                        "id": "call",
                        "api_name": "download_file_stream",
                        "expected_count": 1,
                        "state": "confirmed",
                    }
                ]
            ),
            outcome=lambda row, **kwargs: TransportResult(
                "confirmed", {"artifact": wire(artifact)}
            ),
        )
    )
    action = SimpleNamespace(action_id="action")
    receipt = await Operation(service, "api").receipt(action, SimpleNamespace(attempt_id="attempt"))
    assert receipt.execution == "succeeded"
    assert receipt.artifacts == (artifact,)


@pytest.mark.asyncio
async def test_configure_reconciliation_does_not_require_native_api_arguments():
    handler = Operation(
        SimpleNamespace(repository=SimpleNamespace(connection=AsyncMock())), "configure"
    )
    handler.receipt = AsyncMock(return_value="original")
    action = SimpleNamespace(arguments={"id": "snow"})
    assert await handler.reconcile(action, "attempt") == "original"
    handler.service.repository.connection.assert_not_awaited()
