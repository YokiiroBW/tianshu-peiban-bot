"""Acquisition business stages execute inside canonical Actions and Procrastinate jobs."""
from uuid import uuid4
import asyncio
from tianshu.types import Action,OriginKind,Receipt,SourceRef
from tianshu.runtime.serialization import wire
from ..catalog import CapabilityDefinition,Availability
from ..contracts import Fault,digest
from ..serialization import context_from
from ..tracking import mark_submitted
from .runtime import public_job

class Acquisition:
    definition=CapabilityDefinition("media.acquire","获取媒体","执行已受理的下载、整理、发布和实际库核验。",{"type":"object","required":["job_id"],"properties":{"job_id":{"type":"string"}}},{"type":"object"},"write")
    def __init__(self,service):self.service,self.media=service,service.media
    async def availability(self,context):return Availability(True,True)
    async def execute(self,action,attempt):
        identity=action.arguments["job_id"]
        existing=await self.media.local(self.media.repo.job,identity)
        if not existing or existing.get("context",{}).get("principal_id")!=action.context.principal_id:raise Fault("not_found",404)
        owner=str(uuid4());job=await self.media.local(self.media.repo.claim,owner,job_id=identity)
        if job is None:return await self.reconcile(action,attempt)
        await mark_submitted(identity)
        task=asyncio.create_task(self.media.worker.run(job,owner));self.media.worker.active[identity]=task
        try:await task
        finally:self.media.worker.active.pop(identity,None)
        return await self.reconcile(action,attempt)
    async def reconcile(self,action,attempt):
        row=await self.media.local(self.media.repo.job,action.arguments["job_id"])
        if row is None:raise Fault("not_found",404)
        state=row["state"]
        execution="succeeded" if state in {"completed","published"} else "failed" if state in {"failed","auth_required","waiting_metadata"} else "cancelled" if state=="cancelled" else "unknown" if state=="unknown" else "running"
        effect="confirmed" if execution=="succeeded" else "unknown" if row.get("asset_operation") or row.get("asset_receipt") or state=="unknown" else "none"
        return Receipt(str(uuid4()),action.action_id,attempt.attempt_id,execution,effect,"not_requested",result={"job":public_job(row)},error_code=None if execution=="succeeded" else row.get("code"))
    async def cancel(self,action,attempt):
        row=await self.media.local(self.media.repo.job,action.arguments["job_id"])
        if row and row["state"] not in {"completed","published","cancelled","failed"}:
            await self.media.local(self.media.repo.update_job,row["job_id"],{"cancel_requested":True,"code":"cancel_requested"})
            task=self.media.worker.active.get(row["job_id"])
            if task:task.cancel();await asyncio.gather(task,return_exceptions=True)
        return await self.reconcile(action,attempt)

async def process_job(service,job_id,generation,revision):
    row=await service.media.local(service.media.repo.job,job_id)
    if not row or row.get("generation",0)!=generation or row.get("revision",1)!=revision or row["state"] not in {"queued","retry_wait"}:return
    if not row.get("context"):raise Fault("media_context_required",503)
    context=context_from(row["context"]);key="media-acquire:"+digest([job_id,generation,revision])
    await service.actions.invoke(Action(key,key,"media.acquire",context.actor_id,context,{"job_id":job_id},OriginKind.TIMER,source_refs=tuple(SourceRef(**x) for x in row.get("source_refs",[]))))
