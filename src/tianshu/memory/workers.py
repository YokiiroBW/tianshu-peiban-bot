"""Incremental extraction and indexing from authorized originals, never a history scan."""

from datetime import UTC, datetime, timedelta
import json
from uuid import NAMESPACE_URL, uuid4, uuid5

from jsonschema import Draft202012Validator
from psycopg.types.json import Jsonb
from pgvector import Vector
from pgvector.psycopg import register_vector_async

from tianshu.models.types import ModelRequest
from tianshu.runtime.errors import AppError
from tianshu.runtime.commands import body_hash
from tianshu.runtime.jobs import JobSpec
from tianshu.runtime.serialization import wire
from tianshu.types import ContextContribution, OriginKind, RequestBudget, ServiceClass, SourceRef
from tianshu.sources.dependencies import unique_refs
from . import codec


EXTRACTION = {
    "type": "object",
    "required": ["claims", "matters"],
    "additionalProperties": False,
    "properties": {
        "claims": {
            "type": "array",
            "maxItems": 64,
            "items": {
                "type": "object",
                "required": ["subject_id", "predicate", "value", "source_indexes"],
                "properties": {
                    "subject_id": {"type": "string"},
                    "predicate": {"type": "string"},
                    "value": {},
                    "assertion": {
                        "enum": ["self_confirmed", "reported", "quoted", "inferred", "observed"]
                    },
                    "polarity": {"type": "boolean"},
                    "conditions": {"type": "object"},
                    "valid_from": {"type": ["string", "null"]},
                    "valid_until": {"type": ["string", "null"]},
                    "source_indexes": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 8,
                        "items": {"type": "integer", "minimum": 0},
                    },
                },
                "additionalProperties": False,
            },
        },
        "matters": {
            "type": "array",
            "maxItems": 16,
            "items": {
                "type": "object",
                "required": ["matter_id", "subject_id", "title", "state", "source_indexes"],
                "properties": {
                    "matter_id": {"type": "string"},
                    "subject_id": {"type": "string"},
                    "title": {"type": "string"},
                    "state": {
                        "enum": ["planned", "active", "rescheduled", "cancelled", "uncertain"]
                    },
                    "due_at": {"type": ["string", "null"]},
                    "summary": {"type": "string"},
                    "conditions": {"type": "object"},
                    "source_indexes": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 8,
                        "items": {"type": "integer", "minimum": 0},
                    },
                },
                "additionalProperties": False,
            },
        },
    },
}
VALIDATOR = Draft202012Validator(EXTRACTION)


class MemoryWorkers:
    def __init__(self, memory, assembler, embeddings, steps):
        self.memory, self.assembler, self.embeddings, self.steps = (
            memory,
            assembler,
            embeddings,
            steps,
        )
        self.database, self.identity, self.sources = (
            memory.database,
            memory.identity,
            memory.sources,
        )

    async def _episode(self, episode_id, sequence):
        async with self.database.transaction() as conn:
            row = await (
                await conn.execute(
                    "SELECT * FROM memory.episodes WHERE episode_id=%s AND sequence=%s AND active FOR UPDATE",
                    (episode_id, sequence),
                )
            ).fetchone()
            if not row or row["extraction_state"] not in {"pending", "running"}:
                return None, None, ()
            if (
                row["extraction_state"] == "running"
                and row["extraction_started_at"]
                and row["extraction_started_at"] > datetime.now(UTC) - timedelta(minutes=3)
            ):
                return None, None, ()
            context = await self.identity.context_on(
                conn,
                row["principal_id"],
                actor_id=row["actor_id"],
                person_id=row["person_id"],
                conversation_id=row["conversation_id"],
                origin_kind=OriginKind(row["origin_kind"]),
            )
            subjects = await self.identity.subjects_on(conn, context)
            await conn.execute(
                "UPDATE memory.episodes SET extraction_state='running',extraction_started_at=now() WHERE episode_id=%s AND sequence=%s",
                (episode_id, sequence),
            )
        return row, context, subjects

    async def _material_sources(self, row, context):
        existing = codec.refs(row["extraction_sources"])
        if existing:
            return existing, tuple(row["extraction_missing"])
        refs = codec.refs(row["source_refs"])
        missing = []
        actual = row["actual_result"] or {}
        consumption = actual.get("consumption") if isinstance(actual, dict) else None
        analysis = actual.get("analysis") if isinstance(actual, dict) else None
        if row["origin_kind"] == "tool_result" and consumption:
            source_value = consumption.get("source") if isinstance(consumption, dict) else None
            source = (
                SourceRef(**source_value)
                if isinstance(source_value, dict)
                and isinstance(source_value.get("source_id"), str)
                and type(source_value.get("revision")) is int
                else None
            )
            matching = source and any(
                (ref.source_id, ref.revision) == (source.source_id, source.revision) for ref in refs
            )
            if (
                matching
                and actual.get("capability_id") == "video.observe"
                and actual.get("execution") == "succeeded"
                and consumption.get("analysis_completed") is True
                and isinstance(analysis, str)
                and analysis
                and isinstance(consumption.get("model_attempt_id"), str)
            ):
                async with self.database.transaction() as conn:
                    descriptions = await self.sources.describe_for_write_on(
                        conn, context, (source,)
                    )
                derived = await self.sources.capture(
                    context,
                    locator={
                        "producer": "memory.observation",
                        "event_id": row["episode_id"],
                        "revision": row["revision"],
                        "support_groups": ((source,),),
                        "consumption": consumption,
                        "evidence_unit": descriptions[0]["evidence_unit"],
                    },
                    raw=analysis.encode("utf-8"),
                    mime_type="text/plain",
                )
                refs = unique_refs(refs, (derived,))
            else:
                missing.append("observation_analysis_unavailable")
        async with self.database.transaction() as conn:
            if not await self.sources.validate_persistence(conn, context, refs):
                raise AppError("source_unavailable", status=403)
            fresh = await (
                await conn.execute(
                    "SELECT sequence,active FROM memory.episodes WHERE episode_id=%s FOR UPDATE",
                    (row["episode_id"],),
                )
            ).fetchone()
            if not fresh or fresh["sequence"] != row["sequence"] or not fresh["active"]:
                raise AppError("episode_changed", status=409)
            await conn.execute(
                "UPDATE memory.episodes SET extraction_sources=%s,extraction_missing=%s WHERE episode_id=%s",
                (Jsonb(wire(refs)), missing, row["episode_id"]),
            )
        return refs, tuple(missing)

    async def extract(self, episode_id, sequence):
        row, context, subjects = await self._episode(episode_id, sequence)
        if not row:
            return
        try:
            refs, missing = await self._material_sources(row, context)
            if not refs:
                await self._state(episode_id, sequence, "no_original_material")
                return
            cursor = row["extraction_cursor"] or {"source_index": 0, "position": None, "step": 0}
            index, position, step = cursor["source_index"], cursor.get("position"), cursor["step"]
            selections = []
            for offset, ref in enumerate(refs[index : index + 4]):
                requested = {"max_characters": 1600}
                if offset == 0 and position is not None:
                    requested["position"] = position
                selections.append((ref, requested))
            originals = await self.sources.read_many(context, tuple(selections))
            selected = []
            material_sources = []
            next_cursor = {"source_index": index, "position": position, "step": step + 1}
            omitted = set(missing)
            for offset, item in enumerate(originals):
                if item["content"]:
                    span = item["coverage"].ranges[0]
                    selected.append(
                        SourceRef(
                            item["source_ref"].source_id,
                            item["source_ref"].revision,
                            {**item["source_ref"].locator, **span},
                        )
                    )
                    material_sources.append(
                        {
                            "index": len(selected) - 1,
                            "content": item["content"],
                            "coverage": item["coverage"],
                            "origin_kind": item["origin_kind"],
                            "locator": item["locator"],
                        }
                    )
                elif item["media"] and not (row["actual_result"] or {}).get("consumption"):
                    omitted.add("media_without_confirmed_analysis")
                if item["next_position"] is not None:
                    next_cursor.update(source_index=index + offset, position=item["next_position"])
                    break
                next_cursor.update(source_index=index + offset + 1, position=None)
            parsed = {"claims": [], "matters": []}
            dependencies = unique_refs((ref for item in originals for ref in (item["source_ref"],)))
            matter_ids = set()
            if selected:
                matters = await self.memory.claims.matters(
                    context, subject_id=context.person_id, limit=20
                )
                matter_ids = {item.matter_id for item in matters.items}
                budget = RequestBudget(datetime.now(UTC) + timedelta(seconds=120))
                material = {
                    "speaker_id": context.person_id,
                    "subjects": subjects,
                    "actor_id": "actor:" + context.actor_id,
                    "origin_kind": row["origin_kind"],
                    "occurred_at": row["occurred_at"],
                    "current_matters": matters.items,
                    "sources": material_sources,
                }
                instruction = (
                    "从本批实际原件或有消费回执的派生观察抽取事实与事项，按JSON Schema返回唯一对象。"
                    "保留说话者/对象、本人确认/转述/引用、否定、条件、时间与改期取消。工具/网页/观看分析/角色自述不能成为用户本人确认。"
                    "媒体只能描述consumption实际ranges/frame_times/modalities，保留omitted和采样限制，不能声称看过整段或听见缺失音轨。"
                    "旧材料不能覆盖新当前态；无明确证据返回空数组，不推断已完成。source_indexes只选本批。\n"
                    + json.dumps(EXTRACTION, ensure_ascii=False)
                )
                dependencies = unique_refs(
                    selected, (ref for item in matters.items for ref in item.source_refs)
                )
                pack = await self.assembler.assemble(
                    context,
                    "extract",
                    instruction,
                    budget,
                    max_input_tokens=32000,
                    reserved_output_tokens=4096,
                    contributions=(
                        ContextContribution(
                            "original_evidence",
                            json.dumps(wire(material), ensure_ascii=False),
                            dependencies,
                            context.scope_ids,
                            100,
                            True,
                            f"{sequence}:{step}",
                        ),
                    ),
                )
                name = f"extract:{episode_id}:{sequence}:{step}"
                request = ModelRequest(
                    name,
                    str(uuid5(NAMESPACE_URL, name)),
                    "memory.extract",
                    ServiceClass.ELASTIC,
                    context,
                    budget,
                    pack.messages,
                    system=pack.system,
                    max_output_tokens=4096,
                    estimated_input_tokens=pack.token_usage.estimated_tokens,
                    source_refs=pack.source_refs,
                )
                result, recorded = await self.steps.run_recorded(name, request)
                parsed = json.loads(
                    "".join(part.text or "" for part in result.parts if part.kind == "text")
                )
                if not VALIDATOR.is_valid(parsed):
                    raise AppError("memory_extraction_invalid", status=502)
                dependencies = recorded.source_refs
            async with self.database.transaction() as conn:
                if not await self.sources.validate_persistence(conn, context, dependencies):
                    raise AppError("source_unavailable", status=403)
                fresh = await (
                    await conn.execute(
                        "SELECT sequence,active,extraction_cursor FROM memory.episodes WHERE episode_id=%s FOR UPDATE",
                        (episode_id,),
                    )
                ).fetchone()
                if (
                    not fresh
                    or fresh["sequence"] != sequence
                    or not fresh["active"]
                    or fresh["extraction_cursor"] != row["extraction_cursor"]
                ):
                    return
                for kind in ("claims", "matters"):
                    for item in parsed[kind]:
                        indexes = item.pop("source_indexes")
                        if any(index >= len(selected) for index in indexes):
                            raise AppError("memory_extraction_source_invalid", status=502)
                        evidence = tuple(selected[index] for index in dict.fromkeys(indexes))
                        value = {
                            **item,
                            "source_refs": evidence,
                            "observed_at": row["occurred_at"],
                            "speaker_id": context.person_id,
                        }
                        if kind == "claims":
                            if (
                                any(
                                    material_sources[index]["origin_kind"] != "human"
                                    for index in indexes
                                )
                                and value.get("assertion") == "self_confirmed"
                            ):
                                value["assertion"] = "observed"
                            await self.memory.claims.put_on(conn, context, value, automatic=True)
                        else:
                            if value["matter_id"] not in matter_ids:
                                value["matter_id"] = (
                                    "matter_"
                                    + body_hash(
                                        {
                                            "actor": context.actor_id,
                                            "subject": value["subject_id"],
                                            "title": value["title"],
                                        }
                                    )[:48]
                                )
                            await self.memory.claims.matter_on(conn, context, value, automatic=True)
                more = next_cursor["source_index"] < len(refs)
                state = "pending" if more else "completed_with_missing" if omitted else "completed"
                await conn.execute(
                    "UPDATE memory.episodes SET extraction_cursor=%s,extraction_state=%s,extraction_missing=%s,extraction_started_at=NULL "
                    "WHERE episode_id=%s",
                    (Jsonb(next_cursor), state, sorted(omitted), episode_id),
                )
                if more:
                    await self.memory.jobs.enqueue(
                        conn,
                        JobSpec(
                            "memory.extract",
                            f"memory-extract:{episode_id}:{sequence}:{step + 1}",
                            {"episode_id": episode_id, "sequence": sequence},
                            ServiceClass.ELASTIC,
                        ),
                    )
        except (AppError, TimeoutError, ValueError, TypeError) as exc:
            await self._state(episode_id, sequence, getattr(exc, "code", "extraction_unavailable"))

    async def _state(self, episode_id, sequence, state):
        async with self.database.transaction() as conn:
            await conn.execute(
                "UPDATE memory.episodes SET extraction_state=%s WHERE episode_id=%s AND sequence=%s AND active",
                (state, episode_id, sequence),
            )

    async def index(self, claim_id, revision):
        async with self.database.transaction(read_only=True) as conn:
            row = await (
                await conn.execute(
                    "SELECT r.*,c.actor_id,c.principal_id FROM memory.claims c JOIN memory.claim_revisions r "
                    "USING(claim_id) WHERE c.claim_id=%s AND r.revision=%s AND c.current_revision=r.revision AND r.state='active'",
                    (claim_id, revision),
                )
            ).fetchone()
            if not row:
                return
            context = await self.identity.context_on(
                conn, row["principal_id"], actor_id=row["actor_id"]
            )
            selected = (await self.memory.claims.selected_groups(conn, context, (row,)))[0]
        if not selected:
            return
        budget = RequestBudget(datetime.now(UTC) + timedelta(seconds=120))
        try:
            vector, fingerprint = await self.embeddings.vector(
                context,
                row["content"],
                budget,
                intent=f"memory-index:{claim_id}:{revision}",
                source_refs=selected,
            )
        except AppError:
            # Lexical retrieval remains available; configuration or unknown paid attempts are not blindly retried.
            return
        async with self.database.transaction() as conn:
            if not await self.sources.validate_persistence(conn, context, selected):
                return
            await register_vector_async(conn)
            await conn.execute(
                "UPDATE memory.claim_revisions SET embedding=%s,embedding_profile=%s WHERE claim_id=%s AND revision=%s AND state='active'",
                (Vector(vector), fingerprint, claim_id, revision),
            )

    async def recover(self):
        async with self.database.transaction() as conn:
            rows = await (
                await conn.execute(
                    "SELECT episode_id,sequence FROM memory.episodes WHERE active AND (extraction_state='pending' "
                    "OR (extraction_state='running' AND extraction_started_at<now()-interval '3 minutes')) "
                    "ORDER BY sequence LIMIT 64"
                )
            ).fetchall()
            for row in rows:
                await self.memory.jobs.enqueue(
                    conn,
                    JobSpec(
                        "memory.extract",
                        "memory-recover:" + str(uuid4()),
                        dict(row),
                        ServiceClass.ELASTIC,
                    ),
                )
            bucket = int(datetime.now(UTC).timestamp()) // 30 + 1
            await self.memory.jobs.enqueue(
                conn,
                JobSpec(
                    "memory.recover",
                    "memory-recovery:" + str(bucket),
                    {},
                    ServiceClass.DUE,
                    scheduled_at=datetime.fromtimestamp(bucket * 30, UTC),
                ),
            )
