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

    @property
    def prompt_text(self):
        additions = "\n\n".join(f"{label}\n{getattr(self, key)}" for key, label in (
            ("tone", "语气"), ("style", "表达方式"), ("address", "称呼约定"), ("world", "角色世界背景")) if getattr(self, key))
        return self.author_text + ("\n\n"+additions if additions else "")


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
    goal_id: str | None = None
    goal_relevance: float = 0.0
    attribution: str = "unknown"
    control: float = 0.5


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
