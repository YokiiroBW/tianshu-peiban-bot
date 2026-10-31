"""One durable input lane for browser messages, channels and declared commands."""
from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid4, uuid5

from tianshu.models.types import ModelMessage, ModelPart, ModelRequest, ToolSpec
from tianshu.runtime.api import CommandResult, Page
from tianshu.runtime.errors import AppError
from tianshu.runtime.jobs import JobSpec
from tianshu.runtime.serialization import wire
from tianshu.types import Action, ContextContribution, Coverage, OriginKind, RequestBudget, ServiceClass
from .codec import (artifacts, context_from_record, document, input_parts, model_parts, receipt,
                    signature, source_refs)
from .types import DialogueResult, ExpressionPlan, InputMessage


class ConversationService:
    def __init__(self, database, commands, jobs, identity, sources, memory, companion,
                 life, growth, assembler, models, catalog, actions, expression, contacts):
        self.database, self.commands, self.jobs, self.identity = database, commands, jobs, identity
        self.sources, self.memory, self.companion = sources, memory, companion
        self.life, self.growth, self.assembler, self.models = life, growth, assembler, models
        self.catalog, self.actions, self.expression, self.contacts = catalog, actions, expression, contacts

    async def ingest(self, context, message: InputMessage, *, connection=None):
        await self.identity.check(context, message.source_refs)
        if not context.actor_id or not context.conversation_id:
            raise AppError("conversation_required")
        if message.conversation_kind != context.conversation_kind:
            raise AppError("conversation_kind_mismatch", status=403)
        self._validate_parts(message.parts)
        actor = await self.companion.actor(context)
        turn_id = str(uuid5(NAMESPACE_URL, f"turn:{context.actor_id}:{context.conversation_id}:{message.producer}:{message.event_id}:{message.revision}"))
        async with self.database.transaction(read_only=True) as probe:
            previous = await (await probe.execute("SELECT input_parts,source_refs,occurred_at FROM conversation.turns WHERE turn_id=%s", (turn_id,))).fetchone()
        if previous:
            if signature(previous["input_parts"]) != signature(message.parts):
                raise AppError("event_revision_conflict", status=409)
            refs = source_refs(previous["source_refs"])
            message = replace(message, occurred_at=previous["occurred_at"])
        else:
            refs = await self.sources.record_event(context, producer=message.producer, event_id=message.event_id,
                revision=message.revision, occurred_at=message.occurred_at, parts=message.parts, origin_kind=context.origin_kind)
        # The channel has already resolved an external reply owner; observing never steals it.
        responding = message.reply_required and message.reply_owner == "tianshu" and actor["enabled"]
        async def persist(connection):
            await connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (f"turn:{context.actor_id}:{context.conversation_id}",))
            old = await (await connection.execute("SELECT turn_id,state,occurred_at FROM conversation.turns WHERE turn_id=%s", (turn_id,))).fetchone()
            if old:
                state = old["state"]
            if not old:
                pending = await (await connection.execute("SELECT count(*) AS count FROM conversation.turns WHERE actor_id=%s AND conversation_id=%s AND state IN ('queued','running')", (context.actor_id, context.conversation_id))).fetchone()
                if responding and pending["count"] >= 2:
                    raise AppError("conversation_busy", "前一条消息仍在处理，请稍后继续", status=429)
                state = "queued" if responding else "observed"
                await connection.execute(
                    "INSERT INTO conversation.turns(turn_id,actor_id,conversation_id,producer,event_id,event_revision,context,input_parts,source_refs,state,reply_owner,reply_required,command,occurred_at,received_at,deadline_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (turn_id, context.actor_id, context.conversation_id, message.producer, message.event_id, message.revision,
                     document(context), document(message.parts), document(refs), state, message.reply_owner, message.reply_required,
                     message.command, message.occurred_at, message.received_at, message.received_at+timedelta(minutes=15)))
                await connection.execute("INSERT INTO conversation.messages(turn_id,actor_id,conversation_id,speaker,parts,source_refs,scope_ids) VALUES (%s,%s,%s,'user',%s,%s,%s)", (turn_id, context.actor_id, context.conversation_id, document(message.parts), document(refs), list(context.scope_ids)))
                if responding:
                    await self.jobs.enqueue(connection, JobSpec("conversation.run", "turn:"+turn_id+":0", {"turn_id": turn_id}, subject_id=context.actor_id))
            return {"turn_id": turn_id, "state": state}
        if connection is not None:
            return await persist(connection)
        async with self.database.transaction() as transaction:
            result = await persist(transaction)
        await self._observe_turn(context, turn_id)
        return result

    async def _observe_turn(self, context, turn_id):
        async with self.database.transaction(read_only=True) as connection:
            row = await (await connection.execute("SELECT * FROM conversation.turns WHERE turn_id=%s AND actor_id=%s AND conversation_id=%s", (turn_id, context.actor_id, context.conversation_id))).fetchone()
        if not row:
            raise AppError("turn_not_found", status=404)
        parts, refs = input_parts(row["input_parts"]), source_refs(row["source_refs"])
        await self.identity.check(context, refs)
        await self.contacts.received(context, row["occurred_at"], input_id=turn_id)
        await self.memory.observe_episode(context, {"episode_id": "input:"+turn_id, "event_id": row["event_id"],
            "revision": row["event_revision"], "origin_kind": context.origin_kind, "actor_id": context.actor_id,
            "subject_id": context.person_id, "occurred_at": row["occurred_at"],
            "summary": "\n".join(part.text for part in parts if part.text)[:4000], "source_refs": refs,
            "actual_result": {"reply_owner": row["reply_owner"], "representation": "source_excerpt",
                              "modalities": [part.kind for part in parts]}})
        await self.growth.changed(context, f"input:{turn_id}:{row['event_revision']}")

    async def send(self, context, body):
        await self.identity.check(context)
        parts = input_parts(body.get("parts", ()))
        now = datetime.now(UTC)
        message = InputMessage("web:"+context.principal_id, body["client_request_id"], 1, now, now,
                               parts, conversation_kind=context.conversation_kind)
        turn_id = str(uuid5(NAMESPACE_URL, f"turn:{context.actor_id}:{context.conversation_id}:{message.producer}:{message.event_id}:1"))
        async def apply(connection):
            result = await self.ingest(context, message, connection=connection)
            return CommandResult(body["client_request_id"], result, 1)
        async def replay(_connection, original):
            return replace(original, data=await self.turn(context, turn_id))
        result = await self.commands.execute(context, "conversation.send", body["client_request_id"], signature(body), apply, on_replay=replay)
        await self._observe_turn(context, turn_id)
        return result

    async def messages(self, context, *, cursor=None, limit=50):
        await self.identity.check(context)
        limit = min(100, max(1, int(limit)))
        async with self.database.transaction(read_only=True) as connection:
            rows = await (await connection.execute("SELECT * FROM conversation.messages WHERE actor_id=%s AND conversation_id=%s AND scope_ids<@%s::text[] AND (%s::bigint IS NULL OR id<%s) ORDER BY id DESC LIMIT %s", (context.actor_id, context.conversation_id, list(context.scope_ids), cursor, cursor, limit+1))).fetchall()
        valid = await self.identity.check_many(context, tuple(source_refs(row["source_refs"]) for row in rows[:limit]))
        items = tuple(row for row, allowed in zip(rows[:limit], valid, strict=True) if allowed)
        more = len(rows)>limit
        return Page(items, str(rows[limit-1]["id"]) if more else None, Coverage(not more and all(valid), missing=() if all(valid) else ("source_unavailable",)))

    async def turn(self, context, turn_id):
        await self.identity.check(context)
        async with self.database.transaction(read_only=True) as connection:
            row = await (await connection.execute("SELECT * FROM conversation.turns WHERE turn_id=%s AND actor_id=%s AND conversation_id=%s", (turn_id, context.actor_id, context.conversation_id))).fetchone()
            links = await (await connection.execute("SELECT action_id FROM conversation.tool_links WHERE turn_id=%s", (turn_id,))).fetchall()
        if not row:
            raise AppError("turn_not_found", status=404)
        await self.identity.check(context, source_refs(row["source_refs"]))
        expression = await self.expression.get(context, row["expression_id"]) if row["expression_id"] else None
        return DialogueResult(turn_id, row["state"], row["expression_id"], receipt(expression["receipt"]) if expression else None,
            tuple(expression["public_parts"]) if expression else (), tuple(item["action_id"] for item in links),
            (row["model_attempt_id"],) if row["model_attempt_id"] else (), tuple(row["missing"]))

    async def run(self, turn_id):
        async with self.database.transaction() as connection:
            row = await (await connection.execute("SELECT * FROM conversation.turns WHERE turn_id=%s FOR UPDATE", (turn_id,))).fetchone()
            if not row or row["state"] != "queued":
                return
            await connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (f"turn:{row['actor_id']}:{row['conversation_id']}",))
            busy = await (await connection.execute("SELECT turn_id FROM conversation.turns WHERE actor_id=%s AND conversation_id=%s AND state='running' AND turn_id<>%s", (row["actor_id"], row["conversation_id"], turn_id))).fetchone()
            if busy:
                return  # The running owner schedules the oldest queued turn when releasing this lane.
            attempt_id = str(uuid4())
            await connection.execute("UPDATE conversation.turns SET state='running',started_at=now(),model_attempt_id=%s,lease_until=now()+interval '16 minutes' WHERE turn_id=%s", (attempt_id, turn_id))
        context = context_from_record(row["context"])
        try:
            await self.identity.check(context, source_refs(row["source_refs"]))
            actor = await self.companion.actor(context)
            if not actor["enabled"]:
                raise AppError("actor_disabled", status=409)
            choices = await self.catalog.discover(context)
            if row["command"] and row["round"] == 0:
                await self._command(context, row, choices)
            else:
                await self._model_turn(context, row, attempt_id, choices)
        except BaseException as error:
            if isinstance(error, asyncio.CancelledError):
                await self.models.cancel(attempt_id)
            code = getattr(error, "code", "turn_outcome_unknown")
            known = isinstance(error, AppError) and code in {"actor_disabled", "forbidden", "source_unavailable", "deadline_exceeded", "capability_unavailable", "command_unknown"}
            async with self.database.transaction() as connection:
                await connection.execute("UPDATE conversation.turns SET state=%s,error_code=%s,completed_at=now() WHERE turn_id=%s AND state='running'", ("failed" if known else "unknown", code, turn_id))
            if isinstance(error, asyncio.CancelledError):
                raise
        finally:
            await self._next(row["actor_id"], row["conversation_id"])

    async def _model_turn(self, context, row, attempt_id, choices):
        actor = await self.companion.actor(context)
        persona = await self.companion.persona(context, actor["persona_id"], actor["persona_revision"]) if actor["persona_id"] else None
        state = await self.life.state(context)
        commitments = await self.life.commitments(context, limit=20)
        life_refs = tuple({(ref.source_id, ref.revision): ref for ref in (
            *state["affect"]["source_refs"],
            *(source_refs(state["activity"]["source_refs"]) if state["activity"] else ()),
            *(ref for item in commitments.items for ref in source_refs(item["source_refs"])))}.values())
        material = {"current_activity": state["activity"], "local_timezone": state["timezone"],
                    "short_term_feelings": state["affect"]["feelings"], "expression_direction": state["affect"]["regulation"]["expression_direction"],
                    "commitments": wire(commitments.items)}
        contributions = (ContextContribution("life_and_feelings", json.dumps(wire(material), ensure_ascii=False),
            life_refs, context.scope_ids, 65),) + await self.growth.material(context)
        recent = await self.messages(context, limit=30)
        messages = tuple(self._message(item["speaker"], item["parts"]) for item in reversed(recent.items))
        continuation = tuple(self._message(item["role"], item["parts"], item.get("native", {})) for item in row["continuation"])
        tools, tool_map = self._tools(choices)
        budget = RequestBudget(row["deadline_at"])
        pack = await self.assembler.assemble(context, "chat", "\n".join(part.text for part in input_parts(row["input_parts"]) if part.text), budget,
            persona_text=persona.prompt_text if persona else "", recent_messages=messages+continuation,
            tools=tools, contributions=contributions)
        required = {"text"}
        if tools:
            required.add("tools")
        required.update(part.kind for message in pack.messages for part in message.parts if part.kind in {"image", "audio", "video"})
        result = await self.models.generate(ModelRequest(row["turn_id"]+":"+str(row["round"]), attempt_id, "chat", ServiceClass.INTERACTIVE,
            context, budget, tuple(pack.messages), system=pack.system, tools=tuple(pack.tools), required_capabilities=frozenset(required),
            estimated_input_tokens=pack.token_usage.estimated_tokens, source_refs=tuple(pack.source_refs), persona_revision=persona.revision if persona else None))
        await self.identity.check(context, tuple(pack.source_refs))
        async with self.database.transaction() as connection:
            fresh = await (await connection.execute("SELECT state FROM conversation.turns WHERE turn_id=%s FOR UPDATE", (row["turn_id"],))).fetchone()
            if fresh["state"] != "running":
                return
            await connection.execute("UPDATE conversation.turns SET model_parts=%s,model_native=%s,source_refs=%s,missing=%s WHERE turn_id=%s", (document(result.parts), document(result.native_response), document(pack.source_refs), list(pack.missing), row["turn_id"]))
        if result.receipt.status != "succeeded":
            raise AppError(result.receipt.error_code or "model_outcome_unknown", details={"attempt_id": result.receipt.attempt_id, "dispatch": result.receipt.dispatch})
        calls = tuple(part for part in result.parts if part.kind == "tool_call")
        ordered = list(row["ordered_parts"])
        for index, part in enumerate(result.parts):
            if part.native.get("thought"):
                continue
            if part.kind == "tool_call":
                ordered.append({"kind": "tool_slot", "call_id": part.call_id or f"round:{row['round']}:{index}"})
            elif part.kind == "text" and part.text:
                ordered.append({"kind": "text", "text": part.text})
            elif part.kind in {"image", "audio", "video"} and part.media:
                ordered.append({"kind": part.kind, "media": wire(part.media)})
        async with self.database.transaction() as connection:
            await connection.execute("UPDATE conversation.turns SET ordered_parts=%s,tool_dispatch_complete=false WHERE turn_id=%s AND state='running'", (document(ordered), row["turn_id"]))
        if calls:
            if row["round"] >= 8 or len(calls)>8:
                raise AppError("tool_round_limit", status=409)
            continuation = list(row["continuation"])+[{"role": "assistant", "parts": wire(result.parts)}]
            async with self.database.transaction() as connection:
                await connection.execute("UPDATE conversation.turns SET continuation=%s WHERE turn_id=%s", (document(continuation), row["turn_id"]))
            for index, call in enumerate(result.parts):
                if call.kind != "tool_call":
                    continue
                definition = tool_map.get(call.name)
                if not definition:
                    raise AppError("capability_unavailable", status=409)
                await self._invoke(context, row, call.call_id or f"round:{row['round']}:{index}", definition, call.arguments or {}, tuple(pack.source_refs))
            async with self.database.transaction() as connection:
                await connection.execute("UPDATE conversation.turns SET tool_dispatch_complete=true WHERE turn_id=%s", (row["turn_id"],))
            await self._await_tools(context, row["turn_id"])
            return
        public = tuple(part for part in ordered if part["kind"] != "tool_slot")
        if not public:
            async with self.database.transaction() as connection:
                await connection.execute("UPDATE conversation.turns SET state='completed',completed_at=now() WHERE turn_id=%s AND state='running'", (row["turn_id"],))
            return
        expression_id = "turn:"+row["turn_id"]
        # The turn is attached before dispatch, so an unknown browser/QQ delivery stays visible.
        async with self.database.transaction() as connection:
            fresh = await (await connection.execute("SELECT state FROM conversation.turns WHERE turn_id=%s FOR UPDATE", (row["turn_id"],))).fetchone()
            if fresh["state"] != "running":
                return
            await connection.execute("UPDATE conversation.turns SET expression_id=%s WHERE turn_id=%s", (expression_id, row["turn_id"]))
        output_artifacts = artifacts(row["artifacts"])
        expression_refs = tuple({(ref.source_id, ref.revision): ref for ref in (*pack.source_refs, *(ref for item in output_artifacts for ref in item.source_refs))}.values())
        delivered = await self.expression.send(context, ExpressionPlan(expression_id, context.actor_id, context.conversation_id,
            public, expression_refs, OriginKind.ACTOR, turn_id=row["turn_id"], artifacts=output_artifacts))
        async with self.database.transaction() as connection:
            await connection.execute("UPDATE conversation.turns SET state=%s,completed_at=now() WHERE turn_id=%s AND state='running'", ("completed" if delivered.delivery == "delivered" else "unknown" if delivered.delivery in {"pending", "unknown"} else "failed", row["turn_id"]))

    @staticmethod
    def _message(role, parts, native=None):
        public_role = "assistant" if role == "assistant" else "tool" if role == "tool" else "user"
        converted = []
        for item in parts:
            if item["kind"] in {"text", "image", "audio", "video", "tool_call", "tool_result"}:
                converted.extend(model_parts((item,)))
            else:
                converted.append(ModelPart("text", text=json.dumps(item, ensure_ascii=False)))
        return ModelMessage(public_role, tuple(converted), native or {})

    @staticmethod
    def _tools(choices):
        tools, mapping = [], {}
        for choice in choices:
            definition = choice["definition"]
            if not choice["can_execute"] or definition.model_tool is None:
                continue
            name = definition.model_tool["name"]
            if name in mapping:
                raise AppError("duplicate_tool_name", status=500)
            mapping[name] = definition
            tools.append(ToolSpec(name, definition.description, definition.input_schema))
        return tuple(tools), mapping

    async def _invoke(self, context, row, call_id, definition, arguments, refs):
        action_id = str(uuid5(NAMESPACE_URL, f"tool:{row['turn_id']}:{call_id}"))
        async with self.database.transaction() as connection:
            unresolved = await (await connection.execute("SELECT action_id FROM conversation.tool_links WHERE turn_id=%s AND capability_id=%s AND arguments_hash=%s AND state='unknown'", (row["turn_id"], definition.id, signature(arguments)))).fetchone()
            if unresolved:
                raise AppError("original_action_unresolved", status=409, details={"action_id": unresolved["action_id"]})
            tool_name = definition.model_tool["name"] if definition.model_tool else "declared_command"
            await connection.execute("INSERT INTO conversation.tool_links(turn_id,call_id,action_id,capability_id,tool_name,arguments_hash,state) VALUES (%s,%s,%s,%s,%s,%s,'queued') ON CONFLICT DO NOTHING", (row["turn_id"], call_id, action_id, definition.id, tool_name, signature(arguments)))
        action = Action(action_id, f"turn:{row['turn_id']}:{call_id}", definition.id, context.actor_id, context,
            arguments, context.origin_kind, refs, expression_id="turn:"+row["turn_id"], deadline_at=row["deadline_at"])
        # Background work releases the dialogue lane; the persisted action owns its actual completion.
        result = receipt(await self.actions.invoke(action, background=definition.long_running,
            service_class=ServiceClass.ELASTIC if definition.long_running else ServiceClass.INTERACTIVE))
        await self._tool_receipt(context, result)

    async def _command(self, context, row, choices):
        name, _, raw = row["command"].strip().partition(" ")
        definition = next((choice["definition"] for choice in choices if choice["can_execute"] and choice["definition"].public_command
            and name in {choice["definition"].public_command["name"], *choice["definition"].public_command.get("aliases", ())}), None)
        if not definition:
            raise AppError("command_unknown", status=404)
        try:
            arguments = json.loads(raw) if raw else {}
        except ValueError:
            raise AppError("command_arguments_invalid") from None
        tool_name = definition.model_tool["name"] if definition.model_tool else "declared_command"
        async with self.database.transaction() as connection:
            continuation = list(row["continuation"])+[{"role": "assistant", "parts": [wire(ModelPart("tool_call", call_id="command", name=tool_name, arguments=arguments))]}]
            await connection.execute("UPDATE conversation.turns SET continuation=%s,ordered_parts=%s,tool_dispatch_complete=false WHERE turn_id=%s", (document(continuation), document([{"kind": "tool_slot", "call_id": "command"}]), row["turn_id"]))
        await self._invoke(context, row, "command", definition, arguments, source_refs(row["source_refs"]))
        async with self.database.transaction() as connection:
            await connection.execute("UPDATE conversation.turns SET tool_dispatch_complete=true WHERE turn_id=%s", (row["turn_id"],))
        await self._await_tools(context, row["turn_id"])

    async def _tool_receipt(self, context, result, result_revision=None):
        if result_revision is None:
            current = await self.actions.get(result.action_id, context)
            result, result_revision = receipt(current["receipt"]), current["result_revision"]
        if result is None:
            return
        async with self.database.transaction() as connection:
            link = await (await connection.execute("SELECT l.*,t.context,t.state AS turn_state FROM conversation.tool_links l JOIN conversation.turns t ON t.turn_id=l.turn_id WHERE action_id=%s AND t.actor_id=%s AND t.conversation_id=%s FOR UPDATE OF l", (result.action_id, context.actor_id, context.conversation_id))).fetchone()
            if not link:
                return
            if result_revision <= link["result_revision"]:
                return
            if link["consumed"]:
                return
            if link["turn_state"] in {"cancelled", "failed", "completed"}:
                return
            state = result.execution
            await connection.execute("UPDATE conversation.tool_links SET state=%s,last_receipt_id=%s,result=%s,result_revision=%s WHERE action_id=%s", (state, result.receipt_id, document(result), result_revision, result.action_id))

    async def _await_tools(self, context, turn_id):
        async with self.database.transaction() as connection:
            row = await (await connection.execute("SELECT * FROM conversation.turns WHERE turn_id=%s FOR UPDATE", (turn_id,))).fetchone()
            if row["state"] not in {"running", "waiting_external"}:
                return
            if not row["tool_dispatch_complete"]:
                return
            links = await (await connection.execute("SELECT * FROM conversation.tool_links WHERE turn_id=%s AND consumed=false ORDER BY call_id", (turn_id,))).fetchall()
            if not links:
                return
            if any(link["state"] in {"queued", "pending", "running"} for link in links):
                await connection.execute("UPDATE conversation.turns SET state='waiting_external' WHERE turn_id=%s", (turn_id,))
                await self.jobs.enqueue(connection, JobSpec("conversation.reconcile", f"turn-reconcile:{turn_id}:{row['round']}:0", {"turn_id": turn_id, "sequence": 0}, scheduled_at=datetime.now(UTC)+timedelta(seconds=15), subject_id=context.actor_id))
                return
            continuation = list(row["continuation"])
            ordered = list(row["ordered_parts"])
            output_artifacts = list(row["artifacts"])
            explicit_expressions = []
            for link in links:
                result = receipt(link["result"])
                explicit_expressions.extend(result.result.get("explicit_expression_ids", ()))
                # Unknown is a useful tool result, never an instruction to submit the action again.
                continuation.append({"role": "tool", "parts": [wire(ModelPart("tool_result", call_id=link["call_id"], name=link["tool_name"], result=wire(result)))]})
                replacements = [] if result.delivery == "delivered" else [{"kind": "image" if item.media.mime_type.startswith("image/") else "audio" if item.media.mime_type.startswith("audio/") else "video" if item.media.mime_type.startswith("video/") else "file", "media": wire(item.media)} for item in result.artifacts]
                ordered = [public for part in ordered for public in (replacements if part.get("kind") == "tool_slot" and part.get("call_id") == link["call_id"] else [part])]
                output_artifacts.extend(wire(result.artifacts))
                await connection.execute("UPDATE conversation.tool_links SET consumed=true WHERE turn_id=%s AND call_id=%s", (turn_id, link["call_id"]))
            if explicit_expressions:
                # A plugin's explicit presentation already used ExpressionService; do not send a second answer.
                outcomes = [receipt(link["result"]) for link in links if receipt(link["result"]).result.get("explicit_expression_ids")]
                state = "completed" if all(item.delivery == "delivered" for item in outcomes) else "unknown" if any(item.delivery in {"unknown", "pending"} for item in outcomes) else "failed"
                await connection.execute("UPDATE conversation.turns SET state=%s,expression_id=%s,completed_at=now() WHERE turn_id=%s", (state, explicit_expressions[-1], turn_id))
                return
            next_round = row["round"]+1
            await connection.execute("UPDATE conversation.turns SET state='queued',continuation=%s,ordered_parts=%s,artifacts=%s,round=%s WHERE turn_id=%s", (document(continuation), document(ordered), document(output_artifacts), next_round, turn_id))
            await self.jobs.enqueue(connection, JobSpec("conversation.run", f"turn:{turn_id}:{next_round}", {"turn_id": turn_id}, subject_id=context.actor_id))

    async def on_result(self, context, result, *, result_revision=None):
        result = receipt(result)
        await self.identity.check(context)
        await self._tool_receipt(context, result, result_revision)
        async with self.database.transaction(read_only=True) as connection:
            link = await (await connection.execute("SELECT turn_id FROM conversation.tool_links WHERE action_id=%s", (result.action_id,))).fetchone()
        if link:
            await self._await_tools(context, link["turn_id"])

    async def reconcile(self, turn_id, sequence):
        async with self.database.transaction(read_only=True) as connection:
            row = await (await connection.execute("SELECT * FROM conversation.turns WHERE turn_id=%s", (turn_id,))).fetchone()
            links = await (await connection.execute("SELECT * FROM conversation.tool_links WHERE turn_id=%s AND consumed=false", (turn_id,))).fetchall()
        if not row or row["state"] != "waiting_external":
            return
        context = context_from_record(row["context"])
        await self.identity.check(context, source_refs(row["source_refs"]))
        for link in links:
            current = await self.actions.get(link["action_id"], context)
            if current["receipt"]:
                await self._tool_receipt(context, receipt(current["receipt"]), current["result_revision"])
        await self._await_tools(context, turn_id)
        async with self.database.transaction() as connection:
            fresh = await (await connection.execute("SELECT state FROM conversation.turns WHERE turn_id=%s", (turn_id,))).fetchone()
            if fresh["state"] == "waiting_external" and datetime.now(UTC)<row["deadline_at"]:
                await self.jobs.enqueue(connection, JobSpec("conversation.reconcile", f"turn-reconcile:{turn_id}:{row['round']}:{sequence+1}", {"turn_id": turn_id, "sequence": sequence+1}, scheduled_at=datetime.now(UTC)+timedelta(seconds=15), subject_id=context.actor_id))
            elif fresh["state"] == "waiting_external":
                await connection.execute("UPDATE conversation.turns SET state='unknown',error_code='action_wait_deadline' WHERE turn_id=%s", (turn_id,))

    async def _next(self, actor_id, conversation_id):
        async with self.database.transaction() as connection:
            row = await (await connection.execute("SELECT turn_id,round FROM conversation.turns WHERE actor_id=%s AND conversation_id=%s AND state='queued' ORDER BY received_at LIMIT 1", (actor_id, conversation_id))).fetchone()
            if row:
                await self.jobs.enqueue(connection, JobSpec("conversation.run", f"turn-wake:{row['turn_id']}:{row['round']}:{uuid4()}", {"turn_id": row["turn_id"]}, subject_id=actor_id))

    async def cancel(self, context, body):
        await self.identity.check(context)
        async def apply(connection):
            row = await (await connection.execute("SELECT * FROM conversation.turns WHERE turn_id=%s AND actor_id=%s AND conversation_id=%s FOR UPDATE", (body.get("turn_id"), context.actor_id, context.conversation_id))).fetchone()
            if not row:
                raise AppError("turn_not_found", status=404)
            if body.get("expected_revision") != row["revision"]:
                raise AppError("revision_conflict", status=409)
            if row["state"] in {"completed", "failed", "cancelled"}:
                raise AppError("turn_already_finished", status=409)
            await connection.execute("UPDATE conversation.turns SET state='cancelled',revision=revision+1 WHERE turn_id=%s", (row["turn_id"],))
            await self.jobs.enqueue(connection, JobSpec("conversation.cancel", "turn-cancel:"+row["turn_id"], {"turn_id": row["turn_id"]}, subject_id=context.actor_id))
            return CommandResult(body["client_request_id"], {"turn_id": row["turn_id"], "state": "cancelled", "external_outcome": "unconfirmed"}, row["revision"]+1, attempt_id=row["model_attempt_id"])
        return await self.commands.execute(context, "conversation.cancel", body["client_request_id"], signature(body), apply)

    async def reconcile_expression(self, context, body):
        await self.identity.check(context)
        async def apply(_connection):
            current = await self.expression.reconcile(context, body.get("expression_id"), body.get("expected_revision"))
            actual = receipt(current["receipt"])
            return CommandResult(body["client_request_id"], current, current["revision"],
                actual.action_id if actual else None, actual.attempt_id if actual else None, actual)
        async def replay(_connection, original):
            current = await self.expression.get(context, body.get("expression_id"))
            return replace(original, data=current, revision=current["revision"])
        return await self.commands.execute(context, "conversation.expression.reconcile", body["client_request_id"], signature(body), apply, on_replay=replay)

    async def cancel_work(self, turn_id):
        async with self.database.transaction(read_only=True) as connection:
            row = await (await connection.execute("SELECT * FROM conversation.turns WHERE turn_id=%s", (turn_id,))).fetchone()
            links = await (await connection.execute("SELECT action_id FROM conversation.tool_links WHERE turn_id=%s", (turn_id,))).fetchall()
        if not row or row["state"] != "cancelled":
            return
        context = context_from_record(row["context"])
        if row["model_attempt_id"]:
            await self.models.cancel(row["model_attempt_id"])
        for link in links:
            await self.actions.cancel(link["action_id"], context)

    @staticmethod
    def _validate_parts(parts):
        if not parts or len(parts)>64:
            raise AppError("invalid_input_parts")
        for part in parts:
            if part.kind not in {"text", "image", "audio", "video", "file", "reply", "reference", "forward", "reaction"}:
                raise AppError("invalid_input_part")
            if part.kind == "text" and (not isinstance(part.text, str) or len(part.text)>100000):
                raise AppError("invalid_input_text")
            if part.kind in {"image", "audio", "video", "file"} and part.media is None:
                raise AppError("input_media_required")
