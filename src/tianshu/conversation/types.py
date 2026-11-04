from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping
from tianshu.types import ArtifactRef, MediaRef, OriginKind, Receipt, SourceRef


@dataclass(frozen=True, slots=True)
class InputPart:
    kind: str
    text: str | None = None
    media: MediaRef | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CollectorPolicy:
    silence_ms: int = 1500
    max_wait_ms: int | None = None
    max_active_turns: int = 2
    max_collectors_per_conversation: int = 32
    max_queued_turns: int = 8
    max_collection_messages: int = 256
    max_collection_bytes: int = 262144

    def __post_init__(self):
        if not 0<=self.silence_ms<=60000 or self.max_wait_ms is not None and self.max_wait_ms<self.silence_ms:
            raise ValueError("invalid collection timing")
        if any(value<1 for value in (self.max_active_turns,self.max_collectors_per_conversation,self.max_queued_turns,self.max_collection_messages,self.max_collection_bytes)):
            raise ValueError("invalid collection capacity")


@dataclass(frozen=True, slots=True)
class InputMessage:
    producer: str
    event_id: str
    revision: int
    occurred_at: datetime
    received_at: datetime
    parts: tuple[InputPart, ...]
    source_refs: tuple[SourceRef, ...] = ()
    reply_owner: str = "tianshu"
    reply_required: bool = True
    command: str | None = None
    conversation_kind: str = "unknown"
    autonomous_eligible: bool = False
    deployment_generation: Any = None


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
    candidate_id: str | None = None
    behavior_refs: tuple[Mapping[str, Any], ...] = ()


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
    revision: int = 1
    error_code: str | None = None
    context_selection: tuple[Mapping[str, Any], ...] | None = None
    token_usage: Mapping[str, Any] | None = None
    provider_usage: Mapping[str, Any] | None = None
    flow: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class ExpressionProgress:
    expression_id: str
    state: str
    receipt: Receipt | None = None
    render_action_ids: tuple[str, ...] = ()
    reason: str | None = None
