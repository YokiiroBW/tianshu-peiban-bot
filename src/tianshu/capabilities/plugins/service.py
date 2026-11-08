"""Native plugins use the same executable definitions, action IDs and authority as builtins."""

import asyncio
import json
from dataclasses import replace
from uuid import uuid4
from tianshu.types import Receipt
from tianshu.runtime.serialization import wire
from ..catalog import CapabilityDefinition, Availability
from ..contracts import Fault, canonical, digest
from ..execution_context import CURRENT_ACTION
from ..external import execution_service_class
from .actor_bridge import PluginActorBridge, binding_visible


class Authorization:
    def __init__(self, authority, context, sources, bridge, binding):
        self.authority, self.context, self.sources, self.bridge, self.binding = (
            authority,
            context,
            sources,
            bridge,
            binding,
        )

    async def __call__(self):
        await self.authority.check(self.context, self.sources)
        resolved = await self.bridge.host.local(
            self.bridge.resolve, self.context, self.binding
        )
        await self.bridge.host.local(self.bridge.ready, *resolved)
        return True

    async def authorize_action(self, instance, action, binding):
        if instance != self.binding["instance_id"] or binding != self.binding["binding_id"]:
            raise Fault("forbidden", 403)
        resolved = await self.bridge.host.local(
            self.bridge.resolve, self.context, {**self.binding, "action_id": action}
        )
        await self.bridge.host.local(self.bridge.ready, *resolved)
        return True


class PluginHandler:
    requires_worker = True

    def __init__(self, service, definition, binding, scope):
        self.service, self.definition, self.binding, self.scope = service, definition, binding, scope

    def visible(self, context):
        return binding_visible(context, {"scope": self.scope})

    async def availability(self, context):
        try:
            resolved = await self.service.host.local(
                self.service.bridge.resolve, context, self.binding
            )
            await self.service.host.local(self.service.bridge.ready, *resolved)
            if "http.client" in {
                x["id"]
                for x in resolved[2]["manifest"]["capabilities"]["required"]
                + resolved[3]["required_capabilities"]
            } and not self.service.host.resource_profiles.get(self.binding["instance_id"], {}).get(
                "quota_group"
            ):
                return Availability(True, False, reason="plugin_resource_not_configured")
            return Availability(True, True)
        except Fault as error:
            return Availability(True, False, reason=error.code)

    async def execute(self, action, attempt):
        host = self.service.host
        authorize = Authorization(
            self.service.authority,
            action.context,
            action.source_refs,
            self.service.bridge,
            self.binding,
        )
        token = CURRENT_ACTION.set(action)
        try:
            arguments = (
                await self.service.configuration.command(action.context, action.arguments)
                if "command_ref" in action.arguments
                else dict(action.arguments)
            )
            value = await host.executions.invoke(
                self.binding["instance_id"],
                self.binding["action_id"],
                arguments,
                action.intent_key,
                action.context.principal_id,
                authorize=authorize,
                binding_id=self.binding["binding_id"],
            )
            task = host.executions.running.get(value["id"])
            if task:
                await task
            value = await host.local(host.repository.call, value["id"])
        finally:
            CURRENT_ACTION.reset(token)
        return self.receipt(action, attempt, value)

    def receipt(self, action, attempt, value):
        state = {
            "accepted": "pending",
            "running": "running",
            "succeeded": "succeeded",
            "interrupted": "unknown",
        }.get(value["execution"], value["execution"])
        effect = {"applied": "confirmed", "not_applied": "none"}.get(
            value["effect"], value["effect"]
        )
        if effect == "unknown" and state in {"failed", "cancelled"}:
            state = "unknown"
        return Receipt(
            str(uuid4()),
            action.action_id,
            attempt.attempt_id,
            state,
            effect,
            value.get("sdk_delivery", "not_requested"),
            result={
                "explicit_expression_ids": value.get("explicit_expression_ids", []),
                "plugin_result": value.get("result"),
                "presentation": value.get("presentation"),
                "external": value.get("external"),
                "progress": value.get("progress"),
            },
            error_code=(value.get("error") or {}).get("code"),
        )

    async def reconcile(self, action, attempt):
        authorize = Authorization(
            self.service.authority,
            action.context,
            action.source_refs,
            self.service.bridge,
            self.binding,
        )
        if action.action_id in self.service.host.executions.running:
            return self.receipt(
                action,
                attempt,
                await self.service.host.local(self.service.host.repository.call, action.action_id),
            )
        await self.service.host.executions.recover(
            action.action_id, action.context.principal_id, authorize
        )
        task = self.service.host.executions.running.get(action.action_id)
        if task:
            await task
        value = await self.service.host.local(self.service.host.repository.call, action.action_id)
        return self.receipt(action, attempt, value)

    async def resume(self, action, attempt):
        authorize = Authorization(
            self.service.authority,
            action.context,
            action.source_refs,
            self.service.bridge,
            self.binding,
        )
        await self.service.host.executions.recover(
            action.action_id, action.context.principal_id, authorize, resume=True
        )
        task = self.service.host.executions.running.get(action.action_id)
        if task:
            await task
        return self.receipt(
            action,
            attempt,
            await self.service.host.local(self.service.host.repository.call, action.action_id),
        )

    async def cancel(self, action, attempt):
        await self.service.host.executions.cancel(action.action_id)
        value = await self.service.host.local(self.service.host.repository.call, action.action_id)
        return self.receipt(action, attempt, value)


class Plugins:
    def __init__(
        self, host, catalog, authority, models, artifacts, sources, expression, jobs, database
    ):
        self.host, self.catalog, self.authority = host, catalog, authority
        self.models, self.artifacts, self.sources, self.expression = (
            models,
            artifacts,
            sources,
            expression,
        )
        self.jobs, self.database = jobs, database
        self.channels = None
        self.bridge = PluginActorBridge(host)
        host.domain_ports = self

    async def refresh(self, context):
        await self.authority.check(context)
        rows = await self.bridge.discover(context)
        visible = set()
        for row in rows:
            binding = row["binding"]
            identifier = (
                "plugin:"
                + binding["instance_id"]
                + ":"
                + binding["binding_id"]
                + ":"
                + binding["action_id"]
            )
            definition = CapabilityDefinition(
                identifier,
                row["title"],
                row["description"],
                row["input_schema"],
                row["output_schema"],
                row["effect"],
                public_command=row.get("public_command"),
                model_tool={"name": "plugin_" + digest(binding)[:24]}
                if row.get("model_tool")
                else None,
                long_running=row["long_running"],
            )
            # Instance revisions replace the same definition; no duplicate parallel handler is installed.
            handler = self.catalog.handlers.get(identifier)
            if not (isinstance(handler, PluginHandler) and handler.definition == definition
                    and handler.binding == binding and handler.scope == row["scope"]):
                handler = PluginHandler(self, definition, binding, row["scope"])
                self.catalog.handlers[identifier] = handler
            visible.add(identifier)
        for identifier, handler in tuple(self.catalog.handlers.items()):
            if (
                isinstance(handler, PluginHandler)
                and handler.visible(context)
                and identifier not in visible
            ):
                del self.catalog.handlers[identifier]

    def revoke(self, identifier):
        """Withdraw live catalog handlers so no route can still call this instance."""
        prefixes = ("plugin:" + identifier + ":", "plugin-event:" + identifier + ":")
        for key in tuple(self.catalog.handlers):
            if key.startswith(prefixes):
                self.catalog.handlers.pop(key, None)

    async def discover(self, context):
        return [
            item
            for item in await self.catalog.discover(context)
            if item["definition"].id.startswith("plugin:")
        ]

    async def contributions(self, context):
        await self.discover(context)
        commands = []
        tools = []
        for instance in await self.host.local(self.host.repository.list, "instances"):
            package = await self.host.package(instance["package_id"], instance["version"])
            for binding in instance["bindings"]:
                if not binding["enabled"] or not binding_visible(context, binding):
                    continue
                allowed = {
                    item["id"] for item in binding.get("entries", []) if item["kind"] == "action"
                }
                for kind, target in (("commands", commands), ("tools", tools)):
                    for contribution in package["description"].get(kind, []):
                        if contribution["action"] in allowed:
                            target.append(
                                {
                                    **contribution,
                                    "capability_id": "plugin:"
                                    + instance["id"]
                                    + ":"
                                    + binding["binding_id"]
                                    + ":"
                                    + contribution["action"],
                                }
                            )
        return {"commands": commands, "tools": tools}

    async def host_port(self, port_context, name, operation, value):
        from dataclasses import replace
        from datetime import UTC, datetime, timedelta
        from tianshu.types import (
            Action,
            MediaRef,
            SourceRef,
            RequestBudget,
            OriginKind,
        )
        from tianshu.models.types import ModelRequest, ModelMessage, ModelPart
        from ..serialization import artifact_from
        from ..contracts import digest
        from ..execution_context import CURRENT_EXECUTION

        call = port_context.get("call")
        if not call:
            raise Fault("invocation_context_required", 403)
        action, attempt, _ = await self.actions.load_for_execution(call)
        await self.authority.check(action.context, action.source_refs)
        generation = await self.actions.saved_generation(action, attempt)
        behavior_refs, behavior_purpose = await self.actions.saved_behaviors(action, attempt)
        await self.actions.check_behaviors(action.context, behavior_refs, purpose=behavior_purpose)
        action_token = CURRENT_ACTION.set(action)
        tracker = CURRENT_EXECUTION.set(
            {
                "owner": self.actions,
                "attempt": attempt,
                "submitted": False,
                "data_generation": generation,
                "behavior_refs": behavior_refs,
                "behavior_purpose": behavior_purpose,
                "service_class": await self.actions.execution_class(call),
            }
        )
        try:
            if name == "channels.native":
                if self.channels is None:
                    raise Fault("channel_port_unavailable", 503)
                if operation == "describe":
                    return await self.channels.describe(action.context, value["connection_id"],
                        api_name=value.get("api_name"), category=value.get("category"))
                if operation == "get":
                    return wire(await self.channels.get(action.context, value["action_id"]))
                result = await self.channels.invoke(
                    action.context, value["connection_id"], value["api_name"], value["params"],
                    request_id="plugin:" + digest([action.action_id, value["request_id"]]),
                    source_refs=action.source_refs, origin_kind=action.origin_kind,
                    behavior_refs=behavior_refs, behavior_purpose=behavior_purpose,
                )
                if result.result.get("expression_id"):
                    document = await self.host.local(self.host.repository.call, call)
                    await self.host.local(self.host.repository.update_call, call,
                        explicit_expression_ids=list(dict.fromkeys([
                            *document.get("explicit_expression_ids", []), result.result["expression_id"]])),
                        sdk_delivery=result.delivery)
                return wire(result)
            if name == "models.generate" and operation == "generate":
                messages = tuple(
                    ModelMessage(
                        x["role"],
                        tuple(
                            ModelPart(
                                part["kind"],
                                text=part.get("text"),
                                media=MediaRef(**part["media"]) if part.get("media") else None,
                            )
                            for part in x["parts"]
                        ),
                    )
                    for x in value["messages"]
                )
                result = await self.models.generate(
                    ModelRequest(
                        intent_id=action.action_id + ":" + value["request_key"],
                        attempt_id=attempt.attempt_id + ":" + value["request_key"],
                        function=value["function"],
                        service_class=execution_service_class(action),
                        context=action.context,
                        budget=RequestBudget(
                            action.deadline_at or datetime.now(UTC) + timedelta(minutes=3)
                        ),
                        execution_timeout_seconds=180, business_deadline_at=action.deadline_at,
                        messages=messages,
                        system=value.get("system", ""),
                        required_capabilities=frozenset(
                            value.get("required_capabilities", ["text"])
                        ),
                        max_output_tokens=min(value.get("max_output_tokens", 2048), 8192),
                        source_refs=action.source_refs,
                    )
                )
                return wire(result)
            if name == "files.store":
                import base64

                if operation == "put":
                    raw = base64.b64decode(value["data_base64"], validate=True)
                    if len(raw) > 512 * 1024:
                        raise Fault("plugin_frame_budget", 413)
                    return wire(
                        await self.artifacts.put(
                            action.context,
                            raw,
                            value["media_type"],
                            source_refs=action.source_refs,
                            metadata={"plugin_instance_id": port_context["instance"]},
                        )
                    )
                raw, mime = await self.artifacts.resolve(action.context, MediaRef(**value["media"]))
                if len(raw) > 512 * 1024:
                    raise Fault("plugin_frame_budget", 413)
                return {"data_base64": base64.b64encode(raw).decode(), "media_type": mime}
            if name == "sources.read" and operation == "read":
                return await self.sources.read(
                    action.context, SourceRef(**value["source"]), value.get("requested", {})
                )
            if name == "messages.present" and operation == "present":
                if self.expression is None:
                    raise Fault("expression_port_unavailable", 503)
                artifacts = tuple(artifact_from(x) for x in value.get("artifacts", []))
                for item in artifacts:
                    await self.artifacts.resolve(action.context, item.media)
                from tianshu.conversation.types import ExpressionPlan

                if not action.context.conversation_id or not action.actor_id:
                    raise Fault("expression_target_required", 409)
                plan = ExpressionPlan(
                    action.expression_id or action.action_id + ":" + value["expression_key"],
                    action.actor_id,
                    action.context.conversation_id,
                    tuple(value["public_parts"]),
                    action.source_refs,
                    action.origin_kind,
                    artifacts=artifacts,
                )
                await self.actions.check_behaviors(action.context, behavior_refs, purpose=behavior_purpose)
                if behavior_refs:
                    # The delivery owner must persist/recheck dependencies at
                    # the eventual send, even if the plugin callback has ended.
                    if "behavior_refs" not in ExpressionPlan.__dataclass_fields__:
                        raise Fault("behavior_expression_port_unavailable", 503)
                    plan = replace(plan, behavior_refs=behavior_refs)
                response = await self.expression.send(action.context, plan)
                document = await self.host.local(self.host.repository.call, call)
                await self.host.local(
                    self.host.repository.update_call,
                    call,
                    explicit_expression_ids=list(
                        dict.fromkeys(
                            [*document.get("explicit_expression_ids", []), plan.expression_id]
                        )
                    ),
                    sdk_delivery=response.delivery,
                )
                return wire(response)
            if name == "jobs.manage" and operation == "submit":
                document = await self.host.local(self.host.repository.call, call)
                identifier = (
                    "plugin:"
                    + port_context["instance"]
                    + ":"
                    + document["binding_id"]
                    + ":"
                    + value["action"]
                )
                await self.discover(action.context)
                key = digest([action.action_id, identifier, value["intent_key"]])
                receipt = await self.actions.invoke(
                    Action(
                        key,
                        value["intent_key"],
                        identifier,
                        action.actor_id,
                        action.context,
                        value["input"],
                        OriginKind.TOOL_RESULT,
                        source_refs=action.source_refs,
                        matter_id=action.matter_id,
                    ),
                    background=True,
                    behavior_refs=behavior_refs,
                    behavior_purpose=behavior_purpose,
                )
                return {"invocation_id": receipt.action_id}
            raise Fault("capability_unavailable", 409)
        finally:
            CURRENT_ACTION.reset(action_token)
            CURRENT_EXECUTION.reset(tracker)


class PluginEventHandler:
    def __init__(self, service, instance, binding, event):
        self.service, self.instance, self.binding, self.event = service, instance, binding, event
        self.definition = CapabilityDefinition(
            "plugin-event:" + instance["id"] + ":" + binding["binding_id"] + ":" + event["id"],
            "插件事件：" + event["id"],
            event["event_type"],
            {"type": "object"},
            {"type": "object"},
        )

    async def availability(self, context):
        current = await self.service.host.instance(self.instance["id"])
        binding = next((item for item in current["bindings"]
                        if item["binding_id"] == self.binding["binding_id"]), None)
        enabled = (
            current["desired_state"] == "enabled"
            and current["revision"] == self.instance["revision"]
            and binding is not None and binding["enabled"] and binding_visible(context, binding)
            and {"kind": "event", "id": self.event["id"]} in binding.get("entries", [])
        )
        return Availability(True, enabled, reason=None if enabled else "plugin_binding_changed")

    async def execute(self, action, attempt):
        host = self.service.host
        await self.service.authority.check(action.context, action.source_refs)
        current = await host.instance(self.instance["id"])
        if (
            current["revision"] != self.instance["revision"]
            or current["desired_state"] != "enabled"
            or not (await self.availability(action.context)).can_execute
        ):
            raise Fault("plugin_binding_changed", 409)
        token = CURRENT_ACTION.set(action)
        try:
            document = {
                "id": action.action_id,
                "instance_id": current["id"],
                "intent_key": action.intent_key,
                "principal": action.context.principal_id,
                "execution": "running",
                "effect": "none",
                "delivery": "not_requested",
                "revision": 1,
                "binding_revision": current["revision"],
                "binding_id": self.binding["binding_id"],
                "config": current["config"],
                "input": dict(action.arguments),
                "snapshot": current,
            }
            async with self.service.database.transaction() as connection:
                row = await (await connection.execute(
                    "SELECT document,attempt FROM capabilities.actions WHERE id=%s FOR UPDATE",
                    (action.action_id,))).fetchone()
                if row is None or row["attempt"]["attempt_id"] != attempt.attempt_id:
                    raise Fault("attempt_changed", 409)
                previous = json.loads(row["document"])
                if previous.get("source_revoked") or previous.get("cancel_requested"):
                    raise Fault("source_revoked", 410)
                await connection.execute(
                    "UPDATE capabilities.actions SET document=%s,instance_id=%s WHERE id=%s",
                    (canonical({**previous, **document}), current["id"], action.action_id),
                )
            process = await host.worker(current)
            # Deliver this event handle to the child that will handle it: one
            # process generation, so replacing that child also retires the handle.
            envelope = await host.executions.context(
                current, action.action_id, generation=process.runtime_id
            )
            await self.service.authority.check(action.context, action.source_refs)
            from ..tracking import mark_submitted
            await mark_submitted(action.action_id)
            result = await process.call(
                "plugin.event",
                {
                    "call": envelope,
                    "subscription_id": self.event["id"],
                    "event": dict(action.arguments),
                },
                timeout=60,
            )
        finally:
            CURRENT_ACTION.reset(token)
        return Receipt(
            str(uuid4()),
            action.action_id,
            attempt.attempt_id,
            "succeeded",
            "none",
            "not_requested",
            result={
                "event_id": action.arguments["event_id"],
                "plugin_result": result.get("result"),
            },
        )

    async def reconcile(self, action, attempt):
        return Receipt(
            str(uuid4()),
            action.action_id,
            attempt.attempt_id,
            "unknown",
            "unknown",
            "not_requested",
            error_code="plugin_event_result_unknown",
        )

    async def cancel(self, action, attempt):
        return await self.reconcile(action, attempt)


async def consume_event(service, connection, event, context):
    """Called by the durable root event consumer with the real audience context."""
    from tianshu.types import Action, OriginKind
    from tianshu.runtime.jobs import JobSpec
    from ..contracts import digest
    from ..execution_context import current_generation

    await service.authority.check(context)
    generation = current_generation() or await service.jobs.current_generation_on(connection)
    await service.jobs.assert_generation_on(connection, generation)
    for instance in await service.host.local(service.host.repository.list, "instances"):
        if instance["desired_state"] != "enabled":
            continue
        package = await service.host.package(instance["package_id"], instance["version"])
        for binding in instance["bindings"]:
            if not binding["enabled"] or not binding_visible(context, binding):
                continue
            allowed = {
                entry["id"] for entry in binding.get("entries", []) if entry["kind"] == "event"
            }
            for subscription in package["description"].get("events", []):
                if (
                    subscription["id"] not in allowed
                    or subscription["event_type"] != event["type"]
                    or subscription["event_version"] != event.get("version", 1)
                ):
                    continue
                handler = PluginEventHandler(service, instance, binding, subscription)
                service.catalog.handlers[handler.definition.id] = handler
                key = "plugin-event:" + digest(
                    [instance["id"], binding["binding_id"], subscription["id"], event["event_id"]]
                )
                cursor = await connection.execute(
                    "INSERT INTO capabilities.plugin_consumptions(id,instance_id,event_id,context,event) VALUES(%s,%s,%s,%s::jsonb,%s::jsonb) ON CONFLICT DO NOTHING RETURNING id",
                    (
                        key,
                        instance["id"],
                        event["event_id"],
                        canonical(wire(context)),
                        canonical(event),
                    ),
                )
                from ..withdrawal import depend
                from tianshu.types import SourceRef

                await depend(
                    connection,
                    "plugin_consumption",
                    key,
                    tuple(SourceRef(**ref) for ref in event.get("source_refs", [])),
                )
                if await cursor.fetchone():
                    await service.jobs.enqueue(
                        connection,
                        JobSpec("capabilities.plugin_event", key, {"consumption_id": key},
                                data_generation=generation),
                    )


async def process_event(service, consumption_id):
    from tianshu.types import Action, TrustedContext, OriginKind, SourceRef
    from ..contracts import digest

    async with service.database.transaction() as connection:
        row = await (
            await connection.execute(
                "SELECT * FROM capabilities.plugin_consumptions WHERE id=%s", (consumption_id,)
            )
        ).fetchone()
    if not row:
        return
    context = dict(row["context"])
    context["permissions"] = frozenset(context["permissions"])
    context["scope_ids"] = tuple(context["scope_ids"])
    context["audience"] = tuple(context["audience"])
    context["origin_kind"] = OriginKind.TOOL_RESULT
    context = TrustedContext(**context)
    # Recreate only actual enabled declarations after worker restart.
    instance = await service.host.instance(row["instance_id"])
    package = await service.host.package(instance["package_id"], instance["version"])
    for binding in instance["bindings"]:
        for subscription in package["description"].get("events", []):
            key = "plugin-event:" + digest(
                [instance["id"], binding["binding_id"], subscription["id"], row["event_id"]]
            )
            if key == consumption_id:
                handler = PluginEventHandler(service, instance, binding, subscription)
                service.catalog.handlers[handler.definition.id] = handler
                action = Action(
                    key,
                    key,
                    handler.definition.id,
                    context.actor_id,
                    context,
                    row["event"],
                    OriginKind.TOOL_RESULT,
                    source_refs=tuple(SourceRef(**x) for x in row["event"].get("source_refs", [])),
                )
                await service.actions.invoke(action, background=False)
                return
