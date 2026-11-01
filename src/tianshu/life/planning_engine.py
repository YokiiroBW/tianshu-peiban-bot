"""One decision joins directions, today's appointments and the next actual step."""
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from tianshu.conversation.codec import context_from_record, document, signature, source_refs
from tianshu.conversation.generation import attempt_for
from tianshu.runtime.errors import AppError
from tianshu.runtime.jobs import JobSpec
from tianshu.runtime.serialization import wire
from tianshu.sources.dependencies import unique_refs
from tianshu.types import RequestBudget
from . import planner
from .day_plan import awake_window, MAX_ACTIVITIES


def scene(context):
    return {key: sorted(getattr(context, key)) if key in {"scope_ids", "audience"} else getattr(context, key)
        for key in ("principal_id", "actor_id", "person_id", "conversation_id", "conversation_kind", "scope_ids", "audience", "delivery_allowed")}


def decision_fingerprint(context, actor, data, day, events=None):
    # Date is material; clock seconds and generic Growth/style revisions are not.
    return signature({"contract": planner.CONTRACT, "scene": scene(context), "day": day.isoformat() if hasattr(day,'isoformat') else day,
        "persona": (actor.get("persona_id"), actor.get("persona_revision")), "routine": data["routine"],
        "events": {key:value for key,value in (events or {}).items() if set(value.get("scope_ids",()))<=set(context.scope_ids)},
        "commitments": data["commitments"], "interests": data["interests"],
        "goals":data["goals"],"writing":data.get("writing",()),
        "regulation": {key:data["affect_regulation"].get(key) for key in ('mode','activity_bias')},
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
        if not mark or mark["revision"] != revision or (mark["planned_watermark"] == mark["change_watermark"] and (not mark["generation_key"] or mark["generation_consumed"])):
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
        now = datetime.now(UTC)
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
        material = decision_fingerprint(context, actor, data, day, mark["change"])
        pending = mark["generation_key"] and not mark["generation_consumed"]
        if not pending and mark["decision_fingerprint"] == material:
            await self.finish(context, mark, "unchanged_decision_material", fingerprint=material)
            return
        scheduled = [row for row in daily["slots"] if row["kind"] == "activity" and row["state"] in {"planned", "running"}]
        important = not mark["change_watermark"].startswith(("rhythm:", "activity-", "growth:"))
        if not pending and scheduled and not important and not mark["change_watermark"].startswith("growth:"):
            await self.finish(context, mark, "existing_activity_continues", fingerprint=material)
            return
        count = sum(row["kind"] == "activity" and row["state"] not in {"cancelled", "carried"} for row in daily["slots"])
        if not pending and count >= MAX_ACTIVITIES and not important:
            await self.finish(context, mark, "day_arrangements_complete", fingerprint=material)
            return
        frozen = None
        observe = False
        if pending:
            # Same material may arrive with a newer legal epoch. A genuinely new
            # scene/material can supersede a proposal, without rewriting its outcome.
            if mark["generation_material"] != material:
                await self.supersede(mark, "material_changed")
            else:
                try:
                    frozen = await self.worker.generations.observe_request(attempt_for(mark["generation_key"]), observer=context)
                except AppError as error:
                    if error.code == "generation_observation_context_mismatch":
                        await self.supersede(mark, "scene_changed")
                    elif error.code == "source_unavailable":
                        await self.finish(context, mark, "source_unavailable", consumed=True, fingerprint=material)
                        return
                    else:
                        raise
                if frozen is not None:
                    observe = self.worker.generations.context_key(frozen.context) != self.worker.generations.context_key(context)
        if frozen is None:
            if pending and mark["generation_material"] == material:
                # New requests are atomically frozen with the Life pointer, so a
                # missing original is an explicit integrity failure, never a replay.
                await self.finish(context, mark, "planner_original_request_missing", consumed=True, fingerprint=material)
                return
            key = "life-plan:" + signature({"actor": actor_id, "material": material})
            persona = await self.life.companion.persona(context, actor["persona_id"], actor["persona_revision"]) if actor.get("persona_id") else None
            try:
                frozen = await planner.request(self.worker.assembler, context, RequestBudget(now + timedelta(seconds=90)),
                    key=key, persona=persona.prompt_text if persona else "", data=data, refs=refs,
                    growth=await self.worker.growth.material(context) if self.worker.growth else ())
            except AppError as error:
                if error.code not in {"planner_context_budget_exceeded", "context_mandatory_budget_exceeded"}:
                    raise
                await self.finish(context, mark, "planner_context_budget_exceeded", fingerprint=material)
                return
            async with self.life.database.transaction() as connection:
                await self.life.validate_persistence(connection, context, frozen.source_refs)
                fresh = await (await connection.execute("SELECT * FROM life.plan_watermarks WHERE actor_id=%s FOR UPDATE", (actor_id,))).fetchone()
                if fresh["revision"] != revision:
                    return
                frozen = await self.worker.generations.prepare(key, frozen, connection=connection)
                await connection.execute(
                    "INSERT INTO life.plan_generations(generation_key,actor_id,day,material_fingerprint,base_revision,context) "
                    "VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING", (key, actor_id, day, material, revision, document(context)))
                await connection.execute("UPDATE life.plan_watermarks SET generation_material=%s,generation_key=%s,generation_consumed=false WHERE actor_id=%s",
                    (material, key, actor_id))
                mark = {**fresh, "generation_key": key, "generation_material": material, "generation_consumed": False}
        key = mark["generation_key"]

        async def current(connection):
            fresh = await (await connection.execute("SELECT * FROM life.plan_watermarks WHERE actor_id=%s FOR UPDATE", (actor_id,))).fetchone()
            return fresh and fresh["generation_key"] == key and not fresh["generation_consumed"]

        try:
            result = await self.worker.generations.run(key, frozen, guard=current, observer=context if observe else None)
        except AppError as error:
            if error.code == "generation_request_expired":
                await self.finish(context, mark, "original_planner_not_registered_before_deadline", consumed=True, fingerprint=material)
                return
            if error.code == "generation_no_longer_current":
                return
            raise
        status = result.receipt.status if result else "absent"
        if status != "succeeded":
            if status in {"failed", "cancelled"}:
                await self.finish(context, mark, "planner_" + status, consumed=True, fingerprint=material)
                return
            # Unknown is kept as unknown, with the same frozen attempt. A clock
            # event can observe it; it never earns a new ID merely by getting old.
            async with self.life.database.transaction() as connection:
                await connection.execute("UPDATE life.plan_watermarks SET reason=%s WHERE actor_id=%s AND generation_key=%s",
                    ("planner_" + status, actor_id, key))
                if status in {"queued", "running"}:
                    await self.life.jobs.enqueue(connection, JobSpec("life.plan", f"plan-observe:{key}:{revision}:{now.isoformat()}",
                        {"actor_id": actor_id, "revision": revision, "context": wire(context)},
                        scheduled_at=now + timedelta(seconds=30), subject_id=actor_id))
            return
        try:
            decision, original_data = planner.decision(result, frozen)
            # Catalog changes and source changes are rechecked for actual dispatch.
            available = {item.id: item for item in planner.eligible_capabilities(choices)}
            for activity in ([decision["activity"]] if decision["activity"] else []) + decision["arrangements"]:
                if activity["capability_id"] and activity["capability_id"] not in available:
                    raise AppError("planner_capability_unavailable")
                if activity["capability_id"] and not activity.get("existing_activity_id") and not planner.valid_arguments(
                        available[activity["capability_id"]].input_schema, activity["arguments"]):
                    raise AppError("planner_arguments_invalid")
        except AppError as error:
            await self.finish(context, mark, error.code, consumed=True, fingerprint=material)
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
            reviewed=decision_fingerprint(context,actor,post_data,day,fresh["change"])
            reason = "planned_with_reduced_context" if planner.reduced(original_data) else "planned" if created or prepared_contact else "no_meaningful_activity"
            await self._finish(connection, context, fresh, reason, fingerprint=reviewed, consumed=True)
            await connection.execute("UPDATE life.plan_generations SET state='applied',reason=%s,reviewed_at=now() WHERE generation_key=%s", (reason, key))

    async def supersede(self, mark, reason):
        state = await self.worker.models.status(attempt_for(mark["generation_key"]))
        status = state.status if state else "absent"
        async with self.life.database.transaction() as connection:
            await connection.execute("UPDATE life.plan_generations SET state='superseded',reason=%s,reviewed_at=now() WHERE generation_key=%s AND state='pending'",
                (reason + ":" + status, mark["generation_key"]))

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
            await self._finish(connection, context, mark, reason, **kwargs)
