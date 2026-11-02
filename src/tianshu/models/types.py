"""Canonical model inputs. Native continuation metadata remains lossless."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Mapping

from tianshu.types import MediaRef, RequestBudget, ServiceClass, SourceRef, TrustedContext


@dataclass(frozen=True, slots=True)
class ModelPart:
    kind: Literal["text", "image", "audio", "video", "tool_call", "tool_result"]
    text: str | None = None
    media: MediaRef | None = None
    call_id: str | None = None
    name: str | None = None
    arguments: Mapping[str, Any] | None = None
    result: Any = None
    native: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ModelMessage:
    role: Literal["user", "assistant", "tool"]
    parts: tuple[ModelPart, ...]
    native: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    parameters: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ModelRequest:
    intent_id: str
    attempt_id: str
    function: str
    service_class: ServiceClass
    context: TrustedContext
    budget: RequestBudget
    messages: tuple[ModelMessage, ...]
    system: str = ""
    tools: tuple[ToolSpec, ...] = ()
    required_capabilities: frozenset[str] = frozenset({"text"})
    max_output_tokens: int = 2048
    estimated_input_tokens: int | None = None
    max_queue_wait: float | None = None
    route_revision: int | None = None
    persona_revision: int | None = None
    context_revision: int | None = None
    task_id: str | None = None
    parent_task_id: str | None = None
    native_payload: Mapping[str, Any] | None = None
    protocol: str | None = None
    route_id: str | None = None
    parameters: Mapping[str, Any] = field(default_factory=dict)
    source_refs: tuple[SourceRef, ...] = ()
    capability_probe: bool = False
    # Absence retains the absolute budget of previously frozen requests.
    execution_timeout_seconds: float | None = None
    business_deadline_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ModelUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_input_tokens: int | None = None
    cache_write_tokens: int | None = None
    estimated_input_tokens: int | None = None
    raw: Mapping[str, Any] | None = None
    metering: Literal["reported", "estimated", "unknown"] = "unknown"


@dataclass(frozen=True, slots=True)
class ModelReceipt:
    attempt_id: str
    intent_id: str
    status: Literal["queued", "running", "succeeded", "failed", "cancelled", "unknown"]
    dispatch: Literal["not_started", "submitted", "unknown"]
    route_id: str
    route_revision: int
    provider_id: str
    model: str
    protocol: str
    quota_group: str
    usage: ModelUsage = field(default_factory=ModelUsage)
    provider_handle: str | None = None
    queued_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    first_output_ms: float | None = None
    error_code: str | None = None
    hold_until: datetime | None = None
    deadline_at: datetime | None = None
    execution_started_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ModelCompletion:
    """Content termination is separate from physical request settlement."""

    state: Literal["complete", "incomplete", "unknown"] = "unknown"
    stop_reasons: tuple[str, ...] = ()
    provider_status: str | None = None


@dataclass(frozen=True, slots=True)
class ModelResult:
    parts: tuple[ModelPart, ...]
    receipt: ModelReceipt
    native_response: Mapping[str, Any] | None = None
    completion: ModelCompletion = field(default_factory=ModelCompletion)


@dataclass(frozen=True, slots=True)
class ModelEvent:
    kind: Literal["text", "part", "usage", "native", "receipt"]
    text: str | None = None
    part: ModelPart | None = None
    receipt: ModelReceipt | None = None
    data: Any = None


@dataclass(frozen=True, slots=True)
class ExternalRequest:
    """A domain-owned external protocol whose actual capacity still belongs to Broker."""

    intent_id: str
    attempt_id: str
    context: TrustedContext
    budget: RequestBudget
    quota_group: str
    function: str
    service_class: ServiceClass
    provider_id: str
    billing_account: str | None = None
    estimated_units: Mapping[str, float] = field(default_factory=dict)
    max_queue_wait: float | None = None
    source_refs: tuple[SourceRef, ...] = ()
    execution_timeout_seconds: float | None = None
    business_deadline_at: datetime | None = None
    execution_start: Literal["admitted", "provider_running"] = "admitted"
