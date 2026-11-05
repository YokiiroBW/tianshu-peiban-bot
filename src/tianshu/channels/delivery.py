"""One expression identity maps to the sole Action ledger and real transport receipts."""

from dataclasses import replace
from uuid import uuid4, uuid5, NAMESPACE_URL
from tianshu.types import Action, Receipt, MediaRef, OriginKind, ServiceClass
from tianshu.runtime.serialization import wire
from tianshu.capabilities.contracts import canonical, digest
from tianshu.capabilities.serialization import receipt_from
from tianshu.capabilities.catalog import CapabilityDefinition, Availability
from tianshu.conversation.types import ExpressionPlan
from .contracts import ChannelError
from .onebot.registry import qq_id
from .onebot.messages import validate_outbound


class ContactPreflightStopped(Exception):
    """Known pre-submit policy stop; transports must not turn it into unknown I/O."""

    def __init__(self, decision):
        super().__init__(decision["reason"])
        self.decision = decision
        self.code = "contact_preflight_stopped"


class BehaviorPreflightPending(Exception):
    code = "behavior_preflight_pending"


class BehaviorPreflightInvalid(Exception):
    code = "behavior_invalid"


class ChannelDelivery:
    def __init__(self, service):
        self.service = service

    async def deliver(self, context, plan):
        if not context.delivery_allowed:
            raise ChannelError("channel_delivery_not_allowed", 403)
        if plan.target_ref != context.conversation_id or plan.actor_id != context.actor_id:
            raise ChannelError("channel_target_mismatch", 403)
        target = await self.service.identity.channel_target(context, plan.target_ref)
        context = target["context"]
        refs = tuple(plan.source_refs) + await self.service.media.references(
            context, plan.public_parts
        )
        plan = replace(plan, source_refs=refs)
        identifier = str(uuid5(NAMESPACE_URL, "tianshu:expression:" + plan.expression_id))
        semantics = digest(wire(plan))
        async with self.service.runtime.database.transaction() as conn:
            if not await self.service.sources.validate_persistence(conn, context, refs):
                raise ChannelError("source_revoked", 410)
            old = await (
                await conn.execute(
                    "SELECT * FROM channels.expressions WHERE expression_id=%s FOR UPDATE",
                    (plan.expression_id,),
                )
            ).fetchone()
            if old and (
                old["semantics"] != semantics
                or old["principal_id"] != context.principal_id
                or old["conversation_id"] != context.conversation_id
            ):
                raise ChannelError("expression_conflict", 409)
            await conn.execute(
                "INSERT INTO channels.expressions(expression_id,action_id,principal_id,conversation_id,semantics) VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                (
                    plan.expression_id,
                    identifier,
                    context.principal_id,
                    context.conversation_id,
                    semantics,
                ),
            )
        if old:
            found = await self.get(context, plan.expression_id)
            if found is not None:
                return found
        action = Action(
            identifier,
            "expression:" + plan.expression_id,
            "channels.deliver",
            context.actor_id,
            context,
            {"plan": wire(plan)},
            plan.origin_kind,
            source_refs=refs,
            expression_id=plan.expression_id,
        )
        return await self.service.actions.invoke(
            action, background=True, service_class=ServiceClass.INTERACTIVE,
            behavior_refs=plan.behavior_refs, behavior_purpose="conversation"
        )

    async def get(self, context, expression_id):
        async with self.service.runtime.database.transaction(read_only=True) as conn:
            row = await (
                await conn.execute(
                    "SELECT * FROM channels.expressions WHERE expression_id=%s", (expression_id,)
                )
            ).fetchone()
        if not row:
            return None
        if (
            row["principal_id"] != context.principal_id
            or row["conversation_id"] != context.conversation_id
        ):
            raise ChannelError("forbidden", 403)
        await self.service.identity.channel_target(context, row["conversation_id"])
        try:
            return receipt_from(
                (await self.service.actions.get(row["action_id"], context))["receipt"]
            )
        except Exception as error:
            if getattr(error, "status", None) == 404:
                return None
            raise

    async def reconcile(self, context, expression_id):
        current = await self.get(context, expression_id)
        if current is None:
            return None
        return await self.service.actions.reconcile(current.action_id, context)


class DeliveryHandler:
    definition = CapabilityDefinition(
        "channels.deliver",
        "提交有序表达",
        "唯一表达出口提交真实渠道，不推断发送成功。",
        {"type": "object", "required": ["plan"]},
        {"type": "object"},
        "write",
    )

    def __init__(self, service):
        self.service = service

    async def availability(self, context):
        return Availability(True, True)

    async def behavior_preflight(self, action):
        from tianshu.conversation.behaviors import behavior_status
        refs = tuple(action.arguments.get("plan", {}).get("behavior_refs", ()))
        state, _ = await behavior_status(getattr(self.service, "behavior_resolver", None), action.context, refs)
        if state == "pending":
            raise BehaviorPreflightPending()
        if state == "invalid":
            raise BehaviorPreflightInvalid()

    @staticmethod
    def begun(attempt, rows, *, current_call_id=None):
        return getattr(attempt, "dispatch", "not_started") != "not_started" or any(
            row.get("submitted_at") or row["state"] in {"confirmed", "accepted", "unknown"}
            or row["state"] == "sending" and row["id"] != current_call_id for row in rows)

    async def preflight(self, action, attempt, *, current_call_id=None):
        if action.capability_id != "channels.deliver":
            return None
        candidate = action.arguments["plan"].get("candidate_id")
        check = getattr(self.service, "contact_preflight", None)
        if not candidate or check is None:
            return None
        rows = await self.service.repository.calls_for(action.action_id, attempt.attempt_id)
        if self.begun(attempt, rows, current_call_id=current_call_id):
            return None
        decision = await check(action.context, candidate, expression_id=action.expression_id)
        return decision if decision["decision"] != "send" else None

    async def stopped(self, action, attempt, decision):
        from .contracts import TransportResult

        rows = await self.service.repository.calls_for(action.action_id, attempt.attempt_id)
        if self.begun(attempt, rows):
            return None
        for row in rows:
            if row["state"] == "prepared":
                await self.service.repository.settle(row["id"], TransportResult(
                    "rejected", error_code="contact_preflight_stopped", raw={"contact_preflight": decision}))
        return Receipt(str(uuid4()), action.action_id, attempt.attempt_id,
            "cancelled", "none", "not_requested", result={"expression_id": action.expression_id,
                "contact_preflight": decision}, error_code=decision["reason"])

    async def execute(self, action, attempt):
        target = await self.service.identity.channel_target(
            action.context, action.context.conversation_id
        )
        context = target["context"]
        if not context.delivery_allowed:
            raise ChannelError("channel_delivery_not_allowed", 403)
        value = action.arguments["plan"]
        if value["target_ref"] != context.conversation_id:
            raise ChannelError("channel_target_mismatch", 403)
        existing = await self.service.repository.calls_for(action.action_id, attempt.attempt_id)
        if existing and (self.begun(attempt, existing) or any(row["state"] != "prepared" for row in existing)):
            return await self.reconcile(action, attempt)
        decision = await self.preflight(action, attempt)
        if decision:
            stopped = await self.stopped(action, attempt, decision)
            return stopped or await self.reconcile(action, attempt)
        if target["adapter"] == "web":
            if self.service.web is None:
                raise ChannelError("web_publication_port_unavailable", 503)
            from tianshu.capabilities.serialization import artifact_from

            plan = ExpressionPlan(
                value["expression_id"],
                value["actor_id"],
                value["target_ref"],
                tuple(value["public_parts"]),
                action.source_refs,
                OriginKind(value["origin_kind"]),
                value.get("turn_id"),
                tuple(artifact_from(x) for x in value.get("artifacts", [])),
                value.get("candidate_id"),
                tuple(value.get("behavior_refs", ())),
            )
            identifier = await self.service.repository.prepare_call(
                action,
                attempt,
                "web",
                "web.publish",
                {"expression_id": plan.expression_id},
            )
            decision = await self.preflight(action, attempt)
            if decision:
                stopped = await self.stopped(action, attempt, decision)
                return stopped or await self.reconcile(action, attempt)
            try:
                await self.behavior_preflight(action)
            except BehaviorPreflightPending:
                await self.service.defer_delivery(action.action_id)
                return Receipt(str(uuid4()), action.action_id, attempt.attempt_id, "pending", "none", "pending",
                               result={"expression_id": action.expression_id}, error_code="behavior_preflight_pending")
            except BehaviorPreflightInvalid:
                from .contracts import TransportResult
                await self.service.repository.settle(identifier, TransportResult("rejected", error_code="behavior_invalid"))
                return await self.reconcile(action, attempt)
            async with self.service.runtime.database.transaction() as conn:
                persisted_call = await self.service.repository.call(identifier)
                if persisted_call.get("deployment_generation") is not None:
                    await self.service.runtime.jobs.assert_generation_on(conn, persisted_call["deployment_generation"])
                if not await self.service.sources.validate_persistence(
                    conn, context, action.source_refs
                ):
                    raise ChannelError("source_revoked", 410)
                claimed = await (
                    await conn.execute(
                        "UPDATE channels.calls SET state='sending',submitted_at=now() WHERE id=%s AND state='prepared' RETURNING id,state,created_at",
                        (identifier,),
                    )
                ).fetchone()
                from .observability import record_ledger_on
                await record_ledger_on(self.service, conn, scope="outbound", row=claimed)
            if not claimed:
                return await self.reconcile(action, attempt)
            await self.service.actions.submitted(attempt, identifier)
            from .contracts import TransportResult

            try:
                actual = await self.service.web.publish(context, plan)
            except BaseException:
                import asyncio

                await asyncio.shield(
                    self.service.repository.settle(
                        identifier,
                        TransportResult("unknown", error_code="web_publication_interrupted"),
                    )
                )
                raise
            await self.service.repository.settle(identifier, self.web_outcome(actual))
            return replace(
                actual,
                receipt_id=str(uuid4()),
                action_id=action.action_id,
                attempt_id=attempt.attempt_id,
            )
        connection = await self.service.repository.connection(target["connection_id"], context)
        calls = await compile_calls(
            self.service, context, target, connection, value["public_parts"]
        )
        identifiers = []
        for index, (name, params) in enumerate(calls):
            call_id = await self.service.repository.prepare_call(
                action,
                attempt,
                connection["id"],
                name,
                params,
                suffix=str(index),
                expected_count=len(calls),
            )
            identifiers.append(call_id)
        for call_id in identifiers:
            try:
                result = await self.service.execute_call(call_id)
            except ChannelError as error:
                if error.effect == "unknown":
                    return await self.reconcile(action, attempt)
                if error.code not in {"source_revoked", "source_unavailable", "channel_delivery_not_allowed",
                                      "channel_target_connection_mismatch", "channel_configuration_changed"}:
                    raise
                from .contracts import TransportResult
                for pending_id in identifiers:
                    pending = await self.service.repository.call(pending_id)
                    if pending["state"] == "prepared":
                        await self.service.repository.settle(pending_id, TransportResult("rejected", error_code=error.code))
                return await self.reconcile(action, attempt)
            if result.state != "confirmed":
                if result.error_code == "behavior_invalid":
                    from .contracts import TransportResult
                    for pending_id in identifiers:
                        pending = await self.service.repository.call(pending_id)
                        if pending["state"] == "prepared":
                            await self.service.repository.settle(pending_id, TransportResult("rejected", error_code="behavior_invalid"))
                break
        return await self.reconcile(action, attempt)

    async def resume(self, action, attempt):
        rows = await self.service.repository.calls_for(action.action_id, attempt.attempt_id)
        if any(row["state"] in {"unknown", "accepted", "sending"} for row in rows):
            return await self.reconcile(action, attempt)
        if rows and rows[0]["api_name"] == "web.publish":
            return await self.execute(action, attempt)
        from .contracts import TransportResult
        for row in rows:
            if row["state"] != "prepared":
                continue
            try:
                outcome = await self.service.execute_call(row["id"])
            except ChannelError as error:
                if error.effect == "unknown":
                    return await self.reconcile(action, attempt)
                if error.code not in {"source_revoked", "source_unavailable", "channel_delivery_not_allowed",
                                      "channel_target_connection_mismatch", "channel_configuration_changed"}:
                    raise
                for other in rows:
                    current = await self.service.repository.call(other["id"])
                    if current["state"] == "prepared":
                        await self.service.repository.settle(other["id"], TransportResult("rejected", error_code=error.code))
                return await self.reconcile(action, attempt)
            if outcome.state != "confirmed":
                if outcome.error_code == "behavior_invalid":
                    for other in rows:
                        current = await self.service.repository.call(other["id"])
                        if current["state"] == "prepared":
                            await self.service.repository.settle(other["id"], TransportResult("rejected", error_code="behavior_invalid"))
                break
        return await self.reconcile(action, attempt)

    async def reconcile(self, action, attempt):
        rows = await self.service.repository.calls_for(action.action_id, attempt.attempt_id)
        if not rows:
            _, persisted_attempt, current = await self.service.actions.load_for_execution(
                action.action_id
            )
            if current.execution == "cancelled" and current.result.get("contact_preflight"):
                return current
            if persisted_attempt.dispatch == "not_started" and current.execution in {
                "pending",
                "running",
            }:
                return current
            return Receipt(
                str(uuid4()),
                action.action_id,
                attempt.attempt_id,
                "unknown",
                "unknown",
                "unknown",
                result={"expression_id": action.expression_id},
                error_code="channel_no_original_call",
            )
        for row in rows:
            if row["api_name"] == "web.publish" and row["state"] in {
                "unknown",
                "accepted",
                "sending",
            }:
                actual = await self.service.web.get(action.context, action.expression_id)
                if actual is not None:
                    await self.service.repository.settle(row["id"], self.web_outcome(actual))
            elif row["state"] in {"unknown", "accepted", "sending"}:
                await self.service.reconcile_call(row["id"])
        rows = await self.service.repository.calls_for(action.action_id, attempt.attempt_id)
        if not self.begun(attempt, rows):
            for row in rows:
                if (row.get("result") or {}).get("error_code") == "contact_preflight_stopped":
                    actual = self.service.repository.outcome(row, include_data=True)
                    decision = actual.raw.get("contact_preflight")
                    if decision:
                        stopped = await self.stopped(action, attempt, decision)
                        if stopped:
                            return stopped
        results = [self.service.repository.outcome(row) for row in rows]
        ids = tuple(identifier for result in results for identifier in result.message_ids)
        states = {result.state for result in results}
        if states == {"confirmed"}:
            execution, effect, delivery = "succeeded", "confirmed", "delivered"
        elif states <= {"rejected"}:
            execution, effect, delivery = "failed", "none", "failed"
        elif "unknown" in states or "sending" in states:
            execution, effect, delivery = "unknown", "unknown", "unknown"
        elif "rejected" in states:
            execution, effect, delivery = "failed", "confirmed" if ids else "none", "failed"
        else:
            execution, effect, delivery = (
                "pending",
                "confirmed" if ids else "unknown" if any(row["submitted_at"] for row in rows) else "none",
                "pending",
            )
        expected = rows[0]["expected_count"]
        if len(rows) != expected:
            execution, effect, delivery = "unknown", "confirmed" if ids else "unknown", "unknown"
        return Receipt(
            str(uuid4()),
            action.action_id,
            attempt.attempt_id,
            execution,
            effect,
            delivery,
            result={
                "expression_id": action.expression_id,
                "message_ids": ids,
                "delivery_progress": ("delivered" if states == {"confirmed"}
                                      else "partial_unknown" if ids and ("unknown" in states or "sending" in states)
                                      else "partial" if ids else "none"),
                "calls": [
                    {"call_id": row["id"], "api_name": row["api_name"], "state": row["state"],
                     "submitted_at": wire(row.get("submitted_at")), "completed_at": wire(row.get("completed_at")),
                     "error_code": (row.get("result") or {}).get("error_code"),
                     "retcode": (row.get("result") or {}).get("retcode")}
                    for row in rows
                ],
            },
            error_code=next((x.error_code for x in results if x.error_code), None),
        )

    @staticmethod
    def web_outcome(receipt):
        from .contracts import TransportResult

        state = (
            "confirmed"
            if receipt.delivery == "delivered"
            else "rejected"
            if receipt.execution in {"failed", "cancelled"}
            else "unknown"
        )
        identifiers = tuple(str(value) for value in receipt.result.get("message_ids", ()))
        if receipt.result.get("message_id") is not None:
            identifiers += (str(receipt.result["message_id"]),)
        return TransportResult(
            state,
            dict(receipt.result),
            error_code=receipt.error_code,
            message_ids=identifiers,
            raw={"data": wire(receipt.result)},
        )

    async def cancel(self, action, attempt):
        # API interruption does not undo a message. Delete-message is an explicit
        # separately authorized command, never an invented rollback.
        for row in await self.service.repository.calls_for(action.action_id, attempt.attempt_id):
            if row["api_name"] != "web.publish" and row["state"] in {
                "sending",
                "accepted",
                "unknown",
            }:
                await self.service.cancel_call(row["id"])
        return await self.reconcile(action, attempt)


async def compile_calls(service, context, target, connection, parts):
    # Identity's target.adapter is its external namespace (often qq). The
    # registered connection chooses the actual native protocol/SDK adapter.
    adapter = connection["adapter"]
    actual = target["target_ref"]
    if adapter in {"onebot", "nonebot", "astrbot"}:
        destination, identifier = actual.split(":", 1)
        if destination not in {"private", "group"}:
            raise ChannelError("onebot_target", 400)
        rows = []
        calls = []
        total = 0
        target_key = "group_id" if destination == "group" else "user_id"

        def flush():
            if rows:
                validate_outbound(rows, connection)
                calls.append(
                    (
                        "send_" + destination + "_msg",
                        {target_key: qq_id(identifier), "message": list(rows)},
                    )
                )
                rows.clear()

        async def native_content(value, depth=0):
            from .onebot.messages import segments

            if depth > 4:
                raise ChannelError("outbound_forward_depth_budget", 413)
            content = []
            for child in segments(value):
                data = dict(child["data"])
                if child["type"] in {"image", "record", "video"}:
                    reference = data.pop("media", None)
                    if not isinstance(reference, dict):
                        raise ChannelError("native_media_reference_required", 400)
                    data["file"] = await service.media.outbound_onebot(
                        context, connection, MediaRef(**reference)
                    )
                elif child["type"] == "node" and "content" in data:
                    data["content"] = await native_content(data["content"], depth + 1)
                content.append({"type": child["type"], "data": data})
            return content

        async def forward(nodes):
            flush()
            native = []
            for node in nodes:
                data = dict(node)
                if "content" in data:
                    data["content"] = await native_content(data["content"])
                native.append({"type": "node", "data": data})
            validate_outbound(native, connection)
            name = "send_" + destination + "_forward_msg"
            from .onebot.registry import validate_call

            validate_call(name, {target_key: qq_id(identifier), "messages": native}, connection)
            calls.append((name, {target_key: qq_id(identifier), "messages": native}))

        for part in parts:
            kind = part["kind"]
            if kind == "text":
                rows.append({"type": "text", "data": {"text": part["text"]}})
            elif kind == "mention":
                rows.append({"type": "at", "data": {"qq": qq_id(part["user_id"])}})
            elif kind == "mention_all":
                if destination != "group" or not connection.get("allow_at_all"):
                    raise ChannelError("channel_at_all_not_configured", 409)
                rows.append({"type": "at", "data": {"qq": "all"}})
            elif kind in {"image", "reaction", "audio", "video", "file"}:
                media = MediaRef(**part["media"])
                encoded = await service.media.outbound_onebot(context, connection, media)
                total += len(encoded)
                if total > 64 * 1024 * 1024:
                    raise ChannelError("channel_message_media_budget", 413)
                if kind == "file":
                    flush()
                    name = "upload_group_file" if destination == "group" else "upload_private_file"
                    from .onebot.registry import validate_call

                    params = {
                        target_key: qq_id(identifier),
                        "file": encoded,
                        "name": part.get("filename", "tianshu-file"),
                    }
                    validate_call(name, params, connection)
                    calls.append((name, params))
                else:
                    rows.append(
                        {
                            "type": {"reaction": "image", "audio": "record"}.get(kind, kind),
                            "data": {"file": encoded},
                        }
                    )
            elif kind in {"reply", "reference"}:
                original = part.get("message_id") or part.get("event_id")
                if original:
                    rows.append({"type": "reply", "data": {"id": str(original).rsplit(":", 1)[-1]}})
                elif part.get("text"):
                    rows.append({"type": "text", "data": {"text": part["text"]}})
                else:
                    raise ChannelError("reply_message_id_required", 400)
            elif kind == "channel_segment":
                if part.get("channel") != "onebot.v11":
                    raise ChannelError("channel_segment_mismatch", 400)
                segment = dict(part["segment"])
                if segment["type"] == "node":
                    await forward([segment["data"]])
                else:
                    if segment["type"] in {"image", "record", "video"}:
                        if not part.get("media"):
                            raise ChannelError("native_media_reference_required", 400)
                        segment = {
                            "type": segment["type"],
                            "data": {
                                **segment["data"],
                                "file": await service.media.outbound_onebot(
                                    context, connection, MediaRef(**part["media"])
                                ),
                            },
                        }
                    rows.append(segment)
            elif kind == "forward":
                await forward(part["nodes"])
            else:
                raise ChannelError("channel_part_not_supported", 409)
        flush()
        if not calls:
            raise ChannelError("empty_expression", 400)
        return calls
    if adapter == "telegram":
        components = actual.split(":")
        chat_id = components[1]
        base = {"chat_id": chat_id}
        if len(components) > 3 and components[2] == "thread":
            base["message_thread_id"] = int(components[3])
        calls = []
        reply = None
        for part in parts:
            kind = part["kind"]
            params = dict(base)
            if reply:
                params["reply_parameters"] = {"message_id": reply}
            if kind in {"reply", "reference"} and (part.get("message_id") or part.get("event_id")):
                reply = int(str(part.get("message_id", part.get("event_id"))).rsplit(":", 1)[-1])
                continue
            if kind == "text":
                text = part["text"]
                for offset in range(0, len(text), 4096):
                    calls.append(("sendMessage", {**params, "text": text[offset : offset + 4096]}))
            elif kind in {"image", "reaction", "audio", "video", "file"}:
                media = MediaRef(**part["media"])
                field = (
                    "animation"
                    if media.mime_type == "image/gif"
                    else {
                        "image": "photo",
                        "reaction": "sticker",
                        "audio": "voice" if part.get("voice", True) else "audio",
                        "video": "video",
                        "file": "document",
                    }[kind]
                )
                method = {
                    "photo": "sendPhoto",
                    "animation": "sendAnimation",
                    "sticker": "sendSticker",
                    "audio": "sendAudio",
                    "voice": "sendVoice",
                    "video": "sendVideo",
                    "document": "sendDocument",
                }[field]
                params["_uploads"] = [
                    {
                        "field": field,
                        "media": wire(media),
                        "filename": part.get("filename", "tianshu-media"),
                        "mime_type": media.mime_type,
                    }
                ]
                calls.append((method, params))
            elif kind == "mention":
                user_id = str(part["user_id"])
                if not user_id.isdigit():
                    raise ChannelError("telegram_mention_id", 400)
                label = part.get("text", "user")
                calls.append(
                    (
                        "sendMessage",
                        {
                            **params,
                            "text": label,
                            "entities": [
                                {
                                    "type": "text_mention",
                                    "offset": 0,
                                    "length": len(label.encode("utf-16-le")) // 2,
                                    "user": {
                                        "id": int(user_id),
                                        "is_bot": False,
                                        "first_name": label,
                                    },
                                }
                            ],
                        },
                    )
                )
            else:
                raise ChannelError("telegram_part_not_supported", 409)
        if not calls:
            raise ChannelError("empty_expression", 400)
        return calls
    raise ChannelError("channel_adapter_not_implemented", 503)
