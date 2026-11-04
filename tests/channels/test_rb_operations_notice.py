"""Operations notices use the existing QQ delivery ledger and a stable system identity."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tianshu.channels.contracts import ChannelError
from tianshu.channels.service import Channels
from tianshu.types import OriginKind, Receipt, TrustedContext


def context(origin=OriginKind.SYSTEM):
    return TrustedContext("ops", "bound-actor", None, "admin-chat", ("ops-scope",), ("ops-scope",),
                          1, origin, conversation_kind="private", delivery_allowed=True)


@pytest.mark.asyncio
async def test_operations_notice_uses_stable_identity_and_real_delivery_port():
    ctx = context()
    service = object.__new__(Channels)
    service.identity = SimpleNamespace(channel_target=AsyncMock(return_value={"adapter": "qq",
        "connection_id": "qq-connection", "context": ctx}))
    service.repository = SimpleNamespace(connection=AsyncMock(return_value={"adapter": "onebot"}))
    expected = Receipt("receipt", "action", "attempt", "pending", "none", "pending")
    service.delivery = SimpleNamespace(deliver=AsyncMock(return_value=expected), get=AsyncMock(return_value=expected))
    first = await service.notify_operations(ctx, notification_id="incident-7", target_ref="admin-chat", text="需要查看")
    second = await service.notify_operations(ctx, notification_id="incident-7", target_ref="admin-chat", text="需要查看")
    plans = [call.args[1] for call in service.delivery.deliver.await_args_list]
    assert first is second is expected and plans[0] == plans[1]
    assert plans[0].expression_id.startswith("operations:") and plans[0].turn_id is None
    assert plans[0].origin_kind == OriginKind.SYSTEM and plans[0].public_parts[0]["text"].startswith("【天枢运维】")
    assert await service.operations_notification_status(ctx, notification_id="incident-7") is expected
    with pytest.raises(ChannelError, match="operations_notification_context"):
        await service.notify_operations(context(OriginKind.ACTOR), notification_id="incident-8", target_ref="admin-chat", text="内容")
