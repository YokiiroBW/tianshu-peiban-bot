from dataclasses import replace
from datetime import UTC, datetime, timedelta
from importlib.resources import files
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from tianshu.capabilities.catalog import Availability, Catalog, CapabilityDefinition
from tianshu.conversation.codec import document
from tianshu.conversation.context.assembly import ContextAssembler
from tianshu.runtime.serialization import wire
from tianshu.life import planner
from tianshu.life.service import LifeService
from tianshu.life.worker import LifeWorker
from tianshu.models.types import ModelCompletion, ModelEvent, ModelMessage, ModelPart, ModelReceipt, ModelRequest, ModelResult
from tianshu.runtime.commands import CommandStore
from tianshu.runtime.errors import AppError
from tianshu.runtime.jobs import JobQueue
from tianshu.runtime.migrations import Migration, migrate
from tianshu.types import OriginKind, Receipt, TrustedContext, RequestBudget, ServiceClass, SourceRef
from tianshu.conversation.generation import attempt_for


class Identity:
    epoch=1

    async def refresh_scheduled_context(self,previous,**_kwargs):
        await self.check(previous)
        return replace(previous,access_epoch=self.epoch)
    async def check(self, *_args):
        pass

    async def check_many(self, _context, groups):
        return tuple(True for _ in groups)


class Sources:
    async def validate_persistence(self, *_args):
        return True

    async def check_many_on(self, connection, context, groups):
        return tuple([await self.validate_persistence(connection, context, group) for group in groups])

    async def prepare_event_writes(self, connection, context, events, *, additional_refs=(), scoped_refs=()):
        # This isolated fake has no source locks; still check the same full
        # dependency batch that a real Sources implementation validates.
        refs = [*additional_refs]
        for event in events:
            assert event["producer"] and event["event_id"] and event["revision"] > 0
            refs.extend(event.get("support_refs", ()))
        if not await self.validate_persistence(connection, context, tuple(refs)):
            raise AppError("source_support_unavailable", status=403)
        for scoped_context, scoped_group in scoped_refs:
            await self.validate_persistence(connection, scoped_context, tuple(scoped_group))


class Companion:
    async def actor(self, _context):
        return {"enabled": True, "revision": 1, "persona_id": None}

    async def affect(self, _context):
        return {"regulation": {}, "source_refs": (), "valence": 0}


class ReadPage:
    definition = CapabilityDefinition(
        "content.read",
        "Read an actual page",
        "Read selected URL",
        {
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
            "additionalProperties": False,
        },
        {"type": "object"},
        model_tool={"name": "content_read"},
    )

    async def availability(self, _context):
        return Availability(True, True)


class Models:
    def __init__(self, output, *, lose_response=False):
        self.output, self.lose_response = output, lose_response
        self.calls, self.observations = [], []
        self.streams = []
        self.actual = None

    async def generation_profile(self, request):
        return {"model": "deepseek-v4.1-flash", "protocol": "openai.chat", "route_id": "fixture-route",
                "route_revision": 1, "verified_capabilities": ["text", "stream"],
                "max_output_tokens": request.max_output_tokens, "output_parameters": {"max_tokens": request.max_output_tokens}}

    async def stream(self, request):
        self.streams.append(request)
        result = await self.generate(request)
        # A complete-looking partial event must never be the domain's result.
        yield ModelEvent("text", text=self.output)
        yield ModelEvent("receipt", receipt=result.receipt, data=result)

    async def generate(self, request):
        self.calls.append(request)
        self.actual = ModelResult(
            (ModelPart("text", text=self.output),),
            ModelReceipt(
                request.attempt_id,
                request.intent_id,
                "unknown" if self.lose_response else "succeeded",
                "submitted",
                "fixture-route",
                1,
                "fixture-provider",
                "fixture-model",
                "native",
                "quota",
            ),
            completion=ModelCompletion("complete", ("stop",)),
        )
        if self.lose_response:
            raise TimeoutError("fixture response lost after submission")
        return self.actual

    async def result(self, attempt_id, *, context):
        self.observations.append((attempt_id, context))
        assert self.actual is None or self.actual.receipt.attempt_id == attempt_id
        return self.actual

    async def status(self,attempt_id, *, phase="observe"):
        assert phase in {"pre_submit", "observe"}
        return self.actual.receipt if self.actual and self.actual.receipt.attempt_id==attempt_id else None


class Actions:
    def __init__(self):
        self.calls = []

    async def invoke(self, action, **kwargs):
        self.calls.append((action, kwargs))
        self.receipt = Receipt(
            str(uuid4()), action.action_id, str(uuid4()), "pending", "none", "not_requested"
        )
        return self.receipt

    async def get(self, action_id, _context):
        action = next(action for action,_kwargs in self.calls if action.action_id==action_id)
        assert action_id == action.action_id
        return {
            "action": wire(action),
            "receipt": wire(self.receipt),
            "result_revision": 0,
            "result_observed_at": datetime.now(UTC),
        }

    async def assert_result_head_on(self, connection, context, *, action_id, expected_receipt,
                                    result_revision, data_generation, source_refs=()):
        # An explicit in-memory port double; real Action row locking has its
        # own PostgreSQL integration coverage.
        current = await self.get(action_id, context)
        if current["result_revision"] > result_revision:
            raise AppError("result_head_advanced", status=409)
        if current["result_revision"] < result_revision:
            raise AppError("result_head_regressed", status=409)
        if current["receipt"] != wire(expected_receipt):
            raise AppError("result_receipt_revision_conflict", status=409)
        return {"action_id": action_id, "result_revision": result_revision}


async def setup(database, output, *, lose_response=False, capability_id="content.read"):
    await migrate(
        database,
        (
            Migration("0031_life", files("tianshu.life").joinpath("schema.sql").read_text()),
            Migration("0033_life_contact_expiry", files("tianshu.life").joinpath("contact_expiry.sql").read_text()),
            Migration("0035_life_planning_recovery", files("tianshu.life").joinpath("planning_recovery.sql").read_text()),
            Migration("0038_life_computation_retry", files("tianshu.life").joinpath("0038_life_computation_retry.sql").read_text()),
            Migration(
                "0032_conversation",
                files("tianshu.conversation").joinpath("schema.sql").read_text(),
            ),
            Migration("0040_life_execution", files("tianshu.life").joinpath("rb_life_execution.sql").read_text()),
            Migration("rbf_life_planning_wake", files("tianshu.life").joinpath("rbf_life_planning_wake.sql").read_text()),
            Migration("rbi_activity_reconciliation", files("tianshu.life").joinpath("rbi_activity_reconciliation.sql").read_text(encoding="utf-8")),
        ),
    )
    identity, sources, jobs, actions = (
        Identity(),
        Sources(),
        JobQueue(database.config.dsn),
        Actions(),
    )
    context = TrustedContext(
        "author",
        "actor",
        None,
        None,
        ("actor-scope",),
        ("actor-scope",),
        1,
        OriginKind.ACTOR,
        frozenset({"*"}),
    )
    life = LifeService(
        database,
        CommandStore(database),
        jobs,
        identity,
        Companion(),
        None,
        sources=sources,
        memory=None,
    )
    catalog = Catalog()
    handler = ReadPage()
    handler.definition = replace(handler.definition, id=capability_id)
    catalog.register(handler)
    from historical_support import adapt_models
    from tianshu.runtime.recovery_policy import RecoveryPolicy
    models = adapt_models(Models(output, lose_response=lose_response))
    life.bind_recovery_policy(RecoveryPolicy())
    catalog.planning = catalog.discover
    worker = LifeWorker(
        life, models, ContextAssembler(identity, None, None), catalog, actions, None
    )
    jobs.register("life.activity_step", worker.activity_step)
    jobs.register("life.plan", worker.plan)
    jobs.register("life.activity_reconcile", worker.reconcile_activity)
    async with database.transaction() as connection:
        await connection.execute("INSERT INTO life.actor_state(actor_id,timezone) VALUES ('actor','UTC')")
        await connection.execute(
            "INSERT INTO life.routines(actor_id,context,next_due_at,timezone,wake_at,sleep_at) VALUES ('actor',%s,now(),'UTC','00:00','23:59')",
            (json.dumps(wire(context)),),
        )
        await connection.execute(
            "INSERT INTO life.plan_watermarks(actor_id,change_watermark) VALUES ('actor','rhythm:fixture')"
        )
        data_generation = await jobs.current_generation_on(connection)
    return SimpleNamespace(
        worker=worker, models=models, actions=actions, context=context, jobs=jobs, data_generation=data_generation
    )


async def mark(database):
    async with database.transaction(read_only=True) as connection:
        return await (
            await connection.execute("SELECT * FROM life.plan_watermarks WHERE actor_id='actor'")
        ).fetchone()


def chosen_activity(capability_id="content.read"):
    return json.dumps(
        {
            "goal_changes":[],"arrangements":[],"cancel_activity_ids":[],"free_time_reason":"Leave space for rest",
            "activity": {
                "title": "Read the selected page",
                "kind": "reading",
                "goal_id": None,
                "capability_id": capability_id,
                "arguments": {"url": "https://example.test/page"},
                "duration_minutes": 30,
            },
            "reason": "A feasible activity with a concrete input",
        }
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("capability_id", ["content.read", "plugin.trailbook.observe"])
async def test_native_plan_creates_activity_then_dispatches_that_exact_action(
    database, capability_id
):
    fixture = await setup(database, chosen_activity(capability_id), capability_id=capability_id)
    await fixture.worker.plan("actor", 1, wire(fixture.context), fixture.data_generation)
    request = fixture.models.calls[0]
    assert planner.TASK in request.system
    assert not fixture.models.streams and request.max_output_tokens == 16384
    assert fixture.models.completions[request.attempt_id].task == "life.plan"
    assert planner.frozen_material(request)["routine"]["wake_at"] == "00:00"
    async with database.transaction(read_only=True) as connection:
        rows = await (await connection.execute("SELECT * FROM life.activities")).fetchall()
        queued = await (
            await connection.execute(
                "SELECT count(*) AS n FROM procrastinate_jobs WHERE task_name='life.activity_step'"
            )
        ).fetchone()
    assert len(rows) == 1 and rows[0]["state"] == "planned" and queued["n"] == 1
    assert (await mark(database))["generation_consumed"] is True
    await fixture.worker.activity_step(rows[0]["id"], 1, wire(fixture.context))
    action, dispatch = fixture.actions.calls[0]
    assert action.context == fixture.context and action.activity_id == rows[0]["id"]
    assert action.arguments == {"url": "https://example.test/page"}
    assert action.capability_id == capability_id and dispatch["background"] is True
    async with database.transaction(read_only=True) as connection:
        row = await (
            await connection.execute("SELECT * FROM life.activities WHERE id=%s", (rows[0]["id"],))
        ).fetchone()
    assert row["state"] == "dormant" and row["waiting_reason"] == "capability_running"
    assert row["action_id"] == action.action_id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "output,reason",
    [
        ("A quiet moment might be pleasant today.", "planner_output_invalid"),
        (json.dumps({"activity":None,"reason":"No feasible activity now","goal_changes":[],"arrangements":[],"cancel_activity_ids":[],"free_time_reason":"Rest"}), "no_meaningful_activity"),
    ],
)
async def test_successful_model_invalid_text_and_legitimate_null_have_distinct_states(
    database, output, reason
):
    fixture = await setup(database, output)
    await fixture.worker.plan("actor", 1, wire(fixture.context), fixture.data_generation)
    current = await mark(database)
    assert current["reason"] == reason and current["generation_consumed"] is True
    if reason == "planner_output_invalid":
        assert current["decision_fingerprint"] is None and current["last_error"] == reason
        assert current["repair_due_at"] is not None
    else:
        assert current["decision_fingerprint"] and current["review_after"] is not None
    assert fixture.models.actual.receipt.status == "succeeded"
    await fixture.worker.plan("actor", 1, wire(fixture.context), fixture.data_generation)
    assert len(fixture.models.calls) == 1
    async with database.transaction(read_only=True) as connection:
        assert not await (await connection.execute("SELECT id FROM life.activities")).fetchall()


@pytest.mark.asyncio
async def test_lost_response_and_refreshed_clock_only_observe_original_attempt(database):
    fixture = await setup(database, chosen_activity(), lose_response=True)
    with pytest.raises(TimeoutError):
        await fixture.worker.plan("actor", 1, wire(fixture.context), fixture.data_generation)
    original = fixture.models.calls[0]
    key = (await mark(database))["generation_key"]
    restarted = LifeWorker(
        fixture.worker.life,
        fixture.models,
        fixture.worker.assembler,
        fixture.worker.catalog,
        fixture.actions,
        None,
    )
    await restarted.plan("actor", 1, wire(fixture.context), fixture.data_generation)
    assert (await mark(database))["reason"] == "planner_retry_wait"
    refreshed = replace(fixture.context, access_epoch=2)
    fixture.worker.life.identity.epoch=2
    await restarted.plan("actor", 1, wire(refreshed), fixture.data_generation)
    current = await mark(database)
    assert current["generation_key"] == key and current["generation_consumed"] is True
    assert current["reason"] == "planner_retry_wait" and current["repair_due_at"] is not None
    assert len(fixture.models.calls) == 1 and not fixture.actions.calls
    assert all(attempt == original.attempt_id for attempt, _ in fixture.models.observations)
    async with database.transaction(read_only=True) as connection:
        rows = await (
            await connection.execute(
                "SELECT request,attempt_id FROM conversation.generation_requests"
            )
        ).fetchall()
    assert len(rows) == 1 and rows[0]["attempt_id"] == original.attempt_id
    assert rows[0]["request"]["context"]["access_epoch"] == 1
    assert rows[0]["request"]["budget"]["deadline_at"] == original.budget.deadline_at.isoformat()


@pytest.mark.asyncio
async def test_successful_original_plan_is_consumed_after_legal_epoch_refresh(database):
    fixture=await setup(database,chosen_activity(),lose_response=True)
    with pytest.raises(TimeoutError):
        await fixture.worker.plan('actor',1,wire(fixture.context), fixture.data_generation)
    original=fixture.models.actual
    fixture.models.actual=replace(original,receipt=replace(original.receipt,status='succeeded'))
    fixture.worker.life.identity.epoch=2
    refreshed=replace(fixture.context,access_epoch=2)
    await fixture.worker.life.signal(refreshed,'growth:updated-interest')
    await fixture.worker.plan('actor',2,wire(refreshed), fixture.data_generation)
    assert len(fixture.models.calls)==1
    assert (await mark(database))['generation_consumed'] is True
    async with database.transaction(read_only=True) as connection:
        rows=await (await connection.execute('SELECT * FROM life.activities')).fetchall()
    assert len(rows)==1 and rows[0]['context']['access_epoch']==2
    assert fixture.models.calls[0].context.access_epoch==1


@pytest.mark.asyncio
async def test_private_trigger_does_not_replace_saved_routine_authority(database):
    fixture=await setup(database,chosen_activity())
    foreign=replace(fixture.context,person_id='someone-else',conversation_id='other-private',
        scope_ids=('other-scope',),audience=('other-scope',))
    await fixture.worker.life.signal(foreign,'growth:another-private-scene')
    await fixture.worker.plan('actor',2,wire(foreign), fixture.data_generation)
    assert fixture.models.calls[0].context==fixture.context
    assert planner.frozen_material(fixture.models.calls[0])['logical_conversation_id'] is None


@pytest.mark.asyncio
async def test_direction_requires_actual_declared_milestone_and_deduplicates_progress(database):
    fixture=await setup(database,'{}')
    life=fixture.worker.life
    proof=SourceRef('experience',1)
    life.sources.record_event=AsyncMock(return_value=(SourceRef('intention',1),))
    change={'operation':'create','key':'read','id':None,'title':'Read one selected article',
        'motivation':'Understand an actual question','priority':.7,
        'criteria':{'kind':'completed_activity','capability_id':'content.read','target_count':1},
        'basis_ids':['experience'],'reason':'The observed question remains unanswered'}
    async with database.transaction() as connection:
        aliases=await life.directions.apply(connection,fixture.context,[change],
            {'basis':[{'id':'experience','source_refs':wire((proof,))}],'goals':[]},'decision',datetime.now(UTC))
        activity={'id':'actual-read','goal_id':aliases['new:read']}
        unknown=Receipt('unknown','action','attempt','unknown','unknown','not_requested')
        await life.directions.observe_result(connection,fixture.context,activity,unknown,0,'content.read',(proof,))
        row=await (await connection.execute('SELECT * FROM life.goals WHERE id=%s',(activity['goal_id'],))).fetchone()
        assert row['progress']==0 and row['state']=='active'
        result=Receipt('actual','action','attempt','succeeded','none','not_requested')
        await life.directions.observe_result(connection,fixture.context,activity,result,1,'content.read',(proof,))
        await life.directions.observe_result(connection,fixture.context,activity,result,1,'content.read',(proof,))
        row=await (await connection.execute('SELECT * FROM life.goals WHERE id=%s',(activity['goal_id'],))).fetchone()
        receipts=await (await connection.execute('SELECT count(*) AS value FROM life.goal_progress_receipts')).fetchone()
    assert row['progress']==1 and row['state']=='completed' and row['origin']=='autonomous'
    assert row['reason']=='declared_milestone_confirmed_by_receipt' and receipts['value']==1


@pytest.mark.asyncio
async def test_day_plan_leaves_free_time_and_carries_only_unstarted_work(database):
    fixture=await setup(database,'{}')
    now=datetime(2026,11,2,12,tzinfo=UTC)
    data={'unfinished':[],'commitments':[],'coverage':{}}
    decision=json.loads(chosen_activity())
    decision['arrangements']=[{**decision['activity'],'title':'Independent second read','after_minutes':70}]
    async with database.transaction() as connection:
        await fixture.worker.life.days.apply(connection,fixture.context,decision,data,'day',{},(),
            {'timezone':'UTC','wake_at':'00:00','sleep_at':'23:59','idle_seconds':5400},now,'fingerprint')
        rows=await (await connection.execute("SELECT * FROM life.day_slots WHERE kind='activity' ORDER BY starts_at")).fetchall()
        # These assertions describe focus work, while capability work defaults
        # to background execution and is intentionally allowed to cross days.
        for slot in rows:
            await connection.execute("UPDATE life.activities SET checkpoint_data=jsonb_set(checkpoint_data,'{execution_mode}',%s) WHERE id=%s",
                (document('focus'),slot['activity_id']))
        await connection.execute("UPDATE life.activities SET state='dormant',action_id='original-pending',waiting_reason='capability_running' WHERE id=%s",(rows[0]['activity_id'],))
        await connection.execute("UPDATE life.day_slots SET state='waiting_result' WHERE id=%s",(rows[0]['id'],))
        await fixture.worker.life.days.close(connection,fixture.context,now.date())
        activities=await (await connection.execute('SELECT * FROM life.activities ORDER BY id')).fetchall()
        free=await (await connection.execute("SELECT * FROM life.day_slots WHERE kind='free'")).fetchall()
    assert len(rows)==2 and free
    pending=next(row for row in activities if row['action_id'])
    carried=next(row for row in activities if not row['action_id'])
    assert pending['action_id']=='original-pending' and pending['waiting_reason']=='capability_running'
    assert carried['waiting_reason']=='day_finished' and carried['state']=='dormant'


@pytest.mark.asyncio
async def test_manually_moved_slot_cannot_cover_explicit_commitment(database):
    fixture=await setup(database,'{}')
    now=(datetime.now(UTC)+timedelta(days=1)).replace(hour=12,minute=0,second=0,microsecond=0)
    async with database.transaction() as connection:
        await fixture.worker.life.days.apply(connection,fixture.context,json.loads(chosen_activity()),
            {'unfinished':[],'commitments':[],'coverage':{}},'move-day',{},(),
            {'timezone':'UTC','wake_at':'00:00','sleep_at':'23:59','idle_seconds':5400},now,'fingerprint')
        slot=await (await connection.execute("SELECT * FROM life.day_slots WHERE kind='activity'")).fetchone()
        await connection.execute("UPDATE life.activities SET checkpoint_data=jsonb_set(checkpoint_data,'{execution_mode}',%s) WHERE id=%s",
            (document('focus'),slot['activity_id']))
        await connection.execute("INSERT INTO life.commitments(id,actor_id,title,due_at,timezone,state,scope_ids,context) VALUES ('promise','actor','An explicit promise',%s,'UTC','scheduled',ARRAY['actor-scope'],%s)",
            (now+timedelta(hours=2),json.dumps(wire(fixture.context))))
    with pytest.raises(AppError,match='day_slot_conflict'):
        await fixture.worker.life.days.move(fixture.context,{'id':slot['id'],'expected_revision':1,
            'starts_at':(now+timedelta(hours=2)).isoformat(),'client_request_id':'move-conflict'})


@pytest.mark.asyncio
async def test_rewritten_chapter_replaces_invalid_milestone_proof(database):
    fixture=await setup(database,'{}')
    life=fixture.worker.life
    life.memory=SimpleNamespace(observe_episode=AsyncMock())
    life.signal=AsyncMock()
    async with database.transaction() as connection:
        await connection.execute("INSERT INTO life.works(id,actor_id,title,scope_ids) VALUES ('work','actor','Work',ARRAY['actor-scope'])")
        await connection.execute("INSERT INTO life.chapters(id,work_id,position,title,current_revision) VALUES ('chapter','work',1,'Chapter',1)")
        await connection.execute("INSERT INTO life.goals(id,actor_id,title,motivation,state,scope_ids,criteria) VALUES ('goal','actor','Save chapter','A real work','active',ARRAY['actor-scope'],%s)",
            (json.dumps({'kind':'chapter_saved','capability_id':'writing.request','work_id':'work','target_count':1}),))
        chapter={'id':'chapter','work_id':'work','title':'Chapter','current_revision':1,'model_attempt_id':'attempt1',
            'completion_source_refs':wire((SourceRef('saved-chapter',1),))}
        await life.writing_completed(fixture.context,chapter,(SourceRef('saved-chapter',1),),connection)
        await connection.execute("UPDATE life.goal_milestones SET satisfied=false WHERE goal_id='goal'")
        await connection.execute("UPDATE life.goals SET progress=0,state='active',reason='milestone_source_unavailable' WHERE id='goal'")
        await connection.execute("UPDATE life.chapters SET current_revision=2 WHERE id='chapter'")
        chapter.update(current_revision=2,model_attempt_id='attempt2',completion_source_refs=wire((SourceRef('saved-chapter',2),)))
        await life.writing_completed(fixture.context,chapter,(SourceRef('saved-chapter',2),),connection)
        goal=await (await connection.execute("SELECT * FROM life.goals WHERE id='goal'")).fetchone()
        milestone=await (await connection.execute("SELECT * FROM life.goal_milestones WHERE goal_id='goal'")).fetchone()
    assert goal['state']=='completed' and goal['progress']==1
    assert milestone['version']==2 and milestone['satisfied'] and milestone['evidence_id']=='attempt2'
    assert goal['source_refs']==wire((SourceRef('saved-chapter',2),))


@pytest.mark.asyncio
async def test_late_background_result_does_not_clear_new_current_activity(database):
    fixture=await setup(database,'{}')
    life=fixture.worker.life
    life.sources.record_event=AsyncMock(return_value=(SourceRef('actual-completion',1),))
    life.memory=SimpleNamespace(observe_episode=AsyncMock())
    life.signal=AsyncMock()
    async with database.transaction() as connection:
        await connection.execute("INSERT INTO life.activities(id,actor_id,title,kind,state,action_id,last_result_revision,scope_ids) "
            "VALUES ('background','actor','Read actual page','reading','dormant','original-action',0,ARRAY['actor-scope']),"
            "('new-current','actor','Quiet reflection','reflection','running',NULL,-1,ARRAY['actor-scope'])")
        await connection.execute("UPDATE life.actor_state SET activity_id='new-current' WHERE actor_id='actor'")
    result=Receipt('actual-receipt','original-action','attempt','succeeded','none','not_requested',
        result={'summary':'Actual read result','read_basis':'actual_source_body'})
    await fixture.worker._activity_result(fixture.context,result,1,datetime.now(UTC),'content.read')
    async with database.transaction(read_only=True) as connection:
        state=await (await connection.execute("SELECT * FROM life.actor_state WHERE actor_id='actor'")).fetchone()
        row=await (await connection.execute("SELECT * FROM life.activities WHERE id='background'")).fetchone()
    assert state['activity_id']=='new-current' and row['state']=='completed'
    assert row['checkpoint_data']['completed_action_id']=='original-action'
    assert life.memory.observe_episode.call_args.args[1]['actual_result']['execution']=='succeeded'


@pytest.mark.asyncio
async def test_recovery_does_not_enqueue_blocked_routine_context(database):
    fixture = await setup(database, chosen_activity())

    async def denied(*_args):
        raise AppError("access_changed", status=409)

    fixture.worker.life.identity.check = denied
    await fixture.worker.recover()
    async with database.transaction(read_only=True) as connection:
        assert not await (
            await connection.execute(
                "SELECT id FROM procrastinate_jobs WHERE task_name='life.plan'"
            )
        ).fetchall()
    assert not fixture.models.calls and not fixture.actions.calls


@pytest.mark.asyncio
async def test_imagined_completion_registers_actual_actor_original_in_same_transaction(database):
    fixture = await setup(database, '{}')
    original = SourceRef("imagined-completion", 1)
    fixture.worker.life.sources.record_event = AsyncMock(return_value=(original,))
    fixture.worker.life.memory = SimpleNamespace(observe_episode=AsyncMock())
    fixture.worker.life.signal = AsyncMock()
    async with database.transaction() as connection:
        await connection.execute("INSERT INTO life.activities(id,actor_id,title,kind,state,waiting_reason,next_due_at,scope_ids) "
            "VALUES ('imagination','actor','在房间整理书架','room','running','fictional_activity',now()-interval '1 second',ARRAY['actor-scope'])")
    await fixture.worker.activity_step("imagination", 1, wire(fixture.context))
    call = fixture.worker.life.sources.record_event.call_args
    assert call.kwargs["origin_kind"] == OriginKind.ACTOR
    assert call.kwargs["connection"] is not None
    assert call.kwargs["parts"][0].metadata["imagined"] is True
    episode = fixture.worker.life.memory.observe_episode.call_args.args[1]
    assert original in episode["source_refs"]
    assert episode["origin_kind"] == "actor"
    assert episode["actual_result"]["experience_kind"] == "imagined"
    assert episode["actual_result"]["software_action"] is False
    assert "execution" not in episode["actual_result"]


@pytest.mark.asyncio
async def test_web_pagination_continuation_resumes_the_exact_returned_arguments(database):
    fixture = await setup(database, '{}')
    checkpoint = {"capability_id": "content.read", "arguments": {"url": "https://example.invalid/1"},
        "continuation": {"capability_id": "content.read", "arguments": {"url": "https://example.invalid/2", "mode": "continue"}}}
    async with database.transaction() as connection:
        await connection.execute("INSERT INTO life.activities(id,actor_id,title,kind,state,waiting_reason,checkpoint_data,scope_ids) "
            "VALUES ('reading','actor','继续阅读','reading','dormant','continuation_available',%s,ARRAY['actor-scope'])", (json.dumps(checkpoint),))
    await fixture.worker.life.transition_activity(fixture.context,
        {"id": "reading", "expected_revision": 1, "client_request_id": "continue-page"}, "running")
    async with database.transaction(read_only=True) as connection:
        row = await (await connection.execute("SELECT checkpoint_data,action_id FROM life.activities WHERE id='reading'")).fetchone()
    assert row["checkpoint_data"]["arguments"] == checkpoint["continuation"]["arguments"]
    assert row["action_id"] is None


@pytest.mark.asyncio
async def test_action_receipt_original_keeps_actual_consumed_source_and_execution():
    original, consumed = SourceRef("actual-receipt", 1), SourceRef("video-original", 2)
    sources = SimpleNamespace(record_event=AsyncMock(return_value=(original,)))
    worker = LifeWorker.__new__(LifeWorker)
    worker.life = SimpleNamespace(sources=sources)
    result = Receipt("receipt", "action", "attempt", "succeeded", "none", "not_requested",
        result={"consumption": {"source": wire(consumed), "analysis_completed": True}})
    context, connection = object(), object()
    refs = await worker._receipt_sources(context, result, 4, datetime.now(UTC), connection, ())
    assert refs == (consumed, original)
    call = sources.record_event.call_args
    assert call.kwargs["origin_kind"] == OriginKind.TOOL_RESULT
    assert call.kwargs["support_refs"] == (consumed,)
    assert call.kwargs["connection"] is connection
    metadata = call.kwargs["parts"][0].metadata
    assert metadata["execution"] == "succeeded" and metadata["receipt_id"] == "receipt"


def test_complete_physical_request_with_truncated_text_cannot_become_authored_draft_or_plan():
    from tianshu.life.writing import generated_text, generation_error
    from tianshu.models.types import ModelCompletion

    result = ModelResult((ModelPart("text", text='{"activity":null,"reason":"partial"}'),),
        ModelReceipt("a", "i", "succeeded", "submitted", "r", 1, "p", "m", "openai.chat", "q"),
        completion=ModelCompletion("incomplete", ("length",)))
    assert generated_text(result) == ""
    assert generation_error(result, "fallback") == "generation_content_incomplete"
    with pytest.raises(AppError) as rejected:
        planner.decision(result, None)
    assert rejected.value.code == "planner_output_incomplete"
