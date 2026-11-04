from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from tianshu.life.contacts import Contacts
from test_planner_execution import setup


@pytest.mark.asyncio
async def test_received_reuses_outer_transaction_and_rolls_back_with_input(database):
    value = await setup(database, "{}")
    context = replace(value.context, person_id="person", conversation_id="chat")
    received_at = datetime.now(UTC)
    async with database.transaction() as connection:
        await connection.execute("INSERT INTO life.contacts(expression_id,actor_id,person_id,conversation_id,delivered_at,signature,receipt) "
            "VALUES ('sent','actor','person','chat',%s,'{}','{}')", (received_at - timedelta(minutes=1),))
    contacts = Contacts(SimpleNamespace(transaction=Mock(side_effect=AssertionError("unexpected nested transaction"))))
    contacts.on_received = AsyncMock()

    class Rollback(Exception):
        pass

    with pytest.raises(Rollback):
        async with database.transaction() as connection:
            await contacts.received(context, received_at, input_id="input", connection=connection)
            await contacts.received(context, received_at, input_id="input", connection=connection)
            assert contacts.on_received.await_count == 1
            assert contacts.on_received.await_args.kwargs["connection"] is connection
            raise Rollback()
    async with database.transaction(read_only=True) as connection:
        receipt = await (await connection.execute("SELECT * FROM life.contact_replies WHERE input_id='input'")).fetchone()
        sent = await (await connection.execute("SELECT replied_at FROM life.contacts WHERE expression_id='sent'")).fetchone()
    assert receipt is None and sent["replied_at"] is None


@pytest.mark.asyncio
async def test_received_opens_its_own_transaction_when_called_independently(database):
    value = await setup(database, "{}")
    contacts = Contacts(database)
    contacts.on_received = AsyncMock()
    await contacts.received(value.context, input_id="standalone")
    async with database.transaction(read_only=True) as connection:
        receipt = await (await connection.execute("SELECT input_id FROM life.contact_replies WHERE input_id='standalone'")).fetchone()
    assert receipt["input_id"] == "standalone" and contacts.on_received.await_count == 1
