"""Authorized SQL candidates over the existing Memory tables and embedding profile."""

from datetime import UTC, datetime
from dataclasses import dataclass
import re
from uuid import uuid4

from pgvector import Vector
from pgvector.psycopg import register_vector_async
from psycopg.errors import QueryCanceled

from tianshu.runtime.errors import AppError
from .candidate_sources import check_rows_on


EXACT_VECTOR_CANDIDATES = 2000
LEXICAL_BATCH_ROWS = 128
REFILL_FACTOR = 8


@dataclass(frozen=True)
class CandidateSelection:
    rows: tuple[dict, ...]
    missing: tuple[str, ...]


def source_predicate(groups, derivations, explicit):
    return ("sources.any_support_usable(" + groups + ",%s::text[]) AND "
            "(" + derivations + "='[]'::jsonb OR sources.any_support_usable(jsonb_build_array(" + derivations + "),%s::text[])) AND "
            "(" + explicit + "='[]'::jsonb OR sources.any_support_usable(jsonb_build_array(" + explicit + "),%s::text[]))")


def families(kinds):
    return tuple(name for name in ("claim", "matter", "episode", "learning")
                 if name in kinds or name == "claim" and "hypothesis" in kinds)


def query_terms(query):
    # Keep intact words and Chinese bigrams ahead of the legal single-character
    # fallback. Otherwise a long Chinese query exhausts the token limit on
    # individual characters before its useful names and phrases are considered.
    words = re.findall(r"[A-Za-z0-9_]+|[\u3400-\u9fff]+", query.casefold())
    words = list(dict.fromkeys(words))
    chinese = [word for word in words if re.fullmatch(r"[\u3400-\u9fff]+", word)]
    bigrams = list(dict.fromkeys(word[index:index + 2] for word in chinese for index in range(len(word) - 1)))
    singles = list(dict.fromkeys(character for word in chinese for character in word))
    tokens = list(dict.fromkeys((*words[:48], *bigrams[:48], *singles[:32], *words, *bigrams, *singles)))[:128]
    tsquery = " | ".join("'" + token.replace("'", "''") + "'" for token in tokens)
    fragments = ["%" + token.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
                 for token in tokens if len(token) > 1 or len(token) == 1 and "\u3400" <= token <= "\u9fff"]
    return tsquery, fragments


def _proof_projection(parts, *, include_candidate=True):
    relation, args = "NULL::jsonb", ()
    if parts["relation_where"]:
        relation = ("COALESCE((SELECT jsonb_agg(jsonb_build_object('supports',l.support_groups,"
                    "'derivations',l.derivation_refs)) FROM memory.semantic_links l WHERE " +
                    parts["relation_where"] + "),'[]'::jsonb)")
        args = tuple(parts["relation_scoped_args"])
    return ((parts["candidate_select"] + "," if include_candidate else "") + parts["groups"] + " AS _supports," +
            parts["derivations"] + " AS _derivations," + parts["explicit"] + " AS _explicit," +
            relation + " AS _relation_proofs"), args


class CandidateStore:
    def __init__(self, memory, settings=None):
        self.memory, self.database = memory, memory.database
        self.settings = settings or {}

    def selection(self, context, plan, family, *, snapshot=None, include_unknown=False):
        args, clauses, extra_projection, supplements = [], [], "", "'[]'::jsonb"
        relation, relation_args = "", []
        relation_where, relation_scoped_args = "", []
        knowledge_time = plan.known_at if plan.time_mode == "known_at" else snapshot
        moment = plan.valid_at or plan.known_at or snapshot or datetime.now(UTC)
        if family == "claim":
            table = "memory.claims h JOIN memory.claim_revisions r ON r.claim_id=h.claim_id"
            if plan.time_mode == "current":
                clauses.append("r.revision=h.current_revision")
                if snapshot is not None:
                    clauses.append("(r.recorded_at<=%s OR r.recorded_at IS NULL)")
                    args.append(snapshot)
            elif knowledge_time is None:
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
            if plan.time_mode == "current":
                table, head = "memory.matters r", "r.revision"
                content, search, embedding, profile = "(r.title||' '||r.summary||' '||r.conditions::text)", "r.search_vector", "r.embedding", "r.embedding_profile"
                if snapshot is not None:
                    clauses.append("(r.recorded_at<=%s OR r.recorded_at IS NULL)")
                    args.append(snapshot)
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
        elif family == "learning":
            # Only a reference and lexemes live in Memory. Companion resolves
            # the authoritative text and current eligibility on every read.
            table, key, subject, actor, kind = "memory.learning_index_refs r", "r.rule_id", "NULL::text", "r.actor_id", "'learning'::text"
            head = ("(SELECT max(newer.revision) FROM memory.learning_index_refs newer "
                    "WHERE newer.actor_id=r.actor_id AND newer.rule_id=r.rule_id)")
            if plan.time_mode == "current":
                clauses.append("r.revision=" + head)
                if snapshot is not None:
                    clauses.append("r.recorded_at<=%s")
                    args.append(snapshot)
            elif knowledge_time is not None:
                clauses.append("r.recorded_at<=%s AND NOT EXISTS(SELECT 1 FROM memory.learning_index_refs newer "
                    "WHERE newer.actor_id=r.actor_id AND newer.rule_id=r.rule_id AND newer.revision>r.revision "
                    "AND newer.recorded_at<=%s)")
                args.extend((knowledge_time, knowledge_time))
            else:
                clauses.append("r.revision=" + head)
            content, search, embedding, profile = "''::text", "r.search_vector", "NULL::vector", "NULL::text"
            groups, derivations = "jsonb_build_array(r.source_refs)", "'[]'::jsonb"
        else:
            key = "r.episode_id"
            subject, actor, kind = "r.person_id", "r.actor_id", "'episode'::text"
            content, groups = "r.summary", "jsonb_build_array(r.source_refs)"
            if plan.time_mode == "current":
                table, head = "memory.episodes r", "r.revision"
                search, embedding, profile, derivations = "r.search_vector", "r.embedding", "r.embedding_profile", "'[]'::jsonb"
                supplements = "r.supplement_refs"
                extra_projection = ",r.supplement_refs AS _head_supplement_refs"
                clauses.append("r.active")
                if snapshot is not None:
                    clauses.append("(r.recorded_at<=%s OR r.recorded_at IS NULL)")
                    args.append(snapshot)
            else:
                table = ("memory.episodes h JOIN memory.episode_revisions r ON r.episode_id=h.episode_id AND r.actor_id=h.actor_id "
                    "LEFT JOIN memory.episode_original_terms ot ON ot.episode_id=r.episode_id AND ot.revision=r.revision "
                    "AND ot.source_refs=r.source_refs")
                head = "h.revision"
                search = "(to_tsvector('simple',r.summary)||COALESCE(ot.search_vector,''::tsvector))"
                embedding, profile, derivations = "NULL::vector", "NULL::text", "h.source_refs"
                supplements = "(r.supplement_refs||h.supplement_refs)"
                clauses.extend(("h.active", "r.active", "h.scope_ids<@%s::text[]"))
                args.append(list(context.scope_ids))
                extra_projection = (",h.sequence AS sequence,'not_historical'::text AS extraction_state,"
                    "CASE WHEN r.revision=h.revision THEN h.extraction_missing ELSE ARRAY[]::text[] END AS extraction_missing,"
                    "(r.summary='') AS _original_text_required,h.source_refs AS derivation_refs,"
                    "ARRAY(SELECT DISTINCT unnest(r.scope_ids||h.scope_ids)) AS scope_ids,"
                    "h.supplement_refs AS _head_supplement_refs")
                if knowledge_time is None:
                    clauses.append("r.revision=h.revision")
                else:
                    legacy = " OR r.recorded_at IS NULL" if plan.time_mode != "known_at" or include_unknown else ""
                    clauses.append("(r.recorded_at<=%s" + legacy + ") AND NOT EXISTS(SELECT 1 FROM memory.episode_revisions newer "
                        "WHERE newer.episode_id=r.episode_id AND newer.revision>r.revision AND (newer.recorded_at<=%s" +
                        legacy.replace("r.", "newer.") + "))")
                    args.extend((knowledge_time, knowledge_time))
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
        if plan.subject_ids and family != "learning":
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
                # Learning references have no authoritative about_matter edge.
                clauses.append("false")
        select = (f"r.*{extra_projection},{actor} AS actor_id,{key} AS _id,{kind} AS _kind,{head} AS head_revision,"
                  f"{head} AS current_revision,{content} AS _content,{embedding} AS _embedding,{profile} AS _profile")
        lexical_column = ("NULL::text" if family == "learning" or family == "matter" and table != "memory.matters r"
                          or family == "episode" and table != "memory.episodes r"
                          else "r.lexical")
        candidate_select = (f"{actor} AS actor_id,{subject} AS subject_id,{key} AS _id,{kind} AS _kind,r.revision,"
                            f"{head} AS head_revision,{head} AS current_revision,left({content},512) AS _content,"
                            f"left({lexical_column},1024) AS lexical,r.recorded_at")
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
                "embedding": embedding, "profile": profile, "supplements": supplements}

    @staticmethod
    def _searchable(parts):
        result = dict(parts)
        result["derivations"] = "(" + parts["derivations"] + "||" + parts["supplements"] + ")"
        return result

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
        parts = self._searchable(self.selection(context, plan, family))
        projection, proof_args = _proof_projection(parts)
        if tsquery:
            strong = " | ".join(token for token in tsquery.split(" | ") if len(token[1:-1]) > 1) or tsquery
            whole = " | ".join(token for token in tsquery.split(" | ") if len(token[1:-1]) > 2) or strong
            relevance = ("ts_rank_cd(" + parts["search"] + ",to_tsquery('simple',%s)) + "
                         "2*ts_rank_cd(" + parts["search"] + ",to_tsquery('simple',%s)) + "
                         "4*ts_rank_cd(" + parts["search"] + ",to_tsquery('simple',%s))")
            # Current rows have a lexical tsvector generated by the writer.
            # Historical matter revisions predate that projection and need a
            # scoped literal fallback until their own index is rebuilt.
            historical_matter = (family == "matter" and parts["table"] != "memory.matters r" or
                                 family == "episode" and parts["table"] != "memory.episodes r")
            match = " AND (" + parts["search"] + "@@to_tsquery('simple',%s)"
            if historical_matter:
                match += " OR " + parts["content"] + " ILIKE ANY(%s::text[])"
            match += ")"
            text_args, match_args = (tsquery, strong, whole), ((tsquery, fragments) if historical_matter else (tsquery,))
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
        accepted, scanned = 0, 0
        scan_limit = max(LEXICAL_BATCH_ROWS, limit * REFILL_FACTOR)
        while accepted < limit:
            if budget:
                budget.check()
            batch = await cursor.fetchmany(min(LEXICAL_BATCH_ROWS, scan_limit - scanned))
            if not batch:
                break
            scanned += len(batch)
            checked = await check_rows_on(self.memory.sources, conn, context, batch)
            for row, allowed in zip(batch, checked, strict=True):
                if allowed is None:
                    missing.append(source_gap)
                elif allowed and accepted < limit:
                    rows.append({key: value for key, value in row.items() if key not in
                                 {"_supports", "_derivations", "_explicit", "_relation_proofs"}})
                    accepted += 1
            if scanned >= scan_limit and accepted < limit:
                missing.append(source_gap + ":refill_limit")
                break
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
            generation = await self.memory.jobs.current_generation_on(conn)
            missing = []
            for family in families(plan.evidence_kinds):
                if family == "learning":
                    continue
                p = self._searchable(self._vector_selection(self.selection(context, plan, family), generation))
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

    @staticmethod
    def _vector_selection(parts, generation):
        """Join only the same object revision; the WHERE profile prunes its partition."""
        result = dict(parts)
        result["table"] += (" JOIN memory.search_vectors sv ON sv.kind=" + parts["kind"] +
                            " AND sv.object_id=" + parts["key"] + " AND sv.revision=r.revision")
        result["embedding"], result["profile"] = "sv.embedding", "sv.profile_key"
        result["scoped_where"] += " AND sv.generation=%s"
        result["scoped_args"] = [*parts["scoped_args"], generation]
        return result

    async def profile_missing(self, rows, fingerprint, budget):
        """Check coverage of already authorized lexical candidates, not all rows."""
        if not rows:
            return False
        keys = {(row["_kind"], row["_id"], row["revision"]) for row in rows if row["_kind"] != "learning"}
        if not keys:
            return False
        kinds, identifiers, revisions = zip(*keys)
        async with self.database.transaction(budget, read_only=True) as conn:
            generation = await self.memory.jobs.current_generation_on(conn)
            found = await (await conn.execute(
                "SELECT kind,object_id,revision FROM memory.search_vectors WHERE profile_key=%s AND generation=%s "
                "AND (kind,object_id,revision) IN (SELECT * FROM unnest(%s::text[],%s::text[],%s::bigint[]))",
                (fingerprint, generation, list(kinds), list(identifiers), list(revisions)))).fetchall()
        present = {(row["kind"], row["object_id"], row["revision"]) for row in found}
        return bool(keys - present)

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
        query = ("SELECT " + projection + ",1-" + distance +
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
                generation = await self.memory.jobs.current_generation_on(conn)
                for family in families(plan.evidence_kinds):
                    if family == "learning":
                        continue
                    rows = selected.setdefault(family, [])
                    try:
                        async with conn.transaction():
                            if budget:
                                budget.check()
                            p = self._searchable(self._vector_selection(self.selection(context, plan, family), generation))
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
            await self.memory.identity.check_context(context, connection=conn)
            for family in families(plan.evidence_kinds):
                p = self.selection(context, plan, family, snapshot=snapshot)
                tsquery, fragments = query_terms(plan.query)
                match = " AND (" + p["search"] + "@@to_tsquery('simple',%s) OR " + p["content"] + " ILIKE ANY(%s::text[]))" if tsquery else ""
                text_args = (tsquery, fragments) if tsquery else ()
                if tsquery and family == "episode":
                    p = self._searchable(p)
                proof, proof_args = _proof_projection(p, include_candidate=False)
                cursor = conn.cursor(name="memory_page_" + uuid4().hex)
                await cursor.execute("SELECT " + p["select"] + "," + proof + " FROM " + p["table"] + " WHERE " + p["scoped_where"] +
                    match +
                    " AND (%s::text IS NULL OR (" + p["kind"] + "," + p["key"] + ")>(%s,%s)) "
                    "ORDER BY " + p["kind"] + "," + p["key"],
                    (*proof_args, *p["scoped_args"], *text_args, after[0], after[0], after[1]))
                selected = []
                while len(selected) <= limit:
                    if budget:
                        budget.check()
                    batch = await cursor.fetchmany(LEXICAL_BATCH_ROWS)
                    if not batch:
                        break
                    checks = await check_rows_on(self.memory.sources, conn, context, batch)
                    if any(value is None for value in checks):
                        raise AppError("memory_source_proof_unavailable", status=503)
                    selected.extend(row for row, allowed in zip(batch, checks, strict=True) if allowed)
                await cursor.close()
                rows.extend(selected[:limit + 1])
        return sorted(rows, key=lambda row: (row["_kind"], row["_id"]))[:limit + 1]

    async def reread(self, conn, context, plan, rows, *, snapshot=None):
        pending = []
        for family in families(plan.evidence_kinds):
            ids = [row["_id"] for row in rows if row["_kind"] == family or family == "claim" and row["_kind"] == "hypothesis"]
            if not ids:
                continue
            p = self.selection(context, plan, family, snapshot=snapshot)
            proof, proof_args = _proof_projection(p, include_candidate=False)
            pending.extend(await (await conn.execute("SELECT " + p["select"] + "," + proof + " FROM " + p["table"] +
                " WHERE " + p["scoped_where"] + " AND " + p["key"] + "=ANY(%s::text[])",
                (*proof_args, *p["scoped_args"], ids))).fetchall())
        checks = await check_rows_on(self.memory.sources, conn, context, pending)
        latest = {(row["_kind"], row["_id"], row["revision"]): row
                  for row, allowed in zip(pending, checks, strict=True) if allowed is True}
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
        if plan.time_mode == "valid_at" and "matter" in plan.evidence_kinds:
            missing.append("matter_effective_time_inferred_from_report")
        if plan.time_mode == "valid_at" and "episode" in plan.evidence_kinds:
            missing.append("episode_valid_at_uses_occurrence_time")
        return tuple(missing)
