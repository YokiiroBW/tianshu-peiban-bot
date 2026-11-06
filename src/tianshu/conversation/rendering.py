"""Expression rendering owns no model or speech engine and never redelivers a result."""
from tianshu.sources.dependencies import unique_refs
from dataclasses import replace
from datetime import UTC,datetime
from uuid import NAMESPACE_URL,uuid4,uuid5
from tianshu.runtime.errors import AppError
from tianshu.runtime.jobs import JobSpec
from tianshu.runtime.serialization import wire
from tianshu.types import Action,OriginKind,ServiceClass
from .codec import artifacts,context_from_record,document,receipt,signature,source_refs
from .types import ExpressionPlan,ExpressionProgress
from .expression import delivery_state, receipt_advances
from .behaviors import behavior_status
from .recovery import can_retry_resource_wait, is_resource_wait, raise_resource_wait
from .persistence import MaterialSnapshotChanged, retry_material_snapshot


def decode_plan(value):
    return ExpressionPlan(value["expression_id"],value["actor_id"],value["target_ref"],tuple(value["public_parts"]),source_refs(value["source_refs"]),OriginKind(value["origin_kind"]),value.get("turn_id"),artifacts(value.get("artifacts",())),value.get("candidate_id"),tuple(value.get("behavior_refs", ())))


def progress(row):
    return ExpressionProgress(row["expression_id"],row["state"],receipt(row["receipt"]),tuple(step["action_id"] for step in row["steps"]),row["reason"])


class ExpressionRenderer:
    def __init__(self,database,jobs,identity,sources,actions,speech,delivery):
        self.database,self.jobs,self.identity,self.sources=database,jobs,identity,sources
        self.actions,self.speech,self.delivery=actions,speech,delivery
        self.contact_preflight = None
        self.behavior_resolver = None

    def bind_behavior_resolver(self, resolver):
        self.behavior_resolver = resolver
        channel_service = getattr(getattr(self.delivery, "channels", None), "service", None)
        if channel_service is not None:
            channel_service.bind_behavior_resolver(resolver)

    def bind_statistics(self, statistics):
        if hasattr(self.delivery, "bind_statistics"):
            self.delivery.bind_statistics(statistics)

    async def behavior_ready(self, context, plan, *, task, payload):
        status, _ = await behavior_status(self.behavior_resolver, context, plan.behavior_refs)
        if status == "invalid":
            await self._failed(context, plan.expression_id, "behavior_invalid")
            return False
        if status == "pending":
            from datetime import timedelta
            from uuid import uuid4
            async with self.database.transaction() as connection:
                if plan.turn_id:
                    await connection.execute("UPDATE conversation.turns SET error_code='behavior_pending' "
                        "WHERE turn_id=%s AND state IN ('running','waiting_expression')", (plan.turn_id,))
                await self.jobs.enqueue(connection, JobSpec(task, f"behavior-wake:{plan.expression_id}:{uuid4()}",
                    payload, ServiceClass.INTERACTIVE, datetime.now(UTC)+timedelta(seconds=10), context.actor_id))
            return False
        if plan.turn_id:
            async with self.database.transaction() as connection:
                await connection.execute("UPDATE conversation.turns SET error_code=NULL WHERE turn_id=%s "
                    "AND error_code IN ('behavior_pending','behavior_preflight_pending')", (plan.turn_id,))
        return True

    def bind_contact_preflight(self, callback):
        self.contact_preflight = callback

    async def hint(self,context):
        profile=await self.speech.expression_profile(context)
        return {"output_mode":profile["output_mode"] if profile else "text","voice_profile_selected":profile is not None,
                "instruction":"text: no automatic speech; audio/both: final public text is rendered with the fixed voice; natural: use the speech tool only when expression purpose calls for it. Existing audio is never synthesized again."}

    async def send(self,context,plan,*,service_class=ServiceClass.INTERACTIVE):
        if not context.delivery_allowed or plan.actor_id!=context.actor_id or plan.target_ref!=context.conversation_id:
            raise AppError("expression_target_mismatch",status=403)
        await self.identity.check(context,plan.source_refs)
        fingerprint=signature(plan)
        async with self.database.transaction(read_only=True) as connection:
            old=await (await connection.execute("SELECT * FROM conversation.expression_plans WHERE expression_id=%s",(plan.expression_id,))).fetchone()
        if old:
            if old["fingerprint"]!=fingerprint or old["actor_id"]!=context.actor_id or old["conversation_id"]!=context.conversation_id:
                raise AppError("expression_identity_conflict",status=409)
            return progress(old)
        profile=await self.speech.expression_profile(context)
        mode=profile["output_mode"] if profile else "text"
        has_audio=any(part["kind"]=="audio" for part in plan.public_parts)
        steps=[]
        if mode in {"audio","both"} and not has_audio:
            for index,part in enumerate(plan.public_parts):
                if part["kind"]=="text" and part.get("text","").strip():
                    steps.append({"index":index,"action_id":str(uuid5(NAMESPACE_URL,f"expression-render:{plan.expression_id}:{index}")),"state":"planned","result_revision":-1,"artifacts":[],"receipt":None})
        async with self.database.transaction() as connection:
            if not await self.sources.validate_persistence(connection,context,plan.source_refs):
                raise AppError("source_unavailable",status=409)
            deadline = None
            if plan.turn_id:
                turn = await (await connection.execute(
                    "SELECT deadline_at FROM conversation.turns WHERE turn_id=%s AND actor_id=%s "
                    "AND conversation_id=%s AND autonomous", (plan.turn_id, context.actor_id, context.conversation_id))).fetchone()
                deadline = turn["deadline_at"] if turn else None
            added=await (await connection.execute("INSERT INTO conversation.expression_plans(expression_id,actor_id,conversation_id,fingerprint,context,plan,source_refs,profile,state,service_class,steps,deadline_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING *",(plan.expression_id,context.actor_id,context.conversation_id,fingerprint,document(context),document(plan),document(plan.source_refs),document(profile),"waiting_render" if steps else "ready",service_class.value,document(steps),deadline))).fetchone()
            if not added:
                existing=await (await connection.execute("SELECT * FROM conversation.expression_plans WHERE expression_id=%s",(plan.expression_id,))).fetchone()
                if existing["fingerprint"]!=fingerprint:
                    raise AppError("expression_identity_conflict",status=409)
                return progress(existing)
            if steps:
                for step in steps:
                    await self.jobs.enqueue(connection,JobSpec("conversation.expression_render",f"expression-render:{step['action_id']}",{"expression_id":plan.expression_id,"index":step["index"]},service_class,subject_id=context.actor_id))
            if deadline is not None:
                await self.jobs.enqueue(connection,JobSpec("conversation.render_expire","render-expire:"+plan.expression_id,{"expression_id":plan.expression_id},ServiceClass.INTERACTIVE,deadline,context.actor_id))
        if not steps:
            return await self._deliver(context,plan)
        return progress(added)

    async def render(self,expression_id,index):
        async with self.database.transaction(read_only=True) as connection:
            row=await (await connection.execute("SELECT * FROM conversation.expression_plans WHERE expression_id=%s",(expression_id,))).fetchone()
        if not row or row["state"] not in {"waiting_render","render_unknown"}:
            return
        context,plan=context_from_record(row["context"]),decode_plan(row["plan"])
        if not await self.behavior_ready(context, plan, task="conversation.expression_render",
                                         payload={"expression_id": expression_id, "index": index}):
            return
        await self.identity.check(context,plan.source_refs)
        async with self.database.transaction() as connection:
            if not await self.sources.validate_persistence(connection,context,plan.source_refs):
                return
            fresh=await (await connection.execute("SELECT * FROM conversation.expression_plans WHERE expression_id=%s FOR UPDATE",(expression_id,))).fetchone()
            if fresh["state"] not in {"waiting_render","render_unknown"}:
                return
            steps=fresh["steps"]
            step=next(item for item in steps if item["index"]==index)
            if step["state"] in {"succeeded","failed","cancelled"}:
                return
            step["state"]="dispatching"
            await connection.execute("UPDATE conversation.expression_plans SET steps=%s WHERE expression_id=%s",(document(steps),expression_id))
        action=Action(step["action_id"],f"expression-render:{expression_id}:{index}","speech.synthesize",context.actor_id,context,
            {"profile_id":row["profile"]["profile_id"],"profile_revision":row["profile"]["revision"],"text":plan.public_parts[index]["text"]},OriginKind.ACTOR,plan.source_refs,expression_id=expression_id,deadline_at=row["deadline_at"])
        try:
            try:
                current=await self.actions.get(action.action_id,context)
            except AppError as error:
                raise_resource_wait(error)
                if error.status!=404:
                    raise
                if row["deadline_at"] is not None and row["deadline_at"]<=datetime.now(UTC):
                    await self._failed(context,expression_id,"expression_window_expired")
                    return
                # No ledger entry exists. This is the first registration of the
                # original action ID, including a crash before invoke.
                await self.actions.invoke(action,background=True,service_class=ServiceClass(row["service_class"]),
                                          behavior_refs=plan.behavior_refs,behavior_purpose="conversation")
                current=await self.actions.get(action.action_id,context)
            if current["receipt"] and current["receipt"]["execution"]=="unknown":
                await self.actions.reconcile(action.action_id,context)
                current=await self.actions.get(action.action_id,context)
            await self.on_action_result(context,receipt(current["receipt"]),current["result_revision"])
        except BaseException as error:
            raise_resource_wait(error)
            # Pre-ledger rejection is distinct from losing the outcome of a submitted call.
            try:
                actual=await self.actions.get(action.action_id,context)
            except BaseException as observation_error:
                raise_resource_wait(observation_error)
                if getattr(observation_error,"status",None)==404:
                    await self._failed(context,expression_id,getattr(error,"code","render_not_started"))
                else:
                    async with self.database.transaction() as connection:
                        changed=await (await connection.execute("UPDATE conversation.expression_plans SET state='render_unknown',reason=%s,revision=revision+1 WHERE expression_id=%s AND state='waiting_render' RETURNING revision",(getattr(error,"code","render_outcome_unknown"),expression_id))).fetchone()
                        if changed:
                            await self._event(connection,context,expression_id,changed["revision"])
            else:
                await self.on_action_result(context,receipt(actual["receipt"]),actual["result_revision"])
            raise

    async def on_action_result(self,context,result,result_revision,*,result_fence=None):
        defer_release = result_fence is not None
        if result_fence is None and result is not None and result.action_id:
            if self.actions is None:
                raise AppError("action_head_unavailable", status=503)
            from .rbi_result_fence import action_result_fence
            result, result_revision, result_fence = await action_result_fence(
                self.database, self.jobs, self.actions, context, result, result_revision=result_revision)
        return await retry_material_snapshot(
            lambda: self._on_action_result_once(context, result, result_revision,
                result_fence=result_fence, defer_release=defer_release))

    async def _on_action_result_once(self,context,result,result_revision,*,result_fence=None,defer_release=False):
        if result is None or not result.action_id:
            return False
        async with self.database.transaction(read_only=True) as connection:
            row=await (await connection.execute("SELECT * FROM conversation.expression_plans WHERE actor_id=%s AND conversation_id=%s AND steps@>%s",(context.actor_id,context.conversation_id,document([{"action_id":result.action_id}])))).fetchone()
        if not row:
            return False
        refs=unique_refs(tuple(ref for ref in (*source_refs(row["source_refs"]),*source_refs(result.result.get("source_refs",())),*(ref for item in result.artifacts for ref in item.source_refs))))
        locked_refs = unique_refs(refs, getattr(result_fence, "source_refs", ()))
        async with self.database.transaction() as connection:
            if not await self.sources.validate_persistence(connection,context,locked_refs):
                if result_fence:
                    raise AppError("source_unavailable", status=403)
                await connection.execute("UPDATE conversation.expression_plans SET state='failed',plan='{}',rendered=NULL,reason='source_unavailable',revision=revision+1 WHERE expression_id=%s",(row["expression_id"],))
                return True
            if result_fence:
                await result_fence(connection)
            fresh=await (await connection.execute("SELECT * FROM conversation.expression_plans WHERE expression_id=%s FOR UPDATE",(row["expression_id"],))).fetchone()
            if result_fence and (not fresh or signature(row["source_refs"]) != signature(fresh["source_refs"])):
                raise MaterialSnapshotChanged(fresh.get("revision") if fresh else None)
            steps=fresh["steps"]
            step=next(item for item in steps if item["action_id"]==result.action_id)
            resume = result_revision<=step["result_revision"] and fresh["state"] == "delivering"
            updated = False
            if result_revision<=step["result_revision"] and not resume:
                return True
            if resume:
                # The render result was committed but delivery preparation may
                # have rolled back. An existing expression owns any send.
                if fresh["continuation_unconfirmed"]:
                    return True
                existing = await (await connection.execute("SELECT state FROM conversation.expressions WHERE expression_id=%s",
                                                            (row["expression_id"],))).fetchone()
                if existing and existing["state"] != "prepared":
                    return True
                state = "delivering"
                refs = source_refs(fresh["source_refs"])
            else:
                updated = True
                step.update(state=result.execution,result_revision=result_revision,artifacts=wire(result.artifacts),receipt=wire(result))
                state="render_unknown" if any(item["state"]=="unknown" for item in steps) else "failed" if any(item["state"] in {"failed","cancelled"} for item in steps) else "delivering" if all(item["state"]=="succeeded" for item in steps) else "waiting_render"
                may_prepare = False
                if fresh["state"] == "delivering":
                    existing = await (await connection.execute(
                        "SELECT state FROM conversation.expressions WHERE expression_id=%s", (row["expression_id"],))).fetchone()
                    may_prepare = not fresh["continuation_unconfirmed"] and (not existing or existing["state"] == "prepared")
                    if not may_prepare:
                        state = fresh["state"]
                elif fresh["state"] in {"waiting_delivery","delivered","delivery_unknown","cancelled","failed"}:
                    state=fresh["state"]
                await connection.execute("UPDATE conversation.expression_plans SET steps=%s,state=%s,source_refs=%s,reason=%s,revision=revision+1 WHERE expression_id=%s",(document(steps),state,document(refs),result.error_code,row["expression_id"]))
                if state in {"failed","render_unknown"}:
                    await self._event(connection,context,row["expression_id"],fresh["revision"]+1)
                if state == "delivering" and fresh["state"] == "delivering" and may_prepare:
                    resume = True
        if state=="delivering" and (resume or fresh["state"] not in {"delivering","waiting_delivery","delivered","delivery_unknown","cancelled","failed"}):
            expected_revision = fresh["revision"] + int(updated)
            if fresh["deadline_at"] is not None and datetime.now(UTC)>fresh["deadline_at"]:
                await self._failed(context,fresh["expression_id"],"expression_window_expired",
                                   result_fence=result_fence, refs=refs, expected_revision=expected_revision)
                return True
            plan=decode_plan(fresh["plan"])
            parts=[]
            output=list(plan.artifacts)
            by_index={item["index"]:item for item in steps}
            for index,part in enumerate(plan.public_parts):
                step=by_index.get(index)
                if step is None:
                    parts.append(part)
                    continue
                actual=artifacts(step["artifacts"])
                if not actual:
                    await self._failed(context,plan.expression_id,"speech_artifact_missing",
                                       result_fence=result_fence, refs=refs, expected_revision=expected_revision)
                    return True
                if fresh["profile"]["output_mode"]=="both":
                    parts.append(part)
                parts.extend({"kind":"audio","media":wire(item.media)} for item in actual)
                output.extend(actual)
            try:
                rendered_plan = replace(plan,public_parts=tuple(parts),source_refs=refs,artifacts=tuple(output))
                if result_fence:
                    await self._deliver(context, rendered_plan, result_fence=result_fence,
                                        expected_revision=expected_revision, defer_release=defer_release)
                else:
                    await self._deliver(context, rendered_plan)
            except Exception as error:
                if is_resource_wait(error) and not can_retry_resource_wait(error):
                    try:
                        async with self.database.transaction() as connection:
                            if result_fence:
                                if not await self.sources.validate_persistence(connection, context,
                                        unique_refs(refs, getattr(result_fence, "source_refs", ()))):
                                    raise AppError("source_unavailable", status=403)
                                await result_fence(connection)
                            await connection.execute("UPDATE conversation.expression_plans SET continuation_unconfirmed=true "
                                "WHERE expression_id=%s AND state='delivering' "
                                "AND (%s::bigint IS NULL OR revision=%s)",
                                (plan.expression_id, expected_revision if result_fence else None,
                                 expected_revision if result_fence else None))
                    except BaseException as observation_error:
                        raise error from observation_error
                raise
        return True

    async def _deliver(self,context,plan,*,result_fence=None,expected_revision=None,defer_release=False):
        async with self.database.transaction() as connection:
            refs = unique_refs(plan.source_refs, getattr(result_fence, "source_refs", ()))
            if not await self.sources.validate_persistence(connection,context,refs):
                raise AppError("source_unavailable",status=409)
            if result_fence:
                await result_fence(connection)
                current = await (await connection.execute(
                    "SELECT state,revision,source_refs FROM conversation.expression_plans "
                    "WHERE expression_id=%s FOR UPDATE", (plan.expression_id,))).fetchone()
                if not current or current["state"] != "delivering" or current["revision"] != expected_revision:
                    return None
                if signature(source_refs(current["source_refs"])) != signature(plan.source_refs):
                    raise MaterialSnapshotChanged(current["revision"])
            await connection.execute("UPDATE conversation.expression_plans SET rendered=%s,state='ready' WHERE expression_id=%s",(document(plan),plan.expression_id))
            if result_fence and not defer_release:
                await self.jobs.enqueue(connection, JobSpec("conversation.expression_release",
                    f"render-ready:{plan.expression_id}:{expected_revision}:{uuid4()}", {"expression_id": plan.expression_id},
                    ServiceClass.INTERACTIVE, subject_id=context.actor_id),
                    queueing_lock=f"expression-release:{plan.expression_id}")
        if defer_release:
            # ResultConsumer persists the release job together with its proof.
            return None
        return await self.release(plan.expression_id)

    async def release(self,expression_id):
        async with self.database.transaction(read_only=True) as connection:
            row=await (await connection.execute("SELECT * FROM conversation.expression_plans WHERE expression_id=%s",(expression_id,))).fetchone()
        if not row or row["state"] not in {"ready","waiting_order"}:
            return progress(row) if row else None
        context=context_from_record(row["context"])
        plan=decode_plan(row["rendered"] or row["plan"])
        fences = []
        if self.actions is None and any(receipt(step.get("receipt")) and receipt(step.get("receipt")).action_id
                                        for step in row["steps"]):
            raise AppError("action_head_unavailable", status=503)
        if self.actions is not None:
            from .rbi_result_fence import action_result_fence
            for step in sorted(row["steps"], key=lambda item: item["action_id"]):
                actual = receipt(step.get("receipt"))
                if actual and actual.action_id:
                    _, _, fence = await action_result_fence(self.database, self.jobs, self.actions,
                        context, actual, result_revision=step["result_revision"])
                    if fence:
                        fences.append(fence)
        if not await self.behavior_ready(context, plan, task="conversation.expression_release",
                                         payload={"expression_id": expression_id}):
            async with self.database.transaction(read_only=True) as connection:
                current = await (await connection.execute(
                    "SELECT * FROM conversation.expression_plans WHERE expression_id=%s", (expression_id,))).fetchone()
            return progress(current) if current else None
        await self.identity.check(context,plan.source_refs)
        async with self.database.transaction() as connection:
            refs = unique_refs(plan.source_refs, *(fence.source_refs for fence in fences))
            if not await self.sources.validate_persistence(connection,context,refs):
                return None
            for fence in fences:
                await fence(connection)
            await connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",("expression-order:"+str(context.actor_id)+":"+str(context.conversation_id),))
            fresh=await (await connection.execute("SELECT * FROM conversation.expression_plans WHERE expression_id=%s FOR UPDATE",(expression_id,))).fetchone()
            if fences and (not fresh or signature(row["steps"]) != signature(fresh["steps"])
                           or signature(row["source_refs"]) != signature(fresh["source_refs"])
                           or signature(row["rendered"]) != signature(fresh["rendered"])):
                raise MaterialSnapshotChanged(fresh.get("revision") if fresh else None)
            if fresh["state"] not in {"ready","waiting_order"}:
                return progress(fresh)
            if fresh.get("deadline_at") is not None and fresh["deadline_at"] <= datetime.now(UTC):
                updated = await (await connection.execute(
                    "UPDATE conversation.expression_plans SET state='failed',reason='expression_window_expired',revision=revision+1 "
                    "WHERE expression_id=%s RETURNING *", (expression_id,))).fetchone()
                await self._event(connection, context, expression_id, updated["revision"])
                return progress(updated)
            if plan.turn_id:
                turn=await (await connection.execute("SELECT sequence,state FROM conversation.turns WHERE turn_id=%s",(plan.turn_id,))).fetchone()
                if not turn or turn["state"] in {"cancelled","failed"}:
                    updated=await (await connection.execute("UPDATE conversation.expression_plans SET state='cancelled',reason='turn_cancelled',revision=revision+1 WHERE expression_id=%s RETURNING *",(expression_id,))).fetchone()
                    await self._event(connection,context,expression_id,updated["revision"])
                    return progress(updated)
                earlier=await (await connection.execute("SELECT turn_id FROM conversation.turns WHERE actor_id=%s AND conversation_id=%s AND sequence<%s AND state IN ('collecting','queued','running','waiting_external','waiting_expression') AND COALESCE(error_code,'') NOT IN ('behavior_pending','behavior_preflight_pending') ORDER BY sequence LIMIT 1",(context.actor_id,context.conversation_id,turn["sequence"]))).fetchone()
                if earlier:
                    updated=await (await connection.execute("UPDATE conversation.expression_plans SET state='waiting_order',reason='earlier_turn_pending' WHERE expression_id=%s RETURNING *",(expression_id,))).fetchone()
                    return progress(updated)
            await connection.execute("UPDATE conversation.expression_plans SET state='delivering',reason=NULL WHERE expression_id=%s",(expression_id,))
        if plan.candidate_id and self.contact_preflight is not None:
            decision = await self.contact_preflight(context, plan.candidate_id, expression_id=expression_id)
            if decision["decision"] != "send":
                # Life has already closed this candidate generation or persisted its
                # next due job. This rendered expression has never entered delivery.
                async with self.database.transaction() as connection:
                    updated = await (await connection.execute(
                        "UPDATE conversation.expression_plans SET state='cancelled',reason=%s,revision=revision+1,updated_at=now() "
                        "WHERE expression_id=%s AND state='delivering' AND revision=%s RETURNING *",
                        (decision["reason"], expression_id, fresh["revision"]))).fetchone()
                    if updated:
                        await self._event(connection, context, expression_id, updated["revision"])
                    else:
                        updated = await (await connection.execute(
                            "SELECT * FROM conversation.expression_plans WHERE expression_id=%s", (expression_id,))).fetchone()
                return progress(updated)
        try:
            result=await self.delivery.send(context,plan)
        except Exception as error:
            if can_retry_resource_wait(error):
                async with self.database.transaction() as connection:
                    existing=await (await connection.execute("SELECT state FROM conversation.expressions WHERE expression_id=%s",
                                                             (expression_id,))).fetchone()
                    if existing is None or existing["state"] == "prepared":
                        await connection.execute("UPDATE conversation.expression_plans SET state='ready' "
                            "WHERE expression_id=%s AND state='delivering' AND revision=%s", (expression_id, fresh["revision"]))
            raise
        result_fence = None
        synthetic = (result.execution == "unknown" and result.receipt_id ==
                     str(uuid5(NAMESPACE_URL, "expression-receipt:" + expression_id)))
        if result.action_id and not synthetic:
            if self.actions is None:
                raise AppError("action_head_unavailable", status=503)
            from .rbi_result_fence import action_result_fence
            result, _, result_fence = await action_result_fence(
                self.database, self.jobs, self.actions, context, result, refresh=True)
        state = "cancelled" if result.execution == "cancelled" and result.result.get("contact_preflight") else delivery_state(result)
        if state == "unknown":
            state = "delivery_unknown"
        async with self.database.transaction() as connection:
            if result_fence:
                snapshot = await (await connection.execute(
                    "SELECT source_refs FROM conversation.expression_plans WHERE expression_id=%s",
                    (expression_id,))).fetchone()
                if not snapshot:
                    return None
                refs = unique_refs(plan.source_refs, source_refs(snapshot["source_refs"]), result_fence.source_refs)
                if not await self.sources.validate_persistence(connection, context, refs):
                    raise AppError("source_unavailable", status=403)
                await result_fence(connection)
                current = await (await connection.execute(
                    "SELECT revision,source_refs FROM conversation.expression_plans WHERE expression_id=%s FOR UPDATE",
                    (expression_id,))).fetchone()
                if not current or signature(snapshot["source_refs"]) != signature(current["source_refs"]):
                    raise MaterialSnapshotChanged(current.get("revision") if current else None)
            row=await (await connection.execute("UPDATE conversation.expression_plans SET state=%s,receipt=%s,reason=%s,revision=revision+1,updated_at=now() WHERE expression_id=%s AND state='delivering' AND revision=%s AND (state<>%s OR receipt IS DISTINCT FROM %s) RETURNING *",(state,document(result),result.error_code,expression_id,fresh["revision"],state,document(result)))).fetchone()
            if row:
                await self._event(connection,context,expression_id,row["revision"])
            else:
                row=await (await connection.execute("SELECT * FROM conversation.expression_plans WHERE expression_id=%s",(expression_id,))).fetchone()
        return progress(row)

    async def wake(self,actor_id,conversation_id):
        async with self.database.transaction() as connection:
            rows=await (await connection.execute("SELECT expression_id,revision FROM conversation.expression_plans WHERE actor_id=%s AND conversation_id=%s AND state IN ('ready','waiting_order') ORDER BY created_at",(actor_id,conversation_id))).fetchall()
            for row in rows:
                # New wake identity, same expression; release itself arbitrates the persisted input order.
                from uuid import uuid4
                await self.jobs.enqueue(connection,JobSpec("conversation.expression_release",f"expression-release:{row['expression_id']}:{uuid4()}",{"expression_id":row["expression_id"]},ServiceClass.INTERACTIVE,subject_id=actor_id))

    async def cancel(self,context,expression_id):
        current=await self.get(context,expression_id)
        if "render_action_ids" not in current and current.get("receipt") and current["receipt"].get("action_id") and current["state"]!="delivered":
            await self.actions.cancel(current["receipt"]["action_id"],context)
        for action_id in current.get("render_action_ids",()):
            await self.actions.cancel(action_id,context)
        async with self.database.transaction() as connection:
            row=await (await connection.execute("UPDATE conversation.expression_plans SET state=CASE WHEN state IN ('delivering','waiting_delivery','delivery_unknown') THEN 'delivery_unknown' ELSE 'cancelled' END,reason='cancel_requested',revision=revision+1 WHERE expression_id=%s AND state<>'delivered' RETURNING *",(expression_id,))).fetchone()
            if row:
                await self._event(connection,context,expression_id,row["revision"])

    async def _failed(self,context,expression_id,reason,*,result_fence=None,refs=(),expected_revision=None):
        async with self.database.transaction() as connection:
            if result_fence:
                snapshot = await (await connection.execute(
                    "SELECT source_refs FROM conversation.expression_plans WHERE expression_id=%s", (expression_id,))).fetchone()
                if not snapshot:
                    return
                refs = unique_refs(refs, source_refs(snapshot["source_refs"]),
                                   getattr(result_fence, "source_refs", ()))
                if not await self.sources.validate_persistence(connection, context, refs):
                    raise AppError("source_unavailable", status=403)
                await result_fence(connection)
                current = await (await connection.execute(
                    "SELECT state,revision,source_refs FROM conversation.expression_plans "
                    "WHERE expression_id=%s FOR UPDATE", (expression_id,))).fetchone()
                if not current or signature(snapshot["source_refs"]) != signature(current["source_refs"]):
                    raise MaterialSnapshotChanged(current.get("revision") if current else None)
                if current["state"] != "delivering" or current["revision"] != expected_revision:
                    return
            row=await (await connection.execute("UPDATE conversation.expression_plans SET state='failed',reason=%s,revision=revision+1 WHERE expression_id=%s RETURNING revision",(reason,expression_id))).fetchone()
            await self._event(connection,context,expression_id,row["revision"])

    async def _event(self,connection,context,expression_id,revision):
        await self.jobs.enqueue(connection,JobSpec("conversation.expression_result",f"expression-result:{expression_id}:{revision}",{"expression_id":expression_id,"context":wire(context)},ServiceClass.INTERACTIVE,subject_id=context.actor_id))

    async def get(self,context,expression_id):
        await self.identity.check(context)
        async with self.database.transaction(read_only=True) as connection:
            row=await (await connection.execute("SELECT * FROM conversation.expression_plans WHERE expression_id=%s AND actor_id=%s AND conversation_id=%s",(expression_id,context.actor_id,context.conversation_id))).fetchone()
        if not row:
            return await self.delivery.get(context,expression_id)
        await self.identity.check(context,source_refs(row["source_refs"]))
        plan=row["rendered"] or row["plan"]
        return {"expression_id":expression_id,"state":row["state"],"public_parts":plan["public_parts"],"artifacts":plan["artifacts"],"receipt":row["receipt"],"revision":row["revision"],"render_action_ids":[step["action_id"] for step in row["steps"]],"reason":row["reason"],"source_refs":row["source_refs"],"observed_at":row["updated_at"]}

    async def reconcile(self,context,expression_id,expected_revision):
        current=await self.get(context,expression_id)
        if current["revision"]!=expected_revision:
            raise AppError("revision_conflict",status=409)
        if "render_action_ids" not in current:
            return await self.delivery.reconcile(context,expression_id,expected_revision)
        if current["state"] == "delivered" and current["receipt"]:
            # The original final channel receipt can repair a previously failed projection.
            await self.channel_result(context, expression_id, receipt(current["receipt"]))
            return await self.get(context, expression_id)
        for action_id in current.get("render_action_ids",()):
            await self.actions.reconcile(action_id,context)
            actual=await self.actions.get(action_id,context)
            await self.on_action_result(context,receipt(actual["receipt"]),actual["result_revision"])
        pending_failure = current["state"] == "failed" and current["receipt"] and delivery_state(receipt(current["receipt"])) in {"waiting_delivery", "unknown"}
        if current["state"] in {"delivering","waiting_delivery","delivery_unknown"} or pending_failure:
            try:
                original=await self.delivery.get(context,expression_id)
            except AppError as error:
                if error.status!=404:
                    raise
            else:
                actual=await self.delivery.reconcile(context,expression_id,original["revision"])
                if actual["receipt"]:
                    await self.channel_result(context,expression_id,receipt(actual["receipt"]))
        return await self.get(context,expression_id)

    async def channel_result(self,context,expression_id,result,*,result_fence=None):
        bound = getattr(result_fence, "action_ids", ()) or (getattr(result_fence, "action_id", ""),)
        synthetic = (result and result.execution == "unknown" and result.receipt_id ==
                     str(uuid5(NAMESPACE_URL, "expression-receipt:" + expression_id)))
        if result and result.action_id and result.action_id not in bound and not synthetic:
            if self.actions is None:
                raise AppError("action_head_unavailable", status=503)
            from .rbi_result_fence import action_result_fence, combine_result_fences
            result, _, channel_fence = await action_result_fence(
                self.database, self.jobs, self.actions, context, result, refresh=True)
            result_fence = combine_result_fences(result_fence, channel_fence)
        if result_fence:
            result = await self.delivery.channel_result(context, expression_id, result, result_fence=result_fence)
        else:
            result = await self.delivery.channel_result(context, expression_id, result)
        if not result:
            return
        async with self.database.transaction() as connection:
            snapshot = None
            if result_fence:
                snapshot = await (await connection.execute(
                    "SELECT source_refs FROM conversation.expression_plans WHERE expression_id=%s "
                    "AND actor_id=%s AND conversation_id=%s", (expression_id, context.actor_id,
                                                                 context.conversation_id))).fetchone()
                if not snapshot:
                    return
                refs = unique_refs(source_refs(snapshot["source_refs"]), getattr(result_fence, "source_refs", ()))
                if not await self.sources.validate_persistence(connection, context, refs):
                    raise AppError("source_unavailable", status=403)
                await result_fence(connection)
            current = await (await connection.execute(
                "SELECT receipt,source_refs FROM conversation.expression_plans WHERE expression_id=%s "
                "AND actor_id=%s AND conversation_id=%s FOR UPDATE",
                (expression_id, context.actor_id, context.conversation_id))).fetchone()
            if result_fence and (not current or signature(snapshot["source_refs"]) != signature(current["source_refs"])):
                raise MaterialSnapshotChanged()
            if not current or not receipt_advances(receipt(current["receipt"]), result):
                return
            state = "cancelled" if result.execution == "cancelled" and result.result.get("contact_preflight") else delivery_state(result)
            if state == "unknown":
                state = "delivery_unknown"
            row=await (await connection.execute("UPDATE conversation.expression_plans SET state=%s,receipt=%s,reason=%s,revision=revision+1,updated_at=now() WHERE expression_id=%s AND (state<>%s OR receipt IS DISTINCT FROM %s) RETURNING revision",(state,document(result),result.error_code,expression_id,state,document(result)))).fetchone()
            if row:
                await self._event(connection,context,expression_id,row["revision"])

    async def expire(self,expression_id):
        async with self.database.transaction() as connection:
            row=await (await connection.execute("UPDATE conversation.expression_plans SET state=CASE WHEN state IN ('delivering','waiting_delivery') THEN 'delivery_unknown' ELSE 'render_unknown' END,reason='expression_lease_expired',revision=revision+1 WHERE expression_id=%s AND state IN ('waiting_render','ready','waiting_order','delivering','waiting_delivery') AND deadline_at<=now() RETURNING *",(expression_id,))).fetchone()
            if row:
                await self._event(connection,context_from_record(row["context"]),expression_id,row["revision"])


    async def expire_delivery(self, expression_id):
        """Observe the existing delivery intent and update both projections."""
        result = await self.delivery.expire(expression_id)
        if result is None:
            return
        async with self.database.transaction(read_only=True) as connection:
            row = await (await connection.execute(
                "SELECT context FROM conversation.expressions WHERE expression_id=%s", (expression_id,))).fetchone()
        if row:
            await self.channel_result(context_from_record(row["context"]), expression_id, result)


    async def recover(self):
        async with self.database.transaction() as connection:
            rows=await (await connection.execute("SELECT * FROM conversation.expression_plans WHERE state IN ('waiting_render','render_unknown','ready','waiting_order','delivering','waiting_delivery')")).fetchall()
            from uuid import uuid4
            for row in rows:
                if row["deadline_at"] is not None:
                    await self.jobs.enqueue(connection,JobSpec("conversation.render_expire",f"render-recover:{row['expression_id']}:{uuid4()}",{"expression_id":row["expression_id"]},ServiceClass.INTERACTIVE,row["deadline_at"],row["actor_id"]))
                if row["state"] in {"waiting_render","render_unknown"}:
                    for step in row["steps"]:
                        if step["state"] not in {"succeeded","failed","cancelled"}:
                            await self.jobs.enqueue(connection,JobSpec("conversation.expression_render",f"render-step-recover:{step['action_id']}:{uuid4()}",
                                {"expression_id":row["expression_id"],"index":step["index"]},ServiceClass.INTERACTIVE,subject_id=row["actor_id"]))
                if row["state"] in {"ready","waiting_order"}:
                    await self.jobs.enqueue(connection,JobSpec("conversation.expression_release",f"render-release-recover:{row['expression_id']}:{uuid4()}",{"expression_id":row["expression_id"]},ServiceClass.INTERACTIVE,subject_id=row["actor_id"]))
