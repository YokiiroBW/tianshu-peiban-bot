"""Dockge's actual external operation phase, under one canonical Action identity."""
from uuid import uuid4
from datetime import UTC,datetime,timedelta
from tianshu.types import Action,Receipt,OriginKind,Coverage
from tianshu.runtime.jobs import JobSpec
from ..catalog import CapabilityDefinition,Availability
from ..contracts import Fault,digest
from ..serialization import context_from
from ..tracking import mark_submitted
class InfrastructureOperation:
    definition=CapabilityDefinition("infrastructure.execute","基础设施操作","读取实际远端操作并核验执行、效果与观察状态。",{"type":"object","required":["operation_id"]},{"type":"object"},"write",permissions=frozenset({"external.manage"}),long_running=True)
    def __init__(self,service):self.service=service;self.control=service.infrastructure.control
    async def availability(self,context):return Availability(True,self.control.configured,reason=None if self.control.configured else "infrastructure_control_not_configured")
    async def execute(self,action,attempt):
        await self.service.authority.check(action.context,action.source_refs)
        owner=str(uuid4());control=self.control
        row=await control.local(control.repo.claim,owner,control.clock(),control.lease,operation_id=action.arguments["operation_id"])
        if row:
            if row["document"]["context"]["principal_id"]!=action.context.principal_id:raise Fault("forbidden",403)
            if row["kind"] in {"action","execute"}:await mark_submitted(row["operation_id"])
            await control._work(row,owner)
        return await self.project(action,attempt)
    async def project(self,action,attempt):
        value=await self.control.local(self.control.lookup,operation_id=action.arguments["operation_id"])
        state=value["state"];execution="succeeded" if state in {"observed","partial"} and value["settled"] else "failed" if state=="failed" else "unknown" if state=="unknown" else "running"
        effect="none" if value["kind"] not in {"action","execute"} else "confirmed" if execution=="succeeded" else "none" if value["acceptance"] in {"not_sent","rejected"} else "unknown"
        return Receipt(str(uuid4()),action.action_id,attempt.attempt_id,execution,effect,"not_requested",coverage=Coverage(state=="observed",missing=("partial_observation",) if state=="partial" else (),representation="actual_remote_operation"),result={"operation":value},error_code=value["reason_codes"][0] if value["reason_codes"] else None)
    async def reconcile(self,action,attempt):return await self.execute(action,attempt)
    async def cancel(self,action,attempt):return await self.project(action,attempt)

async def process_job(service,operation_id):
    control=service.infrastructure.control
    row=await control.local(control.repo.get,operation_id)
    if not row or row["settled"]:return
    context=context_from(row["document"]["context"]);key="infrastructure-work:"+digest(operation_id)
    action=Action(key,key,"infrastructure.execute",context.actor_id,context,{"operation_id":operation_id},OriginKind(row["document"]["origin_kind"]))
    receipt=await service.actions.invoke(action)
    if receipt.execution in {"running","unknown"}:receipt=await service.actions.reconcile(receipt.action_id,context)
    row=await control.local(control.repo.get,operation_id)
    if not row["settled"]:
        due=datetime.now(UTC)+timedelta(seconds=2)
        async with service.runtime.database.transaction() as conn:await service.runtime.jobs.enqueue(conn,JobSpec("capabilities.infrastructure_job","infrastructure-poll:"+operation_id+":"+str(due.timestamp()),{"operation_id":operation_id},scheduled_at=due))
