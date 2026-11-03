"""One decision joins directions, today's appointments and the next actual step."""
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo
from uuid import uuid4

from tianshu.conversation.codec import context_from_record, document, signature, source_refs
from tianshu.conversation.generation import attempt_for
from tianshu.runtime.errors import AppError
from tianshu.runtime.jobs import JobSpec
from tianshu.runtime.serialization import wire
from tianshu.runtime.cancellation import finish_cleanup
from tianshu.sources.dependencies import unique_refs
from tianshu.models.retry import retry_delay_seconds, transient_model_failure
from .computation_retry import outcome
from . import planner
from .day_plan import awake_window
from .model_budget import generation_request, generation_profile


MAX_REPAIRS = 2
REPAIR_DELAY = timedelta(minutes=15)


def due(value, now):
    return value is not None and value <= now


def review_time(routine, now, boundary):
    # Choosing nothing is a real decision for a bounded part of the waking day.
    # A normal tick, changing seconds or passive affect decay does not expire it.
    seconds = min(10800, max(5400, (routine or {}).get("idle_seconds", 5400)))
    return min(now + timedelta(seconds=seconds), boundary)


def decision_review_at(mark):
    """A valid decision's saved idle deadline remains useful after its work ends.

    Older accepted plans and the removed daily quota did not set review_after.
    Reuse their existing deadline; errors and unresolved generations retain their
    own observation/repair rules instead of becoming periodic fresh attempts.
    """
    if mark.get("review_after") is not None:
        return mark["review_after"]
    pending = mark.get("generation_key") and not mark.get("generation_consumed")
    if mark.get("decision_fingerprint") and not mark.get("last_error") and not pending:
        return mark.get("next_due_at")
    return None


def scene(context):
    return {key: sorted(getattr(context, key)) if key in {"scope_ids", "audience"} else getattr(context, key)
        for key in ("principal_id", "actor_id", "person_id", "conversation_id", "conversation_kind", "scope_ids", "audience", "delivery_allowed")}


def lifecycle_material(data):
    """Only crossing a stored due/slot fence changes time-sensitive material."""
    now = datetime.fromisoformat(data["local_time"])
    def timestamp(value):
        return datetime.fromisoformat(value) if isinstance(value, str) else value
    slots = [{**{key: row.get(key) for key in ("id", "activity_id", "state", "starts_at", "ends_at")},
        "phase": "future" if timestamp(row["starts_at"]) > now else "elapsed" if timestamp(row["ends_at"]) <= now else "current"}
        for row in data.get("day_plan", ()) if row.get("activity_id")]
    unfinished = [{**{key: row.get(key) for key in ("id", "revision", "state", "waiting_reason", "checkpoint_step")},
        "due": bool(row.get("next_due_at") and timestamp(row["next_due_at"]) <= now),
        "protected": bool(row.get("protected_until") and timestamp(row["protected_until"]) > now),
        "checkpoint": {key: (row.get("checkpoint_data") or {}).get(key) for key in
            ("completed_action_id", "resolved_action_id", "resolved_execution", "continuation", "next_position", "continuation_stop_reason")}}
        for row in data.get("unfinished", ())]
    return wire({"slots": sorted(slots, key=lambda row: row["id"]), "unfinished": sorted(unfinished, key=lambda row: row["id"])})


def decision_fingerprint(context, actor, data, day, events=None):
    # Date is material; clock seconds and generic Growth/style revisions are not.
    return signature({"contract": planner.CONTRACT, "scene": scene(context), "day": day.isoformat() if hasattr(day,'isoformat') else day,
        "persona": (actor.get("persona_id"), actor.get("persona_revision")), "routine": data["routine"],
        "events": {key:value for key,value in (events or {}).items() if set(value.get("scope_ids",()))<=set(context.scope_ids)},
        "commitments": data["commitments"], "interests": data["interests"],
        "goals":data["goals"],"writing":data.get("writing",()),
        "model_configuration": data.get("model_configuration"),
        "lifecycle": lifecycle_material(data),
        "capabilities": data["capabilities"]})


def photo_followup(events, context, day, timezone):
    for item in events.values():
        if (item.get("kind")!="activity-result" or item.get("capability_id")!="images.generate"
                or not set(item.get("scope_ids",()))<=set(context.scope_ids)):
            continue
        observed=item.get("observed_at")
        if observed and datetime.fromisoformat(observed).astimezone(ZoneInfo(timezone)).date()==day:
            return True
    return False


class PlanningEngine:
    def __init__(self, worker):
        self.worker, self.life = worker, worker.life

    async def run(self, actor_id, revision, context):
        context = context_from_record(context)
        if context.actor_id != actor_id:
            raise AppError("actor_scope_mismatch", status=403)
        async with self.life.database.transaction(read_only=True) as connection:
            mark = await (await connection.execute("SELECT * FROM life.plan_watermarks WHERE actor_id=%s", (actor_id,))).fetchone()
            routine = await (await connection.execute("SELECT * FROM life.routines WHERE actor_id=%s", (actor_id,))).fetchone()
        now = datetime.now(UTC)
        if not mark or mark["revision"] != revision:
            return
        if (mark["planned_watermark"] == mark["change_watermark"]
                and (not mark["generation_key"] or mark["generation_consumed"])
                and not due(mark["repair_due_at"], now) and not due(decision_review_at(mark), now)):
            return
        # A private chat/Growth event is a change notification, not the author
        # identity of the actor's entire day. The saved configuration owns it.
        if routine:
            from .recovery import scheduled_context
            context = await scheduled_context(self.life, routine["context"])
        await self.life.identity.check(context)
        actor = await self.life.companion.actor(context)
        if not actor["enabled"]:
            return
        if routine and not routine["enabled"]:
            await self.finish(context, mark, "routine_disabled")
            return
        begins, next_boundary = awake_window(routine, now)
        if begins is None:
            await self.finish(context, mark, "actor_resting", next_due=next_boundary)
            return
        state = await self.life.state(context)
        day = now.astimezone(ZoneInfo(state["timezone"])).date()
        daily = await self.life.days.snapshot(context, day)
        goals = await self.life.goals(context, limit=40)
        commitments = await self.life.commitments(context, limit=40)
        from .writing_progress import material as writing_material
        writing=await writing_material(self.worker.writing,context)
        choices = await self.worker.catalog.discover(context)
        from .events import available
        await available(self.life,context,choices)
        if photo_followup(mark["change"] or {},context,day,state["timezone"]):
            choices = [item for item in choices if item["definition"].id != "images.generate"]
        growth = await self.worker.growth.view(context) if self.worker.growth else {"items": []}
        interests = [{key: row.get(key) for key in ("id", "title", "content", "state", "conditions")}
            for row in growth["items"] if row["kind"] == "interest" and row["state"] == "active"]
        refs = unique_refs(tuple(state["affect"]["source_refs"]),
            *(source_refs(item["source_refs"]) for item in (*goals.items, *commitments.items, *daily["unfinished"],*daily["recent_results"])),
            *(source_refs(row["source_refs"]) for row in growth["items"] if row["kind"] == "interest" and row["state"] == "active"),
            *(source_refs(row["source_refs"]) for row in writing))
        basis = [{"id": f"source:{ref.source_id}:{ref.revision}", "kind": "experience", "source_refs": wire((ref,))} for ref in refs]
        if actor.get("persona_id"):
            basis.append({"id": f"persona:{actor['persona_id']}:{actor['persona_revision']}", "kind": "authored_persona", "source_refs": []})
        basis.extend({"id":"result:"+row["checkpoint_data"]["completed_action_id"],"kind":"actual_result", "source_refs":row["source_refs"]}
            for row in daily["recent_results"])
        data = planner.material(context, state, routine, goals, commitments, choices,
            now.astimezone(ZoneInfo(state["timezone"])).isoformat(), day=daily, basis=basis, interests=interests,writing=writing)
        profile, profile_error = None, None
        try:
            profile = await generation_profile(self.worker.models, context, function="life.plan")
            data["model_configuration"] = profile
        except AppError as error:
            if error.code not in {"model_function_not_configured", "model_route_unavailable", "model_capability_unverified"}:
                raise
            profile_error = error
            data["model_configuration"] = {"error": error.code}
        material = decision_fingerprint(context, actor, data, day, mark["change"])
        pending = mark["generation_key"] and not mark["generation_consumed"]
        same_material = mark["generation_material"] == material
        repair_ready = False
        if not pending and same_material and mark["last_error"]:
            if transient_model_failure(mark["last_error"]):
                restored = await self.retry_original(context, mark, now)
                if restored is None:
                    return
                if restored:
                    mark, pending = restored, True
                else:
                    repair_ready = True
            elif not due(mark["repair_due_at"], now):
                await self.finish(context, mark, mark["last_error"], next_due=mark["repair_due_at"])
                return
            else:
                data["repair"] = {"reason": mark["last_error"], "number": mark["repair_count"],
                                  "instruction": "Return one complete compact JSON decision; no previous partial output is an activity."}
                repair_ready = True
        if not pending and not repair_ready and mark["decision_fingerprint"] == material and not due(decision_review_at(mark), now):
            await self.finish(context, mark, "unchanged_decision_material", fingerprint=material)
            return
        scheduled = [row for row in daily["slots"] if row["kind"] == "activity" and row["state"] in {"planned", "running"}
                     and row["starts_at"] <= now < row["ends_at"]]
        running = any(row["state"] == "running" for row in daily["unfinished"])
        important = not mark["change_watermark"].startswith(("rhythm:", "activity-", "growth:"))
        if not pending and (scheduled or running) and not important:
            # Interest/style notifications wait for the current activity too.
            await self.finish(context, mark, "existing_activity_continues", fingerprint=material)
            return
        frozen = None
        observe = False
        if pending:
            # Same material may arrive with a newer legal epoch. A genuinely new
            # scene/material can supersede a proposal, without rewriting its outcome.
            if mark["generation_material"] != material:
                status = await self.supersede(mark, "material_changed")
                if status in {"queued", "running"}:
                    await self.waiting(context, mark, "planner_" + status, now)
                    return
            else:
                try:
                    frozen = await self.worker.generations.observe_request(attempt_for(mark["generation_key"]), observer=context)
                except AppError as error:
                    if error.code == "generation_observation_context_mismatch":
                        await self.supersede(mark, "scene_changed")
                    elif error.code == "source_unavailable":
                        await self.failure(context, mark, "source_unavailable", material, now)
                        return
                    else:
                        raise
                if frozen is not None:
                    observe = (self.worker.generations.context_key(frozen.context) != self.worker.generations.context_key(context)
                        or await self.worker.models.status(frozen.attempt_id) is not None)
        if frozen is None:
            if pending and mark["generation_material"] == material:
                # New requests are atomically frozen with the Life pointer, so a
                # missing original is an explicit integrity failure, never a replay.
                await self.failure(context, mark, "planner_original_request_missing", material, now)
                return
            key = "life-plan:" + signature({"actor": actor_id, "material": material, "previous": mark["generation_key"]})
            persona = await self.life.companion.persona(context, actor["persona_id"], actor["persona_revision"]) if actor.get("persona_id") else None
            try:
                if profile_error:
                    raise profile_error
                selected = await generation_request(self.worker.models, context, key=key, attempt_id=attempt_for(key),
                    function="life.plan", profile=profile, business_deadline_at=next_boundary)
                frozen = await planner.request(self.worker.assembler, context, selected.budget,
                    key=key, persona=persona.prompt_text if persona else "", data=data, refs=refs,
                    growth=await self.worker.growth.material(context) if self.worker.growth else (), model_request=selected)
            except AppError as error:
                if error.code not in {"planner_context_budget_exceeded", "context_mandatory_budget_exceeded",
                    "model_function_not_configured", "model_route_unavailable", "model_capability_unverified"}:
                    raise
                reason = "planner_context_budget_exceeded" if "budget" in error.code else "planner_model_unavailable"
                await self.failure(context, mark, reason, material, now)
                return
            async with self.life.database.transaction() as connection:
                await self.life.validate_persistence(connection, context, frozen.source_refs)
                fresh = await (await connection.execute("SELECT * FROM life.plan_watermarks WHERE actor_id=%s FOR UPDATE", (actor_id,))).fetchone()
                if (fresh["revision"] != revision or fresh["generation_key"] != mark["generation_key"]
                        or fresh["generation_consumed"] != mark["generation_consumed"]):
                    return
                frozen = await self.worker.generations.prepare(key, frozen, connection=connection)
                if fresh["generation_key"] and not same_material:
                    await connection.execute("UPDATE life.plan_generations SET state='superseded',reason='material_changed',reviewed_at=now() "
                        "WHERE generation_key=%s AND state IN ('pending','reviewed','retry_wait')",(fresh["generation_key"],))
                await connection.execute(
                    "INSERT INTO life.plan_generations(generation_key,actor_id,day,material_fingerprint,base_revision,context,previous_generation_key) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING", (key, actor_id, day, material, revision, document(context),mark["generation_key"]))
                repairs = fresh["repair_count"] if fresh["generation_material"] == material else 0
                await connection.execute("UPDATE life.plan_watermarks SET generation_material=%s,generation_key=%s,generation_consumed=false,"
                    "repair_count=%s,computation_retry_count=%s,repair_due_at=NULL,review_after=NULL,reason='planner_running' WHERE actor_id=%s",
                    (material, key, repairs,fresh["computation_retry_count"] if same_material else 0, actor_id))
                mark = {**fresh, "generation_key": key, "generation_material": material, "generation_consumed": False, "repair_count": repairs,
                        "computation_retry_count":fresh["computation_retry_count"] if same_material else 0}
        key = mark["generation_key"]

        async def current(connection):
            fresh = await (await connection.execute("SELECT * FROM life.plan_watermarks WHERE actor_id=%s FOR UPDATE", (actor_id,))).fetchone()
            return fresh and fresh["generation_key"] == key and not fresh["generation_consumed"]

        try:
            result = await self.worker.generations.run(key, frozen, guard=current, observer=context if observe else None, stream=True)
        except AppError as error:
            if error.code == "generation_request_expired":
                await self.failure(context, mark, "original_planner_not_registered_before_deadline", material, now)
                return
            if error.code == "generation_no_longer_current":
                return
            await self.computation_failure(context,mark,material,now,error=error)
            return
        except BaseException as error:
            await finish_cleanup(self.computation_failure(context,mark,material,now,error=error))
            raise
        status = result.receipt.status if result else "absent"
        if status != "succeeded":
            await self.computation_failure(context,mark,material,now,result=result)
            return
        finished_at = datetime.now(UTC)
        awake, boundary = awake_window(routine, finished_at)
        if awake is None or finished_at.astimezone(ZoneInfo(state["timezone"])).date() != day:
            await self.finish(context, mark, "waiting_for_next_life_window", consumed=True, next_due=boundary)
            async with self.life.database.transaction() as connection:
                await connection.execute("UPDATE life.plan_generations SET state='superseded',reason='life_window_expired',reviewed_at=now() "
                    "WHERE generation_key=%s AND state='reviewed'", (key,))
            return
        now, next_boundary = finished_at, boundary
        latest=await self.worker.models.status(frozen.attempt_id)
        if latest and getattr(latest,"cancellation_reason",None)=="explicit_cancel":
            await self.failure(context,mark,"explicit_cancel",material,now,status_reason="planner_cancelled")
            return
        try:
            decision, original_data = planner.decision(result, frozen)
            # Catalog changes and source changes are rechecked for actual dispatch.
            available = {item["id"]: item for item in planner.capability_material(await self.worker.catalog.discover(context))}
            for activity in ([decision["activity"]] if decision["activity"] else []) + decision["arrangements"]:
                if activity["capability_id"] and activity["capability_id"] not in available:
                    raise AppError("planner_capability_unavailable")
                if activity["capability_id"] and not activity.get("existing_activity_id") and not planner.allowed_arguments(
                        available[activity["capability_id"]], activity["arguments"]):
                    raise AppError("planner_arguments_invalid")
        except AppError as error:
            await self.failure(context, mark, error.code, material, now, repairable=bool((error.details or {}).get("repairable")))
            return
        from .contact_planning import prepare as prepare_contact
        prepared_contact=await prepare_contact(self.worker,context,decision.get("contact"),original_data,key,now)
        async with self.life.database.transaction() as connection:
            await self.life.validate_persistence(connection, context, frozen.source_refs)
            fresh = await (await connection.execute("SELECT * FROM life.plan_watermarks WHERE actor_id=%s FOR UPDATE", (actor_id,))).fetchone()
            if fresh["revision"] != revision or fresh["generation_key"] != key or fresh["generation_consumed"]:
                return
            aliases = await self.life.directions.apply(connection, context, decision["goal_changes"], original_data, key, now)
            created = await self.life.days.apply(connection, context, decision, original_data, key, aliases,
                frozen.source_refs, routine, now, material)
            if prepared_contact:
                await self.worker.persist_contact(connection,prepared_contact)
            # Include our own accepted direction edits in the reviewed snapshot;
            # a later style notification must not mistake them for new input.
            current_goals=await (await connection.execute("SELECT * FROM life.goals WHERE actor_id=%s AND scope_ids<@%s::text[] ORDER BY id LIMIT 40",(actor_id,list(context.scope_ids)))).fetchall()
            post_data={**data,"goals":[{"id":row["id"],"revision":row["revision"],"title":row["title"][:200],"motivation":row["motivation"][:300],"priority":row["priority"],"state":row["state"],"progress":row["progress"],"criteria":row["criteria"]} for row in current_goals if row["state"] in {'active','paused'}]}
            post_data.update(await self.life.days.decision_state(connection, context, day))
            post_data["local_time"] = now.isoformat()
            reviewed=decision_fingerprint(context,actor,post_data,day,fresh["change"])
            reason = "planned_with_reduced_context" if planner.reduced(original_data) else "planned" if created or prepared_contact else "no_meaningful_activity"
            reconsider_at = review_time(routine, now, next_boundary) if not created and not scheduled else None
            await self._finish(connection, context, fresh, reason, fingerprint=reviewed, consumed=True, next_due=reconsider_at)
            await connection.execute("UPDATE life.plan_watermarks SET last_error=NULL,failed_generation_key=NULL,repair_count=0,computation_retry_count=0,repair_due_at=NULL,review_after=%s "
                "WHERE actor_id=%s", (reconsider_at, actor_id))
            if reconsider_at:
                await self.schedule(connection, context, fresh, reconsider_at, "review")
            await connection.execute("UPDATE life.plan_generations SET state='applied',reason=%s,reviewed_at=now() WHERE generation_key=%s", (reason, key))

    async def computation_failure(self, context, mark, material, now, *, result=None, error=None):
        try:
            status, code, retry = await outcome(self.worker.models,attempt_for(mark["generation_key"]),result=result,error=error)
        except Exception:
            await self.waiting(context,mark,"planner_unknown",now)
            return
        if status in {"queued","running","succeeded"} and code != "explicit_cancel":
            await self.waiting(context,mark,"planner_"+status if status != "succeeded" else "planner_unknown",now)
            return
        if not retry:
            await self.failure(context,mark,code,material,now,status_reason="planner_cancelled" if code=="explicit_cancel" or status=="cancelled" else "planner_failed")
            return
        async with self.life.database.transaction() as connection:
            fresh=await (await connection.execute("SELECT * FROM life.plan_watermarks WHERE actor_id=%s FOR UPDATE",(context.actor_id,))).fetchone()
            if (not fresh or fresh["revision"]!=mark["revision"] or fresh["generation_key"]!=mark["generation_key"]
                    or fresh["generation_consumed"]):
                return
            count=(fresh["computation_retry_count"] if fresh["generation_material"]==material else 0)+1
            retry_at=now+timedelta(seconds=retry_delay_seconds(count))
            await self._finish(connection,context,fresh,"planner_retry_wait",consumed=True,next_due=retry_at)
            await connection.execute("UPDATE life.plan_watermarks SET last_error=%s,failed_generation_key=generation_key,"
                "computation_retry_count=%s,repair_due_at=%s,review_after=NULL WHERE actor_id=%s",(code,count,retry_at,context.actor_id))
            await connection.execute("UPDATE life.plan_generations SET state='retry_wait',reason=%s,reviewed_at=now() WHERE generation_key=%s",
                (code,mark["generation_key"]))
            await self.schedule(connection,context,fresh,retry_at,"retry")

    async def retry_original(self,context,mark,now):
        """Observe a yielded attempt before choosing a fresh independent intent."""
        try:
            frozen=await self.worker.generations.observe_request(attempt_for(mark["generation_key"]),observer=context)
            if frozen is None:
                raise AppError("planner_original_request_missing")
            result=await self.worker.generations.read(frozen.attempt_id,frozen.context,observer=context)
            status,code,retry=await outcome(self.worker.models,frozen.attempt_id,result=result,error=mark["last_error"])
        except Exception as error:
            if isinstance(error,AppError) and not transient_model_failure(error.code):
                code,retry=getattr(error,"code","planner_original_request_missing"),False
                status="failed"
            else:
                async with self.life.database.transaction() as connection:
                    await self.schedule(connection,context,mark,now+timedelta(seconds=30),"observe-retry")
                return None
        else:
            if code != "explicit_cancel" and status in {"queued","running"}:
                async with self.life.database.transaction() as connection:
                    await self.schedule(connection,context,mark,now+timedelta(seconds=30),"observe-retry")
                return None
            from tianshu.models.timing import waiting_budget
            deadline=waiting_budget(frozen)
            if code != "explicit_cancel" and deadline is not None and deadline.deadline_at<=now:
                async with self.life.database.transaction() as connection:
                    await connection.execute("UPDATE life.plan_generations SET state='superseded',reason='life_window_expired',reviewed_at=now() "
                        "WHERE generation_key=%s",(mark["generation_key"],))
                return {}
            if code != "explicit_cancel" and result is not None and result.receipt.status=="succeeded":
                async with self.life.database.transaction() as connection:
                    fresh=await (await connection.execute("UPDATE life.plan_watermarks SET generation_consumed=false,last_error=NULL,repair_due_at=NULL "
                        "WHERE actor_id=%s AND revision=%s AND generation_key=%s AND generation_consumed RETURNING *",
                        (context.actor_id,mark["revision"],mark["generation_key"]))).fetchone()
                    if fresh:
                        await connection.execute("UPDATE life.plan_generations SET state='pending' WHERE generation_key=%s",(mark["generation_key"],))
                return fresh
            if code != "explicit_cancel" and status=="succeeded":
                async with self.life.database.transaction() as connection:
                    await self.schedule(connection,context,mark,now+timedelta(seconds=30),"observe-retry")
                return None
            if retry:
                if not due(mark["repair_due_at"],now):
                    await self.finish(context,mark,"planner_retry_wait",next_due=mark["repair_due_at"])
                    return None
                return {}
        async with self.life.database.transaction() as connection:
            await connection.execute("UPDATE life.plan_watermarks SET last_error=%s,repair_due_at=NULL,reason=%s "
                "WHERE actor_id=%s AND revision=%s AND generation_key=%s AND generation_consumed",
                (code,"planner_cancelled" if code=="explicit_cancel" else "planner_failed",context.actor_id,mark["revision"],mark["generation_key"]))
            await connection.execute("UPDATE life.plan_generations SET state='rejected',reason=%s,reviewed_at=now() WHERE generation_key=%s",
                (code,mark["generation_key"]))
        return None

    async def supersede(self, mark, reason):
        state = await self.worker.models.status(attempt_for(mark["generation_key"]))
        status = state.status if state else "absent"
        if status in {"queued", "running"}:
            return status
        async with self.life.database.transaction() as connection:
            await connection.execute("UPDATE life.plan_generations SET state='superseded',reason=%s,reviewed_at=now() WHERE generation_key=%s AND state='pending'",
                (reason + ":" + status, mark["generation_key"]))
        return status

    async def schedule(self, connection, context, mark, when, kind):
        current=await (await connection.execute("SELECT revision,generation_key FROM life.plan_watermarks WHERE actor_id=%s FOR UPDATE",
            (context.actor_id,))).fetchone()
        if not current or current["revision"]!=mark["revision"] or current["generation_key"]!=mark["generation_key"]:
            return
        value=wire(context)
        for key in ("scope_ids","audience","permissions"):
            value[key]=sorted(value[key])
        payload={"actor_id":context.actor_id,"revision":mark["revision"],"context":value}
        await self.life.jobs.enqueue(connection, JobSpec("life.plan",
            f"plan-{kind}:{mark['generation_key']}:{mark['revision']}:{uuid4()}",payload,
            scheduled_at=when, subject_id=context.actor_id),queueing_lock="life-plan:"+signature(payload))

    async def waiting(self, context, mark, reason, now):
        async with self.life.database.transaction() as connection:
            fresh = await (await connection.execute(
                "UPDATE life.plan_watermarks SET reason=%s WHERE actor_id=%s AND revision=%s "
                "AND generation_key=%s AND NOT generation_consumed RETURNING actor_id",
                (reason, context.actor_id, mark["revision"], mark["generation_key"]))).fetchone()
            if fresh and reason in {"planner_queued", "planner_running", "planner_unknown", "planner_absent"}:
                await self.schedule(connection, context, mark, now + timedelta(seconds=30), "observe")

    async def failure(self, context, mark, reason, material, now, *, repairable=False, status_reason=None):
        """Reject a result without accepting its material as a valid decision."""
        count = (mark["repair_count"] if mark["generation_material"] == material else 0) + int(repairable)
        retry_at = now + REPAIR_DELAY * 2**max(0, count - 1) if repairable and count <= MAX_REPAIRS else None
        async with self.life.database.transaction() as connection:
            fresh = await (await connection.execute(
                "SELECT * FROM life.plan_watermarks WHERE actor_id=%s FOR UPDATE", (context.actor_id,))).fetchone()
            if (not fresh or fresh["revision"] != mark["revision"]
                    or fresh["generation_key"] != mark["generation_key"] or fresh["generation_consumed"]):
                return
            await self._finish(connection, context, fresh, status_reason or reason, consumed=True, next_due=retry_at)
            await connection.execute(
                "UPDATE life.plan_watermarks SET last_error=%s,failed_generation_key=generation_key,"
                "generation_material=%s,repair_count=%s,repair_due_at=%s,review_after=NULL WHERE actor_id=%s",
                (reason, material, count, retry_at, context.actor_id))
            if mark["generation_key"]:
                await connection.execute("UPDATE life.plan_generations SET state='rejected',reason=%s,reviewed_at=now() "
                    "WHERE generation_key=%s AND state IN ('pending','reviewed')", (reason, mark["generation_key"]))
            if retry_at:
                await self.schedule(connection, context, mark, retry_at, "repair")

    async def _finish(self, connection, context, mark, reason, *, fingerprint=None, consumed=False, next_due=None):
        await connection.execute(
            "UPDATE life.plan_watermarks SET planned_watermark=change_watermark,generation_consumed=CASE WHEN %s THEN true ELSE generation_consumed END,"
            "decision_fingerprint=COALESCE(%s,decision_fingerprint),last_planned_at=now(),next_due_at=COALESCE(%s,now()+"
            "make_interval(secs=>COALESCE((SELECT idle_seconds FROM life.routines WHERE actor_id=%s),5400))),reason=%s "
            "WHERE actor_id=%s AND revision=%s AND generation_key IS NOT DISTINCT FROM %s",
            (consumed, fingerprint, next_due, context.actor_id, reason, context.actor_id, mark["revision"], mark["generation_key"]))
        if consumed and mark["generation_key"]:
            await connection.execute("UPDATE life.plan_generations SET state='reviewed',reason=%s,reviewed_at=now() WHERE generation_key=%s AND state='pending'",
                (reason, mark["generation_key"]))

    async def finish(self, context, mark, reason, **kwargs):
        async with self.life.database.transaction() as connection:
            dates = [value for value in (mark["repair_due_at"], decision_review_at(mark))
                     if value is not None and value > datetime.now(UTC)]
            if dates and "next_due" not in kwargs:
                # Harmless ticks must not slide the saved idle review forever.
                kwargs["next_due"] = min(dates)
            await self._finish(connection, context, mark, reason, **kwargs)
            # A newer harmless notification has a new CAS revision. Rebind the
            # existing future window to that revision instead of losing its job.
            if dates and reason not in {"actor_resting", "routine_disabled"}:
                await self.schedule(connection, context, mark, min(dates), "due")
