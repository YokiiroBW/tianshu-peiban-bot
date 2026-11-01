"""Actual incremental consumers, source windows, and interpreted command provenance."""

import json
import time
from datetime import UTC, datetime, timedelta
from dataclasses import replace

import pytest

from tianshu.models.types import ModelPart, ModelReceipt, ModelResult
from tianshu.runtime.errors import AppError
from tianshu.runtime.serialization import wire
from tianshu.types import ContextContribution, OriginKind, RequestBudget, SourceRef


class Extractor:
    def __init__(self):
        self.requests = []
        self.results = {}

    async def generate(self, request):
        self.requests.append(request)
        material=json.loads(request.messages[0].parts[0].text)
        operations=[]
        results=[]
        for target in material["targets"]:
            op_ids=[]
            for source in material["materials"]:
                if source["id"] not in target["new_material_ids"] or "尾部事实" not in source["text"]:
                    continue
                op_id="op"+str(len(operations)+1)
                op_ids.append(op_id)
                operations.append({"op_id":op_id,"kind":"add_claim","subject_id":target["subject_id"],
                    "target":{"create":"n"+str(len(operations)+1)},"payload":{
                        "predicate":"interest","value":"尾部事实","speaker_id":source["speaker_id"],
                        "assertion":"self_confirmed" if source["origin_kind"]=="human" else "observed",
                        "polarity":True,"conditions":{},"valid_from":None,"valid_until":None},
                    "support_groups":[[source["id"]]],"context_refs":[],"reads":[],"reason":"原件支持"})
            results.append({"subject_id":target["subject_id"],"status":"changed" if op_ids else "no_change",
                "processed_material_ids":target["new_material_ids"],"operation_ids":op_ids})
        receipt=ModelReceipt(request.attempt_id,request.intent_id,"succeeded","submitted","fixture",1,
            "fixture","fixture","openai.chat","fixture")
        result=ModelResult((ModelPart("text",text=json.dumps({"schema_version":material["schema_version"],
            "batch_id":material["batch_id"],"target_results":results,"operations":operations,"unresolved":[],"needs_context":[]})),),receipt)
        self.results[request.attempt_id]=result
        return result

    async def result(self,attempt_id,*,context):
        return self.results.get(attempt_id)


async def run_pending(core, episode):
    policy=replace(core.memory.consolidation.policy,quiet_seconds=0,max_wait_seconds=0)
    core.memory.consolidation.policy=policy
    core.memory.consolidation.materials.policy=policy
    for _ in range(80):
        async with core.database.transaction() as conn:
            row=await (await conn.execute("SELECT extraction_state,consolidation_partition_id FROM memory.episodes WHERE episode_id=%s",
                (episode.episode_id,))).fetchone()
            if row["extraction_state"] != "pending":
                return row["extraction_state"]
            await conn.execute("UPDATE memory.consolidation_state SET wake_due_at=now() WHERE partition_id=%s",(row["consolidation_partition_id"],))
        await core.workers.consolidate(row["consolidation_partition_id"])
    pytest.fail("incremental consolidation did not finish within its bounded material count")


async def test_long_original_and_more_than_128_parts_reach_the_tail(core, ordinary_users):
    alice, _ = ordinary_users
    refs = await core.sources.record_event(
        alice,
        producer="web",
        event_id="long-many-parts",
        revision=1,
        occurred_at=datetime.now(UTC),
        parts=tuple(
            {"kind": "text", "text": text}
            for text in ["填" * 1700, *(["普通片段"] * 127), "尾部事实"]
        ),
        origin_kind=OriginKind.HUMAN,
    )
    episode = await core.memory.observe_episode(
        alice,
        {
            "event_id": "long-many-parts",
            "occurred_at": datetime.now(UTC),
            "summary": "长事件",
            "source_refs": refs,
            "origin_kind": "human",
        },
    )
    models = Extractor()
    core.steps.models = models
    assert await run_pending(core, episode) == "completed"
    items = (await core.claims.list(alice)).items
    tail = next(item for item in items if item.value == "尾部事实")
    assert tail.source_refs[0].source_id == refs[-1].source_id
    spans=sorted((ref.locator["start"],ref.locator["end"])
        for request in models.requests for ref in request.source_refs
        if ref.source_id==refs[0].source_id and "start" in ref.locator and "end" in ref.locator)
    assert spans[0][0]==0 and max(end for _,end in spans)==1700
    covered=0
    for start,end in spans:
        assert start<=covered
        covered=max(covered,end)
    assert covered==1700
    assert len({request.attempt_id for request in models.requests}) == len(models.requests)


async def test_video_actual_consumption_becomes_observed_evidence(core, ordinary_users):
    alice, _ = ordinary_users
    media = core.files.put_bytes(b"synthetic video original", media_type="video/mp4")
    source = await core.sources.register_media(
        alice, media, locator={"producer": "upload", "event_id": "clip"}
    )
    consumption = {
        "source": wire(source),
        "analysis_completed": True,
        "model_attempt_id": "actual-analysis-attempt",
        "ranges": [{"start_ms": 1000, "end_ms": 2000}],
        "frame_times_ms": [1000, 1500],
        "modalities": ["image"],
        "omitted": ["audio_not_sampled"],
    }
    episode = await core.memory.observe_episode(
        alice,
        {
            "event_id": "action:video",
            "occurred_at": datetime.now(UTC),
            "summary": "读取选定视频片段",
            "source_refs": (source,),
            "origin_kind": "tool_result",
            "actual_result": {
                "capability_id": "video.observe",
                "execution": "succeeded",
                "analysis": "尾部事实",
                "consumption": consumption,
            },
        },
    )
    core.steps.models = Extractor()
    assert await run_pending(core, episode) == "completed_with_missing"
    item = next(item for item in (await core.claims.list(alice)).items if item.value == "尾部事实")
    assert item.assertion == "observed"
    original = await core.sources.read(alice, item.source_refs[0], {})
    assert original["origin_kind"] == "tool_result"
    assert original["locator"]["consumption"] == consumption
    await core.sources.invalidate(
        alice,
        {
            "source_id": source.source_id,
            "expected_revision": 1,
            "client_request_id": "forget-video",
        },
        forgotten=True,
    )
    with pytest.raises(AppError):
        await core.claims.get(alice, item.claim_id)


async def test_fragment_cannot_be_read_before_selected_start(core, ordinary_users):
    alice, _ = ordinary_users
    refs = await core.sources.record_event(
        alice,
        producer="web",
        event_id="window",
        revision=1,
        occurred_at=datetime.now(UTC),
        parts=({"kind": "text", "text": "私密前文公开片段私密后文"},),
        origin_kind=OriginKind.HUMAN,
    )
    selected = SourceRef(refs[0].source_id, 1, {"start": 4, "end": 8})
    with pytest.raises(AppError, match="source_position_outside_reference"):
        await core.sources.read(alice, selected, {"position": 0})
    assert (await core.sources.read(alice, selected, {}))["content"] == "公开片段"


async def test_interpreted_commands_keep_real_input_and_do_not_self_confirm(core, ordinary_users):
    alice, _ = ordinary_users
    refs = await core.sources.record_event(
        alice,
        producer="web",
        event_id="command",
        revision=1,
        occurred_at=datetime.now(UTC),
        parts=({"kind": "text", "text": "把苹果更正为香蕉，并把旅行改到明天。"},),
        origin_kind=OriginKind.HUMAN,
    )
    async with core.database.transaction() as conn:
        old = await core.claims.put_on(
            conn, alice, {"predicate": "interest", "value": "苹果", "source_refs": refs}
        )
    corrected = await core.claims.correct(
        alice,
        {
            "claim_id": old.claim_id,
            "expected_revision": old.revision,
            "value": "香蕉",
            "client_request_id": "interpreted-correct",
        },
        evidence_refs=refs,
    )
    assert corrected.data.assertion == "inferred" and corrected.data.source_refs == refs
    matter = await core.claims.save_matter(
        alice,
        {
            "matter_id": "travel",
            "expected_revision": 0,
            "title": "旅行",
            "state": "rescheduled",
            "client_request_id": "interpreted-matter",
        },
        evidence_refs=refs,
    )
    assert matter.data.interpretation and matter.data.source_refs == refs
    async with core.database.transaction(read_only=True) as conn:
        made_up = await (
            await conn.execute(
                "SELECT count(*) AS count FROM sources.versions WHERE locator->>'producer' "
                "IN ('web.claim.correct','web.matter')"
            )
        ).fetchone()
    assert made_up["count"] == 0


async def test_structured_and_lexical_memory_survive_optional_embedding_timeout(
    core, ordinary_users
):
    alice, _ = ordinary_users
    refs = await core.sources.record_event(
        alice,
        producer="web",
        event_id="lexical",
        revision=1,
        occurred_at=datetime.now(UTC),
        parts=({"kind": "text", "text": "喜欢苹果，计划旅行。"},),
        origin_kind=OriginKind.HUMAN,
    )
    async with core.database.transaction() as conn:
        await core.claims.put_on(
            conn, alice, {"predicate": "interest", "value": "苹果", "source_refs": refs}
        )
    await core.claims.save_matter(
        alice,
        {
            "matter_id": "trip",
            "title": "旅行",
            "state": "planned",
            "expected_revision": 0,
            "source_refs": refs,
            "client_request_id": "trip",
        },
    )

    class SlowEmbedding:
        async def vector(self, *args, **kwargs):
            raise TimeoutError("embedding_deadline_exceeded")

    core.retrieval.embeddings = SlowEmbedding()
    core.retrieval.settings = {"vector": True}
    pack = await core.assembler.assemble(
        alice,
        "chat",
        "苹果",
        RequestBudget(datetime.now(UTC) + timedelta(seconds=5)),
        max_input_tokens=16000,
        contributions=(ContextContribution("input", "", refs, alice.scope_ids, 100, True),),
    )
    evidence = [json.loads(item.text) for item in pack.materials if item.kind == "memory_evidence"]
    assert {item["kind"] for item in evidence} >= {"matter", "claim"}
    assert next(item for item in evidence if item["kind"] == "matter")["state"] == "planned"
    assert next(item for item in evidence if item["kind"] == "claim")["assertion"]
    assert "embedding_unavailable" in pack.missing


async def test_author_original_publication_has_no_fabricated_human_evidence(core, ordinary_users):
    alice, bob = ordinary_users
    published = await core.sources.disclose(
        alice,
        producer="writing",
        event_id="original-work",
        revision=1,
        parts=({"kind": "text", "text": "作者独立创作的故事。"},),
        source_refs=(),
        reader_ids=(bob.principal_id,),
    )
    reader = await core.identity.context(bob.principal_id, actor_id=core.actor)
    original = await core.sources.read(reader, published, {})
    assert original["origin_kind"] == "actor" and original["content"] == "作者独立创作的故事。"


async def test_sparse_authorized_vector_candidates_are_not_starved(
    core, ordinary_users, monkeypatch
):
    import psycopg
    from pgvector import Vector
    from pgvector.psycopg import register_vector_async

    alice, bob = ordinary_users
    templates = []
    for context, name in ((alice, "private"), (bob, "allowed")):
        refs = await core.sources.record_event(
            context,
            producer="web",
            event_id="vector:" + name,
            revision=1,
            occurred_at=datetime.now(UTC),
            parts=({"kind": "text", "text": name},),
            origin_kind=OriginKind.HUMAN,
        )
        async with core.database.transaction() as conn:
            value = await core.claims.put_on(
                conn, context, {"predicate": "interest", "value": name, "source_refs": refs}
            )
        templates.append((context, value, refs))
    columns = (
        "revision,speaker_id,subject_id,predicate,value,assertion,polarity,conditions,valid_from,valid_until,"
        "observed_at,state,source_refs,support_groups,scope_groups,scope_ids,matter_id,supersedes,contradictions,"
        "evidence_units,content,lexical,embedding,embedding_profile,recorded_at,change_kind,memory_kind,"
        "current_status,semantic_fingerprint,derivation_refs,counterexample_refs,change_reason"
    )
    async with core.database.transaction() as conn:
        await register_vector_async(conn)
        for number, (context, value, refs) in enumerate(templates):
            vector = [1.0, 0.0] + [0.0] * 1022 if number == 0 else [0.0, 1.0] + [0.0] * 1022
            await conn.execute(
                "UPDATE memory.claim_revisions SET embedding=%s,embedding_profile='fixture' WHERE claim_id=%s",
                (Vector(vector), value.claim_id),
            )
            count = 2100 if number == 0 else 96
            await conn.execute(
                "INSERT INTO memory.claims(claim_id,current_revision,actor_id,principal_id) "
                "SELECT %s||g::text,1,%s,%s FROM generate_series(1,%s) g",
                ("seed:" + str(number) + ":", context.actor_id, context.principal_id, count),
            )
            await conn.execute(
                "INSERT INTO memory.claim_revisions(claim_id,"
                + columns
                + ") SELECT %s||g::text,"
                + ",".join("r." + name for name in columns.split(","))
                + " FROM memory.claim_revisions r CROSS JOIN generate_series(1,%s) g WHERE r.claim_id=%s",
                ("seed:" + str(number) + ":", count, value.claim_id),
            )

    class QueryEmbedding:
        async def vector(self, *args, **kwargs):
            return [1.0] + [0.0] * 1023, "fixture"

    core.retrieval.embeddings = QueryEmbedding()
    core.retrieval.settings = {"vector": True, "vector_sql_seconds": 5}
    measured = []
    execute = psycopg.AsyncConnection.execute

    async def diagnostic_execute(connection, query, *args, **kwargs):
        statement = str(query)
        phase = (
            "vector_count"
            if statement.startswith("SELECT count(*) AS count FROM(SELECT 1 ")
            else "vector_exact"
            if statement.startswith("WITH scoped AS MATERIALIZED")
            else "vector_ann"
            if "ORDER BY r.embedding<=>" in statement
            else None
        )
        started = time.monotonic()
        error = None
        try:
            return await execute(connection, query, *args, **kwargs)
        except BaseException as exc:
            error = {"type": type(exc).__name__, "sqlstate": getattr(exc, "sqlstate", None)}
            raise
        finally:
            if phase:
                measured.append(
                    {
                        "phase": phase,
                        "seconds": round(time.monotonic() - started, 3),
                        "error": error,
                    }
                )

    monkeypatch.setattr(psycopg.AsyncConnection, "execute", diagnostic_execute)
    material, missing = await core.retrieval.search(
        bob,
        "does-not-match-lexically",
        RequestBudget(datetime.now(UTC) + timedelta(seconds=10)),
        limit=12,
    )
    assert len(material) == 12 and not missing, {
        "material_count": len(material),
        "missing": missing,
        "queries": measured,
    }
    assert all(item.source_refs == templates[1][2] for item in material)
    assert all("private" not in item.text for item in material)
