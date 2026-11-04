"""Search projection and typed references; Companion owns rule text and state."""

from dataclasses import dataclass, field
from typing import Any, Mapping

from .types import MemoryEvidenceUnit


@dataclass(frozen=True, slots=True)
class LearningEvidence(MemoryEvidenceUnit):
    behavior_ref: Mapping[str, Any] = field(default_factory=dict)
    learning_ref: Mapping[str, Any] = field(default_factory=dict)
