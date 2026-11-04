"""A yielded diary generation resumes through its original day and native job."""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

from tianshu.conversation.codec import document, source_refs
from tianshu.models.retry import retry_delay_seconds
from tianshu.runtime.jobs import JobSpec
from .computation_retry import freeze_recipe, outcome
from .model_budget import generation_request
from .writing import generation_error
from .diary_materials import episode_versions


class DiaryRetry:
    def __init__(self, diaries):
        self.diaries, self.life = diaries, diaries.life

    async def failed(self, row, context, *, result=None, error=None, frozen=None):
        generation_id=row["generation_id"]
        try:
            status,code,retry=await outcome(self.diaries.models,generation_id,result=result,error=error)
        except Exception:
            await self.diaries._generation_state(row["id"],generation_id,"unknown",getattr(error,"code","model_outcome_unknown"))
            await self.diaries._observe_later(row["id"],generation_id,context.actor_id)
            return {"state":"unknown","attempt_id":generation_id}
        async with self.life.database.transaction(read_only=True) as connection:
            current=await (await connection.execute("SELECT * FROM life.diaries WHERE id=%s",(row["id"],))).fetchone()
        if not current or current["generation_id"]!=generation_id:
            return {"state":"changed_since_generation","attempt_id":generation_id}
        if code=="explicit_cancel":
            await self.diaries._generation_state(row["id"],generation_id,"cancelled",code,settle=True)
            return {"state":"cancelled","attempt_id":generation_id}
        if current["generation_cancel_requested"]:
            await self.diaries._generation_state(row["id"],generation_id,"cancelling","cancel_requested")
            await self.diaries._observe_later(row["id"],generation_id,context.actor_id)
            return {"state":"cancelling","attempt_id":generation_id}
        if result is not None and result.receipt.status=="succeeded":
            code=getattr(error,"code",None) or generation_error(result,"diary_generation_failed")
            await self.diaries._generation_state(row["id"],generation_id,"failed",code,settle=True)
            return {"state":"failed","attempt_id":generation_id}
        if status in {"queued","running","succeeded"}:
            await self.diaries._generation_state(row["id"],generation_id,"generating","model_"+status)
            await self.diaries._observe_later(row["id"],generation_id,context.actor_id)
            return {"state":"generating","attempt_id":generation_id}
        if not retry:
            state="cancelled" if status=="cancelled" else "failed"
            await self.diaries._generation_state(row["id"],generation_id,state,code,settle=True)
            return {"state":state,"attempt_id":generation_id}
        when=current["generation_retry_due_at"]
        if when is not None and when<=datetime.now(UTC):
            return await self.next(current,context,frozen)
        if when is None:
            async with self.life.database.transaction() as connection:
                await self.life.validate_persistence(connection,context,source_refs(current["source_refs"]))
                fresh=await (await connection.execute("SELECT * FROM life.diaries WHERE id=%s FOR UPDATE",(row["id"],))).fetchone()
                if fresh["generation_id"]!=generation_id or fresh["generation_cancel_requested"]:
                    return {"state":"changed_since_generation","attempt_id":generation_id}
                if fresh["generation_retry_due_at"] is None:
                    count=fresh["generation_retry_count"]+1
                    changed=await (await connection.execute("UPDATE life.diaries SET state='unknown',reason=%s,generation_retry_count=%s,"
                        "generation_retry_due_at=now()+%s*interval '1 second',revision=revision+1 WHERE id=%s RETURNING generation_retry_due_at",
                        (code,count,retry_delay_seconds(count),row["id"]))).fetchone()
                    when=changed["generation_retry_due_at"]
                else:
                    when=fresh["generation_retry_due_at"]
        await self.diaries._observe_later(row["id"],generation_id,context.actor_id,when=when)
        return {"state":"unknown","attempt_id":generation_id,"retry_due_at":when}

    async def next(self,row,context,frozen):
        recipe=row["generation_recipe"]
        refs=source_refs(row["source_refs"])
        if recipe is None:
            await self.diaries._generation_state(row["id"],row["generation_id"],"failed","source_unavailable",settle=True)
            return {"state":"failed","attempt_id":row["generation_id"]}
        if frozen is not None:
            recipe=self.diaries._frozen_recipe(frozen,recipe)
        generation_id=str(uuid4())
        selected=None
        if frozen is not None:
            selected=await generation_request(self.diaries.models,context,key="diary:"+generation_id,attempt_id=generation_id,function="reflect")
            selected=replace(selected,messages=frozen.messages,system=frozen.system,
                estimated_input_tokens=frozen.estimated_input_tokens,source_refs=frozen.source_refs,provider_session_scope="intent")
        async with self.life.database.transaction() as connection:
            await self.life.validate_persistence(connection,context,refs)
            versions=tuple((item["episode_id"],item["revision"]) for item in recipe["episodes"])
            actual=await episode_versions(self.life.memory,context,versions,connection=connection)
            if set(versions)!=actual:
                changed=await (await connection.execute("UPDATE life.diaries SET state='failed',reason='diary_episode_revised',"
                    "generation_retry_due_at=NULL,revision=revision+1 WHERE id=%s AND generation_id=%s "
                    "AND state IN ('generating','unknown') RETURNING id",
                    (row["id"],row["generation_id"]))).fetchone()
                if changed:
                    await self.diaries.automation.settle(connection,row["generation_id"],error="diary_episode_revised",recipe=recipe)
                return {"state":"failed","attempt_id":row["generation_id"],"reason":"diary_episode_revised"}
            fresh=await (await connection.execute("SELECT * FROM life.diaries WHERE id=%s FOR UPDATE",(row["id"],))).fetchone()
            if (fresh["generation_id"]!=row["generation_id"] or fresh["generation_cancel_requested"]
                    or fresh["state"] not in {"generating","unknown"}
                    or fresh["generation_retry_due_at"] is None or fresh["generation_retry_due_at"]>datetime.now(UTC)):
                return {"state":"changed_since_generation","attempt_id":row["generation_id"]}
            days=await (await connection.execute("SELECT * FROM life.diary_days WHERE generation_id=%s FOR UPDATE",(row["generation_id"],))).fetchall()
            if any(day["generation_revision"]<day["revision"] for day in days):
                await freeze_recipe(self.life,connection,"diary",row["id"],row["generation_id"],context,recipe,refs)
                await connection.execute("UPDATE life.diaries SET state='failed',generation_retry_due_at=NULL,reason='diary_material_changed',revision=revision+1 WHERE id=%s",(row["id"],))
                await self.diaries.automation.settle(connection,row["generation_id"],error="diary_material_changed",recipe=recipe)
                return {"state":"material_changed","attempt_id":row["generation_id"]}
            await freeze_recipe(self.life,connection,"diary",row["id"],row["generation_id"],context,recipe,refs)
            await connection.execute("UPDATE life.diaries SET state='generating',generation_id=%s,context=%s,"
                "generation_retry_due_at=NULL,reason=NULL,revision=revision+1 WHERE id=%s",(generation_id,document(context),row["id"]))
            await connection.execute("UPDATE life.diary_days SET generation_id=%s WHERE generation_id=%s",(generation_id,row["generation_id"]))
            if selected is not None:
                selected=await self.diaries.generations.prepare("diary:"+generation_id,selected,connection=connection)
                recipe=self.diaries._frozen_recipe(selected,recipe)
            await freeze_recipe(self.life,connection,"diary",row["id"],generation_id,context,recipe,refs,previous=row["generation_id"])
            await self.life.jobs.enqueue(connection,JobSpec("life.diary_generate","diary:"+generation_id,
                {"diary_id":row["id"],"generation_id":generation_id},subject_id=context.actor_id))
        return {"state":"generating","attempt_id":generation_id}
