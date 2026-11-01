"""Authorized claim/matter use cases over one shared revision writer."""

from dataclasses import replace
from datetime import UTC, datetime
import json

from tianshu.runtime.api import CommandResult, Page
from tianshu.runtime.commands import body_hash
from tianshu.runtime.errors import AppError
from tianshu.runtime.serialization import wire
from tianshu.types import Coverage, OriginKind
from . import codec
from .domain_utils import CLAIM_PREDICATE_PATTERN as CLAIM_PREDICATE_PATTERN, lexical as lexical
from .evidence import build_evidence_unit, select_supports_on, temporal_selection, valid_for_time
from .revisions import CLAIM_FIELDS, RevisionService, stable_id
from .types import RecordRef


ELIGIBLE_SCOPES = """EXISTS(SELECT 1 FROM jsonb_array_elements(r.scope_groups) g
    WHERE ARRAY(SELECT jsonb_array_elements_text(g))<@%s::text[])"""


def command_fingerprint(body, evidence_refs=None):
    return body_hash(body) if evidence_refs is None else body_hash(
        {"body": body, "interpretation": "model_tool", "current_input_refs": evidence_refs})


def revision_cursor(cursor):
    if cursor is None:
        return None
    if not isinstance(cursor, str) or not cursor.startswith("r:") or not cursor[2:].isdigit():
        raise AppError("invalid_memory_history_cursor")
    return int(cursor[2:])


class ClaimService:
    def __init__(self, memory):
        self.memory = memory
        self.database, self.identity, self.sources = memory.database, memory.identity, memory.sources
        self.commands, self.jobs = memory.commands, memory.jobs
        self.revisions = RevisionService(self)

    async def visible_subjects(self, context, conn, subject_ids=None):
        if not context.actor_id:
            return set()
        if subject_ids is not None and hasattr(self.identity, "resolve_memory_subjects_on"):
            rows = await self.identity.resolve_memory_subjects_on(conn, context, subject_ids)
        else:
            rows = await self.identity.memory_subjects_on(conn, context)
        return {row["person_id"] for row in rows if row.get("person_id")} | {"actor:" + context.actor_id}

    async def put_on(self, conn, context, body, *, automatic=False):
        """Explicit callers and finite legacy results reuse the same revision primitive."""
        refs = codec.refs(body.get("source_refs", ()))
        if not refs or not context.actor_id:
            raise AppError("claim_original_sources_required")
        support = await self.revisions.support_on(conn, context, body.get("support_groups") or (refs,), body.get("derivation_refs", ()))
        values = await self.revisions._claim_values_on(conn, context, body, support)
        legacy_semantic = {key: values[key] for key in (
            "actor_id", "subject_id", "predicate", "value", "conditions", "polarity",
            "speaker_id", "assertion", "valid_from", "valid_until")}
        claim_id = body.get("claim_id") or "claim_" + body_hash(legacy_semantic)[:48]
        # New proposals use frozen CAS through apply_operations_on, never this legacy head lookup.
        head = await (await conn.execute("SELECT c.*,r.* FROM memory.claims c JOIN memory.claim_revisions r "
            "ON r.claim_id=c.claim_id AND r.revision=c.current_revision WHERE c.claim_id=%s FOR UPDATE OF c", (claim_id,))).fetchone()
        expected = body.get("expected_revision", head["revision"] if automatic and head else 0)
        if head and automatic:
            if any(wire(head.get(key)) != wire(values.get(key)) for key in CLAIM_FIELDS if key != "observed_at"):
                raise AppError("claim_identity_conflict", status=409)
            if expected != head["revision"]:
                raise AppError("revision_conflict", status=409)
            return await self.revisions.reinforce_on(conn, context, head, support, reason=body.get("change_reason", ""))
        return await self.revisions.write_claim_on(conn, context, values, support=support, claim_id=claim_id,
            expected_revision=expected, change_kind="correct" if head else "create", reason=body.get("change_reason", ""))

    async def selected_groups(self, conn, context, rows):
        return await select_supports_on(self.sources, conn, context, rows)

    async def _claim_rows_on(self, conn, context, *, claim_id=None, subject_id=None, cursor=None,
                             limit=50, time_mode="current", known_at=None):
        if time_mode == "known_at":
            version = "JOIN LATERAL (SELECT * FROM memory.claim_revisions v WHERE v.claim_id=c.claim_id AND v.recorded_at<=%s ORDER BY v.revision DESC LIMIT 1) r ON true "
            arguments = [known_at]
        else:
            version = "JOIN memory.claim_revisions r ON r.claim_id=c.claim_id AND r.revision=c.current_revision "
            arguments = []
        return await (await conn.execute("SELECT r.*,c.actor_id,c.current_revision FROM memory.claims c " + version +
            "WHERE c.actor_id=%s AND " + ELIGIBLE_SCOPES +
            " AND (%s::text IS NULL OR c.claim_id=%s) AND (%s::text IS NULL OR r.subject_id=%s) "
            "AND (%s::text IS NULL OR c.claim_id>%s) ORDER BY c.claim_id LIMIT %s",
            (*arguments, context.actor_id, list(context.scope_ids), claim_id, claim_id,
             subject_id, subject_id, cursor, cursor, limit))).fetchall()

    async def _claim_views_on(self, conn, context, rows, *, historical=False, known_at=None):
        selected = await self.selected_groups(conn, context, rows)
        refs = [RecordRef("hypothesis" if row["memory_kind"] == "hypothesis" else "claim", row["claim_id"], row["revision"])
                for row, sources in zip(rows, selected) if sources is not None and row["state"] == "active"]
        links = await self.revisions.links.for_records_on(conn, context, refs, historical=historical, known_at=known_at)
        return tuple(codec.claim({**row, "scope_ids": sorted(set(row["scope_ids"]) & set(context.scope_ids))}, sources,
                                 relations=links.get(row["claim_id"], ()))
                     for row, sources in zip(rows, selected) if sources is not None and row["state"] == "active"), selected, links

    @staticmethod
    def _superseded(object_id, links, moment):
        return any(link.relation_kind == "supersedes" and link.to_ref.id == object_id
                   and (link.valid_from is None or link.valid_from <= moment)
                   and (link.valid_until is None or moment < link.valid_until) for link in links)

    async def list(self, context, *, subject_id=None, cursor=None, limit=50,
                   time_mode="current", valid_at=None, known_at=None):
        await self.identity.check_context(context)
        time_mode, valid_at, known_at = temporal_selection(time_mode=time_mode, valid_at=valid_at, known_at=known_at)
        limit = min(max(int(limit), 1), 200)
        async with self.database.transaction(read_only=True) as conn:
            rows = await self._claim_rows_on(conn, context, subject_id=subject_id, cursor=cursor, limit=limit + 1,
                time_mode=time_mode, known_at=known_at)
            values, selected, links = await self._claim_views_on(conn, context, rows[:limit],
                historical=time_mode != "current", known_at=known_at)
            unknown_recording = False
            if time_mode == "known_at":
                unknown_recording = await (await conn.execute(
                    "SELECT 1 FROM memory.claims c JOIN memory.claim_revisions r ON r.claim_id=c.claim_id "
                    "WHERE c.actor_id=%s AND r.recorded_at IS NULL AND r.state='active' AND " + ELIGIBLE_SCOPES +
                    " AND (%s::text IS NULL OR r.subject_id=%s) AND NOT EXISTS(SELECT 1 FROM memory.claim_revisions known "
                    "WHERE known.claim_id=c.claim_id AND known.recorded_at<=%s) "
                    "AND sources.any_support_usable(r.support_groups,%s::text[]) "
                    "AND sources.any_support_usable(jsonb_build_array(r.derivation_refs),%s::text[]) LIMIT 1",
                    (context.actor_id, list(context.scope_ids), subject_id, subject_id, known_at,
                     list(context.scope_ids), list(context.scope_ids)))).fetchone() is not None
        moment = valid_at or known_at or datetime.now(UTC)
        items = tuple(value for value in values if valid_for_time(value, time_mode=time_mode, valid_at=moment)
                      and not self._superseded(value.claim_id, links.get(value.claim_id, ()), moment))
        missing = ("unavailable_sources",) if any(refs is None for refs in selected) else ()
        if unknown_recording:
            missing += ("legacy_recorded_time_unknown",)
        return Page(items, rows[limit - 1]["claim_id"] if len(rows) > limit else None,
                    Coverage(len(rows) <= limit and not missing, missing=missing))

    async def get(self, context, claim_id, *, time_mode="current", valid_at=None, known_at=None):
        await self.identity.check_context(context)
        time_mode, valid_at, known_at = temporal_selection(time_mode=time_mode, valid_at=valid_at, known_at=known_at)
        async with self.database.transaction(read_only=True) as conn:
            rows = await self._claim_rows_on(conn, context, claim_id=claim_id, limit=1, time_mode=time_mode, known_at=known_at)
            values, _, _ = await self._claim_views_on(conn, context, rows, historical=time_mode != "current", known_at=known_at)
        if not values or time_mode != "current" and not valid_for_time(values[0], time_mode=time_mode, valid_at=valid_at or known_at):
            raise AppError("claim_unavailable", status=404)
        return values[0]

    async def correct(self, context, body, *, evidence_refs=None):
        old = await self.get(context, body["claim_id"])
        if evidence_refs is None:
            command_refs = await self.sources.record_event(context, producer="web.claim.correct",
                event_id=body["client_request_id"], revision=1, occurred_at=datetime.now(UTC),
                parts=({"text": json.dumps({"claim_id": old.claim_id, "value": body.get("value"),
                    "polarity": body.get("polarity", old.polarity), "conditions": body.get("conditions", old.conditions)}, ensure_ascii=False)},),
                origin_kind=OriginKind.HUMAN)
        else:
            command_refs = tuple(evidence_refs)
            if not command_refs:
                raise AppError("memory_current_input_required", status=422)

        async def apply(conn):
            await self.sources.revoke_projection(conn, context, f"{old.claim_id}:{old.revision}",
                                                  source_refs=(*old.source_refs, *command_refs))
            support = await self.revisions.support_on(conn, context, (command_refs,), old.source_refs)
            authors = support["descriptions"]
            speakers = {item["speaker_id"] for item in authors if item["speaker_id"]}
            speaker = next(iter(speakers)) if len(speakers) == 1 else None
            payload = {**wire(old), **body, "speaker_id": speaker,
                       "assertion": "inferred" if evidence_refs is not None or old.memory_kind == "hypothesis" else "self_confirmed" if speaker == old.subject_id
                       and all(item["origin_kind"] == "human" and item["speaker_id"] == speaker for item in authors) else "reported",
                       "observed_at": datetime.now(UTC)}
            effects = []
            updated = await self.revisions.write_claim_on(conn, context, payload, support=support, claim_id=old.claim_id,
                expected_revision=body["expected_revision"], change_kind="correct", reason=body.get("reason", "用户纠正"),
                scope_fork_id=stable_id(context.actor_id, body["client_request_id"], old.claim_id + ":scope", "claim"), effects=effects)
            await self.memory.domain_changed_on(conn, context,
                ({"kind": "hypothesis" if updated.memory_kind == "hypothesis" else "claim",
                  "id": updated.claim_id, "revision": updated.revision},), source_refs=command_refs,
                affected_subject_ids=tuple({updated.subject_id, *(row["subject_id"] for row in effects if row["subject_id"])}))
            return CommandResult(body["client_request_id"], data={"claim_id": updated.claim_id}, revision=updated.revision)

        async def replay(conn, original):
            await self.identity.check_context(context, connection=conn)
            return original
        result = await self.commands.execute(context, "memory.claim.correct", body["client_request_id"],
            command_fingerprint(body, evidence_refs), apply, on_replay=replay)
        target = result.data["claim_id"] if isinstance(result.data, dict) else result.data.claim_id
        return replace(result, data=await self.get(context, target))

    async def share_ordinary(self, context, body):
        original = await self.get(context, body["claim_id"])
        if original.predicate not in {"interest", "hobby", "preference.ordinary", "public_interest"}:
            raise AppError("claim_not_ordinary_interest", status=422)
        if original.assertion != "self_confirmed" or original.conditions or not isinstance(original.value, (str, list)):
            raise AppError("claim_sharing_requires_explicit_ordinary_unit", status=422)
        if original.memory_kind != "fact" or original.current_status != "current":
            raise AppError("claim_sharing_requires_current_fact", status=422)
        if len(json.dumps(original.value, ensure_ascii=False)) > 1000:
            raise AppError("claim_sharing_unit_too_large")
        if body.get("expected_revision") != original.revision:
            raise AppError("revision_conflict", status=409)
        shared_id = f"shared_{original.claim_id}_{original.revision}"

        async def apply(conn):
            source = await self.sources.publish_projection(context, projection_id=f"{original.claim_id}:{original.revision}",
                content=f"{original.subject_id} {original.predicate}: {json.dumps(original.value, ensure_ascii=False)}",
                source_refs=original.source_refs, connection=conn)
            shared = await self.put_on(conn, context, {**wire(original), "claim_id": shared_id,
                "source_refs": (source,), "support_groups": ((source,),), "derivation_refs": (),
                "expected_revision": 0, "assertion": "observed", "speaker_id": None})
            return CommandResult(body["client_request_id"], data={"claim_id": shared.claim_id}, revision=shared.revision)

        async def replay(conn, original):
            await self.identity.check_context(context, connection=conn)
            return original
        result = await self.commands.execute(context, "memory.claim.share", body["client_request_id"], body_hash(body), apply, on_replay=replay)
        return replace(result, data=await self.get(context, shared_id))

    async def matters(self, context, *, subject_id=None, cursor=None, limit=50,
                      time_mode="current", valid_at=None, known_at=None):
        await self.identity.check_context(context)
        time_mode, valid_at, known_at = temporal_selection(time_mode=time_mode, valid_at=valid_at, known_at=known_at)
        limit = min(max(int(limit), 1), 200)
        async with self.database.transaction(read_only=True) as conn:
            if time_mode == "known_at":
                selection = "JOIN LATERAL(SELECT * FROM memory.matter_revisions v WHERE v.matter_id=m.matter_id AND v.recorded_at<=%s ORDER BY revision DESC LIMIT 1) r ON true "
                arguments = [known_at]
            elif time_mode == "valid_at":
                selection = "JOIN LATERAL(SELECT * FROM memory.matter_revisions v WHERE v.matter_id=m.matter_id AND v.observed_at<=%s ORDER BY observed_at DESC,revision DESC LIMIT 1) r ON true "
                arguments = [valid_at]
            else:
                selection = "JOIN memory.matter_revisions r ON r.matter_id=m.matter_id AND r.revision=m.revision "
                arguments = []
            rows = await (await conn.execute("SELECT r.* FROM memory.matters m " + selection +
                "WHERE m.actor_id=%s AND r.active AND " + ELIGIBLE_SCOPES + " AND (%s::text IS NULL OR r.subject_id=%s) "
                "AND (%s::text IS NULL OR r.matter_id>%s) ORDER BY r.matter_id LIMIT %s",
                (*arguments, context.actor_id, list(context.scope_ids), subject_id, subject_id, cursor, cursor, limit + 1))).fetchall()
            selected = await select_supports_on(self.sources, conn, context, rows[:limit])
            links = await self.revisions.links.for_records_on(conn, context,
                [RecordRef("matter", row["matter_id"], row["revision"]) for row, refs in zip(rows[:limit], selected) if refs],
                historical=time_mode != "current", known_at=known_at)
            values = []
            for row, refs in zip(rows[:limit], selected):
                if refs is None:
                    continue
                related = links.get(row["matter_id"], ())
                if self._superseded(row["matter_id"], related, valid_at or known_at or datetime.now(UTC)):
                    continue
                claim_ids = [link.to_ref.id if link.from_ref.kind == "matter" else link.from_ref.id
                             for link in related if link.relation_kind == "about_matter"]
                values.append(codec.matter({**row, "scope_ids": sorted(set(row["scope_ids"]) & set(context.scope_ids))}, claim_ids, refs, relations=related))
            unknown_recording = False
            if time_mode == "known_at":
                unknown_recording = await (await conn.execute(
                    "SELECT 1 FROM memory.matter_revisions r WHERE r.actor_id=%s AND r.recorded_at IS NULL "
                    "AND r.active AND " + ELIGIBLE_SCOPES +
                    " AND (%s::text IS NULL OR r.subject_id=%s) AND NOT EXISTS(SELECT 1 FROM memory.matter_revisions known "
                    "WHERE known.matter_id=r.matter_id AND known.recorded_at<=%s) "
                    "AND sources.any_support_usable(r.support_groups,%s::text[]) "
                    "AND sources.any_support_usable(jsonb_build_array(r.derivation_refs),%s::text[]) LIMIT 1",
                    (context.actor_id, list(context.scope_ids), subject_id, subject_id, known_at,
                     list(context.scope_ids), list(context.scope_ids)))).fetchone() is not None
        missing = ("unavailable_sources",) if any(refs is None for refs in selected) else ()
        if time_mode == "valid_at":
            missing += ("matter_effective_time_inferred_from_report",)
        if unknown_recording:
            missing += ("legacy_recorded_time_unknown",)
        return Page(tuple(values), rows[limit - 1]["matter_id"] if len(rows) > limit else None,
                    Coverage(len(rows) <= limit and not missing, missing=missing))

    async def matter_on(self, conn, context, body, *, automatic=False):
        refs = codec.refs(body.get("source_refs", ()))
        support = await self.revisions.support_on(conn, context, body.get("support_groups") or (refs,), body.get("derivation_refs", ()))
        return await self.revisions.write_matter_on(conn, context, body, support=support,
            matter_id=body["matter_id"], expected_revision=body.get("expected_revision", 0),
            change_kind=body.get("change_kind", "transition" if body.get("expected_revision", 0) else "create"),
            reason=body.get("change_reason", ""))

    async def get_matter(self, context, matter_id, *, revision=None):
        await self.identity.check_context(context)
        async with self.database.transaction(read_only=True) as conn:
            row = await (await conn.execute("SELECT r.* FROM memory.matter_revisions r JOIN memory.matters m USING(matter_id) "
                "WHERE r.matter_id=%s AND r.revision=COALESCE(%s,m.revision) AND r.actor_id=%s AND r.active",
                (matter_id, revision, context.actor_id))).fetchone()
            selected = (await select_supports_on(self.sources, conn, context, (row,)))[0] if row else None
            if selected is None:
                raise AppError("matter_unavailable", status=404)
            links = await self.revisions.links.for_records_on(conn, context, (RecordRef("matter", matter_id, row["revision"]),), historical=revision is not None)
            claim_ids = [link.to_ref.id if link.from_ref.kind == "matter" else link.from_ref.id
                         for link in links.get(matter_id, ()) if link.relation_kind == "about_matter"]
            return codec.matter({**row, "scope_ids": sorted(set(row["scope_ids"]) & set(context.scope_ids))}, claim_ids, selected, relations=links.get(matter_id, ()))

    async def save_matter(self, context, body, *, evidence_refs=None):
        refs = tuple(evidence_refs) if evidence_refs is not None else codec.refs(body.get("source_refs", ()))
        if evidence_refs is not None and not refs:
            raise AppError("memory_current_input_required", status=422)
        if not refs:
            refs = await self.sources.record_event(context, producer="web.matter", event_id=body["client_request_id"],
                revision=1, occurred_at=datetime.now(UTC),
                parts=({"text": json.dumps({key: body.get(key) for key in ("title", "state", "due_at", "summary", "conditions")}, ensure_ascii=False)},),
                origin_kind=context.origin_kind)

        async def apply(conn):
            support = await self.revisions.support_on(conn, context, (refs,))
            effects = []
            result = await self.revisions.write_matter_on(conn, context, {**body, "interpretation": evidence_refs is not None},
                support=support, matter_id=body["matter_id"], expected_revision=body.get("expected_revision", 0),
                change_kind="transition" if body.get("expected_revision", 0) else "create", reason=body.get("reason", "用户更新事项"),
                scope_fork_id=stable_id(context.actor_id, body["client_request_id"], body["matter_id"] + ":scope", "matter"), effects=effects)
            await self.memory.domain_changed_on(conn, context,
                ({"kind": "matter", "id": result.matter_id, "revision": result.revision},), source_refs=refs,
                affected_subject_ids=tuple({result.subject_id, *(row["subject_id"] for row in effects if row["subject_id"])} - {None}))
            return CommandResult(body["client_request_id"], data={"matter_id": result.matter_id}, revision=result.revision)

        async def replay(conn, original):
            await self.identity.check_context(context, connection=conn)
            return original
        result = await self.commands.execute(context, "memory.matter.save", body["client_request_id"],
            command_fingerprint(body, evidence_refs), apply, on_replay=replay)
        return replace(result, data=await self.get_matter(context, result.data["matter_id"]))

    async def history(self, context, claim_id, *, cursor=None, limit=50):
        return await self._history(context, "claim", claim_id, cursor=cursor, limit=limit)

    async def matter_history(self, context, matter_id, *, cursor=None, limit=50):
        return await self._history(context, "matter", matter_id, cursor=cursor, limit=limit)

    async def _history(self, context, kind, object_id, *, cursor=None, limit=50):
        await self.identity.check_context(context)
        limit, before = min(max(int(limit), 1), 200), revision_cursor(cursor)
        async with self.database.transaction(read_only=True) as conn:
            if kind == "claim":
                query = "SELECT r.*,c.actor_id,c.current_revision FROM memory.claim_revisions r JOIN memory.claims c USING(claim_id) WHERE c.actor_id=%s AND r.claim_id=%s "
            else:
                query = "SELECT r.*,m.revision AS current_revision FROM memory.matter_revisions r JOIN memory.matters m USING(matter_id) WHERE r.actor_id=%s AND r.matter_id=%s "
            rows = await (await conn.execute(query + "AND " + ELIGIBLE_SCOPES +
                " AND (%s::bigint IS NULL OR r.revision<%s) ORDER BY r.revision DESC LIMIT %s",
                (context.actor_id, object_id, list(context.scope_ids), before, before, limit + 1))).fetchall()
            selected = await select_supports_on(self.sources, conn, context, rows[:limit])
            units = []
            for row, refs in zip(rows[:limit], selected):
                if refs is None or row.get("state") == "unsupported" or row.get("active") is False:
                    continue
                reference_kind = "hypothesis" if kind == "claim" and row["memory_kind"] == "hypothesis" else kind
                links = await self.revisions.links.for_records_on(conn, context, (RecordRef(reference_kind, object_id, row["revision"]),), historical=True)
                related = links.get(object_id, ())
                public_row = {**row, "scope_ids": sorted(set(row["scope_ids"]) & set(context.scope_ids))}
                value = codec.claim(public_row, refs, relations=related) if kind == "claim" else codec.matter(public_row, selected_refs=refs)
                if row["revision"] != row["current_revision"]:
                    value = replace(value, current_status="history_only")
                units.append(build_evidence_unit(value, relations=related, time_mode="history"))
        missing = ("unavailable_sources",) if any(refs is None for refs in selected) else ()
        return Page(tuple(units), "r:" + str(rows[limit - 1]["revision"]) if len(rows) > limit else None,
                    Coverage(len(rows) <= limit and not missing, missing=missing))

    async def source_changed(self, conn, sources, kind, cursor=None):
        return await self.revisions.source_changed_on(conn, sources, kind)

    async def purge_source(self, sources, kind, cursor=None):
        async with self.database.transaction() as conn:
            return await self.source_changed(conn, codec.refs(sources), kind, cursor)
