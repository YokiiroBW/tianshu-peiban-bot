"""Life's failed pure computations yield, retain their audits and resume on native jobs."""

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from tianshu.conversation.codec import signature
from tianshu.life.dependencies import LifeDependencies
from tianshu.life.writing import WritingService
from tianshu.models.types import ModelCompletion, ModelEvent, ModelPart, ModelReceipt, ModelResult
from tianshu.runtime.serialization import wire
from tianshu.types import SourceRef
from test_planner_execution import Models, setup, mark, chosen_activity
from test_diary_automation import fixture, start_day, collect_current, diary_state, day_state
from historical_support import adapt_models, diary_fixture as historical_diary_fixture, migrations


pytestmark=pytest.mark.asyncio


class Computations(Models):
    def __init__(self,output,status="unknown",error="timeout_unknown"):
        super().__init__(output)
        self.status_value,self.error=status,error
        self.results,self.receipts={},{}

    async def generate(self,request):
        self.calls.append(request)
        result=ModelResult((ModelPart("text",text=self.output),),ModelReceipt(request.attempt_id,request.intent_id,
            self.status_value,"submitted","route",1,"provider","model","openai.chat","quota",error_code=self.error),
            completion=ModelCompletion("complete",("stop",)))
        self.actual=result
        self.results[request.attempt_id],self.receipts[request.attempt_id]=result,result.receipt
        return result

    async def stream(self,request):
        self.streams.append(request)
        yield ModelEvent("text",text="PREVIOUS_PARTIAL_MUST_NOT_BE_CONTINUED")
        result=await self.generate(request)
        yield ModelEvent("receipt",receipt=result.receipt,data=result)

    async def result(self,attempt_id,*,context):
        self.observations.append((attempt_id,context))
        return self.results.get(attempt_id)

    async def status(self,attempt_id, *, phase="observe"):
        assert phase in {"pre_submit","observe"}
        return self.receipts.get(attempt_id)


async def plan_fixture(database):
    value=await setup(database,chosen_activity())
    await migrations(database,diary=True)
    models=adapt_models(Computations(chosen_activity()))
    value.models=value.worker.models=value.worker.generations.models=models
    return value


async def diary_fixture(database):
    value=await historical_diary_fixture(database)
    models=adapt_models(Computations("这一天只写下实际观察。",error="consumer_interrupted"))
    value.models=value.diaries.models=value.diaries.generations.models=models
    return value


async def expire(database,table,column):
    assert (table,column) in {("plan_watermarks","repair_due_at"),("diaries","generation_retry_due_at"),("chapters","generation_retry_due_at")}
    async with database.transaction() as connection:
        await connection.execute(f"UPDATE life.{table} SET {column}=now()-interval '1 second' WHERE {column} IS NOT NULL")
        await connection.execute("UPDATE runtime.recovery_lineages SET due_at=now()-interval '1 second' WHERE due_at IS NOT NULL")
        await connection.execute("UPDATE runtime.recovery_dependencies SET cooldown_until=now()-interval '1 second' WHERE cooldown_until IS NOT NULL")


async def test_planning_network_retries_are_distinct_from_the_two_schema_repairs(database):
    value=await plan_fixture(database)
    keys=[]
    # The original attempt and its successors share the current three-attempt
    # policy; leave the final slot for the successful independent request.
    for number in range(1,3):
        await value.worker.plan("actor",1,wire(value.context), value.data_generation)
        row=await mark(database)
        keys.append(row["generation_key"])
        assert row["generation_consumed"] and row["computation_retry_count"]==number
        assert row["repair_count"]==0 and row["repair_due_at"]==row["next_due_at"]
        assert row["reason"]=="planner_retry_wait" and row["last_error"]=="timeout_unknown"
        await value.worker.plan("actor",1,wire(value.context), value.data_generation)
        assert len(value.models.calls)==number
        await expire(database,"plan_watermarks","repair_due_at")
    assert len(set(keys))==2
    original=value.models.calls[0]
    async with database.transaction(read_only=True) as connection:
        frozen=await (await connection.execute("SELECT request FROM conversation.generation_requests WHERE attempt_id=%s",(original.attempt_id,))).fetchone()
    assert frozen["request"]==wire(original)
    value.models.status_value,value.models.error="succeeded",None
    await value.worker.plan("actor",1,wire(value.context), value.data_generation)
    current=await mark(database)
    assert current["last_error"] is None and current["computation_retry_count"]==0
    assert len(value.models.calls)==3 and len({request.attempt_id for request in value.models.calls})==3
    assert all(request.provider_session_scope=="intent" for request in value.models.calls)
    assert all("PREVIOUS_PARTIAL_MUST_NOT_BE_CONTINUED" not in str(wire(request.messages)) for request in value.models.calls)
    assert all('"repair"' not in str(wire(request.messages)) for request in value.models.calls)
    async with database.transaction(read_only=True) as connection:
        assert len(await (await connection.execute("SELECT id FROM life.activities")).fetchall())==1
        history=await (await connection.execute("SELECT previous_generation_key FROM life.plan_generations WHERE generation_key=%s",(current["generation_key"],))).fetchone()
        attempts=await (await connection.execute("SELECT status FROM runtime.recovery_attempts ORDER BY attempt_number")).fetchall()
    assert history["previous_generation_key"]==keys[-1]
    assert [entry["status"] for entry in attempts]==["unknown","unknown","succeeded"]
    assert value.models.results[original.attempt_id].receipt.status=="unknown"


async def test_planning_observes_a_live_original_and_later_explicit_cancel_stops_retry(database):
    value=await plan_fixture(database)
    value.models.status_value,value.models.error="queued",None
    await value.worker.plan("actor",1,wire(value.context), value.data_generation)
    await value.worker.plan("actor",1,wire(value.context), value.data_generation)
    assert len(value.models.calls)==1 and not (await mark(database))["generation_consumed"]
    original=value.models.calls[0]
    receipt=replace(value.models.receipts[original.attempt_id],status="unknown",error_code="consumer_interrupted",finished_at=datetime.now(UTC))
    value.models.receipts[original.attempt_id]=receipt
    value.models.results[original.attempt_id]=replace(value.models.results[original.attempt_id],receipt=receipt)
    await value.worker.plan("actor",1,wire(value.context), value.data_generation)
    assert (await mark(database))["generation_consumed"]
    await expire(database,"plan_watermarks","repair_due_at")
    value.models.receipts[original.attempt_id]=replace(receipt,cancellation_reason="explicit_cancel")
    await value.worker.plan("actor",1,wire(value.context), value.data_generation)
    stopped=await mark(database)
    assert stopped["last_error"]=="explicit_cancel" and stopped["repair_due_at"] is None
    assert len(value.models.calls)==1 and value.models.results[original.attempt_id].receipt.status=="unknown"


async def test_an_expired_day_proposal_is_superseded_by_a_fresh_current_day(database,monkeypatch):
    value=await plan_fixture(database)
    await value.worker.plan("actor",1,wire(value.context), value.data_generation)
    first=await mark(database)
    original=value.models.calls[0]
    from tianshu.life import planning_engine
    future=datetime.now(UTC)+timedelta(days=1)
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):
            return future.astimezone(tz) if tz else future.replace(tzinfo=None)
    monkeypatch.setattr(planning_engine,"datetime",Clock)
    value.models.status_value,value.models.error="succeeded",None
    await value.worker.plan("actor",1,wire(value.context), value.data_generation)
    current=await mark(database)
    async with database.transaction(read_only=True) as connection:
        old=await (await connection.execute("SELECT state,day FROM life.plan_generations WHERE generation_key=%s",(first["generation_key"],))).fetchone()
        new=await (await connection.execute("SELECT state,day FROM life.plan_generations WHERE generation_key=%s",(current["generation_key"],))).fetchone()
    assert old["state"]=="superseded" and new["state"]=="applied" and new["day"]>old["day"]
    assert len(value.models.calls)==2 and value.models.calls[1].attempt_id!=original.attempt_id
    assert value.models.results[original.attempt_id].receipt.status=="unknown"


async def test_diary_retry_preserves_full_recipe_and_selection_but_uses_a_new_independent_request(database):
    value=await diary_fixture(database)
    await start_day(value)
    row=await collect_current(database,value)
    await value.diaries.generate(row["id"],row["generation_id"])
    held=await diary_state(database)
    recipe=deepcopy(held["generation_recipe"])
    original=value.models.calls[0]
    assert (await day_state(database))["reviewed_revision"]==0 and held["generation_retry_due_at"]
    await value.diaries.generate(row["id"],row["generation_id"])
    assert len(value.models.calls)==1
    await expire(database,"diaries","generation_retry_due_at")
    value.models.status_value,value.models.error="succeeded",None
    result=await value.diaries.generate(row["id"],row["generation_id"])
    child=await diary_state(database)
    assert child["generation_id"]!=original.attempt_id and result["attempt_id"]==child["generation_id"]
    assert len(value.models.calls)==1  # The successor waits at the native queue tail.
    frozen=await value.diaries.generations.request(child["generation_id"],value.context)
    assert frozen.messages==original.messages and frozen.system==original.system
    assert frozen.intent_id!=original.intent_id and frozen.provider_session_scope=="intent"
    assert child["generation_recipe"]==recipe
    async with database.transaction(read_only=True) as connection:
        audit=await (await connection.execute("SELECT * FROM life.generation_recipes ORDER BY created_at,generation_id")).fetchall()
        old=await (await connection.execute("SELECT request FROM conversation.generation_requests WHERE attempt_id=%s",(original.attempt_id,))).fetchone()
    assert old["request"]==wire(original) and len(audit)==2
    assert all(entry["recipe"]==recipe for entry in audit)
    assert next(entry for entry in audit if entry["generation_id"]==child["generation_id"])["previous_generation_id"]==original.attempt_id
    await value.diaries.generate(child["id"],child["generation_id"])
    saved=await diary_state(database)
    assert saved["state"]=="draft" and saved["current_revision"]==1 and (await day_state(database))["reviewed_revision"]==1
    value.models.results[original.attempt_id]=replace(value.models.results[original.attempt_id],receipt=replace(value.models.results[original.attempt_id].receipt,status="succeeded"))
    await value.diaries.generate(row["id"],row["generation_id"])
    assert (await diary_state(database))["current_revision"]==1 and len(value.models.calls)==2


async def test_an_unknown_diary_does_not_block_a_later_registered_day(database):
    value=await diary_fixture(database)
    await start_day(value)
    row=await collect_current(database,value)
    await value.diaries.generate(row["id"],row["generation_id"])
    later=replace(value.episode,episode_id="later-day",occurred_at=value.episode.occurred_at+timedelta(days=1),summary="仅属于另一天的实际经历")
    value.memory.episodes+=(later,)
    day="2026-11-03"
    await value.diaries.day_closed(value.context,day)
    await value.diaries.review_day("actor",value.identity.scene_id(value.context),day,1,await value.jobs.current_generation())
    async with database.transaction(read_only=True) as connection:
        fresh=await (await connection.execute("SELECT * FROM life.diaries WHERE day=%s",(day,))).fetchone()
    await value.diaries.collect(fresh["id"],fresh["generation_id"],None)
    value.models.status_value,value.models.error="succeeded",None
    await value.diaries.generate(fresh["id"],fresh["generation_id"])
    async with database.transaction(read_only=True) as connection:
        entries=await (await connection.execute("SELECT day,state,current_revision FROM life.diaries ORDER BY day")).fetchall()
    assert entries[0]["state"]=="unknown" and entries[1]["state"]=="draft" and entries[1]["current_revision"]==1
    assert len(value.models.calls)==2 and value.models.calls[0].intent_id!=value.models.calls[1].intent_id
    assert "在想象的小屋里整理书架" not in str(wire(value.models.calls[1].messages))


async def test_diary_permanent_failure_is_not_reviewed_or_periodically_retried_but_new_material_can_continue(database):
    value=await diary_fixture(database)
    value.models.status_value,value.models.error="failed","model_function_not_configured"
    day,scope=await start_day(value)
    row=await collect_current(database,value)
    await value.diaries.generate(row["id"],row["generation_id"])
    failed=await day_state(database)
    assert failed["reviewed_revision"]==0 and failed["blocked_revision"]==1 and failed["generation_id"] is None
    count=len(value.jobs.items)
    await value.diaries.recover()
    assert len(value.jobs.items)==count
    assert (await value.diaries.review_day("actor",scope,day,1, await value.jobs.current_generation()))["state"]=="blocked"
    changed=replace(value.episode,revision=2,summary="更新后的实际经历")
    value.memory.episodes=(changed,)
    await value.diaries.changed(value.context,"changed:2",episode=changed)
    await value.diaries.review_day("actor",scope,day,2, await value.jobs.current_generation())
    fresh=await collect_current(database,value)
    value.models.status_value,value.models.error="succeeded",None
    await value.diaries.generate(fresh["id"],fresh["generation_id"])
    assert (await day_state(database))["reviewed_revision"]==2 and len(value.models.calls)==2


async def test_diary_latest_explicit_cancel_overrides_an_immutable_cleanup_unknown(database):
    value=await diary_fixture(database)
    await start_day(value)
    row=await collect_current(database,value)
    await value.diaries.generate(row["id"],row["generation_id"])
    original=value.models.calls[0]
    await expire(database,"diaries","generation_retry_due_at")
    value.models.receipts[original.attempt_id]=replace(value.models.receipts[original.attempt_id],cancellation_reason="explicit_cancel")
    await value.diaries.generate(row["id"],row["generation_id"])
    stopped=await diary_state(database)
    assert stopped["state"]=="cancelled" and stopped["generation_retry_due_at"] is None
    assert len(value.models.calls)==1 and value.models.results[original.attempt_id].receipt.error_code=="consumer_interrupted"
    assert (await day_state(database))["reviewed_revision"]==0


async def test_a_rejected_success_settles_the_diary_instead_of_observing_it_forever(database):
    value=await diary_fixture(database)
    await start_day(value)
    row=await collect_current(database,value)
    await value.diaries.generate(row["id"],row["generation_id"])
    original=value.models.calls[0]
    invalid=replace(value.models.results[original.attempt_id],parts=(),receipt=replace(
        value.models.receipts[original.attempt_id],status="succeeded",error_code=None))
    value.models.results[original.attempt_id]=invalid
    value.models.receipts[original.attempt_id]=invalid.receipt
    stopped=await value.diaries.retry.failed(await diary_state(database),value.context,result=invalid)
    assert stopped["state"]=="failed" and (await diary_state(database))["generation_retry_due_at"] is None
    current=await day_state(database)
    assert current["generation_id"] is None and current["reviewed_revision"]==0 and current["blocked_revision"]==1
    await value.diaries.recover()
    assert len(value.models.calls)==1


async def test_a_dirty_day_at_retry_time_yields_the_old_pointer_to_normal_collection(database):
    value=await diary_fixture(database)
    day,scope=await start_day(value)
    row=await collect_current(database,value)
    await value.diaries.generate(row["id"],row["generation_id"])
    original=value.models.calls[0]
    later=replace(value.episode,episode_id="same-day-later",summary="同一天稍后实际新增的经历")
    value.memory.episodes+=(later,)
    await value.diaries.changed(value.context,"later",episode=later)
    await expire(database,"diaries","generation_retry_due_at")
    result=await value.diaries.generate(row["id"],row["generation_id"])
    assert result["state"]=="material_changed" and len(value.models.calls)==1
    current=await day_state(database)
    assert current["generation_id"] is None and current["reviewed_revision"]==0 and current["blocked_revision"]==1
    assert any(job.task=="life.diary_review" and job.payload["expected_revision"]==2 for job in value.jobs.items)
    await value.diaries.review_day("actor",scope,day,2, await value.jobs.current_generation())
    child=await collect_current(database,value)
    assert child["generation_id"]!=original.attempt_id and len(child["generation_recipe"]["episodes"])==2
    value.models.status_value,value.models.error="succeeded",None
    await value.diaries.generate(child["id"],child["generation_id"])
    assert (await day_state(database))["reviewed_revision"]==2 and len(value.models.calls)==2


async def test_recipe_audit_is_erased_on_source_withdrawal_and_the_unknown_is_never_replayed(database):
    value=await diary_fixture(database)
    await start_day(value)
    row=await collect_current(database,value)
    await value.diaries.generate(row["id"],row["generation_id"])
    original=value.models.calls[0]
    async with database.transaction() as connection:
        await LifeDependencies(value.life).invalidate(connection,value.episode.source_refs,"forgotten")
    async with database.transaction(read_only=True) as connection:
        audit=await (await connection.execute("SELECT recipe,source_refs FROM life.generation_recipes WHERE generation_id=%s",(original.attempt_id,))).fetchone()
    assert audit["recipe"] is None and audit["source_refs"]
    await value.diaries.generate(row["id"],row["generation_id"])
    assert len(value.models.calls)==1 and value.models.results[original.attempt_id].receipt.status=="unknown"


async def test_chapter_retry_uses_the_existing_writing_queue_and_saves_only_the_selected_successor(database):
    value=await plan_fixture(database)
    value.models.output="独立完成的合成章节。"
    value.models.error="upstream_http_429"
    life=value.worker.life
    life.sources.record_event=AsyncMock(return_value=(SourceRef("chapter-completion",1),))
    writing=WritingService(life,value.models,value.worker.assembler)
    writing.bind_observability(recovery_policy=life.recovery_policy,statistics=None)
    completion=AsyncMock()
    writing.bind_completion(completion)
    life.jobs.register("life.chapter_generate",writing.generate)
    async with database.transaction() as connection:
        await connection.execute("INSERT INTO life.works(id,actor_id,title,scope_ids) VALUES('work','actor','Work',ARRAY['actor-scope'])")
        await connection.execute("INSERT INTO life.chapters(id,work_id,position,title,plan) VALUES('chapter','work',1,'Chapter','A synthetic plan')")
    result=await writing.request_generation(value.context,{"id":"chapter","instruction":"Write this chapter only",
        "expected_revision":1,"client_request_id":"chapter-start"})
    await writing.generate("chapter",result.attempt_id)
    original=value.models.calls[0]
    await expire(database,"chapters","generation_retry_due_at")
    value.models.status_value,value.models.error="succeeded",None
    await writing.generate("chapter",original.attempt_id)
    async with database.transaction(read_only=True) as connection:
        fresh=await (await connection.execute("SELECT * FROM life.chapters WHERE id='chapter'")).fetchone()
    assert fresh["generation_id"]!=original.attempt_id and len(value.models.calls)==1
    await writing.generate("chapter",fresh["generation_id"])
    async with database.transaction(read_only=True) as connection:
        revisions=await (await connection.execute("SELECT * FROM life.chapter_revisions")).fetchall()
        frozen=await (await connection.execute("SELECT request FROM conversation.generation_requests WHERE attempt_id=%s",(original.attempt_id,))).fetchone()
    assert len(revisions)==1 and revisions[0]["model_attempt_id"]==fresh["generation_id"]
    assert completion.await_count==1 and len(value.models.calls)==2
    assert frozen["request"]==wire(original) and value.models.results[original.attempt_id].receipt.status=="unknown"
