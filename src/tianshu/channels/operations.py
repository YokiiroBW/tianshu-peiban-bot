"""Explicit channel configuration, probe and native API commands on the sole ledger."""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4
from tianshu.types import Receipt, OriginKind
from tianshu.runtime.serialization import wire
from tianshu.capabilities.catalog import CapabilityDefinition, Availability
from tianshu.capabilities.contracts import canonical
from .contracts import ChannelError
from .onebot.registry import BY_NAME, resolve_api, validate_call
from .telegram import BOT_API_METHODS


ONEBOT_PROBE_APIS = (
    "get_login_info",
    "get_version_info",
    "get_status",
    "can_send_image",
    "can_send_record",
)
TELEGRAM_PROBE_APIS = ("getMe",)


def probe_apis(adapter):
    return TELEGRAM_PROBE_APIS if adapter == "telegram" else ONEBOT_PROBE_APIS


def technical_api(name):
    """Transport diagnostics/credentials/control, rather than content read operations."""
    if name in ONEBOT_PROBE_APIS or name in TELEGRAM_PROBE_APIS:
        return True
    spec = BY_NAME.get(name)
    return bool(spec and (spec.sensitive or spec.effect == "control"))


def expression_api(name, connection):
    if connection["adapter"] == "telegram":
        return name.startswith("send") and name != "sendChatAction"
    spec = resolve_api(name, connection)
    return bool(spec and (spec.delivery or spec.name in {
        "send_msg", "send_private_msg", "send_group_msg",
        "send_group_forward_msg", "send_private_forward_msg"}))


class Operation:
    def __init__(self, service, name):
        self.service, self.name = service, name
        self.definition = CapabilityDefinition(
            "channels." + name,
            "渠道 / " + name,
            "执行真实渠道配置、探测或明确原生操作。",
            {"type": "object"},
            {"type": "object"},
            "read" if name in {"probe", "read"} else "write",
            permissions=frozenset() if name == "read" else frozenset({"external.manage"}),
        )

    async def availability(self, context):
        return Availability(True, True)

    async def execute(self, action, attempt):
        arguments = (
            await self.service.configuration.command(action.context, action.arguments)
            if "command_ref" in action.arguments
            else dict(action.arguments)
        )
        if self.name == "configure":
            value = await self.service.repository.save_connection(action.context, arguments)
            return Receipt(
                str(uuid4()),
                action.action_id,
                attempt.attempt_id,
                "succeeded",
                "confirmed",
                "not_requested",
                result=value,
            )
        connection = await self.service.repository.connection(
            arguments["connection_id"], action.context
        )
        if self.name == "probe":
            names = probe_apis(connection["adapter"])
            identifiers = [
                await self.service.repository.prepare_call(
                    action,
                    attempt,
                    connection["id"],
                    name,
                    {},
                    suffix="probe:" + str(index),
                    expected_count=len(names),
                )
                for index, name in enumerate(names)
            ]
            results = {}
            for name, identifier in zip(names, identifiers):
                outcome = await self.service.execute_call(identifier)
                if outcome.state != "confirmed":
                    return await self.receipt(action, attempt)
                row = await self.service.repository.call(identifier)
                data = self.service.repository.outcome(row, include_data=True).data
                actual = (
                    data.get("id")
                    if name == "getMe"
                    else data.get("user_id")
                    if name == "get_login_info"
                    else None
                )
                if actual is not None and str(actual) != connection["self_external_id"]:
                    raise ChannelError("channel_account_mismatch", 403)
                if name == "getMe" and data.get("username"):
                    async with self.service.runtime.database.transaction() as conn:
                        await conn.execute(
                            "UPDATE channels.connections SET body=jsonb_set(body,'{username}',%s::jsonb) WHERE id=%s",
                            (canonical(data["username"]), connection["id"]),
                        )
                results[name] = data
            return Receipt(
                str(uuid4()),
                action.action_id,
                attempt.attempt_id,
                "succeeded",
                "none",
                "not_requested",
                result={
                    "connection_id": connection["id"],
                    "observations": results,
                    "verified_at": datetime.now(UTC).isoformat(),
                },
            )
        name = arguments["api_name"]
        params = arguments.get("params", {})
        if connection["adapter"] == "telegram":
            if name not in BOT_API_METHODS:
                raise ChannelError("telegram_api_not_implemented", 400)
            effect = "read" if name.startswith("get") else "write"
            sensitive = False
        else:
            spec, params = validate_call(name, params, connection)
            effect = spec.effect
            sensitive = spec.sensitive
            if connection.get("provider") == "snowluma":
                name = spec.name
        if self.name == "read" and (
            action.origin_kind != OriginKind.OBSERVATION
            or name not in {"get_msg", "get_forward_msg", "get_image", "get_record", "getFile"}
        ):
            raise ChannelError("channel_internal_read_only", 403)
        if expression_api(name, connection):
            if self.service.expression is None:
                raise ChannelError("expression_port_unavailable", 503)
            target = await self.service.identity.channel_target(
                action.context, action.context.conversation_id
            )
            self.service.verify_target(target, params, connection["id"])
            from tianshu.conversation.types import ExpressionPlan

            if connection["adapter"] == "telegram":
                if name != "sendMessage":
                    raise ChannelError("native_send_use_ordered_expression", 409)
                parts = ({"kind": "text", "text": params["text"]},)
            elif connection.get("provider") == "snowluma":
                parts = ({"kind": "channel_action", "channel": "onebot.v11",
                          "api_name": name, "params": params},)
            else:
                from .onebot.messages import segments

                parts = tuple(
                    {"kind": "channel_segment", "channel": "onebot.v11", "segment": row}
                    for row in segments(params.get("message", params.get("messages", [])))
                )
            plan = ExpressionPlan(
                action.expression_id or "native:" + action.action_id,
                action.actor_id,
                action.context.conversation_id,
                parts,
                action.source_refs,
                action.origin_kind,
            )
            actual = await self.service.expression.send(action.context, plan)
            return replace(
                actual,
                receipt_id=str(uuid4()),
                action_id=action.action_id,
                attempt_id=attempt.attempt_id,
                result={**actual.result, "expression_id": plan.expression_id},
            )
        reply_receipt = None
        if name == ".handle_quick_operation":
            if action.origin_kind != OriginKind.HUMAN:
                raise ChannelError("channel_human_operation_required", 403)
            target = await self.service.identity.channel_target(
                action.context, action.context.conversation_id
            )
            native_context = params["context"]
            if (
                not isinstance(native_context, dict)
                or str(native_context.get("self_id")) != connection["self_external_id"]
            ):
                raise ChannelError("channel_account_mismatch", 403)
            self.service.verify_target(target, native_context, connection["id"])
            operation = dict(params["operation"])
            reply = operation.pop("reply", None)
            auto_escape = operation.pop("auto_escape", False)
            if isinstance(reply, str) and auto_escape:
                reply = [{"type": "text", "data": {"text": reply}}]
            if reply is not None:
                if self.service.expression is None:
                    raise ChannelError("expression_port_unavailable", 503)
                from tianshu.conversation.types import ExpressionPlan
                from .onebot.messages import segments

                reply_receipt = await self.service.expression.send(
                    action.context,
                    ExpressionPlan(
                        action.expression_id or "quick:" + action.action_id,
                        action.actor_id,
                        action.context.conversation_id,
                        tuple(
                            {"kind": "channel_segment", "channel": "onebot.v11", "segment": item}
                            for item in segments(reply)
                        ),
                        action.source_refs,
                        action.origin_kind,
                    ),
                )
            if not operation:
                if reply_receipt is None:
                    raise ChannelError("quick_operation_empty", 400)
                return replace(
                    reply_receipt,
                    receipt_id=str(uuid4()),
                    action_id=action.action_id,
                    attempt_id=attempt.attempt_id,
                )
            params = {**params, "operation": operation}
        if effect != "read":
            if action.origin_kind != OriginKind.HUMAN:
                raise ChannelError("channel_human_operation_required", 403)
            if params.get("group_id") or params.get("user_id"):
                target = await self.service.identity.channel_target(
                    action.context, action.context.conversation_id
                )
                self.service.verify_target(target, params, connection["id"])
        identifier = await self.service.repository.prepare_call(
            action, attempt, connection["id"], name, params
        )
        await self.service.execute_call(identifier)
        receipt = await self.receipt(action, attempt, sensitive=sensitive)
        if reply_receipt is not None:
            receipt = replace(
                receipt,
                delivery=reply_receipt.delivery,
                result={
                    **receipt.result,
                    "expression_id": reply_receipt.result.get("expression_id"),
                    "reply_receipt": wire(reply_receipt),
                },
            )
        return receipt

    async def receipt(self, action, attempt, *, sensitive=False):
        rows = await self.service.repository.calls_for(action.action_id, attempt.attempt_id)
        outcomes = [
            self.service.repository.outcome(row, include_data=not sensitive) for row in rows
        ]
        if not rows:
            _, persisted, current = await self.service.actions.load_for_execution(action.action_id)
            if persisted.dispatch == "not_started" and current.execution in {"pending", "running"}:
                return current
            return Receipt(
                str(uuid4()),
                action.action_id,
                attempt.attempt_id,
                "unknown",
                "unknown",
                "not_requested",
                error_code="channel_original_call_missing",
            )
        confirmed = len(rows) == rows[0]["expected_count"] and all(
            item.state == "confirmed" for item in outcomes
        )
        rejected = any(item.state == "rejected" for item in outcomes)
        unknown = any(item.state == "unknown" for item in outcomes)
        execution = (
            "succeeded"
            if confirmed
            else "unknown"
            if unknown
            else "failed"
            if rejected
            else "pending"
        )
        result = {
            "calls": [
                {"call_id": row["id"], "api_name": row["api_name"], "state": row["state"]}
                for row in rows
            ],
            "data": outcomes[-1].data if not sensitive else None,
        }
        if sensitive:
            result["sensitive_result_ref"] = rows[-1]["id"]
        from tianshu.capabilities.serialization import artifact_from

        artifacts = tuple(
            artifact_from(outcome.data["artifact"])
            for row, outcome in zip(rows, outcomes)
            if row["api_name"] in {"download_file_stream", "download_file_image_stream",
                                   "download_file_record_stream"}
            and outcome.state == "confirmed" and isinstance(outcome.data, dict)
            and "artifact" in outcome.data
        )
        return Receipt(
            str(uuid4()),
            action.action_id,
            attempt.attempt_id,
            execution,
            "unknown"
            if unknown
            else "none"
            if self.name in {"read", "probe"} or not confirmed
            else "confirmed",
            "not_requested",
            result=result,
            artifacts=artifacts,
            error_code=next((x.error_code for x in outcomes if x.error_code), None),
        )

    async def reconcile(self, action, attempt):
        arguments = (
            await self.service.configuration.command(action.context, action.arguments)
            if "command_ref" in action.arguments
            else action.arguments
        )
        name = arguments.get("api_name", "")
        connection = (
            await self.service.repository.connection(arguments["connection_id"], action.context)
            if name else {"adapter": "onebot"}
        )
        delegated = expression_api(name, connection) and self.name != "read"
        quick_reply = name == ".handle_quick_operation" and "reply" in arguments.get(
            "params", {}
        ).get("operation", {})
        if delegated or quick_reply:
            expression_id = (
                action.expression_id or ("quick:" if quick_reply else "native:") + action.action_id
            )
            actual = await self.service.delivery.get(action.context, expression_id)
            rows = await self.service.repository.calls_for(action.action_id, attempt.attempt_id)
            if actual is not None and (delegated or not rows):
                return replace(
                    actual,
                    receipt_id=str(uuid4()),
                    action_id=action.action_id,
                    attempt_id=attempt.attempt_id,
                    result={**actual.result, "expression_id": expression_id},
                )
        return await self.receipt(
            action, attempt, sensitive=bool(resolve_api(name, connection)
                                            and resolve_api(name, connection).sensitive)
        )

    async def cancel(self, action, attempt):
        for row in await self.service.repository.calls_for(action.action_id, attempt.attempt_id):
            await self.service.cancel_call(row["id"])
        return await self.reconcile(action, attempt)
