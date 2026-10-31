from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping
from tianshu.types import TrustedContext, SourceRef


@dataclass(frozen=True, slots=True)
class AttentionDecision:
    decision: str
    reason: str
    due_at: datetime | None = None
    target_ref: str | None = None


@dataclass(frozen=True, slots=True)
class ContactCandidate:
    candidate_id: str
    actor_id: str
    target_ref: str
    kind: str
    summary: str
    due_at: datetime
    expires_at: datetime
    context: TrustedContext
    source_refs: tuple[SourceRef, ...] = ()
    explicit: bool = False
    action_id: str | None = None
    delivery_kind: str = "share_candidate"
    payload: Mapping[str, Any] | None = None
