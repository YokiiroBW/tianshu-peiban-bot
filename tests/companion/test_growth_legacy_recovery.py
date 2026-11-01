"""Old Growth checkpoints seed the current queue without replacing old attempts."""

from dataclasses import replace

import pytest

from tianshu.conversation.codec import context_from_record, document, signature
from tianshu.models.types import ModelPart, ModelResult
from tianshu.runtime.errors import AppError
from tianshu.types import OriginKind
from tianshu.companion.growth_recovery import AUTHORIZATION_BLOCKED
from test_growth_output import Authority, Models, prepare, state


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
    assert (await state(database))["legacy_state"] == "requests"
    scope = signature(sorted(context.scope_ids))
    await growth.organize("actor", scope, 1)  # Startup only queued this conversion.
    assert (await state(database))["legacy_state"] == "episodes"
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
    await growth.organize("actor", scope, 1)  # inventory original requests
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
    assert await growth.generations.pending_materials(replace(context, actor_id="another"), prefix="organize:") == ()
    assert await growth.generations.pending_materials(replace(context, scope_ids=("other",)), prefix="organize:") == ()
    assert await growth.generations.pending_materials(replace(context, scope_ids=()), prefix="organize:") == ()
    assert await growth.generations.pending_materials(replace(context, scope_ids=(*context.scope_ids, "other")), prefix="organize:") == ()
    assert await growth.generations.pending_materials(context, prefix="life:") == ()
    first = await growth.generations.pending_materials(context, prefix="organize:", limit=1)
    assert await growth.generations.pending_materials(context, prefix="organize:", after=first[0]["material_key"]) == ()


class EpochAuthority(Authority):
    def __init__(self, *, allow_refresh):
        self.allow_refresh = allow_refresh
        self.refreshes = []

    async def check(self, context, *_args):
        if context.access_epoch != 2:
            raise AppError("access_changed", status=409)

    async def refresh_scheduled_context(self, previous, *, permission, connection=None):
        self.refreshes.append((previous, permission))
        if not self.allow_refresh:
            raise AppError("scheduled_scope_unavailable", status=403)
        return replace(previous, access_epoch=2, origin_kind=OriginKind.TIMER)


@pytest.mark.asyncio
async def test_startup_only_queues_and_revoked_partition_does_not_stop_another(database):
    models = Models({"notes": []})
    growth, memory, jobs, context, _notifications = await prepare(database, models)
    await legacy_checkpoint(database, error="reflection_support_invalid")
    authority = EpochAuthority(allow_refresh=False)
    growth.companion.identity = authority
    scope = signature(sorted(context.scope_ids))
    healthy = replace(context, scope_ids=("healthy",), audience=("healthy",), access_epoch=2)
    healthy_scope = signature(sorted(healthy.scope_ids))
    async with database.transaction() as connection:
        await connection.execute(
            "INSERT INTO companion.organize_state(actor_id,scope_key,context,legacy_state) VALUES(%s,%s,%s,'requests')",
            (context.actor_id, healthy_scope, document(healthy)))
    before = len(jobs.items)
    await growth.recover()
    assert authority.refreshes == []  # Neither authentication nor material I/O at startup.
    assert {item.payload["scope_key"] for item in jobs.items[before:]} == {scope, healthy_scope}
    assert models.calls == []
    old = await state_for(database, scope)
    blocked = await growth.organize("actor", scope, old["revision"])
    assert blocked["state"] == "blocked" and blocked["error_code"] == AUTHORIZATION_BLOCKED
    current = await state_for(database, scope)
    assert current["context"] == old["context"]
    assert current["cursor"] == current["legacy_request_cursor"] == current["legacy_episode_cursor"] is None
    assert current["legacy_state"] == "requests" and current["generation_revision"] == 0
    assert current["blocked_material"]["previous_error"] == "reflection_support_invalid"
    assert current["blocked_material"]["error_code"] == "scheduled_scope_unavailable"
    before = len(jobs.items)
    await growth.changed(replace(context, access_epoch=2, permissions=context.permissions | {"new.permission"}),
        "new-input-while-blocked", episode=replace(memory.episode, revision=2))
    assert len(jobs.items) == before
    assert (await state_for(database, scope))["context"] == old["context"]
    await growth.organize("actor", healthy_scope, 1)
    assert (await state_for(database, healthy_scope))["legacy_state"] == "episodes"
    view = (await growth.view(replace(context, access_epoch=2)))["review"]
    assert view["recovery"] == "reauthorize_original_scope" and view["can_request_review"]
    assert "legacy_state" not in view and view["blocked_material"]["phase"] == "requests"
    before = len(jobs.items)
    await growth.recover()
    assert all(item.payload["scope_key"] != scope for item in jobs.items[before:])
    assert models.calls == []


async def state_for(database, scope):
    async with database.transaction(read_only=True) as connection:
        return await (await connection.execute(
            "SELECT * FROM companion.organize_state WHERE actor_id='actor' AND scope_key=%s", (scope,))).fetchone()


@pytest.mark.asyncio
async def test_epoch_refresh_seeds_inventory_but_never_rebinds_original_unknown_request(database):
    models = Models({"notes": []})
    models.status = "unknown"
    growth, _memory, _jobs, context, _notifications = await prepare(database, models)
    scope = signature(sorted(context.scope_ids))
    await growth.organize("actor", scope, 1)
    original = models.calls[0]
    # Source-invalidated request tombstones are keyed by the original epoch.
    async with database.transaction() as connection:
        await connection.execute(
            "INSERT INTO conversation.generation_requests(material_key,attempt_id,context_key,request,source_refs,state) "
            "VALUES('organize:tombstone','00000000-0000-0000-0000-000000000011',%s,NULL,'[]','invalidated')",
            (growth.generations.context_key(context),))
    await legacy_checkpoint(database, error="reflection_generation_unknown")
    authority = EpochAuthority(allow_refresh=True)
    growth.companion.identity = authority
    await growth.recover()
    await growth.organize("actor", scope, 1)
    await growth.organize("actor", scope, 1)
    seeded = await state(database)
    assert seeded["legacy_state"] == "complete" and seeded["cursor"] is None
    refreshed = context_from_record(seeded["context"])
    assert refreshed.access_epoch == 2 and refreshed.scope_ids == context.scope_ids
    assert refreshed.audience == context.audience and refreshed.permissions == context.permissions
    blocked = await growth.organize("actor", scope, 1)
    assert blocked["state"] == "blocked"
    current = await state(database)
    assert current["active_batch"]["material_key"] == original.intent_id
    assert current["active_batch"]["context"]["access_epoch"] == 1
    assert current["generation_revision"] == 0 and current["cursor"] is None
    assert len(models.calls) == 1 and models.reads == []
    async with database.transaction(read_only=True) as connection:
        entries = await (await connection.execute(
            "SELECT material_key,state FROM companion.growth_legacy_requests ORDER BY material_key")).fetchall()
        saved = await (await connection.execute(
            "SELECT request FROM conversation.generation_requests WHERE attempt_id=%s", (original.attempt_id,))).fetchone()
    assert {item["material_key"]: item["state"] for item in entries} == {
        original.intent_id: "pending", "organize:tombstone": "invalidated"}
    assert saved["request"]["context"]["access_epoch"] == 1
    assert (await growth.view(refreshed))["review"]["can_request_review"] is False
    with pytest.raises(AppError, match="reflection_original_attempt_unresolved"):
        await growth.request_review(refreshed, {"client_request_id": "do-not-replace-original", "expected_revision": 1})


@pytest.mark.asyncio
async def test_explicit_authorization_resume_keeps_conversion_positions_and_generation(database):
    models = Models({"notes": []})
    growth, _memory, _jobs, context, _notifications = await prepare(database, models)
    scope = signature(sorted(context.scope_ids))
    await legacy_checkpoint(database, error="reflection_support_invalid")
    authority = EpochAuthority(allow_refresh=False)
    growth.companion.identity = authority
    await growth.organize("actor", scope, 1)
    old = await state(database)
    authority.allow_refresh = True
    caller = replace(context, access_epoch=2)
    body = {"client_request_id": "resume-original-authority", "expected_revision": old["revision"]}
    resumed = await growth.request_review(caller, body)
    assert resumed.data["recovery"] == "reauthorize_original_scope"
    assert (await growth.request_review(caller, body)).revision == resumed.revision
    current = await state(database)
    assert current["generation_revision"] == 0 and current["legacy_state"] == "requests"
    assert current["cursor"] == old["cursor"] and current["legacy_request_cursor"] == old["legacy_request_cursor"]
    assert current["context"] == old["context"]  # Original inventory identity is not rekeyed.
    await growth.organize("actor", scope, resumed.revision)
    await growth.organize("actor", scope, resumed.revision)
    assert (await state(database))["legacy_state"] == "complete"
    await growth.organize("actor", scope, resumed.revision)
    assert len(models.calls) == 1 and (await state(database))["cursor"] == "e:1"
