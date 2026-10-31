from datetime import UTC,datetime,timedelta

import pytest

from tianshu.runtime.errors import AppError
from tianshu.types import OriginKind,RequestBudget,SourceRef
from tianshu.identity.types import IncomingEvent


async def record(core,context,event,text):
    return await core.sources.record_event(context,producer="web",event_id=event,revision=1,
        occurred_at=datetime.now(UTC),parts=({"kind":"text","text":text},),origin_kind=OriginKind.HUMAN)


async def claim(core,context,refs,value="苹果"):
    async with core.database.transaction() as conn:
        return await core.claims.put_on(conn,context,{"subject_id":context.person_id,"predicate":"interest","value":value,
            "assertion":"self_confirmed","source_refs":refs},automatic=True)


async def test_shared_interest_keeps_private_original_separate(core,ordinary_users):
    alice,bob=ordinary_users
    private=await record(core,alice,"alice-interest","我喜欢苹果。私密经历：只有我知道的内容。")
    original=await claim(core,alice,private)
    with pytest.raises(AppError):
        await core.sources.read(bob,private[0],{})
    published=await core.claims.share_ordinary(alice,{"claim_id":original.claim_id,"expected_revision":original.revision,"client_request_id":"share"})
    shared=await core.claims.get(bob,published.data.claim_id)
    assert shared.value=="苹果" and shared.source_refs!=private
    text=await core.sources.read(bob,shared.source_refs[0],{})
    assert "苹果" in text["content"] and "私密经历" not in text["content"]
    with pytest.raises(AppError):
        await core.sources.read(bob,private[0],{})
    await core.sources.invalidate(alice,{"source_id":private[0].source_id,"expected_revision":1,"client_request_id":"forget"},forgotten=True)
    with pytest.raises(AppError):
        await core.claims.get(bob,shared.claim_id)


async def test_alternative_support_and_nonoverlapping_fragment_survive(core,ordinary_users):
    alice,_=ordinary_users
    first=await record(core,alice,"first","喜欢苹果；不相干的私人句子。")
    second=await record(core,alice,"second","我还是喜欢苹果。")
    first_span=(SourceRef(first[0].source_id,1,{"start":0,"end":4}),)
    initial=await claim(core,alice,first_span)
    supported=await claim(core,alice,second)
    assert initial.claim_id==supported.claim_id
    await core.sources.forget_fragment(alice,{"source_id":first[0].source_id,"expected_revision":1,"start":5,"end":10,"client_request_id":"fragment"})
    assert (await core.claims.get(alice,initial.claim_id)).value=="苹果"
    await core.sources.invalidate(alice,{"source_id":second[0].source_id,"expected_revision":1,"client_request_id":"second-forget"},forgotten=True)
    surviving=await core.claims.get(alice,initial.claim_id)
    assert surviving.source_refs==first_span


async def test_incremental_watermark_and_unknown_coverage(core,ordinary_users):
    alice,bob=ordinary_users
    refs=await record(core,alice,"episode-source","今天安排了旅行。")
    first=await core.memory.observe_episode(alice,{"event_id":"episode","occurred_at":datetime.now(UTC),"summary":"安排旅行",
        "source_refs":refs,"scope_ids":bob.scope_ids,"origin_kind":"human"})
    page=await core.memory.episodes_since(alice,None,10)
    assert page.next_cursor is None and page.watermark==first.cursor
    assert not first.coverage.complete and "coverage_unknown" in first.coverage.missing
    assert set(first.scope_ids)<=set(alice.scope_ids) and not set(first.scope_ids)<=set(bob.scope_ids)
    again=await core.memory.episodes_since(alice,page.watermark,10)
    assert not again.items and again.watermark==page.watermark


async def test_authorized_candidates_are_filtered_before_ranking(core,ordinary_users):
    alice,bob=ordinary_users
    secret=await record(core,alice,"private-ranking","苹果 苹果 苹果 苹果 苹果 极私密")
    await claim(core,alice,secret,"苹果 苹果 苹果 极私密")
    public=await record(core,bob,"own-ranking","苹果")
    own=await claim(core,bob,public)
    material,_=await core.retrieval.search(bob,"苹果",RequestBudget(datetime.now(UTC)+timedelta(seconds=10)),limit=1)
    assert len(material)==1 and "极私密" not in material[0].text
    assert material[0].source_refs==own.source_refs


async def test_publication_does_not_grant_private_context_or_life_management(core,ordinary_users):
    alice,bob=ordinary_users
    refs=await record(core,alice,"private-diary","完整私密日记材料")
    published=await core.sources.disclose(alice,producer="diary",event_id="day",revision=1,
        parts=({"kind":"text","text":"仅公开的一小段"},),source_refs=refs,reader_ids=(bob.principal_id,))
    reader=await core.identity.context(bob.principal_id,actor_id=core.actor)
    assert (await core.sources.read(reader,published,{}))["content"]=="仅公开的一小段"
    with pytest.raises(AppError):
        await core.sources.read(reader,refs[0],{})
    with pytest.raises(AppError):
        await core.identity.context(bob.principal_id,actor_id=core.actor,permission="life.manage")


async def test_real_group_admission_and_private_common_projection(core,ordinary_users):
    alice,_=ordinary_users
    await core.management.execute(core.owner,"connections.save",{"connection_id":"qq","actor_id":core.actor,
        "namespace":"onebot","self_external_id":"999","expected_revision":0,"client_request_id":"qq-create"})
    await core.management.execute(core.owner,"accounts.link",{"connection_id":"qq","external_id":"1001",
        "person_id":alice.person_id,"principal_id":alice.principal_id,"expected_revision":0,"client_request_id":"qq-link"})
    link=await core.management.execute(core.owner,"conversations.link",{"connection_id":"qq","external_id":"99",
        "kind":"group","mode":"reply","label":"群聊","target_ref":"group:99","expected_revision":0,"client_request_id":"group"})
    async with core.database.transaction(read_only=True) as conn:
        scope=await (await conn.execute("SELECT s.scope_id,s.access_epoch FROM identity.scopes s JOIN identity.conversations c USING(scope_id) "
            "WHERE c.conversation_id=%s",(link.data["conversation_id"],))).fetchone()
    await core.management.execute(core.owner,"scopes.grant",{"scope_id":scope["scope_id"],"principal_id":alice.principal_id,
        "permissions":["read"],"expected_revision":scope["access_epoch"],"client_request_id":"group-grant"})
    admitted=await core.identity.admit_event("qq",IncomingEvent("onebot:qq","message:group:1",1,datetime.now(UTC),"99","group","1001",
        parts=({"kind":"text","text":"我喜欢苹果"},)))
    assert admitted.disposition=="reply" and admitted.context.principal_id==alice.principal_id
    assert "*" not in admitted.context.permissions and "config.publish" not in admitted.context.permissions
    refs=await core.sources.record_event(admitted.context,producer="onebot:qq",event_id="message:group:1",revision=1,
        occurred_at=datetime.now(UTC),parts=({"kind":"text","text":"我喜欢苹果"},),origin_kind=OriginKind.HUMAN)
    item=await claim(core,admitted.context,refs)
    shared=await core.claims.share_ordinary(admitted.context,{"claim_id":item.claim_id,"expected_revision":item.revision,"client_request_id":"share-group-interest"})
    private=await core.identity.context(alice.principal_id,actor_id=core.actor,conversation_id=alice.conversation_id)
    assert (await core.claims.get(private,shared.data.claim_id)).value=="苹果"
    with pytest.raises(AppError):
        await core.sources.read(private,refs[0],{})
    async with core.database.transaction() as conn:
        other_actor=await core.identity.register_actor(core.owner,connection=conn)
    with pytest.raises(AppError):
        await core.identity.context(alice.principal_id,actor_id=other_actor)
