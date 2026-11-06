"""Diary replacement prelocks current and original source closures together."""
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from psycopg.errors import LockNotAvailable

from test_goal_source_transaction import source_life
from tianshu.conversation.types import InputPart
from tianshu.life.generation_control import GenerationRecovery
from tianshu.runtime.serialization import wire
from tianshu.types import OriginKind


async def create_source(value,event_id):
    return await value.sources.record_event(value.context,producer="rbi.diary",event_id=event_id,revision=1,
        occurred_at=datetime.now(UTC),origin_kind=OriginKind.TOOL_RESULT,parts=(InputPart("text",event_id),))


async def test_current_diary_and_original_request_sources_are_both_locked(source_life):
    value = source_life
    original, current = await create_source(value,"original"),await create_source(value,"current")
    recovery = GenerationRecovery(None,life=value.life)
    row = {"context":wire(value.context),"source_refs":wire(original)}
    request = SimpleNamespace(source_refs=original)
    async with value.database.transaction() as connection:
        assert await recovery._sources_current_on(connection,row,request,
            additional_context=value.context,additional_refs=current)
        for ref in (*original,*current):
            with pytest.raises(LockNotAvailable):
                async with value.database.transaction() as concurrent:
                    await concurrent.execute("SELECT source_id FROM sources.originals WHERE source_id=%s FOR UPDATE NOWAIT",
                        (ref.source_id,))


async def test_unreadable_original_does_not_turn_current_diary_into_authorized_original(source_life):
    value = source_life
    original, current = await create_source(value,"original-private"),await create_source(value,"current-readable")
    async with value.database.transaction(read_only=True) as connection:
        public = await (await connection.execute("SELECT scope_id FROM identity.scopes WHERE actor_id=%s AND kind='public'",
            (value.context.actor_id,))).fetchone()
    limited = replace(value.context,scope_ids=(public["scope_id"],),audience=(public["scope_id"],))
    row = {"context":wire(limited),"source_refs":wire(original)}
    request = SimpleNamespace(source_refs=original)
    async with value.database.transaction() as connection:
        assert not await GenerationRecovery(None,life=value.life)._sources_current_on(connection,row,request,
            additional_context=value.context,additional_refs=current)
        assert await value.sources.validate_persistence(connection,value.context,current)
