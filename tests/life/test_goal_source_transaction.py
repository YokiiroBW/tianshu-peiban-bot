"""Actual PG source visibility and authorization at Life's completion commit."""
from datetime import UTC, datetime
from importlib.resources import files
from types import SimpleNamespace

import pytest
import pytest_asyncio

from tianshu.conversation.codec import document, source_refs
from tianshu.conversation.types import InputPart
from tianshu.identity.service import IdentityService
from tianshu.life.service import LifeService
from tianshu.life.worker import LifeWorker
from tianshu.runtime.commands import CommandStore
from tianshu.runtime.files import FileStore
from tianshu.runtime.jobs import JobQueue
from tianshu.runtime.migrations import Migration, migrate
from tianshu.runtime.serialization import wire
from tianshu.sources.service import SourceService
from tianshu.types import Coverage, OriginKind, Receipt


@pytest_asyncio.fixture
async def source_life(database, tmp_path):
    await migrate(database, (
        Migration('0010_identity',files('tianshu.identity').joinpath('schema.sql').read_text()),
        Migration('0011_sources',files('tianshu.sources').joinpath('schema.sql').read_text()
            +'\n'+files('tianshu.sources').joinpath('functions.sql').read_text()),
        Migration('0031_life',files('tianshu.life').joinpath('schema.sql').read_text()),
    ))
    commands,jobs=CommandStore(database),JobQueue(database.config.dsn)
    identity=IdentityService(database,commands)
    sources=SourceService(database,identity,FileStore(tmp_path/'files'),commands,jobs)
    identity.bind_sources(sources)
    async with database.transaction() as connection:
        await connection.execute("INSERT INTO identity.people(person_id,display_name) VALUES ('owner-person','Test owner')")
        await connection.execute("INSERT INTO identity.principals(principal_id,username,display_name,person_id,permissions) "
            "VALUES ('owner','owner','Test owner','owner-person',ARRAY['*'])")
    owner=await identity.context('owner')
    async with database.transaction() as connection:
        actor=await identity.register_actor(owner,connection=connection)
    context=await identity.context('owner',actor_id=actor,origin_kind=OriginKind.ACTOR)
    episodes=[]

    async def observe_episode(current,episode,*,connection):
        refs=tuple(episode['source_refs'])
        assert await sources.validate_persistence(connection,current,refs)
        # Deliberate regression witness: another PG reader cannot authorize the
        # new completion original before the owning Life transaction commits.
        visible_elsewhere=(await identity.check_many(current,(refs,)))[0]
        episodes.append((episode,visible_elsewhere))

    life=LifeService(database,commands,jobs,identity,None,None,sources=sources,
        memory=SimpleNamespace(observe_episode=observe_episode))
    worker=LifeWorker(life,None,None,None,None,None)
    jobs.register('life.plan',worker.plan)
    async with database.transaction() as connection:
        await connection.execute("INSERT INTO life.actor_state(actor_id) VALUES (%s)",(actor,))
    return SimpleNamespace(database=database,life=life,worker=worker,identity=identity,
        sources=sources,context=context,episodes=episodes)


@pytest.mark.asyncio
@pytest.mark.parametrize('visible_to_goal',[True,False])
async def test_new_receipt_original_advances_only_authorized_goal_in_same_transaction(source_life,visible_to_goal):
    fixture=source_life
    context=fixture.context
    goal_context=context
    if not visible_to_goal:
        async with fixture.database.transaction(read_only=True) as connection:
            public=await (await connection.execute("SELECT scope_id FROM identity.scopes WHERE actor_id=%s AND kind='public'",(context.actor_id,))).fetchone()
        from dataclasses import replace
        goal_context=replace(context,scope_ids=(public['scope_id'],),audience=(public['scope_id'],))
    page=await fixture.sources.record_event(context,producer='test.actual-page',event_id='page',revision=1,
        occurred_at=datetime.now(UTC),origin_kind=OriginKind.TOOL_RESULT,
        parts=(InputPart('text','A real stored source body, consumed by the completed action.'),))
    assert (await fixture.identity.check_many(context,(page,)))[0]
    async with fixture.database.transaction() as connection:
        await connection.execute("INSERT INTO life.goals(id,actor_id,title,motivation,state,scope_ids,criteria) "
            "VALUES ('goal',%s,'Finish the selected page','A concrete actual question','active',%s,%s)",
            (context.actor_id,list(context.scope_ids),document({'kind':'completed_activity','capability_id':'content.read','target_count':1})))
        await connection.execute("INSERT INTO life.activities(id,actor_id,goal_id,title,kind,state,action_id,last_result_revision,scope_ids,checkpoint_data,context) "
            "VALUES ('reading',%s,'goal','Read the selected page','reading','dormant','original-action',1,%s,%s,%s)",
            (context.actor_id,list(context.scope_ids),document({'capability_id':'content.read','planning_context':wire(goal_context)}),document(context)))
    result=Receipt('actual-receipt','original-action','attempt','succeeded','none','not_requested',
        coverage=Coverage(True),result={'read_basis':'actual_source_body','source':wire(page[0]),
            'source_refs':wire(page),'content':{'text':'A real stored source body.'},'summary':'The selected page was actually read.'})
    await fixture.worker._activity_result(context,result,2,datetime.now(UTC),'content.read')
    await fixture.worker._activity_result(context,result,2,datetime.now(UTC),'content.read')
    async with fixture.database.transaction(read_only=True) as connection:
        goal=await (await connection.execute("SELECT * FROM life.goals WHERE id='goal'")).fetchone()
        proofs=await (await connection.execute("SELECT * FROM life.goal_progress_receipts WHERE goal_id='goal'")).fetchall()
        activity=await (await connection.execute("SELECT * FROM life.activities WHERE id='reading'")).fetchone()
        original=await (await connection.execute("SELECT * FROM sources.versions WHERE evidence_unit='life:action:result:original-action'")).fetchone()
    assert activity['state']=='completed' and activity['last_result_revision']==2
    assert original and original['revision']==2 and original['origin_kind']=='tool_result'
    assert len(fixture.episodes)==1 and fixture.episodes[0][1] is False
    assert len(proofs)==int(visible_to_goal)
    assert goal['progress']==int(visible_to_goal)
    assert goal['state']==('completed' if visible_to_goal else 'active')
    if visible_to_goal:
        assert proofs[0]['action_id']=='original-action' and proofs[0]['result_revision']==2
        assert source_refs(proofs[0]['source_refs'])==source_refs(activity['source_refs'])
        assert (await fixture.identity.check_many(goal_context,(source_refs(goal['source_refs']),)))[0]
    else:
        assert not (await fixture.identity.check_many(goal_context,(source_refs(activity['source_refs']),)))[0]


@pytest.mark.asyncio
async def test_new_chapter_original_and_current_revision_are_consumed_on_writer_connection(source_life):
    fixture=source_life
    context=fixture.context
    async with fixture.database.transaction() as connection:
        await connection.execute("INSERT INTO life.works(id,actor_id,title,scope_ids) VALUES ('work',%s,'Work',%s)",
            (context.actor_id,list(context.scope_ids)))
        await connection.execute("INSERT INTO life.goals(id,actor_id,title,motivation,state,scope_ids,criteria) "
            "VALUES ('chapter-goal',%s,'Save a chapter','Continue the actual work','active',%s,%s)",
            (context.actor_id,list(context.scope_ids),document({'kind':'chapter_saved','capability_id':'writing.request','work_id':'work','target_count':1})))
    async with fixture.database.transaction() as connection:
        proof=await fixture.sources.record_event(context,producer='life.chapter.generated',event_id='chapter',revision=1,
            occurred_at=datetime.now(UTC),origin_kind=OriginKind.ACTOR,connection=connection,
            parts=(InputPart('text','Chapter revision saved',metadata={'chapter_id':'chapter','work_id':'work','content_revision':1}),))
        await connection.execute("INSERT INTO life.chapters(id,work_id,position,title,current_revision,state) VALUES ('chapter','work',1,'Chapter',1,'draft')")
        await connection.execute("INSERT INTO life.chapter_revisions(chapter_id,revision,content,work_revision,model_attempt_id,recipe) "
            "VALUES ('chapter',1,'The saved draft body.',1,'chapter-attempt',%s)",(document({'source_refs':wire(proof)}),))
        chapter={'id':'chapter','work_id':'work','title':'Chapter','current_revision':1,'model_attempt_id':'chapter-attempt',
            'completion_source_refs':wire(proof)}
        await fixture.life.writing_completed(context,chapter,proof,connection)
        goal=await (await connection.execute("SELECT * FROM life.goals WHERE id='chapter-goal'")).fetchone()
        milestones=await (await connection.execute("SELECT * FROM life.goal_milestones WHERE goal_id='chapter-goal'")).fetchall()
        assert goal['progress']==1 and goal['state']=='completed'
        assert len(milestones)==1 and milestones[0]['version']==1
        assert source_refs(milestones[0]['source_refs'])==proof
    assert len(fixture.episodes)==1 and fixture.episodes[0][1] is False
    assert (await fixture.identity.check_many(context,(source_refs(goal['source_refs']),)))[0]
