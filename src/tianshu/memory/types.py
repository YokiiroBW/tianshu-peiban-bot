"""Formal evidence units and durable incremental cursors."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping

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
