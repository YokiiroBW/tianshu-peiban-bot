"""A few executable appointments and genuine free time, with per-activity fences."""
from datetime import UTC, datetime, time, timedelta
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

from tianshu.conversation.codec import document, source_refs, signature, context_from_record
from tianshu.runtime.errors import AppError
from tianshu.runtime.api import CommandResult
from tianshu.runtime.jobs import JobSpec
from tianshu.runtime.serialization import wire
from tianshu.sources.dependencies import unique_refs
from .clock import awake_and_due, hour


MAX_ACTIVITIES = 4
GAP_MINUTES = 20
COMMITMENT_MARGIN = timedelta(minutes=10)


def awake_window(routine, now):
    routine = routine or {"timezone": "Asia/Shanghai", "wake_at": "08:00", "sleep_at": "23:00", "idle_seconds": 5400}
    zone = ZoneInfo(routine["timezone"])
    local = now.astimezone(zone)
    awake, due = awake_and_due(routine, now)
    if not awake:
        return None, due
    end = datetime.combine(local.date(), hour(routine["sleep_at"]), zone)
    if end <= local:
        end += timedelta(days=1)
    # A plan belongs to a local calendar day even for night-shift routines.
    midnight = datetime.combine(local.date() + timedelta(days=1), time(), zone)
    return now, min(end, midnight).astimezone(UTC)


def next_gap(start, duration, end, occupied):
    """Do not put an activity on a promise or an already retained appointment."""
    finish = start + duration
    for begin, until in sorted(occupied):
        if until <= start:
            continue
        if finish <= begin:
            break
        start, finish = until + timedelta(minutes=GAP_MINUTES), until + timedelta(minutes=GAP_MINUTES) + duration
    return (start, finish) if finish <= end else None


def resumable(row, now):
    return (row["state"] == "dormant" and row["waiting_reason"] in {"continuation_available", "day_finished", "follow_up_available"}
        and (not row["protected_until"] or row["protected_until"] <= now)
        and (not row["action_id"] or row["checkpoint_data"].get("completed_action_id") == row["action_id"]))


class DayPlans:
    def __init__(self, life):
        self.life = life

    async def _activities(self, connection, context, day):
        slots = await (await connection.execute(
            "SELECT s.*,a.title,a.kind AS activity_kind,a.goal_id,a.waiting_reason,a.checkpoint_data "
            "FROM life.day_slots s LEFT JOIN life.activities a ON a.id=s.activity_id "
            "WHERE s.actor_id=%s AND s.day=%s AND s.scope_ids<@%s::text[] ORDER BY s.starts_at,s.id",
            (context.actor_id, day, list(context.scope_ids)))).fetchall()
        unfinished = await (await connection.execute(
            "SELECT * FROM life.activities WHERE actor_id=%s AND scope_ids<@%s::text[] "
            "AND state IN ('planned','running','paused','dormant') ORDER BY updated_at DESC,id LIMIT 25",
            (context.actor_id, list(context.scope_ids)))).fetchall()
        return slots, unfinished

    async def decision_state(self, connection, context, day):
        # A newly accepted plan/receipt may have source originals visible only to
        # this transaction. Its reviewed fingerprint must describe that state.
        slots, unfinished = await self._activities(connection, context, day)
        async def authorized(rows):
            return [row for row in rows if await self.life.sources.validate_persistence(connection, context, source_refs(row["source_refs"] or ()))]
        return {"day_plan": await authorized(slots), "unfinished": await authorized(unfinished)}

    async def snapshot(self, context, day):
        async with self.life.database.transaction(read_only=True) as connection:
            plan = await (await connection.execute(
                "SELECT * FROM life.day_plans WHERE actor_id=%s AND day=%s AND scope_ids<@%s::text[]",
                (context.actor_id, day, list(context.scope_ids)))).fetchone()
            slots, unfinished = await self._activities(connection, context, day)
            recent=await (await connection.execute(
                "SELECT * FROM life.activities WHERE actor_id=%s AND scope_ids<@%s::text[] "
                "AND checkpoint_data->>'last_execution'='succeeded' AND checkpoint_data->>'capability_id'<>'images.generate' "
                "AND updated_at>now()-interval '1 day' ORDER BY updated_at DESC,id LIMIT 8",
                (context.actor_id,list(context.scope_ids)))).fetchall()
        allowed = await self.life.identity.check_many(context, tuple(source_refs(row["source_refs"] or ()) for row in slots))
        slots = [row for row, valid in zip(slots, allowed, strict=True) if valid]
        if plan and not (await self.life.identity.check_many(context,(source_refs(plan["source_refs"]),)))[0]:
            plan=None
        allowed = await self.life.identity.check_many(context, tuple(source_refs(row["source_refs"]) for row in unfinished))
        allowed_recent=await self.life.identity.check_many(context,tuple(source_refs(row["source_refs"]) for row in recent))
        return {"plan": plan, "slots": slots,"recent_results":[row for row,valid in zip(recent,allowed_recent,strict=True) if valid],
            "unfinished": [row for row, valid in zip(unfinished, allowed, strict=True) if valid]}

    async def view(self, context, day=None):
        await self.life.identity.check(context)
        state = await self.life.state(context)
        local_day = datetime.now(ZoneInfo(state["timezone"])).date()
        if day:
            from datetime import date
            try:
                local_day = date.fromisoformat(day)
            except (TypeError, ValueError):
                raise AppError("invalid_day") from None
        data = await self.snapshot(context, local_day)
        from .service import public_record
        data={"plan":public_record(data["plan"]) if data["plan"] else None,
            "slots":[public_record(row) for row in data["slots"]],
            "unfinished":[public_record(row) for row in data["unfinished"]],
            "recent_results":[public_record(row) for row in data["recent_results"]]}
        async with self.life.database.transaction(read_only=True) as connection:
            planning=await (await connection.execute("SELECT revision,reason,next_due_at,last_planned_at,generation_consumed,"
                "last_error,repair_count,repair_due_at,review_after,"
                "(planned_watermark<>change_watermark OR (generation_key IS NOT NULL AND NOT generation_consumed)) AS pending "
                "FROM life.plan_watermarks WHERE actor_id=%s",(context.actor_id,))).fetchone()
        return {"day": local_day.isoformat(), **data, "planning":planning}

    async def reconsider(self, context, body):
        from .validation import text, revision
        await self.life.identity.check(context)
        reason=text(body.get("reason") or "authored_reconsideration","调整理由",500)
        async def apply(connection):
            state=await self.life._state(context,connection)
            day=datetime.now(ZoneInfo(state["timezone"])).date()
            row=await (await connection.execute("SELECT * FROM life.day_plans WHERE actor_id=%s AND day=%s AND scope_ids<@%s::text[] FOR UPDATE",(context.actor_id,day,list(context.scope_ids)))).fetchone()
            if body.get("expected_revision") is not None:
                revision(row,body["expected_revision"])
            await self.life.signal(context,"reconsider:"+body["client_request_id"],connection=connection,change={"reason":reason})
            return CommandResult(body["client_request_id"],{"state":"queued","day":day.isoformat()},row["revision"] if row else 0)
        return await self.life.commands.execute(context,"life.day-plan.reconsider",body["client_request_id"],signature(body),apply)

    async def move(self, context, body):
        from .validation import timestamp, revision
        await self.life.identity.check(context)
        starts=timestamp(body.get("starts_at"))
        async def apply(connection):
            slot=await (await connection.execute("SELECT * FROM life.day_slots WHERE id=%s AND actor_id=%s AND kind='activity' AND scope_ids<@%s::text[]",(body.get("id"),context.actor_id,list(context.scope_ids)))).fetchone()
            if not slot:
                raise AppError("day_slot_not_found",status=404)
            row=await self.life.lock_row(connection,context,"SELECT * FROM life.activities WHERE id=%s AND actor_id=%s FOR UPDATE",(slot["activity_id"],context.actor_id))
            slot=await (await connection.execute("SELECT * FROM life.day_slots WHERE id=%s AND actor_id=%s FOR UPDATE",(slot["id"],context.actor_id))).fetchone()
            revision(slot,body.get("expected_revision"))
            if slot["state"]!='planned' or row["state"]!='planned' or row["action_id"]:
                raise AppError("day_slot_already_started",status=409)
            routine=await (await connection.execute("SELECT * FROM life.routines WHERE actor_id=%s",(context.actor_id,))).fetchone()
            begin,end=awake_window(routine,starts)
            finish=starts+(slot["ends_at"]-slot["starts_at"])
            zone=ZoneInfo(routine["timezone"] if routine else "Asia/Shanghai")
            if starts<=datetime.now(UTC) or starts.astimezone(zone).date()!=slot["day"] or begin is None or finish>end:
                raise AppError("invalid_day_slot_window")
            conflict=await (await connection.execute("SELECT 1 FROM life.day_slots WHERE actor_id=%s AND id<>%s AND kind='activity' AND state IN ('planned','running') AND starts_at<%s AND ends_at>%s LIMIT 1",(context.actor_id,slot["id"],finish,starts))).fetchone()
            if conflict:
                raise AppError("day_slot_conflict",status=409)
            promise=await (await connection.execute("SELECT 1 FROM life.commitments WHERE actor_id=%s AND state IN ('scheduled','waiting') AND due_at>%s AND due_at<%s LIMIT 1",(context.actor_id,starts-COMMITMENT_MARGIN,finish+COMMITMENT_MARGIN))).fetchone()
            if promise:
                raise AppError('day_slot_conflict',status=409)
            version=row["revision"]+1
            from .continuation import execution_window
            data = execution_window(row["checkpoint_data"], starts, finish)
            await connection.execute("UPDATE life.day_slots SET starts_at=%s,ends_at=%s,revision=revision+1,reason='authored_reschedule' WHERE id=%s",(starts,finish,slot["id"]))
            await connection.execute("UPDATE life.activities SET next_due_at=%s,checkpoint_data=%s,revision=%s WHERE id=%s",(starts,document(data),version,row["id"]))
            original=context_from_record(row["context"]) if row["context"] else context
            await self.life.jobs.enqueue(connection,JobSpec("life.activity_step",f"activity:{row['id']}:{version}",{"activity_id":row["id"],"expected_revision":version,"context":wire(original)},scheduled_at=starts,subject_id=context.actor_id))
            await self._free_time(connection,context,slot["day"],datetime.now(UTC),end,"留白，允许休息和临时变化",source_refs(row["source_refs"]))
            return CommandResult(body["client_request_id"],{"id":slot["id"],"revision":slot["revision"]+1,"starts_at":starts,"ends_at":finish},slot["revision"]+1)
        return await self.life.commands.execute(context,"life.day-plan.move",body["client_request_id"],signature(body),apply)

    async def close(self, connection, context, day):
        """Only unstarted appointments carry; running or unknown work retains its ID."""
        rows = await (await connection.execute(
            "SELECT a.* FROM life.activities a JOIN life.day_slots s ON s.activity_id=a.id "
            "WHERE s.actor_id=%s AND s.day=%s AND s.state='planned' AND a.state='planned' AND a.action_id IS NULL AND a.scope_ids<@%s::text[] FOR UPDATE OF a",
            (context.actor_id, day, list(context.scope_ids)))).fetchall()
        for row in rows:
            await connection.execute(
                "UPDATE life.activities SET state='dormant',waiting_reason='day_finished',resume_condition='next_day_plan',"
                "next_due_at=NULL,revision=revision+1 WHERE id=%s", (row["id"],))
        await connection.execute(
            "UPDATE life.day_slots SET state='carried',revision=revision+1 WHERE actor_id=%s AND day=%s AND state='planned' AND kind='activity' AND scope_ids<@%s::text[]",
            (context.actor_id, day, list(context.scope_ids)))
        await connection.execute("UPDATE life.day_plans SET state='closed',updated_at=now() WHERE actor_id=%s AND day=%s AND scope_ids<@%s::text[]",
            (context.actor_id, day, list(context.scope_ids)))

    async def apply(self, connection, context, decision, frozen_data, generation_key, aliases, refs, routine, now, fingerprint):
        day = now.astimezone(ZoneInfo(routine["timezone"] if routine else "Asia/Shanghai")).date()
        old = await (await connection.execute(
            "SELECT * FROM life.day_plans WHERE actor_id=%s AND day=%s FOR UPDATE", (context.actor_id, day))).fetchone()
        if old is None or set(old["scope_ids"]) <= set(context.scope_ids):
            await connection.execute(
            "INSERT INTO life.day_plans(actor_id,day,timezone,reason,context,scope_ids,source_refs,material_fingerprint) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(actor_id,day) DO UPDATE SET revision=life.day_plans.revision+1,"
            "reason=excluded.reason,context=excluded.context,source_refs=excluded.source_refs,material_fingerprint=excluded.material_fingerprint,updated_at=now()",
                (context.actor_id, day, routine["timezone"] if routine else "Asia/Shanghai", decision["reason"], document(context),
                 list(context.scope_ids), document(unique_refs(source_refs(old["source_refs"]) if old else (), refs)), fingerprint))
        # A local replan can retract an unstarted proposal. It cannot erase an action.
        original_activities = {row["id"]: row for row in frozen_data.get("unfinished", ())}
        for identifier in decision.get("cancel_activity_ids", ()):
            original = original_activities[identifier]
            row = await self.life.lock_row(connection, context,
                "SELECT * FROM life.activities WHERE id=%s AND actor_id=%s FOR UPDATE", (identifier, context.actor_id))
            if row and row["revision"] == original["revision"] and row["state"] == "planned" and not row["action_id"]:
                await connection.execute("UPDATE life.activities SET state='cancelled',waiting_reason='local_replan',revision=revision+1 WHERE id=%s", (identifier,))
                await connection.execute("UPDATE life.day_slots SET state='cancelled',reason='local_replan',revision=revision+1 WHERE activity_id=%s AND state='planned'", (identifier,))
        # Terminal directions only retract their own unstarted appointments.
        await connection.execute(
            "UPDATE life.activities a SET state='dormant',waiting_reason='goal_not_active',resume_condition='goal_active',revision=a.revision+1 "
            "FROM life.goals g WHERE a.goal_id=g.id AND g.state<>'active' AND a.actor_id=%s AND a.state='planned' AND a.action_id IS NULL AND a.scope_ids<@%s::text[] AND g.scope_ids<@%s::text[]",
            (context.actor_id,list(context.scope_ids),list(context.scope_ids)))
        await connection.execute(
            "UPDATE life.day_slots s SET state='cancelled',reason='goal_not_active',revision=s.revision+1 "
            "FROM life.activities a WHERE s.activity_id=a.id AND s.state='planned' AND a.waiting_reason='goal_not_active' AND s.actor_id=%s AND s.scope_ids<@%s::text[]", (context.actor_id,list(context.scope_ids)))
        retained = await (await connection.execute(
            "SELECT * FROM life.day_slots WHERE actor_id=%s AND day=%s AND kind='activity' AND state NOT IN ('cancelled','carried') ORDER BY starts_at",
            (context.actor_id, day))).fetchall()
        start, end = awake_window(routine, now)
        if start is None:
            return 0
        occupied = [(row["starts_at"], row["ends_at"]) for row in retained if row["state"] in {'planned','running'}]
        for commitment in frozen_data["commitments"]:
            due = commitment.get("due_at")
            if due:
                due = datetime.fromisoformat(due) if isinstance(due, str) else due
                occupied.append((due - COMMITMENT_MARGIN, due + COMMITMENT_MARGIN))
        offered = ([decision["activity"]] if decision["activity"] else []) + decision.get("arrangements", [])
        total_minutes = sum(max(0, (row["ends_at"] - max(row["starts_at"], now)).total_seconds() / 60) for row in retained if row["state"] in {'planned','running'})
        available_minutes = (end - start).total_seconds() / 60
        created = 0
        retained_ids = {row["activity_id"] for row in retained}
        for index, activity in enumerate(offered):
            identifier = activity.get("existing_activity_id")
            if identifier not in retained_ids and len(retained_ids) >= MAX_ACTIVITIES:
                continue
            duration = timedelta(minutes=activity["duration_minutes"])
            if total_minutes + duration.total_seconds() / 60 > available_minutes * .65:
                continue
            begins = now + timedelta(minutes=activity.get("after_minutes", index * GAP_MINUTES))
            period = next_gap(begins, duration, end - timedelta(minutes=GAP_MINUTES), occupied)
            if period is None:
                continue
            begins, finish = period
            if identifier:
                original = original_activities[identifier]
                row = await self.life.lock_row(connection, context,
                    "SELECT * FROM life.activities WHERE id=%s AND actor_id=%s FOR UPDATE", (identifier, context.actor_id))
                if not row or row["revision"] != original["revision"] or not resumable(row, now):
                    continue
                data = dict(row["checkpoint_data"])
                if row["waiting_reason"] in {"continuation_available","follow_up_available"}:
                    from .continuation import resumed_checkpoint
                    data=resumed_checkpoint(row,activity.get("continuation_key"))
                data.pop("continuation", None)
                data["duration_minutes"] = activity["duration_minutes"]
                from .continuation import execution_window
                data = execution_window(data, begins, finish)
                version = row["revision"] + 1
                await connection.execute(
                    "UPDATE life.activities SET state='planned',waiting_reason='day_plan_slot',resume_condition=NULL,next_due_at=%s,"
                    "checkpoint_data=%s,action_id=NULL,last_receipt_id=NULL,last_result_revision=-1,revision=%s WHERE id=%s",
                    (begins, document(data), version, identifier))
            else:
                identifier = str(uuid5(NAMESPACE_URL, generation_key + f":activity:{index}"))
                goal_id = aliases.get(activity["goal_id"], activity["goal_id"])
                version = 1
                await connection.execute(
                    "INSERT INTO life.activities(id,actor_id,goal_id,title,kind,state,next_due_at,waiting_reason,checkpoint_data,scope_ids,source_refs) "
                    "VALUES (%s,%s,%s,%s,%s,'planned',%s,'day_plan_slot',%s,%s,%s) ON CONFLICT DO NOTHING",
                    (identifier, context.actor_id, goal_id, activity["title"], activity["kind"], begins,
                     document({"capability_id": activity["capability_id"], "arguments": activity["arguments"],
                         "reason": decision["reason"], "duration_minutes": activity["duration_minutes"],
                         "execution_window": {"starts_at": begins.isoformat(), "ends_at": finish.isoformat(), "steps": 0, "seen_steps": []},
                         "planning_coverage": frozen_data["coverage"], "autonomous": True, "planning_context": wire(context)}), list(context.scope_ids), document(refs)))
            await connection.execute("UPDATE life.activities SET context=%s WHERE id=%s", (document(context), identifier))
            slot_id = str(uuid5(NAMESPACE_URL, generation_key + f":slot:{index}"))
            await connection.execute(
                "INSERT INTO life.day_slots(id,actor_id,day,kind,starts_at,ends_at,activity_id,reason,scope_ids,source_refs) VALUES (%s,%s,%s,'activity',%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                (slot_id, context.actor_id, day, begins, finish, identifier, decision["reason"],list(context.scope_ids),document(refs)))
            await self.life.jobs.enqueue(connection, JobSpec("life.activity_step", f"activity:{identifier}:{version}",
                {"activity_id": identifier, "expected_revision": version, "context": wire(context)}, scheduled_at=begins, subject_id=context.actor_id))
            occupied.append((begins, finish))
            retained_ids.add(identifier)
            total_minutes += duration.total_seconds() / 60
            created += 1
        await self._free_time(connection, context, day, start, end, decision.get("free_time_reason") or "留白，允许休息和临时变化",refs)
        return created

    async def _free_time(self, connection, context, day, start, end, reason, refs):
        actor_id=context.actor_id
        await connection.execute("DELETE FROM life.day_slots WHERE actor_id=%s AND day=%s AND kind='free' AND scope_ids<@%s::text[]", (actor_id, day,list(context.scope_ids)))
        occupied = await (await connection.execute(
            "SELECT starts_at,ends_at FROM life.day_slots WHERE actor_id=%s AND day=%s AND kind='activity' AND state IN ('planned','running') ORDER BY starts_at",
            (actor_id, day))).fetchall()
        promises=await (await connection.execute("SELECT due_at FROM life.commitments WHERE actor_id=%s AND state IN ('scheduled','waiting') AND due_at>%s AND due_at<%s",(actor_id,start-COMMITMENT_MARGIN,end+COMMITMENT_MARGIN))).fetchall()
        occupied.extend({'starts_at':row['due_at']-COMMITMENT_MARGIN,'ends_at':row['due_at']+COMMITMENT_MARGIN} for row in promises)
        occupied.sort(key=lambda row:row['starts_at'])
        for index, row in enumerate([*occupied, {"starts_at": end, "ends_at": end}]):
            until = min(row["starts_at"], end)
            if until > start:
                await connection.execute(
                    "INSERT INTO life.day_slots(id,actor_id,day,kind,starts_at,ends_at,reason,scope_ids,source_refs) VALUES (%s,%s,%s,'free',%s,%s,%s,%s,%s)",
                    (str(uuid5(NAMESPACE_URL, f"free:{actor_id}:{day}:{signature(sorted(context.scope_ids))}:{index}:{start.isoformat()}")), actor_id, day, start, until, reason,list(context.scope_ids),document(refs)))
            start = max(start, row["ends_at"])

    async def result(self, connection, activity_id, *, continuing=False, state="completed"):
        await connection.execute(
            "UPDATE life.day_slots SET state=%s,revision=revision+1 WHERE activity_id=%s AND kind='activity' AND state IN ('planned','running','waiting_result','unknown')",
            ("partial" if continuing else state, activity_id))
