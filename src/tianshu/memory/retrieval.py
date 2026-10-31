"""Authorized native lexical/vector candidates, bounded fusion, MMR and optional rerank."""

from datetime import UTC, datetime, timedelta
import math
from uuid import uuid4
import aiohttp
import psycopg

from pgvector import Vector
from pgvector.psycopg import register_vector_async

from tianshu.models.types import ModelRequest
from tianshu.runtime.errors import AppError
from tianshu.types import ContextContribution, RequestBudget, ServiceClass
from tianshu.sources.dependencies import unique_refs
from .claims import ELIGIBLE_SCOPES, lexical
from . import codec


AUTHORIZED = (
    "c.actor_id=%s AND r.state='active' AND "
    + ELIGIBLE_SCOPES
    + " AND sources.any_support_usable(r.support_groups,%s::text[]) "
    "AND (r.valid_from IS NULL OR r.valid_from<=%s) AND (r.valid_until IS NULL OR r.valid_until>%s)"
)
JOIN = " FROM memory.claims c JOIN memory.claim_revisions r ON r.claim_id=c.claim_id AND r.revision=c.current_revision "


def similarity(left, right):
    if left.get("vector") is not None and right.get("vector") is not None:
        a, b = left["vector"], right["vector"]
        denominator = math.sqrt(sum(v * v for v in a) * sum(v * v for v in b))
        return sum(a * b for a, b in zip(a, b)) / denominator if denominator else 0.0
    a, b = set(left["lexical"].split()), set(right["lexical"].split())
    return len(a & b) / max(len(a | b), 1)


def mmr(candidates, limit, weight=0.72):
    remaining = list(candidates)
    chosen = []
    maxima = {row["claim_id"]: 0.0 for row in remaining}
    while remaining and len(chosen) < limit:
        selected = max(
            remaining,
            key=lambda row: weight * row["score"] - (1 - weight) * maxima[row["claim_id"]],
        )
        remaining.remove(selected)
        chosen.append(selected)
        for row in remaining:
            key = row["claim_id"]
            maxima[key] = max(maxima[key], similarity(row, selected))
    return chosen


class MemoryRetrieval:
    def __init__(self, memory, embeddings, settings=None):
        self.memory, self.embeddings = memory, embeddings
        self.database, self.models = memory.database, memory.models
        self.settings = settings or {}

    @staticmethod
    def phase_budget(parent, seconds):
        deadline = min(
            parent.deadline_at, datetime.now(UTC) + timedelta(seconds=max(seconds, 0.01))
        )
        return RequestBudget(
            deadline, trace_id=parent.trace_id, cancellation_id=parent.cancellation_id
        )

    async def _lexical(self, context, query, budget):
        now = datetime.now(UTC)
        parameters = (context.actor_id, list(context.scope_ids), list(context.scope_ids), now, now)
        tokens = lexical(query).split()[:128]
        tsquery = " | ".join("'" + token.replace("'", "''") + "'" for token in tokens)
        if not tsquery:
            return []
        async with self.database.transaction(budget, read_only=True) as conn:
            return await (
                await conn.execute(
                    "SELECT r.*,c.actor_id,ts_rank_cd(r.search_vector,to_tsquery('simple',%s)) AS relevance "
                    + JOIN
                    + " WHERE "
                    + AUTHORIZED
                    + " AND r.search_vector@@to_tsquery('simple',%s) "
                    "ORDER BY relevance DESC,r.observed_at DESC,c.claim_id LIMIT 96",
                    (tsquery, *parameters, tsquery),
                )
            ).fetchall()

    async def _vectors(self, context, values, fingerprint, budget):
        now = datetime.now(UTC)
        parameters = (
            context.actor_id,
            list(context.scope_ids),
            list(context.scope_ids),
            now,
            now,
            fingerprint,
        )
        eligible = AUTHORIZED + " AND r.embedding_profile=%s AND r.embedding IS NOT NULL"
        exact = (
            "WITH allowed AS MATERIALIZED(SELECT r.claim_id,r.revision,r.embedding "
            + JOIN
            + " WHERE "
            + eligible
            + "), nearest AS(SELECT claim_id,revision,embedding<=>%s AS distance FROM allowed ORDER BY distance LIMIT 96) "
            "SELECT r.*,c.actor_id,r.embedding::real[] AS vector,1-n.distance AS relevance FROM nearest n "
            "JOIN memory.claim_revisions r USING(claim_id,revision) JOIN memory.claims c USING(claim_id) ORDER BY n.distance"
        )
        async with self.database.transaction(budget, read_only=True) as conn:
            await register_vector_async(conn)
            count = await (
                await conn.execute(
                    "SELECT count(*) AS count FROM(SELECT 1 "
                    + JOIN
                    + " WHERE "
                    + eligible
                    + " LIMIT 2001) q",
                    parameters,
                )
            ).fetchone()
            if count["count"] <= 2000:
                rows = await (await conn.execute(exact, (*parameters, Vector(values)))).fetchall()
                return rows, False
            version = await (
                await conn.execute("SELECT extversion FROM pg_extension WHERE extname='vector'")
            ).fetchone()
            if not version or tuple(int(part) for part in version["extversion"].split(".")[:2]) < (
                0,
                8,
            ):
                raise AppError("pgvector_iterative_scan_required", status=503)
            await conn.execute(
                "SELECT set_config('hnsw.iterative_scan','strict_order',true),set_config('hnsw.ef_search',%s,true),"
                "set_config('hnsw.max_scan_tuples',%s,true),set_config('hnsw.scan_mem_multiplier','2',true)",
                (
                    str(min(max(int(self.settings.get("ef_search", 128)), 96), 1000)),
                    str(min(max(int(self.settings.get("max_scan_tuples", 20000)), 1000), 50000)),
                ),
            )
            rows = await (
                await conn.execute(
                    "SELECT r.*,c.actor_id,r.embedding::real[] AS vector,1-(r.embedding<=>%s) AS relevance "
                    + JOIN
                    + " WHERE "
                    + eligible
                    + " ORDER BY r.embedding<=>%s LIMIT 96",
                    (Vector(values), *parameters, Vector(values)),
                )
            ).fetchall()
            if len(rows) < 96:
                # Sparse authorization can exhaust the bounded ANN scan. Exact distance stays inside PG.
                rows = await (await conn.execute(exact, (*parameters, Vector(values)))).fetchall()
            return rows, len(rows) < 96

    async def search(self, context, query, budget, *, limit=12, source_refs=()):
        budget.check()
        await self.memory.identity.check_context(context)
        if not context.actor_id or not query:
            return (), ()
        limit = min(max(int(limit), 1), 32)
        missing = []
        lexical_rows = await self._lexical(context, query, budget)
        vector_rows = []
        if self.settings.get("vector", True) and budget.remaining() > 0.1:
            try:
                embedding_budget = self.phase_budget(
                    budget, float(self.settings.get("embedding_seconds", 1.0))
                )
                values, fingerprint = await self.embeddings.vector(
                    context,
                    query,
                    embedding_budget,
                    intent="memory-query:" + str(uuid4()),
                    query=True,
                    service_class=ServiceClass.INTERACTIVE,
                    source_refs=source_refs,
                )
                vector_budget = self.phase_budget(
                    budget, float(self.settings.get("vector_sql_seconds", 0.8))
                )
                vector_rows, incomplete = await self._vectors(
                    context, values, fingerprint, vector_budget
                )
                if incomplete:
                    missing.append("vector_candidate_budget")
            except (AppError, TimeoutError, aiohttp.ClientError, psycopg.Error) as exc:
                missing.append(getattr(exc, "code", "embedding_unavailable"))
        fused = {}
        for rows in (lexical_rows, vector_rows):
            for rank, row in enumerate(rows):
                current = fused.setdefault(row["claim_id"], {**row, "score": 0.0})
                current["score"] += 1 / (60 + rank + 1)
                if row.get("vector") is not None:
                    current["vector"] = row["vector"]
        maximum = max((row["score"] for row in fused.values()), default=1)
        for row in fused.values():
            row["score"] /= maximum
        ranked = mmr(
            sorted(fused.values(), key=lambda row: row["score"], reverse=True)[:128], max(limit, 24)
        )
        async with self.database.transaction(read_only=True) as conn:
            supports = await self.memory.claims.selected_groups(conn, context, ranked)
        ranked = [
            {**row, "selected_refs": refs}
            for row, refs in zip(ranked, supports)
            if refs is not None
        ]
        if self.settings.get("rerank", False) and ranked and budget.remaining() > 0.1:
            phase = self.phase_budget(budget, float(self.settings.get("rerank_seconds", 0.8)))
            request = ModelRequest(
                "memory-rerank:" + str(uuid4()),
                str(uuid4()),
                "rerank",
                ServiceClass.INTERACTIVE,
                context,
                phase,
                (),
                required_capabilities=frozenset({"rerank"}),
                max_output_tokens=0,
                native_payload={
                    "query": query,
                    "documents": [row["content"] for row in ranked],
                    "top_n": limit,
                },
                source_refs=unique_refs(
                    source_refs, (ref for row in ranked for ref in row["selected_refs"])
                ),
            )
            try:
                result = await self.models.generate(request)
                indexes = (
                    (result.native_response or {}).get("results", ())
                    if result.receipt.status == "succeeded"
                    else ()
                )
                ordered = []
                seen = set()
                for item in indexes:
                    index = item.get("index") if isinstance(item, dict) else None
                    if type(index) is int and 0 <= index < len(ranked) and index not in seen:
                        seen.add(index)
                        ordered.append(ranked[index])
                if ordered:
                    ranked = ordered + [
                        row for index, row in enumerate(ranked) if index not in seen
                    ]
                else:
                    missing.append("rerank_unavailable")
            except (AppError, TimeoutError, aiohttp.ClientError):
                missing.append("rerank_unavailable")
        selected = ranked[:limit]
        checked = await self.memory.sources.check_many(
            context, tuple(row["selected_refs"] for row in selected)
        )
        return tuple(
            ContextContribution(
                "memory_claim",
                row["content"],
                tuple(row["selected_refs"]),
                tuple(context.scope_ids),
                60,
                False,
                str(row["revision"]),
            )
            for row, valid in zip(selected, checked)
            if valid
        ), tuple(missing)
