"""Business state for continuing activities and commitments on the single durable scheduler."""
from __future__ import annotations
from tianshu.sources.dependencies import unique_refs

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

from tianshu.runtime.api import CommandResult, Page
from tianshu.runtime.errors import AppError
from tianshu.runtime.jobs import JobSpec
from tianshu.types import Coverage, ServiceClass
from tianshu.conversation.codec import document, signature, source_refs
from tianshu.conversation.persistence import lock_material_row
from .attention import AttentionPolicy
from .contacts import DEFAULT_EXPIRY_SECONDS
from .validation import revision, text, timestamp, timezone

TRANSITIONS = {
    "planned": {"running", "cancelled", "dormant"},
    "running": {"paused", "dormant", "completed", "cancelled"},
    "paused": {"running", "dormant", "cancelled"},
    "dormant": {"running", "cancelled"}, "completed": set(), "cancelled": set(),
}


def public_record(row):
    value={key:item.isoformat() if isinstance(item,date) and not isinstance(item,datetime) else item
        for key,item in row.items() if key not in {"context","frozen_action"}}
    if "checkpoint_data" in value:
        value["checkpoint_data"]={key:item for key,item in value["checkpoint_data"].items() if key not in {"planning_context","frozen_action"}}
    return value


class LifeService:
    def __init__(self, database, commands, jobs, identity, companion, contacts, *,
                 sources, memory, weather=None):
        self.database, self.commands, self.jobs = database, commands, jobs
        self.identity, self.companion, self.contacts = identity, companion, contacts
        self.sources, self.memory, self.weather = sources, memory, weather
        self.attention = AttentionPolicy()
        from .directions import Directions
        from .day_plan import DayPlans
        self.directions, self.days = Directions(self), DayPlans(self)
        if contacts is not None:
            contacts.bind_authority(identity)

    async def validate_persistence(self, connection, context, refs=()):
        if not await self.sources.validate_persistence(connection, context, tuple(refs)):
            raise AppError("source_unavailable", "材料已失效，请重新读取当前内容", status=409)

    async def lock_row(self, connection, context, query, parameters=(), *, additional_refs=(), related_refs=None):
        return await lock_material_row(connection, self.sources, context, query, parameters,
                                       additional_refs=additional_refs, related_refs=related_refs)

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
        valid = await self.identity.check_many(context, tuple(source_refs(row["source_refs"]) for row in (activity, outfit) if row))
        decisions = iter(valid)
        missing = []
        if activity and not next(decisions):
            activity = None
            missing.append("activity_source_unavailable")
        if outfit and not next(decisions):
            outfit = None
            missing.append("outfit_source_unavailable")
        return {"actor_id": context.actor_id, "actor_revision": actor["revision"], **state,
                "activity": public_record(activity) if activity else None, "outfit": outfit, "affect": await self.companion.affect(context),
                "observed_at": datetime.now(UTC), "missing": missing}

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
        allowed = await self.identity.check_many(context, tuple(source_refs(row.get("source_refs", ())) for row in rows[:limit]))
        for row, valid in zip(rows[:limit], allowed, strict=True):
            if valid:
                accepted.append(public_record(row))
            else:
                missing.append("source_unavailable")
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
        reason = text(body.get("reason") or "authored_goal_change", "调整理由", 500)
        criteria = body.get("criteria")
        if criteria is not None:
            from jsonschema import Draft202012Validator
            from .directions import criterion_schema
            if not Draft202012Validator(criterion_schema()).is_valid(criteria):
                raise AppError("invalid_goal_criteria")
        async def apply(connection):
            goal_id = body.get("id") or str(uuid4())
            if criteria and criteria["kind"]=="chapter_saved":
                work=await (await connection.execute("SELECT * FROM life.works WHERE id=%s AND actor_id=%s AND scope_ids<@%s::text[]",(criteria["work_id"],context.actor_id,list(context.scope_ids)))).fetchone()
                if not work:
                    raise AppError("work_not_found",status=404)
                await self.validate_persistence(connection,context,source_refs(work["source_refs"]))
            row = await self.lock_row(connection, context, "SELECT * FROM life.goals WHERE id=%s AND actor_id=%s FOR UPDATE", (goal_id, context.actor_id), additional_refs=source_refs(body.get("source_refs", ())))
            if body.get("id") and row is None:
                raise AppError("object_not_found", status=404)
            if row and "scope_ids" in row and not set(row["scope_ids"]).issubset(context.scope_ids):
                raise AppError("object_not_found",status=404)
            if row and "source_refs" in row:
                await self.validate_persistence(connection, context, source_refs(row["source_refs"]))
            revision(row, body.get("expected_revision"))
            if row and row["progress"] > 0 and criteria is not None and criteria != row["criteria"]:
                raise AppError("goal_criteria_already_started", status=409)
            next_revision = row["revision"] + 1 if row else 1
            await connection.execute(
                "INSERT INTO life.goals(id,actor_id,title,motivation,state,priority,matter_id,scope_ids,due_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT(id) DO UPDATE SET title=excluded.title,motivation=excluded.motivation,state=excluded.state,"
                "priority=excluded.priority,matter_id=excluded.matter_id,due_at=excluded.due_at,revision=%s,updated_at=now()",
                (goal_id, context.actor_id, title, motivation, state, priority, body.get("matter_id"), list(context.scope_ids),
                 timestamp(body.get("due_at"), optional=True), next_revision))
            refs=unique_refs(source_refs(row["source_refs"] if row else ()),source_refs(body.get("source_refs",())))
            intentions=unique_refs(source_refs(row["intention_source_refs"] if row else ()),source_refs(body.get("source_refs",())))
            await connection.execute("UPDATE life.goals SET source_refs=%s,intention_source_refs=%s WHERE id=%s",(document(refs),document(intentions),goal_id))
            current = await (await connection.execute(
                "UPDATE life.goals SET criteria=%s,reason=%s WHERE id=%s RETURNING *",
                (document(criteria if criteria is not None else row["criteria"] if row else {}), reason, goal_id))).fetchone()
            await self.directions.record_revision(connection, current, reason)
            await self.signal(context, f"goal:{goal_id}:{next_revision}", connection=connection)
            return CommandResult(body["client_request_id"], {"id": goal_id, "revision": next_revision}, next_revision)
        return await self.commands.execute(context, "life.goals.save", body["client_request_id"], signature(body), apply)

    async def save_activity(self, context, body):
        await self.identity.check(context)
        title = text(body.get("title"), "活动", 1000)
        kind = text(body.get("kind"), "活动类型", 128)
        state = body.get("state", "planned")
        from .events import condition
        wake_condition = condition(body.get("wake_condition"))
        if wake_condition and state == "planned":
            state = "dormant"
        if state not in TRANSITIONS:
            raise AppError("invalid_activity_state")
        due = timestamp(body.get("next_due_at"), optional=True)
        if state == "paused" and due is None and not body.get("waiting_reason"):
            raise AppError("pause_reason_required")
        if state == "dormant" and not body.get("resume_condition") and not wake_condition:
            raise AppError("resume_condition_required")
        async def apply(connection):
            activity_id = body.get("id") or str(uuid4())
            goal_id=body.get("goal_id")
            if goal_id:
                goal=await (await connection.execute("SELECT * FROM life.goals WHERE id=%s AND actor_id=%s AND scope_ids<@%s::text[]",(goal_id,context.actor_id,list(context.scope_ids)))).fetchone()
                if not goal:
                    raise AppError("goal_not_found",status=404)
            goal_refs=source_refs(goal["source_refs"]) if goal_id else ()
            row = await self.lock_row(connection, context, "SELECT * FROM life.activities WHERE id=%s AND actor_id=%s FOR UPDATE", (activity_id, context.actor_id), additional_refs=unique_refs(source_refs(body.get("source_refs", ())), goal_refs))
            if body.get("id") and row is None:
                raise AppError("object_not_found", status=404)
            if row and "scope_ids" in row and not set(row["scope_ids"]).issubset(context.scope_ids):
                raise AppError("object_not_found",status=404)
            if row and "source_refs" in row:
                await self.validate_persistence(connection, context, source_refs(row["source_refs"]))
            revision(row, body.get("expected_revision"))
            await connection.execute("SELECT actor_id FROM life.actor_state WHERE actor_id=%s FOR UPDATE", (context.actor_id,))
            if not isinstance(body.get("arguments",{}),dict):
                raise AppError("invalid_activity_arguments")
            checkpoint={**(row["checkpoint_data"] if row else {}),"capability_id":body.get("capability_id"),"arguments":body.get("arguments",{})}
            if row and row["action_id"] and checkpoint!=row["checkpoint_data"]:
                raise AppError("activity_action_unresolved",status=409,details={"action_id":row["action_id"]})
            if row and state != row["state"] and state not in TRANSITIONS[row["state"]]:
                raise AppError("invalid_activity_transition", status=409)
            if state == "running":
                other = await (await connection.execute(
                    "SELECT id FROM life.activities WHERE actor_id=%s AND state='running' AND id<>%s",
                    (context.actor_id, activity_id))).fetchone()
                if other:
                    raise AppError("activity_already_running", status=409)
                if row and row["action_id"] and row["checkpoint_data"].get("completed_action_id")!=row["action_id"]:
                    raise AppError("outcome_unknown", status=409, details={"action_id": row["action_id"]})
            next_revision = row["revision"] + 1 if row else 1
            await connection.execute(
                "INSERT INTO life.activities(id,actor_id,goal_id,title,kind,state,next_due_at,resume_condition,waiting_reason,matter_id,scope_ids) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(id) DO UPDATE SET "
                "title=excluded.title,kind=excluded.kind,state=excluded.state,next_due_at=excluded.next_due_at,"
                "resume_condition=excluded.resume_condition,waiting_reason=excluded.waiting_reason,revision=%s,updated_at=now()",
                (activity_id, context.actor_id, body.get("goal_id"), title, kind, state, due,
                 body.get("resume_condition") or (wake_condition["kind"] if wake_condition else None),
                 body.get("waiting_reason") or ("waiting_for_event:"+wake_condition["kind"] if wake_condition else None),
                 body.get("matter_id"), list(context.scope_ids), next_revision))
            await connection.execute("UPDATE life.activities SET wake_condition=%s,context=%s WHERE id=%s",
                (document(wake_condition) if wake_condition else None, document(context), activity_id))
            await connection.execute("UPDATE life.activities SET goal_id=%s,checkpoint_data=%s,source_refs=%s WHERE id=%s",(goal_id,document(checkpoint),document(unique_refs(source_refs(row["source_refs"] if row else ()),source_refs(body.get("source_refs",())),goal_refs)),activity_id))
            if state == "running":
                await connection.execute("UPDATE life.actor_state SET activity_id=%s,revision=revision+1,updated_at=now() WHERE actor_id=%s", (activity_id, context.actor_id))
            else:
                await connection.execute("UPDATE life.actor_state SET activity_id=NULL,revision=revision+1 WHERE actor_id=%s AND activity_id=%s", (context.actor_id, activity_id))
            if state in {"planned", "running"}:
                await self.jobs.enqueue(connection, JobSpec("life.activity_step", f"activity:{activity_id}:{next_revision}",
                    {"activity_id": activity_id, "expected_revision": next_revision, "context": self._context(context)},
                    ServiceClass.ELASTIC, due or datetime.now(UTC), context.actor_id, body.get("matter_id")))
            return CommandResult(body["client_request_id"], {"id": activity_id, "revision": next_revision, "state": state}, next_revision)
        return await self.commands.execute(context, "life.activities.save", body["client_request_id"], signature(body), apply)

    async def transition_activity(self, context, body, target, *, explicit=True):
        await self.identity.check(context)
        activity_id = text(body.get("id"), "活动标识", 128)
        async def apply(connection):
            row = await self.lock_row(connection, context, "SELECT * FROM life.activities WHERE id=%s AND actor_id=%s FOR UPDATE", (activity_id, context.actor_id), additional_refs=source_refs(body.get("source_refs", ())))
            if not row:
                raise AppError("activity_not_found", status=404)
            if body.get("id") and row is None:
                raise AppError("object_not_found", status=404)
            if row and "scope_ids" in row and not set(row["scope_ids"]).issubset(context.scope_ids):
                raise AppError("object_not_found",status=404)
            if row and "source_refs" in row:
                await self.validate_persistence(connection, context, source_refs(row["source_refs"]))
            revision(row, body.get("expected_revision"))
            await connection.execute("SELECT actor_id FROM life.actor_state WHERE actor_id=%s FOR UPDATE", (context.actor_id,))
            if target not in TRANSITIONS[row["state"]]:
                raise AppError("invalid_activity_transition", status=409)
            if target == "running" and not explicit and row["protected_until"] and row["protected_until"] > datetime.now(UTC):
                raise AppError("activity_protected", status=409, details={"until": row["protected_until"].isoformat()})
            if target == "running" and row["action_id"] and row["checkpoint_data"].get("completed_action_id")!=row["action_id"]:
                raise AppError("outcome_unknown", status=409, details={"action_id": row["action_id"]})
            if target=='running' and row['waiting_reason']=='follow_up_available':
                raise AppError('activity_follow_up_requires_arrangement',status=409)
            if target == "running" and row["waiting_reason"] == "continuation_available":
                checkpoint = row["checkpoint_data"]
                from .continuation import resumed_checkpoint
                checkpoint = resumed_checkpoint(row)
                await connection.execute("UPDATE life.activities SET action_id=NULL,last_result_revision=-1,last_receipt_id=NULL,checkpoint_data=%s WHERE id=%s",
                    (document(checkpoint),activity_id))
            if target == "running":
                other = await (await connection.execute("SELECT id FROM life.activities WHERE actor_id=%s AND state='running' AND id<>%s", (context.actor_id, activity_id))).fetchone()
                if other:
                    raise AppError("activity_already_running", status=409)
            next_revision = row["revision"] + 1
            waiting = "paused_by_user" if target == "paused" else None
            await connection.execute("UPDATE life.activities SET state=%s,revision=%s,waiting_reason=%s,next_due_at=NULL,updated_at=now() WHERE id=%s", (target, next_revision, waiting, activity_id))
            if target == "running":
                await connection.execute("UPDATE life.actor_state SET activity_id=%s,revision=revision+1 WHERE actor_id=%s", (activity_id, context.actor_id))
            else:
                await connection.execute("UPDATE life.actor_state SET activity_id=NULL,revision=revision+1 WHERE actor_id=%s AND activity_id=%s", (context.actor_id, activity_id))
                await connection.execute("UPDATE life.day_slots SET state=%s,revision=revision+1 WHERE activity_id=%s AND state IN ('planned','running','waiting_result')",(target,activity_id))
            if target == "running":
                await self.jobs.enqueue(connection, JobSpec("life.activity_step", f"activity:{activity_id}:{next_revision}",
                    {"activity_id": activity_id, "expected_revision": next_revision, "context": self._context(context)}, subject_id=context.actor_id))
            elif target == "cancelled" and row["action_id"]:
                await self.jobs.enqueue(connection, JobSpec("life.activity_cancel", f"activity-cancel:{activity_id}:{next_revision}",
                    {"activity_id": activity_id, "context": self._context(context)}, subject_id=context.actor_id))
            await self.signal(context,f"activity-user:{activity_id}:{next_revision}",connection=connection)
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

    async def save_commitment(self, context, body, *, new_id=None):
        await self.identity.check(context)
        title = text(body.get("title"), "约定", 2000)
        due = timestamp(body.get("due_at"), optional=True)
        from .events import condition
        wake_condition = condition(body.get("wake_condition"))
        if not due and not wake_condition:
            raise AppError("commitment_wake_condition_required")
        grace = body.get("grace_seconds", 3600)
        policy = body.get("missed_policy", "skip")
        if type(grace) is not int or not 0 <= grace <= 604800 or policy not in {"skip", "once", "reschedule"}:
            raise AppError("invalid_commitment_window")
        zone = timezone(body.get("timezone", "Asia/Shanghai"))
        repeat = body.get("recurrence")
        days = body.get("weekdays", [])
        if repeat not in (None, "daily", "weekly") or any(type(day) is not int or not 0 <= day <= 6 for day in days):
            raise AppError("invalid_recurrence")
        if repeat == "weekly" and not days:
            raise AppError("weekdays_required")
        async def apply(connection):
            commitment_id = body.get("id") or new_id or str(uuid4())
            row = await self.lock_row(connection, context, "SELECT * FROM life.commitments WHERE id=%s AND actor_id=%s FOR UPDATE", (commitment_id, context.actor_id), additional_refs=source_refs(body.get("source_refs", ())))
            if body.get("id") and row is None:
                raise AppError("object_not_found", status=404)
            if row and "scope_ids" in row and not set(row["scope_ids"]).issubset(context.scope_ids):
                raise AppError("object_not_found",status=404)
            if row and "source_refs" in row:
                await self.validate_persistence(connection, context, source_refs(row["source_refs"]))
            revision(row, body.get("expected_revision"))
            if row and row["state"] not in {"scheduled", "waiting"}:
                raise AppError("commitment_already_started", status=409)
            next_revision = row["revision"] + 1 if row else 1
            refs=unique_refs(tuple(ref for ref in (*source_refs(row["source_refs"] if row else ()),*source_refs(body.get("source_refs",())))))
            await self.validate_persistence(connection,context,refs)
            # The destination is the identity-resolved conversation, never a payload target.
            target = context.conversation_id
            await connection.execute(
                "INSERT INTO life.commitments(id,actor_id,title,due_at,timezone,state,target_ref,person_id,matter_id,capability_id,arguments,"
                "recurrence,weekdays,scope_ids,context) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT(id) DO UPDATE SET title=excluded.title,due_at=excluded.due_at,timezone=excluded.timezone,"
                "state=excluded.state,target_ref=excluded.target_ref,person_id=excluded.person_id,matter_id=excluded.matter_id,scope_ids=excluded.scope_ids,"
                "recurrence=excluded.recurrence,weekdays=excluded.weekdays,capability_id=excluded.capability_id,arguments=excluded.arguments,revision=%s,reason=NULL,context=excluded.context,updated_at=now()",
                (commitment_id, context.actor_id, title, due, zone, "scheduled" if due else "waiting", target,
                 context.person_id, body.get("matter_id"), body.get("capability_id"), document(body.get("arguments", {})),
                 repeat, days, list(context.scope_ids), document(context), next_revision))
            await connection.execute("UPDATE life.commitments SET source_refs=%s WHERE id=%s",(document(refs),commitment_id))
            await connection.execute("UPDATE life.commitments SET wake_condition=%s,grace_seconds=%s,missed_policy=%s WHERE id=%s",
                (document(wake_condition) if wake_condition else None, grace, policy, commitment_id))
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
        await self.identity.check(context)
        due = timestamp(body.get("due_at"))
        async def apply(connection):
            row = await self.lock_row(connection, context,
                "SELECT * FROM life.commitments WHERE id=%s AND actor_id=%s FOR UPDATE",
                (body.get("id"), context.actor_id))
            if not row:
                raise AppError("commitment_not_found", status=404)
            if row and "scope_ids" in row and not set(row["scope_ids"]).issubset(context.scope_ids):
                raise AppError("object_not_found",status=404)
            if row and "source_refs" in row:
                await self.validate_persistence(connection, context, source_refs(row["source_refs"]))
            revision(row, body.get("expected_revision"))
            if row["state"] not in {"scheduled", "waiting"}:
                raise AppError("commitment_already_started", status=409)
            zone = timezone(body.get("timezone", row["timezone"]))
            next_revision = row["revision"] + 1
            await self.jobs.cancel(connection, f"commitment:{row['id']}:{row['revision']}")
            await connection.execute(
                "UPDATE life.commitments SET due_at=%s,timezone=%s,state='scheduled',wake_condition=NULL,reason=NULL,revision=%s,updated_at=now() WHERE id=%s",
                (due, zone, next_revision, row["id"]))
            await self.jobs.enqueue(connection, JobSpec("life.commitment_due", f"commitment:{row['id']}:{next_revision}",
                {"commitment_id": row["id"], "expected_revision": next_revision}, ServiceClass.DUE, due,
                context.actor_id, row["matter_id"]))
            await self.signal(context, f"commitment:{row['id']}:{next_revision}", connection=connection)
            return CommandResult(body["client_request_id"], {"id": row["id"], "revision": next_revision,
                                 "state": "scheduled", "due_at": due}, next_revision)
        return await self.commands.execute(context, "life.commitments.reschedule", body["client_request_id"], signature(body), apply)

    async def cancel_commitment(self, context, body):
        await self.identity.check(context)
        async def apply(connection):
            row = await self.lock_row(connection, context, "SELECT * FROM life.commitments WHERE id=%s AND actor_id=%s FOR UPDATE", (body.get("id"), context.actor_id))
            if not row:
                raise AppError("commitment_not_found", status=404)
            if body.get("id") and row is None:
                raise AppError("object_not_found", status=404)
            if row and "scope_ids" in row and not set(row["scope_ids"]).issubset(context.scope_ids):
                raise AppError("object_not_found",status=404)
            if row and "source_refs" in row:
                await self.validate_persistence(connection, context, source_refs(row["source_refs"]))
            revision(row, body.get("expected_revision"))
            if row["state"] not in {"scheduled", "waiting"}:
                raise AppError("commitment_already_started", status=409)
            await self.jobs.cancel(connection, f"commitment:{row['id']}:{row['revision']}")
            await connection.execute("UPDATE life.commitments SET state='cancelled',revision=revision+1,reason='cancelled_by_user',updated_at=now() WHERE id=%s", (row["id"],))
            await self.signal(context, f"commitment:{row['id']}:{row['revision']+1}", connection=connection)
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
            row = await self.lock_row(connection, context, "SELECT * FROM life.contact_preferences WHERE actor_id=%s AND person_id=%s FOR UPDATE", (context.actor_id, context.person_id or ""))
            if body.get("id") and row is None:
                raise AppError("object_not_found", status=404)
            if row and "scope_ids" in row and not set(row["scope_ids"]).issubset(context.scope_ids):
                raise AppError("object_not_found",status=404)
            if row and "source_refs" in row:
                await self.validate_persistence(connection, context, source_refs(row["source_refs"]))
            revision(row, body.get("expected_revision"))
            next_revision = row["revision"] + 1 if row else 1
            expiry = body.get("expiry_seconds", row["expiry_seconds"] if row else DEFAULT_EXPIRY_SECONDS)
            if type(expiry) is not int or not 60 <= expiry <= 2592000:
                raise AppError("invalid_contact_expiry")
            responsiveness=body.get("responsiveness",row["responsiveness"] if row else .5)
            availability=body.get("availability",row["availability"] if row else "unknown")
            available_until=timestamp(body.get("available_until",row["available_until"] if row else None),optional=True)
            if type(responsiveness) not in (int,float) or not 0<=responsiveness<=1 or availability not in {"unknown","free","busy","sleeping","away"}:
                raise AppError("invalid_contact_preferences")
            if type(body.get("enabled",True)) is not bool:
                raise AppError("invalid_contact_preferences")
            await connection.execute(
                "INSERT INTO life.contact_preferences(actor_id,person_id,revision,timezone,quiet_start,quiet_end,cooldown_seconds,daily_quota,unanswered_limit,enabled,scope_ids) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(actor_id,person_id) DO UPDATE SET "
                "revision=excluded.revision,timezone=excluded.timezone,quiet_start=excluded.quiet_start,quiet_end=excluded.quiet_end,"
                "cooldown_seconds=excluded.cooldown_seconds,daily_quota=excluded.daily_quota,unanswered_limit=excluded.unanswered_limit,enabled=excluded.enabled",
                (context.actor_id, context.person_id or "", next_revision, zone, body["quiet_start"], body["quiet_end"], body["cooldown_seconds"], body["daily_quota"], body["unanswered_limit"], body.get("enabled", True), list(context.scope_ids)))
            await connection.execute("UPDATE life.contact_preferences SET responsiveness=%s,availability=%s,available_until=%s,expiry_seconds=%s WHERE actor_id=%s AND person_id=%s",(responsiveness,availability,available_until,expiry,context.actor_id,context.person_id or ""))
            if "availability" in body or "available_until" in body:
                await connection.execute("UPDATE life.contact_preferences SET availability_event_id=NULL,availability_source_refs='[]',availability_occurred_at=now() WHERE actor_id=%s AND person_id=%s",(context.actor_id,context.person_id or ""))
            return CommandResult(body["client_request_id"], {"revision": next_revision}, next_revision)
        return await self.commands.execute(context, "life.contact_preferences.save", body["client_request_id"], signature(body), apply)

    async def signal(self, context, watermark, *, connection=None, change=None):
        from .planning import signal
        if connection is None:
            await self.identity.check(context)
            async with self.database.transaction() as selected:
                await self.validate_persistence(selected,context)
                return await signal(selected,self.jobs,context,watermark,change=change)
        await self.validate_persistence(connection,context)
        return await signal(connection,self.jobs,context,watermark,change=change)

    async def received(self, context, *, input_id, occurred_at, connection):
        from .events import wake
        await self.validate_persistence(connection, context)
        await connection.execute(
            "UPDATE life.contact_candidates SET state='cancelled',reason='new_human_input',revision=revision+1 "
            "WHERE actor_id=%s AND target_ref=%s AND NOT explicit AND state IN ('pending','waiting') AND created_at<=%s",
            (context.actor_id, context.conversation_id, occurred_at))
        for subject in {context.person_id, context.conversation_id} - {None}:
            await wake(self, context, kind="user_reply", subject_id=subject, event_id=input_id, connection=connection)

    async def wake(self, context, *, kind, subject_id, event_id, connection=None):
        from .events import wake
        await self.identity.check(context)
        if connection is None:
            async with self.database.transaction() as transaction:
                await wake(self, context, kind=kind, subject_id=subject_id, event_id=event_id, connection=transaction)
        else:
            await wake(self, context, kind=kind, subject_id=subject_id, event_id=event_id, connection=connection)

    async def writing_completed(self,context,chapter,refs,connection):
        from .writing_progress import completed
        return await completed(self,context,chapter,refs,connection)
