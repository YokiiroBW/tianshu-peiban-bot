"""Live group participation uses the existing contact budget and ordered turn lane."""
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from tianshu.models.types import ModelRequest
from tianshu.runtime.api import CommandResult
from tianshu.runtime.errors import AppError
from tianshu.runtime.jobs import JobSpec
from tianshu.sources.dependencies import unique_refs
from tianshu.types import ContextContribution, RequestBudget, ServiceClass
from .codec import context_from_record, document, signature, source_refs
from .generation import GenerationRequests, attempt_for
from .persistence import lock_material_row


def eligible(context, message, *, now=None):
    now = now or datetime.now(UTC)
    return (
        context.conversation_kind == "group"
        and message.autonomous_eligible
        and not message.reply_required
        and message.reply_owner == "tianshu"
        and not message.command
        and timedelta(seconds=-5) <= now - message.received_at <= timedelta(seconds=120)
        and timedelta(seconds=-5) <= now - message.occurred_at <= timedelta(seconds=120)
    )


class GroupParticipation:
    def __init__(self, conversation):
        self.service = conversation
        self.generations = GenerationRequests(
            conversation.database, conversation.sources, conversation.models
        )

    async def view(self, context, body):
        target = await self.service.identity.authorize_conversation(
            context, body.get("target_conversation_id"), permission="companion.read"
        )
        if target.conversation_kind != "group":
            raise AppError("group_conversation_required")
        async with self.service.database.transaction(read_only=True) as connection:
            row = await (await connection.execute(
                "SELECT * FROM conversation.group_participation WHERE actor_id=%s AND conversation_id=%s",
                (target.actor_id, target.conversation_id),
            )).fetchone()
        return {
            "actor_id": target.actor_id, "conversation_id": target.conversation_id,
            "enabled": row["enabled"] if row else False,
            "revision": row["revision"] if row else 0,
            "can_manage": bool(target.permissions & {"*", "companion.manage"}),
        }

    async def save(self, context, body):
        current = await self.view(context, body)
        if not current["can_manage"]:
            raise AppError("forbidden", status=403)
        if type(body.get("enabled")) is not bool:
            raise AppError("invalid_participation_policy")
        async def apply(connection):
            checked = await self.service.identity.context_on(connection, context.principal_id,
                actor_id=current["actor_id"], conversation_id=current["conversation_id"], permission="companion.manage")
            if not await self.service.sources.validate_persistence(connection, checked, ()):
                raise AppError("forbidden", status=403)
            await connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                ("group-policy:" + current["conversation_id"],))
            row = await (await connection.execute(
                "SELECT * FROM conversation.group_participation WHERE actor_id=%s AND conversation_id=%s FOR UPDATE",
                (current["actor_id"], current["conversation_id"]),
            )).fetchone()
            revision = row["revision"] if row else 0
            if body.get("expected_revision") != revision:
                raise AppError("revision_conflict", status=409)
            await connection.execute(
                "INSERT INTO conversation.group_participation(actor_id,conversation_id,enabled) VALUES(%s,%s,%s) "
                "ON CONFLICT(actor_id,conversation_id) DO UPDATE SET enabled=excluded.enabled,revision=conversation.group_participation.revision+1",
                (current["actor_id"], current["conversation_id"], body["enabled"]),
            )
            return CommandResult(body["client_request_id"],
                {**current, "enabled": body["enabled"], "revision": revision + 1}, revision + 1)
        return await self.service.commands.execute(context, "conversation.participation.save",
            body["client_request_id"], signature(body), apply)

    async def prepare(self, context, message):
        if not eligible(context, message):
            return None
        async with self.service.database.transaction(read_only=True) as connection:
            row = await (await connection.execute(
                "SELECT enabled,revision FROM conversation.group_participation WHERE actor_id=%s AND conversation_id=%s",
                (context.actor_id, context.conversation_id),
            )).fetchone()
        if not row or not row["enabled"]:
            return None
        preferences = await self.service.contacts.preferences(context, group=True)
        history = await self.service.contacts.history(context, conversation_only=True)
        if self.service.life.attention.window(preferences, history).decision != "send":
            return None
        return {"policy_revision": row["revision"],
                "evaluation_spacing": max(30, min(300, preferences["cooldown_seconds"]))}

    async def offer(self, connection, context, message, turn_id, prepared):
        if prepared is None:
            return
        await connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
            ("group-participation:" + context.actor_id + ":" + context.conversation_id,))
        pending = await (await connection.execute(
            "SELECT turn_id,participation_state FROM conversation.turns WHERE actor_id=%s AND conversation_id=%s "
            "AND participation_state IN ('waiting','evaluating')",
            (context.actor_id, context.conversation_id),
        )).fetchone()
        if pending:
            if pending["participation_state"] == "waiting":
                await connection.execute(
                    "UPDATE conversation.turns SET participation_cutoff=(SELECT max(id) FROM conversation.messages "
                    "WHERE actor_id=%s AND conversation_id=%s) WHERE turn_id=%s",
                    (context.actor_id, context.conversation_id, pending["turn_id"]),
                )
            return
        blocked = await (await connection.execute(
            "SELECT 1 FROM conversation.turns WHERE actor_id=%s AND conversation_id=%s AND "
            "(state IN ('collecting','queued','running','waiting_external','waiting_expression') "
            "OR participation_checked_at > now()-(%s*interval '1 second')) LIMIT 1",
            (context.actor_id, context.conversation_id, prepared["evaluation_spacing"]),
        )).fetchone()
        if blocked:
            return
        deadline = min(message.received_at + timedelta(seconds=120), datetime.now(UTC) + timedelta(seconds=90))
        changed = await (await connection.execute(
            "UPDATE conversation.turns SET participation_state='waiting',participation_checked_at=now(),"
            "participation_policy_revision=%s,participation_deadline=%s,participation_cutoff=message_cutoff "
            "WHERE turn_id=%s AND state='observed' AND participation_state IS NULL RETURNING turn_id",
            (prepared["policy_revision"], deadline, turn_id),
        )).fetchone()
        if changed:
            await self.service.jobs.enqueue(connection, JobSpec(
                "conversation.participation", "participation-expire:" + turn_id, {"turn_id": turn_id},
                ServiceClass.ELASTIC, deadline, context.actor_id,
            ))
            await self.service.jobs.enqueue(connection, JobSpec(
                "conversation.participation", "participation:" + turn_id, {"turn_id": turn_id},
                ServiceClass.ELASTIC, datetime.now(UTC) + timedelta(seconds=3), context.actor_id,
            ))

    async def decide(self, turn_id):
        async with self.service.database.transaction(read_only=True) as connection:
            row = await (await connection.execute(
                "SELECT * FROM conversation.turns WHERE turn_id=%s", (turn_id,)
            )).fetchone()
        if not row or row["state"] != "observed" or row["participation_state"] not in {"waiting", "evaluating", "unknown"}:
            return
        original = context_from_record(row["context"])
        refs = source_refs(row["source_refs"])
        if row["participation_deadline"] <= datetime.now(UTC):
            await self._finish(turn_id, "skipped", "live_participation_window_expired")
            return
        context = await self.service.identity.for_participation(original, source_refs=refs)
        if context is None:
            await self._finish(turn_id, "skipped", "current_group_policy_denied")
            return
        preferences = await self.service.contacts.preferences(context, group=True)
        history = await self.service.contacts.history(context, conversation_only=True)
        window = self.service.life.attention.window(preferences, history)
        if window.decision != "send":
            await self._finish(turn_id, "skipped", window.reason)
            return
        async with self.service.database.transaction() as connection:
            fresh = await lock_material_row(connection, self.service.sources, context,
                "SELECT * FROM conversation.turns WHERE turn_id=%s FOR UPDATE", (turn_id,))
            policy = await (await connection.execute(
                "SELECT enabled,revision FROM conversation.group_participation WHERE actor_id=%s AND conversation_id=%s",
                (context.actor_id, context.conversation_id),
            )).fetchone()
            if fresh["state"] != "observed" or fresh["participation_state"] not in {"waiting", "evaluating", "unknown"}:
                return
            if not policy or not policy["enabled"] or policy["revision"] != row["participation_policy_revision"]:
                await connection.execute("UPDATE conversation.turns SET participation_state='skipped',participation_reason='policy_changed' WHERE turn_id=%s", (turn_id,))
                return
            await connection.execute("UPDATE conversation.turns SET participation_state='evaluating' WHERE turn_id=%s", (turn_id,))
            row = fresh
        material_key = "participation:" + turn_id + ":" + str(row["participation_policy_revision"])
        attempt_id = attempt_for(material_key)
        frozen = await self.generations.request(attempt_id, context)
        if frozen is None:
            actor = await self.service.companion.actor(context)
            persona = await self.service.companion.persona(context, actor["persona_id"], actor["persona_revision"]) if actor["persona_id"] else None
            recent = await self.service.messages(context, limit=12, before_id=row["participation_cutoff"])
            refs = unique_refs(refs, *(source_refs(item["source_refs"]) for item in recent.items))
            contribution = ContextContribution("group_participation",
                "Decide whether this character should briefly join the current group topic. "
                "This is unsolicited participation, not a human command. Stay silent for unsuitable, private, "
                "already answered or low-relevance topics. Return only JSON {participate:boolean,reason:string}.",
                refs, context.scope_ids, 100, True)
            pack = await self.service.assembler.assemble(context, "participation", "", RequestBudget(row["participation_deadline"]),
                persona_text=persona.prompt_text if persona else "",
                recent_messages=tuple(self.service._message(item["speaker"],item["parts"]) for item in reversed(recent.items)),
                contributions=(contribution,), reserved_output_tokens=256)
            frozen = ModelRequest(material_key, attempt_id, "participation", ServiceClass.ELASTIC, context,
                RequestBudget(row["participation_deadline"]), tuple(pack.messages), system=pack.system,
                max_output_tokens=256, source_refs=tuple(pack.source_refs),
                estimated_input_tokens=pack.token_usage.estimated_tokens, persona_revision=persona.revision if persona else None)
        try:
            result = await self.generations.run(material_key, frozen)
        except AppError as error:
            await self._finish(turn_id, "unknown" if error.code != "generation_request_expired" else "skipped", error.code)
            return
        if result.receipt.status != "succeeded":
            await self._finish(turn_id, "unknown" if result.receipt.status in {"unknown","running","queued"} else "skipped", result.receipt.error_code or result.receipt.status)
            return
        try:
            decision = json.loads("".join(part.text or "" for part in result.parts if part.kind == "text"))
        except ValueError:
            await self._finish(turn_id, "skipped", "participation_response_invalid")
            return
        if not isinstance(decision, dict) or decision.get("participate") is not True:
            await self._finish(turn_id, "skipped", "not_relevant_to_current_group")
            return
        context = await self.service.identity.for_participation(original, source_refs=frozen.source_refs)
        preferences = await self.service.contacts.preferences(original, group=True)
        history = await self.service.contacts.history(original, conversation_only=True)
        if context is None or self.service.life.attention.window(preferences, history).decision != "send":
            await self._finish(turn_id, "skipped", "current_contact_window_changed")
            return
        async with self.service.database.transaction() as connection:
            fresh = await lock_material_row(connection, self.service.sources, context,
                "SELECT * FROM conversation.turns WHERE turn_id=%s FOR UPDATE", (turn_id,), additional_refs=frozen.source_refs)
            policy = await (await connection.execute("SELECT enabled,revision FROM conversation.group_participation WHERE actor_id=%s AND conversation_id=%s",
                (context.actor_id, context.conversation_id))).fetchone()
            if fresh["state"] != "observed" or fresh["participation_state"] != "evaluating" or fresh["participation_deadline"] <= datetime.now(UTC):
                return
            if not policy or not policy["enabled"] or policy["revision"] != row["participation_policy_revision"]:
                await connection.execute("UPDATE conversation.turns SET participation_state='skipped',participation_reason='policy_changed' WHERE turn_id=%s", (turn_id,))
                return
            direct = await (await connection.execute(
                "SELECT 1 FROM conversation.turns WHERE actor_id=%s AND conversation_id=%s AND NOT autonomous "
                "AND state IN ('collecting','queued','running','waiting_external','waiting_expression') LIMIT 1",
                (context.actor_id, context.conversation_id),
            )).fetchone()
            if direct:
                await connection.execute("UPDATE conversation.turns SET participation_state='skipped',participation_reason='direct_reply_takes_priority' WHERE turn_id=%s", (turn_id,))
                return
            await connection.execute(
                "UPDATE conversation.turns SET state='queued',autonomous=true,context=%s,source_refs=%s,"
                "message_cutoff=participation_cutoff,participation_state='accepted',participation_reason='current_group_topic',"
                "deadline_at=participation_deadline,revision=revision+1 WHERE turn_id=%s",
                (document(context), document(unique_refs(refs, frozen.source_refs)), turn_id),
            )
            await self.service.collector._run(connection, turn_id, context.actor_id, service_class=ServiceClass.ELASTIC)

    async def _finish(self, turn_id, state, reason):
        async with self.service.database.transaction() as connection:
            await connection.execute("UPDATE conversation.turns SET participation_state=%s,participation_reason=%s WHERE turn_id=%s AND state='observed' AND participation_state IN ('waiting','evaluating','unknown')", (state, reason, turn_id))

    async def recover(self):
        async with self.service.database.transaction() as connection:
            rows = await (await connection.execute("SELECT turn_id,actor_id FROM conversation.turns WHERE state='observed' AND participation_state IN ('waiting','evaluating','unknown')")).fetchall()
            for row in rows:
                await self.service.jobs.enqueue(connection, JobSpec("conversation.participation", f"participation-recover:{row['turn_id']}:{uuid4()}",
                    {"turn_id":row["turn_id"]}, ServiceClass.ELASTIC, subject_id=row["actor_id"]))
