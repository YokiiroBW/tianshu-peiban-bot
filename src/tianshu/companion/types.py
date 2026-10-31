from dataclasses import dataclass, field
from datetime import datetime
from typing import Mapping, Any
from tianshu.types import SourceRef


@dataclass(frozen=True, slots=True)
class Persona:
    persona_id: str
    revision: int
    name: str
    author_text: str
    tone: str = ""
    style: str = ""
    address: str = ""
    world: str = ""


@dataclass(frozen=True, slots=True)
class AffectUpdate:
    event_id: str
    event_revision: int
    appraisal: str
    valence: float
    arousal: float
    stress: float
    social_need: float
    confidence: float
    half_life_seconds: int
    occurred_at: datetime
    source_refs: tuple[SourceRef, ...] = ()


@dataclass(frozen=True, slots=True)
class ReflectionProposal:
    proposal_id: str
    actor_id: str
    episode_ids: tuple[str, ...]
    content: str
    kind: str
    occurred_at: datetime
    source_refs: tuple[SourceRef, ...]
    scope_ids: tuple[str, ...]
    interpretation: bool = True
    conditions: Mapping[str, Any] = field(default_factory=dict)
    counterexamples: tuple[str, ...] = ()
