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


def parse_time(value):
    value = datetime.fromisoformat(value) if isinstance(value, str) else value
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise AppError("aware_datetime_required")
    return value


def cursor_sequence(cursor):
    if cursor is None:
        return 0
    if not isinstance(cursor, str) or not re.fullmatch(r"e:[0-9]{1,20}", cursor):
        raise AppError("invalid_episode_cursor")
    return int(cursor[2:])


class MemoryService:
    def __init__(self, database, identity, sources, commands, jobs, models):
        self.database, self.identity, self.sources = database, identity, sources
        self.commands, self.jobs, self.models = commands, jobs, models
        self.claims = None

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
        origin = OriginKind(episode.get("origin_kind", context.origin_kind))
        if not source_refs and origin == OriginKind.HUMAN:
            raise AppError("human_episode_source_required")
        async with self.connection(connection) as conn:
            descriptions = await self.sources.describe_for_write_on(conn, context, source_refs)
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
            existing = await (
                await conn.execute(
                    "SELECT * FROM memory.episodes WHERE actor_id=%s AND event_id=%s FOR UPDATE",
                    (context.actor_id, event_id),
                )
            ).fetchone()
            if existing and existing["content_hash"] == digest:
                return codec.episode(existing)
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
            await self.jobs.enqueue(
                conn,
                JobSpec(
                    "memory.extract",
                    f"memory-extract:{episode_id}:{row['sequence']}",
                    {"episode_id": episode_id, "sequence": row["sequence"]},
                    ServiceClass.ELASTIC,
                    subject_id=context.actor_id,
                    matter_id=episode.get("matter_id"),
                ),
            )
            return codec.episode(row)

    async def episodes_since(self, context, cursor, limit):
        return await self._episodes(context, cursor, limit)

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

    async def record_reflection(self, context, proposal):
        if proposal.actor_id != context.actor_id or not proposal.interpretation:
            raise AppError("reflection_interpretation_required")
        source_refs = tuple(proposal.source_refs)
        if not source_refs or not proposal.episode_ids:
            raise AppError("reflection_original_evidence_required")
        async with self.database.transaction() as conn:
            descriptions = await self.sources.describe_for_write_on(conn, context, source_refs)
            originals = await (
                await conn.execute(
                    "SELECT episode_id,source_refs FROM memory.episodes WHERE actor_id=%s "
                    "AND episode_id=ANY(%s) AND active",
                    (context.actor_id, list(proposal.episode_ids)),
                )
            ).fetchall()
            if len(originals) != len(set(proposal.episode_ids)):
                raise AppError("reflection_episode_unavailable", status=409)
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
            scopes = sorted({scope for item in descriptions for scope in item["scope_ids"]})
            values = (
                proposal.proposal_id,
                context.actor_id,
                context.principal_id,
                context.person_id,
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
                    "INSERT INTO memory.reflections(proposal_id,actor_id,principal_id,person_id,episode_ids,content,kind,"
                    "occurred_at,source_refs,scope_ids,conditions,counterexamples) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
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

    async def reflection_page(self, context, *, person_id=None, cursor=None, limit=40):
        await self.identity.check_context(context)
        limit = min(max(int(limit), 1), 100)
        async with self.database.transaction(read_only=True) as conn:
            rows = await (
                await conn.execute(
                    "SELECT * FROM memory.reflections WHERE actor_id=%s AND active "
                    "AND scope_ids<@%s::text[] AND (%s::text IS NULL OR person_id=%s) "
                    "AND (%s::text IS NULL OR proposal_id>%s) ORDER BY proposal_id LIMIT %s",
                    (
                        context.actor_id,
                        list(context.scope_ids),
                        person_id,
                        person_id,
                        cursor,
                        cursor,
                        limit + 1,
                    ),
                )
            ).fetchall()
            checked = await self.sources.check_many_on(
                conn, context, tuple(codec.refs(row["source_refs"]) for row in rows[:limit])
            )
        visible = tuple(codec.reflection(row) for row, valid in zip(rows[:limit], checked) if valid)
        return Page(
            visible,
            rows[limit - 1]["proposal_id"] if len(rows) > limit else None,
            Coverage(
                complete=len(rows) <= limit and all(checked),
                missing=("unavailable_sources",) if not all(checked) else (),
            ),
        )

    async def projection(self, context, person_id=None, group_id=None):
        await self.identity.check_context(context)
        if group_id is not None and (
            context.conversation_id != group_id or context.conversation_kind != "group"
        ):
            raise AppError("group_projection_forbidden", status=403)
        claim_page = await self.claims.list(context, subject_id=person_id, limit=100)
        matters = await self.claims.matters(context, subject_id=person_id, limit=50)
        reflections = await self.reflection_page(context, person_id=person_id, limit=40)
        return {
            "person_id": person_id,
            "group_id": group_id,
            "claims": claim_page.items,
            "matters": matters.items,
            "reflections": reflections.items,
            "coverage": Coverage(
                complete=all(page.coverage.complete for page in (claim_page, matters, reflections))
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
        await conn.execute(
            "UPDATE memory.episodes SET active=false,summary='',actual_result=NULL,"
            "sequence=nextval('memory.revision_sequence') WHERE active AND source_refs @> ANY(%s::jsonb[]) AND sources.dependencies_intersect(source_refs,%s,%s)",
            parameters,
        )
        await conn.execute(
            "UPDATE memory.reflections SET active=false,content='',conditions='{}',counterexamples='{}',"
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
        await conn.execute(
            "UPDATE memory.matters SET state='uncertain',summary='',title='依据已失效的事项',revision=revision+1 "
            "WHERE source_refs @> ANY(%s::jsonb[]) AND sources.dependencies_intersect(source_refs,%s,%s)",
            parameters,
        )
        await self.claims.source_changed(conn, sources, kind)
