"""Source-backed versioned relationships and their rebuildable current indexes."""

from datetime import UTC, datetime

from psycopg.types.json import Jsonb

from tianshu.runtime.errors import AppError
from tianshu.runtime.serialization import wire
from . import codec
from .domain_utils import optional_time
from .evidence import build_evidence_unit, select_supports_on
from .types import RecordRef


RELATION_KINDS = {"about_matter", "same_event", "supersedes", "contradicts", "supports", "counterexample"}
IDENTITY_RELATIONS = {"about_matter", "same_event"}


def record_ref(value):
    return value if isinstance(value, RecordRef) else RecordRef(value["kind"], value["id"], value["revision"])


class SemanticLinks:
    def __init__(self, memory):
        self.memory, self.sources = memory, memory.sources

    async def endpoint_on(self, conn, context, value):
        ref = record_ref(value)
        if ref.kind in {"claim", "hypothesis"}:
            row = await (await conn.execute(
                "SELECT r.*,c.actor_id,c.current_revision FROM memory.claim_revisions r "
                "JOIN memory.claims c USING(claim_id) WHERE r.claim_id=%s AND r.revision=%s AND c.actor_id=%s",
                (ref.id, ref.revision, context.actor_id),
            )).fetchone()
            if row and row["state"] != "active":
                return None
        elif ref.kind == "matter":
            row = await (await conn.execute(
                "SELECT r.*,m.revision AS current_revision FROM memory.matter_revisions r "
                "JOIN memory.matters m USING(matter_id) WHERE r.matter_id=%s AND r.revision=%s AND r.actor_id=%s AND r.active",
                (ref.id, ref.revision, context.actor_id),
            )).fetchone()
        elif ref.kind == "episode":
            row = await (await conn.execute(
                "SELECT *,revision AS current_revision FROM memory.episodes WHERE episode_id=%s "
                "AND revision=%s AND actor_id=%s AND active",
                (ref.id, ref.revision, context.actor_id),
            )).fetchone()
        else:
            raise AppError("invalid_memory_record_kind")
        if row and ref.kind == "episode":
            selected = (*codec.refs(row["source_refs"]), *codec.refs(row.get("supplement_refs")))
            if not (await self.sources.check_many_on(conn, context, (selected,)))[0]:
                selected = None
        else:
            selected = (await select_supports_on(self.sources, conn, context, (row,)))[0] if row else None
        if selected is None:
            return None
        return {**row, "_selected_refs": selected}

    @staticmethod
    def endpoint_evidence(context, endpoint, row):
        public = {**row, "scope_ids": sorted(set(row["scope_ids"]) & set(context.scope_ids))}
        refs = row["_selected_refs"]
        if endpoint.kind in {"claim", "hypothesis"}:
            value = codec.claim(public, refs)
        elif endpoint.kind == "matter":
            value = codec.matter(public, selected_refs=refs)
        else:
            public["source_refs"] = wire(refs)
            value = codec.episode(public)
        # Endpoint snapshots carry no nested relations: both sides stay one bounded unit.
        return wire(build_evidence_unit(value))

    async def add_on(self, conn, context, *, link_id, from_ref, to_ref, relation_kind,
                     support, valid_from=None, valid_until=None):
        from_ref, to_ref = record_ref(from_ref), record_ref(to_ref)
        if relation_kind not in RELATION_KINDS or from_ref == to_ref:
            raise AppError("invalid_memory_semantic_link")
        valid_from, valid_until = optional_time(valid_from), optional_time(valid_until)
        if valid_from and valid_until and valid_until <= valid_from:
            raise AppError("invalid_memory_link_validity")
        if relation_kind == "about_matter" and not (
            {from_ref.kind, to_ref.kind} & {"claim", "hypothesis"} and "matter" in {from_ref.kind, to_ref.kind}
        ):
            raise AppError("memory_about_matter_endpoint_invalid")
        endpoints = [await self.endpoint_on(conn, context, endpoint) for endpoint in (from_ref, to_ref)]
        if not all(endpoints):
            raise AppError("memory_link_endpoint_unavailable", status=409)
        if relation_kind == "supersedes":
            await self._check_cycle_on(conn, context.actor_id, from_ref, to_ref)
        await conn.execute(
            "INSERT INTO memory.semantic_links(link_id,actor_id,principal_id,from_kind,from_id,from_revision,"
            "to_kind,to_id,to_revision,relation_kind,valid_from,valid_until,recorded_at,support_groups,scope_groups,"
            "derivation_refs,source_refs,scope_ids,current_status) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT(link_id) DO NOTHING",
            (link_id, context.actor_id, context.principal_id, from_ref.kind, from_ref.id, from_ref.revision,
             to_ref.kind, to_ref.id, to_ref.revision, relation_kind, valid_from, valid_until, datetime.now(UTC),
             Jsonb(wire(support["groups"])), Jsonb(support["scope_groups"]),
             Jsonb(wire(support["derivation_refs"])), Jsonb(wire(support["source_refs"])), support["scope_ids"],
             "current" if await self._current_compatible_on(conn, context, (from_ref, to_ref), endpoints, relation_kind) else "history_only"),
        )
        if relation_kind == "about_matter":
            claim = from_ref if from_ref.kind in {"claim", "hypothesis"} else to_ref
            await self.rebuild_matter_index_on(conn, context.actor_id, claim.id)
        return link_id

    async def _current_compatible_on(self, conn, context, endpoints, stored, relation_kind, cache=None):
        cache = {} if cache is None else cache
        for index, (endpoint, old) in enumerate(zip(endpoints, stored)):
            # A superseded proposition intentionally remains the historical endpoint.
            if relation_kind == "supersedes" and index == 1:
                continue
            current_ref = RecordRef(endpoint.kind, endpoint.id, old["current_revision"])
            key = (current_ref.kind, current_ref.id, current_ref.revision)
            if key not in cache:
                cache[key] = old if endpoint.revision == current_ref.revision else await self.endpoint_on(conn, context, current_ref)
            current = cache[key]
            if current is None:
                return False
            if current.get("current_status", "current") != "current" and not (
                relation_kind == "supersedes" and index == 0 and current.get("current_status") == "retracted"):
                return False
            if endpoint.revision != current_ref.revision and relation_kind not in IDENTITY_RELATIONS:
                if not old.get("semantic_fingerprint") or old["semantic_fingerprint"] != current.get("semantic_fingerprint"):
                    return False
        return True

    async def _check_cycle_on(self, conn, actor_id, origin, target):
        if (origin.kind, origin.id) == (target.kind, target.id):
            raise AppError("memory_supersedes_cycle", status=409)
        rows = await (await conn.execute(
            "WITH RECURSIVE reachable(kind,id,depth,path) AS ("
            "SELECT %s::text,%s::text,0,ARRAY[%s::text] UNION ALL "
            "SELECT l.to_kind,l.to_id,r.depth+1,r.path||(l.to_kind||':'||l.to_id) "
            "FROM reachable r JOIN memory.semantic_links l ON l.from_kind=r.kind AND l.from_id=r.id "
            "WHERE l.actor_id=%s AND l.relation_kind='supersedes' AND l.state='active' "
            "AND l.current_status='current' AND r.depth<128 AND NOT (l.to_kind||':'||l.to_id)=ANY(r.path)) "
            "SELECT kind,id,depth FROM reachable LIMIT 513",
            (target.kind, target.id, target.kind + ":" + target.id, actor_id),
        )).fetchall()
        if any(row["kind"] == origin.kind and row["id"] == origin.id for row in rows):
            raise AppError("memory_supersedes_cycle", status=409)
        if len(rows) > 512 or any(row["depth"] >= 128 for row in rows):
            raise AppError("memory_semantic_cycle_budget_exceeded", status=409)

    async def head_changed_on(self, conn, *, actor_id, kind, object_id, old_revision, semantic_changed, scope_groups=()):
        affected = []
        if semantic_changed:
            # A dependency's changed meaning retires the derived *current* interpretation.
            # Different permission domains cannot retire a public head through a private link.
            affected = await (await conn.execute(
                "WITH RECURSIVE dependent(claim_id) AS ("
                "SELECT l.to_id FROM memory.semantic_links l "
                "JOIN memory.claims c ON c.claim_id=l.to_id JOIN memory.claim_revisions r "
                "ON r.claim_id=c.claim_id AND r.revision=c.current_revision "
                "WHERE l.actor_id=%s AND l.state='active' AND l.current_status='current' "
                "AND l.relation_kind IN ('supports','counterexample') AND l.from_kind=ANY(%s) "
                "AND l.from_id=%s AND l.from_revision=%s AND l.to_kind='hypothesis' "
                "AND r.current_status='current' AND NOT EXISTS(SELECT value FROM jsonb_array_elements(r.scope_groups) "
                "EXCEPT SELECT value FROM jsonb_array_elements(%s::jsonb)) "
                "AND NOT EXISTS(SELECT value FROM jsonb_array_elements(%s::jsonb) "
                "EXCEPT SELECT value FROM jsonb_array_elements(r.scope_groups)) "
                "UNION SELECT l.to_id FROM dependent d "
                "JOIN memory.claims parent ON parent.claim_id=d.claim_id "
                "JOIN memory.semantic_links l ON l.from_id=d.claim_id AND l.from_revision=parent.current_revision "
                "JOIN memory.claims c ON c.claim_id=l.to_id JOIN memory.claim_revisions r "
                "ON r.claim_id=c.claim_id AND r.revision=c.current_revision "
                "WHERE l.actor_id=%s AND l.state='active' AND l.current_status='current' "
                "AND l.from_kind IN ('claim','hypothesis') AND l.relation_kind IN ('supports','counterexample') "
                "AND l.to_kind='hypothesis' AND r.current_status='current' "
                "AND NOT EXISTS(SELECT value FROM jsonb_array_elements(r.scope_groups) "
                "EXCEPT SELECT value FROM jsonb_array_elements(%s::jsonb)) "
                "AND NOT EXISTS(SELECT value FROM jsonb_array_elements(%s::jsonb) "
                "EXCEPT SELECT value FROM jsonb_array_elements(r.scope_groups))), "
                "limited AS MATERIALIZED(SELECT claim_id FROM dependent LIMIT 513) "
                "SELECT d.claim_id,c.current_revision AS revision,r.subject_id FROM limited d "
                "JOIN memory.claims c ON c.claim_id=d.claim_id JOIN memory.claim_revisions r "
                "ON r.claim_id=c.claim_id AND r.revision=c.current_revision",
                (actor_id, [kind, "hypothesis"] if kind == "claim" else [kind], object_id, old_revision,
                 Jsonb(scope_groups), Jsonb(scope_groups), actor_id, Jsonb(scope_groups), Jsonb(scope_groups)),
            )).fetchall()
            if len(affected) > 512:
                raise AppError("memory_dependency_budget_exceeded", status=409)
            if affected:
                ids = sorted({row["claim_id"] for row in affected})
                await conn.execute("SELECT claim_id FROM memory.claims WHERE claim_id=ANY(%s) ORDER BY claim_id FOR UPDATE", (ids,))
                await conn.execute(
                    "UPDATE memory.claim_revisions r SET current_status='history_only' FROM memory.claims c "
                    "WHERE c.claim_id=r.claim_id AND c.current_revision=r.revision AND r.memory_kind='hypothesis' AND c.claim_id=ANY(%s)", (ids,))
                await conn.execute(
                    "UPDATE memory.semantic_links SET current_status='history_only' WHERE actor_id=%s AND state='active' "
                    "AND relation_kind NOT IN ('about_matter','same_event') AND ("
                    "(from_kind IN ('claim','hypothesis') AND from_id=ANY(%s)) OR "
                    "(to_kind IN ('claim','hypothesis') AND to_id=ANY(%s)))", (actor_id, ids, ids))
                for claim_id in ids:
                    await self.rebuild_matter_index_on(conn, actor_id, claim_id)
            await conn.execute(
                "UPDATE memory.semantic_links SET current_status='history_only' WHERE actor_id=%s AND state='active' "
                "AND relation_kind NOT IN ('about_matter','same_event') AND "
                "((from_kind=ANY(%s) AND from_id=%s AND from_revision=%s) OR "
                "(to_kind=ANY(%s) AND to_id=%s AND to_revision=%s))",
                (actor_id, [kind, "hypothesis"] if kind == "claim" else [kind], object_id, old_revision,
                 [kind, "hypothesis"] if kind == "claim" else [kind], object_id, old_revision),
            )
        if kind == "claim":
            await self.rebuild_matter_index_on(conn, actor_id, object_id)
        return tuple({row["claim_id"]: {"kind": "hypothesis", "id": row["claim_id"],
            "revision": row["revision"], "subject_id": row["subject_id"]} for row in affected}.values())

    async def rebuild_matter_index_on(self, conn, actor_id, claim_id=None):
        # This is a cache only; every business read still checks the semantic link's own authority.
        await conn.execute(
            "DELETE FROM memory.matter_claims mc USING memory.claims c WHERE c.claim_id=mc.claim_id AND c.actor_id=%s "
            "AND (%s::text IS NULL OR c.claim_id=%s)", (actor_id, claim_id, claim_id),
        )
        await conn.execute(
            "INSERT INTO memory.matter_claims(matter_id,claim_id) SELECT DISTINCT "
            "CASE WHEN l.from_kind='matter' THEN l.from_id ELSE l.to_id END,c.claim_id "
            "FROM memory.semantic_links l JOIN memory.claims c ON c.claim_id=CASE WHEN l.from_kind='matter' THEN l.to_id ELSE l.from_id END "
            "JOIN memory.claim_revisions r ON r.claim_id=c.claim_id AND r.revision=c.current_revision "
            "JOIN memory.matters m ON m.matter_id=CASE WHEN l.from_kind='matter' THEN l.from_id ELSE l.to_id END "
            "WHERE l.actor_id=%s AND l.relation_kind='about_matter' AND l.state='active' AND l.current_status='current' "
            "AND r.state='active' AND r.current_status='current' AND m.active AND (%s::text IS NULL OR c.claim_id=%s) "
            "ON CONFLICT DO NOTHING", (actor_id, claim_id, claim_id),
        )
        await conn.execute(
            "UPDATE memory.claim_revisions r SET matter_id=(SELECT min(mc.matter_id) FROM memory.matter_claims mc WHERE mc.claim_id=r.claim_id) "
            "FROM memory.claims c WHERE c.claim_id=r.claim_id AND c.current_revision=r.revision AND c.actor_id=%s "
            "AND (%s::text IS NULL OR c.claim_id=%s)", (actor_id, claim_id, claim_id),
        )

    async def for_records_on(self, conn, context, records, *, historical=False, known_at=None):
        records = tuple(record_ref(ref) for ref in records)
        if not records:
            return {}
        if len(records) > 200:
            raise AppError("memory_links_batch_too_large")
        rows = await (await conn.execute(
            "SELECT * FROM memory.semantic_links WHERE actor_id=%s AND state='active' "
            "AND EXISTS(SELECT 1 FROM jsonb_array_elements(scope_groups) g "
            "WHERE ARRAY(SELECT jsonb_array_elements_text(g))<@%s::text[]) "
            "AND (%s OR current_status='current') "
            "AND (%s::timestamptz IS NULL OR recorded_at<=%s) AND ("
            "(from_kind,from_id) IN (SELECT * FROM unnest(%s::text[],%s::text[])) OR "
            "(to_kind,to_id) IN (SELECT * FROM unnest(%s::text[],%s::text[]))) ORDER BY link_id LIMIT 1601",
            (context.actor_id, list(context.scope_ids), historical, known_at, known_at,
             [ref.kind for ref in records], [ref.id for ref in records],
             [ref.kind for ref in records], [ref.id for ref in records]),
        )).fetchall()
        if len(rows) > 1600:
            raise AppError("memory_links_budget_exceeded", status=503)
        selected = await select_supports_on(self.sources, conn, context, rows)
        result = {ref.id: [] for ref in records}
        cache = {}
        for row, refs in zip(rows, selected):
            if refs is None:
                continue
            endpoints = [RecordRef(row[prefix + "kind"], row[prefix + "id"], row[prefix + "revision"])
                         for prefix in ("from_", "to_")]
            checked = []
            for endpoint in endpoints:
                key = (endpoint.kind, endpoint.id, endpoint.revision)
                if key not in cache:
                    cache[key] = await self.endpoint_on(conn, context, endpoint)
                checked.append(cache[key])
            if not all(checked):
                continue
            if not historical and not await self._current_compatible_on(conn, context, endpoints, checked, row["relation_kind"], cache):
                continue
            link = codec.semantic_link({**row, "scope_ids": sorted(set(row["scope_ids"]) & set(context.scope_ids)),
                "from_evidence": self.endpoint_evidence(context, endpoints[0], checked[0]),
                "to_evidence": self.endpoint_evidence(context, endpoints[1], checked[1])}, refs)
            for record in records:
                for endpoint, stored in zip(endpoints, checked):
                    if (record.kind, record.id) != (endpoint.kind, endpoint.id):
                        continue
                    if record.revision != endpoint.revision and row["relation_kind"] not in IDENTITY_RELATIONS:
                        key = (record.kind, record.id, record.revision)
                        if key not in cache:
                            cache[key] = await self.endpoint_on(conn, context, record)
                        current = cache[key]
                        if not current or not current.get("semantic_fingerprint") or current["semantic_fingerprint"] != stored.get("semantic_fingerprint"):
                            continue
                    result[record.id].append(link)
        return {key: tuple({link.link_id: link for link in links}.values()) for key, links in result.items()}
