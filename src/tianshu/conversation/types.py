from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping
from tianshu.types import ArtifactRef, OriginKind, Receipt, SourceRef


@dataclass(frozen=True, slots=True)
class InputMessage:
    producer: str
    event_id: str
    revision: int
    occurred_at: datetime
    received_at: datetime
    parts: tuple[Mapping[str, Any], ...]
    source_refs: tuple[SourceRef, ...] = ()
    reply_owner: str = "tianshu"
    reply_required: bool = True
    command: str | None = None


@dataclass(frozen=True, slots=True)
class ExpressionPlan:
    expression_id: str
    actor_id: str
    target_ref: str
    public_parts: tuple[Mapping[str, Any], ...]
    source_refs: tuple[SourceRef, ...]
    origin_kind: OriginKind
    turn_id: str | None = None
    artifacts: tuple[ArtifactRef, ...] = ()


@dataclass(frozen=True, slots=True)
class DialogueResult:
    turn_id: str
    state: str
    expression_id: str | None
    receipt: Receipt | None
    parts: tuple[Mapping[str, Any], ...]
    action_ids: tuple[str, ...] = ()
    model_attempt_ids: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
