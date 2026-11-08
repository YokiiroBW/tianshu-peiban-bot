from datetime import UTC, datetime

import pytest

from historical_support import planner_setup
from test_goal_source_transaction import source_life
from test_planner_execution import mark
from tianshu.runtime.serialization import wire
from tianshu.conversation.types import InputPart
from tianshu.life.contacts import Contacts
from tianshu.runtime.errors import AppError
from tianshu.types import OriginKind


async def test_routine_resolves_opted_in_private_target_without_reading_its_scene(source_life):
    value=source_life
    context=value.context
    async with value.database.transaction() as conn:
        await conn.execute("INSERT INTO identity.scopes(scope_id,actor_id,person_id,kind,label) "
            "VALUES('contact-private',%s,'owner-person','private','private')",(context.actor_id,))
        await conn.execute("INSERT INTO identity.scope_grants(scope_id,principal_id,permissions) "
            "VALUES('contact-private','owner',ARRAY['*'])")
        await conn.execute("INSERT INTO identity.conversations(conversation_id,actor_id,scope_id,person_id,kind,label) "
            "VALUES('contact-chat',%s,'contact-private','owner-person','private','private')",(context.actor_id,))
    private=await value.identity.authorize_conversation(context,"contact-chat")
    assert private.delivery_allowed and "contact-private" not in context.scope_ids
    async with value.database.transaction() as conn:
        await conn.execute("INSERT INTO life.contact_preferences(actor_id,person_id,scope_ids,enabled) "
            "VALUES(%s,'owner-person',%s,true)",(context.actor_id,list(private.scope_ids)))
    contacts=Contacts(value.database)
    target=await contacts.resolve_target(context,identity=value.identity)
    assert target.conversation_id=="contact-chat" and context.conversation_id is None
    secret=await value.sources.record_event(private,producer="test",event_id="private-material",revision=1,
        occurred_at=datetime.now(UTC),origin_kind=OriginKind.HUMAN,parts=(InputPart("text","private material"),))
    with pytest.raises(AppError,match="source_unavailable"):
        await contacts.resolve_target(context,secret,identity=value.identity)
    async with value.database.transaction() as conn:
        await conn.execute("UPDATE life.contact_preferences SET enabled=false WHERE actor_id=%s",(context.actor_id,))
    assert await contacts.resolve_target(context,identity=value.identity) is None


async def test_preparation_failure_keeps_successful_head_and_can_prepare_after_config_change(database):
    import json
    output=json.dumps({"activity":None,"arrangements":[],"goal_changes":[],"cancel_activity_ids":[],
                       "reason":"rest","free_time_reason":"leave space"})
    value=await planner_setup(database,output)
    await value.worker.plan("actor",1,wire(value.context),value.data_generation)
    first=await mark(database)
    assert first["generation_consumed"]
    profile=value.models.generation_profile
    async def unavailable(request):
        return {**await profile(request),"verified_capabilities":[]}
    value.models.generation_profile=unavailable
    await value.worker.life.signal(value.context,"user:changed")
    await value.worker.plan("actor",None,None,value.data_generation)
    blocked=await mark(database)
    assert blocked["reason"]=="planner_model_unavailable" and blocked["last_error"]=="planner_model_unavailable"
    assert blocked["generation_key"]==first["generation_key"] and blocked["generation_consumed"]
    assert blocked["failed_generation_key"] is None and blocked["repair_due_at"] is not None
    async with database.transaction(read_only=True) as conn:
        old=await(await conn.execute("SELECT state,reason FROM life.plan_generations WHERE generation_key=%s",
            (first["generation_key"],))).fetchone()
    assert old["state"]=="applied" and len(value.models.calls)==1
    value.models.generation_profile=profile
    # The scheduled retry must recover without requiring another user event.
    async with database.transaction() as conn:
        await conn.execute("UPDATE life.plan_watermarks SET repair_due_at=now()-interval '1 second',"
                           "next_due_at=now()-interval '1 second'")
    await value.worker.plan("actor",None,None,value.data_generation)
    final=await mark(database)
    assert final["last_error"] is None and final["generation_key"]!=first["generation_key"]
    assert len(value.models.calls)==2
