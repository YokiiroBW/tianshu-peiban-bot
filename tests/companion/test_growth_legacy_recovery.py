"""Old Growth checkpoints seed the current queue without replacing old attempts."""

from dataclasses import replace
from importlib.resources import files
import json
from uuid import NAMESPACE_URL, uuid5

import pytest

from tianshu.conversation.codec import context_from_record, document, signature
from tianshu.models.types import ModelPart, ModelResult
from tianshu.memory.service import MemoryService
from tianshu.runtime.migrations import Migration, migrate
from tianshu.runtime.serialization import wire
from tianshu.runtime.errors import AppError
from tianshu.types import OriginKind, SourceRef
from tianshu.companion.growth_recovery import AUTHORIZATION_BLOCKED, IDENTITY_BLOCKED
from tianshu.companion.reflection_output import frozen_episodes, prepare_reflections
from test_growth_output import Authority, Models, note, prepare, retry_state, state


async def legacy_checkpoint(database, *, context=None, error=None):
    async with database.transaction() as connection:
        await connection.execute("DELETE FROM companion.growth_inputs")
        await connection.execute("DELETE FROM companion.growth_retry_batches")
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
    assert waiting["state"] == "retry_waiting"
    assert waiting["attempt_id"] == original.attempt_id
    assert len(models.calls) == 1
    current = await state(database)
    retry = await retry_state(database)
    assert current["active_batch"] is None
    assert retry["batch"]["material_key"] == original.intent_id
    assert retry["batch"]["context"]["person_id"] == context.person_id
    assert retry["batch"]["inputs"] == [["episode", 1]]
    assert current["cursor"] is None
    assert (await growth.view(later_context))["review"]["can_request_review"] is False
    # A later observation consumes only the exact original batch, leaving the
    # newer Episode version pending for the normal budget-aware consumer.
    result = models.results[original.attempt_id]
    models.results[original.attempt_id] = ModelResult((ModelPart("text", text='{"notes":[]}'),),
        replace(result.receipt, status="succeeded", dispatch="submitted"))
    async with database.transaction() as connection:
        await connection.execute("UPDATE companion.growth_retry_batches SET due_at=now()")
    await growth.retries.run("actor", scope, retry["retry_key"])
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
    assert waiting["state"] == "retry_waiting" and waiting["attempt_id"] == original.attempt_id
    current = await state(database)
    retry = await retry_state(database)
    assert current["active_batch"] is None
    assert retry["batch"]["material_key"] == original.intent_id
    assert retry["batch"]["context"]["access_epoch"] == 1
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
        original.intent_id: "abandoned", "organize:tombstone": "invalidated"}
    assert saved["request"]["context"]["access_epoch"] == 1
    view = (await growth.view(refreshed))["review"]
    assert not view["can_request_review"] and view["retry_summary"]["waiting"] == 1
    with pytest.raises(AppError, match="reflection_original_attempt_unresolved"):
        await growth.request_review(refreshed,
            {"client_request_id": "do-not-double-submit", "expected_revision": current["revision"]})
    result = models.results[original.attempt_id]
    models.results[original.attempt_id] = replace(result, receipt=replace(result.receipt, status="running"))
    async with database.transaction() as connection:
        await connection.execute("UPDATE companion.growth_retry_batches SET due_at=now()")
    await growth.retries.run("actor", scope, retry["retry_key"])
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
    models.status = "running"  # Freeze a pre-NR06 active pointer before observing a terminal outcome.
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
    result = models.results[original.attempt_id]
    models.results[original.attempt_id] = replace(result, receipt=replace(result.receipt, status="unknown"))
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
    retry = await retry_state(database)
    assert blocked["last_error"] is None and blocked["active_batch"] is None and models.reads == []
    assert retry["state"] == "blocked" and retry["last_error"] == "scheduled_scope_unavailable"
    authority.allow_refresh = True
    caller = replace(context, access_epoch=2)
    view = (await growth.view(caller))["review"]
    assert view["retry_batches"][0]["can_request_review"]

    async def forbidden(*_args, **_kwargs):
        pytest.fail("An original observation must not enter GenerationRequests.run")

    monkeypatch.setattr(growth.generations, "run", forbidden)
    resumed = await growth.request_review(caller,
        {"client_request_id": "read-original", "expected_revision": blocked["revision"]})
    after_resume = await state(database)
    assert resumed.data["recovery"] == "retry_computation"
    assert after_resume["active_batch"] is None
    assert after_resume["generation_revision"] == 0 and after_resume["cursor"] is None
    assert models.reads == []  # review holds the state lock; Broker I/O belongs to the later job.
    if status == "unknown":
        result = models.results[original.attempt_id]
        models.results[original.attempt_id] = replace(result, receipt=replace(result.receipt, status="running"))
    outcome = await growth.retries.run("actor", scope, retry["retry_key"])
    assert len(models.calls) == 1 and models.reads == [original.attempt_id] * (2 if status == "succeeded" else 1)
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
        assert outcome["state"] == "waiting_original" and current["cursor"] is None
        assert current["active_batch"] is None and memory.proposals == []
        assert manifest["state"] == "abandoned"
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
    assert current["active_batch"] is None
    assert (await retry_state(database))["batch"]["inputs"] == [["episode", 1]]
    assert memory.proposals == [] and len(models.calls) == 1
    view = (await growth.view(caller))["review"]
    assert view["retry_batches"][0]["last_error"] == "reflection_material_contract_invalid"
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
    assert outcome["state"] == "retry_blocked" and outcome["error_code"] == "source_unavailable"
    current = await state(database)
    assert current["last_error"] is None and current["active_batch"] is None
    assert (await retry_state(database))["last_error"] == "source_unavailable"
    assert current["cursor"] is None and current["generation_revision"] == 0
    assert models.results[original.attempt_id].receipt.status == "succeeded"
    assert memory.proposals == [] and len(models.calls) == 1


async def paused_generation(database, payload, *, old=False, refs=None):
    models = Models(payload)
    models.status = "running"
    growth, memory, jobs, context, _notifications = await prepare(database, models)
    if refs is not None:
        memory.episode = replace(memory.episode, source_refs=refs)
        memory.episodes = (memory.episode,)
    scope = signature(sorted(context.scope_ids))
    await growth.organize("actor", scope, 1)
    request = models.calls[0]
    models.results[request.attempt_id] = ModelResult(
        (ModelPart("text", text=json.dumps(payload, ensure_ascii=False)),),
        replace(models.results[request.attempt_id].receipt, status="succeeded", dispatch="submitted"))
    if old:
        async with database.transaction() as connection:
            await connection.execute(
                "UPDATE companion.organize_state SET active_batch=active_batch-'reflection_identity_protocol'")
    prepared = prepare_reflections(models.results[request.attempt_id], context, frozen_episodes(request),
        request.source_refs, request=request)
    return growth, memory, jobs, context, models, request, prepared


async def bind_real_reflection_store(database, growth, memory, jobs, context):
    await migrate(database, (
        Migration("0010_identity", files("tianshu.identity").joinpath("schema.sql").read_text()),
        Migration("0011_sources", "\n".join(files("tianshu.sources").joinpath(name).read_text()
            for name in ("schema.sql", "functions.sql"))),
        Migration("0020_memory", files("tianshu.memory").joinpath("schema.sql").read_text()),
        Migration("0021_memory_consolidation", files("tianshu.memory").joinpath("0021_memory_consolidation.sql").read_text()),
    ))

    class Descriptions:
        async def describe_for_write_on(self, _connection, current, refs):
            return [{"scope_ids": current.scope_ids} for _ref in refs]

    service = MemoryService.__new__(MemoryService)
    service.database, service.jobs, service.sources = database, jobs, Descriptions()
    memory.record_reflection = service.record_reflection
    async with database.transaction() as connection:
        for episode in memory.episodes:
            await connection.execute(
                "INSERT INTO memory.episodes(episode_id,event_id,actor_id,principal_id,person_id,conversation_id,"
                "revision,origin_kind,occurred_at,summary,source_refs,scope_ids,coverage,content_hash) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'synthetic')",
                (episode.episode_id, episode.event_id, context.actor_id, context.principal_id,
                 context.person_id, context.conversation_id, episode.revision, episode.origin_kind.value,
                 episode.occurred_at, episode.summary, document(episode.source_refs), list(context.scope_ids), document(episode.coverage)))


async def seed_reflection(database, context, proposal):
    async with database.transaction() as connection:
        await connection.execute(
            "INSERT INTO memory.reflections(proposal_id,actor_id,principal_id,person_id,conversation_id,episode_ids,"
            "content,kind,occurred_at,source_refs,scope_ids,conditions,counterexamples) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (proposal.proposal_id, context.actor_id, context.principal_id, context.person_id, context.conversation_id,
             list(proposal.episode_ids), proposal.content, proposal.kind, proposal.occurred_at, document(proposal.source_refs),
             list(proposal.scope_ids), document(proposal.conditions), list(proposal.counterexamples)))


@pytest.mark.asyncio
async def test_old_subset_identity_collision_gets_v2_without_modifying_old_mixed_batch(database):
    growth, memory, jobs, context, models, request, prepared = await paused_generation(
        database, {"notes": [note(), note("interest")]}, old=True)
    await bind_real_reflection_store(database, growth, memory, jobs, context)
    legacy_id = str(uuid5(NAMESPACE_URL, "reflection:actor:interest:episode"))
    old = replace(prepared[1].proposal, proposal_id=legacy_id, content="早先混合批次的另一项主观认识",
        episode_ids=("episode", "old-second", "old-third"),
        source_refs=tuple(SourceRef("old-source-" + str(index), 1) for index in range(8)))
    await seed_reflection(database, context, old)
    await growth.recover()  # Bounded old-active registration only; no model I/O.
    assert models.reads == []
    outcome = await growth.organize("actor", signature(sorted(context.scope_ids)), 1)
    assert outcome["state"] == "reviewed" and len(models.calls) == 1
    assert models.reads == [request.attempt_id]
    async with database.transaction(read_only=True) as connection:
        rows = await (await connection.execute("SELECT proposal_id,content,source_refs FROM memory.reflections")).fetchall()
        converted = await (await connection.execute("SELECT context,state FROM companion.growth_legacy_requests")).fetchone()
    assert len(rows) == 3
    assert next(row for row in rows if row["proposal_id"] == legacy_id)["content"] == old.content
    assert {item.proposal.proposal_id for item in prepared} <= {row["proposal_id"] for row in rows}
    assert converted["state"] == "reviewed"
    assert converted["context"]["reflection_identity_conversion"]["aliases"] == {}
    assert (await state(database))["generation_revision"] == 0


@pytest.mark.asyncio
async def test_exact_old_commit_has_one_fixed_manifest_alias_and_no_duplicate_affect(database, monkeypatch):
    value = note("interest")
    value["affect"] = {"appraisal": "原主观感受", "valence": .2, "arousal": .2, "stress": .1,
        "social_need": .1, "confidence": .7, "goal_relevance": 0, "attribution": "situation", "control": .5}
    growth, memory, jobs, context, models, request, prepared = await paused_generation(
        database, {"notes": [value]}, old=True, refs=(SourceRef("original", 1), SourceRef("second", 1)))
    await bind_real_reflection_store(database, growth, memory, jobs, context)
    legacy_id = str(uuid5(NAMESPACE_URL, "reflection:actor:interest:episode"))
    old = replace(prepared[0].proposal, proposal_id=legacy_id,
        source_refs=tuple(reversed(prepared[0].proposal.source_refs)))
    await seed_reflection(database, context, old)
    await growth.companion.apply_affect(context, replace(prepared[0].affect, event_id=legacy_id))
    await growth.recover()
    finish = growth._finish

    async def interrupted(*_args, **_kwargs):
        raise AppError("generation_no_longer_current", status=409)

    monkeypatch.setattr(growth, "_finish", interrupted)
    await growth.organize("actor", signature(sorted(context.scope_ids)), 1)
    async with database.transaction(read_only=True) as connection:
        manifest = await (await connection.execute("SELECT context,state FROM companion.growth_legacy_requests")).fetchone()
    receipt = manifest["context"]["reflection_identity_conversion"]
    assert receipt["aliases"] == {prepared[0].proposal.proposal_id: legacy_id} and manifest["state"] == "pending"

    def no_second_v1_probe(*_args, **_kwargs):
        pytest.fail("The fixed conversion receipt must replace the one-time v1 calculation")

    monkeypatch.setattr("tianshu.companion.growth_recovery.uuid5", no_second_v1_probe)
    monkeypatch.setattr(growth, "_finish", finish)
    outcome = await growth.organize("actor", signature(sorted(context.scope_ids)), 1)
    assert outcome["state"] == "reviewed" and len(models.calls) == 1
    async with database.transaction(read_only=True) as connection:
        reflections = await (await connection.execute("SELECT proposal_id,content,source_refs FROM memory.reflections")).fetchall()
        affects = await (await connection.execute("SELECT event_id,event_revision FROM companion.affect_events")).fetchall()
        styles = await (await connection.execute("SELECT id FROM companion.style_rules")).fetchall()
        final = await (await connection.execute("SELECT context,state FROM companion.growth_legacy_requests")).fetchone()
    assert reflections == [{"proposal_id": legacy_id, "content": old.content, "source_refs": wire(old.source_refs)}]
    assert affects == [{"event_id": legacy_id, "event_revision": 1}]
    assert styles == [{"id": legacy_id}]
    assert final["state"] == "reviewed" and final["context"]["reflection_identity_conversion"] == receipt


@pytest.mark.asyncio
async def test_real_v2_conflict_is_visible_and_review_only_observes_original(database, monkeypatch):
    growth, memory, jobs, context, models, request, prepared = await paused_generation(
        database, {"notes": [note(), note("interest")]})
    await bind_real_reflection_store(database, growth, memory, jobs, context)
    await seed_reflection(database, context, replace(prepared[1].proposal, content="同一v2身份的实际不一致正文"))
    scope = signature(sorted(context.scope_ids))
    outcome = await growth.organize("actor", scope, 1)
    assert outcome["state"] == "blocked" and outcome["error_code"] == IDENTITY_BLOCKED
    blocked = await state(database)
    assert blocked["cursor"] is None and blocked["generation_revision"] == 0
    assert (await growth.view(context))["review"]["recovery"] == "observe_original_attempt"
    assert (await growth.view(context))["review"]["can_request_review"]
    async with database.transaction(read_only=True) as connection:
        assert (await (await connection.execute("SELECT count(*) AS n FROM memory.reflections")).fetchone())["n"] == 1
        assert (await (await connection.execute("SELECT count(*) AS n FROM companion.style_rules")).fetchone())["n"] == 0
    before = len(jobs.items)
    await growth.recover()
    assert len(jobs.items) == before

    class Reader(Authority):
        async def refresh_scheduled_context(self, previous, *, permission, connection=None):
            return previous

    growth.companion.identity = Reader()

    async def no_new_intent(*_args, **_kwargs):
        pytest.fail("A true identity conflict must not be turned into a new model request")

    monkeypatch.setattr(growth.generations, "run", no_new_intent)
    reviewed = await growth.request_review(context,
        {"client_request_id": "observe-real-conflict", "expected_revision": blocked["revision"]})
    assert reviewed.data["recovery"] == "observe_original_attempt"
    again = await growth.organize("actor", scope, reviewed.revision)
    assert again["error_code"] == IDENTITY_BLOCKED and len(models.calls) == 1
    assert (await state(database))["generation_revision"] == 0


@pytest.mark.asyncio
async def test_old_identity_conversion_keeps_unavailable_full_sources_authorization_blocked(database):
    growth, memory, _jobs, context, models, request, _prepared = await paused_generation(
        database, {"notes": [note()]}, old=True)
    await growth.recover()

    class DeniedSources(Authority):
        async def validate_persistence(self, _connection, _context, refs):
            return not refs

    growth.companion.sources = DeniedSources()
    outcome = await growth.organize("actor", signature(sorted(context.scope_ids)), 1)
    assert outcome["error_code"] == "source_unavailable"
    current = await state(database)
    assert (await retry_state(database))["last_error"] == "source_unavailable"
    assert current["active_batch"] is None
    assert current["generation_revision"] == 0 and memory.proposals == [] and len(models.calls) == 1
    async with database.transaction(read_only=True) as connection:
        manifest = await (await connection.execute("SELECT context,state FROM companion.growth_legacy_requests")).fetchone()
    assert manifest["state"] == "abandoned" and "reflection_identity_conversion" not in manifest["context"]
