"""Original Models outcomes outrank opaque RPC failures; integration owner runs these."""

import asyncio
from dataclasses import replace
from datetime import UTC,datetime,timedelta
import json

import pytest

from tianshu.memory.embedding import Embeddings
from tianshu.memory.steps import ModelSteps
from tianshu.models.types import ModelCompletion,ModelPart,ModelReceipt,ModelRequest,ModelResult
from tianshu.runtime.errors import AppError
from tianshu.types import OriginKind,RequestBudget,ServiceClass


class RpcModels:
    def __init__(self,status="unknown",*,error="transport_unknown",control="available",invalid=False,wrapper=True):
        self.first_status,self.error,self.control=status,error,control
        self.invalid,self.wrapper=invalid,wrapper
        self.requests,self.reads,self.status_reads=[],[],[]
        self.results,self.receipts={},{}
        self.after_generate=None
        self.next_status="succeeded"

    async def generation_profile(self,request):
        return {"route_id":"fixture","route_revision":1,"protocol":"openai.chat","model":"fixture",
                "output_parameters":{"max_tokens":request.max_output_tokens}}

    async def embedding_profile(self):
        return {"dimensions":1024,"normalization":"none","model_version":"fixture"}

    def answer(self,request,status="succeeded",error=None):
        receipt=ModelReceipt(request.attempt_id,request.intent_id,status,"submitted","fixture",1,
            "fixture","fixture","openai.chat","fixture",error_code=error,
            cancellation_reason="explicit_cancel" if status=="cancelled" else None,
            finished_at=datetime.now(UTC) if status not in {"queued","running"} else None,
            data_generation=request.data_generation)
        if status!="succeeded":
            return ModelResult((),receipt)
        if request.function=="embed":
            return ModelResult((),receipt,{"data":[{"embedding":[1.0]+[0.0]*1023}]})
        value=json.loads(request.messages[0].parts[0].text)
        targets=[]
        next_id=0
        for target in value["targets"]:
            operations=[]
            for material in value["materials"]:
                if material["id"] not in target["new_material_ids"] or "尾部事实" not in material["text"]:
                    continue
                next_id+=1
                operations.append({"kind":"add_claim","target":{"create":"n"+str(next_id)},
                    "payload":{"predicate":"interest","value":"RPC恢复事实","speaker_id":material["speaker_id"],
                        "assertion":"self_confirmed","polarity":True,"conditions":{},"valid_from":None,"valid_until":None},
                    "support_groups":[[material["id"]]],"context_refs":[],"reads":[],"reason":"原文明确支持"})
            targets.append({"subject_id":target["subject_id"],"status":"changed" if operations else "no_change",
                            "operations":operations,"unresolved":[]})
        output={"targets":[] if self.invalid else targets,"needs_context":[]}
        return ModelResult((ModelPart("text",text=json.dumps(output,ensure_ascii=False)),),receipt,
            {"finish_reason":"stop"},ModelCompletion("complete",("stop",)))

    async def generate(self,request):
        self.requests.append(request)
        first=len(self.requests)==1
        status=self.first_status if first else self.next_status
        result=self.answer(request,status,self.error if first else "timeout_unknown" if status=="unknown" else None)
        self.results[request.attempt_id]=result
        self.receipts[request.attempt_id]=result.receipt
        if self.after_generate:
            await self.after_generate(request)
        if first and self.wrapper:
            raise AppError("internal_error",status=500)
        return result

    async def submit(self,request,*,completion=None):
        # Models persists an attempt before acknowledging it; replaying the
        # same submission only reattaches its completion wake.
        if request.attempt_id in self.receipts:
            return self.receipts[request.attempt_id]
        return (await self.generate(request)).receipt

    async def result(self,attempt_id,*,context):
        self.reads.append(attempt_id)
        if self.control in {"unavailable","result_unavailable"}:
            raise AppError("internal_error",status=500)
        return None if self.control=="missing" else self.results.get(attempt_id)

    async def status(self,attempt_id):
        self.status_reads.append(attempt_id)
        if self.control=="unavailable":
            raise AppError("internal_error",status=500)
        return None if self.control=="missing" else self.receipts.get(attempt_id)

    def release(self):
        request=self.requests[0]
        result=self.answer(request)
        self.results[request.attempt_id],self.receipts[request.attempt_id]=result,result.receipt


def configure(core,model):
    core.steps.models=model
    policy=replace(core.memory.consolidation.policy,quiet_seconds=0,max_input_tokens=64000,context_messages=0)
    core.memory.consolidation.policy=core.memory.consolidation.materials.policy=policy


async def observe(core,context,key,text="尾部事实"):
    refs=await core.sources.record_event(context,producer="web",event_id=key,revision=1,
        occurred_at=datetime.now(UTC),parts=({"kind":"text","text":text},),origin_kind=OriginKind.HUMAN)
    return await core.memory.observe_episode(context,{"event_id":key,"occurred_at":datetime.now(UTC),
        "summary":text,"source_refs":refs,"origin_kind":"human"})


async def episode_row(core,episode):
    async with core.database.transaction(read_only=True) as conn:
        return await (await conn.execute("SELECT * FROM memory.episodes WHERE episode_id=%s",(episode.episode_id,))).fetchone()


async def latest(core,partition):
    async with core.database.transaction(read_only=True) as conn:
        return await (await conn.execute("SELECT * FROM memory.consolidation_batches WHERE partition_id=%s ORDER BY created_at DESC LIMIT 1",
            (partition,))).fetchone()


async def attempts(core,batch):
    async with core.database.transaction(read_only=True) as conn:
        return await (await conn.execute("SELECT * FROM memory.consolidation_attempts WHERE batch_id=%s ORDER BY number",(batch["batch_id"],))).fetchall()


async def due_now(core,batch):
    async with core.database.transaction() as conn:
        await conn.execute("UPDATE memory.consolidation_batches SET retry_due_at=now()-interval '1 second' WHERE batch_id=%s",(batch["batch_id"],))


@pytest.mark.parametrize("status",["succeeded","unknown","queued","running","failed","cancelled"])
@pytest.mark.parametrize("with_policy", [False, True])
async def test_rpc_wrapper_uses_original_receipt_instead_of_internal_error(core,ordinary_users,status,with_policy):
    alice,_=ordinary_users
    error=None if status in {"succeeded","queued","running"} else "model_function_not_configured" if status=="failed" else "cancelled" if status=="cancelled" else "transport_unknown"
    model=RpcModels(status,error=error)
    configure(core,model)
    if with_policy:
        from tianshu.runtime.recovery_policy import RecoveryPolicy
        core.memory.bind_observability(statistics=None,recovery_policy=RecoveryPolicy())
    episode=await observe(core,alice,"wrapper-"+status)
    partition=(await episode_row(core,episode))["consolidation_partition_id"]
    await core.workers.consolidate(partition)
    batch=await latest(core,partition)
    chain=await attempts(core,batch)
    assert len(model.requests)==1 and chain[0]["first_failure"]["rpc_failure"]["error_code"]=="internal_error"
    assert chain[0]["first_failure"]["status"]==status
    if status=="succeeded":
        assert batch["status"]=="applied" and len((await core.claims.list(alice)).items)==1
    elif status in {"unknown","queued","running"}:
        assert batch["status"]=="quarantined" and batch["retry_due_at"]>datetime.now(UTC)
        if status in {"queued","running"}:
            await due_now(core,batch)
            await core.workers.consolidate(partition)
            assert len(model.requests)==1
        model.release()
        await core.workers.consolidate(partition)
        assert (await latest(core,partition))["status"]=="applied" and len(model.requests)==1
    else:
        assert batch["status"]=="failed" and batch["retry_due_at"] is None
        await core.workers.recover()
        await core.workers.consolidate(partition)
        assert len(model.requests)==1


@pytest.mark.parametrize("with_policy", [False, True])
async def test_confirmed_unknown_after_rpc_error_retries_independently_and_late_result_applies_once(core,ordinary_users,with_policy):
    alice,_=ordinary_users
    model=RpcModels()
    configure(core,model)
    policy = None
    if with_policy:
        from tianshu.runtime.recovery_policy import RecoveryPolicy
        policy = RecoveryPolicy()
        core.memory.bind_observability(statistics=None,recovery_policy=policy)
    episode=await observe(core,alice,"confirmed-rpc-unknown")
    partition=(await episode_row(core,episode))["consolidation_partition_id"]
    await core.workers.consolidate(partition)
    batch=await latest(core,partition)
    original=model.requests[0]
    await due_now(core,batch)
    if policy:
        async with core.database.transaction() as conn:
            await conn.execute("UPDATE runtime.recovery_lineages SET due_at=now()-interval '1 second' WHERE lineage_id=%s",
                               (batch["manifest"]["recovery"]["lineage_id"],))
            generation=await core.memory.jobs.current_generation_on(conn)
        await core.memory.consolidation.claim_due_recoveries(generation=generation)
    await core.workers.consolidate(partition)
    final=await latest(core,partition)
    chain=await attempts(core,final)
    assert final["batch_id"]==batch["batch_id"] and final["status"]=="applied" and len(model.requests)==2
    assert model.requests[1].attempt_id!=original.attempt_id and model.requests[1].messages==original.messages
    assert chain[1]["previous_step_id"]==chain[0]["step_id"]
    assert model.results[original.attempt_id].receipt.status=="unknown"
    if policy:
        async with core.database.transaction(read_only=True) as conn:
            policy_chain=await (await conn.execute("SELECT status FROM runtime.recovery_attempts WHERE lineage_id=%s "
                "ORDER BY attempt_number",(batch["manifest"]["recovery"]["lineage_id"],))).fetchall()
        assert [item["status"] for item in policy_chain]==["unknown","succeeded"]
    model.release()
    await core.workers.consolidate(partition)
    assert len((await core.claims.list(alice)).items)==1 and final["selected_step_id"]==model.requests[1].intent_id


@pytest.mark.parametrize("with_policy", [False, True])
async def test_superseded_consolidation_result_cannot_win_while_successor_waits(core,ordinary_users,with_policy):
    alice,_=ordinary_users
    model=RpcModels()
    model.next_status="unknown"
    configure(core,model)
    policy=None
    if with_policy:
        from tianshu.runtime.recovery_policy import RecoveryPolicy
        policy=RecoveryPolicy()
        core.memory.bind_observability(statistics=None,recovery_policy=policy)
    episode=await observe(core,alice,"superseded-rpc-result")
    partition=(await episode_row(core,episode))["consolidation_partition_id"]
    await core.workers.consolidate(partition)
    batch=await latest(core,partition)
    await due_now(core,batch)
    if policy:
        async with core.database.transaction() as conn:
            await conn.execute("UPDATE runtime.recovery_lineages SET due_at=now()-interval '1 second' WHERE lineage_id=%s",
                               (batch["manifest"]["recovery"]["lineage_id"],))
            generation=await core.memory.jobs.current_generation_on(conn)
        await core.memory.consolidation.claim_due_recoveries(generation=generation)
    await core.workers.consolidate(partition)
    assert len(model.requests)==2 and (await latest(core,partition))["status"]=="quarantined"
    model.release()
    await core.workers.consolidate(partition)
    assert not (await core.claims.list(alice)).items and len(model.requests)==2
    successor=model.requests[1]
    result=model.answer(successor)
    model.results[successor.attempt_id]=result
    model.receipts[successor.attempt_id]=result.receipt
    await core.workers.consolidate(partition)
    final=await latest(core,partition)
    assert final["status"]=="applied" and final["selected_step_id"]==successor.intent_id
    assert len((await core.claims.list(alice)).items)==1
    if policy:
        async with core.database.transaction(read_only=True) as conn:
            chain=await (await conn.execute("SELECT status FROM runtime.recovery_attempts WHERE lineage_id=%s "
                "ORDER BY attempt_number",(batch["manifest"]["recovery"]["lineage_id"],))).fetchall()
        assert [item["status"] for item in chain]==["unknown","succeeded"]


@pytest.mark.parametrize("control",["missing","unavailable"])
@pytest.mark.parametrize("with_policy", [False, True])
async def test_missing_control_never_becomes_permission_to_retry_on_later_wakes(core,ordinary_users,control,with_policy):
    alice,_=ordinary_users
    model=RpcModels(control=control)
    configure(core,model)
    if with_policy:
        from tianshu.runtime.recovery_policy import RecoveryPolicy
        core.memory.bind_observability(statistics=None,recovery_policy=RecoveryPolicy())
    episode=await observe(core,alice,"control-"+control)
    partition=(await episode_row(core,episode))["consolidation_partition_id"]
    await core.workers.consolidate(partition)
    for _ in range(3):
        batch=await latest(core,partition)
        assert batch["status"]=="quarantined"
        await due_now(core,batch)
        await core.workers.recover()
        await core.workers.consolidate(partition)
        assert len(model.requests)==1 and len(await attempts(core,batch))==1
    model.control="available"
    model.release()
    await core.workers.consolidate(partition)
    assert (await latest(core,partition))["status"]=="applied" and len(model.requests)==1


async def failed_wrapper(core,context,monkeypatch,key):
    model=RpcModels()
    configure(core,model)
    real_execute=core.steps.execute_recorded
    async def old_execute(step_id,**kwargs):
        _,request=await core.steps.recorded(step_id)
        try:
            return await model.generate(request),request
        except AppError:
            await core.steps._outcome(step_id,"unknown","internal_error")
            raise
    monkeypatch.setattr(core.steps,"execute_recorded",old_execute)
    first=await observe(core,context,key+"-one")
    second=await observe(core,context,key+"-two","同批另一条原文")
    partition=(await episode_row(core,first))["consolidation_partition_id"]
    await core.workers.consolidate(partition)
    monkeypatch.setattr(core.steps,"execute_recorded",real_execute)
    batch=await latest(core,partition)
    assert batch["status"]=="failed" and batch["error_code"]=="internal_error"
    return model,first,second,partition,batch


@pytest.mark.parametrize("late_success",[False,True])
async def test_normal_resume_recovers_old_failed_wrapper_with_original_audit(core,ordinary_users,monkeypatch,late_success):
    alice,_=ordinary_users
    model,episode,other,partition,failed=await failed_wrapper(core,alice,monkeypatch,"historical-rpc")
    if late_success:
        model.release()
    status=await core.memory.extraction_status(alice,episode.episode_id)
    assert status["action"]=="observe_original_attempt"
    async with core.database.transaction() as conn:
        resumed=await core.workers.recovery.resume_on(conn,alice,{"episode_id":episode.episode_id,
            "expected_revision":episode.revision,"client_request_id":"normal-rpc-recovery"})
    assert resumed.data["queued"] and resumed.data["batch_id"]==failed["batch_id"]
    await core.workers.consolidate(partition)
    final=await latest(core,partition)
    assert final["status"]=="applied" and final["batch_id"]==failed["batch_id"]
    assert len(model.requests)==(1 if late_success else 2)
    recovery=final["manifest"]["rpc_recoveries"][0]
    assert recovery["previous_error_code"]=="internal_error" and recovery["previous_step"]["status"]=="unknown"
    assert recovery["latest_original_observation"]["status"]==("succeeded" if late_success else "unknown")
    assert (await episode_row(core,other))["extraction_state"]=="completed"
    selected=final["selected_step_id"]
    model.release()
    await core.workers.consolidate(partition)
    assert len((await core.claims.list(alice)).items)==1 and (await latest(core,partition))["selected_step_id"]==selected


async def test_failed_wrapper_resume_keeps_other_principals_and_changed_episode_cursors_out(core,ordinary_users,monkeypatch):
    alice,bob=ordinary_users
    _,episode,other,partition,batch=await failed_wrapper(core,alice,monkeypatch,"rpc-guards")
    body={"episode_id":episode.episode_id,"expected_revision":episode.revision,"client_request_id":"rpc-guard"}
    with pytest.raises(AppError) as forbidden:
        async with core.database.transaction() as conn:
            await core.workers.recovery.resume_on(conn,bob,body)
    assert forbidden.value.status in {403,404}
    async with core.database.transaction() as conn:
        await conn.execute("UPDATE memory.episodes SET extraction_cursor=extraction_cursor || '{\"position\":1}'::jsonb WHERE episode_id=%s",(other.episode_id,))
    with pytest.raises(AppError) as changed:
        async with core.database.transaction() as conn:
            await core.workers.recovery.resume_on(conn,alice,body)
    assert changed.value.code=="memory_consolidation_episode_changed"
    assert (await latest(core,partition))["status"]=="failed" and not (await latest(core,partition))["manifest"].get("rpc_recoveries")


async def test_historical_rpc_recovery_keeps_missing_control_observing_until_original_returns(core,ordinary_users,monkeypatch):
    alice,_=ordinary_users
    model,episode,_,partition,batch=await failed_wrapper(core,alice,monkeypatch,"old-rpc-control")
    model.control="missing"
    async with core.database.transaction() as conn:
        await core.workers.recovery.resume_on(conn,alice,{"episode_id":episode.episode_id,
            "expected_revision":episode.revision,"client_request_id":"old-control-resume"})
    for _ in range(2):
        await due_now(core,batch)
        await core.workers.consolidate(partition)
        assert len(model.requests)==1 and (await latest(core,partition))["status"]=="quarantined"
    model.control="available"
    model.release()
    await core.workers.consolidate(partition)
    assert (await latest(core,partition))["status"]=="applied" and len(model.requests)==1


async def test_source_withdrawal_before_recovered_result_apply_prevents_a_commit(core,ordinary_users):
    alice,_=ordinary_users
    model=RpcModels("succeeded",error=None)
    configure(core,model)
    episode=await observe(core,alice,"withdraw-rpc-result")
    partition=(await episode_row(core,episode))["consolidation_partition_id"]
    async def withdraw(request):
        await core.sources.invalidate(alice,{"source_id":episode.source_refs[0].source_id,
            "expected_revision":1,"client_request_id":"forget-rpc-result"},forgotten=True)
    model.after_generate=withdraw
    await core.workers.consolidate(partition)
    assert (await latest(core,partition))["status"]=="revoked" and not (await core.claims.list(alice)).items
    assert len(model.requests)==1


async def test_models_succeeded_semantic_rejections_never_reselect_the_same_output(core,ordinary_users):
    alice,_=ordinary_users
    model=RpcModels("succeeded",error=None,invalid=True,wrapper=False)
    configure(core,model)
    episode=await observe(core,alice,"semantic-is-not-rpc")
    partition=(await episode_row(core,episode))["consolidation_partition_id"]
    await core.workers.consolidate(partition)
    batch=await latest(core,partition)
    assert batch["status"]=="failed" and len(model.requests)==2
    reads=list(model.reads)
    assert not await core.memory.consolidation.retries.defer(batch["batch_id"],AppError("memory_consolidation_invalid",status=502))
    await core.workers.consolidate(partition)
    assert model.reads==reads and len(model.requests)==2 and not batch["manifest"].get("rpc_recoveries")


@pytest.mark.parametrize("exception_type",[asyncio.CancelledError,GeneratorExit])
async def test_cancellation_and_generator_exit_propagate_without_control_reads(core,ordinary_users,exception_type):
    alice,_=ordinary_users
    model=RpcModels()
    async def stop(request):
        raise exception_type()
    model.generate=stop
    core.steps.models=model
    episode=await observe(core,alice,"cancel-propagation-"+exception_type.__name__)
    request=ModelRequest("cancelled-step","cancelled-attempt","embed",ServiceClass.ELASTIC,alice,
        RequestBudget(datetime.now(UTC)+timedelta(seconds=30)),(),source_refs=episode.source_refs,execution_timeout_seconds=30)
    async with core.database.transaction() as conn:
        await core.steps.prepare_on(conn,request.intent_id,request)
    with pytest.raises(exception_type):
        await core.steps.execute_recorded(request.intent_id)
    step,_=await core.steps.recorded(request.intent_id)
    assert step["state"]=="unknown" and not model.reads and not model.status_reads


@pytest.mark.parametrize("code,status",[("memory_consolidation_invalid",502),("model_function_not_configured",503)])
async def test_named_semantic_or_configuration_error_does_not_trigger_rpc_observation(core,ordinary_users,code,status):
    alice,_=ordinary_users
    model=RpcModels()
    original=AppError(code,status=status)
    async def reject(request):
        raise original
    model.generate=reject
    core.steps.models=model
    episode=await observe(core,alice,"named-"+code)
    request=ModelRequest("named-step","named-attempt","memory.extract",ServiceClass.ELASTIC,alice,
        RequestBudget(datetime.now(UTC)+timedelta(seconds=300)),(),source_refs=episode.source_refs,execution_timeout_seconds=300)
    async with core.database.transaction() as conn:
        await core.steps.prepare_on(conn,request.intent_id,request)
    with pytest.raises(AppError) as raised:
        await core.steps.execute_recorded(request.intent_id)
    assert raised.value is original and not model.reads and not model.status_reads


async def test_cancellation_during_rpc_control_lookup_keeps_unknown_and_propagates(core,ordinary_users):
    alice,_=ordinary_users
    model=RpcModels()
    async def cancel_control(attempt_id,*,context):
        raise asyncio.CancelledError()
    model.result=cancel_control
    core.steps.models=model
    episode=await observe(core,alice,"cancel-during-control")
    request=ModelRequest("control-cancel-step","control-cancel-attempt","embed",ServiceClass.ELASTIC,alice,
        RequestBudget(datetime.now(UTC)+timedelta(seconds=30)),(),source_refs=episode.source_refs,execution_timeout_seconds=30)
    async with core.database.transaction() as conn:
        await core.steps.prepare_on(conn,request.intent_id,request)
    with pytest.raises(asyncio.CancelledError):
        await core.steps.execute_recorded(request.intent_id)
    step,_=await core.steps.recorded(request.intent_id)
    assert step["state"]=="unknown" and step["error_code"]=="internal_error" and not model.status_reads


async def test_embedding_step_recovers_successful_original_rpc_result_with_its_own_budget(core,ordinary_users):
    alice,_=ordinary_users
    model=RpcModels("succeeded",error=None)
    steps=ModelSteps(core.database,model,core.sources)
    embeddings=Embeddings(core.database,model,steps)
    episode=await observe(core,alice,"embedding-rpc-original")
    values,_=await embeddings.vector(alice,"索引文本",RequestBudget(datetime.now(UTC)+timedelta(seconds=30)),
        intent="embedding-rpc",source_refs=episode.source_refs)
    assert values==[1.0]+[0.0]*1023 and len(model.requests)==1
    assert model.requests[0].function=="embed" and model.requests[0].execution_timeout_seconds==30


@pytest.mark.parametrize("wrapper",[False,True])
@pytest.mark.parametrize("status,error",[("unknown","consumer_interrupted"),("cancelled","model_cancelled"),("cancelled",None)])
async def test_confirmed_cancellation_keeps_receipt_error_separate_from_control_decision(core,ordinary_users,wrapper,status,error):
    alice,_=ordinary_users
    model=RpcModels(status,error=error,wrapper=wrapper)
    answer=model.answer
    def cancelled_answer(request,status="succeeded",error=None):
        result=answer(request,status,error)
        return replace(result,receipt=replace(result.receipt,cancellation_reason="explicit_cancel"))
    model.answer=cancelled_answer
    steps=ModelSteps(core.database,model,core.sources)
    episode=await observe(core,alice,"receipt-cancel-"+str(wrapper)+"-"+status+"-"+str(error))
    request=ModelRequest("receipt-cancel-step","receipt-cancel-attempt","embed",ServiceClass.ELASTIC,alice,
        RequestBudget(datetime.now(UTC)+timedelta(seconds=30)),(),source_refs=episode.source_refs,execution_timeout_seconds=30)
    with pytest.raises(AppError) as raised:
        await steps.run(request.intent_id,request)
    assert raised.value.code=="explicit_cancel"
    assert raised.value.details["status"]==status and raised.value.details["receipt_error_code"]==error
    assert raised.value.details["cancellation_reason"]=="explicit_cancel"
    step,_=await steps.recorded(request.intent_id)
    assert step["state"]==status and step["error_code"]==error
    receipt=model.results[request.attempt_id].receipt
    assert receipt.status==status and receipt.error_code==error and len(model.requests)==1
