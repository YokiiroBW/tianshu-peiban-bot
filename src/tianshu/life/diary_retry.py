"""A yielded diary generation resumes through its original day and native job."""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

from tianshu.conversation.codec import document, source_refs
from tianshu.runtime.errors import AppError
from tianshu.runtime.jobs import JobSpec
from .computation_retry import freeze_recipe, outcome
from .model_budget import generation_request
from .writing import generation_error
from .diary_materials import episode_versions
from .generation_control import frozen_data_generation


class DiaryRetry:
    def __init__(self, diaries):
        self.diaries, self.life = diaries, diaries.life

    async def failed(self, row, context, *, result=None, error=None, frozen=None):
        generation_id=row["generation_id"]
        data_generation=frozen_data_generation(row["generation_recipe"])
        try:
            status,code,_retry=await outcome(self.diaries.models,generation_id,result=result,error=error)
        except Exception:
            status,code="unknown",getattr(error,"code","model_observation_unavailable")
        receipt = result.receipt if result is not None else None
        if receipt is None and status == "failed":
            try:
                receipt = await self.diaries.models.status(generation_id)
            except Exception:
                pass
        next_attempt = False
        async with self.life.database.transaction() as connection:
            await self.life.jobs.assert_generation_on(connection,data_generation)
            current=await (await connection.execute("SELECT * FROM life.diaries WHERE id=%s FOR UPDATE",(row["id"],))).fetchone()
            if not current or current["generation_id"]!=generation_id:
                return {"state":"changed_since_generation","attempt_id":generation_id}
            recovery = self.diaries.recovery
            ledger,due = (await recovery.observe_on(connection,generation_id,status,code,
                retry_after=getattr(receipt,"hold_until",None))
                if recovery is not None else ("unregistered",None))
            cancelling = current["generation_cancel_requested"] or code=="explicit_cancel"
            if status in {"queued","running"} or (status=="succeeded" and result is None):
                state = "cancelling" if cancelling else "generating"
                when = None
            elif status in {"unknown","absent"}:
                # Only the original attempt may be observed until Models proves
                # a terminal outcome. No successor is allowed for unknown.
                state = "cancelling" if cancelling else "generating" if ledger=="unregistered" else "unknown"
                when = None
            elif result is not None and result.receipt.status=="succeeded":
                state,when="failed",None
                code=getattr(error,"code",None) or generation_error(result,"diary_generation_failed")
            elif cancelling:
                state,when="cancelled",None
            else:
                when = due or current["generation_retry_due_at"] if ledger=="failed" else None
                state = "unknown" if when is not None else "cancelled" if status=="cancelled" else "failed"
                next_attempt = when is not None and when<=datetime.now(UTC)
                if ledger=="unregistered":
                    code="recovery_original_lineage_missing"
            count=current["generation_retry_count"] + (1 if when is not None and current["generation_retry_due_at"] is None else 0)
            await connection.execute("UPDATE life.diaries SET state=%s,reason=%s,generation_retry_count=%s,"
                "generation_retry_due_at=%s,revision=revision+1 WHERE id=%s", (state,code,count,when,row["id"]))
            if state in {"failed","cancelled"}:
                await self.diaries.automation.settle(connection,generation_id,error=code,recipe=current["generation_recipe"])
        if next_attempt:
            return await self.next({**current,"state":state,"generation_retry_due_at":when},context,frozen)
        if state in {"generating","unknown","cancelling"}:
            await self.diaries._observe_later(row["id"],generation_id,context.actor_id,when=when,data_generation=data_generation)
        return {"state":state,"attempt_id":generation_id,"retry_due_at":when}

    async def next(self,row,context,frozen):
        recipe=row["generation_recipe"]
        data_generation=frozen_data_generation(recipe)
        refs=source_refs(row["source_refs"])
        if frozen is not None:
            recipe=self.diaries._frozen_recipe(frozen,recipe)
        generation_id=str(uuid4())
        selected=None
        if frozen is not None:
            selected=await generation_request(self.diaries.models,context,key="diary:"+generation_id,attempt_id=generation_id,function="reflect")
            selected=replace(selected,messages=frozen.messages,system=frozen.system,
                estimated_input_tokens=frozen.estimated_input_tokens,source_refs=frozen.source_refs,provider_session_scope="intent")
        async with self.life.database.transaction() as connection:
            await self.life.jobs.assert_generation_on(connection,data_generation)
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
            if self.diaries.recovery is None:
                raise AppError("recovery_policy_unavailable",status=503)
            await self.diaries.recovery.inherit_on(connection,generation_id,row["generation_id"])
            await self.life.jobs.enqueue(connection,JobSpec("life.diary_generate","diary:"+generation_id,
                {"diary_id":row["id"],"generation_id":generation_id,"data_generation":data_generation},subject_id=context.actor_id,data_generation=data_generation))
        return {"state":"generating","attempt_id":generation_id}
