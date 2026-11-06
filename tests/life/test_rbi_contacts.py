"""Delivery projection can share the fenced expression transaction."""
from dataclasses import replace

import pytest

from test_rbf_planning_recovery import make_life
from tianshu.conversation.types import ExpressionPlan
from tianshu.types import OriginKind, Receipt


async def test_delivery_projection_rolls_back_with_caller_and_stays_idempotent(database):
    value = await make_life(database)
    context = replace(value.context,conversation_id="chat")
    plan = ExpressionPlan("expression","actor","chat",({"kind":"text","text":"hello"},),(),OriginKind.ACTOR)
    result = Receipt("receipt","action","attempt","succeeded","confirmed","delivered")
    with pytest.raises(RuntimeError,match="rollback"):
        async with database.transaction() as connection:
            await value.worker.life.contacts.record_delivery(context,plan,result,connection=connection)
            raise RuntimeError("rollback")
    async with database.transaction(read_only=True) as connection:
        count = await (await connection.execute("SELECT count(*) AS n FROM life.contacts")).fetchone()
    assert count["n"] == 0
    await value.worker.life.contacts.record_delivery(context,plan,result)
    await value.worker.life.contacts.record_delivery(context,plan,result)
    async with database.transaction(read_only=True) as connection:
        rows = await (await connection.execute("SELECT expression_id,receipt FROM life.contacts")).fetchall()
    assert len(rows) == 1 and rows[0]["expression_id"] == "expression" and rows[0]["receipt"]["receipt_id"] == "receipt"
