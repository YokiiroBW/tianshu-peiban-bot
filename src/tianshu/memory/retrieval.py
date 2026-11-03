"""One multi-type evidence query for chat, tools and bounded consolidation reads."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from itertools import chain, islice
import math
from uuid import uuid4

import aiohttp
import psycopg

from tianshu.models.types import ModelRequest
from tianshu.runtime.api import Page
from tianshu.runtime.errors import AppError
from tianshu.sources.dependencies import unique_refs
from tianshu.types import ContextContribution, Coverage, RequestBudget, ServiceClass
from . import codec
from .candidates import CandidateSelection, CandidateStore
from .domain_utils import lexical
from .display import display_evidence_on
from .evidence import build_evidence_unit, select_supports_on
from .indexing import evidence_text, rerank_text
from .query_plan import parse_plan, read_cursor, write_cursor
from .retrieval_budget import (MODEL_EXECUTION_SECONDS, PROFILE_READ_SECONDS, SQL_READ_SECONDS,
                               VECTOR_SQL_SECONDS, foreground_budget as make_foreground_budget, optional_failure)
from .types import MemoryQueryPlan, RecordRef


PHASE_ERRORS = (AppError, TimeoutError, aiohttp.ClientError, psycopg.Error, OSError)


def record_key(row):
    return row["_kind"], row["_id"], row["revision"]


def similarity(left, right):
    if left.get("vector") is not None and right.get("vector") is not None:
        a, b = left["vector"], right["vector"]
        denominator = math.sqrt(sum(v * v for v in a) * sum(v * v for v in b))
        return sum(a * b for a, b in zip(a, b)) / denominator if denominator else 0.0
    a = set((left.get("lexical") or lexical(left["_content"])).split())
    b = set((right.get("lexical") or lexical(right["_content"])).split())
    return len(a & b) / max(len(a | b), 1)


class _MmrOrder:
    """A replayable ranked prefix; extend it only when a consumer needs more."""

    def __init__(self, candidates, weight=0.72):
        self.remaining, self.chosen = list(candidates), []
        self.maxima = {record_key(row): 0.0 for row in self.remaining}
        self.count, self.weight = len(self.remaining), weight

    def __len__(self):
        return self.count

    def __iter__(self):
        for index in range(self.count):
            if index == len(self.chosen):
                selected = max(self.remaining, key=lambda row: self.weight * row["score"] -
                               (1 - self.weight) * self.maxima[record_key(row)])
                self.remaining.remove(selected)
                self.chosen.append(selected)
                for row in self.remaining:
                    key = record_key(row)
                    self.maxima[key] = max(self.maxima[key], similarity(row, selected))
            yield self.chosen[index]


def contributions(units):
    """A complete evidence unit is also the indivisible context budget unit."""
    priorities = {"matter": 75, "claim": 60, "hypothesis": 55, "episode": 50, "reflection": 40}
    return tuple(ContextContribution("memory_evidence", evidence_text(unit), unit.source_refs,
        unit.scope_ids, priorities[unit.kind], False, f"{unit.kind}:{unit.id}:{unit.revision}")
        for unit in units)


class MemoryRetrieval:
    def __init__(self, memory, embeddings, settings=None):
        self.memory, self.embeddings = memory, embeddings
        self.database, self.models = memory.database, memory.models
        self.settings = settings or {}
        self.store = CandidateStore(memory, self.settings)

    def foreground_budget(self, parent=None, *, seconds=None, deadline_at=None):
        return make_foreground_budget(self.settings, parent, seconds=seconds, deadline_at=deadline_at)

    @classmethod
    def local_budget(cls, parent):
        # A longer response allowance must not become a longer SQL timeout.
        # Background reads retain the database's existing independent bound.
        return cls.phase_budget(parent, SQL_READ_SECONDS) if parent is not None else None

    @staticmethod
    def phase_budget(parent, seconds):
        if parent is None:
            return RequestBudget(datetime.now(UTC) + timedelta(seconds=seconds))
        available = parent.remaining()
        # Optional remote work leaves time for authoritative SQL/source rechecks.
        seconds = min(max(seconds, 0.01), max(available - min(0.5, available / 2), 0.01))
        deadline = min(parent.deadline_at, datetime.now(UTC) + timedelta(seconds=seconds))
        return RequestBudget(deadline, trace_id=parent.trace_id, cancellation_id=parent.cancellation_id)

    @staticmethod
    def remote_phase(parent, execution_seconds):
        """Reserve a whole execution window before admitting optional remote work.

        The parent's deadline is unchanged. Spare time can pay for queueing;
        queueing must not consume the target execution window or final rechecks.
        Consolidation's background candidate port has no business deadline;
        its execution allowance is allocated by Models only on admission.
        """
        if not math.isfinite(execution_seconds) or execution_seconds <= 0:
            raise AppError("invalid_memory_retrieval_phase_budget")
        if parent is None:
            return MemoryRetrieval.phase_budget(None, execution_seconds), None
        spare = parent.remaining() - 0.5 - execution_seconds
        if spare <= 0.05:
            return None
        deadline = parent.deadline_at - timedelta(seconds=0.5)
        return (RequestBudget(deadline, trace_id=parent.trace_id, cancellation_id=parent.cancellation_id),
                spare - 0.05)

    @staticmethod
    def _degrade(error, parent, *, model=False):
        # A lane's shorter deadline may expire; the caller's business deadline
        # and external cancellation never become permission to keep working.
        if parent is not None:
            parent.check()
        if not optional_failure(error, model=model):
            raise error

    async def _vectors(self, context, plan, budget, source_refs, service_class):
        seconds = float(self.settings.get("embedding_seconds", MODEL_EXECUTION_SECONDS))
        if self.remote_phase(budget, seconds) is None:
            return [], ("embedding_budget_insufficient",)
        phase = "embedding_profile"
        try:
            # Profile is a control read, but its client's default five seconds
            # must not escape this query's smaller overall deadline.
            async with self.phase_budget(budget, PROFILE_READ_SECONDS).timeout():
                profile = await self.embeddings.profile()
            phase = "vector_candidates"
            self.store.settings = self.settings
            if not await self.store.has_vectors(context, plan, profile[1],
                    self.phase_budget(budget, float(self.settings.get("vector_sql_seconds", VECTOR_SQL_SECONDS)))):
                return [], ()
            remote = self.remote_phase(budget, seconds)
            if remote is None:
                return [], ("embedding_budget_insufficient",)
            phase = "embedding"
            remote_budget, queue_wait = remote
            values, fingerprint = await self.embeddings.vector(context, plan.query, remote_budget,
                intent="memory-query:" + str(uuid4()), query=True, profile_snapshot=profile,
                max_queue_wait=queue_wait, execution_seconds=seconds,
                business_deadline_at=remote_budget.deadline_at if budget is not None else None,
                service_class=service_class, source_refs=source_refs)
            phase = "vector"
            rows, incomplete = await self.store.vectors(context, plan, values, fingerprint,
                self.phase_budget(budget, float(self.settings.get("vector_sql_seconds", VECTOR_SQL_SECONDS))))
            return rows, ("vector_candidate_budget",) if incomplete else ()
        except PHASE_ERRORS as exc:
            self._degrade(exc, budget, model=phase in {"embedding_profile", "embedding"})
            reason = ("vector_query_timeout" if phase == "vector" and isinstance(
                exc, (TimeoutError, psycopg.errors.QueryCanceled)) else getattr(exc, "code", phase + "_unavailable"))
            return [], (reason,)

    async def _hydrate_on(self, conn, context, plan, rows):
        selected = await select_supports_on(self.memory.sources, conn, context, rows)
        usable = [(row, refs) for row, refs in zip(rows, selected) if refs is not None]
        links = await self.memory.claims.revisions.links.for_records_on(conn, context,
            [RecordRef(row["_kind"], row["_id"], row["revision"]) for row, _ in usable],
            historical=plan.time_mode != "current", known_at=plan.known_at)
        moment = plan.valid_at or plan.known_at or datetime.now(UTC)
        result = []
        for row, refs in usable:
            related = tuple(link for link in links.get(row["_id"], ())
                if (link.valid_from is None or link.valid_from <= moment)
                and (link.valid_until is None or moment < link.valid_until))
            if any(link.relation_kind == "supersedes" and link.to_ref.kind == row["_kind"]
                   and link.to_ref.id == row["_id"] for link in related):
                continue
            if plan.matter_refs and row["_kind"] in {"claim", "hypothesis"} and not any(
                link.relation_kind == "about_matter" and any(endpoint.kind == "matter" and
                    endpoint.id in plan.matter_refs for endpoint in (link.from_ref, link.to_ref))
                for link in related):
                continue
            public = {**row, "scope_ids": sorted(set(row["scope_ids"]) & set(context.scope_ids))}
            if row["_kind"] in {"claim", "hypothesis"}:
                value = codec.claim(public, refs, relations=related)
            elif row["_kind"] == "matter":
                claim_ids = tuple(endpoint.id for link in related if link.relation_kind == "about_matter"
                    for endpoint in (link.from_ref, link.to_ref) if endpoint.kind in {"claim", "hypothesis"})
                value = codec.matter(public, claim_ids, refs, relations=related)
            else:
                public["source_refs"] = refs
                value = codec.episode(public) if row["_kind"] == "episode" else codec.reflection(public)
            result.append((row, build_evidence_unit(value, relations=related, time_mode=plan.time_mode)))
        return result

    async def _checked_on(self, conn, context, plan, rows, *, snapshot=None):
        reread = await self.store.reread(conn, context, plan, rows, snapshot=snapshot)
        missing = ["memory_revision_changed"] if any(row is None for row in reread) else []
        hydrated = await self._hydrate_on(conn, context, plan, [row for row in reread if row is not None])
        if len(hydrated) < sum(row is not None for row in reread):
            missing.append("memory_evidence_unavailable")
        displayed = await display_evidence_on(self.memory, conn, context, [unit for _, unit in hydrated])
        if any(unit is None for unit in displayed):
            missing.append("memory_evidence_unavailable")
        return tuple((row, unit) for (row, _), unit in zip(hydrated, displayed, strict=True)
                     if unit is not None), tuple(missing)

    async def _checked(self, context, plan, rows, budget, *, snapshot=None, limit=None,
                       partial=False, parent=None, phase="memory_evidence_check"):
        """Read the selected versions and their complete relations again after ranking."""
        if not rows:
            return (), ()
        maximum = len(rows) if limit is None else limit
        selected, missing, iterator = [], [], iter(rows)
        try:
            # One transaction/deadline for the whole fill, including replacement
            # candidates. Background reads retain Database's existing bound.
            async with self.database.transaction(budget, read_only=True) as conn:
                await conn.execute("SELECT set_config('jit','off',true)")
                while len(selected) < maximum:
                    if budget is not None:
                        budget.check()
                    batch = tuple(islice(iterator, maximum - len(selected)))
                    if not batch:
                        break
                    checked, unavailable = await self._checked_on(conn, context, plan, batch, snapshot=snapshot)
                    selected.extend(checked)
                    missing.extend(unavailable)
        except PHASE_ERRORS as exc:
            if not partial:
                raise
            self._degrade(exc, parent)
            missing.append(phase + ("_timeout" if isinstance(exc, (TimeoutError, psycopg.errors.QueryCanceled))
                                    else "_unavailable"))
        return tuple(selected), tuple(dict.fromkeys(missing))

    async def _ranked(self, context, plan, budget, *, limit, source_refs, service_class):
        missing = []
        try:
            selection = await self.store.lexical(context, plan, self.local_budget(budget))
        except PHASE_ERRORS as exc:
            self._degrade(exc, budget)
            reason = ("lexical_candidate_timeout" if isinstance(exc, (TimeoutError, psycopg.errors.QueryCanceled))
                      else "lexical_candidate_unavailable")
            selection = CandidateSelection((), (reason,))
        missing.extend(selection.missing)
        if budget is not None:
            budget.check()
        vector_rows = []
        if plan.query and self.settings.get("vector", True):
            vector_rows, unavailable = await self._vectors(context, plan, budget, source_refs, service_class)
            missing.extend(unavailable)
        fused = {}
        for route in (selection.rows, vector_rows):
            for rank, row in enumerate(route):
                current = fused.setdefault(record_key(row), {**row, "score": 0.0})
                current["score"] += 1 / (60 + rank + 1)
                if row.get("vector") is not None:
                    current["vector"] = row["vector"]
        maximum = max((row["score"] for row in fused.values()), default=1)
        for row in fused.values():
            row["score"] /= maximum
        count = min(int(plan.budget.get("max_candidates", 128)), 128)
        candidates = _MmrOrder(sorted(fused.values(), key=lambda row: row["score"], reverse=True)[:count])
        if not candidates:
            return (), tuple(dict.fromkeys(missing))
        ranked, remote = [], None
        if plan.query and self.settings.get("rerank", False) and len(candidates) > 1:
            remote = self.remote_phase(budget, float(self.settings.get("rerank_seconds", MODEL_EXECUTION_SECONDS)))
            if remote is None:
                missing.append("rerank_budget_insufficient")
            else:
                checked, unavailable = await self._checked(context, plan, candidates, self.local_budget(budget),
                    limit=max(limit, 24), partial=True, parent=budget, phase="memory_candidate_check")
                missing.extend(unavailable)
                ranked = list(checked)
                remote = (self.remote_phase(budget, float(self.settings.get("rerank_seconds", MODEL_EXECUTION_SECONDS)))
                          if len(ranked) > 1 else None)
                if len(ranked) > 1 and remote is None:
                    missing.append("rerank_budget_insufficient")
        if remote is not None:
            remote_budget, queue_wait = remote
            documents = [rerank_text(unit) for _, unit in ranked]
            request = ModelRequest("memory-rerank:" + str(uuid4()), str(uuid4()), "rerank",
                service_class, context, remote_budget, (), required_capabilities=frozenset({"rerank"}),
                max_output_tokens=0, native_payload={"query": plan.query, "documents": documents, "top_n": limit},
                execution_timeout_seconds=float(self.settings.get("rerank_seconds", MODEL_EXECUTION_SECONDS)),
                business_deadline_at=remote_budget.deadline_at if budget is not None else None,
                source_refs=unique_refs(source_refs, *(unit.source_refs for _, unit in ranked)))
            request = replace(request, max_queue_wait=queue_wait)
            try:
                result = await self.models.generate(request)
                if result.receipt.status != "succeeded":
                    cancelled = getattr(result.receipt, "cancellation_reason", None) == "explicit_cancel"
                    raise AppError("explicit_cancel" if cancelled else result.receipt.error_code or "model_outcome_" + result.receipt.status,
                                   status=503)
                indexes = (result.native_response or {}).get("results", ())
                order, seen = [], set()
                for item in indexes:
                    index = item.get("index") if isinstance(item, dict) else None
                    if type(index) is int and 0 <= index < len(ranked) and index not in seen:
                        seen.add(index)
                        order.append(ranked[index])
                if order:
                    ranked = order + [value for index, value in enumerate(ranked) if index not in seen]
                else:
                    missing.append("rerank_unavailable")
            except PHASE_ERRORS as exc:
                self._degrade(exc, budget, model=True)
                missing.append("rerank_unavailable")
        # Rerank can outlive a correction, endpoint withdrawal or permission change.
        # Rehydration keeps the final answer and its source closure indivisible.
        preferred = [row for row, _ in ranked]
        chosen = {record_key(row) for row in preferred}
        # A fresh iterator replays every cached prefix row, including candidates
        # whose preparation timed out before they were completely checked.
        remaining = (row for row in candidates if record_key(row) not in chosen)
        final, unavailable = await self._checked(context, plan, chain(preferred, remaining), self.local_budget(budget),
            limit=limit, partial=True, parent=budget, phase="memory_final_check")
        missing.extend(unavailable)
        return tuple(unit for _, unit in final), tuple(dict.fromkeys(missing))

    async def query(self, context, plan, budget, *, source_refs=(), cursor=None, limit=50,
                    service_class=ServiceClass.INTERACTIVE):
        if budget is not None:
            budget.check()
        plan = parse_plan(plan)
        await self.memory.identity.check(context, source_refs)
        if not context.actor_id:
            raise AppError("actor_required")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise AppError("invalid_memory_query_limit")
        try:
            missing = list(await self.store.coverage_gaps(context, plan, self.local_budget(budget)))
        except PHASE_ERRORS as exc:
            if plan.coverage_mode == "list":
                raise
            self._degrade(exc, budget)
            missing = ["memory_coverage_timeout" if isinstance(exc, (TimeoutError, psycopg.errors.QueryCanceled))
                       else "memory_coverage_unavailable"]
        next_cursor = None
        if plan.coverage_mode == "list":
            after, snapshot = read_cursor(context, plan, cursor)
            rows = await self.store.page(context, plan, self.local_budget(budget), after=after, snapshot=snapshot, limit=limit)
            checked, unavailable = await self._checked(context, plan, rows[:limit], self.local_budget(budget), snapshot=snapshot)
            missing.extend(unavailable)
            units = tuple(unit for _, unit in checked)
            if len(rows) > limit:
                next_cursor = write_cursor(context, plan, rows[limit - 1], snapshot)
        else:
            if cursor is not None:
                raise AppError("memory_ranked_query_has_no_cursor")
            units, unavailable = await self._ranked(context, plan, budget, limit=min(limit, 32),
                source_refs=source_refs, service_class=service_class)
            missing.extend((*unavailable, "ranked_selection"))
            rows, checked = (), ()
        maximum = plan.budget.get("max_tokens")
        if maximum:
            fitted, consumed = [], 0
            for unit in units:
                # Conservative UTF-8 token upper bound also used by the default assembler.
                size = len(evidence_text(unit).encode("utf-8")) + 32
                if consumed + size > maximum:
                    if not fitted:
                        raise AppError("memory_evidence_unit_budget_exceeded", status=413,
                            details={"kind": unit.kind, "id": unit.id, "revision": unit.revision,
                                     "minimum_tokens": size, "whole_units": True})
                    missing.append("memory_query_budget")
                    if plan.coverage_mode == "list":
                        prior = next(row for row, value in checked if value.id == fitted[-1].id and value.kind == fitted[-1].kind)
                        next_cursor = write_cursor(context, plan, prior, snapshot)
                    break
                fitted.append(unit)
                consumed += size
            units = tuple(fitted)
        for unit in units:
            missing.extend(unit.coverage.missing)
        missing = tuple(dict.fromkeys(missing))
        if budget is not None:
            budget.check()
        return Page(units, next_cursor, Coverage(plan.coverage_mode == "list" and not next_cursor and not missing,
            missing=missing, representation="memory_evidence",
            ranges=({"selection": plan.coverage_mode, "time_mode": plan.time_mode,
                     "evidence_kinds": plan.evidence_kinds, "returned": len(units),
                     "as_of": snapshot.isoformat() if plan.coverage_mode == "list" else None,
                     "literal_query_filter": bool(plan.query) and plan.coverage_mode == "list"},)))

    async def candidates(self, context, query, budget, *, subject_ids=(), matter_refs=(),
                         kinds=("claim", "hypothesis", "matter"), limit=32):
        # This port is the consolidation background read. Its old 55-second
        # preparation clock is not a business expiry. Each DB/profile/provider
        # phase keeps its own execution bound; waiting for Models consumes none.
        return await self.query(context, MemoryQueryPlan(query=query, subject_ids=tuple(subject_ids),
            matter_refs=tuple(matter_refs), evidence_kinds=tuple(kinds)), None,
            limit=min(max(int(limit), 1), 32), service_class=ServiceClass.ELASTIC)

    async def revalidate(self, context, records, budget):
        """Recheck current automatic context material without rebinding a stale version."""
        budget.check()
        await self.memory.identity.check(context)
        if len(records) > 128:
            raise AppError("memory_context_candidates_too_large")
        kinds = tuple(dict.fromkeys(record.kind for record in records))
        if not kinds:
            return (), ()
        plan = parse_plan(MemoryQueryPlan(evidence_kinds=kinds))
        rows = [{"_kind": record.kind, "_id": record.id, "revision": record.revision} for record in records]
        values, missing = await self._checked(context, plan, rows, budget)
        return tuple(unit for _, unit in values), missing

    async def search(self, context, query, budget, *, limit=12, source_refs=()):
        """Existing context API, now backed solely by complete multi-type evidence."""
        page = await self.query(context, MemoryQueryPlan(query=query), budget,
                                source_refs=source_refs, limit=min(max(int(limit), 1), 32))
        return contributions(page.items), tuple(item for item in page.coverage.missing if item != "ranked_selection")
