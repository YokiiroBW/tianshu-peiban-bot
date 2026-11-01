"""Old Growth checkpoints seed the current queue without replacing old attempts."""

from dataclasses import replace

import pytest

from tianshu.conversation.codec import document, signature
from tianshu.models.types import ModelPart, ModelResult
from test_growth_output import Models, prepare, state


async def legacy_checkpoint(database, *, context=None, error=None):
    async with database.transaction() as connection:
        await connection.execute("DELETE FROM companion.growth_inputs")
        await connection.execute(
            "UPDATE companion.organize_state SET legacy_state='requests',active_batch=NULL,cursor=NULL,"
            "legacy_started_at=now(),last_error=%s,context=COALESCE(%s,context)",
            (error, document(context) if context else None))


@pytest.mark.asyncio
async def test_recover_seeds_old_cursor_tail_before_any_empty_finish(database):
    models = Models({"notes": []})
    growth, _memory, _jobs, context, _notifications = await prepare(database, models)
    await legacy_checkpoint(database, error="context_mandatory_budget_exceeded")
    # The old reviewed watermark can equal the latest tick despite unreviewed
    # Episode material. Recovery must still seed the actual version queue.
    async with database.transaction() as connection:
        await connection.execute("UPDATE companion.organize_state SET reviewed_watermark=watermark")
    await growth.recover()
    assert (await state(database))["legacy_state"] == "episodes"
    scope = signature(sorted(context.scope_ids))
    await growth.organize("actor", scope, 1)
    assert (await state(database))["legacy_state"] == "complete"
    assert (await state(database))["cursor"] is None
    await growth.organize("actor", scope, 1)
    assert len(models.calls) == 1
    assert (await state(database))["cursor"] == "e:1"


@pytest.mark.asyncio
async def test_original_unknown_request_context_and_inputs_survive_legacy_conversion(database):
    models = Models({"notes": []})
    models.status = "unknown"
    growth, memory, _jobs, context, _notifications = await prepare(database, models)
    scope = signature(sorted(context.scope_ids))
    await growth.organize("actor", scope, 1)
    original = models.calls[0]
    later_context = replace(context, person_id="later-person", conversation_id="later-conversation")
    await legacy_checkpoint(database, context=later_context, error="reflection_generation_unknown")
    memory.episodes += (replace(memory.episode, episode_id="later", sequence=2, cursor="e:2"),)
    await growth.recover()
    await growth.organize("actor", scope, 1)  # seed Episode versions
    waiting = await growth.organize("actor", scope, 1)  # observe the original
    assert waiting["recovery"] == "observe_original_attempt"
    assert waiting["attempt_id"] == original.attempt_id
    assert len(models.calls) == 1
    current = await state(database)
    assert current["active_batch"]["material_key"] == original.intent_id
    assert current["active_batch"]["context"]["person_id"] == context.person_id
    assert current["active_batch"]["inputs"] == [["episode", 1]]
    assert current["cursor"] is None
    assert (await growth.view(later_context))["review"]["can_request_review"] is False
    # A later observation consumes only the exact original batch, leaving the
    # newer Episode version pending for the normal budget-aware consumer.
    result = models.results[original.attempt_id]
    models.results[original.attempt_id] = ModelResult((ModelPart("text", text='{"notes":[]}'),),
        replace(result.receipt, status="succeeded", dispatch="submitted"))
    await growth.organize("actor", scope, 1)
    assert len(models.calls) == 1
    async with database.transaction(read_only=True) as connection:
        rows = await (await connection.execute(
            "SELECT episode_id,reviewed_revision FROM companion.growth_inputs ORDER BY sequence")).fetchall()
        manifest = await (await connection.execute("SELECT state FROM companion.growth_legacy_requests")).fetchone()
    assert [(row["episode_id"], row["reviewed_revision"]) for row in rows] == [("episode", 1), ("later", 0)]
    assert manifest["state"] == "reviewed"


@pytest.mark.asyncio
async def test_original_intent_inventory_does_not_cross_principal_or_scope(database):
    models = Models({"notes": []})
    growth, _memory, _jobs, context, _notifications = await prepare(database, models)
    await growth.organize("actor", signature(sorted(context.scope_ids)), 1)
    assert len(await growth.generations.pending_materials(replace(context, person_id="another"), prefix="organize:")) == 1
    assert await growth.generations.pending_materials(replace(context, principal_id="another"), prefix="organize:") == ()
    assert await growth.generations.pending_materials(replace(context, scope_ids=("other",)), prefix="organize:") == ()
    assert await growth.generations.pending_materials(context, prefix="life:") == ()
    first = await growth.generations.pending_materials(context, prefix="organize:", limit=1)
    assert await growth.generations.pending_materials(context, prefix="organize:", after=first[0]["material_key"]) == ()
