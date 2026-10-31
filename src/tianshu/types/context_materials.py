"""Typed contributions to the one context budget; no contributor gains system authority."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, Mapping

from .actions import SourceRef

if TYPE_CHECKING:
    from tianshu.models.types import ModelMessage, ToolSpec


@dataclass(frozen=True, slots=True)
class ContextContribution:
    kind: str
    text: str
    source_refs: tuple[SourceRef, ...] = ()
    scope_ids: tuple[str, ...] = ()
    priority: int = 50
    mandatory: bool = False
    content_revision: str | None = None


@dataclass(frozen=True, slots=True)
class TokenUsage:
    limit_tokens: int
    reserved_output_tokens: int
    used_tokens: int | None
    estimated_tokens: int
    utf8_bytes: int
    measurement: Literal["provider", "tokenizer", "estimate"]
    tokenizer: str | None = None
    media_estimates: tuple[Mapping[str, object], ...] = ()


@dataclass(frozen=True, slots=True)
class ContextPack:
    system: str
    messages: tuple[ModelMessage, ...]
    tools: tuple[ToolSpec, ...]
    source_refs: tuple[SourceRef, ...]
    content_revisions: Mapping[str, int]
    token_usage: TokenUsage
    missing: tuple[str, ...] = ()
    materials: tuple[ContextContribution, ...] = ()
