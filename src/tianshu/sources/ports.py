"""The source owner's small public boundary; implementations do actual authorization."""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from datetime import datetime
from typing import TYPE_CHECKING, Awaitable, BinaryIO, Callable, Protocol

from tianshu.types import MediaRef, OriginKind, SourceRef, TrustedContext

if TYPE_CHECKING:
    from tianshu.conversation.types import InputPart


class SourcePort(Protocol):
    async def validate_persistence(
        self, connection, context: TrustedContext, source_refs: tuple[SourceRef, ...]
    ) -> bool: ...
    def register_dependant(
        self, callback: Callable[[object, tuple[SourceRef, ...], str], Awaitable[None]]
    ) -> None: ...
    async def check(
        self, context: TrustedContext, source_refs: tuple[SourceRef, ...] = ()
    ) -> None: ...
    async def check_many(
        self, context: TrustedContext, refs_groups: tuple[tuple[SourceRef, ...], ...]
    ) -> tuple[bool, ...]: ...
    async def record_event(
        self,
        context: TrustedContext,
        *,
        producer: str,
        event_id: str,
        revision: int,
        occurred_at: datetime,
        parts: tuple[InputPart, ...],
        origin_kind: OriginKind,
    ) -> tuple[SourceRef, ...]: ...
    async def register_media(
        self,
        context: TrustedContext,
        media: MediaRef,
        *,
        locator: dict,
        origin_kind: OriginKind = OriginKind.HUMAN,
    ) -> SourceRef: ...
    async def capture(
        self, context: TrustedContext, *, locator: dict, raw: bytes, mime_type: str
    ) -> SourceRef: ...
    async def resolve(self, context: TrustedContext, media: MediaRef) -> tuple[bytes, str]: ...
    def open_media(
        self, context: TrustedContext, media: MediaRef
    ) -> AbstractAsyncContextManager[BinaryIO]: ...
    async def read(self, context: TrustedContext, source: SourceRef, requested: dict) -> dict: ...
    async def recall_event(
        self,
        context: TrustedContext,
        producer: str,
        event_id: str,
        *,
        revision: int | None = None,
        reason: str = "channel_recall",
    ) -> None: ...
    async def disclose(
        self,
        context: TrustedContext,
        *,
        producer: str,
        event_id: str,
        revision: int,
        parts: tuple[InputPart, ...],
        source_refs: tuple[SourceRef, ...],
        reader_ids: tuple[str, ...] = (),
        conversation_ids: tuple[str, ...] = (),
        connection=None,
    ) -> SourceRef: ...
