"""One decision joins directions, today's appointments and the next actual step."""
from datetime import UTC, datetime, timedelta
from dataclasses import replace
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
from . import plan_recovery


MAX_REPAIRS = 2
REPAIR_DELAY = timedelta(seconds=30)


def due(value, now):
    return value is not None and value <= now


def review_time(routine, now, boundary):
    # Choosing nothing is a real decision for a bounded part of the waking day.
    # A normal tick, changing seconds or passive affect decay does not expire it.
    seconds = max(1, (routine or {}).get("idle_seconds", 5400))
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
    capabilities = [{**row, "availability": {key: value for key, value in row.get("availability", {}).items()
        if key != "verified_at"}} for row in data["capabilities"]]
    return signature({"contract": planner.CONTRACT, "scene": scene(context), "day": day.isoformat() if hasattr(day,'isoformat') else day,
        "persona": (actor.get("persona_id"), actor.get("persona_revision")), "routine": data["routine"],
        "events": {key:value for key,value in (events or {}).items() if set(value.get("scope_ids",()))<=set(context.scope_ids)},
        "commitments": data["commitments"], "interests": data["interests"],
        "goals":data["goals"],"writing":data.get("writing",()),
        "model_configuration": data.get("model_configuration"),
        "lifecycle": lifecycle_material(data),
        "capabilities": capabilities})


def recovery_fingerprint(context, actor, data, day, events=None):
    # Reconfiguring a route changes planning material, but not the source
    # material whose physical attempts share one recovery budget.
    return decision_fingerprint(context, actor, {**data, "model_configuration": None}, day, events)


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
        planning_while_resting = begins is None
        state = await self.life.state(context)
        day = now.astimezone(ZoneInfo(state["timezone"])).date()
        daily = await self.life.days.snapshot(context, day)
        goals = await self.life.goals(context, limit=40)
        commitments = await self.life.commitments(context, limit=40)
        from .writing_progress import material as writing_material
        writing=await writing_material(self.worker.writing,context)
        choices = await self.worker.catalog.planning(context)
        from .events import available
        await available(self.life,context,choices)
        if photo_followup(mark["change"] or {},context,day,state["timezone"]):
            choices = [item for item in choices if item["definition"].id != "images.generate"]
        growth = await self.worker.growth.material(context, purpose="planning") if self.worker.growth else ()
        interests = [{"id": getattr(row, "material_id", None) or row.kind, "content": row.text}
            for row in growth if getattr(row, "semantic_role", None) == "interest"]
        refs = unique_refs(tuple(state["affect"]["source_refs"]),
            *(source_refs(item["source_refs"]) for item in (*goals.items, *commitments.items, *daily["unfinished"],*daily["recent_results"])),
            *(row.source_refs for row in growth),
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
        recovery_material = recovery_fingerprint(context, actor, data, day, mark["change"])
        pending = mark["generation_key"] and not mark["generation_consumed"]
        same_material = mark["generation_material"] == material
        async with self.life.database.transaction(read_only=True) as connection:
            previous = await plan_recovery.generation_on(connection, mark["generation_key"])
        same_recovery_material = bool(previous and previous.get("recovery_lineage_id")
            and previous.get("recovery_version") == recovery_material)
        repair_ready = False
        if not pending and (same_material or same_recovery_material) and mark["last_error"]:
            if transient_model_failure(mark["last_error"]):
                restored = await self.retry_original(context, mark, now)
                if restored is None:
                    return
                if restored:
                    mark, pending = restored, True
                else:
                    repair_ready = True
            elif not due(mark["repair_due_at"], now) and not due(decision_review_at(mark), now):
                await self.finish(context, mark, mark["last_error"], next_due=mark["repair_due_at"])
                return
            else:
                data["repair"] = {"reason": mark["last_error"], "number": mark["repair_count"],
                                  "instruction": "Return one complete compact JSON decision for unresolved work. "
                                  "Keep the accepted activities and directions shown in the current snapshot; do not repeat them."}
                repair_ready = True
        if not pending and not repair_ready and mark["decision_fingerprint"] == material and not due(decision_review_at(mark), now):
            await self.finish(context, mark, "unchanged_decision_material", fingerprint=material)
            return
        frozen = None
        observe = False
        if pending:
            # Same material may arrive with a newer legal epoch. A genuinely new
            # scene/material can supersede a proposal, without rewriting its outcome.
            if mark["generation_material"] != material and not same_recovery_material:
                await self.supersede(mark, "material_changed", recovery_material)
            else:
                try:
                    frozen = await self.worker.generations.observe_request(attempt_for(mark["generation_key"]), observer=context)
                except AppError as error:
                    if error.code == "generation_observation_context_mismatch":
                        await self.supersede(mark, "scene_changed", recovery_material)
                    elif error.code == "source_unavailable":
                        await self.failure(context, mark, "source_unavailable", material, now)
                        return
                    else:
                        raise
                if frozen is not None:
                    observe = (self.worker.generations.context_key(frozen.context) != self.worker.generations.context_key(context)
                        or await self.worker.models.status(frozen.attempt_id) is not None)
        if frozen is None:
            if pending and (mark["generation_material"] == material or same_recovery_material):
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
                    growth=growth, model_request=selected)
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
                previous = await plan_recovery.generation_on(connection, fresh["generation_key"], lock=True)
                recovery = await plan_recovery.reserve_on(self.life, connection, context=context, scene=scene(context),
                    material=recovery_material, key=key, frozen=frozen, profile=profile, previous=previous,
                    continuation=bool(fresh["last_error"] and same_recovery_material))
                if recovery["state"] == "waiting":
                    await self.schedule(connection, context, fresh, now + timedelta(seconds=30), "recovery-claim")
                    return
                if recovery["state"] == "exhausted":
                    await connection.execute("UPDATE life.plan_watermarks SET reason='planner_attempts_exhausted',"
                        "repair_due_at=NULL,review_after=NULL WHERE actor_id=%s", (actor_id,))
                    return
                frozen = await self.worker.generations.prepare(key, frozen, connection=connection)
                if fresh["generation_key"] and not same_material:
                    await connection.execute("UPDATE life.plan_generations SET state='superseded',reason='material_changed',reviewed_at=now() "
                        "WHERE generation_key=%s AND state IN ('pending','reviewed','retry_wait')",(fresh["generation_key"],))
                await connection.execute(
                    "INSERT INTO life.plan_generations(generation_key,actor_id,day,material_fingerprint,base_revision,context,previous_generation_key,"
                    "recovery_lineage_id,recovery_owner,recovery_version,recovery_probe_token,recovery_attempt_number,recovery_state,recovery_dependency,recovery_current_dependency) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'reserved',%s,%s) ON CONFLICT DO NOTHING",
                    (key, actor_id, day, material, revision, document(context), mark["generation_key"], recovery["lineage_id"],
                     recovery["owner"], recovery_material, recovery["probe_token"], recovery["number"], recovery["dependency"], recovery["dependency"]))
                repairs = fresh["repair_count"] if fresh["generation_material"] == material else 0
                await connection.execute("UPDATE life.plan_watermarks SET generation_material=%s,generation_key=%s,generation_consumed=false,"
                    "repair_count=%s,computation_retry_count=%s,repair_due_at=NULL,review_after=NULL,reason='planner_running' WHERE actor_id=%s",
                    (material, key, repairs,fresh["computation_retry_count"] if same_material else 0, actor_id))
                mark = {**fresh, "generation_key": key, "generation_material": material, "generation_consumed": False, "repair_count": repairs,
                        "computation_retry_count":fresh["computation_retry_count"] if same_material else 0}
        key = mark["generation_key"]

        async def current(connection):
            fresh = await (await connection.execute("SELECT * FROM life.plan_watermarks WHERE actor_id=%s FOR UPDATE", (actor_id,))).fetchone()
            return fresh and fresh["revision"] == revision and fresh["generation_key"] == key and not fresh["generation_consumed"]

        try:
            status = await self.worker.models.status(frozen.attempt_id)
            if status is None:
                from tianshu.models.timing import waiting_budget
                deadline = waiting_budget(frozen)
                if observe:
                    raise AppError("generation_observation_context_mismatch", status=403)
                if deadline is not None and deadline.deadline_at <= datetime.now(UTC):
                    raise AppError("generation_request_expired", status=409)
                async with self.life.database.transaction() as connection:
                    await self.life.validate_persistence(connection, context, frozen.source_refs)
                    if not await current(connection):
                        return
                    await connection.execute("UPDATE conversation.generation_requests SET state='claimed',claimed_at=COALESCE(claimed_at,now()) "
                        "WHERE material_key=%s AND state IN ('prepared','claimed')", (key,))
                completion = JobSpec("life.plan", f"life-plan-result:{key}:{revision}",
                    {"actor_id": actor_id, "revision": revision, "context": wire(context)}, subject_id=actor_id)
                status = await self.worker.models.submit(frozen, completion=completion)
            if status.status in {"queued", "running"}:
                await self.waiting(context, mark, "planner_" + status.status, now)
                return
            result = await self.worker.generations.read(frozen.attempt_id, frozen.context, observer=context if observe else None)
            if status.status == "unknown":
                await self.mark_unknown(context, mark, status.error_code or "model_outcome_unknown")
            if status.status == "unknown" and result is None:
                await self.waiting(context, mark, "planner_unknown", now)
                return
        except AppError as error:
            if error.code == "generation_request_expired":
                await self.failure(context, mark, "original_planner_not_registered_before_deadline", material, now)
                return
            if error.code == "generation_no_longer_current":
                return
            if error.code == "generation_observation_context_mismatch":
                await self.failure(context, mark, "planner_original_context_changed", material, now)
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
        async with self.life.database.transaction() as connection:
            if not await current(connection):
                return
            await plan_recovery.succeed_on(self.life, connection, key)
        finished_at = datetime.now(UTC)
        _, boundary = awake_window(routine, finished_at)
        if finished_at >= next_boundary or finished_at.astimezone(ZoneInfo(state["timezone"])).date() != day:
            await self.finish(context, mark, "waiting_for_next_life_window", consumed=True, next_due=boundary)
            async with self.life.database.transaction() as connection:
                await connection.execute("UPDATE life.plan_generations SET state='superseded',reason='life_window_expired',reviewed_at=now() "
                    "WHERE generation_key=%s AND state='reviewed'", (key,))
            return
        now, next_boundary = finished_at, boundary
        latest=await self.worker.models.status(frozen.attempt_id)
        if latest and getattr(latest,"cancellation_reason",None)=="explicit_cancel":
            await self.failure(context,mark,"explicit_cancel",material,now,status_reason="planner_cancelled",physical_failure=True)
            return
        try:
            decision, original_data = planner.decision(result, frozen)
            # Catalog changes and source changes are rechecked for actual dispatch.
            available = {item["id"]: item for item in planner.capability_material(await self.worker.catalog.planning(context))}
            for activity in ([decision["activity"]] if decision["activity"] else []) + decision["arrangements"]:
                # Actual selected capability is probed again immediately before dispatch.
                # A changed snapshot is classified per candidate by the apply path.
                capability = activity.get("capability_id")
                if capability and (capability not in available or (
                        not activity.get("existing_activity_id") and not planner.allowed_arguments(available[capability], activity["arguments"]))):
                    activity["_capability_stale"] = True
        except AppError as error:
            semantic_error = error.code in {"planner_output_invalid", "planner_goal_basis_invalid", "planner_goal_unavailable",
                "planner_goal_criteria_changed", "planner_activity_unavailable", "planner_contact_basis_invalid",
                "planner_capability_unavailable", "planner_arguments_invalid"}
            await self.failure(context, mark, error.code, material, now,
                repairable=semantic_error or bool((error.details or {}).get("repairable")))
            return
        from .contact_planning import prepare as prepare_contact
        prepared_contact=await prepare_contact(self.worker,context,decision.get("contact"),original_data,now)
        async with self.life.database.transaction() as connection:
            await self.life.validate_persistence(connection, context, frozen.source_refs)
            fresh = await (await connection.execute("SELECT * FROM life.plan_watermarks WHERE actor_id=%s FOR UPDATE", (actor_id,))).fetchone()
            if fresh["revision"] != revision or fresh["generation_key"] != key or fresh["generation_consumed"]:
                return
            from .application import apply as apply_proposal
            application = await apply_proposal(self.life, connection, context, decision, original_data, key,
                frozen.source_refs, routine, now, material)
            if prepared_contact:
                await self.worker.persist_contact(connection,prepared_contact)
            # Include our own accepted direction edits in the reviewed snapshot;
            # a later style notification must not mistake them for new input.
            current_goals=await (await connection.execute("SELECT * FROM life.goals WHERE actor_id=%s AND scope_ids<@%s::text[] ORDER BY id LIMIT 40",(actor_id,list(context.scope_ids)))).fetchall()
            post_data={**data,"goals":[{"id":row["id"],"revision":row["revision"],"title":row["title"][:200],"motivation":row["motivation"][:300],"priority":row["priority"],"state":row["state"],"progress":row["progress"],"criteria":row["criteria"]} for row in current_goals if row["state"] in {'active','paused'}]}
            post_data.update(await self.life.days.decision_state(connection, context, day))
            current_capabilities = {item["id"] for item in post_data["capabilities"]}
            post_data["unfinished"] = [planner.unfinished_candidate(row, current_goals, current_capabilities, now=now)
                for row in post_data["unfinished"] if not row.get("goal_id")
                or any(goal["id"] == row["goal_id"] and goal["state"] == "active" for goal in current_goals)]
            post_data["local_time"] = now.astimezone(ZoneInfo(state["timezone"])).isoformat()
            reviewed=decision_fingerprint(context,actor,post_data,day,fresh["change"])
            recovery_reviewed=recovery_fingerprint(context,actor,post_data,day,fresh["change"])
            statuses = {item.status for item in application.items}
            retry_count = 0
            repair_at = None
            if "invalid_proposal" in statuses:
                reason = "planner_output_invalid"
                retry_count = fresh["repair_count"] + 1
                if retry_count <= MAX_REPAIRS:
                    repair_at = now + REPAIR_DELAY * 2**(retry_count - 1)
                dates = [value for value in (repair_at, application.next_due_at) if value is not None]
                reconsider_at = min(dates) if dates else None
            elif "stale_state" in statuses:
                reason, reconsider_at = "planner_stale_state", application.next_due_at or now
            elif "deferred_no_slot" in statuses:
                reason, reconsider_at = "planner_deferred_no_slot", application.next_due_at or next_boundary
            elif "scheduled" in statuses:
                reason = "planned_with_reduced_context" if planner.reduced(original_data) else "planned"
                reconsider_at = None
            elif "intentional_idle" in statuses:
                reason = "planned" if prepared_contact else "no_meaningful_activity"
                reconsider_at = next_boundary if planning_while_resting else review_time(routine, now, next_boundary)
                application = replace(application, next_due_at=reconsider_at,
                    items=tuple(replace(item, next_due_at=reconsider_at) if item.status == "intentional_idle" else item
                        for item in application.items))
            else:
                reason = "planned" if prepared_contact else "planner_output_invalid"
                reconsider_at = None
            accepted = bool(application.items) and statuses <= {"scheduled", "intentional_idle"}
            if "invalid_proposal" in statuses:
                await plan_recovery.advance_version_on(self.life, connection, key, recovery_reviewed)
            await self._finish(connection, context, fresh, reason, fingerprint=reviewed if accepted else None,
                consumed=True, next_due=reconsider_at)
            await connection.execute("UPDATE life.plan_watermarks SET last_error=%s,failed_generation_key=%s,"
                "generation_material=%s,repair_count=%s,computation_retry_count=0,repair_due_at=%s,review_after=%s "
                "WHERE actor_id=%s", ("planner_output_invalid" if "invalid_proposal" in statuses else None,
                    key if "invalid_proposal" in statuses else None, reviewed, retry_count, repair_at, reconsider_at, actor_id))
            if reconsider_at:
                await self.schedule(connection, context, fresh, reconsider_at, "repair" if repair_at else "review")
            await connection.execute("UPDATE life.plan_generations SET state='applied',reason=%s,application_results=%s,reviewed_at=%s WHERE generation_key=%s",
                (reason, document(wire(application)), now, key))
            from .statistics import record_on
            await record_on(self.life, connection, key, actor_id, now, application, enqueue_replay=True)

    async def computation_failure(self, context, mark, material, now, *, result=None, error=None):
        try:
            status, code, retry = await outcome(self.worker.models,attempt_for(mark["generation_key"]),result=result,error=error)
        except Exception:
            await self.waiting(context,mark,"planner_unknown",now)
            return
        if status in {"queued","running","succeeded","unknown"} and code != "explicit_cancel":
            if status == "unknown":
                await self.mark_unknown(context, mark, code)
            await self.waiting(context,mark,"planner_"+status if status != "succeeded" else "planner_unknown",now)
            return
        if status == "absent" and retry:
            # Admission can be retried with the same frozen attempt ID. Do not
            # create a successor while a delayed submit might still arrive.
            await self.waiting(context, mark, "planner_absent", now)
            return
        if not retry:
            await self.failure(context,mark,code,material,now,
                status_reason="planner_cancelled" if code=="explicit_cancel" or status=="cancelled" else "planner_failed",
                physical_failure=status in {"failed", "cancelled"} or code == "explicit_cancel")
            return
        async with self.life.database.transaction() as connection:
            fresh=await (await connection.execute("SELECT * FROM life.plan_watermarks WHERE actor_id=%s FOR UPDATE",(context.actor_id,))).fetchone()
            if (not fresh or fresh["revision"]!=mark["revision"] or fresh["generation_key"]!=mark["generation_key"]
                    or fresh["generation_consumed"]):
                return
            count=(fresh["computation_retry_count"] if fresh["generation_material"]==material else 0)+1
            retry_at=now+timedelta(seconds=retry_delay_seconds(count))
            recovery = await plan_recovery.fail_on(self.life, connection, mark["generation_key"], code, retry_after=retry_at)
            if recovery and recovery.get("recovery_state") == "unknown":
                await connection.execute("UPDATE life.plan_watermarks SET reason='planner_unknown' WHERE actor_id=%s", (context.actor_id,))
                await self.schedule(connection, context, fresh, now + timedelta(seconds=30), "observe")
                return
            exhausted = bool(recovery and (recovery.get("recovery_attempt_number") or 0) >= plan_recovery.MAX_ATTEMPTS)
            if exhausted:
                retry_at = None
            await self._finish(connection,context,fresh,"planner_retry_wait",consumed=True,next_due=retry_at)
            await connection.execute("UPDATE life.plan_watermarks SET last_error=%s,failed_generation_key=generation_key,"
                "computation_retry_count=%s,repair_due_at=%s,review_after=NULL,reason=%s WHERE actor_id=%s",
                (code,count,retry_at,"planner_attempts_exhausted" if exhausted else "planner_retry_wait",context.actor_id))
            await connection.execute("UPDATE life.plan_generations SET state='retry_wait',reason=%s,reviewed_at=now() WHERE generation_key=%s",
                (code,mark["generation_key"]))
            if retry_at:
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
            if code != "explicit_cancel" and status in {"queued","running","unknown"}:
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

    async def supersede(self, mark, reason, recovery_material):
        state = await self.worker.models.status(attempt_for(mark["generation_key"]))
        status = state.status if state else "absent"
        async with self.life.database.transaction() as connection:
            previous = await plan_recovery.generation_on(connection, mark["generation_key"], lock=True)
            await plan_recovery.cancel_on(self.life, connection, previous, recovery_material)
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

    async def mark_unknown(self, context, mark, code):
        async with self.life.database.transaction() as connection:
            fresh = await (await connection.execute("SELECT * FROM life.plan_watermarks WHERE actor_id=%s FOR UPDATE",
                (context.actor_id,))).fetchone()
            if (fresh and fresh["revision"] == mark["revision"] and fresh["generation_key"] == mark["generation_key"]
                    and not fresh["generation_consumed"]):
                await plan_recovery.unknown_on(self.life, connection, mark["generation_key"], code)

    async def failure(self, context, mark, reason, material, now, *, repairable=False, status_reason=None, physical_failure=False):
        """Reject a result without accepting its material as a valid decision."""
        count = (mark["repair_count"] if mark["generation_material"] == material else 0) + int(repairable)
        retry_at = now + REPAIR_DELAY * 2**max(0, count - 1) if repairable and count <= MAX_REPAIRS else None
        async with self.life.database.transaction() as connection:
            fresh = await (await connection.execute(
                "SELECT * FROM life.plan_watermarks WHERE actor_id=%s FOR UPDATE", (context.actor_id,))).fetchone()
            if (not fresh or fresh["revision"] != mark["revision"]
                    or fresh["generation_key"] != mark["generation_key"] or fresh["generation_consumed"]):
                return
            previous = await plan_recovery.generation_on(connection, mark["generation_key"], lock=True)
            if physical_failure:
                settled = await plan_recovery.fail_on(self.life, connection, mark["generation_key"],
                    "cancelled" if reason == "explicit_cancel" else reason)
                if settled and settled.get("recovery_state") == "unknown" and reason != "explicit_cancel":
                    await connection.execute("UPDATE life.plan_watermarks SET reason='planner_unknown' WHERE actor_id=%s", (context.actor_id,))
                    await self.schedule(connection, context, fresh, now + timedelta(seconds=30), "observe")
                    return
                if reason == "explicit_cancel":
                    previous = await plan_recovery.generation_on(connection, mark["generation_key"], lock=True)
                    if previous:
                        await plan_recovery.cancel_on(self.life, connection, previous, previous["recovery_version"])
            elif not repairable and previous and previous.get("recovery_state") == "reserved":
                await plan_recovery.cancel_on(self.life, connection, previous, previous["recovery_version"])
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
