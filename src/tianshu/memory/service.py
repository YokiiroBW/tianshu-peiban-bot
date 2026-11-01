"""Actual episodes, source-backed projections and explicit relationship state."""

from dataclasses import replace
from contextlib import asynccontextmanager
from datetime import UTC, datetime
import json
import re

from psycopg.types.json import Jsonb

from tianshu.runtime.api import CommandResult, Page
from tianshu.runtime.commands import body_hash
from tianshu.runtime.errors import AppError
from tianshu.runtime.jobs import JobSpec
from tianshu.runtime.serialization import wire
from tianshu.types import Coverage, OriginKind, ServiceClass, SourceRef
from . import codec
from .types import EpisodePage
from .domain_utils import parse_time


def cursor_sequence(cursor):
    if cursor is None:
        return 0
    if not isinstance(cursor, str) or not re.fullmatch(r"e:[0-9]{1,20}", cursor):
        raise AppError("invalid_episode_cursor")
    return int(cursor[2:])


class MemoryService:
    def __init__(self, database, identity, sources, commands, jobs, models, *, settings=None):
        self.database, self.identity, self.sources = database, identity, sources
        self.commands, self.jobs, self.models = commands, jobs, models
        self.claims = None
        self.pipeline_recovery = None
        self.episode_observer = None
        self.settings = settings or {}
        from .consolidation import Consolidation
        self.consolidation = Consolidation(self, self.settings.get("consolidation", {}))

    def bind_conversation(self, conversation):
        self.consolidation.materials.bind_conversation(conversation)

    def bind_record_candidates(self,read_port):
        self.consolidation.materials.record_candidates=read_port

    def bind_episode_observer(self,observer):
        if self.episode_observer is not None:
            raise RuntimeError("memory_episode_observer_already_bound")
        self.episode_observer=observer

    async def _episode_observed_on(self,conn,context,row):
        value=codec.episode(row)
        if self.episode_observer:
            await self.episode_observer(context,f"episode:{value.episode_id}:{value.revision}",
                episode=value,connection=conn)
        return value

    def bind_claims(self, claims):
        if self.claims is not None:
            raise RuntimeError("memory_claims_already_bound")
        self.claims = claims

    @asynccontextmanager
    async def connection(self, connection=None):
        if connection is not None:
            yield connection
        else:
            async with self.database.transaction() as opened:
                yield opened

    async def observe_episode(self, context, episode, *, connection=None):
        if not context.actor_id or episode.get("actor_id", context.actor_id) != context.actor_id:
            raise AppError("episode_actor_mismatch", status=403)
        source_refs = codec.refs(episode.get("source_refs", ()))
        occurred_at = parse_time(episode["occurred_at"])
        revision = episode.get("revision", 1)
        if type(revision) is not int or revision < 1:
            raise AppError("invalid_episode_revision")
        event_id = episode.get("event_id") or episode.get("episode_id")
        if not isinstance(event_id, str) or not 1 <= len(event_id) <= 512:
            raise AppError("invalid_episode_event")
        episode_id = (
            episode.get("episode_id")
            or "episode_" + body_hash({"actor": context.actor_id, "event": event_id})[:48]
        )
        summary = episode.get("summary", "")
        if not isinstance(summary, str) or len(summary) > 24000:
            raise AppError("invalid_episode_summary")
        if episode.get("actual_result") is not None and not isinstance(episode["actual_result"], dict):
            raise AppError("invalid_episode_actual_result")
        origin = OriginKind(episode.get("origin_kind", context.origin_kind))
        if not source_refs and origin == OriginKind.HUMAN:
            raise AppError("human_episode_source_required")
        async with self.connection(connection) as conn:
            descriptions = await self.sources.describe_for_write_on(conn, context, source_refs)
            direct_authors={item["speaker_id"] for item in descriptions
                if item["origin_kind"] == "human" and item["speaker_id"]}
            unknown_direct=any(item["origin_kind"] == "human" and not item["speaker_id"] for item in descriptions)
            speaker_id=(next(iter(direct_authors)) if origin == OriginKind.HUMAN
                and len(direct_authors)==1 and not unknown_direct else None)
            scope_ids = sorted({scope for item in descriptions for scope in item["scope_ids"]})
            if not scope_ids:
                scope_ids = list((await self.identity.intake_scope(context, connection=conn))[0])
            semantic = {
                "summary": summary,
                "source_refs": source_refs,
                "origin_kind": origin,
                "actual_result": episode.get("actual_result"),
                "occurred_at": occurred_at,
                "person_id": context.person_id,
                "matter_id": episode.get("matter_id"),
            }
            digest = body_hash(semantic)
            await self.consolidation.lock_partition_on(conn,context,scope_ids)
            existing = await (
                await conn.execute(
                    "SELECT * FROM memory.episodes WHERE actor_id=%s AND event_id=%s FOR UPDATE",
                    (context.actor_id, event_id),
                )
            ).fetchone()
            if existing and existing["content_hash"] == digest:
                if existing.get("speaker_id") != speaker_id:
                    existing=await (await conn.execute("UPDATE memory.episodes SET speaker_id=%s WHERE episode_id=%s RETURNING *",
                        (speaker_id,existing["episode_id"]))).fetchone()
                return await self._episode_observed_on(conn,context,existing)
            if existing and revision <= existing["revision"]:
                raise AppError("episode_revision_conflict", status=409)
            fields = (
                event_id,
                context.actor_id,
                context.principal_id,
                context.person_id,
                context.conversation_id,
                revision,
                str(origin),
                occurred_at,
                summary,
                Jsonb(wire(source_refs)),
                scope_ids,
                Jsonb(wire(episode.get("actual_result")))
                if episode.get("actual_result") is not None
                else None,
                Jsonb(
                    wire(episode.get("coverage") or Coverage(False, missing=("coverage_unknown",)))
                ),
                episode.get("matter_id"),
                episode.get("action_id"),
                episode.get("receipt_id"),
                digest,
            )
            if existing:
                episode_id = existing["episode_id"]
                row = await (
                    await conn.execute(
                        "UPDATE memory.episodes SET event_id=%s,actor_id=%s,principal_id=%s,person_id=%s,"
                        "conversation_id=%s,revision=%s,origin_kind=%s,occurred_at=%s,summary=%s,source_refs=%s,scope_ids=%s,"
                        "actual_result=%s,coverage=%s,matter_id=%s,action_id=%s,receipt_id=%s,content_hash=%s,"
                        "sequence=nextval('memory.revision_sequence'),extraction_state='pending',extraction_cursor='{}',"
                        "extraction_sources='[]',extraction_missing='{}',extraction_started_at=NULL,active=true WHERE episode_id=%s RETURNING *",
                        (*fields, episode_id),
                    )
                ).fetchone()
            else:
                row = await (
                    await conn.execute(
                        "INSERT INTO memory.episodes(event_id,actor_id,principal_id,person_id,conversation_id,"
                        "revision,origin_kind,occurred_at,summary,source_refs,scope_ids,actual_result,coverage,matter_id,action_id,receipt_id,"
                        "content_hash,episode_id) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
                        (*fields, episode_id),
                    )
                ).fetchone()
            from .domain_utils import lexical
            from .extraction_contract import SCHEMA_VERSION
            row = await (await conn.execute(
                "UPDATE memory.episodes SET recorded_at=now(),lexical=%s,semantic_fingerprint=%s,speaker_id=%s,"
                "extraction_cursor=%s WHERE episode_id=%s RETURNING *",
                (lexical(summary), digest, speaker_id, Jsonb({"protocol":SCHEMA_VERSION}),episode_id))).fetchone()
            await self.consolidation.dirty_on(conn, context, row)
            await self.jobs.enqueue(conn,JobSpec("memory.index_object",
                f"memory-episode-index:{episode_id}:{row['revision']}",
                {"kind":"episode","id":episode_id,"revision":row["revision"]},ServiceClass.ELASTIC))
            return await self._episode_observed_on(conn,context,row)

    async def episodes_since(self, context, cursor, limit):
        return await self._episodes(context, cursor, limit)

    async def episodes_by_refs(self,context,refs,*,connection=None):
        refs=tuple(refs)
        if len(refs)>128 or any(not isinstance(item,(tuple,list)) or len(item)!=2 or not isinstance(item[0],str)
                or type(item[1]) is not int or item[1]<1 for item in refs):
            raise AppError("invalid_episode_refs")
        async with self.connection(connection) as conn:
            await self.identity.check_context(context,connection=conn)
            rows=await (await conn.execute("SELECT e.* FROM memory.episodes e JOIN unnest(%s::text[],%s::bigint[]) "
                "WITH ORDINALITY requested(episode_id,revision,ordinal) USING(episode_id,revision) "
                "WHERE e.actor_id=%s AND e.active AND e.scope_ids<@%s::text[] ORDER BY requested.ordinal",
                ([item[0] for item in refs],[item[1] for item in refs],context.actor_id,list(context.scope_ids)))).fetchall()
            valid=[]
            group=[]
            count=0
            for row in rows:
                selected=codec.refs(row["source_refs"])
                if group and count+len(selected)>1800:
                    valid.extend(await self.sources.check_many_on(conn,context,tuple(group)))
                    group=[]
                    count=0
                group.append(selected)
                count+=len(selected)
            if group:
                valid.extend(await self.sources.check_many_on(conn,context,tuple(group)))
        items=tuple(codec.episode(row) for row,allowed in zip(rows,valid,strict=True) if allowed)
        missing=[]
        if len(rows)!=len(refs):
            missing.append("episodes_unavailable_or_revised")
        if not all(valid):
            missing.append("unavailable_sources")
        return Page(items,None,Coverage(not missing,missing=tuple(missing)))

    async def domain_changed_on(self,conn,context,changed,*,source_refs,affected_subject_ids=()):
        """Manual domain commands offer one source-backed change, never replay history."""
        source_refs=codec.refs(source_refs)
        if not source_refs or not changed:
            return None
        records=wire(changed)
        subjects=sorted(set(affected_subject_ids)|{item["subject_id"] for item in records if item.get("subject_id")})
        event_id="memory-revision:"+body_hash({"actor_id":context.actor_id,"changed":records,"sources":source_refs})
        matter_id=next((item["id"] for item in records if item.get("kind")=="matter"),None)
        descriptions=await self.sources.describe_for_write_on(conn,context,source_refs)
        occurred=max((item["occurred_at"] for item in descriptions if item.get("occurred_at")),default=datetime.now(UTC))
        previous=await (await conn.execute("SELECT occurred_at FROM memory.episodes WHERE actor_id=%s AND event_id=%s",
            (context.actor_id,event_id))).fetchone()
        return await self.observe_episode(context,{"event_id":event_id,"occurred_at":previous["occurred_at"] if previous else occurred,
            "origin_kind":"observation","summary":"有来源的记忆修订与相关认识变化", "source_refs":source_refs,
            "matter_id":matter_id,"actual_result":{"internal":True,"event_kind":"memory_revision",
                "changed_records":records,"affected_subject_ids":subjects}},connection=conn)

    async def episodes_between(self, context, start, end, limit=200, cursor=None):
        start, end = parse_time(start), parse_time(end)
        if end <= start:
            raise AppError("invalid_episode_time_range")
        return await self._episodes(context, cursor, limit, start=start, end=end)

    async def _episodes(self, context, cursor, limit, *, start=None, end=None):
        await self.identity.check_context(context)
        if not context.actor_id:
            raise AppError("actor_required")
        sequence, limit = cursor_sequence(cursor), min(max(int(limit), 1), 500)
        async with self.database.transaction(read_only=True) as conn:
            rows = await (
                await conn.execute(
                    "SELECT * FROM memory.episodes WHERE actor_id=%s AND sequence>%s "
                    "AND scope_ids<@%s::text[] AND (%s::timestamptz IS NULL OR occurred_at>=%s) "
                    "AND (%s::timestamptz IS NULL OR occurred_at<%s) ORDER BY sequence LIMIT %s",
                    (
                        context.actor_id,
                        sequence,
                        list(context.scope_ids),
                        start,
                        start,
                        end,
                        end,
                        limit + 1,
                    ),
                )
            ).fetchall()
            head = await (
                await conn.execute(
                    "SELECT COALESCE(max(sequence),0) AS sequence FROM memory.episodes WHERE actor_id=%s",
                    (context.actor_id,),
                )
            ).fetchone()
            more = len(rows) > limit
            scanned = rows[:limit]
            checked = await self.sources.check_many_on(
                conn, context, tuple(codec.refs(row["source_refs"]) for row in scanned)
            )
            if self.pipeline_recovery is not None:
                visible_rows=tuple(row for row,valid in zip(scanned,checked,strict=True) if valid and row["active"])
                statuses=await self.pipeline_recovery.statuses_on(conn,context,visible_rows)
                for row in visible_rows:
                    row["consolidation"]=statuses[row["episode_id"]]
        visible = tuple(
            codec.episode(row) for row, valid in zip(scanned, checked) if valid and row["active"]
        )
        watermark = scanned[-1]["sequence"] if more else max(sequence, head["sequence"])
        missing = ("unavailable_sources",) if len(visible) < len(scanned) else ()
        return EpisodePage(
            visible,
            "e:" + str(watermark) if more else None,
            Coverage(complete=not more and not missing, missing=missing),
            "e:" + str(watermark),
        )

    async def extraction_status(self,context,episode_id):
        await self.identity.check_context(context)
        async with self.database.transaction(read_only=True) as conn:
            row=await (await conn.execute("SELECT * FROM memory.episodes WHERE episode_id=%s AND actor_id=%s AND active",
                (episode_id,context.actor_id))).fetchone()
            if not row or not set(row["scope_ids"])<=set(context.scope_ids) or not all(
                    await self.sources.check_many_on(conn,context,(codec.refs(row["source_refs"]),))):
                raise AppError("episode_unavailable",status=404)
            return {"episode_id":episode_id,"revision":row["revision"],
                **await self.pipeline_recovery.status_on(conn,context,row)}

    async def record_reflection(self, context, proposal, *, connection=None, episode_revisions=None):
        if proposal.actor_id != context.actor_id or not proposal.interpretation:
            raise AppError("reflection_interpretation_required")
        source_refs = tuple(proposal.source_refs)
        if not source_refs or not proposal.episode_ids:
            raise AppError("reflection_original_evidence_required")
        async with self.connection(connection) as conn:
            snapshots=await (await conn.execute("SELECT episode_id,revision,source_refs FROM memory.episodes "
                "WHERE actor_id=%s AND episode_id=ANY(%s) AND active ORDER BY episode_id",
                (context.actor_id,list(proposal.episode_ids)))).fetchall()
            if len(snapshots) != len(set(proposal.episode_ids)):
                raise AppError("reflection_episode_unavailable",status=409)
            from tianshu.sources.dependencies import unique_refs
            closure=unique_refs(source_refs,*(codec.refs(row["source_refs"]) for row in snapshots))
            # An Episode dependency stays a dependency even when the model quoted
            # only one excerpt. Lock its whole source closure before Episode rows.
            descriptions = await self.sources.describe_for_write_on(conn, context, closure)
            originals = await (
                await conn.execute(
                    "SELECT episode_id,revision,source_refs FROM memory.episodes WHERE actor_id=%s "
                    "AND episode_id=ANY(%s) AND active ORDER BY episode_id FOR UPDATE",
                    (context.actor_id, list(proposal.episode_ids)),
                )
            ).fetchall()
            if len(originals) != len(set(proposal.episode_ids)):
                raise AppError("reflection_episode_unavailable", status=409)
            if any((row["revision"],row["source_refs"]) != (before["revision"],before["source_refs"])
                    for row,before in zip(originals,snapshots,strict=True)):
                raise AppError("reflection_episode_revision_conflict",status=409)
            if episode_revisions is not None and any(
                    episode_revisions.get(row["episode_id"]) != row["revision"] for row in originals):
                raise AppError("reflection_episode_revision_conflict",status=409)
            original_refs = tuple(
                ref for row in originals for ref in codec.refs(row["source_refs"])
            )

            def covered(ref):
                for original in original_refs:
                    if (ref.source_id, ref.revision) != (original.source_id, original.revision):
                        continue
                    if not original.locator:
                        return True
                    if original.locator == ref.locator:
                        return True
                    if "start" in original.locator and "end" in original.locator and ref.locator:
                        return (
                            original.locator["start"]
                            <= ref.locator.get("start", -1)
                            <= ref.locator.get("end", -1)
                            <= original.locator["end"]
                        )
                return False

            if any(not covered(ref) for ref in source_refs):
                raise AppError("reflection_not_original_evidence", status=403)
            source_refs=closure
            scopes = sorted({scope for item in descriptions for scope in item["scope_ids"]})
            values = (
                proposal.proposal_id,
                context.actor_id,
                context.principal_id,
                context.person_id,
                context.conversation_id,
                list(proposal.episode_ids),
                proposal.content,
                proposal.kind,
                parse_time(proposal.occurred_at),
                Jsonb(wire(source_refs)),
                scopes,
                Jsonb(wire(proposal.conditions)),
                list(proposal.counterexamples),
            )
            row = await (
                await conn.execute(
                    "INSERT INTO memory.reflections(proposal_id,actor_id,principal_id,person_id,conversation_id,episode_ids,content,kind,"
                    "occurred_at,source_refs,scope_ids,conditions,counterexamples) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                    "ON CONFLICT(proposal_id) DO NOTHING RETURNING *",
                    values,
                )
            ).fetchone()
            if row is None:
                row = await (
                    await conn.execute(
                        "SELECT * FROM memory.reflections WHERE proposal_id=%s AND actor_id=%s",
                        (proposal.proposal_id, context.actor_id),
                    )
                ).fetchone()
                if (
                    not row
                    or row["content"] != proposal.content
                    or row["source_refs"] != wire(source_refs)
                ):
                    raise AppError("reflection_identity_conflict", status=409)
            from .domain_utils import lexical
            row = await (await conn.execute(
                "UPDATE memory.reflections SET recorded_at=COALESCE(recorded_at,now()),lexical=%s,semantic_fingerprint=%s "
                "WHERE proposal_id=%s RETURNING *",
                (lexical(proposal.content),body_hash({"kind":proposal.kind,"content":proposal.content,
                    "conditions":proposal.conditions,"source_refs":source_refs}),proposal.proposal_id))).fetchone()
            await self.jobs.enqueue(conn,JobSpec("memory.index_object",
                f"memory-reflection-index:{row['proposal_id']}:{row['revision']}",
                {"kind":"reflection","id":row["proposal_id"],"revision":row["revision"]},ServiceClass.ELASTIC))
            return codec.reflection(row)

    async def relationship(self, context, person_id):
        selected = await self.identity.context(
            context.principal_id,
            actor_id=context.actor_id,
            person_id=person_id,
            conversation_id=context.conversation_id,
        )
        async with self.database.transaction(read_only=True) as conn:
            row = await (
                await conn.execute(
                    "SELECT * FROM memory.relationships WHERE actor_id=%s AND person_id=%s",
                    (selected.actor_id, person_id),
                )
            ).fetchone()
            details = await (
                await conn.execute(
                    "SELECT * FROM memory.relationship_details WHERE actor_id=%s AND person_id=%s "
                    "AND active AND scope_ids<@%s::text[] ORDER BY detail_id LIMIT 200",
                    (selected.actor_id, person_id, list(selected.scope_ids)),
                )
            ).fetchall()
            checked = await self.sources.check_many_on(
                conn, selected, tuple(codec.refs(item["source_refs"]) for item in details)
            )
            row_visible = bool(
                row
                and set(row["scope_ids"]) <= set(selected.scope_ids)
                and (
                    await self.sources.check_many_on(
                        conn, selected, (codec.refs(row["source_refs"]),)
                    )
                )[0]
            )
        details = [item for item, valid in zip(details, checked) if valid]
        result = {
            "pair": {"actor_id": selected.actor_id, "person_id": person_id},
            "can_manage": bool({"*", "memory.manage"} & selected.permissions),
            "revision": row["revision"] if row else 0,
            "relationship_type": row["relationship_type"] if row_visible else "acquaintance",
            "display_label": row["display_label"] if row_visible else "认识的人",
            "frozen": row["frozen"] if row else False,
            "freeze_revision": row["freeze_revision"] if row else 0,
            "frozen_at": row["frozen_at"] if row else None,
            "summary": row["summary"] if row_visible else "",
            "boundaries": [],
            "shared_experiences": [],
            "preferences": [],
            "source_refs": list(row["source_refs"]) if row_visible else [],
        }
        for detail in details:
            key = {
                "boundary": "boundaries",
                "shared_experience": "shared_experiences",
                "preference": "preferences",
            }[detail["kind"]]
            result[key].append(
                {
                    "detail_id": detail["detail_id"],
                    "content": detail["content"],
                    "revision": detail["revision"],
                    "explicit": detail["explicit"],
                    "source_refs": detail["source_refs"],
                }
            )
            result["source_refs"].extend(detail["source_refs"])
        return result

    async def reflection_page(
        self, context, *, person_id=None, conversation_id=None, cursor=None, limit=40,
        time_mode="current",valid_at=None,known_at=None
    ):
        await self.identity.check_context(context)
        from .evidence import temporal_selection
        time_mode,valid_at,known_at=temporal_selection(time_mode=time_mode,valid_at=valid_at,known_at=known_at)
        if conversation_id is not None and conversation_id != context.conversation_id:
            raise AppError("group_projection_forbidden", status=403)
        limit = min(max(int(limit), 1), 100)
        async with self.database.transaction(read_only=True) as conn:
            rows = await (
                await conn.execute(
                    "SELECT * FROM memory.reflections WHERE actor_id=%s AND active "
                    "AND scope_ids<@%s::text[] AND (%s::text IS NULL OR person_id=%s) "
                    "AND (%s::text IS NULL OR conversation_id=%s) "
                    "AND (%s::timestamptz IS NULL OR recorded_at IS NULL OR recorded_at<=%s) "
                    "AND (%s::timestamptz IS NULL OR occurred_at<=%s) "
                    "AND (%s::text IS NULL OR proposal_id>%s) ORDER BY proposal_id LIMIT %s",
                    (
                        context.actor_id,
                        list(context.scope_ids),
                        person_id,
                        person_id,
                        conversation_id,
                        conversation_id,
                        known_at,known_at,valid_at,valid_at,
                        cursor,
                        cursor,
                        limit + 1,
                    ),
                )
            ).fetchall()
            from .evidence import select_supports_on
            selected = await select_supports_on(self.sources,conn,context,rows[:limit])
            checked = [bool(refs) for refs in selected]
        visible = tuple(codec.reflection(row) for row, valid in zip(rows[:limit], checked) if valid)
        temporal_missing=[]
        if any(item.recorded_at is None for item in visible):
            temporal_missing.append("recorded_at_unknown")
        if time_mode == "valid_at" and visible:
            temporal_missing.append("reflection_valid_time_unknown")
        return Page(
            visible,
            rows[limit - 1]["proposal_id"] if len(rows) > limit else None,
            Coverage(
                complete=len(rows) <= limit and all(checked) and not temporal_missing,
                missing=tuple((*(("unavailable_sources",) if not all(checked) else ()),*temporal_missing)),
            ),
        )

    async def _group_consolidation(self,context):
        """Recent authorized per-person receipts, distinct from a historical fact view."""
        if self.pipeline_recovery is None:
            return {"view":"current_processing","items":(),"subjects":(),"coverage":Coverage(False,missing=("pipeline_unavailable",))}
        async with self.database.transaction(read_only=True) as conn:
            rows=await (await conn.execute("SELECT * FROM memory.episodes WHERE actor_id=%s AND conversation_id=%s "
                "AND active AND scope_ids<@%s::text[] ORDER BY sequence DESC LIMIT 17",
                (context.actor_id,context.conversation_id,list(context.scope_ids)))).fetchall()
            from .evidence import select_supports_on
            selected=await select_supports_on(self.sources,conn,context,rows[:16])
            visible=tuple(row for row,refs in zip(rows[:16],selected,strict=True) if refs)
            statuses=await self.pipeline_recovery.statuses_on(conn,context,visible)
            ids=sorted({item["subject_id"] for status in statuses.values() for item in status["target_results"]}
                | {item["subject_id"] for status in statuses.values() for item in status["unresolved"] if item.get("subject_id")})
            people={}
            for start in range(0,len(ids),128):
                for person in await self.identity.resolve_memory_subjects_on(conn,context,ids[start:start+128]):
                    people[person["person_id"]]=person
            items=[]
            seen_batches=set()
            for episode in visible:
                status=statuses[episode["episode_id"]]
                # One shared batch appears on several Episodes; do not duplicate
                # its per-person result in a group view.
                identity=status["batch_id"] or episode["episode_id"]
                if identity in seen_batches:
                    continue
                seen_batches.add(identity)
                items.append({"episode_id":episode["episode_id"],"revision":episode["revision"],**status,
                    "target_results":[item for item in status["target_results"] if item["subject_id"] in people],
                    "unresolved":[item for item in status["unresolved"] if item.get("subject_id") is None or item["subject_id"] in people]})
        missing=[]
        if len(rows)>16:
            missing.append("recent_processing_window")
        if len(visible)<len(rows[:16]):
            missing.append("unavailable_sources")
        if set(ids)-set(people):
            missing.append("subjects_unavailable")
        return {"view":"current_processing","items":tuple(items),"subjects":tuple(people.values()),
            "coverage":Coverage(not missing,missing=tuple(missing))}

    async def projection(
        self,
        context,
        person_id=None,
        group_id=None,
        *,
        claim_limit=100,
        matter_limit=50,
        reflection_limit=40,
        time_mode="current",
        valid_at=None,
        known_at=None,
    ):
        await self.identity.check_context(context)
        if group_id is not None and (
            context.conversation_id != group_id or context.conversation_kind != "group"
        ):
            raise AppError("group_projection_forbidden", status=403)
        group = None
        if group_id is not None:
            async with self.database.transaction(read_only=True) as conn:
                group = await self.identity.group_on(conn, context)
        subject_id = group["subject_id"] if group else person_id
        claim_page = await self.claims.list(context, subject_id=subject_id, limit=claim_limit,
            time_mode=time_mode,valid_at=valid_at,known_at=known_at)
        matters = await self.claims.matters(context, subject_id=subject_id, limit=matter_limit,
            time_mode=time_mode,valid_at=valid_at,known_at=known_at)
        reflections = await self.reflection_page(
            context,
            person_id=person_id if not group else None,
            conversation_id=group_id,
            limit=reflection_limit,
            time_mode=time_mode,valid_at=valid_at,known_at=known_at,
        )
        from .evidence import build_evidence_unit
        hypotheses = tuple(item for item in claim_page.items if item.memory_kind == "hypothesis")
        facts = tuple(item for item in claim_page.items if item.memory_kind != "hypothesis")
        evidence = tuple(build_evidence_unit(item,time_mode=time_mode,
            temporal_missing=("reflection_valid_time_unknown",) if time_mode=="valid_at" and hasattr(item,"proposal_id") else ())
            for item in (*claim_page.items,*matters.items,*reflections.items))
        pages = (claim_page,matters,reflections)
        missing = tuple(dict.fromkeys(value for page in pages for value in page.coverage.missing))
        return {
            "person_id": person_id,
            "group_id": group_id,
            "group": group,
            "group_consolidation": await self._group_consolidation(context) if group else None,
            "can_manage": "memory.manage" in context.permissions or "*" in context.permissions,
            "claims": facts,
            "hypotheses": hypotheses,
            "matters": matters.items,
            "reflections": reflections.items,
            "evidence_units": evidence,
            "time": {"mode":time_mode,"valid_at":valid_at,"known_at":known_at},
            "pages": {"claims":claim_page.next_cursor,"matters":matters.next_cursor,"reflections":reflections.next_cursor},
            "coverage": Coverage(
                complete=all(page.coverage.complete for page in pages),missing=missing
            ),
        }

    async def style_view(self, context, person_id=None):
        page = await self.reflection_page(context, person_id=person_id, limit=100)
        evidence = tuple(
            item for item in page.items if item.kind in {"style", "interaction", "interest"}
        )
        return {
            "evidence": evidence,
            "counterexamples": tuple(value for item in evidence for value in item.counterexamples),
            "source_refs": tuple(ref for item in evidence for ref in item.source_refs),
            "coverage": page.coverage,
        }

    async def link_matter(self, context, source_ref, matter_id):
        async with self.database.transaction() as conn:
            await self.sources.describe_for_write_on(conn, context, (source_ref,))
            row = await (
                await conn.execute(
                    "SELECT scope_ids FROM memory.matters WHERE matter_id=%s AND actor_id=%s",
                    (matter_id, context.actor_id),
                )
            ).fetchone()
            if not row or not set(row["scope_ids"]) <= set(context.scope_ids):
                raise AppError("matter_forbidden", status=403)
            await conn.execute(
                "UPDATE memory.episodes SET matter_id=%s,sequence=nextval('memory.revision_sequence') "
                "WHERE actor_id=%s AND source_refs @> %s",
                (matter_id, context.actor_id, Jsonb(wire((source_ref,)))),
            )

    async def save_relationship(self, context, body):
        person_id = body["person_id"]
        selected = await self.identity.context(
            context.principal_id,
            actor_id=context.actor_id,
            person_id=person_id,
            conversation_id=context.conversation_id,
            permission="memory.manage",
        )
        expected = body.get("expected_revision")
        if type(expected) is not int or expected < 0:
            raise AppError("expected_revision_required")
        allowed = {"acquaintance", "friend", "family", "partner", "colleague", "custom"}
        if body.get("relationship_type", "acquaintance") not in allowed:
            raise AppError("invalid_relationship_type")
        command_refs = await self.sources.record_event(
            selected,
            producer="web.relationship",
            event_id=body["client_request_id"],
            revision=1,
            occurred_at=parse_time(body["occurred_at"])
            if body.get("occurred_at")
            else datetime.now(UTC),
            parts=(
                {
                    "text": json.dumps(
                        {
                            key: body.get(key)
                            for key in (
                                "relationship_type",
                                "display_label",
                                "summary",
                                "frozen",
                                "boundaries",
                                "preferences",
                                "shared_experiences",
                            )
                        },
                        ensure_ascii=False,
                    )
                },
            ),
            origin_kind=OriginKind.HUMAN,
        )

        async def apply(conn):
            descriptions = await self.sources.describe_for_write_on(conn, selected, command_refs)
            scopes = sorted({scope for item in descriptions for scope in item["scope_ids"]})
            values = (
                context.actor_id,
                person_id,
                body.get("relationship_type", "acquaintance"),
                body.get("display_label", "认识的人"),
                body.get("summary", ""),
                bool(body.get("frozen", False)),
                scopes,
                Jsonb(wire(command_refs)),
            )
            if expected == 0:
                row = await (
                    await conn.execute(
                        "INSERT INTO memory.relationships(actor_id,person_id,relationship_type,display_label,summary,"
                        "frozen,scope_ids,source_refs,frozen_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,CASE WHEN %s THEN now() ELSE NULL END) "
                        "ON CONFLICT DO NOTHING RETURNING revision",
                        (*values, values[5]),
                    )
                ).fetchone()
            else:
                row = await (
                    await conn.execute(
                        "UPDATE memory.relationships SET relationship_type=%s,display_label=%s,summary=%s,"
                        "freeze_revision=freeze_revision+CASE WHEN frozen<>%s THEN 1 ELSE 0 END,"
                        "frozen_at=CASE WHEN frozen=%s THEN frozen_at WHEN %s THEN now() ELSE NULL END,frozen=%s,"
                        "scope_ids=%s,source_refs=%s,revision=revision+1 WHERE actor_id=%s AND person_id=%s AND revision=%s RETURNING revision",
                        (
                            *values[2:5],
                            values[5],
                            values[5],
                            values[5],
                            values[5],
                            scopes,
                            values[7],
                            *values[:2],
                            expected,
                        ),
                    )
                ).fetchone()
            if not row:
                raise AppError("revision_conflict", status=409)
            for kind, key in (
                ("boundary", "boundaries"),
                ("preference", "preferences"),
                ("shared_experience", "shared_experiences"),
            ):
                if key not in body:
                    continue
                if not isinstance(body[key], list) or len(body[key]) > 64:
                    raise AppError("invalid_relationship_details")
                await conn.execute(
                    "UPDATE memory.relationship_details SET active=false WHERE actor_id=%s AND person_id=%s AND kind=%s",
                    (context.actor_id, person_id, kind),
                )
                for index, content in enumerate(body[key]):
                    content = content.get("content") if isinstance(content, dict) else content
                    if not isinstance(content, str) or not 1 <= len(content) <= 4000:
                        raise AppError("invalid_relationship_detail")
                    detail_id = (
                        "relationship_"
                        + body_hash(
                            {"request": body["client_request_id"], "kind": kind, "index": index}
                        )[:48]
                    )
                    await conn.execute(
                        "INSERT INTO memory.relationship_details(detail_id,actor_id,person_id,kind,content,source_refs,scope_ids,explicit) "
                        "VALUES(%s,%s,%s,%s,%s,%s,%s,true)",
                        (
                            detail_id,
                            context.actor_id,
                            person_id,
                            kind,
                            content,
                            Jsonb(wire(command_refs)),
                            scopes,
                        ),
                    )
            return CommandResult(body["client_request_id"], revision=row["revision"])

        async def replay(conn, original):
            await self.identity.check_context(selected, connection=conn)
            return original

        result = await self.commands.execute(
            selected,
            "memory.relationship.save",
            body["client_request_id"],
            body_hash(body),
            apply,
            on_replay=replay,
        )
        return replace(result, data=await self.relationship(selected, person_id))

    async def source_changed(self, conn, sources, kind):
        selected = [
            Jsonb([{"source_id": source.source_id, "revision": source.revision}])
            for source in sources
        ]
        parameters = (selected, Jsonb(wire(sources)), kind)
        if not selected:
            return
        await conn.execute(
            "UPDATE memory.model_steps SET state='source_revoked',request='{}',error_code='source_unavailable' "
            "WHERE request->'source_refs' @> ANY(%s::jsonb[]) AND sources.dependencies_intersect(request->'source_refs',%s,%s)",
            parameters,
        )
        invalidated = await (await conn.execute(
            "UPDATE memory.consolidation_batches SET status='revoked',error_code='source_unavailable',"
            "manifest='{}',read_set='[]',commit_refs='{}',updated_at=now() "
            "WHERE dependency_refs @> ANY(%s::jsonb[]) AND sources.dependencies_intersect(dependency_refs,%s,%s) "
            "RETURNING batch_id,partition_id", parameters)).fetchall()
        for batch in invalidated:
            await conn.execute("UPDATE memory.consolidation_state SET active_batch_id=NULL,last_error='source_unavailable',"
                "lease_owner=NULL,lease_until=NULL WHERE partition_id=%s AND active_batch_id=%s",
                (batch["partition_id"],batch["batch_id"]))
        await conn.execute(
            "UPDATE memory.episodes SET active=false,summary='',actual_result=NULL,lexical='',embedding=NULL,embedding_profile=NULL,semantic_fingerprint=NULL,"
            "sequence=nextval('memory.revision_sequence') WHERE active AND source_refs @> ANY(%s::jsonb[]) AND sources.dependencies_intersect(source_refs,%s,%s)",
            parameters,
        )
        await conn.execute(
            "UPDATE memory.reflections SET active=false,content='',conditions='{}',counterexamples='{}',lexical='',embedding=NULL,embedding_profile=NULL,semantic_fingerprint=NULL,"
            "revision=revision+1 WHERE active AND source_refs @> ANY(%s::jsonb[]) AND sources.dependencies_intersect(source_refs,%s,%s)",
            parameters,
        )
        await conn.execute(
            "UPDATE memory.relationship_details SET active=false,content='',revision=revision+1 "
            "WHERE active AND source_refs @> ANY(%s::jsonb[]) AND sources.dependencies_intersect(source_refs,%s,%s)",
            parameters,
        )
        await conn.execute(
            "UPDATE memory.relationships SET summary='',relationship_type='acquaintance',display_label='认识的人',"
            "revision=revision+1 WHERE source_refs @> ANY(%s::jsonb[]) AND sources.dependencies_intersect(source_refs,%s,%s)",
            parameters,
        )
        await self.claims.source_changed(conn, sources, kind)
