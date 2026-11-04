"""Complete evidence construction and explicit bitemporal version selection rules."""

from datetime import UTC, datetime
from collections.abc import Mapping
import json

from tianshu.runtime.errors import AppError
from tianshu.runtime.serialization import wire
from tianshu.types import Coverage
from . import codec
from .candidate_sources import SourceProofGroups, check_group_batches_on
from .domain_utils import optional_time, unique_refs
from .types import ClaimRevision, Episode, MatterView, MemoryEvidenceUnit


def required_refs(value):
    """Explicit interpretation/completion dependencies stay AND across OR supports."""
    get = value.get if isinstance(value, Mapping) else lambda name, default=None: getattr(value, name, default)
    basis = get("completion_basis") or {}
    return unique_refs((*codec.refs(get("derivation_refs")), *codec.refs(get("counterexample_refs")),
                        *codec.refs(basis.get("source_refs", ()))))


async def select_supports_on(sources, conn, context, rows):
    """Alternative evidence is OR; every support and necessary derivation is AND."""
    candidates, grouped = [], SourceProofGroups()
    for index, row in enumerate(rows):
        derivation = required_refs(row)
        groups = row.get("support_groups") or [row["source_refs"]]
        for group in reversed(groups):
            refs = unique_refs((*codec.refs(group), *derivation))
            if refs:
                # Each chunk remains part of the same AND proof. Splitting a
                # large group never turns a partial source check into access.
                candidates.append((index, refs, grouped.add(refs)))
    selected = [None] * len(rows)
    checked = await check_group_batches_on(sources, conn, context, grouped.values)
    for owner, refs, indexes in candidates:
        if selected[owner] is None and all(checked[index] is True for index in indexes):
            selected[owner] = refs
    return selected


def temporal_selection(*, time_mode="current", valid_at=None, known_at=None):
    if time_mode not in {"current", "valid_at", "known_at"}:
        raise AppError("invalid_memory_time_mode")
    valid_at, known_at = optional_time(valid_at), optional_time(known_at)
    if time_mode == "valid_at" and valid_at is None:
        raise AppError("memory_valid_at_required")
    if time_mode == "known_at" and known_at is None:
        raise AppError("memory_known_at_required")
    return time_mode, valid_at, known_at


def temporal_coverage(value, *, time_mode="current", valid_at=None):
    """Unknown time is retained as uncertainty; it never proves every historical instant."""
    missing = []
    if getattr(value, "recorded_at", None) is None:
        missing.append("recorded_at_unknown")
    if time_mode in {"valid_at", "known_at"} and isinstance(value, ClaimRevision):
        if value.valid_from is None and value.valid_until is None:
            missing.append("valid_time_unknown")
    if isinstance(value, MatterView):
        if time_mode == "valid_at":
            missing.append("matter_effective_time_inferred_from_report")
        if value.state == "completed" and value.completion_basis is None:
            missing.append("completion_basis_unknown")
    return tuple(missing)


def valid_for_time(value, *, time_mode="current", valid_at=None):
    moment = optional_time(valid_at) or datetime.now(UTC)
    if isinstance(value, ClaimRevision):
        if value.state != "active" or value.current_status == "retracted":
            return False
        if time_mode == "current" and value.current_status != "current":
            return False
        if value.valid_from is not None and value.valid_from > moment:
            return False
        if value.valid_until is not None and value.valid_until <= moment:
            return False
    return True


def build_evidence_unit(value, *, relations=(), time_mode="current", temporal_missing=()):
    """Build from an already authorized domain projection; callers validate whole sources first."""
    relations = tuple(relations or getattr(value, "relations", ()))
    relation_missing = tuple("relation_endpoint:" + reason for link in relations
        for endpoint in (link.from_evidence, link.to_evidence) if endpoint
        for reason in (endpoint.get("coverage") or {}).get("missing", ()))
    missing = tuple(dict.fromkeys((*temporal_coverage(value, time_mode=time_mode), *temporal_missing,
        *relation_missing, *(("relation_recorded_at_unknown",) if any(link.recorded_at is None for link in relations) else ()))))
    coverage = Coverage(not missing, missing=missing, representation="memory_evidence")
    endpoint_refs = tuple(ref for link in relations for endpoint in (link.from_evidence, link.to_evidence)
                          if endpoint for ref in codec.refs(endpoint.get("source_refs", ())))
    explicit_refs = required_refs(value)
    common = {"actor_id": value.actor_id, "source_refs": unique_refs((*value.source_refs,
                  *explicit_refs, *endpoint_refs, *(ref for link in relations for ref in (*link.source_refs, *link.derivation_refs)))),
              "scope_ids": value.scope_ids, "recorded_at": value.recorded_at,
              "coverage": coverage, "relations": tuple(relations),
              "head_revision": getattr(value, "head_revision", None) or value.revision}
    if isinstance(value, ClaimRevision):
        content = f"{value.predicate}: {json.dumps(wire(value.value), ensure_ascii=False)}"
        return MemoryEvidenceUnit(
            kind="hypothesis" if value.memory_kind == "hypothesis" else "claim",
            id=value.claim_id, revision=value.revision, subject_id=value.subject_id,
            speaker_id=value.speaker_id, assertion=value.assertion, content=content,
            conditions=value.conditions, occurred_at=value.observed_at,
            valid_from=value.valid_from, valid_until=value.valid_until, matter_id=value.matter_id,
            current_status=value.current_status, state=value.state,
            change_kind=value.change_kind, interpretation=value.memory_kind == "hypothesis",
            predicate=value.predicate, value=value.value, polarity=value.polarity,
            derivation_refs=value.derivation_refs, change_reason=value.change_reason, **common,
            counterexample_refs=value.counterexample_refs,
            semantic_fingerprint=value.semantic_fingerprint,
        )
    if isinstance(value, MatterView):
        return MemoryEvidenceUnit(
            kind="matter", id=value.matter_id, revision=value.revision,
            subject_id=value.subject_id, speaker_id=None, assertion="inferred" if value.interpretation else "reported",
            content=f"{value.title}: {value.summary}", conditions=value.conditions,
            occurred_at=value.observed_at, valid_from=None, valid_until=None,
            matter_id=value.matter_id, current_status=value.current_status, state=value.state,
            change_kind=value.change_kind, interpretation=value.interpretation,
            completion_basis=value.completion_basis, derivation_refs=value.derivation_refs,
            change_reason=value.change_reason, due_at=value.due_at, **common,
        )
    if isinstance(value, Episode):
        episode_missing = tuple(dict.fromkeys((*missing, *value.coverage.missing,
            *(value.extraction_coverage.missing if value.extraction_coverage else ()))))
        common["coverage"] = Coverage(value.coverage.complete and not episode_missing,
                                      missing=episode_missing, representation="memory_evidence")
        return MemoryEvidenceUnit(
            kind="episode", id=value.episode_id, revision=value.revision,
            subject_id=value.person_id, speaker_id=value.speaker_id,
            assertion="reported" if str(value.origin_kind) == "human" else "observed",
            content=value.summary, conditions={}, origin_kind=str(value.origin_kind),
            actual_result=value.actual_result,
            occurred_at=value.occurred_at, valid_from=None, valid_until=None,
            matter_id=value.matter_id, current_status="current", state="active", **common,
        )
    raise TypeError("unsupported_memory_evidence_type")
