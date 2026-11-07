"""One durable input lane for browser messages, channels and declared commands."""
from __future__ import annotations
from tianshu.sources.dependencies import unique_refs

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid4, uuid5

from tianshu.models.budgets import BACKGROUND_INPUT_TOKENS
from tianshu.models.ledger import submission_digest
from tianshu.models.types import ModelMessage, ModelPart, ModelRequest, ToolSpec
from tianshu.runtime.api import CommandResult, Page
from tianshu.runtime.errors import AppError
from tianshu.runtime.jobs import JobSpec
from tianshu.runtime.recovery_policy import RecoveryWait
from tianshu.runtime.execution import ExecutionLane
from tianshu.runtime.serialization import wire
from tianshu.types import Action, ContextContribution, Coverage, OriginKind, RequestBudget, ServiceClass
from .codec import (artifacts, context_from_record, document, input_parts, model_parts, receipt,
                    signature, source_refs)
from .persistence import MaterialSnapshotChanged, lock_material_row, retry_material_snapshot
from .types import CollectorPolicy, DialogueResult, ExpressionPlan, InputMessage
from .collector import ConversationCollector
from .participation import GroupParticipation
from .expression import expression_turn_state
from .context.assembly import ContextMessage
from .generation import GenerationRequests
from .model_admission import BLOCKED_ROUTE, ModelAdmissionWaits
from .recovery import can_retry_resource_wait, is_resource_wait, raise_resource_wait


CHAT_EXECUTION_SECONDS = 15 * 60
TURN_SETTLEMENT_SECONDS = 60
AUTONOMOUS_OUTPUT_RESERVE = 2048


def _business_deadline(row):
    return row["deadline_at"] if row.get("autonomous") else None


class TurnRejected(AppError):
    """A definite local refusal or terminal receipt, never inferred transport failure."""


class ConversationService:
    def __init__(self, database, commands, jobs, identity, sources, memory, companion,
                 life, growth, assembler, models, catalog, actions, expression, contacts, *, policy=CollectorPolicy()):
        self.database, self.commands, self.jobs, self.identity = database, commands, jobs, identity
        self.sources, self.memory, self.companion = sources, memory, companion
        self.life, self.growth, self.assembler, self.models = life, growth, assembler, models
        self.catalog, self.actions, self.expression, self.contacts = catalog, actions, expression, contacts
        self.collector = ConversationCollector(database, jobs, policy)
        self.participation = GroupParticipation(self)
        self.generation = GenerationRequests(database, sources, models)
        self.model_admissions = ModelAdmissionWaits(database, models, self.generation, jobs,
                                                   self._schedule_model_observation)
        self.statistics = None
        if hasattr(self.assembler, "bind_behavior_resolver"):
            self.assembler.bind_behavior_resolver(growth)
        if hasattr(expression, "bind_behavior_resolver"):
            expression.bind_behavior_resolver(growth)

    def bind_statistics(self, statistics):
        self.statistics = statistics
        if hasattr(self.expression, "bind_statistics"):
            self.expression.bind_statistics(statistics)

    async def _schedule_statistics_repair(self, connection):
        if self.statistics is not None:
            from .observability import schedule_repair_on
            await schedule_repair_on(self.jobs, connection)

    async def backfill_message_statistics(self, *, after_sequence=0, limit=500):
        from .observability import backfill_inputs
        return await backfill_inputs(self, after_sequence=after_sequence, limit=limit)

    async def backfill_delivery_statistics(self, *, after_id="", limit=500):
        from .observability import backfill_deliveries
        return await backfill_deliveries(self, after_id=after_id, limit=limit)

    async def reconcile_statistics(self, after_sequence=0, after_id="", inputs_done=False, deliveries_done=False):
        if self.statistics is None:
            return
        inputs = {"complete": True} if inputs_done else await self.backfill_message_statistics(after_sequence=after_sequence)
        deliveries = {"complete": True} if deliveries_done else await self.backfill_delivery_statistics(after_id=after_id)
        if not inputs["complete"] or not deliveries["complete"]:
            async with self.database.transaction() as connection:
                await self.jobs.enqueue(connection, JobSpec("conversation.statistics_reconcile", "conversation-statistics:"+str(uuid4()),
                    {"after_sequence": inputs.get("next_cursor") if not inputs["complete"] else after_sequence,
                     "after_id": deliveries.get("next_cursor") if not deliveries["complete"] else after_id,
                     "inputs_done": inputs["complete"], "deliveries_done": deliveries["complete"]},
                    ServiceClass.ELASTIC, datetime.now(UTC)+timedelta(seconds=10), execution_lane=ExecutionLane.CONTROL))

    async def execution_lane_on(self, connection, payload):
        row = await (await connection.execute(
            "SELECT autonomous FROM conversation.turns WHERE turn_id=%s", (payload["turn_id"],)
        )).fetchone()
        return ExecutionLane.PROACTIVE if row and row["autonomous"] else ExecutionLane.CONVERSATION

    async def _prepare_input(self, context, message: InputMessage, *, connection=None, actor=None):
        if connection is None:
            await self.identity.check(context, message.source_refs)
        else:
            await self.identity.check_on(connection, context, message.source_refs)
        if not context.actor_id or not context.conversation_id:
            raise AppError("conversation_required")
        if message.conversation_kind != context.conversation_kind:
            raise AppError("conversation_kind_mismatch", status=403)
        self._validate_parts(message.parts)
        if actor is None:
            actor = await self.companion.actor(context)
        turn_id = str(uuid5(NAMESPACE_URL, f"turn:{context.actor_id}:{context.conversation_id}:{message.producer}:{message.event_id}:{message.revision}"))
        query = "SELECT original_parts,original_source_refs,original_digest,occurred_at FROM conversation.turns WHERE turn_id=%s"
        if connection is None:
            async with self.database.transaction(read_only=True) as probe:
                previous = await (await probe.execute(query, (turn_id,))).fetchone()
        else:
            previous = await (await connection.execute(query, (turn_id,))).fetchone()
        if previous:
            if (previous.get("original_digest") or signature(previous["original_parts"])) != signature(message.parts):
                raise AppError("event_revision_conflict", status=409)
            refs = source_refs(previous["original_source_refs"])
            message = replace(message, occurred_at=previous["occurred_at"])
        else:
            options = ({"connection": connection, "generation": message.deployment_generation}
                       if connection is not None else {})
            if connection is not None:
                await self.sources.prepare_event_write(connection, context, producer=message.producer,
                    event_id=message.event_id, revision=message.revision, parts=message.parts,
                    additional_refs=message.source_refs)
            refs = await self.sources.record_event(context, producer=message.producer, event_id=message.event_id,
                revision=message.revision, occurred_at=message.occurred_at, parts=message.parts, origin_kind=context.origin_kind,
                **options)
        # The channel has already resolved an external reply owner; observing never steals it.
        responding = context.delivery_allowed and message.reply_required and message.reply_owner == "tianshu" and actor["enabled"]
        return message, refs, turn_id, responding

    async def ingest(self, context, message: InputMessage):
        if message.deployment_generation is None:
            async with self.database.transaction() as connection:
                generation = await self.jobs.current_generation_on(connection)
            message = replace(message, deployment_generation=generation)
        actor = await self.companion.actor(context)
        prepared = await self.participation.prepare(context, message)
        prepared_occurred_at = message.occurred_at
        async with self.database.transaction() as connection:
            await self.jobs.assert_generation_on(connection, message.deployment_generation)
            message, refs, turn_id, responding = await self._prepare_input(context, message, connection=connection, actor=actor)
            if message.occurred_at != prepared_occurred_at:
                prepared = None
            if not await self.sources.validate_persistence(connection,context,refs):
                raise AppError("source_unavailable",status=409)
            result = await self.collector.accept(connection, context, message, refs, turn_id, responding)
            await self.participation.offer(connection, context, message, turn_id, prepared)
            from .observability import received_on
            recorded = await received_on(self.statistics, connection, input_id=turn_id, actor_id=context.actor_id,
                                         occurred_at=message.received_at, reply_required=message.reply_required)
            if self.statistics is not None and not recorded:
                await self._schedule_statistics_repair(connection)
        await self._observe_turn(context, turn_id, deployment_generation=message.deployment_generation)
        return {**result,"source_refs":refs}

    async def _observe_turn(self, context, turn_id, *, deployment_generation=None):
        async with self.database.transaction() as connection:
            if deployment_generation is not None:
                await self.jobs.assert_generation_on(connection, deployment_generation)
            row = await (await connection.execute("SELECT * FROM conversation.turns WHERE turn_id=%s AND actor_id=%s AND conversation_id=%s", (turn_id, context.actor_id, context.conversation_id))).fetchone()
            if not row:
                raise AppError("turn_not_found", status=404)
            parts, refs = input_parts(row["original_parts"]), source_refs(row["original_source_refs"])
            if not await self.sources.validate_persistence(connection, context, refs):
                raise AppError("source_unavailable", status=409)
            await self.contacts.received(context, row["occurred_at"], input_id=turn_id, connection=connection)
            # Coverage describes accepted input parts, not whether attached media was understood.
            unavailable = tuple(
                f"media_part_unavailable:{index}:{part.metadata.get('error_code', 'media_unavailable')}"
                for index, part in enumerate(parts)
                if part.kind in {"image", "audio", "video", "file"} and part.media is None
            )
            received_all = len(refs) == len(parts) and not unavailable
            coverage = Coverage(
                received_all,
                total=len(parts),
                missing=unavailable + (() if len(refs) == len(parts) else ("source_parts_unavailable",)),
                ranges=({"start": 0, "end": len(parts), "unit": "parts"},) if len(refs) == len(parts) else (),
                representation="received_input_parts",
            )
            await self.memory.observe_episode(context, {"episode_id": "input:"+turn_id, "event_id": row["event_id"],
                "revision": row["event_revision"], "origin_kind": context.origin_kind, "actor_id": context.actor_id,
                "subject_id": context.person_id, "occurred_at": row["occurred_at"],
                "summary": "\n".join(part.text for part in parts if part.text)[:4000], "source_refs": refs,
                "coverage": coverage,
                "actual_result": {"reply_owner": row["reply_owner"], "representation": "source_excerpt",
                                  "modalities": [part.kind for part in parts]}}, connection=connection)

    async def send(self, context, body):
        await self.identity.check(context)
        parts = input_parts(body.get("parts", ()))
        now = datetime.now(UTC)
        message = InputMessage("web:"+context.principal_id, body["client_request_id"], 1, now, now,
                               parts, conversation_kind=context.conversation_kind)
        turn_id = str(uuid5(NAMESPACE_URL, f"turn:{context.actor_id}:{context.conversation_id}:{message.producer}:{message.event_id}:1"))
        async with self.database.transaction() as connection:
            generation = await self.jobs.current_generation_on(connection)
        message = replace(message, deployment_generation=generation)
        actor = await self.companion.actor(context)
        async def apply(connection):
            await self.jobs.assert_generation_on(connection, generation)
            prepared_message, refs, _, responding = await self._prepare_input(context, message, connection=connection, actor=actor)
            if not await self.sources.validate_persistence(connection,context,refs):
                raise AppError("source_unavailable",status=409)
            result = await self.collector.accept(connection, context, prepared_message, refs, turn_id, responding)
            from .observability import received_on
            recorded = await received_on(self.statistics, connection, input_id=turn_id, actor_id=context.actor_id,
                                         occurred_at=message.received_at, reply_required=message.reply_required)
            if self.statistics is not None and not recorded:
                await self._schedule_statistics_repair(connection)
            return CommandResult(body["client_request_id"], result, 1)
        async def replay(connection, original):
            row = await (await connection.execute("SELECT turn_id,state,merged_into,revision FROM conversation.turns WHERE turn_id=%s", (turn_id,))).fetchone()
            if not row:
                raise AppError("turn_not_found", status=404)
            return replace(original, data={"turn_id": row["merged_into"] or row["turn_id"], "state": row["state"], "revision": row["revision"]})
        result = await self.commands.execute(context, "conversation.send", body["client_request_id"], signature(body), apply, on_replay=replay)
        await self._observe_turn(context, turn_id, deployment_generation=generation)
        return result

    async def messages(self, context, *, cursor=None, limit=50, before_id=None):
        await self.identity.check(context)
        limit = min(100, max(1, int(limit)))
        async with self.database.transaction(read_only=True) as connection:
            rows = await (await connection.execute("SELECT * FROM conversation.messages WHERE actor_id=%s AND conversation_id=%s AND scope_ids<@%s::text[] AND (%s::bigint IS NULL OR id<%s) AND (%s::bigint IS NULL OR id<=%s) ORDER BY id DESC LIMIT %s", (context.actor_id, context.conversation_id, list(context.scope_ids), cursor, cursor, before_id, before_id, limit+1))).fetchall()
        rows = await self._supplemented_messages(context, rows)
        valid = await self.identity.check_many(context, tuple(source_refs(row["source_refs"]) for row in rows[:limit]))
        items = tuple(row for row, allowed in zip(rows[:limit], valid, strict=True) if allowed)
        more = len(rows)>limit
        return Page(items, str(rows[limit-1]["id"]) if more else None, Coverage(not more and all(valid), missing=() if all(valid) else ("source_unavailable",)))

    async def _supplemented_messages(self, context, rows):
        """Overlay available attachments for reads without rewriting original input."""
        ids = [row["turn_id"] for row in rows if row.get("turn_id")]
        if not ids:
            return rows
        async with self.database.transaction(read_only=True) as connection:
            supplements = await (await connection.execute(
                "SELECT DISTINCT ON (input_id,part_index) * FROM conversation.input_supplements "
                "WHERE input_id=ANY(%s) ORDER BY input_id,part_index,supplement_revision DESC", (ids,))).fetchall()
        allowed = await self.identity.check_many(context, tuple(
            source_refs((item["original_ref"], item["supplement_ref"])) for item in supplements))
        grouped = {}
        for item, valid in zip(supplements, allowed, strict=True):
            if valid:
                grouped.setdefault(item["input_id"], []).append(item)
        result = []
        for row in rows:
            row = dict(row)
            parts = [dict(part) for part in row["parts"]]
            refs = source_refs(row["source_refs"])
            for item in grouped.get(row.get("turn_id"), ()):
                index = item["part_index"]
                if not 0 <= index < len(parts) or parts[index].get("media"):
                    continue
                metadata = dict(parts[index].get("metadata") or {})
                metadata.update(media_unavailable=False, original_pending=False,
                                supplement_revision=item["supplement_revision"],
                                supplement_source_ref=item["supplement_ref"])
                metadata.pop("error_code", None)
                parts[index] = {**parts[index], "media": item["media"], "metadata": metadata}
                refs = unique_refs(refs, source_refs((item["supplement_ref"],)))
            row["parts"], row["source_refs"] = parts, wire(refs)
            result.append(row)
        return result

    async def supplement_input(self, connection, context, *, input_id, part_index, original_ref, supplement_ref, media,
                               deployment_generation=None):
        """Attach a separate version to an original part, never re-ingest it."""
        refs = (original_ref, supplement_ref)
        if deployment_generation is not None:
            await self.jobs.assert_generation_on(connection, deployment_generation)
        if not await self.sources.validate_persistence(connection, context, refs):
            raise AppError("source_unavailable", status=409)
        row = await (await connection.execute(
            "SELECT * FROM conversation.turns WHERE turn_id=%s AND actor_id=%s AND conversation_id=%s FOR UPDATE",
            (input_id, context.actor_id, context.conversation_id))).fetchone()
        if not row or not 0 <= part_index < len(row["original_parts"]):
            raise AppError("supplement_original_missing", status=404)
        original_refs = source_refs(row["original_source_refs"])
        if part_index >= len(original_refs) or original_refs[part_index] != original_ref:
            raise AppError("supplement_original_mismatch", status=409)
        if not await self.sources.validate_media_supplement_on(connection, context, supplement_ref,
                original_ref=original_ref, producer=row["producer"], event_id=row["event_id"],
                event_revision=row["event_revision"], part_index=part_index):
            raise AppError("supplement_source_mismatch", status=409)
        original = row["original_parts"][part_index]
        if original["kind"] not in {"image", "audio", "video", "file"} or original.get("media"):
            raise AppError("supplement_part_not_missing", status=409)
        content_digest = signature({"sha256": media.sha256, "size": media.size_bytes, "mime": media.mime_type})
        existing = await (await connection.execute(
            "SELECT * FROM conversation.input_supplements WHERE input_id=%s AND part_index=%s AND content_digest=%s",
            (input_id, part_index, content_digest))).fetchone()
        if existing:
            return existing["supplement_revision"]
        head = await (await connection.execute(
            "SELECT COALESCE(max(supplement_revision),0) AS revision FROM conversation.input_supplements "
            "WHERE input_id=%s AND part_index=%s", (input_id, part_index))).fetchone()
        revision = head["revision"] + 1
        await connection.execute(
            "INSERT INTO conversation.input_supplements(input_id,part_index,supplement_revision,original_ref,supplement_ref,media,content_digest,deployment_generation) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (input_id, part_index, revision, document(original_ref), document(supplement_ref), document(media), content_digest,
             document(deployment_generation)))
        await self.jobs.enqueue(connection, JobSpec("conversation.supplement_notify",
            f"supplement:{input_id}:{part_index}:{revision}",
            {"input_id": input_id, "part_index": part_index, "supplement_revision": revision},
            ServiceClass.ELASTIC, subject_id=context.actor_id, execution_lane=ExecutionLane.CONTROL))
        return revision

    async def supplement_notify(self, input_id, part_index, supplement_revision):
        async with self.database.transaction(read_only=True) as connection:
            row = await (await connection.execute(
                "SELECT s.*,t.context,t.event_revision,t.event_id FROM conversation.input_supplements s "
                "JOIN conversation.turns t ON t.turn_id=s.input_id WHERE s.input_id=%s AND s.part_index=%s "
                "AND s.supplement_revision=%s", (input_id, part_index, supplement_revision))).fetchone()
        if not row or row["notification_state"] != "pending":
            return
        context = context_from_record(row["context"])
        original_ref, supplement_ref = source_refs((row["original_ref"], row["supplement_ref"]))
        async with self.database.transaction() as connection:
            if row["deployment_generation"] is not None:
                await self.jobs.assert_generation_on(connection, row["deployment_generation"])
            if not await self.sources.validate_persistence(connection, context, (original_ref, supplement_ref)):
                await connection.execute(
                    "UPDATE conversation.input_supplements SET notification_state='withdrawn' "
                    "WHERE input_id=%s AND part_index=%s AND supplement_revision=%s AND notification_state='pending'",
                    (input_id, part_index, supplement_revision))
                return
            await self.memory.supplement_episode(context, episode_id="input:" + input_id,
                event_id=row["event_id"], event_revision=row["event_revision"], part_index=part_index,
                supplement_revision=supplement_revision, original_ref=original_ref, supplement_ref=supplement_ref,
                generation=row["deployment_generation"], connection=connection)
            await connection.execute(
                "UPDATE conversation.input_supplements SET notification_state='notified',notified_at=now() "
                "WHERE input_id=%s AND part_index=%s AND supplement_revision=%s AND notification_state='pending'",
                (input_id, part_index, supplement_revision))

    async def memory_neighborhood(
        self, context, *, cutoff=None, before_id=None, start=None, end=None, event_ids=(), limit=8
    ):
        """Authorized, fixed-cutoff read port for Memory's neighboring conversation material."""
        await self.identity.check_context(context)
        if not context.actor_id or not context.conversation_id:
            return {"items": (), "cutoff": 0, "coverage": Coverage(True)}
        limit = min(32, max(1, int(limit)))
        if len(event_ids)>32 or any(not isinstance(value,str) or len(value)>512 for value in event_ids):
            raise AppError("memory_context_references_invalid")
        if start is not None:
            start = datetime.fromisoformat(start) if isinstance(start, str) else start
            end = datetime.fromisoformat(end) if isinstance(end, str) else end
            if start.tzinfo is None or end is None or end.tzinfo is None or not start < end or (end-start).total_seconds() > 86400:
                raise AppError("memory_context_range_invalid")
        async with self.database.transaction(read_only=True) as connection:
            if cutoff is None:
                head = await (await connection.execute(
                    "SELECT COALESCE(max(id),0) AS cutoff FROM conversation.messages WHERE actor_id=%s AND conversation_id=%s",
                    (context.actor_id, context.conversation_id))).fetchone()
                cutoff = head["cutoff"]
            if type(cutoff) is not int or cutoff < 0:
                raise AppError("invalid_conversation_cutoff")
            rows = await (await connection.execute(
                "SELECT m.*,t.occurred_at,t.context AS original_context,t.producer,t.event_id,t.event_revision "
                "FROM conversation.messages m LEFT JOIN conversation.turns t USING(turn_id) "
                "WHERE m.actor_id=%s AND m.conversation_id=%s AND m.scope_ids<@%s::text[] AND m.id<=%s "
                "AND (%s::bigint IS NULL OR m.id<%s) AND (%s::timestamptz IS NULL OR COALESCE(t.occurred_at,m.created_at)>=%s) "
                "AND (%s::timestamptz IS NULL OR COALESCE(t.occurred_at,m.created_at)<%s) "
                "AND (%s::text[] IS NULL OR t.event_id=ANY(%s::text[])) ORDER BY m.id DESC LIMIT %s",
                (context.actor_id, context.conversation_id, list(context.scope_ids), cutoff,
                 before_id, before_id, start, start, end, end, list(event_ids) or None,list(event_ids),limit+1))).fetchall()
            valid = []
            groups, count = [], 0
            for row in rows[:limit]:
                refs = source_refs(row["source_refs"])
                if groups and count+len(refs)>1800:
                    valid.extend(await self.sources.check_many_on(connection, context, tuple(groups)))
                    groups, count = [], 0
                groups.append(refs)
                count += len(refs)
            if groups:
                valid.extend(await self.sources.check_many_on(connection, context, tuple(groups)))
        rows = await self._supplemented_messages(context, rows)
        items = []
        for row, allowed in reversed(tuple(zip(rows[:limit], valid, strict=True))):
            if allowed:
                items.append({"message_id": row["id"], "turn_id": row["turn_id"],
                    "speaker": row["speaker"], "parts": row["parts"], "source_refs": row["source_refs"],
                    "occurred_at": row["occurred_at"] or row["created_at"],
                    "person_id": (row["original_context"] or {}).get("person_id"),
                    "producer": row["producer"], "event_id": row["event_id"], "revision": row["event_revision"]})
        return {"items": tuple(items), "cutoff": cutoff,
                "coverage": Coverage(len(rows)<=limit and all(valid),
                    missing=() if all(valid) else ("unavailable_conversation_sources",))}

    async def learning_reply(self, context, *, turn_id, expression_id, message_ids=()):
        """Expose one real turn/receipt to Growth without granting private-table access."""
        await self.identity.check(context)
        if (turn_id is not None and not isinstance(turn_id, str) or not isinstance(expression_id, str)
                or not isinstance(message_ids, (tuple, list))
                or any(not isinstance(value, str) for value in message_ids)):
            raise AppError("learning_reply_invalid")
        async with self.database.transaction(read_only=True) as connection:
            if turn_id is None:
                link = await (await connection.execute(
                    "SELECT turn_id FROM conversation.expressions WHERE expression_id=%s AND actor_id=%s AND conversation_id=%s",
                    (expression_id, context.actor_id, context.conversation_id))).fetchone()
                if not link or not link["turn_id"]:
                    return None
                turn_id = link["turn_id"]
            turn = await (await connection.execute(
                "SELECT * FROM conversation.turns WHERE turn_id=%s AND actor_id=%s AND conversation_id=%s",
                (turn_id, context.actor_id, context.conversation_id))).fetchone()
            if not turn or turn["expression_id"] != expression_id:
                return None
            expression = await (await connection.execute(
                "SELECT * FROM conversation.expressions WHERE expression_id=%s AND actor_id=%s AND conversation_id=%s",
                (expression_id, context.actor_id, context.conversation_id))).fetchone()
            inputs = await (await connection.execute(
                "SELECT m.*,t.producer,t.event_id,t.event_revision FROM conversation.messages m "
                "JOIN conversation.turns t ON t.turn_id=m.turn_id WHERE m.actor_id=%s AND m.conversation_id=%s "
                "AND m.scope_ids<@%s::text[] AND m.speaker='user' AND (t.turn_id=%s OR t.merged_into=%s) ORDER BY m.id",
                (context.actor_id, context.conversation_id, list(context.scope_ids), turn_id, turn_id))).fetchall()
            outputs = await (await connection.execute(
                "SELECT * FROM conversation.messages WHERE expression_id=%s AND actor_id=%s AND conversation_id=%s "
                "AND scope_ids<@%s::text[] AND speaker='assistant' ORDER BY id",
                (expression_id, context.actor_id, context.conversation_id, list(context.scope_ids)))).fetchall()
        inputs = await self._supplemented_messages(context, inputs)
        actual = receipt(expression["receipt"]) if expression and expression["receipt"] else None
        actual_ids = tuple(str(value) for value in (actual.result.get("message_ids", ()) if actual else ()))
        if message_ids and not set(message_ids) <= set(actual_ids):
            return None
        refs = unique_refs(source_refs(turn["source_refs"]),
            *(source_refs(item["source_refs"]) for item in (*inputs, *outputs)),
            source_refs(expression["source_refs"]) if expression else ())
        await self.identity.check(context, refs)
        def public_input(item):
            return {"message_id": item["id"], "turn_id": item["turn_id"], "event_id": item["event_id"],
                    "producer": item["producer"], "revision": item["event_revision"],
                    "parts": item["parts"], "source_refs": item["source_refs"]}
        return {"turn_id": turn_id, "expression_id": expression_id, "conversation_id": context.conversation_id,
                "person_id": context.person_id, "scope_ids": tuple(context.scope_ids), "source_refs": refs,
                "input_messages": tuple(public_input(item) for item in inputs),
                "output_messages": tuple({"message_id": item["id"], "parts": item["parts"],
                                          "source_refs": item["source_refs"]} for item in outputs),
                "generated": bool(turn["model_parts"]),
                "delivery_state": actual.delivery if actual else "not_requested", "message_ids": actual_ids}

    async def turn(self, context, turn_id):
        await self.identity.check(context)
        async with self.database.transaction(read_only=True) as connection:
            row = await (await connection.execute("SELECT * FROM conversation.turns WHERE turn_id=%s AND actor_id=%s AND conversation_id=%s", (turn_id, context.actor_id, context.conversation_id))).fetchone()
            if row and row["merged_into"]:
                row = await (await connection.execute("SELECT * FROM conversation.turns WHERE turn_id=%s", (row["merged_into"],))).fetchone()
                turn_id = row["turn_id"]
            links = await (await connection.execute("SELECT * FROM conversation.tool_links WHERE turn_id=%s ORDER BY call_id", (turn_id,))).fetchall()
            selections = await (await connection.execute(
                "SELECT attempt_id,round,selection,token_usage,model_receipt,model_result FROM conversation.context_selections "
                "WHERE turn_id=%s ORDER BY round,created_at", (turn_id,))).fetchall()
            inputs = await (await connection.execute(
                "SELECT turn_id AS input_id,event_id,event_revision FROM conversation.turns "
                "WHERE actor_id=%s AND conversation_id=%s AND (turn_id=%s OR merged_into=%s) ORDER BY sequence",
                (context.actor_id, context.conversation_id, turn_id, turn_id))).fetchall() if row else []
        if not row:
            raise AppError("turn_not_found", status=404)
        await self.identity.check(context, source_refs(row["source_refs"]))
        expression = await self.expression.get(context, row["expression_id"]) if row["expression_id"] else None
        selection = selections[-1] if selections else None
        from .flow import dialogue_flow
        from .input_status import attach_learning_status, input_context_status
        flow = dialogue_flow(row, selections, links, expression, expression)
        input_status = input_context_status(inputs, selections)
        try:
            async with asyncio.timeout(2.0):
                learning = await self.growth.input_learning_status(context, inputs=tuple(inputs))
            attach_learning_status(input_status, learning)
        except Exception:
            for item in input_status:
                item["learning"] = {"state": "unavailable", "revision_state": "not_recorded"}
        manifest = (tuple(item if item.get("selected") else {**item, "source_refs": []}
                          for item in selection["selection"]) if selection else None)
        provider_usage = (selection["model_receipt"] or {}).get("usage") if selection else None
        return DialogueResult(turn_id, row["state"], row["expression_id"], receipt(expression["receipt"]) if expression else None,
            tuple(expression["public_parts"]) if expression else (), tuple(item["action_id"] for item in links),
            (row["model_attempt_id"],) if row["model_attempt_id"] else (), tuple(row["missing"]), row["revision"], row["error_code"],
            manifest, selection["token_usage"] if selection else None, provider_usage, flow, tuple(input_status))

    async def run(self, turn_id):
        from tianshu.runtime.jobs import current_execution_generation
        work_generation = current_execution_generation()
        async with self.database.transaction() as connection:
            if work_generation is not None:
                await self.jobs.assert_generation_on(connection, work_generation)
            row = await (await connection.execute("SELECT * FROM conversation.turns WHERE turn_id=%s", (turn_id,))).fetchone()
            if not row or row["state"] != "queued":
                return
            await connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (f"turn:{row['actor_id']}:{row['conversation_id']}",))
            row = await (await connection.execute("SELECT * FROM conversation.turns WHERE turn_id=%s FOR UPDATE", (turn_id,))).fetchone()
            if not row or row["state"] != "queued":
                return
            if row["deployment_generation"] is not None:
                await self.jobs.assert_generation_on(connection, row["deployment_generation"])
            busy = await (await connection.execute("SELECT count(*) AS count FROM conversation.turns WHERE actor_id=%s AND conversation_id=%s AND state='running' AND turn_id<>%s", (row["actor_id"], row["conversation_id"], turn_id))).fetchone()
            if busy["count"] >= self.collector.policy.max_active_turns:
                return  # The running owner schedules the oldest queued turn when releasing this lane.
            attempt_id = str(uuid4())
            now = datetime.now(UTC)
            # Ordinary collection's timestamp is a local work budget, not a
            # user-authored expiry. A queued turn starts preparation now.
            deadline = _business_deadline(row) or now + timedelta(seconds=CHAT_EXECUTION_SECONDS)
            lease = now + timedelta(seconds=CHAT_EXECUTION_SECONDS + TURN_SETTLEMENT_SECONDS)
            if row.get("autonomous"):
                lease = min(deadline, lease)
            row = await (await connection.execute(
                "UPDATE conversation.turns SET state='running',error_code=NULL,started_at=COALESCE(started_at,now()),model_attempt_id=%s,lease_until=%s,deadline_at=%s "
                "WHERE turn_id=%s RETURNING *", (attempt_id, lease, deadline, turn_id))).fetchone()
            await self.jobs.enqueue(connection, JobSpec("conversation.expire", f"turn-lease:{turn_id}:{attempt_id}",
                {"turn_id": turn_id, "attempt_id": attempt_id}, ServiceClass.INTERACTIVE, lease, row["actor_id"]))
            from .observability import processed_on
            inputs = await (await connection.execute(
                "SELECT turn_id FROM conversation.turns WHERE turn_id=%s OR merged_into=%s ORDER BY sequence", (turn_id, turn_id))).fetchall()
            for item in inputs:
                recorded = await processed_on(self.statistics, connection, input_id=item["turn_id"], actor_id=row["actor_id"],
                                               occurred_at=row["started_at"])
                if self.statistics is not None and not recorded:
                    await self._schedule_statistics_repair(connection)
            await self.jobs.enqueue(connection, JobSpec("conversation.prepare", f"turn-prepare:{turn_id}:{attempt_id}",
                {"turn_id": turn_id, "attempt_id": attempt_id},
                ServiceClass.ELASTIC if row.get("autonomous") else ServiceClass.INTERACTIVE,
                subject_id=row["actor_id"], data_generation=row["deployment_generation"]))

    async def prepare_turn(self, turn_id, attempt_id):
        """Resume preparation for the exact attempt claimed by the durable turn."""
        async with self.database.transaction(read_only=True) as connection:
            row = await (await connection.execute(
                "SELECT * FROM conversation.turns WHERE turn_id=%s AND model_attempt_id=%s AND state='running'",
                (turn_id, attempt_id))).fetchone()
        if not row:
            return
        context = context_from_record(row["context"])
        deferred = False
        try:
            try:
                await self.identity.check(context, source_refs(row["source_refs"]))
                actor = await self.companion.actor(context)
                if not actor["enabled"]:
                    raise AppError("actor_disabled", status=409)
                choices = await self.catalog.discover(context)
            except AppError as error:
                raise_resource_wait(error)
                raise TurnRejected(error.code, error.message, status=error.status, details=error.details) from error
            if row["command"] and row["round"] == 0:
                await self._command(context, row, choices)
            else:
                await self._model_turn(context, row, attempt_id, choices)
        except BaseException as error:
            if is_resource_wait(error):
                deferred = True
                raise
            if isinstance(error, asyncio.CancelledError):
                await self.models.cancel(attempt_id, reason="consumer_interrupted")
            code = getattr(error, "code", "turn_outcome_unknown")
            known = isinstance(error, TurnRejected)
            async with self.database.transaction() as connection:
                if row["deployment_generation"] is not None:
                    await self.jobs.assert_generation_on(connection, row["deployment_generation"])
                await connection.execute("UPDATE conversation.turns SET state=%s,error_code=%s,completed_at=now() WHERE turn_id=%s AND state='running'", ("failed" if known else "unknown", code, turn_id))
            if isinstance(error, asyncio.CancelledError):
                raise
        finally:
            if not deferred:
                await self._next(row["actor_id"], row["conversation_id"])

    async def _context_history(self, context, row, query):
        """Read complete original messages; selection determines their direct dependencies."""
        input_items = input_parts(row["input_parts"])
        reply_ids = {str(value) for part in input_items for value in (
            part.metadata.get("reply_to_event_id"), part.metadata.get("event_id")
        ) if part.kind in {"reply", "reference", "quote"} and value}
        async with self.database.transaction(read_only=True) as connection:
            rows = await (await connection.execute(
                "SELECT m.*,t.producer,t.event_id,t.event_revision,t.merged_into,t.occurred_at "
                "FROM conversation.messages m LEFT JOIN conversation.turns t ON t.turn_id=m.turn_id "
                "WHERE m.actor_id=%s AND m.conversation_id=%s AND m.scope_ids<@%s::text[] AND m.id<=%s "
                "AND (m.id IN (SELECT id FROM conversation.messages WHERE actor_id=%s AND conversation_id=%s "
                "AND scope_ids<@%s::text[] AND id<=%s ORDER BY id DESC LIMIT 30) "
                "OR t.turn_id=%s OR t.merged_into=%s OR t.event_id=ANY(%s::text[])) ORDER BY m.id",
                (context.actor_id, context.conversation_id, list(context.scope_ids), row["message_cutoff"],
                 context.actor_id, context.conversation_id, list(context.scope_ids), row["message_cutoff"],
                 row["turn_id"], row["turn_id"], list(reply_ids)))).fetchall()
        rows = await self._supplemented_messages(context, rows)
        current = [index for index, item in enumerate(rows)
                   if item["turn_id"] == row["turn_id"] or item.get("merged_into") == row["turn_id"]]
        if not current:
            raise AppError("context_current_input_unavailable", status=409)
        required = set(current)
        required.update(index for index, item in enumerate(rows) if item.get("event_id") in reply_ids)
        # Deterministic anaphora needs the immediately preceding exchange. A
        # richer reference is retained explicitly when its event ID is known.
        if reply_ids or any(token in query for token in (
                "刚才", "之前", "上面", "上一", "那个", "这个", "第二", "第一", "刚刚", "那种", "它", "they", "that", "earlier")):
            required.update(range(max(0, current[0] - 2), current[0]))
        result = []
        for index, item in enumerate(rows):
            result.append(ContextMessage(
                self._message(item["speaker"], item["parts"]), source_refs(item["source_refs"]),
                {"message_id": item["id"], "turn_id": item["turn_id"], "producer": item.get("producer"),
                 "event_id": item.get("event_id"), "revision": item.get("event_revision"),
                 "reply_to": [part.get("metadata", {}).get("reply_to_event_id") or part.get("metadata", {}).get("event_id")
                              for part in item["parts"] if part.get("kind") in {"reply", "reference"}],
                 "missing_parts": [{"index": part_index, "kind": part["kind"],
                                    "reason": part.get("metadata", {}).get("error_code", "media_unavailable")}
                                   for part_index, part in enumerate(item["parts"])
                                   if part.get("kind") in {"image", "audio", "video", "file"} and not part.get("media")],
                 "missing_references": [{"index": part_index,
                    "reason": part.get("metadata", {}).get("lookup_unavailable") or "quoted_content_partial"}
                    for part_index, part in enumerate(item["parts"]) if part.get("kind") in {"reply", "reference"}
                    and (part.get("metadata", {}).get("lookup_unavailable") or
                         part.get("metadata", {}).get("quoted_parts_complete") is False)],
                 "speaker": item["speaker"]}, index in required))
        for index, item in enumerate(row["continuation"]):
            result.append(ContextMessage(self._message(item["role"], item["parts"], item.get("native", {})),
                source_refs(row["source_refs"]), {"kind": "tool_continuation", "index": index}, True))
        return tuple(result)

    async def _prepare_model_request(self, context, row, attempt_id, choices):
        actor = await self.companion.actor(context)
        persona = await self.companion.persona(context, actor["persona_id"], actor["persona_revision"]) if actor["persona_id"] else None
        budget = RequestBudget(row["deadline_at"])
        if budget.remaining() <= 0:
            raise AppError("deadline_exceeded", status=408)
        query = "\n".join(part.text for part in input_parts(row["input_parts"]) if part.text)

        async def life_material():
            state, commitments, hint = await asyncio.gather(
                self.life.state(context), self.life.commitments(context, limit=20), self.expression.hint(context))
            refs = unique_refs(tuple(ref for ref in (
                *state["affect"]["source_refs"],
                *(source_refs(state["activity"]["source_refs"]) if state["activity"] else ()),
                *(ref for item in commitments.items for ref in source_refs(item["source_refs"])))))
            material = {"current_activity": state["activity"], "local_timezone": state["timezone"],
                "short_term_feelings": state["affect"]["feelings"],
                "expression_direction": state["affect"]["regulation"]["expression_direction"],
                "commitments": wire(commitments.items), "expression": hint}
            return (ContextContribution("life_and_feelings", json.dumps(wire(material), ensure_ascii=False),
                                        refs, context.scope_ids, 65),)

        async def growth_material():
            return await self.growth.material(context, purpose="conversation", query=query, budget=budget)

        async def group_terms():
            if context.conversation_kind != "group" or not query:
                return ()
            values = await self.memory.group_term_meanings(context, query, limit=8)
            try:
                async with self.database.transaction() as connection:
                    if row["deployment_generation"] is not None:
                        await self.jobs.assert_generation_on(connection, row["deployment_generation"])
                    await self.memory.record_group_term_usage(context, row["event_id"],
                        tuple((value.claim_id, value.revision) for value in values),
                        outcome="matched" if values else "no_match")
            except Exception as error:
                raise_resource_wait(error)
                # Usage accounting cannot turn an available term into a failed read.
                pass
            return tuple(ContextContribution("group_term_meaning", json.dumps(wire(value), ensure_ascii=False),
                tuple(value.source_refs), context.scope_ids, 66, False,
                content_revision=f"group_term:{value.claim_id}:{value.revision}",
                material_id="group_term:" + value.claim_id, semantic_role="current_fact") for value in values)

        async def optional(reader):
            try:
                async with asyncio.timeout(min(2.0, max(budget.remaining() - 0.1, 0.01))):
                    return await reader(), None
            except Exception as error:
                raise_resource_wait(error)
                return (), type(error).__name__

        life_result, growth_result, terms_result, recent = await asyncio.gather(
            optional(life_material), optional(growth_material), optional(group_terms), self._context_history(context, row, query))
        growth_materials = tuple(replace(item, mandatory=True, priority=100)
            if item.semantic_role in {"constraint", "prohibition", "explicit_constraint"} else item
            for item in growth_result[0])
        contributions = tuple(life_result[0]) + growth_materials + tuple(terms_result[0])
        optional_missing = tuple(name for name, result in (("life_context_unavailable", life_result),
            ("growth_context_unavailable", growth_result), ("group_terms_unavailable", terms_result)) if result[1])
        contributions += (ContextContribution("current_input_sources", "", source_refs(row["source_refs"]),
                                              context.scope_ids, 100, True),)
        async with self.database.transaction(read_only=True) as connection:
            previous = await (await connection.execute(
                "SELECT selection FROM conversation.context_selections WHERE turn_id=%s AND round<%s "
                "AND model_receipt->>'dispatch'='submitted' ORDER BY round,created_at",
                (row["turn_id"], row["round"]))).fetchall()
        from .behaviors import unique_behavior_refs
        inherited = unique_behavior_refs(*(self._selected_behavior_refs(item["selection"]) for item in previous))
        contributions += tuple(ContextContribution("prior_behavior_dependency", "", (), context.scope_ids, 100, True,
            content_revision=str(ref.get("semantic_revision")) if ref.get("semantic_revision") is not None else None,
            material_id="prior-behavior:"+str(ref.get("rule_id"))+":"+str(ref.get("revision")),
            semantic_role=ref.get("semantic_role"), behavior_ref=ref) for ref in inherited)
        if row.get("autonomous"):
            contributions += (ContextContribution("group_participation_origin",
                "This is the character's unsolicited contribution to a public group topic, not a direct human command. "
                "Be brief, respect the current group audience, and do not invent a personal request or disclose private context.",
                (), context.scope_ids, 100, True),)
        tools, tool_map = self._tools(choices)
        input_budget = ({"max_input_tokens": BACKGROUND_INPUT_TOKENS + AUTONOMOUS_OUTPUT_RESERVE,
                         "reserved_output_tokens": AUTONOMOUS_OUTPUT_RESERVE} if row.get("autonomous") else {})
        pack = await self.assembler.assemble(context, "chat", query, budget,
            persona_text=persona.prompt_text if persona else "", recent_units=recent, query_is_present=True,
            tools=tools, contributions=contributions, behavior_resolver=self.growth, **input_budget)
        media_missing = tuple(f"media_part_unavailable:{record.get('message_id')}:{part['index']}:{part['reason']}"
            for entry in pack.selection if entry.get("selected") for record in entry.get("records", ())
            for part in record.get("missing_parts", ()))
        reference_missing = tuple(f"reference_unavailable:{record.get('message_id')}:{part['index']}:{part['reason']}"
            for entry in pack.selection if entry.get("selected") for record in entry.get("records", ())
            for part in record.get("missing_references", ()))
        if optional_missing or media_missing or reference_missing:
            pack = replace(pack, missing=tuple(dict.fromkeys((*pack.missing, *optional_missing, *media_missing, *reference_missing))))
        required = {"text"}
        if tools:
            required.add("tools")
        required.update("vision" if part.kind == "image" else part.kind for message in pack.messages
                        for part in message.parts if part.kind in {"image", "audio", "video"})
        if budget.remaining() <= 0:
            raise AppError("deadline_exceeded", status=408)
        request = ModelRequest(row["turn_id"]+":"+str(row["round"]), attempt_id, "chat",
            ServiceClass.ELASTIC if row.get("autonomous") else ServiceClass.INTERACTIVE,
            context, budget, tuple(pack.messages), system=pack.system, tools=tuple(pack.tools),
            required_capabilities=frozenset(required), estimated_input_tokens=pack.token_usage.estimated_tokens,
            source_refs=tuple(pack.source_refs), persona_revision=persona.revision if persona else None,
            execution_timeout_seconds=float(CHAT_EXECUTION_SECONDS),
            business_deadline_at=_business_deadline(row), provider_session_scope="conversation",
            behavior_refs=self._selected_behavior_refs(pack.selection), behavior_purpose="conversation",
            data_generation=row["deployment_generation"])
        return request, pack, tool_map

    async def _model_turn(self, context, row, attempt_id, choices):
        try:
            for retry in range(2):
                request, pack, tool_map = await self._prepare_model_request(context, row, attempt_id, choices)
                try:
                    async with self.database.transaction() as connection:
                        fresh = await lock_material_row(connection, self.sources, context,
                            "SELECT * FROM conversation.turns WHERE turn_id=%s FOR UPDATE",
                            (row["turn_id"],), additional_refs=tuple(pack.source_refs))
                        if not fresh or fresh["state"] != "running" or fresh["model_attempt_id"] != attempt_id:
                            return
                        generation = row["deployment_generation"]
                        if generation is not None:
                            await self.jobs.assert_generation_on(connection, generation)
                        elif fresh["deployment_generation"] is None:
                            generation = await self.jobs.current_generation_on(connection)
                            await connection.execute("UPDATE conversation.turns SET deployment_generation=%s WHERE turn_id=%s",
                                                     (generation, row["turn_id"]))
                        else:
                            return
                        if request.data_generation != generation:
                            request = replace(request, data_generation=generation)
                        frozen = await (await connection.execute(
                            "INSERT INTO conversation.context_selections(attempt_id,turn_id,round,request_digest,selection,token_usage,source_refs,round_snapshot,deployment_generation) "
                            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(attempt_id) DO NOTHING RETURNING request_digest",
                            (attempt_id, row["turn_id"], row["round"], signature(request),
                             document(pack.selection), document(pack.token_usage), document(pack.source_refs),
                             document({"ordered_parts": row["ordered_parts"], "continuation": row["continuation"],
                                       "artifacts": row["artifacts"]}), document(generation)))).fetchone()
                        if not frozen:
                            old = await (await connection.execute(
                                "SELECT request_digest FROM conversation.context_selections WHERE attempt_id=%s",
                                (attempt_id,))).fetchone()
                            if not old or old["request_digest"] != signature(request):
                                raise AppError("context_attempt_conflict", status=409)
                        await self.generation.prepare(f"turn:{row['turn_id']}:{row['round']}", request, connection=connection)
                        await connection.execute(
                            "UPDATE conversation.turns SET state='waiting_external',source_refs=%s,missing=%s,tool_dispatch_complete=false "
                            "WHERE turn_id=%s AND state='running' AND model_attempt_id=%s",
                            (document(pack.source_refs), list(pack.missing), row["turn_id"], attempt_id))
                        await self.jobs.enqueue(connection, JobSpec("conversation.model_submit", "model-submit:"+attempt_id,
                            {"turn_id": row["turn_id"], "attempt_id": attempt_id},
                            ServiceClass.ELASTIC if row.get("autonomous") else ServiceClass.INTERACTIVE,
                            subject_id=context.actor_id, execution_lane=ExecutionLane.CONVERSATION,
                            data_generation=generation))
                        await self.jobs.enqueue(connection, JobSpec("conversation.model_complete", "model-observe:"+attempt_id,
                            {"turn_id": row["turn_id"], "attempt_id": attempt_id}, ServiceClass.INTERACTIVE,
                            datetime.now(UTC)+timedelta(seconds=10), context.actor_id,
                            execution_lane=ExecutionLane.CONVERSATION, data_generation=generation))
                    break
                except AppError as error:
                    raise_resource_wait(error)
                    if retry == 0 and error.code in {"source_unavailable", "revision_conflict"}:
                        continue
                    raise
        except AppError as error:
            raise_resource_wait(error)
            if error.code == "context_behavior_pending":
                async with self.database.transaction() as connection:
                    if row["deployment_generation"] is not None:
                        await self.jobs.assert_generation_on(connection, row["deployment_generation"])
                    changed = await (await connection.execute(
                        "UPDATE conversation.turns SET state='queued',error_code='behavior_pending' "
                        "WHERE turn_id=%s AND state='running' AND model_attempt_id=%s RETURNING actor_id",
                        (row["turn_id"], attempt_id))).fetchone()
                    if changed:
                        await self.jobs.enqueue(connection, JobSpec("conversation.run", f"behavior-turn:{row['turn_id']}:{uuid4()}",
                            {"turn_id": row["turn_id"]}, ServiceClass.INTERACTIVE,
                            datetime.now(UTC)+timedelta(seconds=10), context.actor_id,
                            execution_lane=ExecutionLane.CONVERSATION, data_generation=row["deployment_generation"]))
                return
            # This boundary has not called ModelClient.generate or admitted this attempt.
            raise TurnRejected(error.code, error.message, status=error.status, details=error.details) from error
        # Submission and observation are durable jobs; no model wait owns the
        # conversation worker or the short session lock.
        return

    @staticmethod
    def _selected_behavior_refs(selection):
        from .behaviors import unique_behavior_refs
        return unique_behavior_refs(*(tuple(entry.get("behavior_refs", ())) for entry in selection if entry.get("selected")))

    async def model_submit(self, turn_id, attempt_id):
        async with self.database.transaction(read_only=True) as connection:
            row = await (await connection.execute(
                "SELECT * FROM conversation.turns WHERE turn_id=%s AND model_attempt_id=%s AND state='waiting_external'",
                (turn_id, attempt_id))).fetchone()
            frozen = await (await connection.execute(
                "SELECT * FROM conversation.context_selections WHERE attempt_id=%s", (attempt_id,))).fetchone()
        if not row or not frozen or frozen["consumed_at"] is not None:
            return
        if frozen["consumption_state"] == "unconfirmed":
            await self.models.require_receipt(attempt_id)
            return
        context = context_from_record(row["context"])
        async with self.database.transaction() as connection:
            if frozen["deployment_generation"] is not None:
                await self.jobs.assert_generation_on(connection, frozen["deployment_generation"])
        from .behaviors import behavior_status
        status, _ = await behavior_status(self.growth, context, self._selected_behavior_refs(frozen["selection"]))
        if status == "invalid":
            async with self.database.transaction() as connection:
                if frozen["deployment_generation"] is not None:
                    await self.jobs.assert_generation_on(connection, frozen["deployment_generation"])
                await connection.execute("UPDATE conversation.turns SET state='failed',error_code='behavior_invalid',completed_at=now() "
                    "WHERE turn_id=%s AND model_attempt_id=%s AND state='waiting_external'", (turn_id, attempt_id))
            await self._next(row["actor_id"], row["conversation_id"])
            return
        if status == "pending":
            async with self.database.transaction() as connection:
                if frozen["deployment_generation"] is not None:
                    await self.jobs.assert_generation_on(connection, frozen["deployment_generation"])
                await connection.execute("UPDATE conversation.turns SET error_code='behavior_pending' "
                    "WHERE turn_id=%s AND model_attempt_id=%s AND state='waiting_external'", (turn_id, attempt_id))
            await self._schedule_model_observation(row, attempt_id, submit=True)
            return
        request = await self.generation.request(attempt_id, context)
        if request is None:
            raise AppError("generation_request_not_found", status=404)
        await self.identity.check(context, request.source_refs)
        if frozen["model_receipt"] is not None:
            await self._schedule_model_observation(row, attempt_id)
            return
        async def current_turn(connection):
            if frozen["deployment_generation"] is not None:
                await self.jobs.assert_generation_on(connection, frozen["deployment_generation"])
            current = await (await connection.execute(
                "SELECT state,model_attempt_id,round,source_refs,deployment_generation FROM conversation.turns "
                "WHERE turn_id=%s FOR UPDATE", (turn_id,))).fetchone()
            selected = await (await connection.execute(
                "SELECT consumption_state,consumed_at FROM conversation.context_selections WHERE attempt_id=%s FOR UPDATE",
                (attempt_id,))).fetchone()
            return bool(current and current["state"] == "waiting_external"
                and current["model_attempt_id"] == attempt_id and current["round"] == row["round"]
                and current["deployment_generation"] == row["deployment_generation"]
                and source_refs(current["source_refs"]) == tuple(request.source_refs)
                and selected and selected["consumption_state"] == "idle" and selected["consumed_at"] is None)
        try:
            request, submit = await self.generation.claim(f"turn:{turn_id}:{row['round']}", request, guard=current_turn)
        except Exception as error:
            await self._raise_model_resource(error, attempt_id, frozen["deployment_generation"])
            if not isinstance(error, AppError) or error.code != "attempt_not_found":
                raise
            await self._schedule_model_observation(row, attempt_id)
            return
        if not submit:
            await self._schedule_model_observation(row, attempt_id)
            return
        completion = JobSpec("conversation.model_complete", "model-complete:"+attempt_id,
            {"turn_id": turn_id, "attempt_id": attempt_id}, ServiceClass.INTERACTIVE,
            subject_id=context.actor_id, execution_lane=ExecutionLane.CONVERSATION,
            data_generation=frozen["deployment_generation"])
        try:
            accepted = await self.models.submit(request, completion=completion)
            async with self.database.transaction() as connection:
                if frozen["deployment_generation"] is not None:
                    await self.jobs.assert_generation_on(connection, frozen["deployment_generation"])
                await connection.execute("UPDATE conversation.context_selections SET model_receipt=%s "
                                         "WHERE attempt_id=%s AND model_receipt IS NULL",
                                         (document(accepted), attempt_id))
                await self.generation.confirm(attempt_id, connection=connection)
                await connection.execute("UPDATE conversation.turns SET error_code=NULL "
                    "WHERE turn_id=%s AND model_attempt_id=%s AND error_code=%s",
                    (turn_id, attempt_id, BLOCKED_ROUTE))
                if accepted.dispatch == "submitted":
                    await self._enqueue_example_usage(connection, attempt_id=attempt_id, turn_id=turn_id,
                                                      actor_id=row["actor_id"], outcome="selected")
        except Exception as error:
            await self._raise_model_resource(error, attempt_id, frozen["deployment_generation"])
            if await self.model_admissions.block(row, attempt_id, error):
                return
            # Submission may have reached Models. The next job observes the same
            # attempt and can only re-admit the same unconfirmed frozen identity.
            await self._schedule_model_observation(row, attempt_id)

    async def _schedule_model_observation(self, row, attempt_id, *, submit=False, connection=None):
        task = "conversation.model_submit" if submit else "conversation.model_complete"
        if connection is None:
            async with self.database.transaction() as transaction:
                return await self._schedule_model_observation(row, attempt_id, submit=submit, connection=transaction)
        if row["deployment_generation"] is not None:
            await self.jobs.assert_generation_on(connection, row["deployment_generation"])
        await self.jobs.enqueue(connection, JobSpec(task, f"model-wake:{attempt_id}:{uuid4()}",
            {"turn_id": row["turn_id"], "attempt_id": attempt_id}, ServiceClass.INTERACTIVE,
            datetime.now(UTC)+timedelta(seconds=10), row["actor_id"],
            execution_lane=ExecutionLane.CONVERSATION, data_generation=row["deployment_generation"]))

    async def resume_model_admissions(self, *, limit=32):
        await self.model_admissions.reconcile(limit=limit)

    async def _raise_model_resource(self, error, attempt_id, generation):
        if not is_resource_wait(error):
            return
        if not can_retry_resource_wait(error):
            try:
                async with self.database.transaction() as connection:
                    if generation is not None:
                        await self.jobs.assert_generation_on(connection, generation)
                    await connection.execute("UPDATE conversation.context_selections SET consumption_state='unconfirmed' "
                        "WHERE attempt_id=%s AND consumed_at IS NULL", (attempt_id,))
            except BaseException as observation_error:
                raise error from observation_error
        raise error

    async def _enqueue_example_usage(self, connection, *, attempt_id, turn_id, actor_id, outcome, expression_id=None):
        await self.jobs.enqueue(connection, JobSpec("conversation.example_usage",
            f"example-usage:{attempt_id}:{outcome}:{expression_id or '-'}", {"attempt_id": attempt_id,
            "turn_id": turn_id, "outcome": outcome, "expression_id": expression_id}, ServiceClass.ELASTIC,
            subject_id=actor_id, execution_lane=ExecutionLane.CONTROL))

    async def record_example_usage(self, attempt_id, turn_id, outcome, expression_id=None):
        async with self.database.transaction(read_only=True) as connection:
            row = await (await connection.execute(
                "SELECT s.selection,s.model_receipt,t.context,t.expression_id FROM conversation.context_selections s "
                "JOIN conversation.turns t ON t.turn_id=s.turn_id WHERE s.attempt_id=%s AND s.turn_id=%s",
                (attempt_id, turn_id))).fetchone()
            expression = None
            if expression_id:
                expression = await (await connection.execute(
                    "SELECT receipt FROM conversation.expressions WHERE expression_id=%s AND turn_id=%s",
                    (expression_id, turn_id))).fetchone()
        if not row or not row["model_receipt"] or row["model_receipt"].get("dispatch") != "submitted":
            return
        identifiers = tuple(dict.fromkeys(entry["material_id"][len("growth-example:"):]
            for entry in row["selection"] if entry.get("selected") and entry.get("kind") == "expression_example"
            and isinstance(entry.get("material_id"), str) and entry["material_id"].startswith("growth-example:")))
        if not identifiers:
            return
        if outcome in {"expressed", "delivered"} and row["expression_id"] != expression_id:
            return
        if outcome == "delivered" and (not expression or not expression["receipt"]
                                       or expression["receipt"].get("delivery") != "delivered"):
            return
        context = context_from_record(row["context"])
        await self.growth.record_example_usage(context, identifiers, usage_id=attempt_id, outcome=outcome,
                                                turn_id=turn_id, expression_id=expression_id)

    async def model_complete(self, turn_id, attempt_id):
        async with self.database.transaction(read_only=True) as connection:
            row = await (await connection.execute(
                "SELECT * FROM conversation.turns WHERE turn_id=%s AND model_attempt_id=%s AND state='waiting_external'",
                (turn_id, attempt_id))).fetchone()
            frozen = await (await connection.execute(
                "SELECT * FROM conversation.context_selections WHERE attempt_id=%s", (attempt_id,))).fetchone()
            generation = await (await connection.execute(
                "SELECT submission_confirmed FROM conversation.generation_requests WHERE attempt_id=%s", (attempt_id,))).fetchone()
        if not row or not frozen or frozen["consumed_at"] is not None:
            return
        if frozen["consumption_state"] == "unconfirmed":
            await self.models.require_receipt(attempt_id)
            return
        context = context_from_record(row["context"])
        try:
            result = await self.models.result(attempt_id, context=context)
        except Exception as error:
            await self._raise_model_resource(error, attempt_id, frozen["deployment_generation"])
            if row.get("error_code") == BLOCKED_ROUTE:
                return
            await self._schedule_model_observation(row, attempt_id)
            return
        if result is None:
            if row.get("error_code") == BLOCKED_ROUTE:
                return
            # Missing observation is never a not-started receipt. Only an
            # unconfirmed intent can re-enter Models' idempotent admission.
            confirmed = frozen["model_receipt"] is not None or (generation and generation["submission_confirmed"])
            await self._schedule_model_observation(row, attempt_id, submit=not confirmed)
            return
        async with self.database.transaction() as connection:
            if frozen["deployment_generation"] is not None:
                await self.jobs.assert_generation_on(connection, frozen["deployment_generation"])
            await self.generation.confirm(attempt_id, connection=connection)
            await connection.execute("UPDATE conversation.turns SET error_code=NULL "
                "WHERE turn_id=%s AND model_attempt_id=%s AND error_code=%s",
                (turn_id, attempt_id, BLOCKED_ROUTE))
        if result.receipt.status == "succeeded":
            try:
                original_request = await self.generation.request(attempt_id, context)
                if original_request is None:
                    raise AppError("generation_request_not_found", status=404)
                result = await self.models.reusable_result(attempt_id, context=context,
                    expected_request_digest=submission_digest(original_request),
                    expected_data_generation=frozen["deployment_generation"])
            except Exception as error:
                await self._raise_model_resource(error, attempt_id, frozen["deployment_generation"])
                if not isinstance(error, AppError):
                    await self._schedule_model_observation(row, attempt_id)
                    return
                if error.status not in {401, 403} and error.code not in {
                    "source_unavailable", "result_source_unavailable", "model_attempt_cancelled"
                }:
                    await self._schedule_model_observation(row, attempt_id)
                    return
                async with self.database.transaction() as connection:
                    if frozen["deployment_generation"] is not None:
                        await self.jobs.assert_generation_on(connection, frozen["deployment_generation"])
                    changed = await (await connection.execute(
                        "UPDATE conversation.turns SET state=%s,error_code=%s,completed_at=now() "
                        "WHERE turn_id=%s AND model_attempt_id=%s AND state='waiting_external' RETURNING turn_id",
                        ("cancelled" if error.code == "model_attempt_cancelled" else "failed", error.code,
                         turn_id, attempt_id))).fetchone()
                    if changed:
                        await connection.execute("UPDATE conversation.context_selections SET model_receipt=%s,consumed_at=now(),consumption_state='done' WHERE attempt_id=%s",
                                                 (document(result.receipt), attempt_id))
                if changed:
                    await self._next(row["actor_id"], row["conversation_id"])
                return
        if result.receipt.status not in {"succeeded", "failed", "cancelled"}:
            async with self.database.transaction() as connection:
                if frozen["deployment_generation"] is not None:
                    await self.jobs.assert_generation_on(connection, frozen["deployment_generation"])
                await connection.execute(
                    "UPDATE conversation.context_selections SET model_receipt=%s WHERE attempt_id=%s "
                    "AND consumed_at IS NULL AND (model_receipt IS NULL OR model_receipt->>'status' NOT IN ('succeeded','failed','cancelled'))",
                    (document(result.receipt), attempt_id))
                if result.receipt.dispatch == "submitted":
                    await self._enqueue_example_usage(connection, attempt_id=attempt_id, turn_id=turn_id,
                                                      actor_id=row["actor_id"], outcome="selected")
                if result.receipt.status == "unknown":
                    changed = await (await connection.execute("UPDATE conversation.turns SET state='unknown',error_code=%s "
                        "WHERE turn_id=%s AND model_attempt_id=%s AND state='waiting_external' RETURNING turn_id",
                        (result.receipt.error_code or "model_outcome_unknown", turn_id, attempt_id))).fetchone()
                    if changed:
                        await self.jobs.enqueue(connection, JobSpec("conversation.model_observe_unknown",
                            f"model-unknown:{attempt_id}:{uuid4()}", {"turn_id": turn_id, "attempt_id": attempt_id},
                            ServiceClass.INTERACTIVE, datetime.now(UTC)+timedelta(seconds=60), row["actor_id"],
                            execution_lane=ExecutionLane.CONVERSATION))
            if result.receipt.status == "unknown":
                await self._next(row["actor_id"], row["conversation_id"])
                return
            await self._schedule_model_observation(row, attempt_id)
            return
        from .behaviors import behavior_status
        refs = self._selected_behavior_refs(frozen["selection"])
        status, _ = (await behavior_status(self.growth, context, refs)
                     if result.receipt.status == "succeeded" else ("valid", ()))
        if status == "pending":
            async with self.database.transaction() as connection:
                if frozen["deployment_generation"] is not None:
                    await self.jobs.assert_generation_on(connection, frozen["deployment_generation"])
                await connection.execute("UPDATE conversation.turns SET error_code='behavior_pending' "
                    "WHERE turn_id=%s AND model_attempt_id=%s AND state='waiting_external'", (turn_id, attempt_id))
            await self._schedule_model_observation(row, attempt_id)
            return
        if status == "invalid":
            async with self.database.transaction() as connection:
                if frozen["deployment_generation"] is not None:
                    await self.jobs.assert_generation_on(connection, frozen["deployment_generation"])
                await connection.execute("UPDATE conversation.turns SET state='failed',error_code='behavior_invalid',completed_at=now() "
                    "WHERE turn_id=%s AND model_attempt_id=%s AND state='waiting_external'", (turn_id, attempt_id))
                await connection.execute("UPDATE conversation.context_selections SET model_receipt=%s,model_result=%s,consumed_at=now(),consumption_state='done' WHERE attempt_id=%s",
                    (document(result.receipt), document(result), attempt_id))
                if result.receipt.dispatch == "submitted":
                    await self._enqueue_example_usage(connection, attempt_id=attempt_id, turn_id=turn_id,
                                                      actor_id=row["actor_id"], outcome="selected")
            await self._next(row["actor_id"], row["conversation_id"])
            return
        source_invalid = False
        async with self.database.transaction() as connection:
            if frozen["deployment_generation"] is not None:
                await self.jobs.assert_generation_on(connection, frozen["deployment_generation"])
            if not await self.sources.validate_persistence(connection, context, source_refs(frozen["source_refs"])):
                source_invalid = True
                await connection.execute("UPDATE conversation.turns SET state='failed',error_code='source_unavailable',completed_at=now() "
                    "WHERE turn_id=%s AND model_attempt_id=%s AND state='waiting_external'", (turn_id, attempt_id))
                await connection.execute("UPDATE conversation.context_selections SET model_receipt=%s,consumed_at=now(),consumption_state='done' WHERE attempt_id=%s",
                                         (document(result.receipt), attempt_id))
            else:
                current = await (await connection.execute(
                    "SELECT state,model_attempt_id FROM conversation.turns WHERE turn_id=%s FOR UPDATE", (turn_id,))).fetchone()
                selected = await (await connection.execute(
                    "SELECT consumption_state,consumed_at FROM conversation.context_selections WHERE attempt_id=%s FOR UPDATE",
                    (attempt_id,))).fetchone()
                if (not current or current["state"] != "waiting_external" or current["model_attempt_id"] != attempt_id
                        or not selected or selected["consumed_at"] is not None or selected["consumption_state"] != "idle"):
                    return
                claimed = await (await connection.execute(
                    "UPDATE conversation.turns SET state='running',error_code=NULL,lease_until=now()+interval '5 minutes' "
                    "WHERE turn_id=%s AND model_attempt_id=%s AND state='waiting_external' RETURNING *",
                    (turn_id, attempt_id))).fetchone()
                if not claimed:
                    return
                await connection.execute("UPDATE conversation.context_selections SET model_receipt=%s,model_result=%s,consumption_state='running' WHERE attempt_id=%s",
                                         (document(result.receipt), document(result), attempt_id))
                if result.receipt.dispatch == "submitted":
                    await self._enqueue_example_usage(connection, attempt_id=attempt_id, turn_id=turn_id,
                                                      actor_id=row["actor_id"], outcome="selected")
                await self.jobs.enqueue(connection, JobSpec("conversation.model_consume_expire", f"model-consume:{attempt_id}:{uuid4()}",
                    {"turn_id": turn_id, "attempt_id": attempt_id}, ServiceClass.INTERACTIVE, claimed["lease_until"],
                    row["actor_id"], execution_lane=ExecutionLane.CONVERSATION))
        if source_invalid:
            await self._next(row["actor_id"], row["conversation_id"])
            return
        from types import SimpleNamespace
        pack = SimpleNamespace(source_refs=source_refs(frozen["source_refs"]), missing=tuple(claimed["missing"]),
                               behavior_refs=refs, selection=frozen["selection"], deployment_generation=frozen["deployment_generation"])
        snapshot = frozen["round_snapshot"]
        claimed.update(ordered_parts=snapshot["ordered_parts"], continuation=snapshot["continuation"],
                       artifacts=snapshot["artifacts"])
        consumed = False
        deferred = False
        try:
            choices = await self.catalog.discover(context)
            _, tool_map = self._tools(choices)
            await self._consume_model_result(context, claimed, attempt_id, pack, tool_map, result)
            consumed = True
        except Exception as error:
            if is_resource_wait(error):
                deferred = True
                if can_retry_resource_wait(error):
                    async with self.database.transaction() as connection:
                        if frozen["deployment_generation"] is not None:
                            await self.jobs.assert_generation_on(connection, frozen["deployment_generation"])
                        reset = await (await connection.execute("UPDATE conversation.turns SET state='waiting_external',error_code=NULL,lease_until=NULL "
                            "WHERE turn_id=%s AND model_attempt_id=%s AND state='running' RETURNING turn_id", (turn_id, attempt_id))).fetchone()
                        if reset:
                            await connection.execute("UPDATE conversation.context_selections SET consumption_state='idle' "
                                "WHERE attempt_id=%s AND consumed_at IS NULL", (attempt_id,))
                raise
            async with self.database.transaction() as connection:
                if frozen["deployment_generation"] is not None:
                    await self.jobs.assert_generation_on(connection, frozen["deployment_generation"])
                await connection.execute("UPDATE conversation.turns SET state=%s,error_code=%s,completed_at=now() "
                    "WHERE turn_id=%s AND model_attempt_id=%s AND state='running'",
                    ("failed" if isinstance(error, TurnRejected) else "unknown",
                     getattr(error, "code", "turn_outcome_unknown"), turn_id, attempt_id))
            consumed = True
        finally:
            if consumed:
                async with self.database.transaction() as connection:
                    if frozen["deployment_generation"] is not None:
                        await self.jobs.assert_generation_on(connection, frozen["deployment_generation"])
                    await connection.execute("UPDATE conversation.context_selections SET consumed_at=now(),consumption_state='done' WHERE attempt_id=%s", (attempt_id,))
            if not deferred:
                await self._next(row["actor_id"], row["conversation_id"])

    async def model_consume_expire(self, turn_id, attempt_id):
        async with self.database.transaction() as connection:
            selected = await (await connection.execute(
                "SELECT deployment_generation,consumption_state FROM conversation.context_selections WHERE attempt_id=%s", (attempt_id,))).fetchone()
            if not selected:
                return
            if selected["deployment_generation"] is not None:
                await self.jobs.assert_generation_on(connection, selected["deployment_generation"])
            candidate = await (await connection.execute(
                "SELECT * FROM conversation.turns WHERE turn_id=%s AND model_attempt_id=%s "
                "AND state='running' AND lease_until<=now() FOR UPDATE", (turn_id, attempt_id))).fetchone()
            if not candidate:
                return
            selected = await (await connection.execute(
                "SELECT consumption_state FROM conversation.context_selections WHERE attempt_id=%s FOR UPDATE",
                (attempt_id,))).fetchone()
            row, uncertain = None, None
            if selected and selected["consumption_state"] in {"running", "unconfirmed"}:
                await connection.execute("UPDATE conversation.context_selections SET consumption_state='unconfirmed' "
                                         "WHERE attempt_id=%s AND consumed_at IS NULL", (attempt_id,))
                uncertain = candidate
            elif selected and selected["consumption_state"] == "idle":
                row = await (await connection.execute(
                    "UPDATE conversation.turns SET state='waiting_external' WHERE turn_id=%s AND model_attempt_id=%s "
                    "AND state='running' AND lease_until<=now() RETURNING *", (turn_id, attempt_id))).fetchone()
        if row:
            await self._schedule_model_observation(row, attempt_id)
        elif uncertain:
            # A lost consumer may have crossed an external boundary. Observe
            # its original model receipt; do not restart actions or delivery.
            await self.models.require_receipt(attempt_id)

    async def model_observe_unknown(self, turn_id, attempt_id):
        """Observe the original uncertain attempt without initiating an old reply."""
        async with self.database.transaction(read_only=True) as connection:
            row = await (await connection.execute("SELECT * FROM conversation.turns WHERE turn_id=%s "
                "AND model_attempt_id=%s AND state='unknown'", (turn_id, attempt_id))).fetchone()
            frozen = await (await connection.execute("SELECT * FROM conversation.context_selections WHERE attempt_id=%s",
                                                     (attempt_id,))).fetchone()
        if not row or not frozen:
            return
        context = context_from_record(row["context"])
        try:
            result = await self.models.result(attempt_id, context=context)
        except Exception as error:
            raise_resource_wait(error)
            result = None
        terminal = result is not None and result.receipt.status in {"succeeded", "failed", "cancelled"}
        async with self.database.transaction() as connection:
            if frozen["deployment_generation"] is not None:
                await self.jobs.assert_generation_on(connection, frozen["deployment_generation"])
            if terminal:
                if not await self.sources.validate_persistence(connection, context, source_refs(frozen["source_refs"])):
                    return
                await connection.execute("UPDATE conversation.context_selections SET model_receipt=%s,model_result=%s,consumed_at=now(),consumption_state='done' WHERE attempt_id=%s",
                                         (document(result.receipt), document(result), attempt_id))
                if result.receipt.dispatch == "submitted":
                    await self._enqueue_example_usage(connection, attempt_id=attempt_id, turn_id=turn_id,
                                                      actor_id=row["actor_id"], outcome="selected")
                await connection.execute("UPDATE conversation.turns SET error_code=%s WHERE turn_id=%s AND model_attempt_id=%s AND state='unknown'",
                    ("model_late_"+result.receipt.status+"_unexpressed", turn_id, attempt_id))
            else:
                await self.jobs.enqueue(connection, JobSpec("conversation.model_observe_unknown",
                    f"model-unknown:{attempt_id}:{uuid4()}", {"turn_id": turn_id, "attempt_id": attempt_id},
                    ServiceClass.INTERACTIVE, datetime.now(UTC)+timedelta(seconds=60), row["actor_id"],
                    execution_lane=ExecutionLane.CONVERSATION))

    async def _consume_model_result(self, context, row, attempt_id, pack, tool_map, result):
        result_parts = tuple(replace(part, call_id=part.call_id or f"call_r{row['round']}_p{index}")
            if part.kind == "tool_call" else part for index, part in enumerate(result.parts))
        try:
            await self.identity.check(context, tuple(pack.source_refs))
            async with self.database.transaction() as connection:
                if pack.deployment_generation is not None:
                    await self.jobs.assert_generation_on(connection, pack.deployment_generation)
                fresh = await lock_material_row(connection,self.sources,context,"SELECT * FROM conversation.turns WHERE turn_id=%s FOR UPDATE",(row["turn_id"],),additional_refs=tuple(pack.source_refs))
                if fresh["state"] != "running":
                    return
                if result.receipt.error_code == "result_source_unavailable" or not await self.sources.validate_persistence(connection,context,tuple(pack.source_refs)):
                    await connection.execute("UPDATE conversation.turns SET state='failed',error_code='result_source_unavailable',model_parts='[]',model_native=NULL,continuation='[]',ordered_parts='[]',completed_at=now() WHERE turn_id=%s",(row["turn_id"],))
                    return
                await connection.execute("UPDATE conversation.turns SET model_parts=%s,model_native=%s,source_refs=%s,missing=%s WHERE turn_id=%s", (document(result_parts), document(result.native_response), document(pack.source_refs), list(pack.missing), row["turn_id"]))
        except AppError as error:
            raise_resource_wait(error)
            # A local refusal to persist does not change the model's actual execution receipt.
            raise TurnRejected(error.code, error.message, status=error.status, details=error.details) from error
        if result.receipt.status != "succeeded":
            error_type = TurnRejected if result.receipt.status in {"failed", "cancelled"} else AppError
            raise error_type(result.receipt.error_code or "model_"+result.receipt.status,
                             details={"attempt_id": result.receipt.attempt_id,
                                      "status": result.receipt.status, "dispatch": result.receipt.dispatch})
        calls = tuple(part for part in result_parts if part.kind == "tool_call")
        ordered = list(row["ordered_parts"])
        for index, part in enumerate(result_parts):
            if part.native.get("thought"):
                continue
            if part.kind == "tool_call":
                ordered.append({"kind": "tool_slot", "call_id": f"{row['round']}:{part.call_id}"})
            elif part.kind == "text" and part.text:
                ordered.append({"kind": "text", "text": part.text})
            elif part.kind in {"image", "audio", "video"} and part.media:
                ordered.append({"kind": part.kind, "media": wire(part.media)})
        async with self.database.transaction() as connection:
            if pack.deployment_generation is not None:
                await self.jobs.assert_generation_on(connection, pack.deployment_generation)
            if not await self.sources.validate_persistence(connection,context,tuple(pack.source_refs)):
                raise TurnRejected("source_unavailable",status=409)
            await connection.execute("UPDATE conversation.turns SET ordered_parts=%s,tool_dispatch_complete=false WHERE turn_id=%s AND state='running'", (document(ordered), row["turn_id"]))
        if calls:
            if row["round"] >= 8 or len(calls)>8:
                raise AppError("tool_round_limit", status=409)
            from tianshu.models.continuation import assistant_message
            assistant = assistant_message(result, parts=result_parts)
            continuation = list(row["continuation"])+[{"role": assistant.role, "parts": wire(assistant.parts),
                "native": assistant.native}]
            async with self.database.transaction() as connection:
                if pack.deployment_generation is not None:
                    await self.jobs.assert_generation_on(connection, pack.deployment_generation)
                if not await self.sources.validate_persistence(connection,context,tuple(pack.source_refs)):
                    raise TurnRejected("source_unavailable",status=409)
                await connection.execute("UPDATE conversation.turns SET continuation=%s WHERE turn_id=%s AND state='running'", (document(continuation), row["turn_id"]))
            for index, call in enumerate(result_parts):
                if call.kind != "tool_call":
                    continue
                definition = tool_map.get(call.name)
                if not definition:
                    raise TurnRejected("capability_unavailable", status=409)
                await self._invoke(context, row, call.call_id or f"round:{row['round']}:{index}", definition,
                                   call.arguments or {}, tuple(pack.source_refs), behavior_refs=pack.behavior_refs,
                                   deployment_generation=pack.deployment_generation)
            async with self.database.transaction() as connection:
                if pack.deployment_generation is not None:
                    await self.jobs.assert_generation_on(connection, pack.deployment_generation)
                await connection.execute("UPDATE conversation.turns SET tool_dispatch_complete=true WHERE turn_id=%s", (row["turn_id"],))
            await self._await_tools(context, row["turn_id"], deployment_generation=pack.deployment_generation)
            return
        public = tuple(part for part in ordered if part["kind"] != "tool_slot")
        if not public:
            async with self.database.transaction() as connection:
                if pack.deployment_generation is not None:
                    await self.jobs.assert_generation_on(connection, pack.deployment_generation)
                await connection.execute("UPDATE conversation.turns SET state='completed',completed_at=now() WHERE turn_id=%s AND state='running'", (row["turn_id"],))
            return
        expression_id = "turn:"+row["turn_id"]
        # The turn is attached before dispatch, so an unknown browser/QQ delivery stays visible.
        async with self.database.transaction() as connection:
            if pack.deployment_generation is not None:
                await self.jobs.assert_generation_on(connection, pack.deployment_generation)
            fresh = await (await connection.execute("SELECT state FROM conversation.turns WHERE turn_id=%s FOR UPDATE", (row["turn_id"],))).fetchone()
            if fresh["state"] != "running":
                return
            await connection.execute("UPDATE conversation.turns SET expression_id=%s WHERE turn_id=%s", (expression_id, row["turn_id"]))
            used_attempts = await (await connection.execute(
                "SELECT attempt_id FROM conversation.context_selections WHERE turn_id=%s AND model_receipt->>'dispatch'='submitted'",
                (row["turn_id"],))).fetchall()
            for used in used_attempts:
                await self._enqueue_example_usage(connection, attempt_id=used["attempt_id"], turn_id=row["turn_id"],
                                                  actor_id=context.actor_id, outcome="expressed", expression_id=expression_id)
        output_artifacts = artifacts(row["artifacts"])
        expression_refs = unique_refs(tuple(ref for ref in (*pack.source_refs, *(ref for item in output_artifacts for ref in item.source_refs))))
        delivered = await self.expression.send(context, ExpressionPlan(expression_id, context.actor_id, context.conversation_id,
            public, expression_refs, OriginKind.ACTOR, turn_id=row["turn_id"], artifacts=output_artifacts,
            behavior_refs=tuple(pack.behavior_refs)),
            service_class=ServiceClass.ELASTIC if row.get("autonomous") else ServiceClass.INTERACTIVE)
        state = expression_turn_state(delivered.state)
        async with self.database.transaction() as connection:
            if pack.deployment_generation is not None:
                await self.jobs.assert_generation_on(connection, pack.deployment_generation)
            await connection.execute(
                "UPDATE conversation.turns SET state=%s,completed_at=CASE WHEN %s='waiting_expression' "
                "THEN NULL ELSE now() END WHERE turn_id=%s AND state='running'",
                (state, state, row["turn_id"]))

    @staticmethod
    def _message(role, parts, native=None):
        public_role = "assistant" if role == "assistant" else "tool" if role == "tool" else "user"
        converted = []
        for item in parts:
            if item["kind"] in {"image", "audio", "video", "file"} and not item.get("media"):
                metadata = item.get("metadata") or {}
                converted.append(ModelPart("text", text=json.dumps({
                    "kind": item["kind"], "availability": "unavailable",
                    "reason": metadata.get("error_code", "media_unavailable"),
                    "instruction": "原附件未取得，不能推测其内容。",
                }, ensure_ascii=False)))
                continue
            if item["kind"] in {"text", "image", "audio", "video", "tool_call", "tool_result"}:
                if item["kind"] in {"audio","image","video"} and item.get("metadata",{}).get("analysis_media"):
                    item={**item,"media":item["metadata"]["analysis_media"]}
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

    async def _input_refs(self, context, turn_id):
        async with self.database.transaction(read_only=True) as connection:
            originals=await (await connection.execute(
                "SELECT original_source_refs FROM conversation.turns WHERE actor_id=%s AND conversation_id=%s "
                "AND (turn_id=%s OR merged_into=%s) ORDER BY sequence",
                (context.actor_id,context.conversation_id,turn_id,turn_id))).fetchall()
        return unique_refs(*(source_refs(item["original_source_refs"]) for item in originals))

    async def _invoke(self, context, row, call_id, definition, arguments, refs, *, behavior_refs=(), deployment_generation=None):
        provider_call_id = call_id
        business_deadline = _business_deadline(row)
        if definition.id=="images.generate":
            window = {"due_at": row["received_at"].isoformat()}
            if business_deadline is not None:
                window["expires_at"] = business_deadline.isoformat()
            arguments={**arguments,"target_ref":context.conversation_id,
                "delivery_kind":arguments.get("delivery_kind") or ("create_only" if arguments.get("share") is False else "requested_delivery"),
                "window":arguments.get("window") or window,
                "dedup_key":f"turn-photo:{row['turn_id']}:{row['round']}:{call_id}"}
        call_id = f"{row['round']}:{provider_call_id}"
        action_id = str(uuid5(NAMESPACE_URL, f"tool:{row['turn_id']}:{call_id}"))
        async with self.database.transaction() as connection:
            if deployment_generation is not None:
                await self.jobs.assert_generation_on(connection, deployment_generation)
            if not await self.sources.validate_persistence(connection,context,refs):
                raise TurnRejected("source_unavailable",status=409)
            unresolved = await (await connection.execute("SELECT action_id FROM conversation.tool_links WHERE turn_id=%s AND capability_id=%s AND arguments_hash=%s AND state='unknown' AND action_id<>%s", (row["turn_id"], definition.id, signature(arguments), action_id))).fetchone()
            if unresolved:
                raise AppError("original_action_unresolved", status=409, details={"action_id": unresolved["action_id"]})
            tool_name = definition.model_tool["name"] if definition.model_tool else "declared_command"
            await connection.execute("INSERT INTO conversation.tool_links(turn_id,call_id,action_id,capability_id,tool_name,provider_call_id,arguments_hash,background,state) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'queued') ON CONFLICT DO NOTHING", (row["turn_id"], call_id, action_id, definition.id, tool_name, provider_call_id, signature(arguments),definition.long_running))
            original = await (await connection.execute(
                "SELECT action_id,capability_id,arguments_hash FROM conversation.tool_links WHERE turn_id=%s AND call_id=%s",
                (row["turn_id"], call_id))).fetchone()
            if not original or (original["action_id"], original["capability_id"], original["arguments_hash"]) != (action_id, definition.id, signature(arguments)):
                raise AppError("action_identity_conflict", status=409)
        current_input_refs=() if row.get("autonomous") else await self._input_refs(context,row["turn_id"])
        action = Action(action_id, f"turn:{row['turn_id']}:{call_id}", definition.id, context.actor_id, context,
            arguments, context.origin_kind, refs, expression_id="turn:"+row["turn_id"], deadline_at=business_deadline,
            current_input_refs=current_input_refs)
        # Observe an already registered action, including an unknown result. Only
        # a missing local ledger entry permits registration of this same action.
        try:
            current = await self.actions.get(action_id, context)
        except AppError as error:
            raise_resource_wait(error)
            if error.status != 404:
                raise
            result = receipt(await self.actions.invoke(action, background=True,
                service_class=ServiceClass.ELASTIC if row.get("autonomous") else ServiceClass.INTERACTIVE,
                behavior_refs=tuple(behavior_refs), behavior_purpose="conversation"))
        else:
            result = receipt(current["receipt"])
        if result is None:
            raise RecoveryWait("action_receipt_pending", datetime.now(UTC)+timedelta(seconds=5),
                               dependency="action:"+action_id)
        await self._tool_receipt(context, result)

    async def _command(self, context, row, choices):
        name, _, raw = row["command"].strip().partition(" ")
        definition = next((choice["definition"] for choice in choices if choice["can_execute"] and choice["definition"].public_command
            and name in {choice["definition"].public_command["name"], *choice["definition"].public_command.get("aliases", ())}), None)
        if not definition:
            raise TurnRejected("command_unknown", status=404)
        try:
            arguments = json.loads(raw) if raw else {}
        except ValueError:
            raise TurnRejected("command_arguments_invalid") from None
        tool_name = definition.model_tool["name"] if definition.model_tool else "declared_command"
        async with self.database.transaction() as connection:
            if not await self.sources.validate_persistence(connection,context,source_refs(row["source_refs"])):
                raise TurnRejected("source_unavailable",status=409)
            continuation = list(row["continuation"])
            if not any(part.get("kind") == "tool_call" and part.get("call_id") == "command"
                       for message in continuation for part in message.get("parts", ())):
                continuation.append({"role": "assistant", "parts": [wire(ModelPart("tool_call", call_id="command", name=tool_name, arguments=arguments))]})
            await connection.execute("UPDATE conversation.turns SET continuation=%s,ordered_parts=%s,tool_dispatch_complete=false WHERE turn_id=%s", (document(continuation), document([{"kind": "tool_slot", "call_id": "0:command"}]), row["turn_id"]))
        await self._invoke(context, row, "command", definition, arguments, source_refs(row["source_refs"]))
        async with self.database.transaction() as connection:
            await connection.execute("UPDATE conversation.turns SET tool_dispatch_complete=true WHERE turn_id=%s", (row["turn_id"],))
        await self._await_tools(context, row["turn_id"])

    async def _tool_receipt(self, context, result, result_revision=None, *, result_fence=None):
        if result_revision is None:
            current = await self.actions.get(result.action_id, context)
            result, result_revision = receipt(current["receipt"]), current["result_revision"]
        if result is None:
            return
        if result_fence is None and result.action_id:
            if self.actions is None:
                raise AppError("action_head_unavailable", status=503)
            from .rbi_result_fence import action_result_fence
            result, result_revision, result_fence = await action_result_fence(
                self.database, self.jobs, self.actions, context, result, result_revision=result_revision)
        refs=unique_refs(tuple(ref for ref in (*source_refs(result.result.get("source_refs",())),*(ref for item in result.artifacts for ref in item.source_refs))),
                         getattr(result_fence, "source_refs", ()))
        async with self.database.transaction() as connection:
            valid=await self.sources.validate_persistence(connection,context,refs)
            if result_fence:
                if not valid:
                    raise AppError("source_unavailable", status=403)
                await result_fence(connection)
            link = await (await connection.execute("SELECT l.*,t.context,t.state AS turn_state FROM conversation.tool_links l JOIN conversation.turns t ON t.turn_id=l.turn_id WHERE action_id=%s AND t.actor_id=%s AND t.conversation_id=%s FOR UPDATE OF l", (result.action_id, context.actor_id, context.conversation_id))).fetchone()
            if not link:
                return
            if result_revision <= link["result_revision"]:
                return
            if not valid:
                result=replace(result,artifacts=(),result={},error_code="result_source_unavailable")
            state = result.execution
            await connection.execute("UPDATE conversation.tool_links SET state=%s,last_receipt_id=%s,result=%s,result_revision=%s WHERE action_id=%s", (state, result.receipt_id, document(result), result_revision, result.action_id))

    async def _await_tools(self, context, turn_id, *, deployment_generation=None, result_fence=None):
        async def apply():
            return await self._await_tools_once(context, turn_id, deployment_generation=deployment_generation,
                                                result_fence=result_fence)
        return await retry_material_snapshot(apply)

    async def _await_tools_once(self, context, turn_id, *, deployment_generation=None, result_fence=None):
        async with self.database.transaction() as connection:
            if deployment_generation is not None:
                await self.jobs.assert_generation_on(connection, deployment_generation)
            snapshots = await (await connection.execute("SELECT * FROM conversation.tool_links WHERE turn_id=%s AND consumed=false ORDER BY call_id", (turn_id,))).fetchall()
            dependencies=[]
            fences = {}
            if result_fence:
                fences[getattr(result_fence, "action_id", "")] = result_fence
            for item in snapshots:
                observed=receipt(item["result"])
                if observed:
                    dependencies.extend(source_refs(observed.result.get("source_refs",())))
                    dependencies.extend(ref for artifact in observed.artifacts for ref in artifact.source_refs)
                    if observed.action_id and observed.action_id not in fences:
                        if self.actions is None:
                            raise AppError("action_head_unavailable", status=503)
                        from .rbi_result_fence import action_result_fence
                        _, _, fence = await action_result_fence(self.database, self.jobs, self.actions,
                            context, observed, result_revision=item["result_revision"])
                        if fence:
                            fences[observed.action_id] = fence
            if fences:
                ordered_fences = sorted(
                    (item for fence in fences.values() for item in getattr(fence, "components", (fence,))),
                    key=lambda fence: getattr(fence, "action_id", ""))
                snapshot = await (await connection.execute(
                    "SELECT * FROM conversation.turns WHERE turn_id=%s", (turn_id,))).fetchone()
                refs = unique_refs(source_refs(snapshot.get("source_refs", ())) if snapshot else (),
                                   tuple(dependencies), *(fence.source_refs for fence in ordered_fences))
                if not await self.sources.validate_persistence(connection, context, refs):
                    raise AppError("source_unavailable", status=403)
                for fence in ordered_fences:
                    await fence(connection)
                row = await (await connection.execute(
                    "SELECT * FROM conversation.turns WHERE turn_id=%s FOR UPDATE", (turn_id,))).fetchone()
                if signature(snapshot) != signature(row):
                    raise MaterialSnapshotChanged(row.get("revision") if row else None)
            else:
                row = await lock_material_row(connection,self.sources,context,"SELECT * FROM conversation.turns WHERE turn_id=%s FOR UPDATE",(turn_id,),additional_refs=unique_refs(tuple(dependencies)))
            if row["state"] not in {"running", "waiting_external"}:
                return
            if not row["tool_dispatch_complete"]:
                return
            links = await (await connection.execute("SELECT * FROM conversation.tool_links WHERE turn_id=%s AND consumed=false ORDER BY call_id FOR UPDATE", (turn_id,))).fetchall()
            if signature(links)!=signature(snapshots):
                raise MaterialSnapshotChanged(row["revision"])
            if not links:
                return
            if any(link["state"] in {"queued", "pending", "running"} and not link["background"] for link in links):
                await connection.execute("UPDATE conversation.turns SET state='waiting_external' WHERE turn_id=%s", (turn_id,))
                await self.jobs.enqueue(connection, JobSpec("conversation.reconcile", f"turn-reconcile:{turn_id}:{row['round']}:0", {"turn_id": turn_id, "sequence": 0}, ServiceClass.INTERACTIVE, scheduled_at=datetime.now(UTC)+timedelta(seconds=15), subject_id=context.actor_id))
                return
            continuation = list(row["continuation"])
            ordered = list(row["ordered_parts"])
            output_artifacts = list(row["artifacts"])
            result_refs = list(source_refs(row["source_refs"]))
            explicit_expressions = []
            for link in links:
                result = receipt(link["result"])
                result_refs.extend(source_refs(result.result.get("source_refs", ())))
                result_refs.extend(ref for item in result.artifacts for ref in item.source_refs)
                explicit_expressions.extend(result.result.get("explicit_expression_ids", ()))
                # Unknown is a useful tool result, never an instruction to submit the action again.
                continuation.append({"role": "tool", "parts": [wire(ModelPart("tool_result", call_id=link["provider_call_id"], name=link["tool_name"], result=wire(result)))]})
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
            merged_refs = unique_refs(tuple(ref for ref in result_refs))
            if not await self.sources.validate_persistence(connection,context,merged_refs):
                await connection.execute("UPDATE conversation.turns SET state='failed',error_code='result_source_unavailable',continuation='[]',ordered_parts='[]',artifacts='[]' WHERE turn_id=%s",(turn_id,))
                return
            await connection.execute("UPDATE conversation.turns SET state='queued',continuation=%s,ordered_parts=%s,artifacts=%s,source_refs=%s,round=%s WHERE turn_id=%s", (document(continuation), document(ordered), document(output_artifacts), document(merged_refs), next_round, turn_id))
            await self.jobs.enqueue(connection, JobSpec("conversation.run", f"turn:{turn_id}:{next_round}", {"turn_id": turn_id}, ServiceClass.ELASTIC if row.get("autonomous") else ServiceClass.INTERACTIVE, subject_id=context.actor_id))

    async def on_result(self, context, result, *, result_revision=None, result_fence=None):
        result = receipt(result)
        await self.identity.check(context)
        if result_fence is None and result and result.action_id:
            if self.actions is None:
                raise AppError("action_head_unavailable", status=503)
            from .rbi_result_fence import action_result_fence
            result, result_revision, result_fence = await action_result_fence(
                self.database, self.jobs, self.actions, context, result, result_revision=result_revision,
                refresh=result_revision is None)
        await self._tool_receipt(context, result, result_revision, result_fence=result_fence)
        async with self.database.transaction(read_only=True) as connection:
            link = await (await connection.execute("SELECT turn_id FROM conversation.tool_links WHERE action_id=%s", (result.action_id,))).fetchone()
        if link:
            await self._await_tools(context, link["turn_id"], result_fence=result_fence)

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
            business_deadline = _business_deadline(row)
            if fresh["state"] == "waiting_external" and (business_deadline is None or datetime.now(UTC)<business_deadline):
                await self.jobs.enqueue(connection, JobSpec("conversation.reconcile", f"turn-reconcile:{turn_id}:{row['round']}:{sequence+1}", {"turn_id": turn_id, "sequence": sequence+1}, ServiceClass.INTERACTIVE, scheduled_at=datetime.now(UTC)+timedelta(seconds=15), subject_id=context.actor_id))
            elif fresh["state"] == "waiting_external":
                await connection.execute("UPDATE conversation.turns SET state='unknown',error_code='action_wait_deadline' WHERE turn_id=%s", (turn_id,))

    async def _next(self, actor_id, conversation_id):
        async with self.database.transaction() as connection:
            row = await (await connection.execute("SELECT turn_id,round,autonomous FROM conversation.turns WHERE actor_id=%s AND conversation_id=%s AND state='queued' AND error_code IS DISTINCT FROM 'behavior_pending' ORDER BY received_at LIMIT 1", (actor_id, conversation_id))).fetchone()
            if row:
                await self.jobs.enqueue(connection, JobSpec("conversation.run", f"turn-wake:{row['turn_id']}:{row['round']}:{uuid4()}", {"turn_id": row["turn_id"]}, ServiceClass.ELASTIC if row.get("autonomous") else ServiceClass.INTERACTIVE, subject_id=actor_id))

        await self.expression.wake(actor_id,conversation_id)

    async def expire(self, turn_id, attempt_id):
        async with self.database.transaction(read_only=True) as connection:
            pending = await (await connection.execute(
                "SELECT * FROM conversation.turns WHERE turn_id=%s AND model_attempt_id=%s "
                "AND state='running' AND lease_until<=now()", (turn_id, attempt_id))).fetchone()
        if pending is None:
            return
        if not pending["autonomous"]:
            # Broker owns the provider execution clock. This worker lease only
            # observes its original attempt, including after a worker restart.
            try:
                original = await self.models.result(attempt_id, context=context_from_record(pending["context"]))
            except Exception as error:
                raise_resource_wait(error)
                postpone = True
            else:
                postpone = original is not None and original.receipt.status in {"queued", "running"}
                if (original is not None and original.receipt.status in {"succeeded", "failed", "cancelled"}
                        and original.receipt.finished_at is not None):
                    postpone |= original.receipt.finished_at + timedelta(seconds=TURN_SETTLEMENT_SECONDS) > datetime.now(UTC)
            if postpone:
                async with self.database.transaction() as connection:
                    if pending["deployment_generation"] is not None:
                        await self.jobs.assert_generation_on(connection, pending["deployment_generation"])
                    renewed = await (await connection.execute(
                        "UPDATE conversation.turns SET lease_until=now()+(%s*interval '1 second') "
                        "WHERE turn_id=%s AND model_attempt_id=%s AND state='running' AND lease_until<=now() "
                        "RETURNING actor_id,lease_until", (TURN_SETTLEMENT_SECONDS, turn_id, attempt_id))).fetchone()
                    if renewed:
                        await self.jobs.enqueue(connection, JobSpec("conversation.expire",
                            f"turn-observe:{turn_id}:{attempt_id}:{uuid4()}", {"turn_id": turn_id, "attempt_id": attempt_id},
                            ServiceClass.INTERACTIVE, renewed["lease_until"], renewed["actor_id"]))
                return
        async with self.database.transaction() as connection:
            if pending["deployment_generation"] is not None:
                await self.jobs.assert_generation_on(connection, pending["deployment_generation"])
            row = await (await connection.execute("UPDATE conversation.turns SET state='unknown',error_code='worker_lease_expired',completed_at=now() WHERE turn_id=%s AND model_attempt_id=%s AND state='running' AND lease_until<=now() RETURNING actor_id,conversation_id", (turn_id, attempt_id))).fetchone()
        if row:
            if pending["autonomous"]:
                await self.models.cancel(attempt_id, reason="consumer_interrupted")
            await self._next(row["actor_id"], row["conversation_id"])

    async def recover(self):
        async with self.database.transaction() as connection:
            rows = await (await connection.execute(
                "SELECT t.turn_id,t.actor_id,t.model_attempt_id,t.lease_until,t.autonomous,t.deployment_generation,"
                "EXISTS(SELECT 1 FROM conversation.context_selections s WHERE s.attempt_id=t.model_attempt_id) AS model_frozen "
                "FROM conversation.turns t WHERE t.state='running'")).fetchall()
            for row in rows:
                await self.jobs.enqueue(connection, JobSpec("conversation.expire", f"turn-recover:{row['turn_id']}:{row['model_attempt_id']}:{uuid4()}",
                    {"turn_id": row["turn_id"], "attempt_id": row["model_attempt_id"]}, ServiceClass.INTERACTIVE, row["lease_until"], row["actor_id"]))
                if row["model_attempt_id"] and not row["model_frozen"]:
                    await self.jobs.enqueue(connection, JobSpec("conversation.prepare",
                        f"turn-prepare:{row['turn_id']}:{row['model_attempt_id']}",
                        {"turn_id": row["turn_id"], "attempt_id": row["model_attempt_id"]},
                        ServiceClass.ELASTIC if row["autonomous"] else ServiceClass.INTERACTIVE,
                        subject_id=row["actor_id"], data_generation=row["deployment_generation"]))
            collectors = await (await connection.execute("SELECT turn_id,actor_id,collection_revision,seal_at FROM conversation.turns WHERE state='collecting'")).fetchall()
            for row in collectors:
                await self.jobs.enqueue(connection, JobSpec("conversation.seal", f"collector-recover:{row['turn_id']}:{row['collection_revision']}:{uuid4()}",
                    {"turn_id": row["turn_id"], "expected_revision": row["collection_revision"]}, ServiceClass.INTERACTIVE, row["seal_at"], row["actor_id"]))
            blocked = await (await connection.execute(
                "SELECT turn_id,actor_id FROM conversation.turns WHERE state='queued' AND error_code='behavior_pending'")).fetchall()
            for item in blocked:
                await self.jobs.enqueue(connection, JobSpec("conversation.run", f"behavior-recover:{item['turn_id']}:{uuid4()}",
                    {"turn_id": item["turn_id"]}, ServiceClass.INTERACTIVE, datetime.now(UTC)+timedelta(seconds=10),
                    item["actor_id"], execution_lane=ExecutionLane.CONVERSATION))
            pending = await (await connection.execute(
                "SELECT s.input_id,s.part_index,s.supplement_revision,t.actor_id FROM conversation.input_supplements s "
                "JOIN conversation.turns t ON t.turn_id=s.input_id WHERE s.notification_state='pending'")).fetchall()
            for item in pending:
                await self.jobs.enqueue(connection, JobSpec("conversation.supplement_notify",
                    f"supplement-recover:{item['input_id']}:{item['part_index']}:{item['supplement_revision']}:{uuid4()}",
                    {"input_id": item["input_id"], "part_index": item["part_index"],
                     "supplement_revision": item["supplement_revision"]}, ServiceClass.ELASTIC,
                    subject_id=item["actor_id"], execution_lane=ExecutionLane.CONTROL))
            waiting = await (await connection.execute(
                "SELECT t.turn_id,t.actor_id,t.model_attempt_id,t.state,t.lease_until "
                "FROM conversation.turns t JOIN conversation.context_selections s ON s.attempt_id=t.model_attempt_id "
                "WHERE s.consumed_at IS NULL AND t.state IN ('waiting_external','running')")).fetchall()
            for item in waiting:
                task = "conversation.model_complete" if item["state"] == "waiting_external" else "conversation.model_consume_expire"
                await self.jobs.enqueue(connection, JobSpec(task, f"model-recover:{item['model_attempt_id']}:{uuid4()}",
                    {"turn_id": item["turn_id"], "attempt_id": item["model_attempt_id"]}, ServiceClass.INTERACTIVE,
                    item["lease_until"] if item["state"] == "running" else datetime.now(UTC), item["actor_id"],
                    execution_lane=ExecutionLane.CONVERSATION))
            unknown = await (await connection.execute(
                "SELECT t.turn_id,t.actor_id,t.model_attempt_id FROM conversation.turns t "
                "JOIN conversation.context_selections s ON s.attempt_id=t.model_attempt_id "
                "WHERE t.state='unknown' AND s.consumed_at IS NULL")).fetchall()
            for item in unknown:
                await self.jobs.enqueue(connection, JobSpec("conversation.model_observe_unknown",
                    f"model-unknown-recover:{item['model_attempt_id']}:{uuid4()}",
                    {"turn_id": item["turn_id"], "attempt_id": item["model_attempt_id"]}, ServiceClass.INTERACTIVE,
                    datetime.now(UTC)+timedelta(seconds=60), item["actor_id"], execution_lane=ExecutionLane.CONVERSATION))
            usage = await (await connection.execute(
                "SELECT s.attempt_id,s.turn_id,t.actor_id,t.expression_id,e.receipt FROM conversation.context_selections s "
                "JOIN conversation.turns t ON t.turn_id=s.turn_id LEFT JOIN conversation.expressions e ON e.expression_id=t.expression_id "
                "WHERE s.model_receipt->>'dispatch'='submitted' "
                "AND s.selection@>%s::jsonb", (document([{"kind": "expression_example", "selected": True}]),))).fetchall()
            for item in usage:
                outcomes = ["selected"]
                if item["expression_id"]:
                    outcomes.append("expressed")
                if item["receipt"] and item["receipt"].get("delivery") == "delivered":
                    outcomes.append("delivered")
                for outcome in outcomes:
                    expression_id = item["expression_id"] if outcome != "selected" else None
                    await self.jobs.enqueue(connection, JobSpec("conversation.example_usage",
                        f"example-usage-recover:{item['attempt_id']}:{outcome}:{uuid4()}",
                        {"attempt_id": item["attempt_id"], "turn_id": item["turn_id"], "outcome": outcome,
                         "expression_id": expression_id}, ServiceClass.ELASTIC, subject_id=item["actor_id"],
                        execution_lane=ExecutionLane.CONTROL))

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
            await self.jobs.enqueue(connection, JobSpec("conversation.cancel", "turn-cancel:"+row["turn_id"], {"turn_id": row["turn_id"]}, ServiceClass.INTERACTIVE, subject_id=context.actor_id))
            return CommandResult(body["client_request_id"], {"turn_id": row["turn_id"], "state": "cancelled", "external_outcome": "unconfirmed"}, row["revision"]+1, attempt_id=row["model_attempt_id"])
        return await self.commands.execute(context, "conversation.cancel", body["client_request_id"], signature(body), apply)

    async def reconcile_expression(self,context,body):
        await self.identity.check(context)
        current=await self.expression.get(context,body.get("expression_id"))
        if current["revision"]!=body.get("expected_revision"):
            raise AppError("revision_conflict",status=409)
        async def apply(connection):
            if not await self.sources.validate_persistence(connection,context,()):
                raise AppError("forbidden",status=403)
            await self.jobs.enqueue(connection,JobSpec("conversation.expression_reconcile","expression-reconcile:"+body["client_request_id"],{"expression_id":body["expression_id"],"expected_revision":body["expected_revision"],"context":wire(context)},ServiceClass.INTERACTIVE,subject_id=context.actor_id))
            return CommandResult(body["client_request_id"],{"expression_id":body["expression_id"],"state":"reconcile_queued"},current["revision"])
        return await self.commands.execute(context,"conversation.expression.reconcile",body["client_request_id"],signature(body),apply)

    async def reconcile_expression_work(self,expression_id,expected_revision,context):
        await self.expression.reconcile(context_from_record(context),expression_id,expected_revision)

    async def cancel_work(self, turn_id):
        async with self.database.transaction(read_only=True) as connection:
            row = await (await connection.execute("SELECT * FROM conversation.turns WHERE turn_id=%s", (turn_id,))).fetchone()
            if row and row["merged_into"]:
                row = await (await connection.execute("SELECT * FROM conversation.turns WHERE turn_id=%s", (row["merged_into"],))).fetchone()
                turn_id = row["turn_id"]
            links = await (await connection.execute("SELECT action_id FROM conversation.tool_links WHERE turn_id=%s", (turn_id,))).fetchall()
        if not row or row["state"] != "cancelled":
            return
        context = context_from_record(row["context"])
        if row["model_attempt_id"]:
            await self.models.cancel(row["model_attempt_id"])
        for link in links:
            await self.actions.cancel(link["action_id"], context)
        if row["expression_id"]:
            await self.expression.cancel(context,row["expression_id"])
        await self._next(row["actor_id"],row["conversation_id"])

    @staticmethod
    def _validate_parts(parts):
        if not parts or len(parts)>256:
            raise AppError("invalid_input_parts")
        for part in parts:
            if part.kind not in {"text", "quote", "image", "audio", "video", "file", "reply", "reference", "forward", "reaction","mention","mention_all","channel_segment"}:
                raise AppError("invalid_input_part")
            if part.kind in {"text", "quote"} and (not isinstance(part.text, str) or len(part.text)>100000):
                raise AppError("invalid_input_text")
            if part.kind == "quote" and not isinstance(part.metadata, dict):
                raise AppError("invalid_quote_metadata")
            if (part.kind in {"image", "audio", "video", "file"} and part.media is None
                    and not (part.metadata.get("media_unavailable") or part.metadata.get("original_pending"))):
                raise AppError("input_media_required")

    async def expression_result(self,context,expression_id,current):
        await self.identity.check(context)
        state = expression_turn_state(current["state"])
        async with self.database.transaction() as connection:
            await connection.execute(
                "UPDATE conversation.turns SET state=%s,error_code=%s,revision=revision+1,"
                "completed_at=CASE WHEN %s='waiting_expression' THEN NULL ELSE now() END "
                "WHERE actor_id=%s AND conversation_id=%s AND expression_id=%s "
                "AND (state IN ('running','waiting_expression','unknown') "
                "OR (state='failed' AND error_code IS NULL AND %s='completed')) "
                "AND (state IS DISTINCT FROM %s OR error_code IS DISTINCT FROM %s)",
                (state, current.get("reason"), state, context.actor_id, context.conversation_id,
                 expression_id, state, state, current.get("reason")))
            if state == "completed":
                attempts = await (await connection.execute(
                    "SELECT s.attempt_id,s.turn_id FROM conversation.context_selections s JOIN conversation.turns t ON t.turn_id=s.turn_id "
                    "WHERE t.actor_id=%s AND t.conversation_id=%s AND t.expression_id=%s AND s.model_receipt->>'dispatch'='submitted'",
                    (context.actor_id, context.conversation_id, expression_id))).fetchall()
                for attempt in attempts:
                    await self._enqueue_example_usage(connection, attempt_id=attempt["attempt_id"], turn_id=attempt["turn_id"],
                        actor_id=context.actor_id, outcome="delivered", expression_id=expression_id)
        await self.expression.wake(context.actor_id,context.conversation_id)
