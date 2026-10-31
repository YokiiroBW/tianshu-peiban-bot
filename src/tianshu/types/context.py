"""Trusted invocation facts and an absolute, transportable request deadline."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4


class ServiceClass(StrEnum):
    CONTROL = "control"
    INTERACTIVE = "interactive"
    DUE = "due"
    ELASTIC = "elastic"


class OriginKind(StrEnum):
    HUMAN = "human"
    OBSERVATION = "observation"
    TOOL_RESULT = "tool_result"
    TIMER = "timer"
    SYSTEM = "system"
    ACTOR = "actor"
    REPLAY = "replay"


@dataclass(frozen=True, slots=True)
class TrustedContext:
    """Created by identity, never accepted directly from an unauthenticated body."""

    principal_id: str
    actor_id: str | None
    person_id: str | None
    conversation_id: str | None
    scope_ids: tuple[str, ...]
    audience: tuple[str, ...]
    access_epoch: int
    origin_kind: OriginKind
    permissions: frozenset[str] = frozenset()
    conversation_kind: str | None = None


@dataclass(frozen=True, slots=True)
class RequestBudget:
    deadline_at: datetime
    cancellation_id: str = field(default_factory=lambda: str(uuid4()))
    trace_id: str = field(default_factory=lambda: str(uuid4()))
    _monotonic_deadline: float = field(init=False, repr=False, compare=False)

    def __post_init__(self):
        if self.deadline_at.tzinfo is None:
            raise ValueError("deadline_at must have a timezone")
        seconds = (self.deadline_at - datetime.now(UTC)).total_seconds()
        object.__setattr__(self, "_monotonic_deadline", time.monotonic() + seconds)

    def remaining(self) -> float:
        return max(0.0, self._monotonic_deadline - time.monotonic())

    def check(self) -> None:
        if self.remaining() <= 0:
            raise TimeoutError("request_deadline_exceeded")

    def timeout(self):
        self.check()
        return asyncio.timeout(self.remaining())
