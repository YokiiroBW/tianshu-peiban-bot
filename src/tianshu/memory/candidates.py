"""Authorized SQL candidates over the existing Memory tables and embedding profile."""

from datetime import UTC, datetime
from dataclasses import dataclass, replace
from uuid import uuid4

from pgvector import Vector
from pgvector.psycopg import register_vector_async
from psycopg.errors import QueryCanceled

from tianshu.runtime.errors import AppError
from .candidate_sources import check_rows_on
from .domain_utils import lexical


EXACT_VECTOR_CANDIDATES = 2000
LEXICAL_BATCH_ROWS = 128


@dataclass(frozen=True)
class CandidateSelection:
    rows: tuple[dict, ...]
    missing: tuple[str, ...]


def source_predicate(groups, derivations, explicit):
    return ("sources.any_support_usable(" + groups + ",%s::text[]) AND "
            "(" + derivations + "='[]'::jsonb OR sources.any_support_usable(jsonb_build_array(" + derivations + "),%s::text[])) AND "
            "(" + explicit + "='[]'::jsonb OR sources.any_support_usable(jsonb_build_array(" + explicit + "),%s::text[]))")


def families(kinds):
    return tuple(name for name in ("claim", "matter", "episode", "reflection")
                 if name in kinds or name == "claim" and "hypothesis" in kinds)


def query_terms(query):
    tokens = lexical(query).split()[:128]
    tsquery = " | ".join("'" + token.replace("'", "''") + "'" for token in tokens)
    fragments = ["%" + token.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
                 for token in tokens if len(token) > 1 or len(token) == 1 and "\u3400" <= token <= "\u9fff"]
    return tsquery, fragments


def _proof_projection(parts):
    relation, args = "NULL::jsonb", ()
    if parts["relation_where"]:
        relation = ("COALESCE((SELECT jsonb_agg(jsonb_build_object('supports',l.support_groups,"
                    "'derivations',l.derivation_refs)) FROM memory.semantic_links l WHERE " +
                    parts["relation_where"] + "),'[]'::jsonb)")
        args = tuple(parts["relation_scoped_args"])
    return (parts["candidate_select"] + "," + parts["groups"] + " AS _supports," +
            parts["derivations"] + " AS _derivations," + parts["explicit"] + " AS _explicit," +
            relation + " AS _relation_proofs"), args


class CandidateStore:
    def __init__(self, memory, settings=None):
        self.memory, self.database = memory, memory.database
        self.settings = settings or {}

    def selection(self, context, plan, family, *, snapshot=None, include_unknown=False):
        args, clauses = [], []
        relation, relation_args = "", []
        relation_where, relation_scoped_args = "", []
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
            # Overlap is only an indexed necessary condition. The exact OR-group
            # test below still permits an independently readable alternative.
            clauses.append("r.scope_ids&&%s::text[]")
            args.append(list(context.scope_ids))
            clauses.append("EXISTS(SELECT 1 FROM jsonb_array_elements(r.scope_groups) sg WHERE ARRAY(SELECT jsonb_array_elements_text(sg))<@%s::text[])")
        else:
            clauses.append("r.scope_ids<@%s::text[]")
        args.append(list(context.scope_ids))
        explicit = ("r.counterexample_refs" if family == "claim" else
                    "COALESCE(r.completion_basis->'source_refs','[]'::jsonb)" if family == "matter" else "'[]'::jsonb")
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
                relation_where = ("l.actor_id=%s AND l.state='active' "
                    "AND l.relation_kind='about_matter' AND ((l.from_kind=" + kind + " AND l.from_id=" + key + " AND l.to_kind='matter' AND l.to_id=ANY(%s::text[])) "
                    "OR (l.to_kind=" + kind + " AND l.to_id=" + key + " AND l.from_kind='matter' AND l.from_id=ANY(%s::text[]))) "
                    "AND EXISTS(SELECT 1 FROM jsonb_array_elements(l.scope_groups) sg WHERE ARRAY(SELECT jsonb_array_elements_text(sg))<@%s::text[])")
                related = "EXISTS(SELECT 1 FROM memory.semantic_links l WHERE " + relation_where
                related_args = relation_scoped_args = [context.actor_id, list(plan.matter_refs), list(plan.matter_refs), list(context.scope_ids)]
                clauses.append(related + ")")
                args.extend(related_args)
                relation = (related + " "
                    "AND sources.any_support_usable(l.support_groups,%s::text[]) "
                    "AND sources.any_support_usable(jsonb_build_array(l.derivation_refs),%s::text[]))")
                relation_args = [*related_args, list(context.scope_ids), list(context.scope_ids)]
            else:
                # Reflections have no authoritative about_matter field or edge.
                clauses.append("false")
        select = (f"r.*,{actor} AS actor_id,{key} AS _id,{kind} AS _kind,{head} AS head_revision,"
                  f"{head} AS current_revision,{content} AS _content,{embedding} AS _embedding,{profile} AS _profile")
        lexical_column = "NULL::text" if family == "matter" and table != "memory.matters r" else "r.lexical"
        candidate_select = (f"{actor} AS actor_id,{subject} AS subject_id,{key} AS _id,{kind} AS _kind,r.revision,"
                            f"{head} AS head_revision,{head} AS current_revision,{content} AS _content,"
                            f"{lexical_column} AS lexical,r.recorded_at")
        scoped = " AND ".join(clauses)
        authority = source_predicate(groups, derivations, explicit) + (" AND " + relation if relation else "")
        viewer = list(context.scope_ids)
        return {"table": table, "where": scoped + " AND " + authority,
                "args": [*args, viewer, viewer, viewer, *relation_args], "select": select,
                "scoped_where": scoped, "scoped_args": args,
                "groups": groups, "derivations": derivations, "explicit": explicit,
                "relation": relation, "relation_args": relation_args,
                "relation_where": relation_where, "relation_scoped_args": relation_scoped_args,
                "candidate_select": candidate_select,
                "key": key, "kind": kind, "search": search, "content": content,
                "embedding": embedding, "profile": profile}

    async def lexical(self, context, plan, budget, *, limit=96):
        tsquery, fragments = query_terms(plan.query)
        rows, missing = [], []
        selected_families = families(plan.evidence_kinds)
        position = 0
        try:
            async with self.database.transaction(budget, read_only=True) as conn:
                await conn.execute("SELECT set_config('jit','off',true)")
                await self.memory.identity.check_context(context, connection=conn)
                for position, family in enumerate(selected_families):
                    try:
                        # A statement cancellation rolls back this family's
                        # cursor, not prior fully source-checked batches.
                        async with conn.transaction():
                            await self._lexical_family_on(conn, context, plan, family, budget,
                                tsquery, fragments, limit, rows, missing)
                    except (TimeoutError, QueryCanceled):
                        missing.append("lexical_candidate_timeout:" + family)
        except (TimeoutError, QueryCanceled):
            missing.extend("lexical_candidate_timeout:" + family for family in selected_families[position:])
        ranked = sorted(rows, key=lambda row: (
            row["relevance"], row.get("recorded_at") or datetime.min.replace(tzinfo=UTC)), reverse=True)[:256]
        return CandidateSelection(tuple(ranked), tuple(dict.fromkeys(missing)))

    async def _lexical_family_on(self, conn, context, plan, family, budget, tsquery, fragments, limit, rows, missing):
        if budget:
            budget.check()
        parts = self.selection(context, plan, family)
        projection, proof_args = _proof_projection(parts)
        if tsquery:
            relevance = "ts_rank_cd(" + parts["search"] + ",to_tsquery('simple',%s))"
            match = " AND (" + parts["search"] + "@@to_tsquery('simple',%s) OR " + parts["content"] + " ILIKE ANY(%s::text[]))"
            text_args, match_args = (tsquery,), (tsquery, fragments)
        else:
            relevance, match, text_args, match_args = "0::real", "", (), ()
        query = ("SELECT " + projection + "," + relevance + " AS relevance FROM " + parts["table"] +
                 " WHERE " + parts["scoped_where"] + match +
                 " ORDER BY relevance DESC,recorded_at DESC NULLS LAST," + parts["key"])
        await self._scan_candidates_on(conn, context, query,
            (*proof_args, *text_args, *parts["scoped_args"], *match_args), rows, missing,
            limit=limit, budget=budget, lane="lexical", source_gap="lexical_source_budget:" + family)

    async def _scan_candidates_on(self, conn, context, query, args, rows, missing, *,
                                  limit, budget, lane, source_gap):
        if budget:
            budget.check()
        cursor = conn.cursor(name="memory_" + lane + "_" + uuid4().hex)
        # DECLARE fixes the sorted result for this cursor. No OFFSET or moving
        # rank/time boundary can skip a readable record after a filtered batch.
        await cursor.execute(query, args)
        accepted = 0
        while accepted < limit:
            if budget:
                budget.check()
            batch = await cursor.fetchmany(LEXICAL_BATCH_ROWS)
            if not batch:
                break
            checked = await check_rows_on(self.memory.sources, conn, context, batch)
            for row, allowed in zip(batch, checked, strict=True):
                if allowed is None:
                    missing.append(source_gap)
                elif allowed and accepted < limit:
                    rows.append({key: value for key, value in row.items() if key not in
                                 {"_supports", "_derivations", "_explicit", "_relation_proofs"}})
                    accepted += 1
        # On error the enclosing transaction/savepoint closes the cursor, so a
        # CLOSE in an aborted transaction cannot mask the original exception.
        await cursor.close()

    async def has_vectors(self, context, plan, fingerprint, budget):
        """Only a complete authorized selection may prove this vector lane empty.

        EXISTS bounds returned rows, not the searched authorization domain; an
        SQL timeout is unknown, never evidence that the domain has no vectors.
        """
        async with self.database.transaction(budget, read_only=True) as conn:
            await conn.execute("SELECT set_config('jit','off',true)")
            await self.memory.identity.check_context(context, connection=conn)
            missing = []
            for family in families(plan.evidence_kinds):
                p = self.selection(context, plan, family)
                projection, proof_args = _proof_projection(p)
                rows = []
                await self._scan_candidates_on(conn, context,
                    "SELECT " + projection + " FROM " + p["table"] + " WHERE " + p["scoped_where"] +
                    " AND " + p["profile"] + "=%s AND " + p["embedding"] + " IS NOT NULL ORDER BY " + p["key"],
                    (*proof_args, *p["scoped_args"], fingerprint), rows, missing,
                    limit=1, budget=budget, lane="vector_probe", source_gap="vector_source_budget")
                if rows:
                    return True
            if missing:
                raise AppError("memory_vector_candidate_budget", status=503)
        return False

    async def _vector_scan_on(self, conn, context, parts, values, fingerprint, limit, *,
                              exact, budget=None, rows=None, missing=None):
        p = parts
        rows = [] if rows is None else rows
        missing = [] if missing is None else missing
        projection, proof_args = _proof_projection(p)
        distance = "(" + p["embedding"] + "<=>%s)"
        # +0 forces the exact path to sort all cheap scoped distances rather
        # than accepting a bounded HNSW scan. Only fetched batches prove sources.
        order = distance + ("+0," + p["key"] if exact else "")
        query = ("SELECT " + projection + "," + p["embedding"] + "::real[] AS vector,1-" + distance +
                 " AS relevance FROM " + p["table"] + " WHERE " + p["scoped_where"] +
                 " AND " + p["profile"] + "=%s AND " + p["embedding"] + " IS NOT NULL ORDER BY " + order)
        args = (*proof_args, Vector(values), *p["scoped_args"], fingerprint, Vector(values))
        if not exact:
            # Preserve the original bounded ORDER BY distance LIMIT shape that
            # admits HNSW. A filtered shortfall takes the exact cursor below;
            # this raw ANN prefix can never prove the readable lane exhausted.
            query += " LIMIT %s"
            args += (limit,)
        await self._scan_candidates_on(conn, context, query,
            args, rows, missing,
            limit=limit, budget=budget, lane="vector", source_gap="vector_source_budget")
        return rows

    async def _exact_vectors_on(self, conn, context, parts, values, fingerprint, limit, *,
                                budget=None, rows=None, missing=None):
        return await self._vector_scan_on(conn, context, parts, values, fingerprint, limit,
            exact=True, budget=budget, rows=rows, missing=missing)

    async def _ann_vectors_on(self, conn, context, parts, values, fingerprint, limit, *,
                              budget=None, rows=None, missing=None):
        return await self._vector_scan_on(conn, context, parts, values, fingerprint, limit,
            exact=False, budget=budget, rows=rows, missing=missing)

    @staticmethod
    def _merge_vectors(*groups, limit):
        selected = {}
        for group in groups:
            for row in group:
                selected[(row["_kind"], row["_id"], row["revision"])] = row
        return sorted(selected.values(), key=lambda row: (-row["relevance"], row["_id"]))[:limit]

    async def vectors(self, context, plan, values, fingerprint, budget, *, limit=96):
        selected, missing, bounded = {}, [], False
        try:
            async with self.database.transaction(budget, read_only=True) as conn:
                await register_vector_async(conn)
                # Keep JIT and ANN settings local to this transaction.
                await conn.execute("SELECT set_config('jit','off',true),set_config('hnsw.iterative_scan','strict_order',true),"
                    "set_config('hnsw.ef_search',%s,true),set_config('hnsw.max_scan_tuples',%s,true)",
                    (str(min(max(int(self.settings.get("ef_search", 128)), 96), 1000)),
                     str(min(max(int(self.settings.get("max_scan_tuples", 20000)), 1000), 50000))))
                await self.memory.identity.check_context(context, connection=conn)
                for family in families(plan.evidence_kinds):
                    rows = selected.setdefault(family, [])
                    try:
                        async with conn.transaction():
                            if budget:
                                budget.check()
                            p = self.selection(context, plan, family)
                            count = await (await conn.execute(
                                "SELECT count(*) AS count FROM(SELECT 1 FROM " + p["table"] + " WHERE " +
                                p["scoped_where"] + " AND " + p["profile"] + "=%s AND " + p["embedding"] +
                                " IS NOT NULL LIMIT %s) q", (*p["scoped_args"], fingerprint, EXACT_VECTOR_CANDIDATES + 1),
                            )).fetchone()
                            if not count["count"]:
                                continue
                            options = {"budget": budget, "rows": rows, "missing": missing}
                            if count["count"] <= EXACT_VECTOR_CANDIDATES:
                                await self._exact_vectors_on(conn, context, p, values, fingerprint, limit, **options)
                            else:
                                await self._ann_vectors_on(conn, context, p, values, fingerprint, limit, **options)
                                if len(rows) < limit:
                                    approximate, exact = tuple(rows), []
                                    try:
                                        await self._exact_vectors_on(conn, context, p, values, fingerprint, limit,
                                            budget=budget, rows=exact, missing=missing)
                                    finally:
                                        # A late exact timeout must not erase
                                        # already proved ANN or exact batches.
                                        rows[:] = self._merge_vectors(approximate, exact, limit=limit)
                                    rows[:] = exact
                    except (TimeoutError, QueryCanceled):
                        bounded = True
        except (TimeoutError, QueryCanceled):
            bounded = True
        rows = [row for group in selected.values() for row in group]
        return sorted(rows, key=lambda row: -row["relevance"])[:256], bounded or bool(missing)

    async def page(self, context, plan, budget, *, after=(None, None), snapshot=None, limit=50):
        rows = []
        async with self.database.transaction(budget, read_only=True) as conn:
            await conn.execute("SELECT set_config('jit','off',true)")
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
                await conn.execute("SELECT set_config('jit','off',true)")
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
