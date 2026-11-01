"""Formal evidence units and durable incremental cursors."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Mapping

from tianshu.runtime.api import Page
from tianshu.types import Coverage, OriginKind, SourceRef


@dataclass(frozen=True, slots=True)
class Episode:
    episode_id: str
    event_id: str
    revision: int
    actor_id: str
    origin_kind: OriginKind
    occurred_at: datetime
    summary: str
    source_refs: tuple[SourceRef, ...]
    scope_ids: tuple[str, ...]
    actual_result: Mapping[str, Any] | None
    coverage: Coverage
    sequence: int
    cursor: str
    person_id: str | None = None
    matter_id: str | None = None
    extraction_state: str = "pending"
    extraction_coverage: Coverage | None = None
    recorded_at: datetime | None = None
    speaker_id: str | None = None
    conversation_id: str | None = None
    consolidation: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class EpisodePage(Page):
    watermark: str


@dataclass(frozen=True, slots=True)
class ClaimRevision:
    claim_id: str
    revision: int
    actor_id: str
    speaker_id: str | None
    subject_id: str
    predicate: str
    value: Any
    assertion: str
    polarity: bool
    conditions: Mapping[str, Any]
    valid_from: datetime | None
    valid_until: datetime | None
    observed_at: datetime
    state: str
    source_refs: tuple[SourceRef, ...]
    scope_ids: tuple[str, ...]
    matter_id: str | None = None
    supersedes: tuple[str, ...] = ()
    contradictions: tuple[str, ...] = ()
    recorded_at: datetime | None = None
    change_kind: str = "legacy"
    memory_kind: str = "fact"
    current_status: str = "current"
    semantic_fingerprint: str | None = None
    derivation_refs: tuple[SourceRef, ...] = ()
    support_groups: tuple[tuple[SourceRef, ...], ...] = ()
    change_reason: str = ""
    counterexample_refs: tuple[SourceRef, ...] = ()
    relations: tuple["SemanticLink", ...] = ()
    head_revision: int | None = None


@dataclass(frozen=True, slots=True)
class MatterView:
    matter_id: str
    revision: int
    actor_id: str
    subject_id: str | None
    title: str
    state: str
    due_at: datetime | None
    source_refs: tuple[SourceRef, ...]
    claim_ids: tuple[str, ...]
    summary: str
    scope_ids: tuple[str, ...]
    conditions: Mapping[str, Any] = field(default_factory=dict)
    interpretation: bool = False
    recorded_at: datetime | None = None
    observed_at: datetime | None = None
    change_kind: str = "legacy"
    completion_basis: Mapping[str, Any] | None = None
    derivation_refs: tuple[SourceRef, ...] = ()
    support_groups: tuple[tuple[SourceRef, ...], ...] = ()
    current_status: str = "current"
    change_reason: str = ""
    relations: tuple["SemanticLink", ...] = ()
    head_revision: int | None = None


@dataclass(frozen=True, slots=True)
class ReflectionView:
    proposal_id: str
    revision: int
    actor_id: str
    person_id: str | None
    episode_ids: tuple[str, ...]
    content: str
    kind: str
    occurred_at: datetime
    source_refs: tuple[SourceRef, ...]
    scope_ids: tuple[str, ...]
    conditions: Mapping[str, Any]
    counterexamples: tuple[str, ...]
    interpretation: bool = True
    recorded_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class RecordRef:
    kind: str
    id: str
    revision: int


@dataclass(frozen=True, slots=True)
class SemanticLink:
    link_id: str
    actor_id: str
    from_ref: RecordRef
    to_ref: RecordRef
    relation_kind: str
    valid_from: datetime | None
    valid_until: datetime | None
    recorded_at: datetime | None
    source_refs: tuple[SourceRef, ...]
    scope_ids: tuple[str, ...]
    state: str = "active"
    support_groups: tuple[tuple[SourceRef, ...], ...] = ()
    derivation_refs: tuple[SourceRef, ...] = ()
    current_status: str = "current"
    from_evidence: Mapping[str, Any] | None = None
    to_evidence: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class MemoryQueryPlan:
    query: str = ""
    subject_ids: tuple[str, ...] = ()
    matter_refs: tuple[str, ...] = ()
    time_mode: Literal["current", "valid_at", "known_at"] = "current"
    valid_at: datetime | None = None
    known_at: datetime | None = None
    evidence_kinds: tuple[str, ...] = ("claim", "hypothesis", "matter", "episode", "reflection")
    coverage_mode: str = "ranked"
    budget: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MemoryEvidenceUnit:
    """One indivisible, authorized unit shared by retrieval, answers and management views."""

    kind: str
    id: str
    revision: int
    actor_id: str
    subject_id: str | None
    speaker_id: str | None
    assertion: str
    content: str
    conditions: Mapping[str, Any]
    occurred_at: datetime | None
    valid_from: datetime | None
    valid_until: datetime | None
    recorded_at: datetime | None
    matter_id: str | None
    current_status: str
    state: str
    source_refs: tuple[SourceRef, ...]
    scope_ids: tuple[str, ...]
    coverage: Coverage
    relations: tuple[SemanticLink, ...] = ()
    change_kind: str = "legacy"
    interpretation: bool = False
    predicate: str | None = None
    value: Any = None
    polarity: bool | None = None
    completion_basis: Mapping[str, Any] | None = None
    derivation_refs: tuple[SourceRef, ...] = ()
    change_reason: str = ""
    counterexample_refs: tuple[SourceRef, ...] = ()
    due_at: datetime | None = None
    origin_kind: str | None = None
    actual_result: Mapping[str, Any] | None = None
    episode_ids: tuple[str, ...] = ()
    counterexamples: tuple[str, ...] = ()
    semantic_fingerprint: str | None = None
    head_revision: int | None = None
