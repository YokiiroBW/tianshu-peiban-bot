"""Typed references for provenance without granting access to its members."""

from dataclasses import dataclass

from tianshu.types import SourceRef


@dataclass(frozen=True, slots=True)
class EvidenceSetRef:
    evidence_id: str


@dataclass(frozen=True, slots=True)
class EvidenceSpec:
    required: tuple[SourceRef, ...] = ()
    alternative_supports: tuple[tuple[SourceRef, ...], ...] = ()
    generation_dependencies: tuple[SourceRef, ...] = ()


@dataclass(frozen=True, slots=True)
class SourceStatus:
    reference: SourceRef
    state: str
    head_revision: int | None
    usable: bool
