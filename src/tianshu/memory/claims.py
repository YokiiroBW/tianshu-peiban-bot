"""Typed claim revisions, explicit matter changes and authorized complete lists."""

from dataclasses import replace
from datetime import UTC, datetime
import json
import re

from psycopg.types.json import Jsonb

from tianshu.runtime.api import CommandResult, Page
from tianshu.runtime.commands import body_hash
from tianshu.runtime.errors import AppError
from tianshu.runtime.jobs import JobSpec
from tianshu.runtime.serialization import wire
from tianshu.types import Coverage, OriginKind, ServiceClass
from . import codec
from .service import parse_time


ELIGIBLE_SCOPES = """EXISTS(SELECT 1 FROM jsonb_array_elements(r.scope_groups) g
    WHERE ARRAY(SELECT jsonb_array_elements_text(g))<@%s::text[])"""


def lexical(text):
    """Native simple-TS lexemes plus Chinese characters/bigrams and intact proper names."""
    words = re.findall(r"[A-Za-z0-9_]+|[\u3400-\u9fff]+", text.casefold())
    tokens = []
    for word in words:
        tokens.append(word)
        if re.fullmatch(r"[\u3400-\u9fff]+", word):
            tokens.extend(word)
            tokens.extend(word[index:index+2] for index in range(len(word)-1))
    return " ".join(dict.fromkeys(tokens))


class ClaimService:
    def __init__(self, memory):
        self.memory = memory
        self.database, self.identity, self.sources = memory.database, memory.identity, memory.sources
        self.commands, self.jobs = memory.commands, memory.jobs

    async def visible_subjects(self, context, conn):
        rows = await self.identity.subjects_on(conn,context)
        return {row["person_id"] for row in rows if row["person_id"]} | {"actor:"+context.actor_id}

    async def put_on(self, conn, context, body, *, automatic=False):
        source_refs = codec.refs(body.get("source_refs", ()))
        if not source_refs or not context.actor_id:
            raise AppError("claim_original_sources_required")
        descriptions = await self.sources.describe_many_on(conn, context, source_refs)
        subject_id = body.get("subject_id") or context.person_id
        if subject_id not in await self.visible_subjects(context, conn):
            raise AppError("claim_subject_forbidden", status=403)
        predicate = body.get("predicate")
        if not isinstance(predicate, str) or not re.fullmatch(r"[a-zA-Z0-9_.-]{1,128}", predicate):
            raise AppError("invalid_claim_predicate")
        assertion = body.get("assertion", "reported")
        if assertion not in {"self_confirmed", "reported", "quoted", "inferred", "observed"}:
            raise AppError("invalid_claim_assertion")
        speaker_id = body.get("speaker_id", context.person_id)
        if speaker_id not in {context.person_id, "actor:"+context.actor_id, None}:
            raise AppError("claim_speaker_mismatch", status=403)
        if assertion == "self_confirmed" and (speaker_id != subject_id or
                any(item["origin_kind"] != "human" for item in descriptions)):
            raise AppError("claim_self_confirmation_unproven", status=403)
        value, conditions = body.get("value"), body.get("conditions", {})
        if not isinstance(conditions, dict) or len(json.dumps(wire(value), ensure_ascii=False)) > 16000:
            raise AppError("invalid_claim_value")
        observed_at = parse_time(body.get("observed_at", datetime.now(UTC)))
        valid_from = parse_time(body["valid_from"]) if body.get("valid_from") else None
        valid_until = parse_time(body["valid_until"]) if body.get("valid_until") else None
        if valid_from and valid_until and valid_until <= valid_from:
            raise AppError("invalid_claim_validity")
        polarity = body.get("polarity", True)
        if type(polarity) is not bool:
            raise AppError("invalid_claim_polarity")
        directory=await self.identity.subjects_on(conn,context)
        subject=next((item for item in directory if item["person_id"]==subject_id),{})
        names=" ".join((subject.get("display_name",""),*(subject.get("aliases") or ())))
        content = f"{subject_id} {names} {predicate}: {json.dumps(wire(value), ensure_ascii=False)}; "
        content += f"assertion={assertion}; polarity={polarity}; conditions={json.dumps(conditions, ensure_ascii=False)}"
        units = sorted({item["evidence_unit"] for item in descriptions})
        scope_ids = sorted({scope for item in descriptions for scope in item["scope_ids"]})
        semantic = {"actor_id": context.actor_id, "subject_id": subject_id, "predicate": predicate,
                    "value": value, "conditions": conditions, "polarity": polarity, "speaker_id": speaker_id,
                    "assertion": assertion, "valid_from": valid_from, "valid_until": valid_until}
        claim_id = body.get("claim_id") or "claim_"+body_hash(semantic)[:48]
        await conn.execute("INSERT INTO memory.claims(claim_id,current_revision,actor_id,principal_id) VALUES(%s,0,%s,%s) "
            "ON CONFLICT DO NOTHING", (claim_id, context.actor_id, context.principal_id))
        head = await (await conn.execute("SELECT * FROM memory.claims WHERE claim_id=%s FOR UPDATE", (claim_id,))).fetchone()
        if head["actor_id"] != context.actor_id or head["principal_id"] != context.principal_id:
            raise AppError("claim_forbidden", status=403)
        previous = await (await conn.execute("SELECT * FROM memory.claim_revisions WHERE claim_id=%s AND revision=%s",
                                            (claim_id, head["current_revision"]))).fetchone()
        expected = body.get("expected_revision")
        if expected is not None and expected != head["current_revision"]:
            raise AppError("revision_conflict", status=409)
        if previous and not automatic:
            await self.sources.revoke_projection(conn,context,f"{claim_id}:{previous['revision']}")
        support_groups, scope_groups = [wire(source_refs)], [scope_ids]
        if previous and automatic:
            if (previous["value"] != wire(value) or previous["predicate"] != predicate or previous["subject_id"] != subject_id):
                raise AppError("claim_identity_conflict", status=409)
            if set(units) <= set(previous["evidence_units"]) and previous["state"] == "active":
                return codec.claim({**previous, "actor_id": context.actor_id})
            support_groups = previous["support_groups"]+[wire(source_refs)]
            scope_groups = previous["scope_groups"]+[scope_ids]
            units = sorted(set(units) | set(previous["evidence_units"]))
            source_refs = tuple({body_hash(ref): ref for group in support_groups for ref in codec.refs(group)}.values())
            scope_ids = sorted({scope for group in scope_groups for scope in group})
            observed_at=max(observed_at,previous["observed_at"])
        contradictions = await (await conn.execute("SELECT c.claim_id FROM memory.claims c JOIN memory.claim_revisions r "
            "ON r.claim_id=c.claim_id AND r.revision=c.current_revision WHERE c.actor_id=%s AND r.subject_id=%s "
            "AND r.predicate=%s AND r.state='active' AND (r.value<>%s OR r.polarity<>%s) AND c.claim_id<>%s AND "+ELIGIBLE_SCOPES+
            " AND sources.any_support_usable(r.support_groups,%s::text[]) LIMIT 64",
            (context.actor_id, subject_id, predicate, Jsonb(wire(value)), polarity, claim_id,list(context.scope_ids),list(context.scope_ids)))).fetchall()
        revision = head["current_revision"]+1
        await conn.execute("INSERT INTO memory.claim_revisions(claim_id,revision,speaker_id,subject_id,predicate,value,assertion,polarity,conditions,"
            "valid_from,valid_until,observed_at,state,source_refs,support_groups,scope_groups,scope_ids,matter_id,supersedes,contradictions,evidence_units,content,lexical) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'active',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (claim_id, revision, speaker_id, subject_id, predicate, Jsonb(wire(value)), assertion, polarity, Jsonb(conditions),
             valid_from, valid_until, observed_at, Jsonb(wire(source_refs)), Jsonb(support_groups), Jsonb(scope_groups), scope_ids,
             body.get("matter_id"), list(body.get("supersedes", ())), [row["claim_id"] for row in contradictions], units, content, lexical(content)))
        await conn.execute("UPDATE memory.claims SET current_revision=%s WHERE claim_id=%s", (revision, claim_id))
        await self.jobs.enqueue(conn, JobSpec("memory.index", f"memory-index:{claim_id}:{revision}",
            {"claim_id": claim_id, "revision": revision}, ServiceClass.ELASTIC, subject_id=subject_id))
        row = await (await conn.execute("SELECT r.*,c.actor_id FROM memory.claim_revisions r JOIN memory.claims c USING(claim_id) "
            "WHERE r.claim_id=%s AND r.revision=%s", (claim_id, revision))).fetchone()
        return codec.claim(row)

    async def selected_groups(self, conn, context, rows):
        # Check selected alternatives in batches, stopping once each row has a valid support.
        alternatives = [iter(reversed(row["support_groups"])) for row in rows]
        result, exhausted = [None]*len(rows), set()
        while True:
            groups, owners, units = [], [], 0
            for index, iterator in enumerate(alternatives):
                if result[index] is not None or index in exhausted:
                    continue
                group = next(iterator, None)
                if group is None:
                    exhausted.add(index)
                    continue
                group = codec.refs(group)
                if len(group) > 2048:
                    raise AppError("claim_support_budget_exceeded", status=503)
                if groups and units+len(group)>2048:
                    # Flush this batch before advancing the remaining row iterators.
                    checked = await self.sources.check_many_on(conn, context, tuple(groups))
                    for owner, candidate, valid in zip(owners, groups, checked):
                        if valid:
                            result[owner] = candidate
                    groups, owners, units = [], [], 0
                groups.append(group)
                owners.append(index)
                units += len(group)
            if groups:
                checked = await self.sources.check_many_on(conn, context, tuple(groups))
                for owner, candidate, valid in zip(owners, groups, checked):
                    if valid:
                        result[owner] = candidate
            if all(result[index] is not None or index in exhausted for index in range(len(rows))):
                break
        return result

    async def list(self, context, *, subject_id=None, cursor=None, limit=50):
        await self.identity.check_context(context)
        limit = min(max(int(limit), 1), 200)
        async with self.database.transaction(read_only=True) as conn:
            rows = await (await conn.execute("SELECT r.*,c.actor_id FROM memory.claims c JOIN memory.claim_revisions r "
                "ON r.claim_id=c.claim_id AND r.revision=c.current_revision WHERE c.actor_id=%s AND r.state='active' AND "
                + ELIGIBLE_SCOPES + " AND (%s::text IS NULL OR r.subject_id=%s) AND (%s::text IS NULL OR c.claim_id>%s) "
                "ORDER BY c.claim_id LIMIT %s", (context.actor_id, list(context.scope_ids), subject_id, subject_id,
                cursor, cursor, limit+1))).fetchall()
            selected = await self.selected_groups(conn, context, rows[:limit])
        items = tuple(codec.claim(row, refs) for row, refs in zip(rows[:limit], selected) if refs is not None)
        return Page(items, rows[limit-1]["claim_id"] if len(rows)>limit else None,
            Coverage(complete=len(rows)<=limit and all(item is not None for item in selected),
                missing=("unavailable_sources",) if any(item is None for item in selected) else ()))

    async def get(self, context, claim_id):
        await self.identity.check_context(context)
        async with self.database.transaction(read_only=True) as conn:
            row = await (await conn.execute("SELECT r.*,c.actor_id FROM memory.claims c JOIN memory.claim_revisions r "
                "ON r.claim_id=c.claim_id AND r.revision=c.current_revision WHERE c.claim_id=%s AND c.actor_id=%s AND "
                +ELIGIBLE_SCOPES, (claim_id, context.actor_id, list(context.scope_ids)))).fetchone()
            selected = (await self.selected_groups(conn, context, (row,)))[0] if row else None
        if selected is None:
            raise AppError("claim_unavailable", status=404)
        return codec.claim(row, selected)

    async def correct(self, context, body):
        old = await self.get(context, body["claim_id"])
        command_refs = await self.sources.record_event(context, producer="web.claim.correct", event_id=body["client_request_id"],
            revision=1, occurred_at=datetime.now(UTC), parts=({"text": json.dumps({"claim_id": old.claim_id,
                "value": body.get("value"), "polarity": body.get("polarity", old.polarity), "conditions": body.get("conditions", old.conditions)}, ensure_ascii=False)},),
            origin_kind=OriginKind.HUMAN)
        async def apply(conn):
            if not await self.sources.validate_persistence(conn,context,(*old.source_refs,*command_refs)):
                raise AppError("source_unavailable",status=403)
            updated = await self.put_on(conn, context, {**wire(old), **body, "source_refs": command_refs,
                "speaker_id": context.person_id, "assertion": "self_confirmed" if context.person_id == old.subject_id else "reported",
                "observed_at": datetime.now(UTC)})
            return CommandResult(body["client_request_id"], data=updated, revision=updated.revision)
        async def replay(conn, original):
            await self.identity.check_context(context, connection=conn)
            return original
        result = await self.commands.execute(context, "memory.claim.correct", body["client_request_id"], body_hash(body), apply, on_replay=replay)
        return replace(result, data=await self.get(context, old.claim_id))

    async def share_ordinary(self, context, body):
        original = await self.get(context, body["claim_id"])
        if original.predicate not in {"interest", "hobby", "preference.ordinary", "public_interest"}:
            raise AppError("claim_not_ordinary_interest", status=422)
        if original.assertion != "self_confirmed" or original.conditions or not isinstance(original.value, (str, list)):
            raise AppError("claim_sharing_requires_explicit_ordinary_unit", status=422)
        if len(json.dumps(original.value, ensure_ascii=False)) > 1000:
            raise AppError("claim_sharing_unit_too_large")
        if body.get("expected_revision") != original.revision:
            raise AppError("revision_conflict", status=409)
        async def apply(conn):
            source = await self.sources.publish_projection(context, projection_id=f"{original.claim_id}:{original.revision}",
                content=f"{original.subject_id} {original.predicate}: {json.dumps(original.value, ensure_ascii=False)}",
                source_refs=original.source_refs, connection=conn)
            value = {**wire(original), "claim_id": f"shared_{original.claim_id}_{original.revision}", "source_refs": (source,),
                "expected_revision": None, "assertion": "observed", "speaker_id": None}
            shared = await self.put_on(conn, context, value, automatic=True)
            return CommandResult(body["client_request_id"], data=shared, revision=shared.revision)
        async def replay(conn, identity):
            await self.identity.check_context(context, connection=conn)
            return identity
        result = await self.commands.execute(context, "memory.claim.share", body["client_request_id"], body_hash(body), apply, on_replay=replay)
        return replace(result, data=await self.get(context, f"shared_{original.claim_id}_{original.revision}"))

    async def matters(self, context, *, subject_id=None, cursor=None, limit=50):
        await self.identity.check_context(context)
        limit = min(max(int(limit), 1), 200)
        async with self.database.transaction(read_only=True) as conn:
            rows = await (await conn.execute("SELECT * FROM memory.matters WHERE actor_id=%s AND scope_ids<@%s::text[] "
                "AND (%s::text IS NULL OR subject_id=%s) AND (%s::text IS NULL OR matter_id>%s) ORDER BY matter_id LIMIT %s",
                (context.actor_id, list(context.scope_ids), subject_id, subject_id, cursor, cursor, limit+1))).fetchall()
            checked = await self.sources.check_many_on(conn, context, tuple(codec.refs(row["source_refs"]) for row in rows[:limit]))
            links = await (await conn.execute("SELECT matter_id,claim_id FROM memory.matter_claims WHERE matter_id=ANY(%s)",
                ([row["matter_id"] for row in rows[:limit]],))).fetchall()
        items = tuple(codec.matter(row, (link["claim_id"] for link in links if link["matter_id"] == row["matter_id"]))
            for row, valid in zip(rows[:limit], checked) if valid)
        return Page(items, rows[limit-1]["matter_id"] if len(rows)>limit else None,
                    Coverage(complete=len(rows)<=limit and all(checked)))

    async def matter_on(self, conn, context, body, *, automatic=False):
        source_refs = codec.refs(body["source_refs"])
        descriptions = await self.sources.describe_many_on(conn, context, source_refs)
        if not source_refs:
            raise AppError("matter_sources_required")
        matter_id, title = body["matter_id"], body["title"]
        state = body.get("state", "planned")
        if state not in {"planned", "active", "rescheduled", "cancelled", "uncertain", "completed"}:
            raise AppError("invalid_matter_state")
        if state == "completed" and (automatic or any(item["origin_kind"] != "human" for item in descriptions)):
            raise AppError("matter_completion_requires_human_confirmation")
        if not isinstance(title, str) or not 1 <= len(title) <= 1000:
            raise AppError("invalid_matter_title")
        observed_at = parse_time(body.get("observed_at", datetime.now(UTC)))
        due_at = parse_time(body["due_at"]) if body.get("due_at") else None
        existing = await (await conn.execute("SELECT * FROM memory.matters WHERE matter_id=%s FOR UPDATE", (matter_id,))).fetchone()
        if existing and (existing["actor_id"] != context.actor_id or not set(existing["scope_ids"]) <= set(context.scope_ids)):
            raise AppError("matter_forbidden", status=403)
        if existing and automatic and observed_at < existing["observed_at"]:
            return codec.matter(existing)
        if existing and automatic and observed_at==existing["observed_at"] and all(existing[key]==wire(body.get(key,default))
                for key,default in (("title",None),("state","planned"),("due_at",None),("summary",""),("conditions",{})) if key!="due_at"):
            if existing["due_at"]==due_at:
                return codec.matter(existing)
        expected = body.get("expected_revision", existing["revision"] if automatic and existing else 0)
        if expected != (existing["revision"] if existing else 0):
            raise AppError("revision_conflict", status=409)
        scopes = sorted({scope for item in descriptions for scope in item["scope_ids"]})
        subject_id = body.get("subject_id", context.person_id)
        if subject_id not in await self.visible_subjects(context, conn):
            raise AppError("matter_subject_forbidden", status=403)
        row = await (await conn.execute("INSERT INTO memory.matters(matter_id,actor_id,principal_id,subject_id,title,state,due_at,summary,conditions,source_refs,scope_ids,observed_at) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(matter_id) DO UPDATE SET subject_id=excluded.subject_id,title=excluded.title,"
            "state=excluded.state,due_at=excluded.due_at,summary=excluded.summary,conditions=excluded.conditions,source_refs=excluded.source_refs,"
            "scope_ids=excluded.scope_ids,observed_at=excluded.observed_at,revision=memory.matters.revision+1 "
            "WHERE memory.matters.revision=%s RETURNING *",
            (matter_id, context.actor_id, context.principal_id, subject_id, title, state, due_at,
             body.get("summary", ""), Jsonb(body.get("conditions", {})), Jsonb(wire(source_refs)), scopes, observed_at, expected))).fetchone()
        if row is None:
            raise AppError("revision_conflict", status=409)
        return codec.matter(row)

    async def save_matter(self, context, body):
        source_refs = codec.refs(body.get("source_refs", ()))
        if not source_refs:
            source_refs = await self.sources.record_event(context, producer="web.matter", event_id=body["client_request_id"], revision=1,
                occurred_at=datetime.now(UTC), parts=({"text": json.dumps({key: body.get(key) for key in
                ("title", "state", "due_at", "summary", "conditions")}, ensure_ascii=False)},), origin_kind=context.origin_kind)
        async def apply(conn):
            result = await self.matter_on(conn, context, {**body, "source_refs": source_refs})
            return CommandResult(body["client_request_id"], data=result, revision=result.revision)
        async def replay(conn, original):
            await self.identity.check_context(context, connection=conn)
            row = await (await conn.execute("SELECT * FROM memory.matters WHERE matter_id=%s AND actor_id=%s",
                                            (body["matter_id"], context.actor_id))).fetchone()
            if not row or not (await self.sources.check_many_on(conn, context, (codec.refs(row["source_refs"]),)))[0]:
                return original
            return replace(original, data=codec.matter(row))
        return await self.commands.execute(context, "memory.matter.save", body["client_request_id"], body_hash(body), apply, on_replay=replay)

    async def source_changed(self, conn, sources, kind, cursor=None):
        last_id, last_revision = (cursor["claim_id"], cursor["revision"]) if cursor else (None, None)
        rows = await (await conn.execute("SELECT r.*,c.actor_id,c.principal_id FROM memory.claim_revisions r JOIN memory.claims c "
            "ON c.claim_id=r.claim_id WHERE r.state='active' AND r.source_refs @> ANY(%s::jsonb[]) "
            "AND (%s::text IS NULL OR (r.claim_id,r.revision)>(%s,%s)) ORDER BY r.claim_id,r.revision LIMIT 129",
            ([Jsonb([{"source_id": source.source_id, "revision": source.revision}]) for source in sources], last_id, last_id, last_revision))).fetchall()
        # Retention validity is distinct from the current viewer's access. Independent supports survive
        # even if their owner is disabled; this predicate returns no source content or additional rights.
        selected=rows[:128]
        if selected:
            validity=await (await conn.execute("SELECT claim_id,revision,sources.any_support_usable(support_groups,scope_ids) AS valid "
                "FROM memory.claim_revisions WHERE (claim_id,revision) IN (SELECT * FROM unnest(%s::text[],%s::bigint[]))",
                ([row["claim_id"] for row in selected],[row["revision"] for row in selected]))).fetchall()
            invalid=[row for row in validity if not row["valid"]]
            if invalid:
                await conn.execute("UPDATE memory.claim_revisions SET state='unsupported',content='',value='null',conditions='{}',"
                    "embedding=NULL,lexical='' WHERE (claim_id,revision) IN(SELECT * FROM unnest(%s::text[],%s::bigint[]))",
                    ([row["claim_id"] for row in invalid],[row["revision"] for row in invalid]))
        if len(rows)>128:
            from uuid import uuid4
            await self.jobs.enqueue(conn, JobSpec("memory.purge_source", "memory-purge:"+uuid4().hex,
                {"sources": wire(sources), "kind": kind, "cursor": {"claim_id": rows[127]["claim_id"], "revision": rows[127]["revision"]}},
                ServiceClass.DUE))

    async def purge_source(self, sources, kind, cursor=None):
        from tianshu.types import SourceRef
        async with self.database.transaction() as conn:
            await self.source_changed(conn, tuple(SourceRef(**source) for source in sources), kind, cursor)
