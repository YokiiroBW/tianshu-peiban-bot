"""Transport facts are distinct from the identities verified by IdentityService."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Mapping

from tianshu.types import TrustedContext


@dataclass(frozen=True, slots=True)
class IncomingEvent:
    producer: str
    event_id: str
    revision: int
    occurred_at: datetime
    conversation_external_id: str
    conversation_kind: Literal["private", "group", "project", "system"]
    sender_external_id: str
    parts: tuple[Mapping[str, Any], ...] = ()
    mentioned_external_ids: tuple[str, ...] = ()
    reply_to_event_id: str | None = None
    original_author_external_id: str | None = None
    command: str | None = None
    reply_owner: str = "tianshu"
    archived: bool = False


@dataclass(frozen=True, slots=True)
class AdmissionDecision:
    disposition: Literal["reply", "observe", "ignore"]
    context: TrustedContext | None
    reply_owner: str
    reason: str
    event_id: str
    revision: int
    duplicate: bool = False
    mentioned_person_ids: tuple[str, ...] = ()
    original_author_person_id: str | None = None
    reply_to_event_id: str | None = None
