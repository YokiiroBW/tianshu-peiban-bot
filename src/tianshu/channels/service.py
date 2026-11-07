"""Channel composition: one owner, durable ingress, native protocols and one Action ledger."""

import asyncio
import hmac
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4, uuid5, NAMESPACE_URL
from tianshu.types import Action, Receipt, OriginKind, ServiceClass
from tianshu.runtime.jobs import JobSpec
from tianshu.runtime.serialization import wire
from tianshu.capabilities.catalog import CapabilityDefinition, Availability
from tianshu.capabilities.contracts import canonical, digest
from tianshu.capabilities.serialization import context_from
from .contracts import ChannelError, TransportResult
from .repository import Repository
from .media import Media
from .delivery import ChannelDelivery, DeliveryHandler, ContactPreflightStopped, BehaviorPreflightPending
from .transports import OneBotTransport, OwnerClient
from .telegram import TelegramTransport, BOT_API_METHODS
from .onebot.registry import resolve_api
from .operations import Operation


class Channels:
    def __init__(
        self,
        runtime,
        settings,
        *,
        identity,
        sources,
        actions,
        catalog,
        artifacts,
        configuration,
        web=None,
    ):
        (
            self.runtime,
            self.settings,
            self.identity,
            self.sources,
            self.actions,
            self.catalog,
            self.configuration,
        ) = runtime, dict(settings), identity, sources, actions, catalog, configuration
        self.repository = Repository(runtime, identity, sources)
        self.repository.actions = actions
        self.media = Media(artifacts, sources, runtime.files)
        self.web, self.expression, self.conversation = web, None, None
        self.contact_preflight = None
        self.behavior_resolver = None
        self.recovery_policy = None
        self.statistics = None
        self.delivery = ChannelDelivery(self)
        from .native import ChannelNativeApi

        self.native = ChannelNativeApi(self)
        self.owner_id = str(uuid4())
        self.owner = False
        self.epoch = None
        self.transports = {}
        self.revisions = {}
        self.running = {}
        self.owner_task = None
        self.client = OwnerClient(settings, runtime.credentials)
        self.delivery_handler = DeliveryHandler(self)
        catalog.register(self.delivery_handler)
        for name in ("configure", "probe", "api", "read"):
            catalog.register(Operation(self, name))

    def bind_conversation(self, conversation):
        self.conversation = conversation

    def bind_expression(self, expression):
        self.expression = expression

    def bind_contact_preflight(self, callback):
        self.contact_preflight = callback

    def bind_behavior_resolver(self, resolver):
        self.behavior_resolver = resolver

    def bind_recovery_policy(self, policy):
        self.recovery_policy = policy
        self.repository.recovery_policy = policy

    def bind_statistics(self, statistics):
        self.statistics = statistics
        self.repository.statistics = statistics

    async def backfill_media_statistics(self, *, after_id="", limit=500):
        from .observability import backfill_media
        return await backfill_media(self, after_id=after_id, limit=limit)

    async def reconcile_media_statistics(self, after_id="", scope="media"):
        if self.statistics is None:
            return
        if scope == "media":
            result = await self.backfill_media_statistics(after_id=after_id)
        elif scope in {"ingress", "outbound"}:
            from .observability import backfill_ledger
            result = await backfill_ledger(self, scope=scope, after_id=after_id)
        else:
            raise ValueError("invalid_channel_statistics_scope")
        if not result["complete"]:
            from .observability import schedule_repair
            async with self.runtime.database.transaction() as conn:
                await schedule_repair(self, conn, after_id=result["next_cursor"], scope=scope)

    async def defer_delivery(self, action_id):
        from tianshu.runtime.execution import ExecutionLane
        async with self.runtime.database.transaction() as conn:
            await self.runtime.jobs.enqueue(conn, JobSpec("channels.resume_delivery", f"delivery-behavior:{action_id}:{uuid4()}",
                {"action_id": action_id}, ServiceClass.INTERACTIVE, datetime.now(UTC)+timedelta(seconds=10),
                execution_lane=ExecutionLane.CONVERSATION))

    async def resume_delivery(self, action_id):
        action, attempt, current = await self.actions.load_for_execution(action_id)
        if current.execution in {"succeeded", "failed", "cancelled", "unknown"}:
            return
        result = await self.delivery_handler.resume(action, attempt)
        await self.actions.save(result, attempt)

    def bind_web(self, web):
        self.web = web

    async def notify_operations(self, context, *, notification_id, target_ref, text, source_refs=()):
        """Submit one real operations notice through the existing QQ connection owner."""
        if (context.origin_kind != OriginKind.SYSTEM or not context.delivery_allowed
                or not context.actor_id or context.conversation_id != target_ref):
            raise ChannelError("operations_notification_context", 403)
        if (not isinstance(notification_id, str) or not 1 <= len(notification_id) <= 200
                or not isinstance(text, str) or not text.strip() or len(text) > 10000):
            raise ChannelError("operations_notification_invalid", 400)
        target = await self.identity.channel_target(context, target_ref)
        if target["adapter"] == "web" or not target.get("connection_id"):
            raise ChannelError("operations_qq_target_required", 409)
        connection = await self.repository.connection(target["connection_id"], target["context"])
        if connection["adapter"] not in {"onebot", "nonebot", "astrbot"}:
            raise ChannelError("operations_qq_target_required", 409)
        from tianshu.conversation.types import ExpressionPlan
        expression_id = "operations:" + digest(notification_id)
        plan = ExpressionPlan(expression_id, context.actor_id, target_ref,
            ({"kind": "text", "text": "【天枢运维】\n" + text.strip()},), tuple(source_refs), OriginKind.SYSTEM)
        return await self.delivery.deliver(context, plan)

    async def operations_notification_status(self, context, *, notification_id, reconcile=False):
        if context.origin_kind != OriginKind.SYSTEM or not isinstance(notification_id, str) or not notification_id:
            raise ChannelError("operations_notification_context", 403)
        expression_id = "operations:" + digest(notification_id)
        if reconcile:
            return await self.delivery.reconcile(context, expression_id)
        return await self.delivery.get(context, expression_id)

    def verify_target(self, target, params, connection_id):
        if target["connection_id"] != connection_id:
            raise ChannelError("channel_target_connection_mismatch", 403)
        actual = target["target_ref"]
        if (
            actual.startswith("group:")
            and params.get("group_id") is not None
            and actual != "group:" + str(params["group_id"])
        ):
            raise ChannelError("channel_target_mismatch", 403)
        if (
            actual.startswith("private:")
            and params.get("user_id") is not None
            and actual != "private:" + str(params["user_id"])
        ):
            raise ChannelError("channel_target_mismatch", 403)
        if params.get("chat_id") is not None and actual.split(":")[1] != str(params["chat_id"]):
            raise ChannelError("channel_target_mismatch", 403)

    async def status(self):
        async with self.runtime.database.transaction(control=True, read_only=True) as conn:
            owner = await (
                await conn.execute(
                    "SELECT owner_id,epoch,expires_at,expires_at>now() AS active FROM channels.owners WHERE id='transport'"
                )
            ).fetchone()
            rows = await (
                await conn.execute(
                    "SELECT id,coalesce((body->>'enabled')::boolean,false) AS enabled,coalesce((body->>'configured')::boolean,false) AS configured,body->>'transport' AS transport FROM channels.connections ORDER BY id"
                )
            ).fetchall()
        for connection in rows:
            transport = self.transports.get(connection["id"])
            connection["transport_bound"] = transport is not None
            connection["available"] = (
                False if not connection["enabled"] or not connection["configured"] else None
            )
            if transport is not None and connection["transport"] in {"ws", "reverse_ws"}:
                connection["available"] = any(
                    not socket.closed for socket in transport.sockets.values()
                )
            elif transport is not None and connection["transport"] in {
                "telegram_poll",
                "telegram_webhook",
            }:
                connection["available"] = bool(
                    transport.session
                    and not transport.session.closed
                    and (transport.task is None or not transport.task.done())
                )
        ready = self.expression is not None and self.conversation is not None
        if self.runtime.role == "interactive":
            ready = ready and bool(
                owner
                and owner["active"]
                and owner["owner_id"] == self.owner_id
                and self.owner_task
                and not self.owner_task.done()
            )
        return {"ready": ready, "owner": owner, "connections": rows, "role": self.runtime.role}

    async def start(self):
        if self.runtime.role == "interactive":
            await self.acquire_owner()
            await self.refresh_transports()
            self.owner_task = asyncio.create_task(self.maintain_owner(), name="channels-owner")
        from .supplements import recover
        await recover(self)
        if self.statistics is not None:
            from .observability import schedule_repair
            async with self.runtime.database.transaction() as conn:
                for scope in ("media", "ingress", "outbound"):
                    await schedule_repair(self, conn, scope=scope)

    async def acquire_owner(self):
        async with self.runtime.database.transaction(control=True) as conn:
            row = await (
                await conn.execute(
                    "INSERT INTO channels.owners(id,owner_id,epoch,expires_at) VALUES('transport',%s,1,now()+interval '20 seconds') ON CONFLICT(id) DO UPDATE SET owner_id=excluded.owner_id,epoch=channels.owners.epoch+1,expires_at=excluded.expires_at WHERE channels.owners.expires_at<now() OR channels.owners.owner_id=excluded.owner_id RETURNING epoch",
                    (self.owner_id,),
                )
            ).fetchone()
        if not row:
            raise ChannelError("channel_owner_already_active", 503)
        self.owner = True
        self.epoch = row["epoch"]
        # A previous socket owner may have submitted before it crashed. Keep every
        # original identity unknown; never replay the sealed request automatically.
        async with self.runtime.database.transaction(control=True) as conn:
            changed = await (await conn.execute(
                "UPDATE channels.calls SET state='unknown',result=jsonb_build_object('state','unknown','error_code','owner_restarted') WHERE state='sending' AND owner_id IS DISTINCT FROM %s RETURNING id,state,created_at",
                (self.owner_id,),
            )).fetchall()
            from .observability import record_ledger_on
            for row in changed:
                await record_ledger_on(self, conn, scope="outbound", row=row)

    async def maintain_owner(self):
        try:
            while self.owner:
                await asyncio.sleep(5)
                async with self.runtime.database.transaction(control=True) as conn:
                    row = await (
                        await conn.execute(
                            "UPDATE channels.owners SET expires_at=now()+interval '20 seconds' WHERE id='transport' AND owner_id=%s AND epoch=%s AND expires_at>now() RETURNING id",
                            (self.owner_id, self.epoch),
                        )
                    ).fetchone()
                if not row:
                    raise ChannelError("channel_owner_lease_lost", 503)
                await self.refresh_transports()
        except asyncio.CancelledError:
            raise
        finally:
            self.owner = False
            for transport in tuple(self.transports.values()):
                await transport.close()
            self.transports.clear()

    async def ensure_owner(self):
        if not self.owner:
            raise ChannelError("channel_owner_unavailable", 503)
        async with self.runtime.database.transaction(control=True, read_only=True) as conn:
            row = await (
                await conn.execute(
                    "SELECT 1 FROM channels.owners WHERE id='transport' AND owner_id=%s AND epoch=%s AND expires_at>now()",
                    (self.owner_id, self.epoch),
                )
            ).fetchone()
        if not row:
            raise ChannelError("channel_owner_lease_lost", 503)

    async def refresh_transports(self):
        async with self.runtime.database.transaction(control=True, read_only=True) as conn:
            rows = await (
                await conn.execute("SELECT body FROM channels.connections ORDER BY id")
            ).fetchall()
        wanted = set()
        for row in rows:
            connection = row["body"]
            identifier = connection["id"]
            if not connection.get("enabled") or not connection.get("configured"):
                continue
            wanted.add(identifier)
            if self.revisions.get(identifier) == connection["revision"]:
                continue
            old = self.transports.pop(identifier, None)
            if old:
                await old.close()
            transport = None
            try:
                token = await self.repository.token(connection)
                if connection["adapter"] == "telegram":
                    transport = TelegramTransport(
                        connection, token, self.repository, self.accept_event
                    )
                elif connection["transport"] == "bridge":
                    from .bridges.client import BridgeTransport

                    transport = BridgeTransport(connection, token)
                else:
                    transport = OneBotTransport(
                        connection,
                        token,
                        on_event=self.accept_event,
                        on_response=self.late_response,
                    )
                transport.artifacts = self.media.artifacts
                await transport.start()
                self.transports[identifier] = transport
                self.revisions[identifier] = connection["revision"]
            except asyncio.CancelledError:
                raise
            except Exception as error:
                if transport is not None:
                    await transport.close()
                self.revisions.pop(identifier, None)
                await self.repository.observe(
                    identifier,
                    "transport",
                    "configuration",
                    {
                        "state": "unavailable",
                        "error_code": getattr(error, "code", "transport_start_failed"),
                    },
                )
        for identifier in set(self.transports) - wanted:
            await self.transports.pop(identifier).close()
            self.revisions.pop(identifier, None)

    async def execute_call(self, identifier):
        if self.runtime.role == "interactive":
            return await self.perform_call(identifier)
        try:
            await self.client.execute(identifier)
        except ChannelError as error:
            if error.effect != "unknown":
                raise
        return self.repository.outcome(await self.repository.call(identifier))

    async def perform_call(self, identifier):
        await self.ensure_owner()
        row = await self.repository.call(identifier)
        if row["state"] != "prepared":
            return self.repository.outcome(row)
        action, attempt, _ = await self.actions.load_for_execution(row["action_id"])
        connection = await self.repository.connection(row["connection_id"], action.context)
        if not connection.get("enabled") or not connection.get("configured"):
            raise ChannelError("channel_not_configured", 503)
        context = action.context
        if action.capability_id == "channels.deliver":
            target = await self.identity.channel_target(context, context.conversation_id)
            context = target["context"]
            if not context.delivery_allowed or target["connection_id"] != connection["id"]:
                raise ChannelError("channel_delivery_not_allowed", 403)
        params = (
            self.repository.unseal(row["payload"], identifier + ":request")
            if row["payload"]
            else None
        )
        if params is None:
            raise ChannelError("source_revoked", 410)
        transport = self.transports.get(connection["id"])
        if transport is None or self.revisions.get(connection["id"]) != connection["revision"]:
            await self.refresh_transports()
            transport = self.transports.get(connection["id"])
        if transport is None:
            raise ChannelError("channel_transport_unavailable", 503)
        async with self.runtime.database.transaction(control=True) as conn:
            if row.get("deployment_generation") is not None:
                await self.runtime.jobs.assert_generation_on(conn, row["deployment_generation"])
            if not await self.sources.validate_persistence(conn, context, action.source_refs):
                raise ChannelError("source_revoked", 410)
            claimed = await (
                await conn.execute(
                    "UPDATE channels.calls SET state='sending',owner_id=%s WHERE id=%s AND state='prepared' RETURNING id,state,created_at",
                    (self.owner_id, identifier),
                )
            ).fetchone()
            from .observability import record_ledger_on
            await record_ledger_on(self, conn, scope="outbound", row=claimed)
        if not claimed:
            return self.repository.outcome(await self.repository.call(identifier))

        async def submitted():
            await self.ensure_owner()
            await self.sources.check(context, action.source_refs)
            active_connection = await self.repository.connection(connection["id"], context)
            if active_connection["revision"] != connection["revision"]:
                raise ChannelError("channel_configuration_changed", 409)
            if action.capability_id == "channels.deliver":
                current = await self.identity.channel_target(context, context.conversation_id)
                if not current["context"].delivery_allowed:
                    raise ChannelError("channel_delivery_not_allowed", 403)
                self.verify_target(current, params, connection["id"])
                await self.delivery_handler.behavior_preflight(action)
                decision = await self.delivery_handler.preflight(action, attempt, current_call_id=identifier) if not row["control"] else None
                if decision:
                    # This exception is deliberately outside transport I/O errors:
                    # no bytes have been submitted and the outcome is known.
                    raise ContactPreflightStopped(decision)
            async with self.runtime.database.transaction(control=True) as conn:
                if row.get("deployment_generation") is not None:
                    await self.runtime.jobs.assert_generation_on(conn, row["deployment_generation"])
                if not row["control"]:
                    await self.actions.submitted(attempt, identifier)
                await conn.execute(
                    "UPDATE channels.calls SET submitted_at=now() WHERE id=%s", (identifier,)
                )

        self.running[identifier] = asyncio.current_task()
        try:
            if connection["transport"] != "bridge" and self.effectful(connection, row["api_name"]):

                async def control_submitted():
                    await self.ensure_owner()

                identity_result = await transport.call(
                    identifier + ":identity",
                    "getMe" if connection["adapter"] == "telegram" else "get_login_info",
                    {},
                    control_submitted,
                )
                identity_result = self.checked_identity(connection, identity_result)
                if identity_result.state != "confirmed":
                    raise ChannelError(
                        identity_result.error_code or "channel_account_unverified", 503
                    )
            from .onebot.messages import mentions_all

            if connection["adapter"] != "telegram" and mentions_all(params):
                if not connection.get("allow_at_all") or not params.get("group_id"):
                    raise ChannelError("channel_at_all_not_configured", 409)

                # The standard permits @all but has no quota API. Query a real
                # provider extension only when declared; generic providers return
                # the actual send acknowledgement/rejection themselves.
                async def no_submit():
                    pass

                has_quota_api = connection.get(
                    "provider"
                ) == "napcat" or "get_group_at_all_remain" in connection.get("extension_apis", [])
                quota = None
                if has_quota_api and connection["transport"] == "bridge":
                    control_id = await self.repository.prepare_call(
                        action,
                        attempt,
                        connection["id"],
                        "get_group_at_all_remain",
                        {"group_id": params["group_id"]},
                        suffix="quota:" + identifier,
                        control=True,
                    )
                    await self.execute_call(control_id)
                    quota = self.repository.outcome(
                        await self.repository.call(control_id), include_data=True
                    )
                elif has_quota_api:
                    quota = await transport.call(
                        identifier + ":quota",
                        "get_group_at_all_remain",
                        {"group_id": params["group_id"]},
                        no_submit,
                    )
                if quota is not None and (
                    quota.state != "confirmed"
                    or not isinstance(quota.data, dict)
                    or not quota.data.get("can_at_all")
                    or quota.data.get("remain_at_all_count_for_uin", 0) <= 0
                ):
                    raise ChannelError("channel_at_all_unavailable", 409)
            call_options = {"context": context}
            if isinstance(transport, OneBotTransport):
                call_options["source_refs"] = action.source_refs
            result = await transport.call(identifier, row["api_name"], params, submitted, **call_options)
            current = await self.repository.call(identifier)
            if result.state == "unknown" and not current["submitted_at"]:
                result = replace(result, state="rejected")
            from .snowluma.catalog import MESSAGE_RECEIPT_APIS

            requires_message_id = (
                connection.get("provider") == "snowluma" and row["api_name"] in MESSAGE_RECEIPT_APIS
            )
            if (
                (requires_message_id or row["api_name"]
                in {
                    "send_private_msg",
                    "send_group_msg",
                    "send_msg",
                    "send_group_forward_msg",
                    "send_private_forward_msg",
                    "sendMessage",
                    "sendPhoto",
                    "sendAnimation",
                    "sendAudio",
                    "sendVoice",
                    "sendVideo",
                    "sendDocument",
                    "sendSticker",
                    "sendMediaGroup",
                })
                and result.state == "confirmed"
                and not result.message_ids
            ):
                result = TransportResult(
                    "unknown", result.data, result.retcode, "message_ack_missing", raw=result.raw
                )
        except asyncio.CancelledError:
            current = await self.repository.call(identifier)
            await asyncio.shield(
                self.repository.settle(
                    identifier,
                    TransportResult(
                        "unknown" if current["submitted_at"] else "rejected",
                        error_code="channel_call_interrupted",
                    ),
                )
            )
            raise
        except Exception as error:
            current = await self.repository.call(identifier)
            if isinstance(error, BehaviorPreflightPending) and not current["submitted_at"]:
                result = TransportResult("prepared", error_code=error.code)
                await self.defer_delivery(action.action_id)
            elif isinstance(error, ContactPreflightStopped) and not current["submitted_at"]:
                result = TransportResult("rejected", error_code=error.code,
                    raw={"contact_preflight": error.decision})
            else:
                result = TransportResult(
                    "unknown" if current["submitted_at"] else "rejected",
                    error_code=getattr(error, "code", "channel_call_failed"),
                )
        finally:
            self.running.pop(identifier, None)
        if row["api_name"] in {"get_login_info", "getMe"}:
            result = self.checked_identity(connection, result)
        await self.repository.settle(identifier, result)
        if connection["transport"] == "bridge" and result.state in {"confirmed", "rejected"}:
            try:
                await transport.acknowledge(identifier)
            except (ChannelError, asyncio.TimeoutError):
                pass
        await self.repository.observe(
            connection["id"],
            "api",
            row["api_name"],
            {
                "state": result.state,
                "retcode": result.retcode,
                "verified": result.state == "confirmed",
                "connection_revision": connection["revision"],
            },
        )
        return result

    @staticmethod
    def effectful(connection, name):
        if connection["adapter"] == "telegram":
            return not name.startswith("get")
        base = name[:-6] if name.endswith("_async") else name
        spec = resolve_api(base, connection)
        if spec is None:
            raise ChannelError("channel_api_not_implemented", 400)
        return spec.effect != "read"

    @staticmethod
    def checked_identity(connection, result):
        if result.state != "confirmed":
            return result
        data = result.data if isinstance(result.data, dict) else {}
        actual = data.get("id") if connection["adapter"] == "telegram" else data.get("user_id")
        if actual is None or str(actual) != connection["self_external_id"]:
            return TransportResult("rejected", error_code="channel_account_mismatch")
        return result

    async def late_response(self, connection_id, identifier, result):
        try:
            row = await self.repository.call(identifier)
        except ChannelError:
            return
        if row["connection_id"] != connection_id:
            return
        if row["api_name"] in {"get_login_info", "getMe"}:
            connection = await self.repository.connection(connection_id)
            result = self.checked_identity(connection, result)
        await self.repository.settle(identifier, result)
        if row["control"]:
            return
        action, attempt, _ = await self.actions.load_for_execution(row["action_id"])
        if attempt.attempt_id != row["attempt_id"]:
            return
        handler = self.catalog.handler(action.capability_id)
        receipt = await handler.reconcile(action, attempt)
        await self.actions.save(receipt, attempt)

    async def reconcile_call(self, identifier):
        row = await self.repository.call(identifier)
        if row["state"] in {"confirmed", "rejected", "prepared"}:
            return self.repository.outcome(row)
        if self.runtime.role != "interactive":
            try:
                await self.client.reconcile(identifier)
            except ChannelError:
                pass
            return self.repository.outcome(await self.repository.call(identifier))
        await self.ensure_owner()
        connection = await self.repository.connection(row["connection_id"])
        if connection["transport"] == "bridge":
            transport = self.transports.get(connection["id"])
            temporary = transport is None
            if temporary:
                from .bridges.client import BridgeTransport

                transport = BridgeTransport(connection, await self.repository.token(connection))
                await transport.start()
            try:
                result = await transport.status(identifier)
                await self.repository.settle(identifier, result)
            finally:
                if temporary:
                    await transport.close()
        return self.repository.outcome(await self.repository.call(identifier))

    async def cancel_call(self, identifier):
        if self.runtime.role != "interactive":
            await self.client.cancel(identifier)
            return
        task = self.running.get(identifier)
        if task and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        row = await self.repository.call(identifier)
        if row["api_name"] == "web.publish":
            return
        connection = await self.repository.connection(row["connection_id"])
        if connection["transport"] == "bridge":
            transport = self.transports.get(connection["id"])
            temporary = transport is None
            if temporary:
                from .bridges.client import BridgeTransport

                transport = BridgeTransport(connection, await self.repository.token(connection))
                await transport.start()
            try:
                result = await transport.cancel(identifier)
                await self.repository.settle(identifier, result)
            finally:
                if temporary:
                    await transport.close()

    async def accept_event(self, connection_id, raw, *, ownership=None):
        from .ingress import accept_event

        return await accept_event(self, connection_id, raw, ownership=ownership)

    def register_tasks(self):
        from .ingress import process_event
        from .supplements import retry, expire
        from tianshu.runtime.execution import ExecutionLane

        async def ingest(**payload):
            return await process_event(self, **payload)

        # The original event key, admission, input and read receipts are stable
        # across a dependency wait; no outbound send is replayed here.
        self.runtime.jobs.register("channels.ingest", ingest, resource_recovery=True)

        async def media_supplement(supplement_id):
            await retry(self, supplement_id)

        async def media_supplement_expire(supplement_id, attempt):
            await expire(self, supplement_id, attempt)

        self.runtime.jobs.register("channels.media_supplement", media_supplement,
                                   execution_lane=ExecutionLane.CONVERSATION)
        self.runtime.jobs.register("channels.media_supplement_expire", media_supplement_expire,
                                   execution_lane=ExecutionLane.CONVERSATION)
        self.runtime.jobs.register("channels.resume_delivery", self.resume_delivery,
                                   execution_lane=ExecutionLane.CONVERSATION)
        self.runtime.jobs.register("channels.statistics_reconcile", self.reconcile_media_statistics,
                                   retryable=True, execution_lane=ExecutionLane.CONTROL)

        async def cancel(call_id):
            await self.cancel_call(call_id)

        self.runtime.jobs.register("channels.cancel", cancel)

    async def withdraw_sources(self, connection, refs, reason="source_revoked"):
        return await self.repository.withdraw_sources(connection, refs, reason)

    async def actors_for_source(self, connection, refs, kind):
        return await self.repository.actors_for_source(connection, refs, kind)

    async def invalidate_actor(self, connection, refs, kind, *, actor_id):
        return await self.repository.withdraw_sources(connection, refs, kind, actor_id=actor_id, filter_actor=True)

    async def close(self):
        self.owner = False
        tasks = {task for task in self.running.values() if task is not asyncio.current_task()}
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self.owner_task:
            self.owner_task.cancel()
            await asyncio.gather(self.owner_task, return_exceptions=True)
        for transport in tuple(self.transports.values()):
            await transport.close()
        self.transports.clear()
        async with self.runtime.database.transaction(control=True) as conn:
            await conn.execute(
                "DELETE FROM channels.owners WHERE id='transport' AND owner_id=%s", (self.owner_id,)
            )
