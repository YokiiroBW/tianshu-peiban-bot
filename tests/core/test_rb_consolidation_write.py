"""Transactional stories for the shared write path; executed by the coordinator."""

from dataclasses import replace
from datetime import UTC, datetime
from base64 import b64decode
import asyncio
import json

import pytest

from tianshu.models.types import ModelCompletion, ModelPart, ModelReceipt, ModelResult
from tianshu.memory.extraction_contract import consolidation_example
from tianshu.memory.materials import batch_frozen
from tianshu.runtime.errors import AppError
from tianshu.types import OriginKind


class RecordedModel:
    def __init__(self, *, bad_receipt=False):
        self.results = {}
        self.submissions = []
        self.bad_receipt = bad_receipt

    async def generation_profile(self, request):
        return {"route_id": "fixture", "route_revision": 1, "protocol": "openai.chat",
                "model": "fixture", "output_parameters": {"max_tokens": request.max_output_tokens}}

    async def submit(self, request, *, completion):
        self.submissions.append((request, completion))
        payload = json.loads(request.messages[0].parts[0].text)
        decisions = []
        for target in payload["targets"]:
            material = next((item for item in payload["materials"]
                             if item["id"] in target["new_material_ids"]), None)
            operations = []
            if material:
                operations.append({"kind": "add_claim", "target": {"create": "n1"},
                    "payload": {"predicate": "preference.food", "value": "苹果", "speaker_id": material["speaker_id"],
                        "assertion": "self_confirmed", "polarity": True, "conditions": {},
                        "valid_from": None, "valid_until": None},
                    "support_groups": [[material["id"]]], "context_refs": [], "reads": [], "reason": "本人陈述"})
                if self.bad_receipt:
                    operations.append({"kind": "upsert_matter", "target": {"create": "n2"},
                        "payload": {"title": "任务", "state": "completed", "due_at": None,
                            "summary": "完成", "conditions": {},
                            "completion_basis": {"kind": "action_receipt", "material_refs": [material["id"]]}},
                        "support_groups": [[material["id"]]], "context_refs": [], "reads": [], "reason": "声称有回执"})
            decisions.append({"subject_id": target["subject_id"], "status": "changed" if operations else "no_change",
                              "operations": operations, "unresolved": [],
                              "reason": "有本人陈述" if operations else "没有新增事实"})
        receipt = ModelReceipt(request.attempt_id, request.intent_id, "succeeded", "submitted", "fixture", 1,
                               "fixture", "fixture", "openai.chat", "fixture")
        self.results[request.attempt_id] = ModelResult((ModelPart("text", text=json.dumps(
            {"targets": decisions, "needs_context": []}, ensure_ascii=False)),), receipt,
            {"finish_reason": "stop"}, ModelCompletion("complete", ("stop",)))
        return receipt

    async def status(self, attempt_id):
        return self.results[attempt_id].receipt if attempt_id in self.results else None

    async def result(self, attempt_id, *, context):
        return self.results.get(attempt_id)


class DeferredModel(RecordedModel):
    def __init__(self):
        super().__init__()
        self.ready = False

    async def submit(self, request, *, completion):
        finished = await super().submit(request, completion=completion)
        return replace(finished, status="queued", dispatch="not_started")

    async def status(self, attempt_id):
        receipt = await super().status(attempt_id)
        return receipt if self.ready else replace(receipt, status="queued", dispatch="not_started")

    async def result(self, attempt_id, *, context):
        return await super().result(attempt_id, context=context) if self.ready else None


class TruncatedOnceModel(RecordedModel):
    async def submit(self, request, *, completion):
        receipt = await super().submit(request, completion=completion)
        if len(self.submissions) == 1:
            result = self.results[request.attempt_id]
            self.results[request.attempt_id] = replace(result, native_response={"finish_reason": "length"},
                completion=ModelCompletion("incomplete", ("length",)))
        return receipt


class InvalidOnceModel(RecordedModel):
    async def submit(self, request, *, completion):
        receipt = await super().submit(request, completion=completion)
        if len(self.submissions) == 1:
            result = self.results[request.attempt_id]
            self.results[request.attempt_id] = replace(result,parts=(ModelPart("text",text="{}"),))
        return receipt


class StatisticsProbe:
    def __init__(self):
        self.allow = False
        self.events = []
        self.producers = []

    async def record_safely_on(self, conn, event):
        if not self.allow:
            return False
        self.events.append(event)
        return True

    async def mark_producer_on(self, conn, producer, *, watermark, complete, detail):
        self.producers.append((producer,watermark,complete,detail))


async def episode(core, context, event):
    at = datetime.now(UTC)
    refs = await core.sources.record_event(context, producer="web", event_id=event, revision=1,
        occurred_at=at, parts=({"kind": "text", "text": "我喜欢苹果"},), origin_kind=OriginKind.HUMAN)
    return await core.memory.observe_episode(context, {"event_id": event, "revision": 1, "occurred_at": at,
        "summary": "我喜欢苹果", "source_refs": refs, "origin_kind": "human"})


def configure(core, model):
    core.steps.models = model
    policy = replace(core.memory.consolidation.policy, quiet_seconds=0, context_messages=0)
    core.memory.consolidation.policy = policy
    core.memory.consolidation.materials.policy = policy


async def batch_for(core, episode_id):
    async with core.database.transaction(read_only=True) as conn:
        row = await (await conn.execute("SELECT consolidation_partition_id FROM memory.episodes WHERE episode_id=%s",
                                        (episode_id,))).fetchone()
        batch = await (await conn.execute("SELECT * FROM memory.consolidation_batches WHERE partition_id=%s "
            "ORDER BY created_at DESC LIMIT 1", (row["consolidation_partition_id"],))).fetchone()
    return row["consolidation_partition_id"], batch


async def test_atomic_rollback_keeps_frozen_proposal_when_one_operation_fails(core, ordinary_users):
    alice, _ = ordinary_users
    model = RecordedModel(bad_receipt=True)
    configure(core, model)
    item = await episode(core, alice, "rb-atomic")
    partition, _ = await batch_for(core, item.episode_id)
    await core.workers.consolidate(partition)
    _, batch = await batch_for(core, item.episode_id)
    async with core.database.transaction(read_only=True) as conn:
        counts = await (await conn.execute("SELECT (SELECT count(*) FROM memory.claims) AS claims, "
            "(SELECT count(*) FROM memory.matters) AS matters")).fetchone()
        selected = await (await conn.execute("SELECT * FROM memory.consolidation_compilations "
            "WHERE batch_id=%s AND selected", (batch["batch_id"],))).fetchone()
    assert counts == {"claims": 0, "matters": 0}
    assert batch["status"] == "failed"
    assert selected and len(selected["proposal"]["operations"]) == 2
    assert len(model.submissions) == 1


async def test_committed_batch_replay_returns_original_receipt(core, ordinary_users):
    alice, _ = ordinary_users
    model = RecordedModel()
    configure(core, model)
    item = await episode(core, alice, "rb-replay")
    partition, _ = await batch_for(core, item.episode_id)
    await core.workers.consolidate(partition)
    _, batch = await batch_for(core, item.episode_id)
    async with core.database.transaction(read_only=True) as conn:
        selected = await (await conn.execute("SELECT * FROM memory.consolidation_compilations "
            "WHERE batch_id=%s AND selected", (batch["batch_id"],))).fetchone()
    frozen = batch["manifest"]["steps"][selected["step_id"]]
    receipt = await core.memory.consolidation._apply(batch, alice, frozen, selected["proposal"], step_id=selected["step_id"])
    assert receipt == batch["commit_refs"]
    assert len(model.submissions) == 1


async def test_statistics_failure_is_replayed_from_the_batch_outbox(core, ordinary_users):
    alice, _ = ordinary_users
    model, stats = RecordedModel(), StatisticsProbe()
    configure(core, model)
    core.memory.bind_observability(statistics=stats,recovery_policy=None)
    item = await episode(core, alice, "rb-stats")
    partition, _ = await batch_for(core, item.episode_id)
    await core.workers.consolidate(partition)
    _, batch = await batch_for(core, item.episode_id)
    assert batch["status"] == "applied" and not stats.events
    stats.allow = True
    drained = await core.memory.backfill_statistics()
    assert drained["complete"] and stats.events[0]["event_id"] == "memory-batch:" + batch["batch_id"]
    assert stats.events[0]["metrics"]["memory_applied"] == 1
    assert stats.producers[-1][0] == "memory" and stats.producers[-1][2]


async def test_schema_correction_is_a_successful_physical_continuation_on_one_lineage(core, ordinary_users):
    from tianshu.runtime.recovery_policy import RecoveryPolicy
    alice, _ = ordinary_users
    model = InvalidOnceModel()
    configure(core,model)
    core.memory.bind_observability(statistics=None,recovery_policy=RecoveryPolicy())
    item = await episode(core,alice,"rb-continuation")
    partition, _ = await batch_for(core,item.episode_id)
    await core.workers.consolidate(partition)
    _, batch = await batch_for(core,item.episode_id)
    async with core.database.transaction(read_only=True) as conn:
        attempts = await (await conn.execute("SELECT * FROM runtime.recovery_attempts WHERE lineage_id=%s "
            "ORDER BY attempt_number",(batch["manifest"]["recovery"]["lineage_id"],))).fetchall()
    assert batch["status"] == "applied" and len(attempts) == 2
    assert [item["status"] for item in attempts] == ["succeeded","succeeded"]
    assert attempts[1]["continuation_of"] == attempts[0]["attempt_id"]
    assert attempts[1]["continuation_reason"] == "schema_correction"


async def test_waiting_model_releases_worker_and_completion_observes_same_attempt(core, ordinary_users):
    alice, _ = ordinary_users
    model = DeferredModel()
    configure(core, model)
    item = await episode(core, alice, "rb-deferred")
    partition, _ = await batch_for(core, item.episode_id)
    await core.workers.consolidate(partition)
    _, waiting = await batch_for(core, item.episode_id)
    assert waiting["status"] == "waiting_model" and len(model.submissions) == 1
    assert model.submissions[0][1].task == "memory.consolidate"
    assert model.submissions[0][1].data_generation == waiting["manifest"]["data_generation"]
    async with core.database.transaction(read_only=True) as conn:
        state = await (await conn.execute("SELECT lease_owner FROM memory.consolidation_state WHERE partition_id=%s",
                                          (partition,))).fetchone()
        indexing = await (await conn.execute("SELECT k.data_generation,j.args FROM runtime.job_keys k "
            "JOIN procrastinate_jobs j ON j.id=k.job_id WHERE k.task='memory.index_object' "
            "AND j.args->>'id'=%s",(item.episode_id,))).fetchone()
    assert state["lease_owner"] is None
    assert indexing["data_generation"] == indexing["args"]["generation"] == waiting["manifest"]["data_generation"]
    model.ready = True
    await core.workers.consolidate(partition)
    _, applied = await batch_for(core, item.episode_id)
    assert applied["status"] == "applied" and len(model.submissions) == 1


async def test_previous_generation_completion_cannot_apply_after_reset(core, ordinary_users):
    alice, _ = ordinary_users
    model = DeferredModel()
    configure(core, model)
    item = await episode(core, alice, "rb-old-generation")
    partition, _ = await batch_for(core, item.episode_id)
    await core.workers.consolidate(partition)
    _, old_batch = await batch_for(core, item.episode_id)
    async with core.database.transaction() as conn:
        await conn.execute("UPDATE runtime.installation_generation SET id=%s WHERE singleton=true",
                           (old_batch["manifest"]["data_generation"] + ":new",))
    model.ready = True
    await core.workers.consolidate(partition)
    _, batch = await batch_for(core, item.episode_id)
    async with core.database.transaction(read_only=True) as conn:
        count = await (await conn.execute("SELECT count(*) AS count FROM memory.claims")).fetchone()
        stale_wakes = await (await conn.execute("SELECT count(*) AS count FROM runtime.job_keys "
            "WHERE data_generation=%s AND task='memory.consolidate'",
            (old_batch["manifest"]["data_generation"] + ":new",))).fetchone()
    assert batch["status"] == "waiting_model" and count["count"] == 0
    assert stale_wakes["count"] == 0


async def test_same_scene_different_principals_keep_separate_batch_authority(core, ordinary_users):
    alice, bob = ordinary_users
    group, scope = "rb-shared-scene", "rb-shared-scope"
    async with core.database.transaction() as conn:
        await conn.execute("INSERT INTO identity.scopes(scope_id,actor_id,kind,label) VALUES(%s,%s,'group','group')",
                           (scope,core.actor))
        await conn.execute("INSERT INTO identity.conversations(conversation_id,actor_id,scope_id,kind,label) "
                           "VALUES(%s,%s,%s,'group','group')",(group,core.actor,scope))
        for context in (alice,bob):
            await conn.execute("INSERT INTO identity.scope_grants(scope_id,principal_id,permissions) "
                "VALUES(%s,%s,ARRAY['*'])",(scope,context.principal_id))
    contexts = [await core.identity.context(value.principal_id,actor_id=core.actor,conversation_id=group)
                for value in (alice,bob)]
    model = RecordedModel()
    configure(core, model)
    first = await episode(core,contexts[0],"rb-group-alice")
    second = await episode(core,contexts[1],"rb-group-bob")
    async with core.database.transaction(read_only=True) as conn:
        rows = await (await conn.execute("SELECT episode_id,consolidation_partition_id FROM memory.episodes "
            "WHERE episode_id=ANY(%s::text[])",([first.episode_id,second.episode_id],))).fetchall()
    assert len({row["consolidation_partition_id"] for row in rows}) == 1
    partition = rows[0]["consolidation_partition_id"]
    await core.workers.consolidate(partition)
    await core.workers.consolidate(partition)
    async with core.database.transaction(read_only=True) as conn:
        batches = await (await conn.execute("SELECT manifest,status FROM memory.consolidation_batches "
            "WHERE partition_id=%s ORDER BY created_at",(partition,))).fetchall()
    assert len(batches) == 2 and all(batch["status"] == "applied" for batch in batches)
    assert [batch["manifest"]["authority_bound"]["principal_id"] for batch in batches] == [alice.principal_id,bob.principal_id]
    assert all(len(batch["manifest"]["episodes"]) == 1 for batch in batches)


async def test_truncated_batch_without_proven_closure_is_not_split(core, ordinary_users):
    alice, _ = ordinary_users
    model = TruncatedOnceModel()
    configure(core, model)
    first = await episode(core, alice, "rb-split-a")
    second = await episode(core, alice, "rb-split-b")
    partition, _ = await batch_for(core, first.episode_id)
    await core.workers.consolidate(partition)
    _, parent = await batch_for(core, first.episode_id)
    assert parent["status"] == "failed"
    with pytest.raises(AppError, match="memory_consolidation_batch_terminal"):
        await core.memory.consolidation._apply(parent, alice, batch_frozen(parent), consolidation_example(batch_frozen(parent)))
    async with core.database.transaction(read_only=True) as conn:
        parts = await (await conn.execute("SELECT * FROM memory.consolidation_reorganization_parts "
            "WHERE parent_batch_id=%s ORDER BY ordinal", (parent["batch_id"],))).fetchall()
        rows = await (await conn.execute("SELECT episode_id,extraction_state FROM memory.episodes "
            "WHERE episode_id=ANY(%s::text[])", ([first.episode_id, second.episode_id],))).fetchall()
    assert not parts
    assert all(row["extraction_state"] == "consolidation_failed" for row in rows)
    assert len(model.submissions) == 1


async def test_withdrawing_non_support_input_invalidates_generated_claim(core, ordinary_users):
    alice, _ = ordinary_users
    model, stats = RecordedModel(), StatisticsProbe()
    stats.allow = True
    configure(core, model)
    core.memory.bind_observability(statistics=stats,recovery_policy=None)
    first = await episode(core, alice, "rb-context-a")
    second = await episode(core, alice, "rb-context-b")
    partition, _ = await batch_for(core, first.episode_id)
    await core.workers.consolidate(partition)
    before = await core.claims.list(alice)
    assert len(before.items) == 1
    # The second source was visible in the generation input but was not in
    # this claim's support_groups. It remains a necessary derivation source.
    assert any(ref.source_id == second.source_refs[0].source_id for ref in before.items[0].derivation_refs)
    await core.sources.invalidate(alice, {"source_id": second.source_refs[0].source_id,
        "expected_revision": second.source_refs[0].revision, "client_request_id": "rb-context-forget"}, forgotten=True)
    assert not (await core.claims.list(alice)).items
    async with core.database.transaction() as conn:
        await core.memory.invalidate_actor(conn,second.source_refs,"forgotten",actor_id=core.actor)
    assert stats.events[-1]["dimensions"]["state"] == "cancelled"
    assert stats.events[-1]["metrics"]["memory_applied"] == 0


async def test_episode_semantic_history_and_forgetting(core, ordinary_users):
    alice, _ = ordinary_users
    item = await episode(core, alice, "rb-history")
    updated = await core.memory.observe_episode(alice, {"event_id": item.event_id, "revision": 2,
        "occurred_at": item.occurred_at, "summary": "后来更正为梨", "source_refs": item.source_refs,
        "origin_kind": "human"})
    assert updated.revision > item.revision
    old = await core.memory.episode_at(alice, item.episode_id, time_mode="known_at", known_at=item.recorded_at)
    assert old.summary == "我喜欢苹果" and old.revision == item.revision
    assert (await core.memory.episode_at(alice, item.episode_id)).summary == "后来更正为梨"
    async with core.database.transaction() as conn:
        locked = await core.memory.lock_episodes(alice, ((item.episode_id, updated.revision),), connection=conn)
        stale = await core.memory.lock_episodes(alice, ((item.episode_id, item.revision),), connection=conn)
        assert locked.coverage.complete and locked.items[0].revision == updated.revision
        assert not stale.coverage.complete and not stale.items
    # Replaying identical source content is metadata, not another semantic snapshot.
    await core.memory.observe_episode(alice, {"event_id": item.event_id, "revision": 2,
        "occurred_at": item.occurred_at, "summary": "后来更正为梨", "source_refs": item.source_refs,
        "origin_kind": "human"})
    async with core.database.transaction(read_only=True) as conn:
        count = await (await conn.execute("SELECT count(*) AS n FROM memory.episode_revisions WHERE episode_id=%s",
                                          (item.episode_id,))).fetchone()
    assert count["n"] == 2
    await core.sources.invalidate(alice, {"source_id": item.source_refs[0].source_id,
        "expected_revision": item.source_refs[0].revision, "client_request_id": "rb-forget"}, forgotten=True)
    with pytest.raises(AppError):
        await core.memory.episode_at(alice, item.episode_id, time_mode="known_at", known_at=item.recorded_at)


async def test_late_media_is_idempotent_and_its_withdrawal_preserves_original_text(core, ordinary_users):
    alice, _ = ordinary_users
    at = datetime.now(UTC)
    event = "rb-late-media"
    refs = await core.sources.record_event(alice, producer="web", event_id=event, revision=1, occurred_at=at,
        parts=({"kind":"text","text":"这是图片说明"},
               {"kind":"image","text":None,"metadata":{"original_pending":True}}), origin_kind=OriginKind.HUMAN)
    original = await core.memory.observe_episode(alice, {"event_id":event,"revision":1,"occurred_at":at,
        "summary":"这是图片说明","source_refs":refs,"origin_kind":"human"})
    png = b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/a8sAAAAASUVORK5CYII=")
    media = await asyncio.to_thread(core.files.put_bytes,png,media_type="image/png")
    supplement = await core.sources.register_media_supplement(alice,media,original_ref=refs[1],producer="web",
        event_id=event,event_revision=1,part_index=1,supplement_revision=1)
    kwargs = {"episode_id":original.episode_id,"event_id":event,"event_revision":1,"part_index":1,
              "supplement_revision":supplement.revision,"original_ref":refs[1],"supplement_ref":supplement}
    updated = await core.memory.supplement_episode(alice,**kwargs)
    again = await core.memory.supplement_episode(alice,**kwargs)
    replay = await core.memory.observe_episode(alice, {"event_id":event,"revision":1,"occurred_at":at,
        "summary":"这是图片说明","source_refs":refs,"origin_kind":"human"})
    assert updated.episode_id == original.episode_id and updated.revision == original.revision + 1
    assert again.revision == replay.revision == updated.revision and updated.source_refs == original.source_refs
    assert updated.supplement_refs == (supplement,)
    await core.sources.invalidate(alice,{"source_id":supplement.source_id,"expected_revision":supplement.revision,
        "client_request_id":"rb-media-forget"},forgotten=True)
    with pytest.raises(AppError):
        await core.memory.episode_at(alice,original.episode_id)
    async with core.database.transaction() as conn:
        await core.memory.invalidate_actor(conn,(supplement,),"forgotten",actor_id=core.actor)
    current = await core.memory.episode_at(alice,original.episode_id)
    assert not current.summary and not current.supplement_refs
    assert (await core.sources.read(alice,refs[0],{}))["content"] == "这是图片说明"
    assert "media_unavailable" in current.extraction_coverage.missing
