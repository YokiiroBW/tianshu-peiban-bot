"""A repair targets the original Life owner and never invents old proof."""
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from test_rbi_result_atomicity import result_fixture, state
from tianshu.conversation.codec import document
from tianshu.runtime.errors import AppError


@pytest.mark.parametrize("kind",["activity","commitment"])
async def test_repair_preflight_resolves_owner_and_keeps_unproven_receipt_unknown(database,kind):
    value = await result_fixture(database,kind)
    table,owner = ("life.activities","activity-1") if kind == "activity" else ("life.commitments","promise")
    async def inspect(owner_id):
        async with database.transaction() as connection:
            return await value.worker.inspect_result_repair_on(connection,value.context,value.original,1,
                consumer="life_"+kind,owner_id=owner_id,data_generation=value.data_generation)
    pending = await inspect(None)
    assert pending["state"] == "not_applied" and pending["owner_id"] == owner
    async with database.transaction() as connection:
        await connection.execute(f"UPDATE {table} SET last_result_revision=1,last_receipt_id='original-receipt' WHERE id=%s",(owner,))
    result = await inspect(owner)
    assert result["state"] == "unknown" and result["reason"] == "local_result_application_unconfirmed"
    async def complete(*_args,**_kwargs):
        pytest.fail("the Life row cannot manufacture consumer proof")
    complete.fence,complete.source_refs = AsyncMock(),()
    method = value.worker._activity_result if kind == "activity" else value.worker._commitment_result
    with pytest.raises(AppError,match="local_result_application_unconfirmed"):
        await method(value.context,value.original,1,datetime.now(UTC),"content.read",on_complete=complete,
            expected_owner_id=owner,data_generation=value.data_generation)
    domain,_,episodes = await state(database,kind)
    assert domain["last_receipt_id"] == "original-receipt" and episodes == 0


@pytest.mark.parametrize("kind",["activity","commitment"])
async def test_repair_does_not_switch_owner_after_action_replacement(database,kind):
    value = await result_fixture(database,kind)
    table,owner = ("life.activities","activity-1") if kind == "activity" else ("life.commitments","promise")
    async with database.transaction() as connection:
        await connection.execute(f"UPDATE {table} SET action_id='replacement' WHERE id=%s",(owner,))
        inspected = await value.worker.inspect_result_repair_on(connection,value.context,value.original,1,
            consumer="life_"+kind,owner_id=owner,data_generation=value.data_generation)
    assert inspected["state"] == "superseded" and inspected["owner_id"] == owner
    async def complete(*_args,**kwargs):
        assert kwargs["reason"].endswith("action_replaced")
    complete.fence,complete.source_refs = AsyncMock(),()
    method = value.worker._activity_result if kind == "activity" else value.worker._commitment_result
    await method(value.context,value.original,1,datetime.now(UTC),"content.read",on_complete=complete,
        expected_owner_id=owner,data_generation=value.data_generation)
    domain,_,episodes = await state(database,kind)
    assert domain["last_result_revision"] == -1 and episodes == 0


async def test_missing_and_ambiguous_owner_remain_unknown(database):
    value = await result_fixture(database,"activity")
    async with database.transaction() as connection:
        missing = await value.worker.inspect_result_repair_on(connection,value.context,value.original,1,
            consumer="life_activity",owner_id="missing",data_generation=value.data_generation)
        await connection.execute("INSERT INTO life.activities(id,actor_id,title,kind,state,action_id,scope_ids,context) "
            "VALUES ('second','actor','second','reading','dormant',%s,ARRAY['actor-scope'],%s)",
            (value.action.action_id,document(value.context)))
        ambiguous = await value.worker.inspect_result_repair_on(connection,value.context,value.original,1,
            consumer="life_activity",owner_id=None,data_generation=value.data_generation)
    assert missing["state"] == "unknown" and missing["reason"] == "life_result_owner_missing"
    assert ambiguous["state"] == "unknown" and ambiguous["reason"] == "life_result_owner_ambiguous"
