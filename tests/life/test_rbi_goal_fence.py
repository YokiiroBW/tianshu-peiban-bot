"""A concurrent Goal edit is reloaded after source prelock and Action fence."""
from datetime import UTC, datetime

from test_goal_source_transaction import source_life
from tianshu.conversation.codec import document
from tianshu.conversation.types import InputPart
from tianshu.runtime.serialization import wire
from tianshu.types import Coverage, OriginKind, Receipt


async def test_goal_sources_are_prelocked_and_changed_goal_is_retried(source_life):
    value,context = source_life,source_life.context
    refs = await value.sources.record_event(context,producer="test.goal",event_id="source",revision=1,
        occurred_at=datetime.now(UTC),origin_kind=OriginKind.TOOL_RESULT,parts=(InputPart("text","goal source"),))
    async with value.database.transaction() as connection:
        await connection.execute("INSERT INTO life.goals(id,actor_id,title,motivation,state,scope_ids,source_refs,criteria) "
            "VALUES ('goal',%s,'Read','Actual goal','active',%s,%s,%s)",
            (context.actor_id,list(context.scope_ids),document(refs),
             document({"kind":"completed_activity","capability_id":"content.read","target_count":1})))
        await connection.execute("INSERT INTO life.activities(id,actor_id,goal_id,title,kind,state,action_id,"
            "last_result_revision,scope_ids,checkpoint_data,context) VALUES ('activity',%s,'goal','Read',"
            "'reading','dormant','action',0,%s,%s,%s)",
            (context.actor_id,list(context.scope_ids),document({"capability_id":"content.read",
                "planning_context":wire(context)}),document(context)))
    original_prepare = value.sources.prepare_event_writes
    prepared, fences, completions = [], [], []
    async def prepare(connection,current,events,*,additional_refs=(),scoped_refs=()):
        prepared.append(scoped_refs)
        return await original_prepare(connection,current,events,additional_refs=additional_refs,scoped_refs=scoped_refs)
    value.sources.prepare_event_writes = prepare
    async def complete(_connection,disposition="done",*,reason=None,proof=None):
        completions.append((disposition,proof))
    async def fence(_connection):
        assert prepared and any(tuple(group_refs) == tuple(refs) for _,group_refs in prepared[-1])
        fences.append(1)
        if len(fences) == 1:
            async with value.database.transaction() as concurrent:
                await concurrent.execute("UPDATE life.goals SET revision=revision+1 WHERE id='goal'")
    complete.fence,complete.source_refs = fence,()
    result = Receipt("receipt","action","attempt","succeeded","none","not_requested",
        coverage=Coverage(True),result={"read_basis":"actual_source_body","source_refs":wire(refs),
            "source":wire(refs[0]),"summary":"Read the source"})
    await value.worker._activity_result(context,result,1,datetime.now(UTC),"content.read",on_complete=complete)
    async with value.database.transaction(read_only=True) as connection:
        goal = await (await connection.execute("SELECT revision,state FROM life.goals WHERE id='goal'")).fetchone()
        proof = await (await connection.execute("SELECT count(*) AS n FROM life.goal_progress_receipts WHERE goal_id='goal'")).fetchone()
    assert len(fences) == 2 and len(completions) == 1
    assert completions[0][0] == "done" and completions[0][1]["last_receipt_id"] == "receipt"
    assert goal["state"] == "completed" and proof["n"] == 1
