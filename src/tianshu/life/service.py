"""Business state for continuing activities and commitments on the single durable scheduler."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from tianshu.runtime.api import CommandResult, Page
from tianshu.runtime.errors import AppError
from tianshu.runtime.jobs import JobSpec
from tianshu.types import Coverage, ServiceClass
from tianshu.conversation.codec import document, signature, source_refs
from .attention import AttentionPolicy
from .validation import revision, text, timestamp, timezone

TRANSITIONS = {
    "planned": {"running", "cancelled", "dormant"},
    "running": {"paused", "dormant", "completed", "cancelled"},
    "paused": {"running", "dormant", "cancelled"},
    "dormant": {"running", "cancelled"}, "completed": set(), "cancelled": set(),
}


class LifeService:
    def __init__(self, database, commands, jobs, identity, companion, contacts, *,
                 sources, memory, actions, expression, weather=None):
        self.database, self.commands, self.jobs = database, commands, jobs
        self.identity, self.companion, self.contacts = identity, companion, contacts
        self.sources, self.memory, self.actions = sources, memory, actions
        self.expression, self.weather = expression, weather
        self.attention = AttentionPolicy()

    async def synchronize_actor(self, context, actor_id, enabled, *, connection):
        await connection.execute("INSERT INTO life.actor_state(actor_id) VALUES (%s) ON CONFLICT DO NOTHING", (actor_id,))
        await connection.execute("INSERT INTO life.plan_watermarks(actor_id) VALUES (%s) ON CONFLICT DO NOTHING", (actor_id,))
        if not enabled:
            await connection.execute(
                "UPDATE life.activities SET state='paused',waiting_reason='actor_disabled',next_due_at=NULL,"
                "revision=revision+1 WHERE actor_id=%s AND state='running'", (actor_id,))
            await connection.execute("UPDATE life.actor_state SET activity_id=NULL,revision=revision+1 WHERE actor_id=%s", (actor_id,))

    async def _state(self, context, connection):
        row = await (await connection.execute(
            "SELECT * FROM life.actor_state WHERE actor_id=%s", (context.actor_id,))).fetchone()
        if not row:
            raise AppError("actor_not_initialized", status=409)
        return row

    async def state(self, context):
        actor = await self.companion.actor(context)
        async with self.database.transaction(read_only=True) as connection:
            state = await self._state(context, connection)
            activity = await (await connection.execute(
                "SELECT * FROM life.activities WHERE id=%s AND actor_id=%s AND scope_ids<@%s::text[]",
                (state["activity_id"], context.actor_id, list(context.scope_ids)))).fetchone()
            outfit = await (await connection.execute(
                "SELECT * FROM life.outfits WHERE id=%s AND actor_id=%s AND scope_ids<@%s::text[]",
                (state["outfit_id"], context.actor_id, list(context.scope_ids)))).fetchone()
        if activity:
            await self.identity.check(context, source_refs(activity["source_refs"]))
        if outfit:
            await self.identity.check(context, source_refs(outfit["source_refs"]))
        return {"actor_id": context.actor_id, "actor_revision": actor["revision"], **state,
                "activity": activity, "outfit": outfit, "affect": await self.companion.affect(context),
                "observed_at": datetime.now(UTC)}

    async def goals(self, context, *, cursor=None, limit=40):
        await self.identity.check(context)
        limit = min(100, max(1, int(limit)))
        async with self.database.transaction(read_only=True) as connection:
            rows = await (await connection.execute(
                "SELECT * FROM life.goals WHERE actor_id=%s AND scope_ids<@%s::text[] "
                "AND (%s::text IS NULL OR id>%s) ORDER BY id LIMIT %s",
                (context.actor_id, list(context.scope_ids), cursor, cursor, limit + 1))).fetchall()
        return await self._page(context, rows, limit)

    async def activities(self, context, *, cursor=None, limit=40):
        await self.identity.check(context)
        limit = min(100, max(1, int(limit)))
        async with self.database.transaction(read_only=True) as connection:
            rows = await (await connection.execute(
                "SELECT * FROM life.activities WHERE actor_id=%s AND scope_ids<@%s::text[] "
                "AND (%s::text IS NULL OR id>%s) ORDER BY id LIMIT %s",
                (context.actor_id, list(context.scope_ids), cursor, cursor, limit + 1))).fetchall()
        return await self._page(context, rows, limit)

    async def _page(self, context, rows, limit):
        missing, accepted = [], []
        for row in rows[:limit]:
            try:
                await self.identity.check(context, source_refs(row.get("source_refs", ())))
                accepted.append(row)
            except AppError as error:
                missing.append(error.code)
        more = len(rows) > limit
        return Page(tuple(accepted), rows[limit - 1]["id"] if more else None,
                    Coverage(not more and not missing, None, tuple(sorted(set(missing)))))

    async def save_goal(self, context, body):
        await self.identity.check(context)
        title = text(body.get("title"), "目标", 1000)
        motivation = text(body.get("motivation", ""), "目标理由", empty=True)
        state = body.get("state", "active")
        if state not in {"active", "paused", "completed", "cancelled"}:
            raise AppError("invalid_goal_state")
        priority = body.get("priority", .5)
        if type(priority) not in (int, float) or not 0 <= priority <= 1:
            raise AppError("invalid_priority")
        async def apply(connection):
            goal_id = body.get("id") or str(uuid4())
            row = await (await connection.execute("SELECT * FROM life.goals WHERE id=%s AND actor_id=%s FOR UPDATE", (goal_id, context.actor_id))).fetchone()
            if body.get("id") and row is None:
                raise AppError("object_not_found", status=404)
            revision(row, body.get("expected_revision"))
            next_revision = row["revision"] + 1 if row else 1
            await connection.execute(
                "INSERT INTO life.goals(id,actor_id,title,motivation,state,priority,matter_id,scope_ids,due_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT(id) DO UPDATE SET title=excluded.title,motivation=excluded.motivation,state=excluded.state,"
                "priority=excluded.priority,matter_id=excluded.matter_id,due_at=excluded.due_at,revision=%s,updated_at=now()",
                (goal_id, context.actor_id, title, motivation, state, priority, body.get("matter_id"), list(context.scope_ids),
                 timestamp(body.get("due_at"), optional=True), next_revision))
            await self.signal(context, f"goal:{goal_id}:{next_revision}", connection=connection)
            return CommandResult(body["client_request_id"], {"id": goal_id, "revision": next_revision}, next_revision)
        return await self.commands.execute(context, "life.goals.save", body["client_request_id"], signature(body), apply)

    async def save_activity(self, context, body):
        await self.identity.check(context)
        title = text(body.get("title"), "活动", 1000)
        kind = text(body.get("kind"), "活动类型", 128)
        state = body.get("state", "planned")
        if state not in TRANSITIONS:
            raise AppError("invalid_activity_state")
        due = timestamp(body.get("next_due_at"), optional=True)
        if state == "paused" and due is None and not body.get("waiting_reason"):
            raise AppError("pause_reason_required")
        if state == "dormant" and not body.get("resume_condition"):
            raise AppError("resume_condition_required")
        async def apply(connection):
            activity_id = body.get("id") or str(uuid4())
            row = await (await connection.execute("SELECT * FROM life.activities WHERE id=%s AND actor_id=%s FOR UPDATE", (activity_id, context.actor_id))).fetchone()
            if body.get("id") and row is None:
                raise AppError("object_not_found", status=404)
            revision(row, body.get("expected_revision"))
            if row and state != row["state"] and state not in TRANSITIONS[row["state"]]:
                raise AppError("invalid_activity_transition", status=409)
            if state == "running":
                other = await (await connection.execute(
                    "SELECT id FROM life.activities WHERE actor_id=%s AND state='running' AND id<>%s FOR UPDATE",
                    (context.actor_id, activity_id))).fetchone()
                if other:
                    raise AppError("activity_already_running", status=409)
                if row and row["action_id"] and row["waiting_reason"] == "outcome_unknown":
                    raise AppError("outcome_unknown", status=409, details={"action_id": row["action_id"]})
            next_revision = row["revision"] + 1 if row else 1
            await connection.execute(
                "INSERT INTO life.activities(id,actor_id,goal_id,title,kind,state,next_due_at,resume_condition,waiting_reason,matter_id,scope_ids) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(id) DO UPDATE SET "
                "title=excluded.title,kind=excluded.kind,state=excluded.state,next_due_at=excluded.next_due_at,"
                "resume_condition=excluded.resume_condition,waiting_reason=excluded.waiting_reason,revision=%s,updated_at=now()",
                (activity_id, context.actor_id, body.get("goal_id"), title, kind, state, due, body.get("resume_condition"),
                 body.get("waiting_reason"), body.get("matter_id"), list(context.scope_ids), next_revision))
            if state == "running":
                await connection.execute("UPDATE life.actor_state SET activity_id=%s,revision=revision+1,updated_at=now() WHERE actor_id=%s", (activity_id, context.actor_id))
            if state in {"planned", "running"} and due:
                await self.jobs.enqueue(connection, JobSpec("life.activity_step", f"activity:{activity_id}:{next_revision}",
                    {"activity_id": activity_id, "expected_revision": next_revision, "context": self._context(context)},
                    ServiceClass.ELASTIC, due, context.actor_id, body.get("matter_id")))
            return CommandResult(body["client_request_id"], {"id": activity_id, "revision": next_revision, "state": state}, next_revision)
        return await self.commands.execute(context, "life.activities.save", body["client_request_id"], signature(body), apply)

    async def transition_activity(self, context, body, target):
        await self.identity.check(context)
        activity_id = text(body.get("id"), "活动标识", 128)
        async def apply(connection):
            row = await (await connection.execute("SELECT * FROM life.activities WHERE id=%s AND actor_id=%s FOR UPDATE", (activity_id, context.actor_id))).fetchone()
            if not row:
                raise AppError("activity_not_found", status=404)
            if body.get("id") and row is None:
                raise AppError("object_not_found", status=404)
            revision(row, body.get("expected_revision"))
            if target not in TRANSITIONS[row["state"]]:
                raise AppError("invalid_activity_transition", status=409)
            if target == "running" and row["protected_until"] and row["protected_until"] > datetime.now(UTC):
                raise AppError("activity_protected", status=409, details={"until": row["protected_until"].isoformat()})
            if target == "running" and row["waiting_reason"] == "outcome_unknown":
                raise AppError("outcome_unknown", status=409, details={"action_id": row["action_id"]})
            next_revision = row["revision"] + 1
            waiting = "paused_by_user" if target == "paused" else None
            await connection.execute("UPDATE life.activities SET state=%s,revision=%s,waiting_reason=%s,next_due_at=NULL,updated_at=now() WHERE id=%s", (target, next_revision, waiting, activity_id))
            await connection.execute("UPDATE life.actor_state SET activity_id=%s,revision=revision+1 WHERE actor_id=%s", (activity_id if target == "running" else None, context.actor_id))
            if target == "running":
                await self.jobs.enqueue(connection, JobSpec("life.activity_step", f"activity:{activity_id}:{next_revision}",
                    {"activity_id": activity_id, "expected_revision": next_revision, "context": self._context(context)}, subject_id=context.actor_id))
            return CommandResult(body["client_request_id"], {"id": activity_id, "revision": next_revision, "state": target, "action_id": row["action_id"]}, next_revision)
        return await self.commands.execute(context, f"life.activities.{target}", body["client_request_id"], signature(body), apply)

    @staticmethod
    def _context(context):
        from tianshu.runtime.serialization import wire
        return wire(context)

    async def commitments(self, context, *, cursor=None, limit=40):
        await self.identity.check(context)
        limit = min(100, max(1, int(limit)))
        async with self.database.transaction(read_only=True) as connection:
            rows = await (await connection.execute(
                "SELECT *,state IN ('scheduled','waiting') AS can_cancel,state IN ('scheduled','waiting') AS can_reschedule "
                "FROM life.commitments WHERE actor_id=%s AND scope_ids<@%s::text[] AND (%s::text IS NULL OR id>%s) ORDER BY id LIMIT %s",
                (context.actor_id, list(context.scope_ids), cursor, cursor, limit + 1))).fetchall()
        return await self._page(context, rows, limit)

    async def save_commitment(self, context, body):
        await self.identity.check(context)
        title = text(body.get("title"), "约定", 2000)
        due = timestamp(body.get("due_at"), optional=True)
        zone = timezone(body.get("timezone", "Asia/Shanghai"))
        repeat = body.get("recurrence")
        days = body.get("weekdays", [])
        if repeat not in (None, "daily", "weekly") or any(type(day) is not int or not 0 <= day <= 6 for day in days):
            raise AppError("invalid_recurrence")
        if repeat == "weekly" and not days:
            raise AppError("weekdays_required")
        async def apply(connection):
            commitment_id = body.get("id") or str(uuid4())
            row = await (await connection.execute("SELECT * FROM life.commitments WHERE id=%s AND actor_id=%s FOR UPDATE", (commitment_id, context.actor_id))).fetchone()
            if body.get("id") and row is None:
                raise AppError("object_not_found", status=404)
            revision(row, body.get("expected_revision"))
            if row and row["state"] not in {"scheduled", "waiting"}:
                raise AppError("commitment_already_started", status=409)
            next_revision = row["revision"] + 1 if row else 1
            # The destination is the identity-resolved conversation, never a payload target.
            target = context.conversation_id
            await connection.execute(
                "INSERT INTO life.commitments(id,actor_id,title,due_at,timezone,state,target_ref,person_id,matter_id,capability_id,arguments,"
                "recurrence,weekdays,scope_ids,context) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT(id) DO UPDATE SET title=excluded.title,due_at=excluded.due_at,timezone=excluded.timezone,"
                "state=excluded.state,recurrence=excluded.recurrence,weekdays=excluded.weekdays,revision=%s,reason=NULL,context=excluded.context,updated_at=now()",
                (commitment_id, context.actor_id, title, due, zone, "scheduled" if due else "waiting", target,
                 context.person_id, body.get("matter_id"), body.get("capability_id"), document(body.get("arguments", {})),
                 repeat, days, list(context.scope_ids), document(context), next_revision))
            if row and row["due_at"]:
                await self.jobs.cancel(connection, f"commitment:{commitment_id}:{row['revision']}")
            if due:
                await self.jobs.enqueue(connection, JobSpec("life.commitment_due", f"commitment:{commitment_id}:{next_revision}",
                    {"commitment_id": commitment_id, "expected_revision": next_revision}, ServiceClass.DUE, due,
                    context.actor_id, body.get("matter_id")))
            await self.signal(context, f"commitment:{commitment_id}:{next_revision}", connection=connection)
            return CommandResult(body["client_request_id"], {"id": commitment_id, "revision": next_revision,
                                 "state": "scheduled" if due else "waiting", "due_at": due}, next_revision)
        return await self.commands.execute(context, "life.commitments.save", body["client_request_id"], signature(body), apply)

    async def reschedule_commitment(self, context, body):
        async with self.database.transaction(read_only=True) as connection:
            row = await (await connection.execute("SELECT * FROM life.commitments WHERE id=%s AND actor_id=%s", (body.get("id"), context.actor_id))).fetchone()
        if not row:
            raise AppError("commitment_not_found", status=404)
        return await self.save_commitment(context, {**row, **body, "due_at": body.get("due_at"),
            "timezone": body.get("timezone", row["timezone"]), "client_request_id": body["client_request_id"]})

    async def cancel_commitment(self, context, body):
        await self.identity.check(context)
        async def apply(connection):
            row = await (await connection.execute("SELECT * FROM life.commitments WHERE id=%s AND actor_id=%s FOR UPDATE", (body.get("id"), context.actor_id))).fetchone()
            if not row:
                raise AppError("commitment_not_found", status=404)
            if body.get("id") and row is None:
                raise AppError("object_not_found", status=404)
            revision(row, body.get("expected_revision"))
            if row["state"] not in {"scheduled", "waiting"}:
                raise AppError("commitment_already_started", status=409)
            await self.jobs.cancel(connection, f"commitment:{row['id']}:{row['revision']}")
            await connection.execute("UPDATE life.commitments SET state='cancelled',revision=revision+1,reason='cancelled_by_user',updated_at=now() WHERE id=%s", (row["id"],))
            return CommandResult(body["client_request_id"], {"id": row["id"], "state": "cancelled"}, row["revision"] + 1)
        return await self.commands.execute(context, "life.commitments.cancel", body["client_request_id"], signature(body), apply)

    async def save_contact_preferences(self, context, body):
        await self.identity.check(context)
        zone = timezone(body.get("timezone", "Asia/Shanghai"))
        for key in ("quiet_start", "quiet_end"):
            try:
                hour, minute = map(int, body[key].split(":"))
                if not 0 <= hour <= 23 or not 0 <= minute <= 59:
                    raise ValueError
            except (ValueError, KeyError, AttributeError):
                raise AppError("invalid_quiet_hours") from None
        for key, maximum in (("cooldown_seconds", 604800), ("daily_quota", 100), ("unanswered_limit", 100)):
            if type(body.get(key)) is not int or not 0 <= body[key] <= maximum:
                raise AppError("invalid_contact_budget")
        async def apply(connection):
            row = await (await connection.execute("SELECT * FROM life.contact_preferences WHERE actor_id=%s AND person_id=%s FOR UPDATE", (context.actor_id, context.person_id or ""))).fetchone()
            if body.get("id") and row is None:
                raise AppError("object_not_found", status=404)
            revision(row, body.get("expected_revision"))
            next_revision = row["revision"] + 1 if row else 1
            await connection.execute(
                "INSERT INTO life.contact_preferences(actor_id,person_id,revision,timezone,quiet_start,quiet_end,cooldown_seconds,daily_quota,unanswered_limit,enabled,scope_ids) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(actor_id,person_id) DO UPDATE SET "
                "revision=excluded.revision,timezone=excluded.timezone,quiet_start=excluded.quiet_start,quiet_end=excluded.quiet_end,"
                "cooldown_seconds=excluded.cooldown_seconds,daily_quota=excluded.daily_quota,unanswered_limit=excluded.unanswered_limit,enabled=excluded.enabled",
                (context.actor_id, context.person_id or "", next_revision, zone, body["quiet_start"], body["quiet_end"], body["cooldown_seconds"], body["daily_quota"], body["unanswered_limit"], body.get("enabled", True), list(context.scope_ids)))
            return CommandResult(body["client_request_id"], {"revision": next_revision}, next_revision)
        return await self.commands.execute(context, "life.contact_preferences.save", body["client_request_id"], signature(body), apply)

    async def signal(self, context, watermark, *, connection=None):
        if connection is None:
            async with self.database.transaction() as selected:
                return await self.signal(context, watermark, connection=selected)
        row = await (await connection.execute(
            "INSERT INTO life.plan_watermarks(actor_id,change_watermark) VALUES (%s,%s) "
            "ON CONFLICT(actor_id) DO UPDATE SET change_watermark=excluded.change_watermark,revision=life.plan_watermarks.revision+1 "
            "WHERE life.plan_watermarks.change_watermark<>excluded.change_watermark RETURNING revision",
            (context.actor_id, watermark))).fetchone()
        if row:
            await self.jobs.enqueue(connection, JobSpec("life.plan", f"life-plan:{context.actor_id}:{row['revision']}",
                {"actor_id": context.actor_id, "revision": row["revision"], "context": self._context(context)}, subject_id=context.actor_id))
        return bool(row)
