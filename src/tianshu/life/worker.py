"""Change-driven durable life jobs. External completion always comes from an actual receipt."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid4, uuid5
from zoneinfo import ZoneInfo

from tianshu.models.types import ModelRequest
from tianshu.runtime.errors import AppError
from tianshu.runtime.jobs import JobSpec
from tianshu.runtime.serialization import wire
from tianshu.types import Action, ContextContribution, OriginKind, RequestBudget, ServiceClass
from tianshu.conversation.codec import context_from_record, artifacts, document, receipt, signature, source_refs
from tianshu.conversation.types import ExpressionPlan
from .validation import timestamp
from .salience import evaluate, fingerprint, similarity, interest_fit
from .types import ContactCandidate


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

    async def plan(self, actor_id, revision, context):
        context = context_from_record(context)
        if context.actor_id != actor_id:
            raise AppError("actor_scope_mismatch", status=403)
        await self.life.identity.check(context)
        actor = await self.life.companion.actor(context)
        if not actor["enabled"]:
            return
        async with self.life.database.transaction(read_only=True) as connection:
            mark = await (await connection.execute("SELECT * FROM life.plan_watermarks WHERE actor_id=%s", (actor_id,))).fetchone()
        if not mark or mark["revision"] != revision or mark["planned_watermark"] == mark["change_watermark"]:
            return
        state = await self.life.state(context)
        if state["activity"] and state["activity"]["state"] == "running":
            # A new expression or relation revision never rebuilds the day's active work.
            await self._planned(actor_id, revision, mark["change_watermark"], "current_activity_continues")
            return
        goals = await self.life.goals(context, limit=20)
        commitments = await self.life.commitments(context, limit=20)
        learned = await self.growth.view(context) if self.growth else {"items": []}
        if not any(item["state"] == "active" for item in goals.items) and not any(item["state"] in {"scheduled", "waiting"} for item in commitments.items) and not any(item["state"] == "active" and item["kind"] == "interest" for item in learned["items"]):
            await self._planned(actor_id, revision, mark["change_watermark"], "no_new_goal_or_interest")
            return
        choices = await self.catalog.discover(context)
        persona = await self.life.companion.persona(context, actor["persona_id"], actor["persona_revision"]) if actor["persona_id"] else None
        budget = RequestBudget(datetime.now(UTC) + timedelta(seconds=90))
        refs = tuple({(ref.source_id, ref.revision): ref for ref in (
            *state["affect"]["source_refs"], *(ref for item in (*goals.items, *commitments.items) for ref in source_refs(item["source_refs"])))}.values())
        contribution = ContextContribution("life_plan", json.dumps(wire({
            "goals": goals.items, "commitments": commitments.items,
            "affect_regulation": state["affect"]["regulation"], "capabilities": choices,
            "instruction": "Choose one feasible next activity, or no activity. Preserve explicit commitments. Return JSON {activity:null|{title,kind,goal_id,capability_id,arguments},reason}. Fictional rest is allowed; software actions require a listed available capability. Do not claim work already happened."}), ensure_ascii=False),
            refs, context.scope_ids, 70)
        pack = await self.assembler.assemble(context, "plan", "Choose a meaningful next activity.", budget,
            persona_text=persona.prompt_text if persona else "", contributions=(contribution,) + (await self.growth.material(context) if self.growth else ()))
        result = await self.models.generate(ModelRequest(
            f"life-plan:{actor_id}:{revision}", str(uuid4()), "life.plan", ServiceClass.ELASTIC,
            context, budget, tuple(pack.messages), system=pack.system, tools=tuple(pack.tools),
            estimated_input_tokens=pack.token_usage.estimated_tokens, source_refs=tuple(pack.source_refs), max_output_tokens=1500))
        raw = "".join(part.text or "" for part in result.parts if part.kind == "text" and not part.native.get("thought"))
        try:
            decision = json.loads(raw)
        except (ValueError, TypeError):
            await self._planned(actor_id, revision, mark["change_watermark"], "planner_output_invalid")
            return
        activity = decision.get("activity")
        if not activity:
            await self._planned(actor_id, revision, mark["change_watermark"], "no_meaningful_activity")
            return
        if not isinstance(activity, dict) or not isinstance(activity.get("title"), str):
            raise AppError("invalid_plan")
        capability_id = activity.get("capability_id")
        allowed = {choice["definition"].id for choice in choices if self._can_execute(choice)}
        if capability_id is not None and capability_id not in allowed:
            raise AppError("capability_unavailable", status=409)
        if capability_id is None and activity.get("kind") not in {"rest", "reflection", "room"}:
            raise AppError("capability_required_for_activity")
        await self.life.identity.check(context, tuple(pack.source_refs))
        async with self.life.database.transaction() as connection:
            fresh = await (await connection.execute("SELECT * FROM life.plan_watermarks WHERE actor_id=%s FOR UPDATE", (actor_id,))).fetchone()
            if fresh["revision"] != revision:
                return
            activity_id = str(uuid5(NAMESPACE_URL, f"life:{actor_id}:{revision}"))
            await connection.execute(
                "INSERT INTO life.activities(id,actor_id,goal_id,title,kind,state,next_due_at,checkpoint_data,scope_ids,source_refs) "
                "VALUES (%s,%s,%s,%s,%s,'planned',now(),%s,%s,%s) ON CONFLICT DO NOTHING",
                (activity_id, actor_id, activity.get("goal_id"), activity["title"], activity["kind"],
                 document({"capability_id": capability_id, "arguments": activity.get("arguments", {}), "reason": decision.get("reason", "")}),
                 list(context.scope_ids), document(tuple(pack.source_refs))))
            await self.life.jobs.enqueue(connection, JobSpec("life.activity_step", f"activity:{activity_id}:1",
                {"activity_id": activity_id, "expected_revision": 1, "context": wire(context)}, subject_id=actor_id))
            await connection.execute("UPDATE life.plan_watermarks SET planned_watermark=change_watermark,last_planned_at=now(),reason=%s WHERE actor_id=%s", (decision.get("reason", "planned"), actor_id))

    async def _planned(self, actor_id, revision, watermark, reason):
        async with self.life.database.transaction() as connection:
            await connection.execute("UPDATE life.plan_watermarks SET planned_watermark=%s,last_planned_at=now(),reason=%s WHERE actor_id=%s AND revision=%s", (watermark, reason, actor_id, revision))

    @staticmethod
    def _can_execute(choice):
        return choice.get("can_execute") is True

    async def activity_step(self, activity_id, expected_revision, context):
        context = context_from_record(context)
        await self.life.identity.check(context)
        choices = await self.catalog.discover(context)
        available = {choice["definition"].id for choice in choices if choice["can_execute"]}
        fictional_done = False
        async with self.life.database.transaction() as connection:
            await connection.execute("SELECT actor_id FROM life.actor_state WHERE actor_id=%s FOR UPDATE", (context.actor_id,))
            row = await (await connection.execute("SELECT * FROM life.activities WHERE id=%s AND actor_id=%s FOR UPDATE", (activity_id, context.actor_id))).fetchone()
            if not row or row["revision"] != expected_revision or row["state"] not in {"planned", "running"}:
                return
            other = await (await connection.execute("SELECT id FROM life.activities WHERE actor_id=%s AND state='running' AND id<>%s", (context.actor_id, activity_id))).fetchone()
            if other:
                await connection.execute("UPDATE life.activities SET waiting_reason='another_activity_running' WHERE id=%s", (activity_id,))
                return
            if row["action_id"]:
                return  # Reconcile an existing external attempt, never submit it a second time.
            await self.life.identity.check(context, source_refs(row["source_refs"]))
            data, now = row["checkpoint_data"], datetime.now(UTC)
            capability_id = data.get("capability_id")
            if capability_id is None:
                if row["kind"] not in {"rest", "reflection", "room"}:
                    await connection.execute("UPDATE life.activities SET state='dormant',waiting_reason='capability_required',resume_condition='configure_capability',revision=revision+1 WHERE id=%s", (activity_id,))
                    return
                if row["waiting_reason"] == "fictional_activity" and row["next_due_at"] and row["next_due_at"] <= now:
                    await connection.execute("UPDATE life.activities SET state='completed',waiting_reason=NULL,next_due_at=NULL,checkpoint_step=checkpoint_step+1,revision=revision+1 WHERE id=%s", (activity_id,))
                    await connection.execute("UPDATE life.actor_state SET activity_id=NULL,revision=revision+1 WHERE actor_id=%s AND activity_id=%s", (context.actor_id, activity_id))
                    fictional_done = True
                else:
                    due = now+timedelta(minutes=30)
                    await connection.execute("UPDATE life.activities SET state='running',waiting_reason='fictional_activity',next_due_at=%s WHERE id=%s", (due, activity_id))
                    await connection.execute("UPDATE life.actor_state SET activity_id=%s,revision=revision+1 WHERE actor_id=%s", (activity_id, context.actor_id))
                    await self.life.jobs.enqueue(connection, JobSpec("life.activity_step", f"activity-fiction:{activity_id}:{expected_revision}",
                        {"activity_id": activity_id, "expected_revision": expected_revision, "context": wire(context)}, scheduled_at=due, subject_id=context.actor_id))
                    return
            else:
                if capability_id not in available:
                    await connection.execute("UPDATE life.activities SET state='dormant',waiting_reason='capability_unavailable',resume_condition='capability_available',revision=revision+1 WHERE id=%s", (activity_id,))
                    return
                action_id = str(uuid5(NAMESPACE_URL, f"activity:{activity_id}:{expected_revision}:{row['checkpoint_step']}"))
                deadline = now+timedelta(minutes=30)
                await connection.execute("UPDATE life.activities SET state='running',action_id=%s,last_result_revision=-1,last_receipt_id=NULL,waiting_reason='capability_queued',next_due_at=%s WHERE id=%s", (action_id, deadline, activity_id))
                await connection.execute("UPDATE life.actor_state SET activity_id=%s,revision=revision+1 WHERE actor_id=%s", (activity_id, context.actor_id))
                await self.life.jobs.enqueue(connection, JobSpec("life.activity_reconcile", f"activity-reconcile:{action_id}:0", {"activity_id": activity_id, "context": wire(context), "sequence": 0}, scheduled_at=now+timedelta(seconds=15), subject_id=context.actor_id))
        if fictional_done:
            await self.life.memory.observe_episode(context, {"episode_id": f"fiction:{activity_id}:{expected_revision}", "event_id": f"fiction:{activity_id}:{expected_revision}",
                "origin_kind": "actor", "actor_id": context.actor_id, "occurred_at": datetime.now(UTC), "summary": row["title"],
                "source_refs": source_refs(row["source_refs"]), "actual_result": {"representation": "fictional_world", "software_action": False}})
            if self.growth:
                await self.growth.changed(context, f"fiction:{activity_id}:{expected_revision}")
            return
        action = Action(action_id, f"activity:{activity_id}:{expected_revision}:{row['checkpoint_step']}", capability_id,
            context.actor_id, context, data.get("arguments", {}), OriginKind.ACTOR,
            source_refs(row["source_refs"]), activity_id=activity_id, matter_id=row["matter_id"], deadline_at=deadline)
        result = receipt(await self.actions.invoke(action, background=True, service_class=ServiceClass.ELASTIC))
        await self.capability_result(context, result)

    async def reconcile_activity(self, activity_id, context, sequence):
        context = context_from_record(context)
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
            async with self.life.database.transaction() as connection:
                await connection.execute("UPDATE life.activities SET state='dormant',waiting_reason='outcome_unknown',resume_condition='reconcile_original_action',revision=revision+1 WHERE id=%s AND action_id=%s", (activity_id, row["action_id"]))
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

    async def capability_result(self, context, result, *, result_revision=None, observed_at=None):
        result = receipt(result)
        if result is None or not result.action_id:
            return False
        if result_revision is None:
            current = await self.actions.get(result.action_id, context)
            result, result_revision, observed_at = receipt(current["receipt"]), current["result_revision"], current["result_observed_at"]
        if result is not None:
            activity = await self._activity_result(context, result, result_revision, observed_at)
            commitment = await self._commitment_result(context, result, result_revision, observed_at)
            return activity or commitment
        return False

    async def _activity_result(self, context, result, result_revision, observed_at):
        await self.life.identity.check(context)
        async with self.life.database.transaction() as connection:
            row = await (await connection.execute("SELECT * FROM life.activities WHERE action_id=%s AND actor_id=%s FOR UPDATE", (result.action_id, context.actor_id))).fetchone()
            if not row:
                return False
            if result_revision <= row["last_result_revision"]:
                return True
            refs = tuple({(ref.source_id, ref.revision): ref for ref in (
                *source_refs(row["source_refs"]), *(ref for item in result.artifacts for ref in item.source_refs))}.values())
            await self.life.identity.check(context, refs)
            await connection.execute("UPDATE life.activities SET last_result_revision=%s,last_receipt_id=%s WHERE id=%s", (result_revision, result.receipt_id, row["id"]))
            if result.execution in {"pending", "running"}:
                if row["state"] not in {"completed", "cancelled"}:
                    await connection.execute("UPDATE life.activities SET waiting_reason='capability_running' WHERE id=%s", (row["id"],))
                return True
            uncertain = result.execution == "unknown" or result.effect == "unknown"
            if row["state"] != "cancelled":
                if uncertain:
                    await connection.execute("UPDATE life.activities SET state='dormant',waiting_reason='outcome_unknown',resume_condition='reconcile_original_action',revision=revision+1 WHERE id=%s", (row["id"],))
                elif result.execution == "succeeded":
                    continuing = result.result.get("next_position") is not None and result.result.get("complete") is not True
                    step = 0 if row["checkpoint_data"].get("completed_action_id") == result.action_id else 1
                    await connection.execute("UPDATE life.activities SET state=%s,waiting_reason=%s,resume_condition=%s,checkpoint_step=checkpoint_step+%s,checkpoint_data=checkpoint_data||%s,revision=revision+1 WHERE id=%s", (
                        "dormant" if continuing else "completed", "continuation_available" if continuing else None,
                        "continue_from_checkpoint" if continuing else None, step,
                        document({"completed_action_id": result.action_id, "actual_receipt_id": result.receipt_id,
                                  "next_position": result.result.get("next_position"), "coverage": wire(result.coverage)}), row["id"]))
                else:
                    await connection.execute("UPDATE life.activities SET state='dormant',waiting_reason=%s,resume_condition='review_failure',revision=revision+1 WHERE id=%s", (result.error_code or result.execution, row["id"]))
            await connection.execute("UPDATE life.actor_state SET activity_id=NULL,revision=revision+1 WHERE actor_id=%s AND activity_id=%s", (context.actor_id, row["id"]))
            summary = row["title"] if result.execution == "succeeded" and not uncertain else ("结果未确认：" if uncertain else "操作未完成：")+row["title"]
            await self.life.memory.observe_episode(context, {"episode_id": "action:"+result.action_id,
                "event_id": "action:"+result.action_id, "revision": result_revision,
                "origin_kind": "tool_result", "actor_id": context.actor_id, "subject_id": context.actor_id,
                "occurred_at": observed_at, "summary": summary, "source_refs": refs,
                "matter_id": row["matter_id"], "action_id": result.action_id, "receipt_id": result.receipt_id,
                "coverage": result.coverage, "actual_result": {**result.result, "execution": result.execution, "effect": result.effect,
                    "delivery": result.delivery, "receipt_id": result.receipt_id}}, connection=connection)
            if self.growth:
                await self.growth.changed(context, f"result:{result.action_id}:{result_revision}", connection=connection)
            delivery_kind = row["checkpoint_data"].get("arguments", {}).get("delivery_kind", "create_only")
            if result.execution == "succeeded" and result.artifacts and delivery_kind != "create_only" and context.conversation_id and row["state"] != "cancelled":
                await self.propose_contact(ContactCandidate("action:"+result.action_id, context.actor_id, context.conversation_id,
                    "creation", row["title"], datetime.now(UTC), datetime.now(UTC)+timedelta(hours=4), context,
                    refs, explicit=delivery_kind == "requested_delivery", action_id=result.action_id,
                    delivery_kind=delivery_kind, payload={"artifacts": wire(result.artifacts), "public_parts": [
                        {"kind": "image" if item.media.mime_type.startswith("image/") else "audio" if item.media.mime_type.startswith("audio/") else "file", "media": wire(item.media)} for item in result.artifacts]}), connection=connection)
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

    async def propose_contact(self, candidate: ContactCandidate, *, connection=None):
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
            "social_cost": 1 if preferences["availability"] in {"busy", "sleeping"} else .2, "repetition": repetition}, preferences["responsiveness"])
        payload = {**(candidate.payload or {}), "salience": assessment, "fingerprint": content_fingerprint}
        async def persist(connection):
            added = await (await connection.execute(
                "INSERT INTO life.contact_candidates(id,actor_id,target_ref,person_id,kind,summary,due_at,expires_at,state,explicit,delivery_kind,action_id,context,source_refs,payload) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'pending',%s,%s,%s,%s,%s,%s) ON CONFLICT(id) DO NOTHING RETURNING id",
                (candidate.candidate_id, candidate.actor_id, candidate.target_ref, context.person_id, candidate.kind, candidate.summary,
                 candidate.due_at, candidate.expires_at, candidate.explicit, candidate.delivery_kind, candidate.action_id, document(context), document(candidate.source_refs), document(payload)))).fetchone()
            if added:
                await self.life.jobs.enqueue(connection, JobSpec("life.contact_due", "contact:"+candidate.candidate_id+":1",
                    {"candidate_id": candidate.candidate_id, "expected_revision": 1}, ServiceClass.DUE, candidate.due_at, context.actor_id))

        if connection is not None:
            await persist(connection)
        else:
            async with self.life.database.transaction() as transaction:
                await persist(transaction)

    async def commitment_due(self, commitment_id, expected_revision):
        async with self.life.database.transaction(read_only=True) as connection:
            initial = await (await connection.execute("SELECT * FROM life.commitments WHERE id=%s", (commitment_id,))).fetchone()
        if not initial:
            return
        context = context_from_record(initial["context"])
        await self.life.identity.check(context, source_refs(initial["source_refs"]))
        async with self.life.database.transaction() as connection:
            row = await (await connection.execute("SELECT * FROM life.commitments WHERE id=%s FOR UPDATE", (commitment_id,))).fetchone()
            if not row or row["revision"] != expected_revision or row["state"] not in {"scheduled", "waiting"}:
                return
            now = datetime.now(UTC)
            if row["due_at"] is None or row["due_at"] > now:
                return
            if (now-row["due_at"]).total_seconds() > row["grace_seconds"]:
                await connection.execute("UPDATE life.commitments SET state='expired',reason='missed_window',revision=revision+1 WHERE id=%s", (commitment_id,))
                next_due = future_occurrence(row, now)
                if next_due:
                    await self._recur(connection, row, next_due)
                return
            if not row["target_ref"] and not row["capability_id"]:
                await connection.execute("UPDATE life.commitments SET state='waiting',reason='destination_required' WHERE id=%s", (commitment_id,))
                return
            action_id = str(uuid5(NAMESPACE_URL, f"commitment:{commitment_id}:{expected_revision}")) if row["capability_id"] else None
            expression_id = None if action_id else f"commitment:{commitment_id}:{expected_revision}"
            await connection.execute("UPDATE life.commitments SET state='dispatching',reason=NULL,action_id=%s,last_result_revision=-1,last_receipt_id=NULL,last_expression_id=%s WHERE id=%s", (action_id, expression_id, commitment_id))
        try:
            if action_id:
                action = Action(action_id, f"commitment:{commitment_id}:{expected_revision}", row["capability_id"], context.actor_id,
                    context, row["arguments"], OriginKind.TIMER, source_refs(row["source_refs"]), matter_id=row["matter_id"],
                    deadline_at=row["due_at"]+timedelta(seconds=row["grace_seconds"]))
                result = receipt(await self.actions.invoke(action, background=True, service_class=ServiceClass.DUE))
                await self.capability_result(context, result)
                return
            # A promise at an explicit time outranks the default unsolicited quiet period.
            plan = ExpressionPlan(expression_id, context.actor_id, row["target_ref"], ({"kind": "text", "text": row["title"]},), source_refs(row["source_refs"]), OriginKind.TIMER)
            result = await self.expression.send(context, plan)
            state = "completed" if result.delivery == "delivered" else "unknown" if result.delivery in {"unknown", "pending"} else "failed"
            reason = result.error_code
        except BaseException as error:
            async with self.life.database.transaction() as connection:
                await connection.execute("UPDATE life.commitments SET state='unknown',reason=%s,revision=revision+1 WHERE id=%s AND state='dispatching'", (getattr(error, "code", "commitment_outcome_unknown"), commitment_id))
            raise
        async with self.life.database.transaction() as connection:
            fresh = await (await connection.execute("SELECT * FROM life.commitments WHERE id=%s FOR UPDATE", (commitment_id,))).fetchone()
            if fresh["revision"] != expected_revision or fresh["state"] != "dispatching":
                return
            await connection.execute("UPDATE life.commitments SET state=%s,last_receipt_id=%s,reason=%s,revision=revision+1,updated_at=now() WHERE id=%s", (state, result.receipt_id, reason, commitment_id))
            if state == "completed":
                next_due = future_occurrence(fresh, datetime.now(UTC))
                if next_due:
                    await self._recur(connection, fresh, next_due)

    async def _commitment_result(self, context, result, result_revision, observed_at):
        await self.life.identity.check(context)
        async with self.life.database.transaction() as connection:
            row = await (await connection.execute("SELECT * FROM life.commitments WHERE action_id=%s AND actor_id=%s FOR UPDATE", (result.action_id, context.actor_id))).fetchone()
            if not row:
                return False
            if row["state"] in {"completed", "expired", "cancelled", "scheduled"}:
                return True
            if result_revision <= row["last_result_revision"]:
                return True
            await connection.execute("UPDATE life.commitments SET last_result_revision=%s WHERE id=%s", (result_revision, row["id"]))
            state = "completed" if result.execution == "succeeded" else "unknown" if result.execution == "unknown" or result.effect == "unknown" else "waiting_result" if result.execution in {"pending", "running"} else "failed"
            if state == row["state"] and row["last_receipt_id"] == result.receipt_id:
                return True
            await connection.execute("UPDATE life.commitments SET state=%s,last_receipt_id=%s,reason=%s,revision=revision+1,updated_at=now() WHERE id=%s", (state, result.receipt_id, result.error_code, row["id"]))
            if state == "completed":
                next_due = future_occurrence(row, datetime.now(UTC))
                if next_due:
                    await self._recur(connection, row, next_due)
            if state == "completed":
                await self.life.memory.observe_episode(context, {"episode_id": "action:"+result.action_id, "event_id": "action:"+result.action_id, "revision": result_revision,
                    "origin_kind": "tool_result", "actor_id": context.actor_id, "subject_id": context.actor_id,
                    "occurred_at": observed_at, "summary": row["title"], "source_refs": source_refs(row["source_refs"]),
                    "matter_id": row["matter_id"], "action_id": result.action_id, "receipt_id": result.receipt_id,
                    "coverage": result.coverage, "actual_result": result.result}, connection=connection)
        return True

    async def _recur(self, connection, row, due):
        next_revision = row["revision"] + 2
        await connection.execute("UPDATE life.commitments SET state='scheduled',due_at=%s,revision=%s,reason=NULL WHERE id=%s", (due, next_revision, row["id"]))
        await self.life.jobs.enqueue(connection, JobSpec("life.commitment_due", f"commitment:{row['id']}:{next_revision}",
            {"commitment_id": row["id"], "expected_revision": next_revision}, ServiceClass.DUE, due, row["actor_id"], row["matter_id"]))

    async def contact_due(self, candidate_id, expected_revision):
        async with self.life.database.transaction(read_only=True) as connection:
            candidate = await (await connection.execute("SELECT * FROM life.contact_candidates WHERE id=%s", (candidate_id,))).fetchone()
        if not candidate or candidate["revision"] != expected_revision or candidate["state"] not in {"pending", "waiting"}:
            return
        context = context_from_record(candidate["context"])
        await self.life.identity.check(context, source_refs(candidate["source_refs"]))
        decision = self.life.attention.decide(candidate, await self.life.contacts.preferences(context), await self.life.contacts.history(context))
        async with self.life.database.transaction() as connection:
            current = await (await connection.execute("SELECT * FROM life.contact_candidates WHERE id=%s FOR UPDATE", (candidate_id,))).fetchone()
            if current["revision"] != expected_revision or current["state"] not in {"pending", "waiting"}:
                return
            if decision.decision == "defer":
                await connection.execute("UPDATE life.contact_candidates SET state='waiting',due_at=%s,reason=%s,revision=revision+1 WHERE id=%s", (decision.due_at, decision.reason, candidate_id))
                await self.life.jobs.enqueue(connection, JobSpec("life.contact_due", f"contact:{candidate_id}:{expected_revision+1}", {"candidate_id": candidate_id, "expected_revision": expected_revision + 1}, ServiceClass.DUE, decision.due_at, context.actor_id))
                return
            if decision.decision in {"archive", "cancel"}:
                await connection.execute("UPDATE life.contact_candidates SET state=%s,reason=%s,revision=revision+1 WHERE id=%s", (decision.decision, decision.reason, candidate_id))
                return
            await connection.execute("UPDATE life.contact_candidates SET state='sending' WHERE id=%s", (candidate_id,))
        plan = ExpressionPlan(f"contact:{candidate_id}", context.actor_id, decision.target_ref,
            tuple(candidate["payload"].get("public_parts", ({"kind": "text", "text": candidate["summary"]},))),
            source_refs(candidate["source_refs"]), OriginKind.ACTOR,
            artifacts=artifacts(candidate["payload"].get("artifacts", ())), candidate_id=candidate_id)
        result = await self.expression.send(context, plan)
        async with self.life.database.transaction() as connection:
            await connection.execute("UPDATE life.contact_candidates SET state=%s,expression_id=%s,reason=%s,revision=revision+1 WHERE id=%s AND state='sending'", ("delivered" if result.delivery == "delivered" else "unknown" if result.delivery == "unknown" else "failed", plan.expression_id, result.error_code, candidate_id))
