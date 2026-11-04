"""Rolling executable appointments and free time, with per-activity fences."""
from datetime import UTC, datetime, time, timedelta
from time import perf_counter
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

from tianshu.conversation.codec import document, source_refs, signature, context_from_record
from tianshu.runtime.errors import AppError
from tianshu.runtime.api import CommandResult
from tianshu.runtime.jobs import JobSpec
from tianshu.runtime.serialization import wire
from tianshu.sources.dependencies import unique_refs
from .clock import awake_and_due, hour
from .eligibility import background_work, evaluate
from .types import PlanApplyResult, PlanCandidateResult


COMMITMENT_MARGIN = timedelta(minutes=10)


def elapsed_ms(start):
    return round((perf_counter() - start) * 1000, 3)


def awake_window(routine, now):
    routine = routine or {"timezone": "Asia/Shanghai", "wake_at": "08:00", "sleep_at": "23:00", "idle_seconds": 5400}
    zone = ZoneInfo(routine["timezone"])
    local = now.astimezone(zone)
    awake, due = awake_and_due(routine, now)
    midnight = datetime.combine(local.date() + timedelta(days=1), time(), zone)
    if not awake:
        return None, min(due, midnight.astimezone(UTC))
    end = datetime.combine(local.date(), hour(routine["sleep_at"]), zone)
    if end <= local:
        end += timedelta(days=1)
    # A plan belongs to a local calendar day even for night-shift routines.
    return now, min(end, midnight).astimezone(UTC)


def next_gap(start, duration, end, occupied):
    """Do not put an activity on a promise or an already retained appointment."""
    finish = start + duration
    for begin, until in sorted(occupied):
        if until <= start:
            continue
        if finish <= begin:
            break
        start, finish = until, until + duration
    return (start, finish) if finish <= end else None


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

    async def snapshot(self, context, day, *, timings=None):
        started = perf_counter() if timings is not None else None
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
        if timings is not None:
            timings["snapshot_database"] = elapsed_ms(started)
            started = perf_counter()
        allowed = await self.life.identity.check_many(context, tuple(source_refs(row["source_refs"] or ()) for row in slots))
        slots = [row for row, valid in zip(slots, allowed, strict=True) if valid]
        if plan and not (await self.life.identity.check_many(context,(source_refs(plan["source_refs"]),)))[0]:
            plan=None
        allowed = await self.life.identity.check_many(context, tuple(source_refs(row["source_refs"]) for row in unfinished))
        allowed_recent=await self.life.identity.check_many(context,tuple(source_refs(row["source_refs"]) for row in recent))
        result = {"plan": plan, "slots": slots,"recent_results":[row for row,valid in zip(recent,allowed_recent,strict=True) if valid],
            "unfinished": [row for row, valid in zip(unfinished, allowed, strict=True) if valid]}
        if timings is not None:
            timings["snapshot_source_check"] = elapsed_ms(started)
        return result

    async def view(self, context, day=None):
        view_started = perf_counter()
        started = perf_counter()
        await self.life.identity.check(context)
        identity_ms = elapsed_ms(started)
        started = perf_counter()
        state = await self.life.state(context)
        state_ms = elapsed_ms(started)
        local_day = datetime.now(ZoneInfo(state["timezone"])).date()
        if day:
            from datetime import date
            try:
                local_day = date.fromisoformat(day)
            except (TypeError, ValueError):
                raise AppError("invalid_day") from None
        timings = {"identity_check": identity_ms, "state_lookup": state_ms,
            "application_database": None, "json_serialization": None, "http_total": None}
        data = await self.snapshot(context, local_day, timings=timings)
        application = None
        if data["plan"]:
            started = perf_counter()
            async with self.life.database.transaction(read_only=True) as connection:
                application = await (await connection.execute(
                    "SELECT generation_key,application_results FROM life.plan_generations "
                    "WHERE actor_id=%s AND day=%s AND state='applied' AND application_results IS NOT NULL "
                    "AND context->'scope_ids'<@%s::jsonb AND context->'audience'<@%s::jsonb "
                    "ORDER BY reviewed_at DESC,generation_key DESC LIMIT 1",
                    (context.actor_id, local_day, document(sorted(context.scope_ids)), document(sorted(context.audience))))).fetchone()
            timings["application_database"] = elapsed_ms(started)
        started = perf_counter()
        if application:
            application = {"generation_key": application["generation_key"], **application["application_results"]}
        from .service import public_record
        data={"plan":public_record(data["plan"]) if data["plan"] else None,
            "slots":[public_record(row) for row in data["slots"]],
            "unfinished":[public_record(row) for row in data["unfinished"]],
            "recent_results":[public_record(row) for row in data["recent_results"]]}
        timings["projection"] = elapsed_ms(started)
        started = perf_counter()
        async with self.life.database.transaction(read_only=True) as connection:
            planning=await (await connection.execute("SELECT revision,reason,next_due_at,last_planned_at,generation_consumed,"
                "last_error,repair_count,repair_due_at,review_after,"
                "(planned_watermark<>change_watermark OR (generation_key IS NOT NULL AND NOT generation_consumed)) AS pending "
                "FROM life.plan_watermarks WHERE actor_id=%s",(context.actor_id,))).fetchone()
        timings["planning_database"] = elapsed_ms(started)
        timings["view_total"] = elapsed_ms(view_started)
        return {"day": local_day.isoformat(), **data, "planning":planning, "application": application, "timings_ms": timings}

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
            background = background_work(row["checkpoint_data"])
            if starts<=datetime.now(UTC) or starts.astimezone(zone).date()!=slot["day"] or (not background and (begin is None or finish>end)):
                raise AppError("invalid_day_slot_window")
            collisions=await (await connection.execute("SELECT a.checkpoint_data FROM life.day_slots s LEFT JOIN life.activities a ON a.id=s.activity_id "
                "WHERE s.actor_id=%s AND s.id<>%s AND s.kind='activity' AND s.state IN ('planned','running') AND s.starts_at<%s AND s.ends_at>%s",
                (context.actor_id,slot["id"],finish,starts))).fetchall()
            if not background_work(row["checkpoint_data"]) and any(not background_work(item["checkpoint_data"]) for item in collisions):
                raise AppError("day_slot_conflict",status=409)
            promise=await (await connection.execute("SELECT 1 FROM life.commitments WHERE actor_id=%s AND state IN ('scheduled','waiting') AND due_at>%s AND due_at<%s LIMIT 1",(context.actor_id,starts-COMMITMENT_MARGIN,finish+COMMITMENT_MARGIN))).fetchone()
            if promise and not background:
                raise AppError('day_slot_conflict',status=409)
            version=row["revision"]+1
            from .continuation import execution_window
            data = execution_window(row["checkpoint_data"], starts, finish)
            await connection.execute("UPDATE life.day_slots SET starts_at=%s,ends_at=%s,revision=revision+1,reason='authored_reschedule' WHERE id=%s",(starts,finish,slot["id"]))
            await connection.execute("UPDATE life.activities SET next_due_at=%s,checkpoint_data=%s,revision=%s WHERE id=%s",(starts,document(data),version,row["id"]))
            original=context_from_record(row["context"]) if row["context"] else context
            await self.life.jobs.enqueue(connection,JobSpec("life.activity_step",f"activity:{row['id']}:{version}",{"activity_id":row["id"],"expected_revision":version,"context":wire(original)},scheduled_at=starts,subject_id=context.actor_id))
            current = datetime.now(UTC)
            free_start, free_end = awake_window(routine, current)
            if free_start is not None and current.astimezone(zone).date() == slot["day"]:
                await self._free_time(connection,context,slot["day"],free_start,free_end,"留白，允许休息和临时变化",source_refs(row["source_refs"]))
            await self.life.signal(context, f"activity-moved:{row['id']}:{version}", connection=connection)
            return CommandResult(body["client_request_id"],{"id":slot["id"],"revision":slot["revision"]+1,"starts_at":starts,"ends_at":finish},slot["revision"]+1)
        return await self.life.commands.execute(context,"life.day-plan.move",body["client_request_id"],signature(body),apply)

    async def close(self, connection, context, day):
        """Only unstarted appointments carry; running or unknown work retains its ID."""
        rows = await (await connection.execute(
            "SELECT a.* FROM life.activities a JOIN life.day_slots s ON s.activity_id=a.id "
            "WHERE s.actor_id=%s AND s.day=%s AND s.state='planned' AND a.state='planned' AND a.action_id IS NULL AND a.scope_ids<@%s::text[] FOR UPDATE OF a",
            (context.actor_id, day, list(context.scope_ids)))).fetchall()
        for row in rows:
            if background_work(row["checkpoint_data"]):
                continue
            await connection.execute(
                "UPDATE life.activities SET state='dormant',waiting_reason='day_finished',resume_condition='next_day_plan',"
                "next_due_at=NULL,revision=revision+1 WHERE id=%s", (row["id"],))
            await connection.execute(
                "UPDATE life.day_slots SET state='carried',revision=revision+1 WHERE actor_id=%s AND day=%s AND activity_id=%s "
                "AND state='planned' AND kind='activity' AND scope_ids<@%s::text[]",
                (context.actor_id, day, row["id"], list(context.scope_ids)))
        await connection.execute("UPDATE life.day_plans SET state='closed',updated_at=now() WHERE actor_id=%s AND day=%s AND state<>'closed' AND scope_ids<@%s::text[]",
            (context.actor_id, day, list(context.scope_ids)))

    async def apply(self, connection, context, decision, frozen_data, generation_key, aliases, refs, routine, now, fingerprint):
        day = now.astimezone(ZoneInfo(routine["timezone"] if routine else "Asia/Shanghai")).date()
        zone = ZoneInfo(routine["timezone"] if routine else "Asia/Shanghai")
        midnight = datetime.combine(day + timedelta(days=1), time(), zone).astimezone(UTC)
        old = await (await connection.execute(
            "SELECT * FROM life.day_plans WHERE actor_id=%s AND day=%s FOR UPDATE", (context.actor_id, day))).fetchone()
        editable_plan = old is None or set(old["scope_ids"]) <= set(context.scope_ids)
        if old is None:
            await connection.execute(
            "INSERT INTO life.day_plans(actor_id,day,timezone,reason,context,scope_ids,source_refs,material_fingerprint) "
            "VALUES (%s,%s,%s,'planning_incomplete',%s,%s,%s,'') ON CONFLICT(actor_id,day) DO NOTHING",
                (context.actor_id, day, routine["timezone"] if routine else "Asia/Shanghai", document(context),
                 list(context.scope_ids), document(refs)))
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
            "SELECT s.*,a.checkpoint_data FROM life.day_slots s LEFT JOIN life.activities a ON a.id=s.activity_id "
            "WHERE s.actor_id=%s AND s.day=%s AND s.kind='activity' AND s.state IN ('planned','running') ORDER BY s.starts_at",
            (context.actor_id, day))).fetchall()
        start, end = awake_window(routine, now)
        occupied = [(row["starts_at"], row["ends_at"]) for row in retained if not background_work(row.get("checkpoint_data"))]
        commitments = await (await connection.execute(
            "SELECT due_at FROM life.commitments WHERE actor_id=%s AND state IN ('scheduled','waiting') AND due_at IS NOT NULL",
            (context.actor_id,))).fetchall()
        for commitment in commitments:
            due = commitment.get("due_at")
            if due:
                due = datetime.fromisoformat(due) if isinstance(due, str) else due
                occupied.append((due - COMMITMENT_MARGIN, due + COMMITMENT_MARGIN))
        offered = ([decision["activity"]] if decision["activity"] else []) + decision.get("arrangements", [])
        results = [PlanCandidateResult(**item) for item in decision.get("_invalid_candidates", ())]
        if not offered and not results and not decision.get("_has_deferred_group"):
            results.append(PlanCandidateResult("idle", "intentional_idle", decision.get("free_time_reason") or decision["reason"], next_due_at=end))
        for index, activity in enumerate(offered):
            identifier = activity.get("existing_activity_id")
            candidate_key = activity.get("_candidate_key") or activity.get("option_id") or ("activity:" + identifier + ":" + str(activity.get("continuation_key") or "next") if identifier else f"proposal:{index}")
            if activity.get("_capability_stale"):
                results.append(PlanCandidateResult(candidate_key, "stale_state", "capability_snapshot_changed", identifier))
                continue
            goal_id = aliases.get(activity.get("goal_id"), activity.get("goal_id"))
            goal = None
            if goal_id:
                goal = await (await connection.execute("SELECT * FROM life.goals WHERE id=%s AND actor_id=%s FOR UPDATE", (goal_id, context.actor_id))).fetchone()
                if not goal or not set(goal["scope_ids"]) <= set(context.scope_ids) or goal["state"] != "active":
                    results.append(PlanCandidateResult(candidate_key, "stale_state", "goal_not_active", identifier, revision=goal["revision"] if goal else None))
                    continue
                frozen_goal = next((item for item in frozen_data.get("goals", ()) if item["id"] == goal_id), None)
                changed_goal_ids = {change.get("id") for change in decision.get("goal_changes", ()) if change.get("id")}
                if frozen_goal is None and goal_id not in aliases.values():
                    results.append(PlanCandidateResult(candidate_key, "stale_state", "goal_not_in_snapshot", identifier, revision=goal["revision"]))
                    continue
                if frozen_goal and goal_id not in changed_goal_ids and goal["revision"] != frozen_goal["revision"]:
                    results.append(PlanCandidateResult(candidate_key, "stale_state", "goal_revision_changed", identifier, revision=goal["revision"]))
                    continue
            row = None
            if identifier:
                original = original_activities.get(identifier)
                row = await self.life.lock_row(connection, context,
                    "SELECT * FROM life.activities WHERE id=%s AND actor_id=%s FOR UPDATE", (identifier, context.actor_id))
                if not original or not row:
                    results.append(PlanCandidateResult(candidate_key, "stale_state", "activity_missing", identifier))
                    continue
                if row.get("goal_id") != goal_id:
                    results.append(PlanCandidateResult(candidate_key, "stale_state", "activity_goal_changed", identifier, revision=row["revision"]))
                    continue
                assessment = evaluate(goal=goal, activity=row, expected_revision=original["revision"],
                    continuation_key=activity.get("continuation_key"), now=now, resume=True)
                if not assessment.eligible:
                    results.append(PlanCandidateResult(candidate_key, "stale_state", assessment.reason, identifier, revision=assessment.revision,
                        next_due_at=row.get("protected_until") if assessment.reason == "activity_protected" else None))
                    continue
                if assessment.step["capability_id"] != activity.get("capability_id"):
                    results.append(PlanCandidateResult(candidate_key, "invalid_proposal", "continuation_capability_mismatch", identifier, revision=row["revision"]))
                    continue
            background = background_work({"capability_id": activity.get("capability_id")})
            duration = timedelta(minutes=activity["duration_minutes"])
            begins = now + timedelta(minutes=activity.get("after_minutes", 0))
            period = (begins, begins + duration) if background and begins < midnight else (
                next_gap(begins, duration, end, occupied) if start is not None else None)
            if period is None:
                next_due = min((value for value in [end, midnight, *(until for _, until in occupied if until > now)] if value and value > now), default=end)
                results.append(PlanCandidateResult(candidate_key, "deferred_no_slot", "no_available_window", identifier, revision=row["revision"] if row else None, next_due_at=next_due))
                continue
            begins, finish = period
            if identifier:
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
            if not background:
                occupied.append((begins, finish))
            results.append(PlanCandidateResult(candidate_key, "scheduled", "accepted", identifier, slot_id, version, begins))
        accepted = any(item.status in {"scheduled", "intentional_idle"} for item in results)
        complete = bool(results) and not decision.get("_has_deferred_group") and all(item.status in {"scheduled", "intentional_idle"} for item in results)
        if editable_plan and accepted:
            await connection.execute("UPDATE life.day_plans SET reason=%s,context=%s,source_refs=%s,"
                "material_fingerprint=CASE WHEN %s THEN %s ELSE material_fingerprint END,"
                "revision=revision+%s,updated_at=now() WHERE actor_id=%s AND day=%s",
                (decision["reason"], document(context), document(unique_refs(source_refs(old["source_refs"]) if old else (), refs)),
                 complete, fingerprint, int(old is not None), context.actor_id, day))
        if start is not None and accepted:
            await self._free_time(connection, context, day, start, end, decision.get("free_time_reason") or "留白，允许休息和临时变化",refs)
        dates = [item.next_due_at or now for item in results if item.status == "stale_state"]
        dates.extend(item.next_due_at for item in results if item.status == "deferred_no_slot" and item.next_due_at)
        return PlanApplyResult(tuple(results), min(dates) if dates else None)

    async def _free_time(self, connection, context, day, start, end, reason, refs):
        actor_id=context.actor_id
        await connection.execute("DELETE FROM life.day_slots WHERE actor_id=%s AND day=%s AND kind='free' AND scope_ids<@%s::text[]", (actor_id, day,list(context.scope_ids)))
        occupied = await (await connection.execute(
            "SELECT s.starts_at,s.ends_at,a.checkpoint_data FROM life.day_slots s LEFT JOIN life.activities a ON a.id=s.activity_id "
            "WHERE s.actor_id=%s AND s.day=%s AND s.kind='activity' AND s.state IN ('planned','running') ORDER BY s.starts_at",
            (actor_id, day))).fetchall()
        occupied = [row for row in occupied if not background_work(row.get("checkpoint_data"))]
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
