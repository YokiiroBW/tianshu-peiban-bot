"""Actual episodes, source-backed projections and explicit relationship state."""

from dataclasses import replace
from contextlib import asynccontextmanager
from datetime import UTC, datetime
import json
import re

from aiohttp import ClientError
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
        self.statistics = None
        self.recovery_policy = None
        self.index_readiness = None
        self.settings = settings or {}
        from .consolidation import Consolidation
        self.consolidation = Consolidation(self, self.settings.get("consolidation", {}))

    def bind_conversation(self, conversation):
        self.consolidation.materials.bind_conversation(conversation)

    def bind_record_candidates(self,read_port):
        self.consolidation.materials.record_candidates=read_port

    def bind_index_readiness(self, callback):
        """Bind Retrieval's authorized, current-profile readiness read port."""
        if self.index_readiness is not None:
            raise RuntimeError("memory_index_readiness_already_bound")
        self.index_readiness = callback

    def bind_observability(self, *, statistics, recovery_policy):
        self.statistics, self.recovery_policy = statistics, recovery_policy

    async def record_batch_statistics_on(self, conn, batch_id, state):
        row = await (await conn.execute("UPDATE memory.consolidation_batches SET "
            "observation_revision=observation_revision+1 WHERE batch_id=%s RETURNING partition_id,observation_revision",
            (batch_id,))).fetchone()
        partition = await (await conn.execute("SELECT actor_id FROM memory.consolidation_state WHERE partition_id=%s",
            (row["partition_id"],))).fetchone()
        event = {"family":"memory", "event_id":"memory-batch:"+batch_id,
            "revision":row["observation_revision"], "occurred_at":wire(datetime.now(UTC)),
            "module":"memory", "actor_id":partition["actor_id"], "route_id":None,
              "dimensions":{"state":"cancelled" if state == "revoked" else state},
            "metrics":{"memory_applied":int(state == "applied"),"memory_failed":int(state == "failed")}}
        await conn.execute("INSERT INTO memory.statistics_outbox(batch_id,revision,event) VALUES(%s,%s,%s) "
            "ON CONFLICT(batch_id) DO UPDATE SET revision=excluded.revision,event=excluded.event,updated_at=now()",
            (batch_id,row["observation_revision"],Jsonb(event)))
        if self.statistics and await self.statistics.record_safely_on(conn,event):
            await conn.execute("UPDATE memory.statistics_outbox SET published_revision=revision WHERE batch_id=%s",
                (batch_id,))

    async def backfill_statistics(self, *, limit=128):
        """Reconstruct missing events from the batch ledger and retry its outbox."""
        if self.statistics is None:
            return {"complete":False,"reason":"statistics_not_bound"}
        async with self.database.transaction() as conn:
            missing = await (await conn.execute("SELECT b.batch_id,b.status FROM memory.consolidation_batches b "
                "WHERE b.status IN ('applied','failed') AND NOT EXISTS(SELECT 1 FROM memory.statistics_outbox o "
                "WHERE o.batch_id=b.batch_id) ORDER BY b.updated_at,b.batch_id LIMIT %s FOR UPDATE SKIP LOCKED",
                (limit,))).fetchall()
            for batch in missing:
                await self.record_batch_statistics_on(conn,batch["batch_id"],batch["status"])
            rows = await (await conn.execute("SELECT * FROM memory.statistics_outbox WHERE published_revision<revision "
                "ORDER BY updated_at,batch_id LIMIT %s FOR UPDATE SKIP LOCKED",(limit,))).fetchall()
            for row in rows:
                if await self.statistics.record_safely_on(conn,row["event"]):
                    await conn.execute("UPDATE memory.statistics_outbox SET published_revision=%s "
                        "WHERE batch_id=%s AND revision=%s",(row["revision"],row["batch_id"],row["revision"]))
            pending = await (await conn.execute("SELECT (SELECT count(*) FROM memory.statistics_outbox "
                "WHERE published_revision<revision) + (SELECT count(*) FROM memory.consolidation_batches b "
                "WHERE b.status IN ('applied','failed') AND NOT EXISTS(SELECT 1 FROM memory.statistics_outbox o "
                "WHERE o.batch_id=b.batch_id)) AS count")).fetchone()
            complete = pending["count"] == 0
            # The producer watermark describes only material actually drained.
            try:
                async with conn.transaction():
                    await self.statistics.mark_producer_on(conn,"memory",watermark=wire(datetime.now(UTC)) if complete else None,
                        complete=complete,detail={"pending":pending["count"]})
            except Exception:
                complete = False
        return {"complete":complete,"pending":pending["count"]}

    def bind_episode_observer(self,observer):
        if self.episode_observer is not None:
            raise RuntimeError("memory_episode_observer_already_bound")
        self.episode_observer=observer

    async def enqueue_index_on(self, conn, *, kind, object_id, revision, key, subject_id=None):
        """Freeze the originating installation in both the job and its arguments."""
        generation = await self.jobs.current_generation_on(conn)
        await self.jobs.enqueue(conn, JobSpec("memory.index_object", key,
            {"kind":kind,"id":object_id,"revision":revision,"generation":generation},
            ServiceClass.ELASTIC,subject_id=subject_id,data_generation=generation))

    async def _episode_observed_on(self,conn,context,row):
        value=codec.episode(row)
        if self.episode_observer:
            await self.episode_observer(context,f"episode:{value.episode_id}:{value.revision}",
                episode=value,connection=conn)
        return value

    @staticmethod
    async def _snapshot_episode_on(conn, episode_id):
        await conn.execute(
            "INSERT INTO memory.episode_revisions(episode_id,revision,event_id,actor_id,person_id,conversation_id,"
            "origin_kind,occurred_at,recorded_at,summary,source_refs,supplement_refs,scope_ids,actual_result,coverage,matter_id,"
            "speaker_id,semantic_fingerprint,active) SELECT episode_id,revision,event_id,actor_id,person_id,"
            "conversation_id,origin_kind,occurred_at,recorded_at,summary,source_refs,supplement_refs,scope_ids,actual_result,"
            "coverage,matter_id,speaker_id,semantic_fingerprint,active FROM memory.episodes WHERE episode_id=%s",
            (episode_id,))

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
            partition = await self.consolidation.lock_partition_on(conn,context,scope_ids)
            existing = await (
                await conn.execute(
                    "SELECT * FROM memory.episodes WHERE actor_id=%s AND event_id=%s FOR UPDATE",
                    (context.actor_id, event_id),
                )
            ).fetchone()
            if existing and revision >= existing["event_revision"] and existing["event_content_hash"] == digest:
                if revision > existing["event_revision"]:
                    existing = await (await conn.execute("UPDATE memory.episodes SET event_revision=%s "
                        "WHERE episode_id=%s RETURNING *", (revision,existing["episode_id"]))).fetchone()
                if existing.get("speaker_id") != speaker_id:
                    existing=await (await conn.execute("UPDATE memory.episodes SET speaker_id=%s WHERE episode_id=%s RETURNING *",
                        (speaker_id,existing["episode_id"]))).fetchone()
                    await conn.execute("UPDATE memory.episode_revisions SET speaker_id=%s WHERE episode_id=%s AND revision=%s",
                        (speaker_id,existing["episode_id"],existing["revision"]))
                return await self._episode_observed_on(conn,context,existing)
            if existing and revision <= existing["event_revision"]:
                raise AppError("episode_revision_conflict", status=409)
            semantic_revision = existing["revision"] + 1 if existing else revision
            fields = (
                event_id,
                context.actor_id,
                context.principal_id,
                context.person_id,
                context.conversation_id,
                semantic_revision,
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
                revision,
                digest,
            )
            if existing:
                episode_id = existing["episode_id"]
                row = await (
                    await conn.execute(
                        "UPDATE memory.episodes SET event_id=%s,actor_id=%s,principal_id=%s,person_id=%s,"
                        "conversation_id=%s,revision=%s,origin_kind=%s,occurred_at=%s,summary=%s,source_refs=%s,scope_ids=%s,"
                        "actual_result=%s,coverage=%s,matter_id=%s,action_id=%s,receipt_id=%s,content_hash=%s,"
                        "event_revision=%s,event_content_hash=%s,"
                        "supplement_refs='[]',"
                        "embedding=NULL,embedding_profile=NULL,"
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
                        "content_hash,event_revision,event_content_hash,episode_id) "
                        "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
                        (*fields, episode_id),
                    )
                ).fetchone()
            from .domain_utils import lexical
            from .extraction_contract import SCHEMA_VERSION
            row = await (await conn.execute(
                "UPDATE memory.episodes SET recorded_at=now(),lexical=%s,semantic_fingerprint=%s,speaker_id=%s,"
                "extraction_cursor=%s WHERE episode_id=%s RETURNING *",
                (lexical(summary), digest, speaker_id, Jsonb({"protocol":SCHEMA_VERSION}),episode_id))).fetchone()
            await self._snapshot_episode_on(conn, episode_id)
            await self.consolidation.dirty_on(conn, context, row, state=partition)
            await self.enqueue_index_on(conn,kind="episode",object_id=episode_id,revision=row["revision"],
                key=f"memory-episode-index:{episode_id}:{row['revision']}")
            return await self._episode_observed_on(conn,context,row)

    async def episodes_since(self, context, cursor, limit):
        return await self._episodes(context, cursor, limit)

    async def episode_at(self, context, episode_id, *, time_mode="current", valid_at=None, known_at=None):
        """Read a saved semantic version while enforcing today's authority and retention.

        An Episode has an occurrence time, not an invented effective interval for
        edits. valid_at filters that occurrence; known_at selects the version
        actually recorded by that instant. Unknown legacy times stay unknown.
        """
        from .evidence import temporal_selection
        time_mode, valid_at, known_at = temporal_selection(
            time_mode=time_mode, valid_at=valid_at, known_at=known_at)
        await self.identity.check_context(context)
        if not context.actor_id:
            raise AppError("actor_required")
        async with self.database.transaction(read_only=True) as conn:
            await self.identity.check_context(context, connection=conn)
            head = await (await conn.execute(
                "SELECT * FROM memory.episodes WHERE episode_id=%s AND actor_id=%s AND active",
                (episode_id, context.actor_id))).fetchone()
            if not head or not set(head["scope_ids"]) <= set(context.scope_ids):
                raise AppError("episode_unavailable", status=404)
            if time_mode == "known_at" or known_at is not None:
                snapshot = await (await conn.execute(
                    "SELECT * FROM memory.episode_revisions WHERE episode_id=%s AND active "
                    "AND recorded_at<=%s ORDER BY recorded_at DESC,revision DESC LIMIT 1",
                    (episode_id, known_at))).fetchone()
            else:
                snapshot = await (await conn.execute(
                    "SELECT * FROM memory.episode_revisions WHERE episode_id=%s AND revision=%s AND active",
                    (episode_id, head["revision"]))).fetchone()
            if not snapshot:
                raise AppError("episode_history_unavailable", status=404,
                    details={"reason": "no_recorded_version_at_requested_time"})
            if valid_at is not None and snapshot["occurred_at"] > valid_at:
                raise AppError("episode_unavailable", status=404)
            if not set(snapshot["scope_ids"]) <= set(context.scope_ids):
                raise AppError("episode_unavailable", status=404)
            valid = await self.sources.check_many_on(conn, context,
                (codec.refs(head["source_refs"]), codec.refs(head["supplement_refs"]),
                 codec.refs(snapshot["source_refs"]), codec.refs(snapshot["supplement_refs"])))
            if not all(valid):
                raise AppError("episode_unavailable", status=404)
            historical = snapshot["revision"] != head["revision"]
            # Operational extraction state is not a semantic historical version.
            row = {**head, **snapshot}
            if historical:
                row.update(extraction_state="historical", extraction_missing=["historical_extraction_not_recorded"])
            return codec.episode(row)

    async def group_term_meanings(self, context, text, *, limit=8):
        """Return currently sourced interpretations only when this group's term occurs."""
        from .types import GroupTermMeaning
        if not isinstance(text, str):
            raise AppError("invalid_group_term_text")
        if not text or context.conversation_kind != "group" or not context.conversation_id:
            return ()
        limit = min(max(int(limit), 1), 32)
        await self.identity.check_context(context)
        async with self.database.transaction(read_only=True) as conn:
            group = await self.identity.group_on(conn, context)
            if not group:
                return ()
            rows = await (await conn.execute(
                "SELECT r.*,c.actor_id,c.current_revision FROM memory.claims c "
                "JOIN memory.claim_revisions r ON r.claim_id=c.claim_id AND r.revision=c.current_revision "
                "WHERE c.actor_id=%s AND r.subject_id=%s AND r.predicate='language.term_meaning' "
                "AND r.state='active' AND r.current_status='current' AND r.polarity "
                "AND (r.valid_from IS NULL OR r.valid_from<=now()) "
                "AND (r.valid_until IS NULL OR r.valid_until>now()) "
                "AND length(r.value->>'term')>0 AND strpos(lower(%s),lower(r.value->>'term'))>0 "
                "ORDER BY length(r.value->>'term') DESC,r.observed_at DESC,r.claim_id LIMIT %s",
                (context.actor_id, group["subject_id"], text, limit * 4))).fetchall()
            selected = await self.claims.selected_groups(conn, context, rows)
            results = []
            for row, refs in zip(rows, selected, strict=True):
                value = row["value"]
                if refs is None or not isinstance(value, dict) or not row["speaker_id"]:
                    continue
                if row["assertion"] == "self_confirmed" or not isinstance(value.get("meaning"), str):
                    continue
                results.append(GroupTermMeaning(row["claim_id"], row["revision"], context.conversation_id,
                    value["term"], value["meaning"], row["speaker_id"], row["assertion"], row["polarity"],
                    row["conditions"], tuple(refs)))
                if len(results) >= limit:
                    break
            return tuple(results)

    async def record_group_term_usage(self, context, event_id, refs, *, outcome):
        """Record exposure or use, never treating it as evidence or positive feedback."""
        if (context.conversation_kind != "group" or not context.conversation_id or not context.actor_id
                or not isinstance(event_id, str) or not event_id or outcome not in {"no_match","matched","explained","used"}):
            raise AppError("memory_group_term_usage_invalid")
        refs = tuple(refs)
        if (len(refs) > 32 or outcome == "no_match" and refs or outcome != "no_match" and not refs
                or any(not isinstance(ref,(tuple,list)) or len(ref) != 2 or not isinstance(ref[0],str)
                       or type(ref[1]) is not int or ref[1] < 1 for ref in refs)):
            raise AppError("memory_group_term_usage_invalid")
        async with self.database.transaction() as conn:
            group = await self.identity.group_on(conn,context)
            if not group:
                raise AppError("group_projection_forbidden",status=403)
            if refs:
                rows = await (await conn.execute("SELECT r.*,c.actor_id,c.current_revision FROM memory.claims c "
                    "JOIN memory.claim_revisions r ON r.claim_id=c.claim_id AND r.revision=c.current_revision "
                    "WHERE c.actor_id=%s AND r.subject_id=%s AND r.predicate='language.term_meaning' "
                    "AND r.state='active' AND r.current_status='current' "
                    "AND (r.claim_id,r.revision) IN (SELECT * FROM unnest(%s::text[],%s::bigint[]))",
                    (context.actor_id,group["subject_id"],[ref[0] for ref in refs],[ref[1] for ref in refs]))).fetchall()
                selected = await self.claims.selected_groups(conn,context,rows)
                if len(rows) != len(set(tuple(ref) for ref in refs)) or any(item is None for item in selected):
                    raise AppError("memory_group_term_unavailable",status=409)
            for claim_id, revision in refs or (("",0),):
                await conn.execute("INSERT INTO memory.group_term_uses(actor_id,conversation_id,event_id,claim_id,revision,outcome) "
                    "VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                    (context.actor_id,context.conversation_id,event_id,claim_id,revision,outcome))

    async def supplement_episode(self, context, *, episode_id, event_id, event_revision, part_index,
                                 supplement_revision, original_ref, supplement_ref, generation, connection=None):
        """Attach a verified late source to its original Episode without ingesting a new event."""
        from tianshu.sources.dependencies import unique_refs
        from .extraction_contract import SCHEMA_VERSION
        original, supplement = codec.refs((original_ref, supplement_ref))
        if (not isinstance(episode_id, str) or not isinstance(event_id, str)
                or type(event_revision) is not int or event_revision < 1
                or type(part_index) is not int or part_index < 0
                or type(supplement_revision) is not int or supplement_revision < 1
                or supplement.revision != supplement_revision):
            raise AppError("memory_episode_supplement_invalid")
        async with self.connection(connection) as conn:
            await self.jobs.assert_generation_on(conn,generation)
            await self.identity.check_context(context, connection=conn)
            probe = await (await conn.execute("SELECT * FROM memory.episodes WHERE episode_id=%s AND actor_id=%s AND active",
                (episode_id,context.actor_id))).fetchone()
            if not probe or probe["event_id"] != event_id or probe["event_revision"] != event_revision:
                raise AppError("episode_unavailable",status=404)
            if not set(probe["scope_ids"]) <= set(context.scope_ids):
                raise AppError("episode_unavailable",status=404)
            originals = codec.refs(probe["source_refs"])
            if not any((ref.source_id,ref.revision) == (original.source_id,original.revision) for ref in originals):
                raise AppError("memory_episode_original_mismatch",status=409)
            descriptions = await self.sources.describe_for_write_on(conn, context, unique_refs(originals, (supplement,)))
            description = next((item for item in descriptions if item["source_id"] == original.source_id
                                and item["revision"] == original.revision), None)
            locator = description["locator"] if description else {}
            producer = locator.get("producer")
            if not producer or locator.get("event_id") != event_id or locator.get("part") != part_index:
                raise AppError("memory_episode_original_mismatch",status=409)
            if not await self.sources.validate_media_supplement_on(conn,context,supplement,
                    original_ref=original,producer=producer,event_id=event_id,
                    event_revision=event_revision,part_index=part_index):
                raise AppError("memory_episode_supplement_mismatch",status=409)
            partition = await self.consolidation.lock_partition_on(conn,context,probe["scope_ids"])
            row = await (await conn.execute("SELECT * FROM memory.episodes WHERE episode_id=%s FOR UPDATE",
                (episode_id,))).fetchone()
            if (not row or not row["active"] or row["revision"] != probe["revision"]
                    or row["source_refs"] != probe["source_refs"] or row["supplement_refs"] != probe["supplement_refs"]
                    or row["event_id"] != event_id or row["event_revision"] != event_revision):
                raise AppError("episode_revision_conflict",status=409)
            existing = await (await conn.execute("SELECT * FROM memory.episode_supplements WHERE episode_id=%s "
                "AND event_revision=%s AND part_index=%s AND supplement_revision=%s",
                (episode_id,event_revision,part_index,supplement_revision))).fetchone()
            if existing:
                if existing["original_ref"] != wire(original) or existing["supplement_ref"] != wire(supplement):
                    raise AppError("memory_episode_supplement_conflict",status=409)
                return codec.episode(row)
            latest = await (await conn.execute("SELECT * FROM memory.episode_supplements WHERE episode_id=%s "
                "AND event_revision=%s AND part_index=%s ORDER BY supplement_revision DESC LIMIT 1",
                (episode_id,event_revision,part_index))).fetchone()
            if latest and latest["supplement_revision"] > supplement_revision:
                raise AppError("memory_episode_supplement_stale",status=409)
            await conn.execute("UPDATE memory.episode_supplements SET active=false WHERE episode_id=%s "
                "AND event_revision=%s AND part_index=%s AND active", (episode_id,event_revision,part_index))
            await conn.execute("INSERT INTO memory.episode_supplements(episode_id,event_id,event_revision,part_index,"
                "supplement_revision,original_ref,supplement_ref,episode_revision) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
                (episode_id,event_id,event_revision,part_index,supplement_revision,Jsonb(wire(original)),
                 Jsonb(wire(supplement)),row["revision"]+1))
            active = await (await conn.execute("SELECT supplement_ref FROM memory.episode_supplements WHERE episode_id=%s "
                "AND event_revision=%s AND active ORDER BY part_index,supplement_revision",
                (episode_id,event_revision))).fetchall()
            supplements = unique_refs(codec.refs(item["supplement_ref"] for item in active))
            digest = body_hash({"event_content_hash":row["event_content_hash"],"supplement_refs":supplements})
            row = await (await conn.execute("UPDATE memory.episodes SET revision=revision+1,"
                "sequence=nextval('memory.revision_sequence'),recorded_at=now(),supplement_refs=%s,content_hash=%s,"
                "semantic_fingerprint=%s,embedding=NULL,embedding_profile=NULL,extraction_state='pending',"
                "extraction_started_at=NULL,extraction_cursor=%s,extraction_sources='[]',extraction_missing='{}' "
                "WHERE episode_id=%s RETURNING *", (Jsonb(wire(supplements)),digest,digest,
                Jsonb({"protocol":SCHEMA_VERSION}),episode_id))).fetchone()
            await self._snapshot_episode_on(conn, episode_id)
            await self.consolidation.dirty_on(conn,context,row,state=partition)
            await self.enqueue_index_on(conn,kind="episode",object_id=episode_id,revision=row["revision"],
                key=f"memory-episode-index:{episode_id}:{row['revision']}")
            return await self._episode_observed_on(conn,context,row)

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
                selected=(*codec.refs(row["source_refs"]),*codec.refs(row.get("supplement_refs")))
                if group and count+len(selected)>1800:
                    valid.extend(await self.sources.check_many_on(conn,context,tuple(group)))
                    group=[]
                    count=0
                group.append(selected)
                count+=len(selected)
            if group:
                valid.extend(await self.sources.check_many_on(conn,context,tuple(group)))
            if self.pipeline_recovery is not None:
                visible=tuple(row for row,allowed in zip(rows,valid,strict=True) if allowed)
                statuses=await self.pipeline_recovery.statuses_on(conn,context,visible)
                for row in visible:
                    row["consolidation"]=statuses[row["episode_id"]]
        items=tuple(codec.episode(row) for row,allowed in zip(rows,valid,strict=True) if allowed)
        missing=[]
        if len(rows)!=len(refs):
            missing.append("episodes_unavailable_or_revised")
        if not all(valid):
            missing.append("unavailable_sources")
        return Page(items,None,Coverage(not missing,missing=tuple(missing)))

    async def lock_episodes(self, context, refs, *, connection):
        """Lock selected sources first, then recheck and lock episode heads in ID order."""
        from .domain_utils import unique_refs
        refs = tuple(refs)
        if (len(refs) > 128 or any(not isinstance(item, (tuple, list)) or len(item) != 2
                or not isinstance(item[0], str) or type(item[1]) is not int or item[1] < 1 for item in refs)
                or len({item[0] for item in refs}) != len(refs)):
            raise AppError("invalid_episode_refs")
        await self.identity.check_context(context, connection=connection)
        ids = sorted(item[0] for item in refs)
        expected = dict(refs)
        first = await (await connection.execute("SELECT * FROM memory.episodes WHERE actor_id=%s "
            "AND episode_id=ANY(%s::text[]) ORDER BY episode_id", (context.actor_id, ids))).fetchall()
        if len(first) != len(ids) or any(not row["active"] or row["revision"] != expected[row["episode_id"]]
                or not set(row["scope_ids"]) <= set(context.scope_ids) for row in first):
            return EpisodePage((), None, Coverage(False, missing=("episodes_unavailable_or_revised",)), "e:0")
        sources = unique_refs(ref for row in first
                              for ref in (*codec.refs(row["source_refs"]), *codec.refs(row["supplement_refs"])))
        if not await self.sources.validate_persistence(connection, context, sources):
            return EpisodePage((), None, Coverage(False, missing=("unavailable_sources",)), "e:0")
        locked = await (await connection.execute("SELECT * FROM memory.episodes WHERE actor_id=%s "
            "AND episode_id=ANY(%s::text[]) ORDER BY episode_id FOR UPDATE", (context.actor_id, ids))).fetchall()
        if len(locked) != len(first) or any(
                old["episode_id"] != row["episode_id"] or not row["active"]
                or row["revision"] != expected[row["episode_id"]] or row["source_refs"] != old["source_refs"]
                or row["supplement_refs"] != old["supplement_refs"]
                or not set(row["scope_ids"]) <= set(context.scope_ids)
                for old, row in zip(first, locked, strict=True)):
            return EpisodePage((), None, Coverage(False, missing=("episodes_unavailable_or_revised",)), "e:0")
        by_id = {row["episode_id"]: codec.episode(row) for row in locked}
        watermark = "e:" + str(max((row["sequence"] for row in locked), default=0))
        return EpisodePage(tuple(by_id[item[0]] for item in refs), None, Coverage(True), watermark)

    async def index_learning(self, context, *, rule_id, revision, semantic_revision, kind,
                             source_refs, scope_ids, search_text, connection):
        """Delegate the reference projection to the single retrieval index owner."""
        from .indexing import put_learning_reference_on
        return await put_learning_reference_on(connection, context, rule_id=rule_id, revision=revision,
            semantic_revision=semantic_revision, kind=kind, source_refs=source_refs,
            scope_ids=scope_ids, search_text=search_text)

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
                conn, context, tuple((*codec.refs(row["source_refs"]),*codec.refs(row.get("supplement_refs"))) for row in scanned)
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

    async def _extraction_status_snapshot(self,context,episode_id):
        async with self.database.transaction(read_only=True) as conn:
            await self.identity.check_context(context,connection=conn)
            row=await (await conn.execute("SELECT * FROM memory.episodes WHERE episode_id=%s AND actor_id=%s AND active",
                (episode_id,context.actor_id))).fetchone()
            if not row or not set(row["scope_ids"])<=set(context.scope_ids) or not all(
                    await self.sources.check_many_on(conn,context,((*codec.refs(row["source_refs"]),*codec.refs(row.get("supplement_refs"))),))):
                raise AppError("episode_unavailable",status=404)
            return {"episode_id":episode_id,"revision":row["revision"],
                **await self.pipeline_recovery.status_on(conn,context,row)}

    async def _read_index_readiness(self,context,records):
        """Ask Retrieval after releasing the Memory transaction; preserve each object."""
        def unavailable(record):
            return {**record,"profile_key":None,"generation":None,"state":"unavailable"}

        results=[]
        for start in range(0,len(records),128):
            chunk=records[start:start+128]
            if self.index_readiness is None:
                results.extend(unavailable(record) for record in chunk)
                continue
            try:
                returned=wire(await self.index_readiness(context,tuple(chunk),budget=None))
            except (AppError,TimeoutError,OSError,ClientError):
                results.extend(unavailable(record) for record in chunk)
                continue
            if not isinstance(returned,list) or len(returned)!=len(chunk):
                results.extend(unavailable(record) for record in chunk)
                continue
            for expected,actual in zip(chunk,returned,strict=True):
                if (not isinstance(actual,dict) or any(actual.get(key)!=expected.get(key)
                        for key in ("kind","id","revision"))
                        or actual.get("state") not in {"ready","pending","failed","unavailable"}
                        or actual["state"] == "ready" and (not actual.get("profile_key")
                                                           or actual.get("generation") is None)):
                    results.append(unavailable(expected))
                else:
                    results.append({key:actual.get(key) for key in
                        ("kind","id","revision","profile_key","generation","state")})
        states={item["state"] for item in results}
        state=next((item for item in ("failed","unavailable","pending") if item in states),"ready")
        return {"state":state,"objects":results}

    async def extraction_status(self,context,episode_id):
        await self.identity.check_context(context)
        snapshot=await self._extraction_status_snapshot(context,episode_id)
        for _ in range(2):
            records=snapshot["indexing"]["objects"]
            if not records:
                return snapshot
            indexing=await self._read_index_readiness(context,records)
            # The profile lookup can involve network I/O. Reauthorize the
            # episode and its batch after it, in a new short transaction.
            current=await self._extraction_status_snapshot(context,episode_id)
            if (current["revision"]==snapshot["revision"]
                    and current["batch_id"]==snapshot["batch_id"]
                    and current["commit_state"]==snapshot["commit_state"]=="applied"
                    and current["indexing"]["objects"]==records):
                current["indexing"]=indexing
                return current
            snapshot=current
        if snapshot["indexing"]["objects"]:
            snapshot["indexing"]={"state":"unavailable","objects":[
                {**record,"profile_key":None,"generation":None,"state":"unavailable"}
                for record in snapshot["indexing"]["objects"]]}
        return snapshot

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
            ids=sorted({item["subject_id"] for status in statuses.values() for item in status["targets"]}
                | {item["subject_id"] for status in statuses.values() for item in status["target_results"]}
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
                    "targets":[item for item in status["targets"] if item["subject_id"] in people],
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
        from .evidence import build_evidence_unit
        hypotheses = tuple(item for item in claim_page.items if item.memory_kind == "hypothesis")
        facts = tuple(item for item in claim_page.items if item.memory_kind != "hypothesis")
        evidence = tuple(build_evidence_unit(item,time_mode=time_mode)
            for item in (*claim_page.items,*matters.items))
        from .display import display_evidence_on
        displayed = ()
        if evidence:
            async with self.database.transaction(read_only=True) as conn:
                displayed = await display_evidence_on(self,conn,context,evidence)
        evidence = tuple(item for item in displayed if item is not None)
        pages = (claim_page,matters)
        missing = [value for page in pages for value in page.coverage.missing]
        if len(evidence)<len(displayed):
            missing.append("memory_evidence_unavailable")
        missing = tuple(dict.fromkeys(missing))
        return {
            "person_id": person_id,
            "group_id": group_id,
            "group": group,
            "group_consolidation": await self._group_consolidation(context) if group else None,
            "can_manage": "memory.manage" in context.permissions or "*" in context.permissions,
            "claims": facts,
            "hypotheses": hypotheses,
            "matters": matters.items,
            "evidence_units": evidence,
            "time": {"mode":time_mode,"valid_at":valid_at,"known_at":known_at},
            "pages": {"claims":claim_page.next_cursor,"matters":matters.next_cursor},
            "coverage": Coverage(
                complete=all(page.coverage.complete for page in pages) and not missing,missing=missing
            ),
        }

    async def link_matter(self, context, source_ref, matter_id):
        from .extraction_contract import SCHEMA_VERSION
        from tianshu.sources.dependencies import unique_refs
        async with self.database.transaction() as conn:
            candidates = await (await conn.execute("SELECT * FROM memory.episodes WHERE actor_id=%s AND principal_id=%s "
                "AND active AND source_refs @> %s AND matter_id IS DISTINCT FROM %s ORDER BY episode_id LIMIT 129",
                (context.actor_id,context.principal_id,Jsonb(wire((source_ref,))),matter_id))).fetchall()
            if len(candidates) > 128:
                raise AppError("memory_episode_link_budget_exceeded",status=409)
            refs = unique_refs((source_ref,),*(codec.refs(row["source_refs"]) for row in candidates),
                *(codec.refs(row.get("supplement_refs")) for row in candidates))
            await self.sources.describe_for_write_on(conn, context, refs)
            row = await (
                await conn.execute(
                    "SELECT scope_ids,source_refs FROM memory.matters WHERE matter_id=%s AND actor_id=%s AND active",
                    (matter_id, context.actor_id),
                )
            ).fetchone()
            if not row or not set(row["scope_ids"]) <= set(context.scope_ids):
                raise AppError("matter_forbidden", status=403)
            if not (await self.sources.check_many_on(conn,context,(codec.refs(row["source_refs"]),)))[0]:
                raise AppError("matter_forbidden",status=403)
            partitions = {}
            for candidate in candidates:
                if not set(candidate["scope_ids"]) <= set(context.scope_ids):
                    raise AppError("episode_unavailable",status=403)
                key = tuple(sorted(candidate["scope_ids"]))
                if key not in partitions:
                    partitions[key] = await self.consolidation.lock_partition_on(conn,context,candidate["scope_ids"])
            for candidate in candidates:
                current = await (await conn.execute("SELECT * FROM memory.episodes WHERE episode_id=%s FOR UPDATE",
                    (candidate["episode_id"],))).fetchone()
                if (not current or not current["active"] or current["revision"] != candidate["revision"]
                        or current["source_refs"] != candidate["source_refs"]):
                    raise AppError("episode_revision_conflict",status=409)
                digest = body_hash({"content_hash":current["content_hash"],"matter_id":matter_id})
                changed = await (await conn.execute("UPDATE memory.episodes SET matter_id=%s,revision=revision+1,"
                    "sequence=nextval('memory.revision_sequence'),recorded_at=now(),content_hash=%s,semantic_fingerprint=%s,"
                    "embedding=NULL,embedding_profile=NULL,extraction_state='pending',extraction_cursor=%s,"
                    "extraction_sources='[]',extraction_missing='{}',extraction_started_at=NULL WHERE episode_id=%s RETURNING *",
                    (matter_id,digest,digest,Jsonb({"protocol":SCHEMA_VERSION}),current["episode_id"]))).fetchone()
                await self._snapshot_episode_on(conn,changed["episode_id"])
                await self.consolidation.dirty_on(conn,context,changed,state=partitions[tuple(sorted(candidate["scope_ids"]))])
                await self.enqueue_index_on(conn,kind="episode",object_id=changed["episode_id"],revision=changed["revision"],
                    key=f"memory-episode-index:{changed['episode_id']}:{changed['revision']}")
                await self._episode_observed_on(conn,context,changed)

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

    async def actors_for_source(self, conn, refs, kind):
        """Identify affected actors for Sources' independently dispatched invalidations."""
        rows = await (await conn.execute(
            "WITH i AS (SELECT %s::jsonb AS refs,%s::text AS kind), affected AS ("
            "SELECT e.actor_id FROM memory.episodes e,i WHERE sources.dependencies_intersect(e.source_refs||e.supplement_refs,i.refs,i.kind) "
            "UNION SELECT e.actor_id FROM memory.episode_revisions e,i WHERE sources.dependencies_intersect(e.source_refs||e.supplement_refs,i.refs,i.kind) "
            "UNION SELECT e.actor_id FROM memory.episode_supplements s JOIN memory.episodes e USING(episode_id),i "
            "WHERE sources.dependencies_intersect(jsonb_build_array(s.supplement_ref),i.refs,i.kind) "
            "UNION SELECT s.actor_id FROM memory.consolidation_batches b JOIN memory.consolidation_state s USING(partition_id),i "
            "WHERE sources.dependencies_intersect(b.dependency_refs,i.refs,i.kind) "
            "UNION SELECT m.request->'context'->>'actor_id' FROM memory.model_steps m,i "
            "WHERE sources.dependencies_intersect(COALESCE(m.request->'source_refs','[]'::jsonb),i.refs,i.kind) "
            "UNION SELECT c.actor_id FROM memory.claim_revisions r JOIN memory.claims c USING(claim_id),i "
            "WHERE sources.dependencies_intersect(r.source_refs,i.refs,i.kind) "
            "UNION SELECT r.actor_id FROM memory.matter_revisions r,i WHERE sources.dependencies_intersect(r.source_refs,i.refs,i.kind) "
            "UNION SELECT l.actor_id FROM memory.semantic_links l,i WHERE sources.dependencies_intersect(l.source_refs,i.refs,i.kind) "
            "UNION SELECT r.actor_id FROM memory.relationships r,i WHERE sources.dependencies_intersect(r.source_refs,i.refs,i.kind) "
            "UNION SELECT d.actor_id FROM memory.relationship_details d,i WHERE sources.dependencies_intersect(d.source_refs,i.refs,i.kind) "
            "UNION SELECT r.actor_id FROM memory.learning_index_refs r,i WHERE sources.dependencies_intersect(r.source_refs,i.refs,i.kind)) "
            "SELECT DISTINCT actor_id FROM affected ORDER BY actor_id NULLS FIRST",
            (Jsonb(wire(refs)),kind))).fetchall()
        return tuple(row["actor_id"] for row in rows)

    async def invalidate_actor(self, conn, sources, kind, *, actor_id):
        selected = [
            Jsonb([{"source_id": source.source_id, "revision": source.revision}])
            for source in sources
        ]
        parameters = (actor_id, selected, Jsonb(wire(sources)), kind)
        if not selected:
            return
        await conn.execute(
            "UPDATE memory.model_steps SET state='source_revoked',request='{}',error_code='source_unavailable' "
            "WHERE request->'context'->>'actor_id' IS NOT DISTINCT FROM %s AND request->'source_refs' @> ANY(%s::jsonb[]) "
            "AND sources.dependencies_intersect(request->'source_refs',%s,%s)",
            parameters,
        )
        affected_batches = await (await conn.execute("SELECT batch_id,manifest FROM memory.consolidation_batches "
            "WHERE partition_id IN (SELECT partition_id FROM memory.consolidation_state WHERE actor_id IS NOT DISTINCT FROM %s) "
            "AND dependency_refs @> ANY(%s::jsonb[]) AND sources.dependencies_intersect(dependency_refs,%s,%s) "
            "ORDER BY batch_id FOR UPDATE", parameters)).fetchall()
        for batch in affected_batches:
            await self.consolidation.cancel_recovery_on(conn,batch["manifest"])
        invalidated = await (await conn.execute(
            "UPDATE memory.consolidation_batches SET status='revoked',error_code='source_unavailable',"
            "manifest='{}',read_set='[]',commit_refs='{}',updated_at=now() "
            "WHERE partition_id IN (SELECT partition_id FROM memory.consolidation_state WHERE actor_id IS NOT DISTINCT FROM %s) "
            "AND dependency_refs @> ANY(%s::jsonb[]) AND sources.dependencies_intersect(dependency_refs,%s,%s) "
            "RETURNING batch_id,partition_id", parameters)).fetchall()
        if invalidated:
            await conn.execute("UPDATE memory.consolidation_compilations SET proposal='{}',alias_map='[]',"
                "source_snapshot='[]',selected=false WHERE batch_id=ANY(%s::text[])",
                ([batch["batch_id"] for batch in invalidated],))
            counted = await (await conn.execute("SELECT batch_id FROM memory.statistics_outbox "
                "WHERE batch_id=ANY(%s::text[])", ([batch["batch_id"] for batch in invalidated],))).fetchall()
            for batch in counted:
                await self.record_batch_statistics_on(conn,batch["batch_id"],"revoked")
        from .indexing import purge_learning_references_on
        await purge_learning_references_on(conn,sources,kind,actor_id=actor_id)
        for batch in invalidated:
            await conn.execute("UPDATE memory.consolidation_state SET active_batch_id=NULL,last_error='source_unavailable',"
                "lease_owner=NULL,lease_until=NULL WHERE partition_id=%s AND active_batch_id=%s",
                (batch["partition_id"],batch["batch_id"]))
        await conn.execute(
            "UPDATE memory.episodes SET active=false,summary='',actual_result=NULL,lexical='',embedding=NULL,embedding_profile=NULL,semantic_fingerprint=NULL,"
            "sequence=nextval('memory.revision_sequence') WHERE actor_id IS NOT DISTINCT FROM %s AND active "
            "AND source_refs @> ANY(%s::jsonb[]) AND sources.dependencies_intersect(source_refs,%s,%s)",
            parameters,
        )
        await conn.execute(
            "UPDATE memory.episode_revisions SET active=false,summary='',actual_result=NULL,semantic_fingerprint=NULL "
            "WHERE actor_id IS NOT DISTINCT FROM %s AND active AND (sources.dependencies_intersect(source_refs,%s,%s) "
            "OR sources.dependencies_intersect(supplement_refs,%s,%s))",
            (actor_id,Jsonb(wire(sources)),kind,Jsonb(wire(sources)),kind),
        )
        # Withdrawal of only a later attachment must not erase the separately
        # accepted original text. Retire that attachment and expose the same
        # Episode with explicit missing coverage and a new semantic revision.
        retired = await (await conn.execute("UPDATE memory.episode_supplements s SET active=false FROM memory.episodes e "
            "WHERE s.episode_id=e.episode_id AND e.actor_id IS NOT DISTINCT FROM %s AND s.active "
            "AND sources.dependencies_intersect(jsonb_build_array(s.supplement_ref),%s,%s) RETURNING s.episode_id",
            (actor_id,Jsonb(wire(sources)),kind))).fetchall()
        for episode_id in sorted({item["episode_id"] for item in retired}):
            head = await (await conn.execute("SELECT * FROM memory.episodes WHERE episode_id=%s AND active FOR UPDATE",
                (episode_id,))).fetchone()
            if not head:
                continue
            live = await (await conn.execute("SELECT supplement_ref FROM memory.episode_supplements WHERE episode_id=%s "
                "AND event_revision=%s AND active ORDER BY part_index,supplement_revision",
                (episode_id,head["event_revision"]))).fetchall()
            supplements = codec.refs(item["supplement_ref"] for item in live)
            if wire(supplements) == head["supplement_refs"]:
                continue
            digest = body_hash({"event_content_hash":head["event_content_hash"],"supplement_refs":supplements})
            head = await (await conn.execute("UPDATE memory.episodes SET revision=revision+1,"
                "sequence=nextval('memory.revision_sequence'),recorded_at=now(),supplement_refs=%s,"
                "content_hash=%s,semantic_fingerprint=%s,summary='',actual_result=NULL,lexical='',embedding=NULL,embedding_profile=NULL,"
                "extraction_state='completed_with_missing',extraction_started_at=NULL,extraction_sources='[]',"
                "extraction_missing=ARRAY(SELECT DISTINCT x FROM unnest(extraction_missing||ARRAY['media_unavailable']) x) "
                "WHERE episode_id=%s RETURNING *", (Jsonb(wire(supplements)),digest,digest,episode_id))).fetchone()
            await self._snapshot_episode_on(conn,episode_id)
            await self.enqueue_index_on(conn,kind="episode",object_id=episode_id,revision=head["revision"],
                key=f"memory-episode-index:{episode_id}:{head['revision']}")
        await conn.execute(
            "UPDATE memory.relationship_details SET active=false,content='',revision=revision+1 "
            "WHERE actor_id IS NOT DISTINCT FROM %s AND active AND source_refs @> ANY(%s::jsonb[]) AND sources.dependencies_intersect(source_refs,%s,%s)",
            parameters,
        )
        await conn.execute(
            "UPDATE memory.relationships SET summary='',relationship_type='acquaintance',display_label='认识的人',"
            "revision=revision+1 WHERE actor_id IS NOT DISTINCT FROM %s AND source_refs @> ANY(%s::jsonb[]) AND sources.dependencies_intersect(source_refs,%s,%s)",
            parameters,
        )
        await self.claims.source_changed(conn, sources, kind, actor_id=actor_id)
