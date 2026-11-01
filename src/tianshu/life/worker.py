"""Change-driven durable life jobs. External completion always comes from an actual receipt."""
from __future__ import annotations
from tianshu.sources.dependencies import unique_refs

from datetime import UTC, datetime, timedelta
import json
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

from tianshu.runtime.errors import AppError
from tianshu.runtime.jobs import JobSpec
from tianshu.runtime.serialization import wire
from tianshu.types import Action, OriginKind, ServiceClass
from tianshu.conversation.codec import context_from_record, artifacts, document, receipt, source_refs
from tianshu.conversation.types import ExpressionPlan, InputPart
from tianshu.conversation.generation import GenerationRequests
from tianshu.capabilities.actions import result_sources
from .attention import contact_expiry
from .salience import evaluate, fingerprint, similarity, interest_fit
from .imagined import record_completed_imagined
from .types import ContactCandidate, PreparedContact
from . import planner


def future_occurrence(row, now):
    if row["recurrence"] is None or row["due_at"] is None:
        return None
    local = row["due_at"].astimezone(ZoneInfo(row["timezone"]))
    current = now.astimezone(ZoneInfo(row["timezone"]))
    base = current.replace(hour=local.hour, minute=local.minute, second=local.second, microsecond=local.microsecond)
    for offset in range(0, 8):
        candidate = base + timedelta(days=offset)
        if candidate > current and (row["recurrence"] == "daily" or candidate.weekday() in row["weekdays"]):
            return candidate.astimezone(UTC)
    return None


class LifeWorker:
    def __init__(self, life, models, assembler, catalog, actions, expression, growth=None):
        self.life, self.models, self.assembler, self.catalog = life, models, assembler, catalog
        self.actions, self.expression, self.growth = actions, expression, growth
        self.generations = GenerationRequests(life.database, life.sources, models)
        self.writing=None

    def bind_writing(self,writing):
        self.writing=writing

    async def plan(self, actor_id, revision, context):
        from .planning_engine import PlanningEngine
        return await PlanningEngine(self).run(actor_id, revision, context)

    async def contact_preflight(self,context,candidate_id,*,expression_id=None):
        from .contact_control import preflight
        return await preflight(self,context,candidate_id,expression_id=expression_id)

    async def reconcile_contact(self,candidate_id,expected_revision):
        from .reconciliation import contact
        return await contact(self,candidate_id,expected_revision)

    async def reconcile_commitment(self,commitment_id,expected_revision):
        from .reconciliation import commitment
        return await commitment(self,commitment_id,expected_revision)

    async def recover(self):
        from .recovery import recover
        return await recover(self)

    async def activity_step(self, activity_id, expected_revision, context):
        from .recovery import scheduled_context
        context = await scheduled_context(self.life, context)
        await self.life.identity.check(context)
        choices = await self.catalog.discover(context)
        available = {choice["definition"].id for choice in choices if choice["can_execute"]}
        fictional_done = False
        async with self.life.database.transaction() as connection:
            row = await self.life.lock_row(connection,context,"SELECT * FROM life.activities WHERE id=%s AND actor_id=%s FOR UPDATE", (activity_id, context.actor_id))
            await connection.execute("SELECT actor_id FROM life.actor_state WHERE actor_id=%s FOR UPDATE", (context.actor_id,))
            if not row or row["revision"] != expected_revision or row["state"] not in {"planned", "running"}:
                return
            if row['state']=='planned' and row['goal_id'] and not row['action_id']:
                goal=await (await connection.execute('SELECT state FROM life.goals WHERE id=%s AND actor_id=%s',(row['goal_id'],context.actor_id))).fetchone()
                if goal and goal['state']!='active':
                    await connection.execute("UPDATE life.activities SET state='dormant',waiting_reason='goal_not_active',resume_condition='goal_active',revision=revision+1 WHERE id=%s",(activity_id,))
                    await self.life.days.result(connection,activity_id,state='cancelled')
                    return
            now = datetime.now(UTC)
            if row["protected_until"] and row["protected_until"] > now:
                await self.life.jobs.enqueue(connection, JobSpec("life.activity_step", f"activity-protected:{activity_id}:{expected_revision}:{row['protected_until'].isoformat()}",
                    {"activity_id":activity_id,"expected_revision":expected_revision,"context":wire(context)}, scheduled_at=row["protected_until"], subject_id=context.actor_id))
                return
            if row["state"] == "planned" and row["next_due_at"] and row["next_due_at"] > now:
                return
            if row["state"] == "planned":
                from .day_plan import awake_window
                routine = await (await connection.execute("SELECT * FROM life.routines WHERE actor_id=%s", (context.actor_id,))).fetchone()
                begins, wake = awake_window(routine, now)
                if begins is None or (row["next_due_at"] and row["next_due_at"].astimezone(ZoneInfo(routine["timezone"] if routine else "Asia/Shanghai")).date() < now.astimezone(ZoneInfo(routine["timezone"] if routine else "Asia/Shanghai")).date()):
                    await connection.execute("UPDATE life.activities SET state='dormant',waiting_reason='day_finished',resume_condition='next_day_plan',next_due_at=NULL,revision=revision+1 WHERE id=%s", (activity_id,))
                    await self.life.days.result(connection, activity_id, state="carried")
                    return
            other = await (await connection.execute("SELECT id,revision,next_due_at FROM life.activities WHERE actor_id=%s AND state='running' AND id<>%s", (context.actor_id, activity_id))).fetchone()
            if other:
                await connection.execute("UPDATE life.activities SET waiting_reason='another_activity_running' WHERE id=%s", (activity_id,))
                due = max(now+timedelta(minutes=1), other["next_due_at"] or now+timedelta(minutes=5))
                await self.life.jobs.enqueue(connection, JobSpec("life.activity_step", f"activity-wait:{activity_id}:{expected_revision}:{other['id']}:{other['revision']}",
                    {"activity_id":activity_id,"expected_revision":expected_revision,"context":wire(context)}, scheduled_at=due, subject_id=context.actor_id))
                return
            if row["action_id"]:
                return  # Reconcile an existing external attempt, never submit it a second time.
            await self.life.validate_persistence(connection, context, source_refs(row["source_refs"]))
            data, now = row["checkpoint_data"], datetime.now(UTC)
            capability_id = data.get("capability_id")
            if capability_id is None:
                if row["kind"] not in {"rest", "reflection", "room"}:
                    await connection.execute("UPDATE life.activities SET state='dormant',waiting_reason='capability_required',resume_condition='configure_capability',revision=revision+1 WHERE id=%s", (activity_id,))
                    return
                if row["waiting_reason"] == "fictional_activity" and row["next_due_at"] and row["next_due_at"] <= now:
                    completion, refs = await record_completed_imagined(self.life, context, row,
                        expected_revision, now, connection)
                    await connection.execute("UPDATE life.activities SET state='completed',waiting_reason=NULL,next_due_at=NULL,checkpoint_step=checkpoint_step+1,revision=revision+1 WHERE id=%s", (activity_id,))
                    await connection.execute("UPDATE life.actor_state SET activity_id=NULL,revision=revision+1 WHERE actor_id=%s AND activity_id=%s", (context.actor_id, activity_id))
                    fictional_done = True
                    await self.life.days.result(connection, activity_id)
                    await self.life.memory.observe_episode(context, {"episode_id": f"fiction:{activity_id}:{expected_revision}", "event_id": f"fiction:{activity_id}:{expected_revision}",
                        "revision":1,"origin_kind":"actor","actor_id":context.actor_id,"occurred_at":now,"summary":"想象活动完成："+row["title"],
                        "source_refs":refs,"actual_result":completion},connection=connection)
                    await self.life.signal(context,f"activity-finished:{activity_id}:{expected_revision}",connection=connection)
                else:
                    due = now+timedelta(minutes=int(data.get("duration_minutes",30)))
                    await connection.execute("UPDATE life.activities SET state='running',waiting_reason='fictional_activity',next_due_at=%s WHERE id=%s", (due, activity_id))
                    await connection.execute("UPDATE life.actor_state SET activity_id=%s,revision=revision+1 WHERE actor_id=%s", (activity_id, context.actor_id))
                    await self.life.jobs.enqueue(connection, JobSpec("life.activity_step", f"activity-fiction:{activity_id}:{expected_revision}",
                        {"activity_id": activity_id, "expected_revision": expected_revision, "context": wire(context)}, scheduled_at=due, subject_id=context.actor_id))
                    return
            else:
                if capability_id not in available or (data.get("autonomous") and capability_id not in {item.id for item in planner.eligible_capabilities(choices)}):
                    await connection.execute("UPDATE life.activities SET state='dormant',waiting_reason='capability_unavailable',resume_condition='capability_available',wake_condition=%s,revision=revision+1 WHERE id=%s", (document({"kind":"capability_available","subject_id":capability_id}),activity_id))
                    return
                action_id = str(uuid5(NAMESPACE_URL, f"activity:{activity_id}:{expected_revision}:{row['checkpoint_step']}"))
                deadline = now+timedelta(minutes=30)
                if capability_id=="images.generate":
                    arguments=dict(data.get("arguments",{}))
                    requested_kind=arguments.get("delivery_kind","create_only")
                    selected = await self.life.contacts.resolve_target(context, source_refs(row["source_refs"]), identity=self.life.identity) if requested_kind=="share_candidate" else None
                    if selected is not None:
                        context=selected
                        await connection.execute("UPDATE life.activities SET context=%s,scope_ids=%s WHERE id=%s", (document(context),list(context.scope_ids),activity_id))
                        await connection.execute("UPDATE life.day_slots SET scope_ids=%s WHERE activity_id=%s",(list(context.scope_ids),activity_id))
                    kind="share_candidate" if selected is not None else "create_only"
                    arguments.update(target_ref=context.conversation_id if kind!="create_only" else None,delivery_kind=kind,
                                     trigger="actor",window={"due_at":now.isoformat(),"expires_at":deadline.isoformat()},
                                     dedup_key=f"activity-photo:{activity_id}:{expected_revision}:{row['checkpoint_step']}",
                                     motivation=arguments.get("motivation") or row["title"])
                    data={**data,"arguments":arguments}
                    await connection.execute("UPDATE life.activities SET checkpoint_data=%s WHERE id=%s",(document(data),activity_id))
                await connection.execute("UPDATE life.activities SET state='running',action_id=%s,last_result_revision=-1,last_receipt_id=NULL,waiting_reason='capability_queued',next_due_at=%s WHERE id=%s", (action_id, deadline, activity_id))
                await connection.execute("UPDATE life.actor_state SET activity_id=%s,revision=revision+1 WHERE actor_id=%s", (activity_id, context.actor_id))
                await connection.execute("UPDATE life.day_slots SET state='running' WHERE activity_id=%s AND state='planned'", (activity_id,))
                await self.life.jobs.enqueue(connection, JobSpec("life.activity_reconcile", f"activity-reconcile:{action_id}:0", {"activity_id": activity_id, "context": wire(context), "sequence": 0}, scheduled_at=now+timedelta(seconds=15), subject_id=context.actor_id))
                frozen=Action(action_id,f"activity:{activity_id}:{expected_revision}:{row['checkpoint_step']}",capability_id,context.actor_id,context,data.get("arguments",{}),OriginKind.ACTOR,source_refs(row["source_refs"]),activity_id=activity_id,matter_id=row["matter_id"],deadline_at=deadline)
                await connection.execute("UPDATE life.activities SET checkpoint_data=checkpoint_data||%s WHERE id=%s",(document({"frozen_action":wire(frozen)}),activity_id))
        if fictional_done:
            return
        action = Action(action_id, f"activity:{activity_id}:{expected_revision}:{row['checkpoint_step']}", capability_id,
            context.actor_id, context, data.get("arguments", {}), OriginKind.ACTOR,
            source_refs(row["source_refs"]), activity_id=activity_id, matter_id=row["matter_id"], deadline_at=deadline)
        result = receipt(await self.actions.invoke(action, background=True, service_class=ServiceClass.ELASTIC))
        await self.capability_result(context, result)

    async def reconcile_activity(self, activity_id, context, sequence):
        from .recovery import scheduled_context
        context = await scheduled_context(self.life, context)
        await self.life.identity.check(context)
        async with self.life.database.transaction(read_only=True) as connection:
            row = await (await connection.execute("SELECT * FROM life.activities WHERE id=%s AND actor_id=%s", (activity_id, context.actor_id))).fetchone()
        if not row or not row["action_id"] or row["state"] in {"cancelled", "completed"} or row["checkpoint_data"].get("completed_action_id") == row["action_id"]:
            return
        await self.life.identity.check(context, source_refs(row["source_refs"]))
        try:
            actual = await self.actions.get(row["action_id"], context)
        except AppError as error:
            if error.status != 404:
                raise
            from .reconciliation import absent_action
            original=await absent_action(self,row["checkpoint_data"].get("frozen_action"))
            if original:
                await self.capability_result(context,original)
                return
            async with self.life.database.transaction() as connection:
                await connection.execute("UPDATE life.activities SET state='dormant',waiting_reason='outcome_unknown',resume_condition='reconcile_original_action',revision=revision+1 WHERE id=%s AND action_id=%s", (activity_id, row["action_id"]))
                await connection.execute("UPDATE life.actor_state SET activity_id=NULL,revision=revision+1 WHERE actor_id=%s AND activity_id=%s",(context.actor_id,activity_id))
                await self.life.days.result(connection,activity_id,state='unknown')
            return
        if actual["receipt"]:
            await self.capability_result(context, actual["receipt"])
        result = receipt(actual["receipt"])
        if result and result.execution in {"pending", "running"} and row["next_due_at"] and row["next_due_at"] > datetime.now(UTC):
            async with self.life.database.transaction() as connection:
                await self.life.jobs.enqueue(connection, JobSpec("life.activity_reconcile", f"activity-reconcile:{row['action_id']}:{sequence+1}", {"activity_id": activity_id, "context": wire(context), "sequence": sequence+1}, scheduled_at=datetime.now(UTC)+timedelta(seconds=15), subject_id=context.actor_id))
        elif not result or result.execution in {"pending", "running"}:
            async with self.life.database.transaction() as connection:
                await connection.execute("UPDATE life.activities SET state='dormant',waiting_reason='outcome_unknown',resume_condition='reconcile_original_action',revision=revision+1 WHERE id=%s AND action_id=%s", (activity_id, row["action_id"]))
                await connection.execute("UPDATE life.actor_state SET activity_id=NULL,revision=revision+1 WHERE actor_id=%s AND activity_id=%s",(context.actor_id,activity_id))
                await self.life.days.result(connection,activity_id,state='unknown')

    async def capability_result(self, context, result, *, result_revision=None, observed_at=None):
        result = receipt(result)
        if result is None or not result.action_id:
            return False
        current = await self.actions.get(result.action_id, context)
        result, result_revision, observed_at = receipt(current["receipt"]), current["result_revision"], current["result_observed_at"]
        capability_id = current["action"]["capability_id"]
        if result is not None:
            activity = await self._activity_result(context, result, result_revision, observed_at, capability_id)
            commitment = await self._commitment_result(context, result, result_revision, observed_at, capability_id)
            return activity or commitment
        return False

    async def _receipt_sources(self, context, result, result_revision, observed_at, connection, dependencies):
        refs = unique_refs(tuple(dependencies), result_sources(result))
        original = await self.life.sources.record_event(context, producer="life:action",
            event_id="result:" + result.action_id, revision=result_revision,
            occurred_at=observed_at, origin_kind=OriginKind.TOOL_RESULT,
            parts=(InputPart("text", json.dumps(wire(result), ensure_ascii=False), metadata={
                "derived_kind": "action_receipt", "action_id": result.action_id,
                "receipt_id": result.receipt_id, "execution": result.execution, "effect": result.effect}),),
            support_refs=refs, connection=connection)
        return unique_refs(refs, original)

    async def _activity_result(self, context, result, result_revision, observed_at, capability_id):
        await self.life.identity.check(context)
        async with self.life.database.transaction(read_only=True) as probe:
            snapshot = await (await probe.execute("SELECT * FROM life.activities WHERE action_id=%s AND actor_id=%s", (result.action_id, context.actor_id))).fetchone()
        prepared = None
        if snapshot and snapshot["state"] != "cancelled":
            from .photo_contacts import prepare
            prepared = await prepare(self, context, result, source_refs(snapshot["source_refs"]), snapshot["title"])
        async with self.life.database.transaction() as connection:
            row = await self.life.lock_row(connection,context,"SELECT * FROM life.activities WHERE action_id=%s AND actor_id=%s FOR UPDATE", (result.action_id, context.actor_id), additional_refs=result_sources(result))
            if not row:
                return False
            if result_revision <= row["last_result_revision"]:
                return True
            refs = unique_refs(source_refs(row["source_refs"]), result_sources(result))
            await self.life.validate_persistence(connection, context, refs)
            await connection.execute("UPDATE life.activities SET last_result_revision=%s,last_receipt_id=%s,updated_at=now() WHERE id=%s", (result_revision, result.receipt_id, row["id"]))
            if result.execution in {"pending", "running"}:
                if row["state"] not in {"completed", "cancelled"}:
                    await connection.execute("UPDATE life.activities SET state='dormant',waiting_reason='capability_running',resume_condition='original_action_result',revision=revision+1 WHERE id=%s", (row["id"],))
                    await connection.execute("UPDATE life.actor_state SET activity_id=NULL,revision=revision+1 WHERE actor_id=%s AND activity_id=%s",(context.actor_id,row["id"]))
                    await self.life.days.result(connection,row["id"],state="waiting_result")
                return True
            uncertain = result.execution == "unknown" or result.effect == "unknown"
            continuing,confirmed=False,True
            if row["state"] != "cancelled":
                if uncertain:
                    await connection.execute("UPDATE life.activities SET state='dormant',waiting_reason='outcome_unknown',resume_condition='reconcile_original_action',revision=revision+1 WHERE id=%s", (row["id"],))
                elif result.execution == "succeeded":
                    from tianshu.capabilities.content.continuation import continuation_step, exploration_checkpoint
                    consumption=result.result.get("consumption")
                    next_position=consumption.get("next_position") if consumption else result.result.get("next_position")
                    continuation=continuation_step(capability_id,row["checkpoint_data"].get("arguments",{}),result.result)
                    exploration=exploration_checkpoint(capability_id,row["checkpoint_data"].get("arguments",{}),result.result,
                        previous=row["checkpoint_data"].get("exploration"))
                    if continuation is None and exploration.get("next_actions"):
                        following=exploration["next_actions"][0]
                        continuation={key:following[key] for key in ("capability_id","arguments")}
                    if continuation is None and not exploration.get('next_actions'):
                        continuation=exploration.get('discovery_continuation')
                    follow_up=not continuation and bool((exploration.get('current_item') or {}).get('follow_up'))
                    continuing=next_position is not None or continuation is not None
                    confirmed = not consumption or consumption.get("analysis_completed") is True
                    step = 0 if row["checkpoint_data"].get("completed_action_id") == result.action_id else 1
                    await connection.execute("UPDATE life.activities SET state=%s,waiting_reason=%s,resume_condition=%s,checkpoint_step=checkpoint_step+%s,checkpoint_data=checkpoint_data||%s,revision=revision+1 WHERE id=%s", (
                        "dormant" if continuing or follow_up or not confirmed else "completed", "analysis_unconfirmed" if not confirmed else "continuation_available" if continuing else "follow_up_available" if follow_up else None,
                        "review_original_result" if not confirmed else "continue_from_checkpoint" if continuing else "plan_later_check" if follow_up else None, step,
                        document({"completed_action_id": result.action_id, "actual_receipt_id": result.receipt_id,
                                  "next_position": next_position, "continuation": continuation, "consumption":consumption,
                                  "exploration":exploration,
                                  "last_execution":"succeeded","result_summary":str(result.result.get("summary") or row["title"])[:1200],
                                  "completion_basis":"partial_source_read" if continuing else "requested_window_ended" if consumption else "actual_result",
                                  "coverage": wire(result.coverage)}), row["id"]))
                else:
                    await connection.execute("UPDATE life.activities SET state='dormant',waiting_reason=%s,resume_condition='review_failure',revision=revision+1 WHERE id=%s", (result.error_code or result.execution, row["id"]))
            await connection.execute("UPDATE life.actor_state SET activity_id=NULL,revision=revision+1 WHERE actor_id=%s AND activity_id=%s", (context.actor_id, row["id"]))
            summary = row["title"] if result.execution == "succeeded" and not uncertain else ("结果未确认：" if uncertain else "操作未完成：")+row["title"]
            if capability_id=="writing.request" and result.execution=="succeeded":
                summary="已安排章节创作，正文尚未由此回执确认："+row["title"]
            refs = await self._receipt_sources(context, result, result_revision, observed_at, connection, refs)
            await connection.execute("UPDATE life.activities SET source_refs=%s WHERE id=%s",(document(refs),row["id"]))
            await connection.execute("UPDATE life.day_slots SET source_refs=%s WHERE activity_id=%s",(document(refs),row["id"]))
            await self.life.days.result(connection,row["id"],continuing=result.execution=="succeeded" and not uncertain and continuing and confirmed,
                state="unknown" if uncertain else "analysis_unconfirmed" if not confirmed else "completed" if result.execution=="succeeded" else "failed")
            if result.execution=="succeeded" and not uncertain and confirmed and row["state"]!="cancelled":
                goal_context=context
                if row["checkpoint_data"].get("planning_context"):
                    from .recovery import scheduled_context
                    goal_context=await scheduled_context(self.life,row["checkpoint_data"]["planning_context"],connection=connection)
                if (await self.life.identity.check_many(goal_context,(refs,)))[0]:
                    await self.life.directions.observe_result(connection,goal_context,row,result,result_revision,capability_id,refs)
                if not continuing:
                    await self.life.wake(context,kind="activity_completed",subject_id=row["id"],event_id=result.receipt_id,connection=connection)
            await self.life.memory.observe_episode(context, {"episode_id": "action:"+result.action_id,
                "event_id": "action:"+result.action_id, "revision": result_revision,
                "origin_kind": "tool_result", "actor_id": context.actor_id, "subject_id": context.actor_id,
                "occurred_at": observed_at, "summary": summary, "source_refs": refs,
                "matter_id": row["matter_id"], "action_id": result.action_id, "receipt_id": result.receipt_id,
                "coverage": result.coverage, "actual_result": {**result.result, "execution": result.execution, "effect": result.effect,
                    "delivery": result.delivery, "receipt_id": result.receipt_id, "capability_id": capability_id}}, connection=connection)
            if prepared is not None and row["state"] != "cancelled":
                await self.persist_contact(connection, prepared)
            if result.execution != "unknown" and result.effect != "unknown":
                await self.life.signal(context,f"activity-result:{result.action_id}:{result_revision}",connection=connection,
                    change={"capability_id":capability_id,"action_id":result.action_id,"observed_at":observed_at.isoformat()})
        return True

    async def cancel_activity(self, activity_id, context):
        context = context_from_record(context)
        await self.life.identity.check(context)
        async with self.life.database.transaction(read_only=True) as connection:
            row = await (await connection.execute("SELECT * FROM life.activities WHERE id=%s AND actor_id=%s", (activity_id, context.actor_id))).fetchone()
        if not row or row["state"] != "cancelled" or not row["action_id"]:
            return
        result = receipt(await self.actions.cancel(row["action_id"], context))
        async with self.life.database.transaction() as connection:
            await connection.execute("UPDATE life.activities SET waiting_reason=%s,last_receipt_id=%s WHERE id=%s AND state='cancelled'", (
                "external_cancelled" if result.execution == "cancelled" else "cancellation_outcome_unknown" if result.execution == "unknown" or result.effect == "unknown" else "external_action_already_finished" if result.execution in {"succeeded", "failed"} else "external_cancellation_pending", result.receipt_id, activity_id))

    async def prepare_contact(self, candidate: ContactCandidate):
        context = candidate.context
        if candidate.actor_id != context.actor_id or candidate.target_ref != context.conversation_id:
            raise AppError("contact_target_mismatch", status=403)
        await self.life.identity.check(context, candidate.source_refs)
        preferences, history = await self.life.contacts.preferences(context), await self.life.contacts.history(context)
        content_fingerprint = fingerprint(candidate.summary, [ref.source_id for ref in candidate.source_refs])
        repetition = max((similarity(content_fingerprint, item["signature"]) for item in history), default=0)
        habits = await self.growth.view(context) if self.growth else {"items": []}
        interests = [{"topic": row["title"], "strength": .8} for row in habits["items"] if row["kind"] == "interest" and row["state"] == "active"]
        affect = await self.life.companion.affect(context)
        assessment = evaluate({"novelty": 1-repetition, "interest_fit": interest_fit(candidate.summary, interests),
            "affect_pull": abs(affect["valence"]), "recency_need": .5,
            "social_cost": 1 if preferences["availability"] in {"busy", "sleeping", "away"} else .2, "repetition": repetition}, preferences["responsiveness"])
        payload = {**(candidate.payload or {}), "salience": assessment, "fingerprint": content_fingerprint}
        return PreparedContact(candidate, payload, preferences["expiry_seconds"])

    async def persist_contact(self, connection, prepared: PreparedContact):
        candidate, payload = prepared.candidate, prepared.payload
        context = candidate.context
        await self.life.validate_persistence(connection,context,candidate.source_refs)
        added = await (await connection.execute(
            "INSERT INTO life.contact_candidates(id,actor_id,target_ref,person_id,kind,summary,due_at,expires_at,state,explicit,delivery_kind,action_id,context,source_refs,payload,created_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,"
            "CASE WHEN %s THEN %s::timestamptz ELSE LEAST(%s::timestamptz,now()+%s*interval '1 second') END,"
            "'pending',%s,%s,%s,%s,%s,%s,now()) ON CONFLICT(id) DO NOTHING RETURNING id",
            (candidate.candidate_id, candidate.actor_id, candidate.target_ref, context.person_id, candidate.kind, candidate.summary,
             candidate.due_at, candidate.explicit or candidate.kind == "reply", candidate.expires_at,
             candidate.expires_at, prepared.expiry_seconds, candidate.explicit, candidate.delivery_kind,
             candidate.action_id, document(context), document(candidate.source_refs), document(payload)))).fetchone()
        if added:
            await self.life.jobs.enqueue(connection, JobSpec("life.contact_due", "contact:"+candidate.candidate_id+":1",
                {"candidate_id": candidate.candidate_id, "expected_revision": 1}, ServiceClass.DUE if candidate.explicit else ServiceClass.ELASTIC, candidate.due_at, context.actor_id))

    async def propose_contact(self, candidate: ContactCandidate):
        prepared = await self.prepare_contact(candidate)
        async with self.life.database.transaction() as connection:
            await self.persist_contact(connection, prepared)

    async def commitment_due(self, commitment_id, expected_revision):
        async with self.life.database.transaction(read_only=True) as connection:
            initial = await (await connection.execute("SELECT * FROM life.commitments WHERE id=%s", (commitment_id,))).fetchone()
        if not initial:
            return
        from .recovery import scheduled_context
        context = await scheduled_context(self.life, initial["context"])
        await self.life.identity.check(context, source_refs(initial["source_refs"]))
        # Discovery happens before the short domain transaction, without holding a
        # business connection while another domain reads its configuration.
        definition = None
        if initial["capability_id"]:
            choices = await self.catalog.discover(context)
            definition = next((item["definition"] for item in choices
                               if item["definition"].id == initial["capability_id"] and item["can_execute"]), None)
        async with self.life.database.transaction() as connection:
            row = await self.life.lock_row(connection,context,"SELECT * FROM life.commitments WHERE id=%s FOR UPDATE", (commitment_id,))
            if not row or row["revision"] != expected_revision or row["state"] not in {"scheduled", "waiting"}:
                return
            now = datetime.now(UTC)
            if row["due_at"] is None or row["due_at"] > now:
                return
            if row["wake_condition"]:
                # A due time is the alternative wake boundary of a stored event wait.
                await connection.execute('UPDATE life.commitments SET wake_condition=NULL WHERE id=%s',(commitment_id,))
            if (now-row["due_at"]).total_seconds() > row["grace_seconds"]:
                if row["missed_policy"]=="reschedule" and row['reason']!='rescheduled_after_missed_window':
                    due=now+timedelta(minutes=5)
                    await connection.execute("UPDATE life.commitments SET due_at=%s,revision=revision+1,reason='rescheduled_after_missed_window' WHERE id=%s",(due,commitment_id))
                    await self.life.jobs.enqueue(connection,JobSpec("life.commitment_due",f"commitment:{commitment_id}:{expected_revision+1}",{"commitment_id":commitment_id,"expected_revision":expected_revision+1},ServiceClass.DUE,due,context.actor_id))
                    return
                if row["missed_policy"] in {'once','reschedule'}:
                    row={**row,"execution_due_at":now}
                else:
                    await connection.execute("UPDATE life.commitments SET state='expired',reason='missed_window',revision=revision+1 WHERE id=%s", (commitment_id,))
                    next_due = future_occurrence(row, now)
                    if next_due:
                        await self._recur(connection, row, next_due)
                    return
            if row["capability_id"] and definition is None:
                await connection.execute("UPDATE life.commitments SET state='waiting',reason='capability_unavailable',wake_condition=%s WHERE id=%s",(document({"kind":"capability_available","subject_id":row["capability_id"]}),commitment_id))
                return
            if not row["target_ref"] and not row["capability_id"]:
                await connection.execute("UPDATE life.commitments SET state='waiting',reason='destination_required' WHERE id=%s", (commitment_id,))
                return
            action_id = str(uuid5(NAMESPACE_URL, f"commitment:{commitment_id}:{expected_revision}")) if row["capability_id"] else None
            expression_id = None if action_id else f"commitment:{commitment_id}:{expected_revision}"
            arguments = dict(row["arguments"])
            if row["capability_id"] == "images.generate":
                delivery_kind = arguments.get("delivery_kind", "requested_delivery")
                if not context.delivery_allowed or not context.conversation_id:
                    delivery_kind = "create_only"
                arguments.update(
                    trigger="schedule", delivery_kind=delivery_kind,
                    target_ref=context.conversation_id if delivery_kind != "create_only" else None,
                    window={"due_at": row.get("execution_due_at",row["due_at"]).isoformat(),
                            "expires_at": (row.get("execution_due_at",row["due_at"]) + timedelta(seconds=max(60,row["grace_seconds"]))).isoformat()},
                    dedup_key=f"commitment:{commitment_id}:{expected_revision}",
                    motivation=row["title"],
                )
            await connection.execute(
                "UPDATE life.commitments SET state='dispatching',reason=NULL,action_id=%s,"
                "last_result_revision=-1,last_receipt_id=NULL,last_expression_id=%s,arguments=%s,context=%s WHERE id=%s",
                (action_id, expression_id, document(arguments), document(context), commitment_id))
            if action_id:
                frozen=Action(action_id,f"commitment:{commitment_id}:{expected_revision}",row["capability_id"],context.actor_id,context,arguments,OriginKind.TIMER,source_refs(row["source_refs"]),matter_id=row["matter_id"],deadline_at=row.get("execution_due_at",row["due_at"])+timedelta(seconds=max(60,row["grace_seconds"])))
                await connection.execute("UPDATE life.commitments SET frozen_action=%s WHERE id=%s",(document(frozen),commitment_id))
        try:
            if action_id:
                action = Action(action_id, f"commitment:{commitment_id}:{expected_revision}", row["capability_id"], context.actor_id,
                    context, arguments, OriginKind.TIMER, source_refs(row["source_refs"]), matter_id=row["matter_id"],
                    deadline_at=row.get("execution_due_at",row["due_at"])+timedelta(seconds=max(60,row["grace_seconds"])))
                result = receipt(await self.actions.invoke(
                    action, background=True, service_class=ServiceClass.DUE))
                await self.capability_result(context, result)
                return
            # A promise at an explicit time outranks the default unsolicited quiet period.
            plan = ExpressionPlan(expression_id, context.actor_id, row["target_ref"], ({"kind": "text", "text": row["title"]},), source_refs(row["source_refs"]), OriginKind.TIMER)
            output = await self.expression.send(context,plan,service_class=ServiceClass.DUE)
            result=output.receipt
            state="completed" if output.state=="delivered" else "waiting_expression" if output.state in {"waiting_render","ready","waiting_order","delivering","waiting_delivery"} else "unknown" if output.state in {"render_unknown","delivery_unknown"} else "failed"
            reason=output.reason
        except BaseException as error:
            async with self.life.database.transaction() as connection:
                await connection.execute("UPDATE life.commitments SET state='unknown',reason=%s,revision=revision+1 WHERE id=%s AND state='dispatching'", (getattr(error, "code", "commitment_outcome_unknown"), commitment_id))
            raise
        async with self.life.database.transaction() as connection:
            fresh = await self.life.lock_row(connection,context,"SELECT * FROM life.commitments WHERE id=%s FOR UPDATE", (commitment_id,))
            if fresh["revision"] != expected_revision or fresh["state"] != "dispatching":
                return
            await connection.execute("UPDATE life.commitments SET state=%s,last_receipt_id=%s,reason=%s,revision=revision+1,updated_at=now() WHERE id=%s", (state, result.receipt_id if result else None, reason, commitment_id))
            if state == "completed":
                next_due = future_occurrence(fresh, datetime.now(UTC))
                if next_due:
                    await self._recur(connection, fresh, next_due)

    async def _commitment_result(self, context, result, result_revision, observed_at, capability_id):
        await self.life.identity.check(context)
        async with self.life.database.transaction(read_only=True) as probe:
            snapshot=await (await probe.execute("SELECT * FROM life.commitments WHERE action_id=%s AND actor_id=%s",(result.action_id,context.actor_id))).fetchone()
        prepared=None
        if snapshot and snapshot["state"] not in {"completed","expired","cancelled","scheduled"}:
            from .photo_contacts import prepare
            prepared=await prepare(self,context,result,source_refs(snapshot["source_refs"]),snapshot["title"])
        async with self.life.database.transaction() as connection:
            row = await self.life.lock_row(connection,context,"SELECT * FROM life.commitments WHERE action_id=%s AND actor_id=%s FOR UPDATE", (result.action_id, context.actor_id), additional_refs=result_sources(result))
            if not row:
                return False
            if row["state"] in {"completed", "expired", "cancelled", "scheduled"}:
                return True
            if result_revision <= row["last_result_revision"]:
                return True
            await connection.execute("UPDATE life.commitments SET last_result_revision=%s WHERE id=%s", (result_revision, row["id"]))
            state = "unknown" if result.execution == "unknown" or result.effect == "unknown" else "completed" if result.execution == "succeeded" else "waiting_result" if result.execution in {"pending", "running"} else "failed"
            if state=="completed" and prepared:
                state="waiting_expression"
                await self.persist_contact(connection,prepared)
                await connection.execute("UPDATE life.commitments SET last_expression_id=%s WHERE id=%s",("contact:"+prepared.candidate.candidate_id+":1",row["id"]))
            elif state=="completed" and capability_id=="images.generate" and (result.result.get("sharing") or {}).get("decision")=="archive" and (result.result.get("photo_intent") or {}).get("delivery_kind")=="requested_delivery":
                state="failed"
            if state == row["state"] and row["last_receipt_id"] == result.receipt_id:
                return True
            await connection.execute("UPDATE life.commitments SET state=%s,last_receipt_id=%s,reason=%s,revision=revision+1,updated_at=now() WHERE id=%s", (state, result.receipt_id, result.error_code, row["id"]))
            if state == "completed":
                next_due = future_occurrence(row, datetime.now(UTC))
                if next_due:
                    await self._recur(connection, row, next_due)
            if result.execution=="succeeded" and result.effect!="unknown":
                refs = await self._receipt_sources(context, result, result_revision, observed_at, connection, source_refs(row["source_refs"]))
                await self.life.memory.observe_episode(context, {"episode_id": "action:"+result.action_id, "event_id": "action:"+result.action_id, "revision": result_revision,
                    "origin_kind": "tool_result", "actor_id": context.actor_id, "subject_id": context.actor_id,
                    "occurred_at": observed_at, "summary": row["title"], "source_refs": refs,
                    "matter_id": row["matter_id"], "action_id": result.action_id, "receipt_id": result.receipt_id,
                    "coverage": result.coverage, "actual_result": {**result.result,"receipt_id":result.receipt_id,"capability_id":capability_id,"execution":result.execution,"effect":result.effect,"delivery":result.delivery}}, connection=connection)
        return True

    async def _recur(self, connection, row, due):
        next_revision = row["revision"] + 2
        await connection.execute("UPDATE life.commitments SET state='scheduled',due_at=%s,revision=%s,reason=NULL,action_id=NULL,frozen_action=NULL,last_expression_id=NULL,last_receipt_id=NULL,last_result_revision=-1 WHERE id=%s", (due, next_revision, row["id"]))
        await self.life.jobs.enqueue(connection, JobSpec("life.commitment_due", f"commitment:{row['id']}:{next_revision}",
            {"commitment_id": row["id"], "expected_revision": next_revision}, ServiceClass.DUE, due, row["actor_id"], row["matter_id"]))

    async def contact_due(self, candidate_id, expected_revision):
        async with self.life.database.transaction(read_only=True) as connection:
            candidate = await (await connection.execute("SELECT *,now() AS review_time FROM life.contact_candidates WHERE id=%s", (candidate_id,))).fetchone()
        if not candidate or candidate["revision"] != expected_revision or candidate["state"] not in {"pending", "waiting"}:
            return
        from .recovery import scheduled_context
        context = await scheduled_context(self.life,candidate["context"])
        await self.life.identity.check(context, source_refs(candidate["source_refs"]))
        preferences = await self.life.contacts.preferences(context)
        expires = contact_expiry(candidate["expires_at"], preferences,
            created_at=candidate["created_at"], explicit=candidate["explicit"], kind=candidate["kind"])
        candidate = {**candidate, "expires_at": expires}
        decision = self.life.attention.decide(candidate, preferences, await self.life.contacts.history(context), now=candidate["review_time"])
        async with self.life.database.transaction() as connection:
            current = await self.life.lock_row(connection,context,"SELECT * FROM life.contact_candidates WHERE id=%s FOR UPDATE", (candidate_id,))
            if current["revision"] != expected_revision or current["state"] not in {"pending", "waiting"}:
                return
            await connection.execute("UPDATE life.contact_candidates SET expires_at=LEAST(expires_at,%s) WHERE id=%s", (expires, candidate_id))
            if decision.decision == "defer":
                await connection.execute("UPDATE life.contact_candidates SET state='waiting',due_at=%s,reason=%s,revision=revision+1 WHERE id=%s", (decision.due_at, decision.reason, candidate_id))
                await self.life.jobs.enqueue(connection, JobSpec("life.contact_due", f"contact:{candidate_id}:{expected_revision+1}", {"candidate_id": candidate_id, "expected_revision": expected_revision + 1}, ServiceClass.DUE, decision.due_at, context.actor_id))
                return
            if decision.decision in {"archive", "cancel"}:
                await connection.execute("UPDATE life.contact_candidates SET state=%s,reason=%s,revision=revision+1 WHERE id=%s", (decision.decision, decision.reason, candidate_id))
                from .contact_control import expression_id
                await connection.execute("UPDATE life.commitments SET state='expired',reason=%s,revision=revision+1 WHERE last_expression_id=%s AND state='waiting_expression'",(decision.reason,expression_id(candidate)))
                return
            from .contact_control import expression_id
            expression=expression_id(candidate)
            await connection.execute("UPDATE life.contact_candidates SET state='sending',expression_id=%s WHERE id=%s", (expression,candidate_id))
        plan = ExpressionPlan(expression, context.actor_id, decision.target_ref,
            tuple(candidate["payload"].get("public_parts", ({"kind": "text", "text": candidate["summary"]},))),
            source_refs(candidate["source_refs"]), OriginKind.ACTOR,
            artifacts=artifacts(candidate["payload"].get("artifacts", ())), candidate_id=candidate_id)
        output = await self.expression.send(context, plan, service_class=ServiceClass.ELASTIC)
        async with self.life.database.transaction() as connection:
            await connection.execute("UPDATE life.contact_candidates SET state=%s,reason=%s,revision=revision+1 WHERE id=%s AND state='sending' AND expression_id=%s AND delivery_generation=%s", ("delivered" if output.state=="delivered" else "waiting_expression" if output.state in {"waiting_render","ready","waiting_order","delivering","waiting_delivery"} else "unknown" if output.state in {"render_unknown","delivery_unknown"} else "failed", output.reason, candidate_id,plan.expression_id,candidate['delivery_generation']))


    async def expression_result(self,context,expression_id,current):
        state="completed" if current["state"]=="delivered" else "waiting_expression" if current["state"] in {"waiting_render","ready","waiting_order","delivering","waiting_delivery"} else "unknown" if current["state"] in {"render_unknown","delivery_unknown","unknown"} else "failed"
        result=receipt(current["receipt"])
        async with self.life.database.transaction() as connection:
            row=await self.life.lock_row(connection,context,"SELECT * FROM life.commitments WHERE actor_id=%s AND last_expression_id=%s FOR UPDATE",(context.actor_id,expression_id))
            if row and row["state"] in {"dispatching","waiting_expression","unknown"}:
                await connection.execute("UPDATE life.commitments SET state=%s,last_receipt_id=%s,reason=%s,revision=revision+1 WHERE id=%s",(state,result.receipt_id if result else None,current.get("reason"),row["id"]))
                if state=="completed":
                    next_due=future_occurrence(row,datetime.now(UTC))
                    if next_due:
                        await self._recur(connection,row,next_due)
            await connection.execute("UPDATE life.contact_candidates SET state=%s,reason=%s,revision=revision+1 WHERE actor_id=%s AND expression_id=%s AND state IN ('sending','waiting_expression','unknown')",("delivered" if state=="completed" else state,current.get("reason"),context.actor_id,expression_id))


    async def detached_result(self,context,result,action):
        if action["capability_id"]!="images.generate":
            return
        from .photo_contacts import prepare
        prepared=await prepare(self,context,result,source_refs(action["source_refs"]),
            (result.result.get("photo_intent") or {}).get("motivation") or "图片已完成")
        if prepared:
            async with self.life.database.transaction() as connection:
                await self.persist_contact(connection,prepared)
