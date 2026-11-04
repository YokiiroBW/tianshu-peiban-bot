"""Resolve a configured QQ recipient to its original authorized channel scene."""

from __future__ import annotations

import json
import re

from tianshu.runtime.errors import AppError
from tianshu.runtime.serialization import wire
from tianshu.types import OriginKind


def receipt_state(receipt):
    if receipt is None:
        return {"state": "unknown", "code": "qq_receipt_unavailable"}
    value = wire(receipt)
    delivery, execution = value.get("delivery"), value.get("execution")
    result = {"receipt": value, "transport": "qq"}
    if delivery == "delivered":
        result.update(state="accepted", meaning="channel_delivered_not_read")
    elif delivery == "unknown" or execution == "unknown" or value.get("effect") == "unknown":
        result.update(state="unknown", code="qq_delivery_unknown")
    elif delivery == "failed" or execution in {"failed", "cancelled"}:
        result.update(state="failed", code=value.get("error_code") or "qq_delivery_failed")
    else:
        result.update(state="observing")
    return result


class QQOperationsNotifier:
    def __init__(self, identity, channels):
        self.identity, self.channels = identity, channels

    async def _scene(self, connection_id, recipient_id):
        value = recipient_id.removeprefix("private:") if isinstance(recipient_id, str) else ""
        if not re.fullmatch(r"[0-9]{1,20}", value):
            raise AppError("qq_recipient_invalid", status=422)
        target = "private:" + value
        # channel_context resolves only an already-bound connection/recipient;
        # it does not grant delivery. Rebuild the original principal's current
        # system scene and verify the exact binding with the identity owner.
        bound = await self.identity.channel_context(connection_id, target)
        context = await self.identity.context(bound.principal_id, actor_id=bound.actor_id,
            conversation_id=bound.conversation_id, origin_kind=OriginKind.SYSTEM)
        actual = await self.identity.channel_target(context, context.conversation_id)
        if actual.get("connection_id") != connection_id or actual.get("target_ref") != target:
            raise AppError("qq_binding_unavailable", status=409)
        return context

    async def __call__(self, *, connection_id, recipient_id, notification_id, payload):
        try:
            context = await self._scene(connection_id, recipient_id)
        except AppError as exc:
            return {"state": "failed", "code": exc.code}
        text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
        if len(text) > 9500:
            text = text[:9500] + "\n…完整影响范围请查看运维事件详情。"
        try:
            receipt = await self.channels.notify_operations(context, notification_id=notification_id,
                target_ref=context.conversation_id, text=text, source_refs=())
        except Exception:
            # The Action may already have been accepted by the shared owner.
            return {"state": "unknown", "code": "qq_submission_interrupted"}
        return receipt_state(receipt)

    async def observe(self, *, connection_id, recipient_id, notification_id):
        try:
            context = await self._scene(connection_id, recipient_id)
            receipt = await self.channels.operations_notification_status(context,
                notification_id=notification_id, reconcile=False)
            return receipt_state(receipt)
        except Exception:
            return {"state": "unknown", "code": "qq_observation_unavailable"}
