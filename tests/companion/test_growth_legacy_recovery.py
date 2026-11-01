"""Old Growth checkpoints seed the current queue without replacing old attempts."""

from dataclasses import replace
import json

import pytest

from tianshu.conversation.codec import context_from_record, document, signature
from tianshu.models.types import ModelPart, ModelResult
from tianshu.runtime.errors import AppError
from tianshu.types import OriginKind
from tianshu.companion.growth_recovery import AUTHORIZATION_BLOCKED
from test_growth_output import Authority, Models, note, prepare, state


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
    waiting = await growth.organize("actor", scope, 1)
    assert waiting["state"] == "waiting" and waiting["attempt_id"] == original.attempt_id
    current = await state(database)
    assert current["active_batch"]["material_key"] == original.intent_id
    assert current["active_batch"]["context"]["access_epoch"] == 1
    assert current["generation_revision"] == 0 and current["cursor"] is None
    assert len(models.calls) == 1 and models.reads == [original.attempt_id]
    assert models.read_contexts[0].access_epoch == 2
    assert models.read_contexts[0].scope_ids == original.context.scope_ids
    async with database.transaction(read_only=True) as connection:
        entries = await (await connection.execute(
            "SELECT material_key,state FROM companion.growth_legacy_requests ORDER BY material_key")).fetchall()
        saved = await (await connection.execute(
            "SELECT request FROM conversation.generation_requests WHERE attempt_id=%s", (original.attempt_id,))).fetchone()
    assert {item["material_key"]: item["state"] for item in entries} == {
        original.intent_id: "pending", "organize:tombstone": "invalidated"}
    assert saved["request"]["context"]["access_epoch"] == 1
    view = (await growth.view(refreshed))["review"]
    assert view["can_request_review"] and view["recovery"] == "observe_original_attempt"
    reviewed = await growth.request_review(refreshed,
        {"client_request_id": "do-not-replace-original", "expected_revision": 1})
    assert reviewed.data["recovery"] == "observe_original_attempt"
    await growth.organize("actor", scope, reviewed.revision)
    assert len(models.calls) == 1 and models.reads == [original.attempt_id] * 2
    assert (await state(database))["generation_revision"] == 0


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


async def active_original(database):
    models = Models({"notes": [note()]})
    models.status = "unknown"
    growth, memory, jobs, context, _notifications = await prepare(database, models)
    scope = signature(sorted(context.scope_ids))
    await growth.organize("actor", scope, 1)
    original = models.calls[0]
    async with database.transaction() as connection:
        current = await (await connection.execute(
            "SELECT active_batch FROM companion.organize_state WHERE actor_id='actor' AND scope_key=%s", (scope,))).fetchone()
        await connection.execute(
            "UPDATE companion.organize_state SET active_batch=active_batch||'{\"legacy_material\":true}'::jsonb")
        await connection.execute(
            "INSERT INTO companion.growth_legacy_requests(actor_id,scope_key,material_key,context,inputs,end_cursor,state,created_at) "
            "VALUES('actor',%s,%s,%s,%s,'e:1','pending',now())",
            (scope, original.intent_id, document(context), document(current["active_batch"]["inputs"])))
        saved = await (await connection.execute(
            "SELECT request FROM conversation.generation_requests WHERE attempt_id=%s", (original.attempt_id,))).fetchone()
    return growth, memory, models, context, original, saved["request"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["succeeded", "unknown"])
async def test_active_old_epoch_review_reads_only_original_under_current_bounded_authority(
    database, monkeypatch, status,
):
    growth, memory, models, context, original, saved = await active_original(database)
    if status == "succeeded":
        models.results[original.attempt_id] = ModelResult(
            (ModelPart("text", text=json.dumps({"notes": [note()]}, ensure_ascii=False)),),
            replace(models.results[original.attempt_id].receipt, status="succeeded", dispatch="submitted"))
    authority = EpochAuthority(allow_refresh=False)
    growth.companion.identity = authority
    scope = signature(sorted(context.scope_ids))
    await growth.organize("actor", scope, 1)
    blocked = await state(database)
    assert blocked["last_error"] == AUTHORIZATION_BLOCKED and models.reads == []
    original_batch = blocked["active_batch"]
    authority.allow_refresh = True
    caller = replace(context, access_epoch=2)
    view = (await growth.view(caller))["review"]
    assert view["can_request_review"] and view["recovery"] == "observe_original_attempt"
    assert "只读取原请求结果" in view["reason"]

    async def forbidden(*_args, **_kwargs):
        pytest.fail("An original observation must not enter GenerationRequests.run")

    monkeypatch.setattr(growth.generations, "run", forbidden)
    resumed = await growth.request_review(caller,
        {"client_request_id": "read-original", "expected_revision": blocked["revision"]})
    after_resume = await state(database)
    assert resumed.data["recovery"] == "observe_original_attempt"
    assert after_resume["active_batch"] == original_batch
    assert after_resume["generation_revision"] == 0 and after_resume["cursor"] is None
    assert models.reads == []  # review holds the state lock; Broker I/O belongs to the later job.
    outcome = await growth.organize("actor", scope, resumed.revision)
    assert len(models.calls) == 1 and models.reads == [original.attempt_id]
    assert models.read_contexts[0].access_epoch == 2
    assert models.read_contexts[0].scope_ids == original.context.scope_ids
    assert models.read_contexts[0].audience == original.context.audience
    assert models.read_contexts[0].permissions == original.context.permissions
    current = await state(database)
    async with database.transaction(read_only=True) as connection:
        stored = await (await connection.execute(
            "SELECT request FROM conversation.generation_requests WHERE attempt_id=%s", (original.attempt_id,))).fetchone()
        manifest = await (await connection.execute("SELECT state FROM companion.growth_legacy_requests")).fetchone()
    assert stored["request"] == saved
    if status == "succeeded":
        assert outcome["state"] == "reviewed" and current["cursor"] == "e:1"
        assert len(memory.proposals) == 1 and manifest["state"] == "reviewed"
    else:
        assert outcome["state"] == "waiting" and current["cursor"] is None
        assert current["active_batch"] == original_batch and memory.proposals == []
        assert manifest["state"] == "pending"
    assert current["generation_revision"] == 0


@pytest.mark.asyncio
async def test_old_success_with_new_episode_revision_is_refused_for_explicit_review(database, monkeypatch):
    growth, memory, models, context, original, saved = await active_original(database)
    models.results[original.attempt_id] = ModelResult(
        (ModelPart("text", text=json.dumps({"notes": [note()]})),),
        replace(models.results[original.attempt_id].receipt, status="succeeded", dispatch="submitted"))
    authority = EpochAuthority(allow_refresh=True)
    growth.companion.identity = authority
    memory.episodes = (replace(memory.episode, revision=2),)
    caller = replace(context, access_epoch=2)
    await growth.changed(caller, "repaired-version", episode=memory.episodes[0])

    async def forbidden(*_args, **_kwargs):
        pytest.fail("A stale successful original must not start another generation")

    monkeypatch.setattr(growth.generations, "run", forbidden)
    before = await state(database)
    refused = await growth.organize("actor", signature(sorted(context.scope_ids)), before["revision"])
    assert refused["error_code"] == "reflection_material_contract_invalid"
    current = await state(database)
    assert current["cursor"] is None and current["generation_revision"] == 0
    assert current["active_batch"]["inputs"] == [["episode", 1]]
    assert memory.proposals == [] and len(models.calls) == 1
    view = (await growth.view(caller))["review"]
    assert view["can_request_review"] and view["last_error"] == "reflection_material_contract_invalid"
    async with database.transaction(read_only=True) as connection:
        stored = await (await connection.execute(
            "SELECT request FROM conversation.generation_requests WHERE attempt_id=%s", (original.attempt_id,))).fetchone()
        pending = await (await connection.execute(
            "SELECT revision,reviewed_revision FROM companion.growth_inputs WHERE episode_id='episode'")).fetchone()
    assert stored["request"] == saved and pending == {"revision": 2, "reviewed_revision": 0}


@pytest.mark.asyncio
async def test_original_source_denial_remains_authorization_blocked_without_output_reclassification(database):
    growth, memory, models, context, original, _saved = await active_original(database)
    models.results[original.attempt_id] = ModelResult((),
        replace(models.results[original.attempt_id].receipt, status="succeeded", dispatch="submitted"))

    class SourceDenied(EpochAuthority):
        async def check(self, current, *args):
            await super().check(current)
            if args and args[0]:
                raise AppError("source_unavailable", status=403)

    growth.companion.identity = SourceDenied(allow_refresh=True)
    outcome = await growth.organize("actor", signature(sorted(context.scope_ids)), 1)
    assert outcome["state"] == "blocked" and outcome["error_code"] == AUTHORIZATION_BLOCKED
    current = await state(database)
    assert current["last_error"] == AUTHORIZATION_BLOCKED
    assert current["blocked_material"]["error_code"] == "source_unavailable"
    assert current["active_batch"]["material_key"] == original.intent_id
    assert current["cursor"] is None and current["generation_revision"] == 0
    assert models.results[original.attempt_id].receipt.status == "succeeded"
    assert memory.proposals == [] and len(models.calls) == 1
