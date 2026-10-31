"""Expression rendering owns no model or speech engine and never redelivers a result."""
from tianshu.sources.dependencies import unique_refs
from dataclasses import replace
from datetime import UTC,datetime,timedelta
from uuid import NAMESPACE_URL,uuid5
from tianshu.runtime.errors import AppError
from tianshu.runtime.jobs import JobSpec
from tianshu.runtime.serialization import wire
from tianshu.types import Action,OriginKind,ServiceClass
from .codec import artifacts,context_from_record,document,receipt,signature,source_refs
from .types import ExpressionPlan,ExpressionProgress


def decode_plan(value):
    return ExpressionPlan(value["expression_id"],value["actor_id"],value["target_ref"],tuple(value["public_parts"]),source_refs(value["source_refs"]),OriginKind(value["origin_kind"]),value.get("turn_id"),artifacts(value.get("artifacts",())),value.get("candidate_id"))


def progress(row):
    return ExpressionProgress(row["expression_id"],row["state"],receipt(row["receipt"]),tuple(step["action_id"] for step in row["steps"]),row["reason"])


class ExpressionRenderer:
    def __init__(self,database,jobs,identity,sources,actions,speech,delivery):
        self.database,self.jobs,self.identity,self.sources=database,jobs,identity,sources
        self.actions,self.speech,self.delivery=actions,speech,delivery

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
        deadline=datetime.now(UTC)+timedelta(minutes=15)
        async with self.database.transaction() as connection:
            if not await self.sources.validate_persistence(connection,context,plan.source_refs):
                raise AppError("source_unavailable",status=409)
            added=await (await connection.execute("INSERT INTO conversation.expression_plans(expression_id,actor_id,conversation_id,fingerprint,context,plan,source_refs,profile,state,service_class,steps,deadline_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING *",(plan.expression_id,context.actor_id,context.conversation_id,fingerprint,document(context),document(plan),document(plan.source_refs),document(profile),"waiting_render" if steps else "ready",("due" if service_class==ServiceClass.DUE else "elastic"),document(steps),deadline))).fetchone()
            if not added:
                existing=await (await connection.execute("SELECT * FROM conversation.expression_plans WHERE expression_id=%s",(plan.expression_id,))).fetchone()
                if existing["fingerprint"]!=fingerprint:
                    raise AppError("expression_identity_conflict",status=409)
                return progress(existing)
            if steps:
                for step in steps:
                    await self.jobs.enqueue(connection,JobSpec("conversation.expression_render",f"expression-render:{step['action_id']}",{"expression_id":plan.expression_id,"index":step["index"]},ServiceClass.DUE if service_class==ServiceClass.DUE else ServiceClass.ELASTIC,subject_id=context.actor_id))
            await self.jobs.enqueue(connection,JobSpec("conversation.render_expire","render-expire:"+plan.expression_id,{"expression_id":plan.expression_id},ServiceClass.INTERACTIVE,deadline,context.actor_id))
        if not steps:
            return await self._deliver(context,plan)
        return progress(added)

    async def render(self,expression_id,index):
        async with self.database.transaction(read_only=True) as connection:
            row=await (await connection.execute("SELECT * FROM conversation.expression_plans WHERE expression_id=%s",(expression_id,))).fetchone()
        if not row or row["state"]!="waiting_render":
            return
        context,plan=context_from_record(row["context"]),decode_plan(row["plan"])
        await self.identity.check(context,plan.source_refs)
        async with self.database.transaction() as connection:
            if not await self.sources.validate_persistence(connection,context,plan.source_refs):
                return
            fresh=await (await connection.execute("SELECT * FROM conversation.expression_plans WHERE expression_id=%s FOR UPDATE",(expression_id,))).fetchone()
            if fresh["state"]!="waiting_render":
                return
            steps=fresh["steps"]
            step=next(item for item in steps if item["index"]==index)
            if step["state"]!="planned":
                return
            step["state"]="dispatching"
            await connection.execute("UPDATE conversation.expression_plans SET steps=%s WHERE expression_id=%s",(document(steps),expression_id))
        action=Action(step["action_id"],f"expression-render:{expression_id}:{index}","speech.synthesize",context.actor_id,context,
            {"profile_id":row["profile"]["profile_id"],"profile_revision":row["profile"]["revision"],"text":plan.public_parts[index]["text"]},OriginKind.ACTOR,plan.source_refs,expression_id=expression_id,deadline_at=row["deadline_at"])
        try:
            await self.actions.invoke(action,background=True,service_class=ServiceClass(row["service_class"]))
            current=await self.actions.get(action.action_id,context)
            await self.on_action_result(context,receipt(current["receipt"]),current["result_revision"])
        except BaseException as error:
            # Pre-ledger rejection is distinct from losing the outcome of a submitted call.
            try:
                actual=await self.actions.get(action.action_id,context)
            except BaseException as observation_error:
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

    async def on_action_result(self,context,result,result_revision):
        if result is None or not result.action_id:
            return False
        async with self.database.transaction(read_only=True) as connection:
            row=await (await connection.execute("SELECT * FROM conversation.expression_plans WHERE actor_id=%s AND conversation_id=%s AND steps@>%s",(context.actor_id,context.conversation_id,document([{"action_id":result.action_id}])))).fetchone()
        if not row:
            return False
        refs=unique_refs(tuple(ref for ref in (*source_refs(row["source_refs"]),*source_refs(result.result.get("source_refs",())),*(ref for item in result.artifacts for ref in item.source_refs))))
        async with self.database.transaction() as connection:
            if not await self.sources.validate_persistence(connection,context,refs):
                await connection.execute("UPDATE conversation.expression_plans SET state='failed',plan='{}',rendered=NULL,reason='source_unavailable',revision=revision+1 WHERE expression_id=%s",(row["expression_id"],))
                return True
            fresh=await (await connection.execute("SELECT * FROM conversation.expression_plans WHERE expression_id=%s FOR UPDATE",(row["expression_id"],))).fetchone()
            steps=fresh["steps"]
            step=next(item for item in steps if item["action_id"]==result.action_id)
            if result_revision<=step["result_revision"]:
                return True
            step.update(state=result.execution,result_revision=result_revision,artifacts=wire(result.artifacts),receipt=wire(result))
            state="render_unknown" if any(item["state"]=="unknown" for item in steps) else "failed" if any(item["state"] in {"failed","cancelled"} for item in steps) else "delivering" if all(item["state"]=="succeeded" for item in steps) else "waiting_render"
            if fresh["state"] in {"delivering","waiting_delivery","delivered","delivery_unknown","cancelled","failed"}:
                state=fresh["state"]
            await connection.execute("UPDATE conversation.expression_plans SET steps=%s,state=%s,source_refs=%s,reason=%s,revision=revision+1 WHERE expression_id=%s",(document(steps),state,document(refs),result.error_code,row["expression_id"]))
            if state in {"failed","render_unknown"}:
                await self._event(connection,context,row["expression_id"],fresh["revision"]+1)
        if state=="delivering" and fresh["state"] not in {"delivering","waiting_delivery","delivered","delivery_unknown","cancelled","failed"}:
            if datetime.now(UTC)>fresh["deadline_at"]:
                await self._failed(context,fresh["expression_id"],"expression_window_expired")
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
                    await self._failed(context,plan.expression_id,"speech_artifact_missing")
                    return True
                if fresh["profile"]["output_mode"]=="both":
                    parts.append(part)
                parts.extend({"kind":"audio","media":wire(item.media)} for item in actual)
                output.extend(actual)
            await self._deliver(context,replace(plan,public_parts=tuple(parts),source_refs=refs,artifacts=tuple(output)))
        return True

    async def _deliver(self,context,plan):
        async with self.database.transaction() as connection:
            if not await self.sources.validate_persistence(connection,context,plan.source_refs):
                raise AppError("source_unavailable",status=409)
            await connection.execute("UPDATE conversation.expression_plans SET rendered=%s,state='ready' WHERE expression_id=%s",(document(plan),plan.expression_id))
        return await self.release(plan.expression_id)

    async def release(self,expression_id):
        async with self.database.transaction(read_only=True) as connection:
            row=await (await connection.execute("SELECT * FROM conversation.expression_plans WHERE expression_id=%s",(expression_id,))).fetchone()
        if not row or row["state"] not in {"ready","waiting_order"}:
            return progress(row) if row else None
        context=context_from_record(row["context"])
        plan=decode_plan(row["rendered"] or row["plan"])
        await self.identity.check(context,plan.source_refs)
        async with self.database.transaction() as connection:
            if not await self.sources.validate_persistence(connection,context,plan.source_refs):
                return None
            await connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",("expression-order:"+str(context.actor_id)+":"+str(context.conversation_id),))
            fresh=await (await connection.execute("SELECT * FROM conversation.expression_plans WHERE expression_id=%s FOR UPDATE",(expression_id,))).fetchone()
            if fresh["state"] not in {"ready","waiting_order"}:
                return progress(fresh)
            if plan.turn_id:
                turn=await (await connection.execute("SELECT sequence,state FROM conversation.turns WHERE turn_id=%s",(plan.turn_id,))).fetchone()
                if not turn or turn["state"] in {"cancelled","failed"}:
                    updated=await (await connection.execute("UPDATE conversation.expression_plans SET state='cancelled',reason='turn_cancelled',revision=revision+1 WHERE expression_id=%s RETURNING *",(expression_id,))).fetchone()
                    await self._event(connection,context,expression_id,updated["revision"])
                    return progress(updated)
                earlier=await (await connection.execute("SELECT turn_id FROM conversation.turns WHERE actor_id=%s AND conversation_id=%s AND sequence<%s AND state IN ('collecting','queued','running','waiting_external','waiting_expression') ORDER BY sequence LIMIT 1",(context.actor_id,context.conversation_id,turn["sequence"]))).fetchone()
                if earlier:
                    updated=await (await connection.execute("UPDATE conversation.expression_plans SET state='waiting_order',reason='earlier_turn_pending' WHERE expression_id=%s RETURNING *",(expression_id,))).fetchone()
                    return progress(updated)
            await connection.execute("UPDATE conversation.expression_plans SET state='delivering',reason=NULL WHERE expression_id=%s",(expression_id,))
        result=await self.delivery.send(context,plan)
        state="delivered" if result.delivery=="delivered" else "waiting_delivery" if result.delivery=="pending" else "delivery_unknown" if result.delivery=="unknown" else "failed"
        async with self.database.transaction() as connection:
            row=await (await connection.execute("UPDATE conversation.expression_plans SET state=%s,receipt=%s,reason=%s,revision=revision+1,updated_at=now() WHERE expression_id=%s AND (state<>%s OR receipt IS DISTINCT FROM %s) RETURNING *",(state,document(result),result.error_code,expression_id,state,document(result)))).fetchone()
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

    async def _failed(self,context,expression_id,reason):
        async with self.database.transaction() as connection:
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
        for action_id in current.get("render_action_ids",()):
            await self.actions.reconcile(action_id,context)
            actual=await self.actions.get(action_id,context)
            await self.on_action_result(context,receipt(actual["receipt"]),actual["result_revision"])
        if current["state"] in {"delivering","waiting_delivery","delivery_unknown"}:
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

    async def channel_result(self,context,expression_id,result):
        await self.delivery.channel_result(context,expression_id,result)
        async with self.database.transaction() as connection:
            state="delivered" if result.delivery=="delivered" else "waiting_delivery" if result.delivery=="pending" else "delivery_unknown" if result.delivery=="unknown" else "failed"
            row=await (await connection.execute("UPDATE conversation.expression_plans SET state=%s,receipt=%s,reason=%s,revision=revision+1,updated_at=now() WHERE expression_id=%s AND (state<>%s OR receipt IS DISTINCT FROM %s) RETURNING revision",(state,document(result),result.error_code,expression_id,state,document(result)))).fetchone()
            if row:
                await self._event(connection,context,expression_id,row["revision"])

    async def expire(self,expression_id):
        async with self.database.transaction() as connection:
            row=await (await connection.execute("UPDATE conversation.expression_plans SET state=CASE WHEN state IN ('delivering','waiting_delivery') THEN 'delivery_unknown' ELSE 'render_unknown' END,reason='expression_lease_expired',revision=revision+1 WHERE expression_id=%s AND state IN ('waiting_render','ready','waiting_order','delivering','waiting_delivery') AND deadline_at<=now() RETURNING *",(expression_id,))).fetchone()
            if row:
                await self._event(connection,context_from_record(row["context"]),expression_id,row["revision"])


    async def recover(self):
        async with self.database.transaction() as connection:
            rows=await (await connection.execute("SELECT * FROM conversation.expression_plans WHERE state IN ('waiting_render','ready','waiting_order','delivering','waiting_delivery')")).fetchall()
            from uuid import uuid4
            for row in rows:
                await self.jobs.enqueue(connection,JobSpec("conversation.render_expire",f"render-recover:{row['expression_id']}:{uuid4()}",{"expression_id":row["expression_id"]},ServiceClass.INTERACTIVE,row["deadline_at"],row["actor_id"]))
                if row["state"] in {"ready","waiting_order"}:
                    await self.jobs.enqueue(connection,JobSpec("conversation.expression_release",f"render-release-recover:{row['expression_id']}:{uuid4()}",{"expression_id":row["expression_id"]},ServiceClass.INTERACTIVE,subject_id=row["actor_id"]))
