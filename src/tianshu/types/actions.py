"""Action identity, actual effects and media references shared across domain boundaries."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Mapping

from .context import OriginKind, TrustedContext


@dataclass(frozen=True, slots=True)
class SourceRef:
    source_id: str
    revision: int
    locator: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Coverage:
    complete: bool
    total: int | None = None
    missing: tuple[str, ...] = ()
    ranges: tuple[Mapping[str, Any], ...] = ()
    representation: str | None = None


@dataclass(frozen=True, slots=True)
class MediaRef:
    """A controlled artifact URI, never a client-supplied local filesystem path."""

    media_id: str
    uri: str
    mime_type: str
    size_bytes: int | None = None
    sha256: str | None = None
    duration_ms: int | None = None
    width: int | None = None
    height: int | None = None


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    artifact_id: str
    media: MediaRef
    source_refs: tuple[SourceRef, ...] = ()
    coverage: Coverage | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Action:
    action_id: str
    intent_key: str
    capability_id: str
    actor_id: str | None
    context: TrustedContext
    arguments: Mapping[str, Any]
    origin_kind: OriginKind
    source_refs: tuple[SourceRef, ...] = ()
    expression_id: str | None = None
    activity_id: str | None = None
    matter_id: str | None = None
    revision: int = 1
    deadline_at: datetime | None = None
    # The trusted caller selects these original inputs; model arguments never supply them.
    # This is a selected subset of source_refs, preserving every complete locator.
    current_input_refs: tuple[SourceRef, ...] = ()

    @property
    def access_epoch(self) -> int:
        return self.context.access_epoch


@dataclass(frozen=True, slots=True)
class Attempt:
    attempt_id: str
    action_id: str
    dispatch: Literal["not_started", "submitted", "unknown"] = "not_started"
    provider_handle: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class Receipt:
    receipt_id: str
    action_id: str | None
    attempt_id: str | None
    execution: Literal["pending", "running", "succeeded", "failed", "cancelled", "unknown"]
    effect: Literal["none", "confirmed", "unknown"]
    delivery: Literal["not_requested", "pending", "delivered", "failed", "unknown"]
    artifacts: tuple[ArtifactRef, ...] = ()
    coverage: Coverage | None = None
    result: Mapping[str, Any] = field(default_factory=dict)
    error_code: str | None = None
