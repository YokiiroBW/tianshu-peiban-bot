"""Authorized SQL candidates over the existing Memory tables and embedding profile."""

from datetime import UTC, datetime
from dataclasses import replace

from pgvector import Vector
from pgvector.psycopg import register_vector_async

from .domain_utils import lexical


def families(kinds):
    return tuple(name for name in ("claim", "matter", "episode", "reflection")
                 if name in kinds or name == "claim" and "hypothesis" in kinds)


def query_terms(query):
    tokens = lexical(query).split()[:128]
    tsquery = " | ".join("'" + token.replace("'", "''") + "'" for token in tokens)
    fragments = ["%" + token.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
                 for token in tokens if len(token) > 1 or len(token) == 1 and "\u3400" <= token <= "\u9fff"]
    return tsquery, fragments


class CandidateStore:
    def __init__(self, memory, settings=None):
        self.memory, self.database = memory, memory.database
        self.settings = settings or {}

    def selection(self, context, plan, family, *, snapshot=None, include_unknown=False):
        args, clauses = [], []
        knowledge_time = plan.known_at if plan.time_mode == "known_at" else snapshot
        moment = plan.valid_at or plan.known_at or snapshot or datetime.now(UTC)
        if family == "claim":
            table = "memory.claims h JOIN memory.claim_revisions r ON r.claim_id=h.claim_id"
            if knowledge_time is None:
                clauses.append("r.revision=h.current_revision")
            else:
                legacy = " OR r.recorded_at IS NULL" if plan.time_mode != "known_at" or include_unknown else ""
                clauses.append("(r.recorded_at<=%s" + legacy + ") AND NOT EXISTS(SELECT 1 FROM memory.claim_revisions newer "
                    "WHERE newer.claim_id=r.claim_id AND newer.revision>r.revision AND (newer.recorded_at<=%s" + legacy.replace("r.", "newer.") + "))")
                args.extend((knowledge_time, knowledge_time))
            key, subject, actor, head = "r.claim_id", "r.subject_id", "h.actor_id", "h.current_revision"
            kind = "CASE WHEN r.memory_kind='hypothesis' THEN 'hypothesis' ELSE 'claim' END"
            clauses += ["r.state='active'", "r.memory_kind=ANY(%s::text[])"]
            args.append(["hypothesis" if item == "hypothesis" else "fact" for item in plan.evidence_kinds if item in {"claim", "hypothesis"}])
            if plan.time_mode == "current":
                clauses.append("r.current_status='current'")
            else:
                clauses.append("r.current_status<>'retracted'")
            clauses += ["(r.valid_from IS NULL OR r.valid_from<=%s)", "(r.valid_until IS NULL OR r.valid_until>%s)"]
            args.extend((moment, moment))
            content, search, embedding, profile = "r.content", "r.search_vector", "r.embedding", "r.embedding_profile"
            groups, derivations = "r.support_groups", "r.derivation_refs"
        elif family == "matter":
            key, subject, actor, kind = "r.matter_id", "r.subject_id", "r.actor_id", "'matter'::text"
            if knowledge_time is None and plan.time_mode != "valid_at":
                table, head = "memory.matters r", "r.revision"
                content, search, embedding, profile = "(r.title||' '||r.summary||' '||r.conditions::text)", "r.search_vector", "r.embedding", "r.embedding_profile"
            else:
                table, head = "memory.matters h JOIN memory.matter_revisions r ON r.matter_id=h.matter_id", "h.revision"
                if plan.time_mode == "valid_at":
                    # Pick the report that describes that instant, inside the page's
                    # knowledge snapshot. A later reschedule must not hide the old plan.
                    eligible = ""
                    if knowledge_time is not None:
                        eligible = " AND (newer.recorded_at<=%s OR newer.recorded_at IS NULL)"
                        clauses.append("(r.recorded_at<=%s OR r.recorded_at IS NULL)")
                        args.append(knowledge_time)
                    clauses.append("r.observed_at<=%s AND NOT EXISTS(SELECT 1 FROM memory.matter_revisions newer "
                        "WHERE newer.matter_id=r.matter_id AND newer.observed_at<=%s" + eligible +
                        " AND (newer.observed_at,newer.revision)>(r.observed_at,r.revision))")
                    args.extend((moment, moment))
                    if knowledge_time is not None:
                        args.append(knowledge_time)
                elif knowledge_time is not None:
                    legacy = " OR r.recorded_at IS NULL" if plan.time_mode != "known_at" or include_unknown else ""
                    clauses.append("(r.recorded_at<=%s" + legacy + ") AND NOT EXISTS(SELECT 1 FROM memory.matter_revisions newer "
                        "WHERE newer.matter_id=r.matter_id AND newer.revision>r.revision AND (newer.recorded_at<=%s" + legacy.replace("r.", "newer.") + "))")
                    args.extend((knowledge_time, knowledge_time))
                content = "(r.title||' '||r.summary||' '||r.conditions::text)"
                search = "to_tsvector('simple'," + content + ")"
                embedding, profile = "CASE WHEN r.revision=h.revision THEN h.embedding END", "CASE WHEN r.revision=h.revision THEN h.embedding_profile END"
            clauses.append("r.active AND r.current_status<>'retracted'")
            groups, derivations = "r.support_groups", "r.derivation_refs"
        else:
            table = "memory." + ("episodes" if family == "episode" else "reflections") + " r"
            key = "r.episode_id" if family == "episode" else "r.proposal_id"
            subject, actor, head, kind = "r.person_id", "r.actor_id", "r.revision", "'" + family + "'::text"
            content = "r.summary" if family == "episode" else "r.content"
            search, embedding, profile = "r.search_vector", "r.embedding", "r.embedding_profile"
            groups, derivations = "jsonb_build_array(r.source_refs)", "'[]'::jsonb"
            clauses.append("r.active")
            if knowledge_time is not None:
                clauses.append("(r.recorded_at<=%s" + (" OR r.recorded_at IS NULL" if plan.time_mode != "known_at" or include_unknown else "") + ")")
                args.append(knowledge_time)
            if plan.time_mode == "valid_at":
                clauses.append("r.occurred_at<=%s")
                args.append(moment)
        clauses.append(actor + "=%s")
        args.append(context.actor_id)
        if family in {"claim", "matter"}:
            clauses.append("EXISTS(SELECT 1 FROM jsonb_array_elements(r.scope_groups) sg WHERE ARRAY(SELECT jsonb_array_elements_text(sg))<@%s::text[])")
        else:
            clauses.append("r.scope_ids<@%s::text[]")
        args.append(list(context.scope_ids))
        clauses.append("sources.any_support_usable(" + groups + ",%s::text[]) AND "
                       "sources.any_support_usable(jsonb_build_array(" + derivations + "),%s::text[])")
        args.extend((list(context.scope_ids), list(context.scope_ids)))
        if family in {"claim", "matter"}:
            explicit = ("r.counterexample_refs" if family == "claim" else
                        "COALESCE(r.completion_basis->'source_refs','[]'::jsonb)")
            clauses.append("sources.any_support_usable(jsonb_build_array(" + explicit + "),%s::text[])")
            args.append(list(context.scope_ids))
        if plan.subject_ids:
            clauses.append(subject + "=ANY(%s::text[])")
            args.append(list(plan.subject_ids))
        if plan.matter_refs:
            if family == "matter":
                clauses.append(key + "=ANY(%s::text[])")
                args.append(list(plan.matter_refs))
            elif family == "episode":
                clauses.append("r.matter_id=ANY(%s::text[])")
                args.append(list(plan.matter_refs))
            elif family == "claim":
                clauses.append("EXISTS(SELECT 1 FROM memory.semantic_links l WHERE l.actor_id=%s AND l.state='active' "
                    "AND l.relation_kind='about_matter' AND ((l.from_kind=" + kind + " AND l.from_id=" + key + " AND l.to_kind='matter' AND l.to_id=ANY(%s::text[])) "
                    "OR (l.to_kind=" + kind + " AND l.to_id=" + key + " AND l.from_kind='matter' AND l.from_id=ANY(%s::text[]))) "
                    "AND EXISTS(SELECT 1 FROM jsonb_array_elements(l.scope_groups) sg WHERE ARRAY(SELECT jsonb_array_elements_text(sg))<@%s::text[]) "
                    "AND sources.any_support_usable(l.support_groups,%s::text[]) "
                    "AND sources.any_support_usable(jsonb_build_array(l.derivation_refs),%s::text[]))")
                args.extend((context.actor_id, list(plan.matter_refs), list(plan.matter_refs),
                             list(context.scope_ids), list(context.scope_ids), list(context.scope_ids)))
            else:
                # Reflections have no authoritative about_matter field or edge.
                clauses.append("false")
        select = (f"r.*,{actor} AS actor_id,{key} AS _id,{kind} AS _kind,{head} AS head_revision,"
                  f"{head} AS current_revision,{content} AS _content,{embedding} AS _embedding,{profile} AS _profile")
        return {"table": table, "where": " AND ".join(clauses), "args": args, "select": select,
                "key": key, "kind": kind, "search": search, "content": content,
                "embedding": embedding, "profile": profile}

    async def lexical(self, context, plan, budget, *, limit=96):
        tsquery, fragments = query_terms(plan.query)
        rows = []
        async with self.database.transaction(budget, read_only=True) as conn:
            for family in families(plan.evidence_kinds):
                parts = self.selection(context, plan, family)
                if tsquery:
                    relevance = "ts_rank_cd(" + parts["search"] + ",to_tsquery('simple',%s))"
                    match = " AND (" + parts["search"] + "@@to_tsquery('simple',%s) OR " + parts["content"] + " ILIKE ANY(%s::text[]))"
                    args = (tsquery, *parts["args"], tsquery, fragments, limit)
                else:
                    relevance, match = "0::real", ""
                    args = (*parts["args"], limit)
                rows.extend(await (await conn.execute("SELECT " + parts["select"] + "," + relevance + " AS relevance "
                    "FROM " + parts["table"] + " WHERE " + parts["where"] + match +
                    " ORDER BY relevance DESC,recorded_at DESC NULLS LAST," + parts["key"] + " LIMIT %s", args)).fetchall())
        return sorted(rows, key=lambda row: (row["relevance"], row.get("recorded_at") or datetime.min.replace(tzinfo=UTC)), reverse=True)[:256]

    async def vectors(self, context, plan, values, fingerprint, budget, *, limit=96):
        rows, bounded = [], False
        async with self.database.transaction(budget, read_only=True) as conn:
            await register_vector_async(conn)
            await conn.execute("SELECT set_config('hnsw.iterative_scan','strict_order',true),"
                "set_config('hnsw.ef_search',%s,true),set_config('hnsw.max_scan_tuples',%s,true)",
                (str(min(max(int(self.settings.get("ef_search", 128)), 96), 1000)),
                 str(min(max(int(self.settings.get("max_scan_tuples", 20000)), 1000), 50000))))
            for family in families(plan.evidence_kinds):
                p = self.selection(context, plan, family)
                profile = " AND " + p["profile"] + "=%s AND " + p["embedding"] + " IS NOT NULL"
                selected = await (await conn.execute("SELECT " + p["select"] + "," + p["embedding"] + "::real[] AS vector,"
                    "1-(" + p["embedding"] + "<=>%s) AS relevance FROM " + p["table"] + " WHERE " + p["where"] + profile +
                    " ORDER BY " + p["embedding"] + "<=>%s LIMIT %s",
                    (Vector(values), *p["args"], fingerprint, Vector(values), limit))).fetchall()
                if len(selected) < limit:
                    # Materializing the authorized set makes this a true exact fallback;
                    # no distance or ranking is evaluated for an unauthorized record.
                    selected = await (await conn.execute("WITH allowed AS MATERIALIZED(SELECT " + p["select"] +
                        " FROM " + p["table"] + " WHERE " + p["where"] + profile + ") "
                        "SELECT allowed.*,_embedding::real[] AS vector,1-(_embedding<=>%s) AS relevance "
                        "FROM allowed ORDER BY _embedding<=>%s LIMIT %s",
                        (*p["args"], fingerprint, Vector(values), Vector(values), limit))).fetchall()
                rows.extend(selected)
        return sorted(rows, key=lambda row: -row["relevance"])[:256], bounded

    async def page(self, context, plan, budget, *, after=(None, None), snapshot=None, limit=50):
        rows = []
        async with self.database.transaction(budget, read_only=True) as conn:
            for family in families(plan.evidence_kinds):
                p = self.selection(context, plan, family, snapshot=snapshot)
                tsquery, fragments = query_terms(plan.query)
                match = " AND (" + p["search"] + "@@to_tsquery('simple',%s) OR " + p["content"] + " ILIKE ANY(%s::text[]))" if tsquery else ""
                text_args = (tsquery, fragments) if tsquery else ()
                rows.extend(await (await conn.execute("SELECT " + p["select"] + " FROM " + p["table"] + " WHERE " + p["where"] +
                    match +
                    " AND (%s::text IS NULL OR (" + p["kind"] + "," + p["key"] + ")>(%s,%s)) "
                    "ORDER BY " + p["kind"] + "," + p["key"] + " LIMIT %s",
                    (*p["args"], *text_args, after[0], after[0], after[1], limit + 1))).fetchall())
        return sorted(rows, key=lambda row: (row["_kind"], row["_id"]))[:limit + 1]

    async def reread(self, conn, context, plan, rows, *, snapshot=None):
        latest = {}
        for family in families(plan.evidence_kinds):
            ids = [row["_id"] for row in rows if row["_kind"] == family or family == "claim" and row["_kind"] == "hypothesis"]
            if not ids:
                continue
            p = self.selection(context, plan, family, snapshot=snapshot)
            checked = await (await conn.execute("SELECT " + p["select"] + " FROM " + p["table"] + " WHERE " + p["where"] +
                " AND " + p["key"] + "=ANY(%s::text[])", (*p["args"], ids))).fetchall()
            latest.update({(row["_kind"], row["_id"], row["revision"]): row for row in checked})
        return [latest.get((row["_kind"], row["_id"], row["revision"])) for row in rows]

    async def coverage_gaps(self, context, plan, budget):
        missing = []
        if plan.time_mode == "known_at":
            async with self.database.transaction(budget, read_only=True) as conn:
                for family in families(plan.evidence_kinds):
                    p = self.selection(context, plan, family, include_unknown=True)
                    if await (await conn.execute("SELECT 1 FROM " + p["table"] + " WHERE " + p["where"] +
                        " AND r.recorded_at IS NULL LIMIT 1", p["args"])).fetchone():
                        missing.append("recorded_at_unknown:" + family)
                    if family in {"episode", "reflection"}:
                        # These existing tables retain the latest revision, not a
                        # fabricated pre-edit snapshot. A known-at answer says so.
                        current = self.selection(context, replace(plan, time_mode="current", known_at=None, valid_at=None), family)
                        if await (await conn.execute("SELECT 1 FROM " + current["table"] + " WHERE " + current["where"] +
                            " AND r.revision>1 AND r.recorded_at>%s LIMIT 1", (*current["args"], plan.known_at))).fetchone():
                            missing.append(family + "_revision_history_unavailable")
        if plan.time_mode == "valid_at" and "matter" in plan.evidence_kinds:
            missing.append("matter_effective_time_inferred_from_report")
        return tuple(missing)
