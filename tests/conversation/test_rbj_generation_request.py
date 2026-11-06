"""A caller's transaction observes the same frozen generation identity."""

from dataclasses import replace
from unittest.mock import AsyncMock, patch

import pytest

from test_generation_recovery import Models, Sources, prepare, request
from tianshu.conversation.generation import GenerationRequests
from tianshu.runtime.errors import AppError


@pytest.mark.asyncio
async def test_request_reuses_the_callers_transaction_and_checks_frozen_identity(database):
    context = await prepare(database)
    models = Models()
    sources = Sources()
    sources.validate_persistence = AsyncMock(return_value=True)
    generations = GenerationRequests(database, sources, models)
    original = request("rbj-original", context)
    async with database.transaction() as connection:
        await generations.prepare("rbj-original", original, connection=connection)
        sources.validate_persistence.reset_mock()
        # The uncommitted row is invisible to a different transaction. Refuse
        # opening any transaction while exercising this public read port.
        with patch.object(database, "transaction", side_effect=AssertionError("unexpected nested transaction")):
            assert await generations.request(original.attempt_id, context, connection=connection,
                                             material_key="rbj-original") == original
            assert await generations.request("missing", context, connection=connection,
                                             material_key="rbj-original") is None
            with pytest.raises(AppError, match="generation_identity_conflict"):
                await generations.request(original.attempt_id, context, connection=connection, material_key="other")
            with pytest.raises(AppError, match="generation_context_changed"):
                await generations.request(original.attempt_id, replace(context, actor_id="other"),
                                          connection=connection, material_key="rbj-original")
        sources.validate_persistence.assert_not_awaited()
        await connection.execute("UPDATE conversation.generation_requests SET state='expired' WHERE attempt_id=%s",
                                 (original.attempt_id,))
        assert await generations.request(original.attempt_id, context, connection=connection,
                                         material_key="rbj-original") == original
        await connection.execute("UPDATE conversation.generation_requests SET state='invalidated',request=NULL "
                                 "WHERE attempt_id=%s", (original.attempt_id,))
        with pytest.raises(AppError, match="source_unavailable"):
            await generations.request(original.attempt_id, context, connection=connection, material_key="rbj-original")
    assert models.calls == [] and models.observations == []
