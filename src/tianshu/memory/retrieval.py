"""One multi-type evidence query for chat, tools and bounded consolidation reads."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from itertools import chain, islice
import math
from uuid import uuid4

import aiohttp
import psycopg

from tianshu.models.types import ModelRequest
from tianshu.runtime.api import Page
from tianshu.runtime.commands import body_hash
from tianshu.runtime.errors import AppError
from tianshu.runtime.jobs import current_execution_generation
from tianshu.sources.dependencies import unique_refs
from tianshu.types import ContextContribution, Coverage, RequestBudget, ServiceClass
from . import codec
from .candidates import CandidateSelection, CandidateStore
from .candidate_sources import SourceProofGroups, check_group_batches_on, check_rows_on
from .domain_utils import lexical
from .display import display_evidence_on
from .embedding import normalized_input
from .episode_original import needs_original_text, read_original_text
from .evidence import build_evidence_unit, select_supports_on
from .indexing import evidence_text, index_text, rerank_text
from .learning_index import LearningEvidence
from .query_plan import parse_plan, read_cursor, write_cursor
from .retrieval_budget import (MODEL_EXECUTION_SECONDS, PROFILE_READ_SECONDS, SQL_READ_SECONDS,
                               VECTOR_SQL_SECONDS, foreground_budget as make_foreground_budget, optional_failure)
from .types import MemoryQueryPlan, RecordRef


PHASE_ERRORS = (AppError, TimeoutError, aiohttp.ClientError, psycopg.Error, OSError)


class _VectorRows(list):
    def __init__(self, rows=(), profile_key=None):
        super().__init__(rows)
        self.profile_key = profile_key


class _CheckedPairs(tuple):
    def __new__(cls, values, examined_keys):
        result = super().__new__(cls, values)
        selected_keys = {record_key(row) for row, _ in result}
        result.examined = len(examined_keys)
        result.rejected_keys = frozenset(key for key in examined_keys if key not in selected_keys)
        return result


class _RankedUnits(tuple):
    def __new__(cls, values, diagnostics):
        result = super().__new__(cls, values)
        result.diagnostics = diagnostics
        return result


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
    priorities = {"matter": 75, "claim": 60, "hypothesis": 55, "episode": 50, "learning": 40}
    return tuple(ContextContribution("memory_evidence", evidence_text(unit), unit.source_refs,
        unit.scope_ids, priorities[unit.kind], False, f"{unit.kind}:{unit.id}:{unit.revision}",
        material_id=("behavior:" + unit.id if getattr(unit, "behavior_ref", None) else
                     "learning:reflection:" + unit.id if getattr(unit, "learning_ref", None) else None),
        semantic_role=("behavior_reference" if getattr(unit, "behavior_ref", None) else
                       "learning_reference" if getattr(unit, "learning_ref", None) else None),
        behavior_ref=getattr(unit, "behavior_ref", None) or None,
        behavior_refs=getattr(unit, "behavior_refs", ()))
        for unit in units)


def unique_behavior_refs(*groups):
    references = {}
    for group in groups:
        for reference in group:
            references.setdefault(body_hash(reference), reference)
    return tuple(references.values())


def behavior_result(resolved, reference):
    """Match the requested version; the resolver may sort repeated rule IDs."""
    def field(value, name, default=None):
        return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)
    matches = [value for value in resolved if field(value, "rule_id") == reference["rule_id"]]
    exact = [value for value in matches if field(value, "requested_revision") == reference.get("revision") and
             field(value, "requested_semantic_revision") == reference.get("semantic_revision")]
    if exact:
        return exact[0]
    # Older adapters may omit requested_* when there is no ambiguity.
    return matches[0] if len(matches) == 1 and field(matches[0], "requested_revision") is None and \
        field(matches[0], "requested_semantic_revision") is None else None


class MemoryRetrieval:
    def __init__(self, memory, embeddings, settings=None, *, behavior_resolver=None, learning_resolver=None):
        self.memory, self.embeddings = memory, embeddings
        self.database, self.models = memory.database, memory.models
        self.settings = settings or {}
        self.store = CandidateStore(memory, self.settings)
        self.behavior_resolver = behavior_resolver
        self.learning_resolver = learning_resolver

    def bind_behavior_resolver(self, resolver):
        """Bind Growth's public resolve_behaviors port during product assembly."""
        self.behavior_resolver = resolver

    def bind_learning_resolver(self, resolver):
        """Bind Growth's typed learning_records port for reflection evidence."""
        self.learning_resolver = resolver

    async def index_readiness(self, context, records, budget=None, *, profile_key=None):
        """Report actual projections for authorized current revisions, in input order."""
        records = tuple(records)
        if len(records) > 128:
            raise AppError("memory_index_readiness_batch_too_large")
        keys = []
        for ref in records:
            get = ref.get if isinstance(ref, dict) else lambda name, default=None: getattr(ref, name, default)
            kind, identifier, revision = get("kind"), get("id"), get("revision")
            if (kind not in {"claim", "hypothesis", "matter", "episode"} or
                    not isinstance(identifier, str) or not identifier or type(revision) is not int or revision < 1):
                raise AppError("invalid_memory_index_readiness_reference")
            keys.append((kind, identifier, revision))
        if profile_key is None:
            _, profile_key = await self.embeddings.profile()
        if not isinstance(profile_key, str) or len(profile_key) != 64 or any(c not in "0123456789abcdef" for c in profile_key):
            raise AppError("invalid_memory_embedding_profile")
        if not keys:
            return ()
        plan = MemoryQueryPlan(evidence_kinds=("claim", "hypothesis", "matter", "episode"))
        requested = [{"_kind": kind, "_id": identifier, "revision": revision} for kind, identifier, revision in keys]
        async with self.database.transaction(budget) as conn:
            await self.memory.identity.check_context(context, connection=conn)
            generation = await self.memory.jobs.current_generation_on(conn)
            executing = current_execution_generation()
            if executing is not None:
                await self.memory.jobs.assert_generation_on(conn, executing)
            rows = await self.store.reread(conn, context, plan, requested)
            present_rows = [row for row in rows if row is not None]
            supports = await select_supports_on(self.memory.sources, conn, context, present_rows)
            groups, supplements = SourceProofGroups(), {}
            for row in present_rows:
                if row["_kind"] == "episode":
                    refs = unique_refs(codec.refs(row.get("supplement_refs")), codec.refs(row.get("_head_supplement_refs")))
                    if refs:
                        supplements[record_key(row)] = groups.add(refs)
            checked = await check_group_batches_on(self.memory.sources, conn, context, groups.values)
            authorized = {}
            for row, proof in zip(present_rows, supports, strict=True):
                key = record_key(row)
                if proof is None or not all(checked[index] is True for index in supplements.get(key, ())):
                    continue
                value = (codec.claim(row, proof) if row["_kind"] in {"claim", "hypothesis"} else
                         codec.matter(row, selected_refs=proof) if row["_kind"] == "matter" else codec.episode(row))
                unit = build_evidence_unit(value)
                if row["_kind"] == "episode" and needs_original_text(row):
                    unit = replace(unit, content=row.get("lexical") or "")
                authorized[key] = body_hash(normalized_input(index_text(unit)))
            kinds, identifiers, revisions = zip(*keys)
            params = (profile_key, generation, list(kinds), list(identifiers), list(revisions))
            vectors = await (await conn.execute(
                "SELECT kind,object_id,revision,content_hash FROM memory.search_vectors WHERE profile_key=%s AND generation=%s "
                "AND (kind,object_id,revision) IN (SELECT * FROM unnest(%s::text[],%s::text[],%s::bigint[]))", params)).fetchall()
            jobs = await (await conn.execute(
                "SELECT kind,object_id,revision,content_hash,state FROM memory.index_jobs WHERE profile_key=%s AND generation=%s "
                "AND (kind,object_id,revision) IN (SELECT * FROM unnest(%s::text[],%s::text[],%s::bigint[]))", params)).fetchall()
            vector_keys = {(row["kind"], row["object_id"], row["revision"], row["content_hash"]) for row in vectors}
            job_states = {(row["kind"], row["object_id"], row["revision"], row["content_hash"]): row["state"] for row in jobs}
            await self.memory.jobs.assert_generation_on(conn, generation)
        return tuple({"kind": kind, "id": identifier, "revision": revision, "profile_key": profile_key,
            "generation": generation, "state": ("unavailable" if key not in authorized else
                "ready" if (*key, authorized[key]) in vector_keys else
                "failed" if job_states.get((*key, authorized[key])) in {"failed", "cancelled", "revoked", "obsolete"} else "pending")}
            for key in keys for kind, identifier, revision in (key,))

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

    async def _vectors(self, context, plan, budget, source_refs, service_class, *, data_generation=None):
        seconds = float(self.settings.get("embedding_seconds", MODEL_EXECUTION_SECONDS))
        if self.remote_phase(budget, seconds) is None:
            return _VectorRows(), ("embedding_budget_insufficient",)
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
                return _VectorRows(profile_key=profile[1]), ()
            remote = self.remote_phase(budget, seconds)
            if remote is None:
                return _VectorRows(profile_key=profile[1]), ("embedding_budget_insufficient",)
            phase = "embedding"
            remote_budget, queue_wait = remote
            values, fingerprint = await self.embeddings.vector(context, plan.query, remote_budget,
                intent="memory-query:" + str(uuid4()), query=True, profile_snapshot=profile,
                max_queue_wait=queue_wait, execution_seconds=seconds,
                business_deadline_at=remote_budget.deadline_at if budget is not None else None,
                service_class=service_class, source_refs=source_refs, data_generation=data_generation)
            phase = "vector"
            rows, incomplete = await self.store.vectors(context, plan, values, fingerprint,
                self.phase_budget(budget, float(self.settings.get("vector_sql_seconds", VECTOR_SQL_SECONDS))))
            if budget is not None:
                budget.check()
            return _VectorRows(rows, fingerprint), ("vector_candidate_budget",) if incomplete else ()
        except PHASE_ERRORS as exc:
            if (phase == "vector_candidates" and isinstance(exc, AppError)
                    and exc.code == "memory_vector_candidate_budget" and exc.status == 503):
                if budget is not None:
                    budget.check()
                return _VectorRows(), ("vector_candidate_budget",)
            self._degrade(exc, budget, model=phase in {"embedding_profile", "embedding"})
            reason = ("vector_query_timeout" if phase == "vector" and isinstance(
                exc, (TimeoutError, psycopg.errors.QueryCanceled)) else getattr(exc, "code", phase + "_unavailable"))
            return _VectorRows(), (reason,)

    async def _hydrate_on(self, conn, context, plan, rows):
        selected = await select_supports_on(self.memory.sources, conn, context, rows)
        usable = [(row, refs) for row, refs in zip(rows, selected) if refs is not None]
        groups, supplement_proofs = SourceProofGroups(), {}
        for row, _ in usable:
            if row["_kind"] == "episode":
                supplements = unique_refs(codec.refs(row.get("supplement_refs")),
                                          codec.refs(row.get("_head_supplement_refs")))
                if supplements:
                    supplement_proofs[record_key(row)] = (supplements, groups.add(supplements))
        supplement_checks = await check_group_batches_on(self.memory.sources, conn, context, groups.values)
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
            elif row["_kind"] == "episode":
                public["source_refs"] = refs
                value = codec.episode(public)
            else:
                raise AppError("invalid_memory_evidence_kind")
            unit = build_evidence_unit(value, relations=related, time_mode=plan.time_mode)
            supplement = supplement_proofs.get(record_key(row))
            if supplement:
                dependencies, checks = supplement
                if all(supplement_checks[index] is True for index in checks):
                    unit = replace(unit, source_refs=unique_refs(unit.source_refs, dependencies),
                                   derivation_refs=unique_refs(unit.derivation_refs, dependencies))
                else:
                    # The summary may contain withdrawn image understanding.
                    # Recover only the authorized original text outside this DB
                    # transaction; never present the stale derived summary.
                    row = {**row, "_supplement_unavailable": True}
                    coverage = replace(unit.coverage, complete=False,
                        missing=tuple(dict.fromkeys((*unit.coverage.missing, "media_unavailable"))))
                    unit = replace(unit, content="", actual_result=None, coverage=coverage)
            if row["_kind"] == "episode" and needs_original_text(row):
                row = {**row, "_supplement_unavailable": True}
                reason = "media_unavailable" if "media_unavailable" in (row.get("extraction_missing") or ()) else "original_text_fallback"
                coverage = replace(unit.coverage, complete=False,
                    missing=tuple(dict.fromkeys((*unit.coverage.missing, reason))))
                unit = replace(unit, content="", actual_result=None, coverage=coverage)
            result.append((row, unit))
        return result

    async def _original_episode_units(self, context, pairs, budget):
        """Restore authorized original text when an attachment supplement is gone."""
        result, missing = [], []
        for row, unit in pairs:
            refs = codec.refs(row["source_refs"])
            content, partial = "", False
            try:
                content, partial = await read_original_text(self.memory.sources, context, refs, budget)
            except AppError as error:
                if error.status in {401, 403, 404}:
                    missing.append("memory_evidence_unavailable")
                    continue
                if error.status < 500 and error.status not in {408, 429}:
                    raise
                partial = True
            except (TimeoutError, aiohttp.ClientError, OSError):
                partial = True
            reasons = list(unit.coverage.missing)
            if not content:
                reasons.append("original_text_unavailable")
            elif partial:
                reasons.append("original_text_partial")
            coverage = replace(unit.coverage, complete=False, missing=tuple(dict.fromkeys(reasons)))
            result.append((row, replace(unit, content=content, coverage=coverage)))
        return tuple(result), tuple(dict.fromkeys(missing))

    async def _checked_on(self, conn, context, plan, rows, *, snapshot=None, derivation_refs=(), behavior_refs=()):
        if not rows:
            return (), ()
        reread = await self.store.reread(conn, context, plan, rows, snapshot=snapshot)
        missing = ["memory_revision_changed"] if any(row is None for row in reread) else []
        hydrated = await self._hydrate_on(conn, context, plan, [row for row in reread if row is not None])
        if len(hydrated) < sum(row is not None for row in reread):
            missing.append("memory_evidence_unavailable")
        values = [replace(unit, source_refs=unique_refs(unit.source_refs, derivation_refs),
                          derivation_refs=unique_refs(unit.derivation_refs, derivation_refs),
                          behavior_refs=unique_behavior_refs(getattr(unit, "behavior_refs", ()), behavior_refs))
                  if derivation_refs or behavior_refs else unit for _, unit in hydrated]
        displayed = list(await display_evidence_on(self.memory, conn, context, values))
        # If only a supplement was withdrawn between hydration and display,
        # retain the original episode instead of dropping the entire item.
        retry, positions = [], []
        for index, ((row, unit), displayed_unit) in enumerate(zip(hydrated, displayed, strict=True)):
            if displayed_unit is not None or row["_kind"] != "episode":
                continue
            supplements = unique_refs(codec.refs(row.get("supplement_refs")), codec.refs(row.get("_head_supplement_refs")))
            if not supplements:
                continue
            suppressed = {body_hash(ref) for ref in supplements}
            coverage = replace(unit.coverage, complete=False,
                missing=tuple(dict.fromkeys((*unit.coverage.missing, "media_unavailable"))))
            retry.append(replace(unit, content="", actual_result=None, coverage=coverage,
                source_refs=unique_refs((ref for ref in unit.source_refs if body_hash(ref) not in suppressed), derivation_refs),
                derivation_refs=unique_refs((ref for ref in unit.derivation_refs if body_hash(ref) not in suppressed), derivation_refs),
                behavior_refs=unique_behavior_refs(getattr(unit, "behavior_refs", ()), behavior_refs)))
            positions.append(index)
        if retry:
            restored = await display_evidence_on(self.memory, conn, context, retry)
            for index, unit in zip(positions, restored, strict=True):
                if unit is not None:
                    row, _ = hydrated[index]
                    hydrated[index] = ({**row, "_supplement_unavailable": True}, unit)
                    displayed[index] = unit
        if any(unit is None for unit in displayed):
            missing.append("memory_evidence_unavailable")
        return tuple((row, unit) for (row, _), unit in zip(hydrated, displayed, strict=True)
                     if unit is not None), tuple(missing)

    async def _learning_units(self, context, plan, rows, purpose, derivation_refs, behavior_refs=(), budget=None):
        if not rows:
            return (), ()
        def field(value, name, default=None):
            return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)
        behavior_rows = tuple(row for row in rows if row.get("kind") != "reflection")
        reflection_rows = tuple(row for row in rows if row.get("kind") == "reflection")
        behavior_refs_to_read = tuple({"rule_id": row["_id"], "revision": row["revision"],
            "semantic_revision": row["semantic_revision"]} for row in behavior_rows)
        reflection_refs = tuple({"kind": "reflection", "id": row["_id"]} for row in reflection_rows)
        missing, results = [], {}
        for family, resolver, refs in (("behavior", self.behavior_resolver, behavior_refs_to_read),
                                       ("reflection", self.learning_resolver, reflection_refs)):
            if not refs:
                continue
            if resolver is None:
                missing.append("learning_resolver_unavailable:" + family)
                continue
            try:
                if budget is None:
                    resolved = await resolver(context, refs, purpose=purpose)
                else:
                    async with budget.timeout():
                        resolved = await resolver(context, refs, purpose=purpose)
            except AppError as error:
                if error.status < 500 and error.status not in {408, 429}:
                    raise
                missing.append("learning_resolver_unavailable:" + family)
                continue
            except (TimeoutError, aiohttp.ClientError, OSError):
                missing.append("learning_resolver_unavailable:" + family)
                continue
            results[family] = tuple(resolved.get("items", ())) if isinstance(resolved, dict) else tuple(resolved)
        result = []
        for row in rows:
            reflection = row.get("kind") == "reflection"
            family = "reflection" if reflection else "behavior"
            if family not in results:
                continue
            reference = ({"kind": "reflection", "id": row["_id"], "revision": row["revision"]} if reflection else
                {"rule_id": row["_id"], "revision": row["revision"], "semantic_revision": row["semantic_revision"]})
            value = (next((item for item in results[family] if field(item, "kind") == "reflection" and
                           field(item, "id") == row["_id"]), None) if reflection else
                     behavior_result(results[family], reference))
            if value is None:
                missing.append("learning_reference_unresolved")
                continue
            if reflection and field(value, "state") != "active":
                continue
            if not reflection and (field(value, "eligible") is not True or
                    (purpose != "history" and field(value, "compatible") is not True)):
                continue
            actual_revision = (field(value, "revision") if reflection else
                field(value, "historical_revision", field(value, "revision")))
            if (reflection or purpose == "history") and actual_revision != row["revision"]:
                # A current body cannot represent an earlier indexed revision.
                missing.append("learning_revision_history_unavailable")
                continue
            occurred_at = field(value, "occurred_at") if reflection else None
            if reflection and plan.time_mode == "valid_at":
                if occurred_at is not None and occurred_at > plan.valid_at:
                    continue
                missing.append("learning_valid_time_unknown")
            subject_id = field(value, "subject_id")
            if plan.subject_ids and subject_id not in plan.subject_ids:
                continue
            content, sources = field(value, "content" if reflection else "text"), field(value, "source_refs")
            if not isinstance(content, str) or not sources:
                missing.append("learning_material_unavailable")
                continue
            scopes = tuple(sorted(set(row["scope_ids"]) | set(field(value, "scope_ids", ()))))
            if not set(scopes) <= set(context.scope_ids):
                continue
            closure = unique_refs(codec.refs(row["source_refs"]), codec.refs(sources), derivation_refs)
            state = field(value, "state", "unknown")
            unit = LearningEvidence(kind="learning", id=row["_id"], revision=row["revision"],
                actor_id=context.actor_id, subject_id=subject_id, speaker_id="actor:" + context.actor_id,
                assertion="inferred", content=content, conditions=field(value, "conditions", {}) or {},
                occurred_at=occurred_at, valid_from=None, valid_until=None, recorded_at=row["recorded_at"],
                matter_id=None, current_status=state, state=state, source_refs=closure, scope_ids=scopes,
                coverage=Coverage(True, representation="memory_evidence"), interpretation=True,
                derivation_refs=tuple(derivation_refs), behavior_ref={} if reflection else reference,
                learning_ref=reference if reflection else {}, behavior_refs=tuple(behavior_refs))
            result.append((row, unit))
        return tuple(result), tuple(dict.fromkeys(missing))

    async def _checked(self, context, plan, rows, budget, *, snapshot=None, limit=None,
                       partial=False, parent=None, phase="memory_evidence_check", purpose="conversation",
                       derivation_refs=(), behavior_refs=()):
        """Read the selected versions and their complete relations again after ranking."""
        if not rows:
            return (), ()
        maximum = len(rows) if limit is None else limit
        selected, missing, iterator, examined = [], [], iter(rows), []
        try:
            while len(selected) < maximum:
                learning, checked, fallback = (), (), ()
                async with self.database.transaction(budget, read_only=True) as conn:
                    await conn.execute("SELECT set_config('jit','off',true)")
                    # Keep ordinary replacement batches on one connection. A
                    # Growth call happens outside this transaction so a small
                    # connection pool cannot deadlock on cross-domain reads.
                    while len(selected) < maximum:
                        if budget is not None:
                            budget.check()
                        batch = tuple(islice(iterator, maximum - len(selected)))
                        if not batch:
                            return _CheckedPairs(selected, examined), tuple(dict.fromkeys(missing))
                        examined.extend(record_key(row) for row in batch)
                        ordinary = tuple(row for row in batch if row["_kind"] != "learning")
                        references = tuple(row for row in batch if row["_kind"] == "learning")
                        checked, unavailable = await self._checked_on(conn, context, plan, ordinary,
                            snapshot=snapshot, derivation_refs=derivation_refs, behavior_refs=behavior_refs)
                        missing.extend(unavailable)
                        fallback = tuple(item for item in checked if item[0].get("_supplement_unavailable"))
                        if not references and not fallback:
                            selected.extend(checked)
                            continue
                        fresh = await self.store.reread(conn, context, plan, references, snapshot=snapshot) if references else ()
                        if any(row is None for row in fresh):
                            missing.append("memory_revision_changed")
                        learning = tuple(row for row in fresh if row is not None)
                        break
                if len(selected) >= maximum:
                    return _CheckedPairs(selected, examined), tuple(dict.fromkeys(missing))
                resolved, unavailable = await self._learning_units(context, plan, learning, purpose, derivation_refs,
                                                                   behavior_refs, budget=budget)
                missing.extend(unavailable)
                restored, unavailable = await self._original_episode_units(context, fallback, budget)
                missing.extend(unavailable)
                if resolved or restored:
                    async with self.database.transaction(budget, read_only=True) as conn:
                        reread_values = (*resolved, *restored)
                        latest = await self.store.reread(conn, context, plan, [row for row, _ in reread_values], snapshot=snapshot)
                        current = [(row, unit) for (row, unit), fresh in zip(reread_values, latest, strict=True) if fresh is not None]
                        if len(current) != len(reread_values):
                            missing.append("memory_revision_changed")
                        displayed = await display_evidence_on(self.memory, conn, context, [unit for _, unit in current])
                        resolved = tuple((row, unit) for (row, _), unit in zip(current, displayed, strict=True) if unit is not None)
                        if len(resolved) != len(current):
                            missing.append("memory_evidence_unavailable")
                fallback_keys = {record_key(row) for row, _ in fallback}
                values = {record_key(row): (row, unit) for row, unit in checked if record_key(row) not in fallback_keys}
                values.update({record_key(row): (row, unit) for row, unit in resolved})
                selected.extend(values[record_key(row)] for row in batch if record_key(row) in values)
        except PHASE_ERRORS as exc:
            if not partial:
                raise
            self._degrade(exc, parent)
            missing.append(phase + ("_timeout" if isinstance(exc, (TimeoutError, psycopg.errors.QueryCanceled))
                                    else "_unavailable"))
        return _CheckedPairs(selected, examined), tuple(dict.fromkeys(missing))

    async def _derivation_valid(self, context, refs, budget):
        if not refs:
            return True
        async with self.database.transaction(budget, read_only=True) as conn:
            proof = {"_supports": [refs], "_derivations": (), "_explicit": (), "_relation_proofs": None}
            return (await check_rows_on(self.memory.sources, conn, context, (proof,)))[0] is True

    async def _behavior_dependencies_valid(self, context, refs, purpose, budget=None):
        if not refs:
            return True
        if self.behavior_resolver is None:
            return False
        try:
            if budget is None:
                resolved = await self.behavior_resolver(context, refs, purpose=purpose)
            else:
                async with budget.timeout():
                    resolved = await self.behavior_resolver(context, refs, purpose=purpose)
        except AppError as error:
            if error.status < 500 and error.status not in {408, 429}:
                raise
            return False
        except (TimeoutError, aiohttp.ClientError, OSError):
            return False
        if isinstance(resolved, dict):
            resolved = resolved.get("items", ())
        def field(value, name, default=None):
            return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)
        return all((value := behavior_result(resolved, ref)) is not None and field(value, "eligible") is True and
                   field(value, "compatible") is True for ref in refs)

    async def _lexical(self, context, plan, budget):
        try:
            return await self.store.lexical(context, plan, self.local_budget(budget))
        except PHASE_ERRORS as exc:
            self._degrade(exc, budget)
            reason = ("lexical_candidate_timeout" if isinstance(exc, (TimeoutError, psycopg.errors.QueryCanceled))
                      else "lexical_candidate_unavailable")
            return CandidateSelection((), (reason,))

    async def _ranked(self, context, plan, budget, *, limit, source_refs, service_class, purpose, data_generation=None):
        missing = []
        if not plan.query:
            rows = await self.store.page(context, plan, self.local_budget(budget),
                                         after=(None, None), snapshot=None, limit=limit)
            final, unavailable = await self._checked(context, plan, rows, self.local_budget(budget),
                limit=limit, partial=True, parent=budget, phase="memory_final_check", purpose=purpose)
            diagnostics = {"selection": "structured", "not_recalled": "unknown",
                "final_examined": getattr(final, "examined", len(final)),
                "qualification_or_check_dropped": max(getattr(final, "examined", len(final)) - len(final), 0),
                "after_final_check": len(final)}
            return _RankedUnits((unit for _, unit in final), diagnostics), unavailable
        vector_rows, fingerprint = [], None
        if plan.query and self.settings.get("vector", True):
            vector_options = {"data_generation": data_generation} if data_generation is not None else {}
            tasks = (asyncio.create_task(self._lexical(context, plan, budget)),
                     asyncio.create_task(self._vectors(context, plan, budget, source_refs, service_class, **vector_options)))
            try:
                selection, vector_result = await asyncio.gather(*tasks)
            except BaseException:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                raise
            vector_rows, unavailable = vector_result
            fingerprint = getattr(vector_rows, "profile_key", None)
            missing.extend(unavailable)
        else:
            selection = await self._lexical(context, plan, budget)
        missing.extend(selection.missing)
        if budget is not None:
            budget.check()
        if fingerprint and selection.rows and hasattr(self.store, "profile_missing"):
            try:
                if await self.store.profile_missing(selection.rows, fingerprint, self.local_budget(budget)):
                    missing.append("vector_index_partial")
            except PHASE_ERRORS as exc:
                self._degrade(exc, budget)
                missing.append("vector_index_coverage_unavailable")
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
        ordered = sorted(fused.values(), key=lambda row: row["score"], reverse=True)[:count]
        candidates = _MmrOrder(ordered)
        diagnostics = {"lexical_candidates": len(selection.rows), "vector_candidates": len(vector_rows),
                       "fused_candidates": len(fused), "candidate_budget_discarded": max(len(fused) - len(ordered), 0),
                       "not_recalled": "unknown"}
        if not candidates:
            return _RankedUnits((), diagnostics), tuple(dict.fromkeys(missing))
        ranked, rerank_candidates, remote, rank_sources, rank_behaviors = [], [], None, (), ()
        # A reranker helps decide the cut when near-ranked candidates compete.
        # A complete structured list or a clearly separated cut does not need it.
        close_cut = (len(ordered) > limit and
                     ordered[limit - 1]["score"] - ordered[limit]["score"] <= 0.15)
        if plan.query and purpose in {"conversation", "tool", "consolidation"} and self.settings.get("rerank", False) and close_cut:
            remote = self.remote_phase(budget, float(self.settings.get("rerank_seconds", MODEL_EXECUTION_SECONDS)))
            if remote is None:
                missing.append("rerank_budget_insufficient")
            else:
                checked, unavailable = await self._checked(context, plan, candidates, self.local_budget(budget),
                    limit=max(limit, 24), partial=True, parent=budget, phase="memory_candidate_check", purpose=purpose)
                missing.extend(unavailable)
                ranked = list(checked)
                # Non-behavior reflections have no downstream dependency port.
                # Preserve their local slots while reranking the other items.
                rerank_candidates = [item for item in ranked if not getattr(item[1], "learning_ref", None)]
                remote = (self.remote_phase(budget, float(self.settings.get("rerank_seconds", MODEL_EXECUTION_SECONDS)))
                          if len(rerank_candidates) > 1 else None)
                if len(rerank_candidates) > 1 and remote is None:
                    missing.append("rerank_budget_insufficient")
        if remote is not None:
            remote_budget, queue_wait = remote
            documents = [rerank_text(unit) for _, unit in rerank_candidates]
            proposed_behaviors = unique_behavior_refs(*(getattr(unit, "behavior_refs", ()) for _, unit in rerank_candidates),
                *((unit.behavior_ref,) if getattr(unit, "behavior_ref", None) else () for _, unit in rerank_candidates))
            request = ModelRequest("memory-rerank:" + str(uuid4()), str(uuid4()), "rerank",
                service_class, context, remote_budget, (), required_capabilities=frozenset({"rerank"}),
                max_output_tokens=0, native_payload={"query": plan.query, "documents": documents, "top_n": min(limit, len(documents))},
                execution_timeout_seconds=float(self.settings.get("rerank_seconds", MODEL_EXECUTION_SECONDS)),
                business_deadline_at=remote_budget.deadline_at if budget is not None else None,
                source_refs=unique_refs(source_refs, *(unit.source_refs for _, unit in rerank_candidates)),
                behavior_refs=proposed_behaviors, behavior_purpose=purpose if proposed_behaviors else None,
                data_generation=data_generation)
            proposed_sources = request.source_refs
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
                    if type(index) is int and 0 <= index < len(rerank_candidates) and index not in seen:
                        seen.add(index)
                        order.append(rerank_candidates[index])
                if order:
                    sources_valid = await self._derivation_valid(context, proposed_sources, self.local_budget(budget))
                    behaviors_valid = (await self._behavior_dependencies_valid(context, proposed_behaviors, purpose,
                                                                               self.local_budget(budget)) if sources_valid else False)
                    if sources_valid and behaviors_valid:
                        rank_sources = proposed_sources
                        rank_behaviors = proposed_behaviors
                        reranked_iter = iter(order + [value for index, value in enumerate(rerank_candidates) if index not in seen])
                        ranked = [value if getattr(value[1], "learning_ref", None) else next(reranked_iter) for value in ranked]
                    else:
                        ranked = []
                        missing.append("rerank_behaviors_unavailable" if sources_valid else "rerank_inputs_revoked")
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
            limit=limit, partial=True, parent=budget, phase="memory_final_check", purpose=purpose,
            derivation_refs=rank_sources, behavior_refs=rank_behaviors)
        missing.extend(unavailable)
        if rank_sources and not final:
            try:
                still_valid = await self._derivation_valid(context, rank_sources, self.local_budget(budget))
            except PHASE_ERRORS as exc:
                self._degrade(exc, budget)
                still_valid = False
            if not still_valid:
                missing.append("rerank_inputs_revoked")
                rank_sources, rank_behaviors = (), ()
                final, unavailable = await self._checked(context, plan, candidates, self.local_budget(budget),
                    limit=limit, partial=True, parent=budget, phase="memory_final_check", purpose=purpose)
                missing.extend(unavailable)
        if rank_behaviors and not await self._behavior_dependencies_valid(context, rank_behaviors, purpose, self.local_budget(budget)):
            missing.append("rerank_behaviors_unavailable")
            rank_sources, rank_behaviors = (), ()
            final, unavailable = await self._checked(context, plan, candidates, self.local_budget(budget),
                limit=limit, partial=True, parent=budget, phase="memory_final_check", purpose=purpose)
            missing.extend(unavailable)
        rejected = getattr(final, "rejected_keys", ())
        rejected_count = len(rejected)
        if rejected and not rank_sources and not rank_behaviors:
            # A removed early pick must not keep influencing MMR diversity of
            # the replacement candidates. Recompute once on the proved remainder.
            remaining = _MmrOrder([row for row in ordered if record_key(row) not in rejected])
            final, unavailable = await self._checked(context, plan, remaining, self.local_budget(budget),
                limit=limit, partial=True, parent=budget, phase="memory_final_check", purpose=purpose)
            missing.extend(unavailable)
        diagnostics.update(final_examined=getattr(final, "examined", len(final)),
            qualification_or_check_dropped=rejected_count + (max(getattr(final, "examined", len(final)) - len(final), 0)
                if rejected and not rank_sources and not rank_behaviors else 0),
            after_final_check=len(final))
        return _RankedUnits((unit for _, unit in final), diagnostics), tuple(dict.fromkeys(missing))

    async def query(self, context, plan, budget, *, source_refs=(), cursor=None, limit=50,
                    service_class=ServiceClass.INTERACTIVE, purpose="conversation", data_generation=None):
        if data_generation is None:
            data_generation = current_execution_generation()
        if budget is not None:
            budget.check()
        plan = parse_plan(plan)
        if plan.time_mode != "current":
            purpose = "history"
        await self.memory.identity.check(context, source_refs)
        if data_generation is not None:
            async with self.database.transaction(budget) as conn:
                await self.memory.jobs.assert_generation_on(conn, data_generation)
        if not context.actor_id:
            raise AppError("actor_required")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise AppError("invalid_memory_query_limit")
        if purpose not in {"conversation", "tool", "consolidation", "history"}:
            raise AppError("invalid_memory_query_purpose")
        try:
            missing = list(await self.store.coverage_gaps(context, plan, self.local_budget(budget)))
        except PHASE_ERRORS as exc:
            if plan.coverage_mode == "list":
                raise
            self._degrade(exc, budget)
            missing = ["memory_coverage_timeout" if isinstance(exc, (TimeoutError, psycopg.errors.QueryCanceled))
                       else "memory_coverage_unavailable"]
        next_cursor, diagnostics = None, {}
        if plan.coverage_mode == "list":
            after, snapshot = read_cursor(context, plan, cursor)
            rows = await self.store.page(context, plan, self.local_budget(budget), after=after, snapshot=snapshot, limit=limit)
            checked, unavailable = await self._checked(context, plan, rows[:limit], self.local_budget(budget),
                                                       snapshot=snapshot, purpose=purpose)
            missing.extend(unavailable)
            units = tuple(unit for _, unit in checked)
            if len(rows) > limit:
                next_cursor = write_cursor(context, plan, rows[limit - 1], snapshot)
        else:
            if cursor is not None:
                raise AppError("memory_ranked_query_has_no_cursor")
            units, unavailable = await self._ranked(context, plan, budget, limit=min(limit, 32),
                source_refs=source_refs, service_class=service_class, purpose=purpose, data_generation=data_generation)
            diagnostics = dict(getattr(units, "diagnostics", {}))
            missing.extend((*unavailable, "ranked_selection"))
            rows, checked = (), ()
        before_budget = len(units)
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
        if diagnostics:
            diagnostics.update(token_budget_discarded=before_budget - len(units), final_returned=len(units))
        missing = tuple(dict.fromkeys(missing))
        if budget is not None:
            budget.check()
        if data_generation is not None:
            async with self.database.transaction(budget) as conn:
                await self.memory.jobs.assert_generation_on(conn, data_generation)
        return Page(units, next_cursor, Coverage(plan.coverage_mode == "list" and not next_cursor and not missing,
            missing=missing, representation="memory_evidence",
            ranges=({"selection": plan.coverage_mode, "time_mode": plan.time_mode,
                     "evidence_kinds": plan.evidence_kinds, "returned": len(units),
                     "as_of": snapshot.isoformat() if plan.coverage_mode == "list" else None,
                     "literal_query_filter": False,
                     "query_filter": "lexical_or_fragment" if plan.query and plan.coverage_mode == "list" else None,
                     "diagnostics": diagnostics},)))

    async def candidates(self, context, query, budget, *, subject_ids=(), matter_refs=(),
                         kinds=("claim", "hypothesis", "matter"), limit=32, data_generation=None):
        # This port is the consolidation background read. Its old 55-second
        # preparation clock is not a business expiry. Each DB/profile/provider
        # phase keeps its own execution bound; waiting for Models consumes none.
        return await self.query(context, MemoryQueryPlan(query=query, subject_ids=tuple(subject_ids),
            matter_refs=tuple(matter_refs), evidence_kinds=tuple(kinds)), None,
            limit=min(max(int(limit), 1), 32), service_class=ServiceClass.ELASTIC, purpose="consolidation",
            data_generation=data_generation)

    async def revalidate(self, context, records, budget, *, data_generation=None):
        """Recheck current automatic context material without rebinding a stale version."""
        if data_generation is None:
            data_generation = current_execution_generation()
        budget.check()
        await self.memory.identity.check(context)
        if data_generation is not None:
            async with self.database.transaction(budget) as conn:
                await self.memory.jobs.assert_generation_on(conn, data_generation)
        if len(records) > 128:
            raise AppError("memory_context_candidates_too_large")
        kinds = tuple(dict.fromkeys(record.kind for record in records))
        if not kinds:
            return (), ()
        plan = parse_plan(MemoryQueryPlan(evidence_kinds=kinds))
        rows = [{"_kind": record.kind, "_id": record.id, "revision": record.revision} for record in records]
        values, missing = await self._checked(context, plan, rows, budget)
        if data_generation is not None:
            async with self.database.transaction(budget) as conn:
                await self.memory.jobs.assert_generation_on(conn, data_generation)
        return tuple(unit for _, unit in values), missing

    async def search(self, context, query, budget, *, limit=12, source_refs=(), data_generation=None):
        """Existing context API, now backed solely by complete multi-type evidence."""
        page = await self.query(context, MemoryQueryPlan(query=query), budget,
                                source_refs=source_refs, limit=min(max(int(limit), 1), 32), data_generation=data_generation)
        return contributions(page.items), tuple(item for item in page.coverage.missing if item != "ranked_selection")
