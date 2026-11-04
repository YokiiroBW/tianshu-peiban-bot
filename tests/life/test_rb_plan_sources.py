from tianshu.conversation.codec import document
from tianshu.life.dependencies import LifeDependencies
from tianshu.types import SourceRef
import pytest

from test_planner_execution import setup


@pytest.mark.asyncio
async def test_source_invalidation_is_partitioned_by_actor_and_idempotent(database):
    value = await setup(database, "{}")
    ref = SourceRef("shared-source", 1)
    async with database.transaction() as connection:
        await connection.execute("INSERT INTO life.actor_state(actor_id,timezone) VALUES ('other','UTC')")
        for actor in ("actor", "other"):
            await connection.execute("INSERT INTO life.activities(id,actor_id,title,kind,state,source_refs,scope_ids) "
                "VALUES (%s,%s,'private title','reading','planned',%s,ARRAY['actor-scope'])",
                (actor + "-activity", actor, document((ref,))))
    dependencies = LifeDependencies()
    async with database.transaction() as connection:
        assert await dependencies.actors_for_source(connection, (ref,), "forgotten") == ("actor", "other")
        await dependencies.invalidate_actor(connection, (ref,), "forgotten", actor_id="actor")
        first = await (await connection.execute("SELECT * FROM life.activities WHERE id='actor-activity'")).fetchone()
        other = await (await connection.execute("SELECT * FROM life.activities WHERE id='other-activity'")).fetchone()
        await dependencies.invalidate_actor(connection, (ref,), "forgotten", actor_id="actor")
        repeated = await (await connection.execute("SELECT * FROM life.activities WHERE id='actor-activity'")).fetchone()
    assert first["waiting_reason"] == "source_unavailable" and first["revision"] == repeated["revision"]
    assert other["state"] == "planned" and other["title"] == "private title" and other["revision"] == 1

    async with database.transaction() as connection:
        await dependencies.invalidate_actor(connection, (ref,), "forgotten", actor_id="other")
        other = await (await connection.execute("SELECT * FROM life.activities WHERE id='other-activity'")).fetchone()
    assert other["waiting_reason"] == "source_unavailable"
