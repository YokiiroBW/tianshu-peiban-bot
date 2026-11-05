"""Change-driven durable life jobs. External completion always comes from an actual receipt."""
from __future__ import annotations
from tianshu.sources.dependencies import unique_refs

from datetime import UTC, datetime, timedelta
import json
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

from tianshu.runtime.errors import AppError
from tianshu.runtime.jobs import JobSpec, current_execution_generation
from tianshu.runtime.serialization import wire
from tianshu.types import Action, OriginKind, ServiceClass
from tianshu.conversation.codec import context_from_record, artifacts, document, receipt, source_refs
from tianshu.conversation.types import ExpressionPlan, InputPart
from tianshu.conversation.generation import GenerationRequests
from tianshu.capabilities.actions import result_sources
from .attention import contact_expiry
from .salience import evaluate, fingerprint, similarity, interest_fit
from .imagined import record_completed_imagined
from .continuation import (READ_CAPABILITIES, action_resolved, automatic_step, completed_target, discovery_progress, execution_window, failed_read_checkpoint,
    failed_target, known_read_failure, options, recoverable_action, remaining_actions, resumed_checkpoint, submitted_checkpoint, unread_coverage)
from .types import ContactCandidate, PreparedContact
from .eligibility import background_work, evaluate as activity_eligibility
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


def capability_recovery_due(result, activity):
    """A saved business deadline remains a bound on the original Action."""
    now = datetime.now(UTC)
    raw = result.result.get("retry_at")
    try:
        due = datetime.fromisoformat(raw) if isinstance(raw, str) else raw
        if due is not None and (not isinstance(due, datetime) or due.utcoffset() is None):
            due = None
    except ValueError:
        due = None
    due = max(now, due) if due else now + timedelta(seconds=5)
    raw_deadline = (activity.get("checkpoint_data", {}).get("frozen_action") or {}).get("deadline_at")
    try:
        deadline = datetime.fromisoformat(raw_deadline) if isinstance(raw_deadline, str) else raw_deadline
    except ValueError:
        return None
    if deadline is not None and (not isinstance(deadline, datetime) or deadline.utcoffset() is None or due >= deadline):
        return None
    return due


class LifeWorker:
    def __init__(self, life, models, assembler, catalog, actions, expression, growth=None):
        self.life, self.models, self.assembler, self.catalog = life, models, assembler, catalog
        self.actions, self.expression, self.growth = actions, expression, growth
        self.generations = GenerationRequests(life.database, life.sources, models)
        self.writing=None
        self.recovery_guard_on=None

    def operations_recovery(self, guard_on):
        from .operations_recovery import LifeOperationsRecovery
        self.recovery_guard_on = guard_on
        return LifeOperationsRecovery(self, guard_on).registration()

    def bind_writing(self,writing):
        self.writing=writing

    async def plan(self, actor_id, revision, context, data_generation):
        from .planning_engine import PlanningEngine
        from tianshu.runtime.recovery_policy import RecoveryWait, recovery_wait_retryable
        execution_generation = current_execution_generation()
        if execution_generation is not None and execution_generation != data_generation:
            raise AppError("job_generation_expired", status=409)
        engine = PlanningEngine(self, data_generation=data_generation)
        try:
            return await engine.run(actor_id, revision, context)
        except RecoveryWait as wait:
            if wait.code == "recovery_claim_lost" or not recovery_wait_retryable(wait):
                raise
            return await engine.defer_recovery(actor_id, wait)
        except AppError as error:
            if error.code != "planner_preparation_changed":
                raise
            return await engine.defer_material_change(actor_id)

    async def recovery_candidates(self, limit=32):
        from .operations_recovery import LifeOperationsRecovery
        return await LifeOperationsRecovery(self, self.recovery_guard_on).candidates(limit=limit)

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

    async def statistics_replay(self, generation_key, sequence):
        from .statistics import replay
        return await replay(self, generation_key, sequence)

    async def statistics_backfill(self, cursor=None):
        from .statistics import scan
        return await scan(self, cursor)

    async def _stop_continuation(self, connection, context, row, reason):
        if row["checkpoint_data"].get("continuation_stop_reason") == reason and row["next_due_at"] is None:
            return
        await connection.execute("UPDATE life.activities SET next_due_at=NULL,checkpoint_data=checkpoint_data||%s,revision=revision+1 WHERE id=%s",
            (document({"continuation_stop_reason": reason}), row["id"]))
        await self.life.days.result(connection, row["id"], continuing=True)
        await self.life.signal(context, f"activity-boundary:{row['id']}:{row['revision']}:{reason}", connection=connection)

    async def _queue_continuation(self, connection, context, row, now):
        step, reason = automatic_step(row, now)
        background = background_work(row["checkpoint_data"])
        due = now if background else max(now, row["protected_until"] or now)
        if step and not background and due >= datetime.fromisoformat(row["checkpoint_data"]["execution_window"]["ends_at"]):
            step, reason = None, "activity_window_ended"
        if not step:
            await self._stop_continuation(connection, context, row, reason)
            return
        await connection.execute("UPDATE life.activities SET next_due_at=%s WHERE id=%s", (due, row["id"]))
        await connection.execute("UPDATE life.day_slots SET state='running' WHERE activity_id=%s AND state='partial' AND starts_at<=%s AND ends_at>%s",
            (row["id"], now, now))
        await self.life.jobs.enqueue(connection, JobSpec("life.activity_step", f"activity-continuation:{row['id']}:{row['revision']}",
            {"activity_id": row["id"], "expected_revision": row["revision"], "context": wire(context)},
            scheduled_at=due, subject_id=context.actor_id))

    async def _close_read_failure(self, connection, row, result, result_revision):
        data = failed_read_checkpoint(row, result, result_revision, row["checkpoint_data"]["capability_id"])
        continuing = bool(options(data) or data.get("continuation"))
        await connection.execute("UPDATE life.activities SET state='dormant',waiting_reason=%s,resume_condition=%s,next_due_at=NULL,"
            "checkpoint_data=%s,revision=revision+1 WHERE id=%s",
            ("continuation_available" if continuing else "read_candidates_exhausted",
             "continue_from_checkpoint" if continuing else "new_reading_material", document(data), row["id"]))
        if continuing:
            now = datetime.now(UTC)
            await connection.execute("UPDATE life.day_slots SET state='partial',revision=revision+1 WHERE activity_id=%s "
                "AND state='failed' AND starts_at<=%s AND ends_at>%s", (row["id"], now, now))
        return continuing

    async def _record_read_skip(self, connection, context, row, result, result_revision, refs):
        """Observe today's control decision without rewriting the old failed Episode."""
        now = datetime.now(UTC)
        event = f"activity-read-skip:{result.action_id}:{result_revision}"
        coverage = unread_coverage(result, row["checkpoint_data"])
        observation = {"derived_kind": "activity_read_skip", "activity_id": row["id"],
            "action_id": result.action_id, "receipt_id": result.receipt_id,
            "capability_id": row["checkpoint_data"]["capability_id"], "execution": result.execution,
            "effect": result.effect, "delivery": result.delivery, "error_code": result.error_code,
            "read_basis": "unread_candidate", "content_key": row["checkpoint_data"].get("arguments", {}).get("content_key"),
            "missing": list(coverage.missing), "decision": "skip_failed_candidate_without_retry"}
        original = await self.life.sources.record_event(context, producer="life:continuation", event_id=event,
            revision=1, occurred_at=now, origin_kind=OriginKind.ACTOR,
            parts=(InputPart("text", json.dumps(observation, ensure_ascii=False), metadata={
                "derived_kind": "activity_read_skip", "action_id": result.action_id}),), support_refs=refs, connection=connection)
        refs = unique_refs(refs, original)
        await self.life.memory.observe_episode(context, {"episode_id": event, "event_id": event, "revision": 1,
            "origin_kind": "actor", "actor_id": context.actor_id, "occurred_at": now,
            "summary": "原候选读取失败且正文未读，保留缺项并跳过该候选："+row["title"],
            "source_refs": refs, "coverage": coverage, "actual_result": observation,
            "action_id": result.action_id, "receipt_id": result.receipt_id, "matter_id": row["matter_id"]}, connection=connection)
        await connection.execute("UPDATE life.activities SET source_refs=%s WHERE id=%s", (document(refs), row["id"]))
        await connection.execute("UPDATE life.day_slots SET source_refs=%s WHERE activity_id=%s", (document(refs), row["id"]))

    async def activity_step(self, activity_id, expected_revision, context):
        from .recovery import scheduled_context
        context = await scheduled_context(self.life, context)
        await self.life.identity.check(context)
        # Resolve the next step first, then probe only that capability outside
        # the transaction. The revision fence below rejects a changed checkpoint.
        async with self.life.database.transaction(read_only=True) as connection:
            snapshot = await (await connection.execute(
                "SELECT * FROM life.activities WHERE id=%s AND actor_id=%s", (activity_id, context.actor_id))).fetchone()
        if not snapshot or snapshot["revision"] != expected_revision:
            return
        next_step = None
        if snapshot["state"] == "dormant" and snapshot["waiting_reason"] == "continuation_available":
            next_step, _ = automatic_step(snapshot, datetime.now(UTC))
        capability = (next_step or snapshot["checkpoint_data"]).get("capability_id")
        choices, available = [], set()
        if capability:
            try:
                # Actions can prepare a dynamically installed handler without
                # dispatching it; use the identity the actual next step owns.
                next_revision = expected_revision + int(next_step is not None)
                probe_id = str(uuid5(NAMESPACE_URL, f"activity:{activity_id}:{next_revision}:{snapshot['checkpoint_step']}"))
                step_data = next_step or snapshot["checkpoint_data"]
                probe = Action(probe_id, f"activity:{activity_id}:{next_revision}:{snapshot['checkpoint_step']}",
                    capability, context.actor_id, context, step_data.get("arguments", {}), OriginKind.ACTOR,
                    source_refs(snapshot["source_refs"]), activity_id=activity_id, matter_id=snapshot["matter_id"])
                handler = await self.actions.handler(probe) if hasattr(self.actions, "handler") else self.catalog.handler(capability)
                definition = handler.definition
                actor_visible = getattr(handler, "visible_actor_id", context.actor_id) == context.actor_id
                if actor_visible and ("*" in context.permissions or definition.permissions <= context.permissions):
                    availability = await self.catalog.availability(context, handler)
                    choices = [{"definition": definition, "availability": availability, "can_execute": availability.can_execute}]
                    if availability.can_execute:
                        available.add(capability)
            except AppError:
                pass
        fictional_done = False
        async with self.life.database.transaction() as connection:
            row = await self.life.lock_row(connection,context,"SELECT * FROM life.activities WHERE id=%s AND actor_id=%s FOR UPDATE", (activity_id, context.actor_id))
            await connection.execute("SELECT actor_id FROM life.actor_state WHERE actor_id=%s FOR UPDATE", (context.actor_id,))
            if not row or row["revision"] != expected_revision:
                return
            continuing = row["state"] == "dormant" and row["waiting_reason"] == "continuation_available"
            if row["state"] not in {"planned", "running"} and not continuing:
                return
            now = datetime.now(UTC)
            if continuing:
                step, reason = automatic_step(row, now)
                if not step:
                    await self._stop_continuation(connection, context, row, reason)
                    return
            goal = None
            if ((row['state']=='planned' and not row['action_id']) or continuing) and row['goal_id']:
                goal=await (await connection.execute('SELECT state FROM life.goals WHERE id=%s AND actor_id=%s',(row['goal_id'],context.actor_id))).fetchone()
                if not goal or not activity_eligibility(goal=goal).eligible:
                    await connection.execute("UPDATE life.activities SET state='dormant',waiting_reason='goal_not_active',resume_condition='goal_active',revision=revision+1 WHERE id=%s",(activity_id,))
                    await self.life.days.result(connection,activity_id,state='cancelled')
                    return
            background = background_work({**snapshot["checkpoint_data"], "capability_id": capability})
            if not background and row["protected_until"] and row["protected_until"] > now:
                if continuing and row["protected_until"] >= datetime.fromisoformat(row["checkpoint_data"]["execution_window"]["ends_at"]):
                    await self._stop_continuation(connection, context, row, "activity_window_ended")
                    return
                if continuing:
                    await connection.execute("UPDATE life.activities SET next_due_at=%s WHERE id=%s", (row["protected_until"], activity_id))
                await self.life.jobs.enqueue(connection, JobSpec("life.activity_step", f"activity-protected:{activity_id}:{expected_revision}:{row['protected_until'].isoformat()}",
                    {"activity_id":activity_id,"expected_revision":expected_revision,"context":wire(context)}, scheduled_at=row["protected_until"], subject_id=context.actor_id))
                return
            if (row["state"] == "planned" or continuing) and row["next_due_at"] and row["next_due_at"] > now:
                return
            if not background and (row["state"] == "planned" or continuing):
                from .focus import conflict
                occupied = await conflict(connection, context.actor_id, activity_id, now)
                if occupied:
                    due = occupied["until"]
                    window = row["checkpoint_data"].get("execution_window") or {}
                    if window.get("ends_at") and due >= datetime.fromisoformat(window["ends_at"]):
                        if continuing:
                            await self._stop_continuation(connection, context, row, "day_plan_boundary")
                        else:
                            await connection.execute("UPDATE life.activities SET state='dormant',waiting_reason='day_finished',"
                                "resume_condition='next_day_plan',next_due_at=NULL,revision=revision+1 WHERE id=%s", (activity_id,))
                            await self.life.days.result(connection, activity_id, state="carried")
                            await self.life.signal(context, f"activity-boundary:{activity_id}:{expected_revision}", connection=connection)
                        return
                    await connection.execute("UPDATE life.activities SET next_due_at=%s WHERE id=%s", (due, activity_id))
                    await self.life.jobs.enqueue(connection, JobSpec("life.activity_step",
                        f"activity-focus:{activity_id}:{expected_revision}:{occupied['activity_id']}:{occupied['revision']}",
                        {"activity_id": activity_id, "expected_revision": expected_revision, "context": wire(context)},
                        scheduled_at=due, subject_id=context.actor_id))
                    return
            if (row["state"] == "planned" or continuing) and not background:
                from .day_plan import COMMITMENT_MARGIN, awake_window
                routine = await (await connection.execute("SELECT * FROM life.routines WHERE actor_id=%s", (context.actor_id,))).fetchone()
                begins, wake = awake_window(routine, now)
                if continuing and (begins is None or (routine and not routine["enabled"])):
                    await self._stop_continuation(connection, context, row, "actor_resting" if begins is None else "routine_disabled")
                    return
                if begins is None or (routine and not routine["enabled"]) or (row["next_due_at"] and row["next_due_at"].astimezone(ZoneInfo(routine["timezone"] if routine else "Asia/Shanghai")).date() < now.astimezone(ZoneInfo(routine["timezone"] if routine else "Asia/Shanghai")).date()):
                    await connection.execute("UPDATE life.activities SET state='dormant',waiting_reason='day_finished',resume_condition='next_day_plan',next_due_at=NULL,revision=revision+1 WHERE id=%s", (activity_id,))
                    await self.life.days.result(connection, activity_id, state="carried")
                    return
                if not continuing:
                    promise = await (await connection.execute("SELECT due_at FROM life.commitments WHERE actor_id=%s "
                        "AND state IN ('scheduled','waiting') AND due_at>=%s AND due_at<=%s ORDER BY due_at LIMIT 1",
                        (context.actor_id, now-COMMITMENT_MARGIN, now+COMMITMENT_MARGIN))).fetchone()
                    if promise:
                        due = promise["due_at"] + COMMITMENT_MARGIN
                        window = row["checkpoint_data"].get("execution_window") or {}
                        if window.get("ends_at") and due >= datetime.fromisoformat(window["ends_at"]):
                            await connection.execute("UPDATE life.activities SET state='dormant',waiting_reason='day_finished',"
                                "resume_condition='next_day_plan',next_due_at=NULL,revision=revision+1 WHERE id=%s", (activity_id,))
                            await self.life.days.result(connection, activity_id, state="carried")
                            await self.life.signal(context, f"activity-promise-boundary:{activity_id}:{expected_revision}", connection=connection)
                        else:
                            await connection.execute("UPDATE life.activities SET next_due_at=%s WHERE id=%s", (due, activity_id))
                            await self.life.jobs.enqueue(connection, JobSpec("life.activity_step",
                                f"activity-promise:{activity_id}:{expected_revision}:{due.isoformat()}",
                                {"activity_id": activity_id, "expected_revision": expected_revision, "context": wire(context)},
                                scheduled_at=due, subject_id=context.actor_id))
                        return
                if continuing:
                    window = dict(row["checkpoint_data"]["execution_window"])
                    end = min(datetime.fromisoformat(window["ends_at"]), wake)
                    appointments = await (await connection.execute("SELECT s.starts_at,a.checkpoint_data FROM life.day_slots s LEFT JOIN life.activities a ON a.id=s.activity_id "
                        "WHERE s.actor_id=%s AND s.activity_id<>%s AND s.kind='activity' AND s.state IN ('planned','running') AND s.ends_at>%s ORDER BY s.starts_at",
                        (context.actor_id, activity_id, now))).fetchall()
                    appointment = next((item for item in appointments if not background_work(item["checkpoint_data"])), None)
                    if appointment:
                        end = min(end, appointment["starts_at"])
                    promise = await (await connection.execute("SELECT due_at FROM life.commitments WHERE actor_id=%s AND state IN ('scheduled','waiting') "
                        "AND due_at>=%s AND due_at<%s ORDER BY due_at LIMIT 1",
                        (context.actor_id, now-COMMITMENT_MARGIN, end+COMMITMENT_MARGIN))).fetchone()
                    if promise:
                        end = min(end, promise["due_at"]-COMMITMENT_MARGIN)
                    if end <= now:
                        await self._stop_continuation(connection, context, row, "day_plan_boundary")
                        return
                    window["ends_at"] = end.isoformat()
                    row = {**row, "checkpoint_data": {**row["checkpoint_data"], "execution_window": window}}
            if continuing:
                data = resumed_checkpoint(row)
                expected_revision += 1
                await connection.execute("UPDATE life.activities SET state='planned',waiting_reason=NULL,resume_condition=NULL,"
                    "action_id=NULL,last_receipt_id=NULL,last_result_revision=-1,checkpoint_data=%s,revision=%s WHERE id=%s",
                    (document(data), expected_revision, activity_id))
                row = {**row, "state": "planned", "action_id": None, "checkpoint_data": data, "revision": expected_revision}
            if row["action_id"]:
                return  # Reconcile an existing external attempt, never submit it a second time.
            await self.life.validate_persistence(connection, context, source_refs(row["source_refs"]))
            data, now = row["checkpoint_data"], datetime.now(UTC)
            capability_id = data.get("capability_id")
            window = data.get("execution_window")
            imagined_completion = (capability_id is None and row["waiting_reason"] == "fictional_activity"
                and row["next_due_at"] and row["next_due_at"] <= now)
            if window and not background and datetime.fromisoformat(window["ends_at"]) <= now and not imagined_completion:
                await connection.execute("UPDATE life.activities SET state='dormant',waiting_reason='day_finished',resume_condition='next_day_plan',next_due_at=NULL,revision=revision+1 WHERE id=%s", (activity_id,))
                await self.life.days.result(connection, activity_id, state="carried")
                return
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
                    if window:
                        due = min(due, datetime.fromisoformat(window["ends_at"]))
                    else:
                        data = execution_window(data, now, due)
                        await connection.execute("UPDATE life.activities SET checkpoint_data=%s WHERE id=%s", (document(data), activity_id))
                    await connection.execute("UPDATE life.activities SET state='running',waiting_reason='fictional_activity',next_due_at=%s,context=%s WHERE id=%s", (due, document(context), activity_id))
                    await connection.execute("UPDATE life.actor_state SET activity_id=%s,revision=revision+1 WHERE actor_id=%s", (activity_id, context.actor_id))
                    await self.life.jobs.enqueue(connection, JobSpec("life.activity_step", f"activity-fiction:{activity_id}:{expected_revision}",
                        {"activity_id": activity_id, "expected_revision": expected_revision, "context": wire(context)}, scheduled_at=due, subject_id=context.actor_id))
                    return
            else:
                assessment = activity_eligibility(goal=goal, activity=row, available=available)
                if not assessment.eligible or (data.get("autonomous") and capability_id not in {item.id for item in planner.eligible_capabilities(choices)}):
                    await connection.execute("UPDATE life.activities SET state='dormant',waiting_reason='capability_unavailable',resume_condition='capability_available',wake_condition=%s,revision=revision+1 WHERE id=%s", (document({"kind":"capability_available","subject_id":capability_id}),activity_id))
                    return
                actual = choices[0]
                live_capability = {"id": capability_id, "input_schema": actual["definition"].input_schema,
                    "constraints": wire(actual["availability"]).get("constraints", {})}
                if not planner.allowed_arguments(live_capability, data.get("arguments", {})):
                    await connection.execute("UPDATE life.activities SET state='dormant',waiting_reason='capability_configuration_changed',"
                        "resume_condition='replan_current_capability',next_due_at=NULL,revision=revision+1 WHERE id=%s", (activity_id,))
                    await self.life.days.result(connection, activity_id, state="carried")
                    await self.life.signal(context, f"activity-capability-changed:{activity_id}:{expected_revision}", connection=connection)
                    return
                if "execution_window" not in data:
                    data = execution_window(data, now, now+timedelta(minutes=int(data.get("duration_minutes", 30))))
                data = submitted_checkpoint(data)
                deadline = data.get("business_deadline_at") if background else data["execution_window"]["ends_at"]
                if deadline is None and capability_id == "images.generate":
                    # Image delivery carries its accepted explicit creation window.
                    deadline = data["execution_window"]["ends_at"]
                if isinstance(deadline, str):
                    deadline = datetime.fromisoformat(deadline)
                if deadline and deadline <= now:
                    await connection.execute("UPDATE life.activities SET state='dormant',waiting_reason='activity_deadline_expired',resume_condition=NULL,next_due_at=NULL,revision=revision+1 WHERE id=%s", (activity_id,))
                    return
                await connection.execute("UPDATE life.activities SET checkpoint_data=%s WHERE id=%s", (document(data), activity_id))
                action_id = str(uuid5(NAMESPACE_URL, f"activity:{activity_id}:{expected_revision}:{row['checkpoint_step']}"))
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
                if not background:
                    await connection.execute("UPDATE life.actor_state SET activity_id=%s,revision=revision+1 WHERE actor_id=%s", (activity_id, context.actor_id))
                await connection.execute("UPDATE life.day_slots SET state='running' WHERE activity_id=%s AND state='planned'", (activity_id,))
                await self.life.jobs.enqueue(connection, JobSpec("life.activity_reconcile", f"activity-reconcile:{action_id}:0", {"activity_id": activity_id, "context": wire(context), "sequence": 0}, scheduled_at=now+timedelta(seconds=15), subject_id=context.actor_id))
                frozen=Action(action_id,f"activity:{activity_id}:{expected_revision}:{row['checkpoint_step']}",capability_id,context.actor_id,context,data.get("arguments",{}),OriginKind.ACTOR,source_refs(row["source_refs"]),activity_id=activity_id,matter_id=row["matter_id"],deadline_at=deadline)
                await connection.execute("UPDATE life.activities SET checkpoint_data=checkpoint_data||%s,context=%s WHERE id=%s",
                    (document({"frozen_action":wire(frozen)}), document(frozen.context), activity_id))
        if fictional_done:
            return
        result = receipt(await self.actions.invoke(frozen, background=True, service_class=ServiceClass.ELASTIC))
        await self.capability_result(context, result)

    async def reconcile_activity(self, activity_id, context, sequence):
        from .recovery import scheduled_context
        context = await scheduled_context(self.life, context)
        await self.life.identity.check(context)
        async with self.life.database.transaction(read_only=True) as connection:
            row = await (await connection.execute("SELECT * FROM life.activities WHERE id=%s AND actor_id=%s", (activity_id, context.actor_id))).fetchone()
        if not row or not row["action_id"] or row["state"] in {"cancelled", "completed"} or action_resolved(row):
            return
        await self.life.identity.check(context, source_refs(row["source_refs"]))
        try:
            actual = await self.actions.get(row["action_id"], context)
        except AppError as error:
            if error.status != 404:
                raise
            if row["waiting_reason"] == "outcome_unknown":
                async with self.life.database.transaction() as connection:
                    await self.life.jobs.enqueue(connection, JobSpec("life.activity_reconcile",
                        f"activity-reconcile:{row['action_id']}:{sequence+1}",
                        {"activity_id": activity_id, "context": wire(context), "sequence": sequence+1},
                        scheduled_at=datetime.now(UTC)+timedelta(seconds=30), subject_id=context.actor_id))
                return  # Absence is not permission to replay a previously unknown effect.
            from .reconciliation import absent_action
            original=await absent_action(self,row["checkpoint_data"].get("frozen_action"))
            if original:
                await self.capability_result(context,original)
                return
            async with self.life.database.transaction() as connection:
                await connection.execute("UPDATE life.activities SET state='dormant',waiting_reason='outcome_unknown',resume_condition='reconcile_original_action',revision=revision+1,updated_at=now() WHERE id=%s AND action_id=%s", (activity_id, row["action_id"]))
                await connection.execute("UPDATE life.actor_state SET activity_id=NULL,revision=revision+1 WHERE actor_id=%s AND activity_id=%s",(context.actor_id,activity_id))
                await self.life.days.result(connection,activity_id,state='unknown')
            return
        result = receipt(actual["receipt"])
        if (result and row["state"] == "dormant" and row["waiting_reason"] == "capability_recovery"
                and row["last_result_revision"] == actual["result_revision"]
                and row["last_receipt_id"] == result.receipt_id
                and recoverable_action(actual["action"]["capability_id"], result)):
            return await self.resume_capability(context, row, actual, result, sequence)
        if actual["receipt"]:
            await self.capability_result(context, actual["receipt"])
        unknown = result and (result.execution == "unknown" or result.effect == "unknown")
        if unknown or (result and result.execution in {"pending", "running"} and (row["next_due_at"] is None or row["next_due_at"] > datetime.now(UTC))):
            async with self.life.database.transaction() as connection:
                await self.life.jobs.enqueue(connection, JobSpec("life.activity_reconcile", f"activity-reconcile:{row['action_id']}:{sequence+1}", {"activity_id": activity_id, "context": wire(context), "sequence": sequence+1}, scheduled_at=datetime.now(UTC)+timedelta(seconds=15), subject_id=context.actor_id))
        elif not result or result.execution in {"pending", "running"}:
            async with self.life.database.transaction() as connection:
                await connection.execute("UPDATE life.activities SET state='dormant',waiting_reason='outcome_unknown',resume_condition='reconcile_original_action',revision=revision+1,updated_at=now() WHERE id=%s AND action_id=%s", (activity_id, row["action_id"]))
                await connection.execute("UPDATE life.actor_state SET activity_id=NULL,revision=revision+1 WHERE actor_id=%s AND activity_id=%s",(context.actor_id,activity_id))
                await self.life.days.result(connection,activity_id,state='unknown')

    async def defer_capability_recovery(self, context, row, result, result_revision, sequence, when):
        async with self.life.database.transaction() as connection:
            current = await (await connection.execute(
                "UPDATE life.activities SET next_due_at=%s,checkpoint_data=checkpoint_data||%s,revision=revision+1,updated_at=now() "
                "WHERE id=%s AND actor_id=%s AND revision=%s AND state='dormant' "
                "AND action_id=%s AND last_result_revision=%s AND last_receipt_id=%s "
                "AND waiting_reason='capability_recovery' RETURNING id",
                (when,document({"capability_recovery":{"action_id":result.action_id,"receipt_id":result.receipt_id,
                    "result_revision":result_revision,"due_at":when.isoformat()}}),
                 row["id"],context.actor_id,row["revision"],result.action_id,result_revision,result.receipt_id))).fetchone()
            if current:
                await self.life.jobs.enqueue(connection, JobSpec("life.activity_reconcile",
                    f"activity-recovery:{result.action_id}:{result.receipt_id}:{sequence+1}",
                    {"activity_id":row["id"],"context":wire(context),"sequence":sequence+1},
                    scheduled_at=when,subject_id=context.actor_id))

    async def resume_capability(self, context, row, actual, result, sequence):
        """Resume the original Action only under both Life and Action CAS fences."""
        from tianshu.runtime.recovery_policy import RecoveryWait, recovery_wait_retryable
        due = capability_recovery_due(result, row)
        if due is None:
            # The original deadline expired while waiting: resolve the known
            # failed receipt, never issue a fresh read beyond that deadline.
            await self._activity_result(context,result,actual["result_revision"],actual["result_observed_at"],
                actual["action"]["capability_id"],recovery_deadline_expired=True)
            return
        if row["next_due_at"] and row["next_due_at"] > datetime.now(UTC):
            await self.defer_capability_recovery(context,row,result,actual["result_revision"],sequence,row["next_due_at"])
            return

        async def still_current(connection, activity_id, expected_revision):
            activity = await (await connection.execute(
                "SELECT revision,state,action_id,last_result_revision,last_receipt_id,waiting_reason "
                "FROM life.activities WHERE id=%s AND actor_id=%s FOR UPDATE",(activity_id,context.actor_id))).fetchone()
            return bool(activity and activity["revision"] == int(expected_revision) and activity["state"] == "dormant"
                and activity["waiting_reason"] == "capability_recovery" and activity["action_id"] == result.action_id
                and activity["last_result_revision"] == actual["result_revision"] and activity["last_receipt_id"] == result.receipt_id)

        try:
            resumed = receipt(await self.actions.resume(result.action_id,context,
                client_request_id=f"life-recovery:{row['id']}:{result.attempt_id}:{result.receipt_id}:{actual['result_revision']}",
                expected_attempt_id=result.attempt_id,expected_receipt_id=result.receipt_id,
                expected_revision=actual["result_revision"],recovery_guard=(still_current,row["id"],str(row["revision"]))))
        except RecoveryWait as wait:
            if wait.code == "recovery_claim_lost" or not recovery_wait_retryable(wait):
                raise
            raw_deadline = (row["checkpoint_data"].get("frozen_action") or {}).get("deadline_at")
            deadline = datetime.fromisoformat(raw_deadline) if isinstance(raw_deadline,str) else raw_deadline
            when = min(wait.retry_at,deadline) if deadline is not None else wait.retry_at
            await self.defer_capability_recovery(context,row,result,actual["result_revision"],sequence,when)
            return
        except AppError as error:
            if error.code == "recovery_guard_rejected":
                return
            if error.code in {"video_checkpoint_unavailable", "read_checkpoint_unavailable"}:
                async with self.life.database.transaction() as connection:
                    await connection.execute("UPDATE life.activities SET waiting_reason=%s,resume_condition='review_failure',"
                        "next_due_at=NULL,revision=revision+1,updated_at=now() WHERE id=%s AND actor_id=%s AND revision=%s "
                        "AND action_id=%s AND last_result_revision=%s AND last_receipt_id=%s AND state='dormant'",
                        (error.code,row["id"],context.actor_id,row["revision"],result.action_id,actual["result_revision"],result.receipt_id))
                return
            if error.code not in {"attempt_changed","revision_conflict","read_recovery_changed"}:
                raise
            latest = await self.actions.get(result.action_id,context)
            if latest["receipt"]:
                await self.capability_result(context,latest["receipt"])
                await self.queue_action_observation(context,row,latest,sequence)
            return
        if resumed is None or resumed.action_id != result.action_id or not resumed.attempt_id:
            raise AppError("action_receipt_identity_mismatch",status=409)
        latest = await self.actions.get(result.action_id,context)
        latest_receipt = receipt(latest["receipt"])
        if latest_receipt:
            if latest.get("attempt", {}).get("attempt_id") != latest_receipt.attempt_id:
                raise AppError("action_receipt_identity_mismatch",status=409)
            # A faster execution may already have advanced beyond the returned
            # pending receipt; consume the actual head rather than inventing it.
            await self.capability_result(context,latest_receipt)
            await self.queue_action_observation(context,row,latest,sequence)

    async def queue_action_observation(self, context, row, snapshot, sequence):
        current = receipt(snapshot["receipt"])
        if current and current.execution in {"pending","running","unknown"}:
            async with self.life.database.transaction() as connection:
                await self.life.jobs.enqueue(connection, JobSpec("life.activity_reconcile",
                    f"activity-resume:{current.action_id}:{current.attempt_id}:{sequence+1}",
                    {"activity_id":row["id"],"context":wire(context),"sequence":sequence+1},
                    scheduled_at=datetime.now(UTC)+timedelta(seconds=15),subject_id=context.actor_id))

    async def recovery_observe_activity(self, activity_id, expected_revision, context, attempt_id, owner_token):
        """A fenced Operations request observes only the original Action ledger."""
        from .recovery import scheduled_context
        context = await scheduled_context(self.life, context)
        async with self.life.database.transaction(read_only=True) as connection:
            row = await (await connection.execute("SELECT * FROM life.activities WHERE id=%s AND actor_id=%s",
                (activity_id, context.actor_id))).fetchone()
        if (not row or row["revision"] != expected_revision or not row["action_id"]
                or row["state"] in {"paused", "cancelled", "completed"}):
            return
        await self.life.identity.check(context, source_refs(row["source_refs"]))
        try:
            current = await self.actions.get(row["action_id"], context)
        except AppError as error:
            if error.status == 404:
                return
            raise
        if current.get("receipt") is not None:
            await self.capability_result(context, current["receipt"], recovery_fence={"activity_id": activity_id,
                "expected_revision": expected_revision, "attempt_id": attempt_id, "owner_token": owner_token})

    async def capability_result(self, context, result, *, result_revision=None, observed_at=None, recovery_fence=None):
        result = receipt(result)
        if result is None or not result.action_id:
            return False
        current = await self.actions.get(result.action_id, context)
        result, result_revision, observed_at = receipt(current["receipt"]), current["result_revision"], current["result_observed_at"]
        capability_id = current["action"]["capability_id"]
        if result is not None:
            activity = await self._activity_result(context, result, result_revision, observed_at, capability_id, recovery_fence=recovery_fence)
            if recovery_fence is not None:
                return activity
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

    async def _activity_result(self, context, result, result_revision, observed_at, capability_id, *, recovery_fence=None, recovery_deadline_expired=False):
        await self.life.identity.check(context)
        async with self.life.database.transaction(read_only=True) as probe:
            snapshot = await (await probe.execute("SELECT * FROM life.activities WHERE action_id=%s AND actor_id=%s", (result.action_id, context.actor_id))).fetchone()
        prepared = None
        if snapshot and snapshot["state"] != "cancelled":
            from .photo_contacts import prepare
            prepared = await prepare(self, context, result, source_refs(snapshot["source_refs"]), snapshot["title"])
        async with self.life.database.transaction() as connection:
            if recovery_fence is not None:
                if self.recovery_guard_on is None:
                    raise AppError("recovery_guard_unavailable", status=503)
                await self.recovery_guard_on(connection, recovery_fence["attempt_id"], recovery_fence["owner_token"])
            row = await self.life.lock_row(connection,context,"SELECT * FROM life.activities WHERE action_id=%s AND actor_id=%s FOR UPDATE", (result.action_id, context.actor_id), additional_refs=result_sources(result))
            if not row:
                return False
            if recovery_fence is not None:
                if row["revision"] != recovery_fence["expected_revision"] or row["id"] != recovery_fence["activity_id"]:
                    raise AppError("recovery_target_changed", status=409)
            if result_revision <= row["last_result_revision"]:
                if (result_revision == row["last_result_revision"] and row["state"] == "dormant"
                        and (row["resume_condition"] == "review_failure" and row["waiting_reason"] not in {"video_checkpoint_unavailable", "read_checkpoint_unavailable"}
                             or recovery_deadline_expired and row["waiting_reason"] == "capability_recovery") and not action_resolved(row)
                        and known_read_failure(capability_id, result)):
                    refs = unique_refs(source_refs(row["source_refs"]), result_sources(result))
                    await self.life.validate_persistence(connection, context, refs)
                    continuing = await self._close_read_failure(connection, row, result, result_revision)
                    await self._record_read_skip(connection, context, row, result, result_revision, refs)
                    if continuing:
                        fresh = await (await connection.execute("SELECT * FROM life.activities WHERE id=%s", (row["id"],))).fetchone()
                        await self._queue_continuation(connection, context, fresh, datetime.now(UTC))
                    await self.life.signal(context, f"activity-read-skip:{result.action_id}:{result_revision}", connection=connection)
                return True
            refs = unique_refs(source_refs(row["source_refs"]), result_sources(result))
            await self.life.validate_persistence(connection, context, refs)
            await connection.execute("UPDATE life.activities SET last_result_revision=%s,last_receipt_id=%s,updated_at=now() WHERE id=%s", (result_revision, result.receipt_id, row["id"]))
            recoverable = recoverable_action(capability_id,result)
            due = capability_recovery_due(result,row) if recoverable else None
            if recoverable and due is not None and row["state"] == "paused":
                await connection.execute("UPDATE life.activities SET checkpoint_data=checkpoint_data||%s,revision=revision+1 WHERE id=%s",
                    (document({"capability_recovery":{"action_id":result.action_id,"receipt_id":result.receipt_id,
                        "result_revision":result_revision,"due_at":due.isoformat()}}),row["id"]))
                return True
            if recoverable and due is not None and row["state"] not in {"paused","cancelled","completed"}:
                await connection.execute("UPDATE life.activities SET state='dormant',waiting_reason='capability_recovery',"
                    "resume_condition='original_action_recovery',next_due_at=%s,checkpoint_data=checkpoint_data||%s,revision=revision+1 WHERE id=%s",
                    (due,document({"capability_recovery":{"action_id":result.action_id,"receipt_id":result.receipt_id,
                        "result_revision":result_revision,"due_at":due.isoformat()}}),row["id"]))
                await connection.execute("UPDATE life.actor_state SET activity_id=NULL,revision=revision+1 WHERE actor_id=%s AND activity_id=%s",(context.actor_id,row["id"]))
                await self.life.days.result(connection,row["id"],state="waiting_result")
                await self.life.jobs.enqueue(connection, JobSpec("life.activity_reconcile",
                    f"activity-recovery:{result.action_id}:{result.receipt_id}:0",
                    {"activity_id":row["id"],"context":wire(context),"sequence":0},scheduled_at=due,subject_id=context.actor_id))
                return True
            if result.execution in {"pending", "running"}:
                if row["state"] not in {"completed", "cancelled", "paused"}:
                    deadline = (row["checkpoint_data"].get("frozen_action") or {}).get("deadline_at")
                    await connection.execute("UPDATE life.activities SET state='dormant',waiting_reason='capability_running',resume_condition='original_action_result',"
                        "next_due_at=CASE WHEN waiting_reason='capability_recovery' THEN %s ELSE next_due_at END,"
                        "checkpoint_data=CASE WHEN waiting_reason='capability_recovery' THEN checkpoint_data-'capability_recovery' ELSE checkpoint_data END,"
                        "revision=revision+1 WHERE id=%s", (deadline,row["id"]))
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
                    previous_exploration = row["checkpoint_data"].get("exploration") or {}
                    exploration=exploration_checkpoint(capability_id,row["checkpoint_data"].get("arguments",{}),result.result,
                        previous=previous_exploration)
                    if capability_id == "content.discover":
                        exploration = discovery_progress(previous_exploration, exploration, result.result)
                    if (capability_id == "content.read" and result.result.get("read_basis") == "actual_source_body"
                            and not result.result.get("continuation")):
                        exploration.pop("no_progress_signature", None)
                        exploration.pop("no_progress_stop", None)
                        content_key = result.result.get("content_key") or row["checkpoint_data"].get("arguments", {}).get("content_key")
                        if content_key:
                            exploration["completed_content_keys"] = list(dict.fromkeys(
                                [*exploration.get("completed_content_keys", ()), content_key]))
                    exploration["next_actions"] = remaining_actions(exploration)
                    if continuation and (failed_target(continuation, exploration) or completed_target(continuation, exploration)):
                        continuation = None
                    if continuation is None and exploration.get("next_actions"):
                        following=exploration["next_actions"][0]
                        continuation={key:following[key] for key in ("capability_id","arguments")}
                    if continuation is None and not exploration.get('next_actions'):
                        continuation=exploration.get('discovery_continuation')
                    follow_up=not continuation and bool((exploration.get('current_item') or {}).get('follow_up'))
                    continuing=next_position is not None or continuation is not None
                    confirmed = not consumption or consumption.get("analysis_completed") is True
                    step = 0 if row["checkpoint_data"].get("completed_action_id") == result.action_id else 1
                    await connection.execute("UPDATE life.activities SET state=%s,waiting_reason=%s,resume_condition=%s,next_due_at=NULL,checkpoint_step=checkpoint_step+%s,checkpoint_data=(checkpoint_data-'capability_recovery')||%s,revision=revision+1 WHERE id=%s", (
                        "paused" if row["state"] == "paused" else "dormant" if continuing or follow_up or not confirmed else "completed",
                        "paused_by_user" if row["state"] == "paused" else "analysis_unconfirmed" if not confirmed else "continuation_available" if continuing else "follow_up_available" if follow_up else None,
                        "review_original_result" if not confirmed else "continue_from_checkpoint" if continuing else "plan_later_check" if follow_up else None, step,
                        document({"completed_action_id": result.action_id, "actual_receipt_id": result.receipt_id,
                                  "next_position": next_position, "continuation": continuation, "consumption":consumption,
                                  "exploration":exploration,
                                  "last_execution":"succeeded","result_summary":str(result.result.get("summary") or row["title"])[:1200],
                                  "completion_basis":"discovery_metadata" if result.result.get("read_basis")=="discovery_metadata" else "partial_source_read" if continuing else "requested_window_ended" if consumption else "actual_result",
                                  "coverage": wire(result.coverage)}), row["id"]))
                elif known_read_failure(capability_id, result) and row["state"] != "completed":
                    continuing = await self._close_read_failure(connection, row, result, result_revision)
                else:
                    await connection.execute("UPDATE life.activities SET state='dormant',waiting_reason=%s,resume_condition='review_failure',revision=revision+1 WHERE id=%s", (result.error_code or result.execution, row["id"]))
                if row["state"] == "paused" and result.execution != "succeeded":
                    await connection.execute("UPDATE life.activities SET state='paused',waiting_reason='paused_by_user',next_due_at=NULL WHERE id=%s", (row["id"],))
            await connection.execute("UPDATE life.actor_state SET activity_id=NULL,revision=revision+1 WHERE actor_id=%s AND activity_id=%s", (context.actor_id, row["id"]))
            summary = row["title"] if result.execution == "succeeded" and not uncertain else ("结果未确认：" if uncertain else "操作未完成：")+row["title"]
            if capability_id=="writing.request" and result.execution=="succeeded":
                summary="已安排章节创作，正文尚未由此回执确认："+row["title"]
            refs = await self._receipt_sources(context, result, result_revision, observed_at, connection, refs)
            await connection.execute("UPDATE life.activities SET source_refs=%s WHERE id=%s",(document(refs),row["id"]))
            await connection.execute("UPDATE life.day_slots SET source_refs=%s WHERE activity_id=%s",(document(refs),row["id"]))
            await self.life.days.result(connection,row["id"],continuing=not uncertain and continuing and confirmed,
                state="unknown" if uncertain else "analysis_unconfirmed" if not confirmed else "completed" if result.execution=="succeeded" else "failed")
            if result.execution=="succeeded" and not uncertain and confirmed and row["state"]!="cancelled":
                goal_context=context
                if row["checkpoint_data"].get("planning_context"):
                    from .recovery import scheduled_context
                    goal_context=await scheduled_context(self.life,row["checkpoint_data"]["planning_context"],connection=connection)
                # The receipt original was just registered on this connection.
                # A separate reader cannot see it until this transaction commits.
                if await self.life.sources.validate_persistence(connection,goal_context,refs):
                    await self.life.directions.observe_result(connection,goal_context,row,result,result_revision,capability_id,refs)
                if not continuing:
                    await self.life.wake(context,kind="activity_completed",subject_id=row["id"],event_id=result.receipt_id,connection=connection)
            await self.life.memory.observe_episode(context, {"episode_id": "action:"+result.action_id,
                "event_id": "action:"+result.action_id, "revision": result_revision,
                "origin_kind": "tool_result", "actor_id": context.actor_id, "subject_id": context.actor_id,
                "occurred_at": observed_at, "summary": summary, "source_refs": refs,
                "matter_id": row["matter_id"], "action_id": result.action_id, "receipt_id": result.receipt_id,
                "coverage": unread_coverage(result, row["checkpoint_data"]) if known_read_failure(capability_id, result) else result.coverage,
                "actual_result": {**result.result, "execution": result.execution, "effect": result.effect,
                    "delivery": result.delivery, "receipt_id": result.receipt_id, "capability_id": capability_id,
                    "error_code": result.error_code}}, connection=connection)
            if prepared is not None and row["state"] != "cancelled":
                await self.persist_contact(connection, prepared)
            if continuing and confirmed and not uncertain and row["state"] not in {"paused", "cancelled"}:
                fresh = await (await connection.execute("SELECT * FROM life.activities WHERE id=%s", (row["id"],))).fetchone()
                await self._queue_continuation(connection, context, fresh, datetime.now(UTC))
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
        habits = await self.growth.material(context, purpose="planning") if self.growth else ()
        interests = [{"topic": row.text, "strength": .8} for row in habits if getattr(row, "semantic_role", None) == "interest"]
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
            routine = await (await connection.execute("SELECT * FROM life.routines WHERE actor_id=(SELECT actor_id FROM life.contact_candidates WHERE id=%s)", (candidate_id,))).fetchone()
        if not candidate or candidate["revision"] != expected_revision or candidate["state"] not in {"pending", "waiting"}:
            return
        from .recovery import scheduled_context
        context = await scheduled_context(self.life,candidate["context"])
        await self.life.identity.check(context, source_refs(candidate["source_refs"]))
        preferences = await self.life.contacts.preferences(context)
        expires = contact_expiry(candidate["expires_at"], preferences,
            created_at=candidate["created_at"], explicit=candidate["explicit"], kind=candidate["kind"])
        candidate = {**candidate, "expires_at": expires}
        decision = self.life.attention.decide(candidate, preferences, await self.life.contacts.history(context), now=candidate["review_time"], routine=routine)
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
        from .photo_contacts import eligible,prepare
        if action["capability_id"]!="images.generate" or not eligible(result):
            return
        prepared=await prepare(self,context,result,source_refs(action["source_refs"]),
            (result.result.get("photo_intent") or {}).get("motivation") or "图片已完成")
        if prepared:
            async with self.life.database.transaction() as connection:
                await self.persist_contact(connection,prepared)
