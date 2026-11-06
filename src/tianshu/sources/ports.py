"""The source owner's small public boundary; implementations do actual authorization."""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from datetime import datetime
from typing import TYPE_CHECKING, Awaitable, BinaryIO, Callable, Protocol

from tianshu.types import MediaRef, OriginKind, SourceRef, TrustedContext
from .types import EvidenceSetRef, EvidenceSpec, SourceStatus

if TYPE_CHECKING:
    from tianshu.conversation.types import InputPart


class SourcePort(Protocol):
    async def register_evidence_on(
        self, connection, context: TrustedContext, spec: EvidenceSpec
    ) -> SourceRef | EvidenceSetRef: ...
    async def prove_many_on(
        self, connection, context: TrustedContext,
        references: tuple[SourceRef | EvidenceSetRef, ...], *, lock: bool = False
    ) -> tuple[bool, ...]: ...
    async def prove_many(
        self, context: TrustedContext, references: tuple[SourceRef | EvidenceSetRef, ...]
    ) -> tuple[bool, ...]: ...
    async def source_status_many_on(
        self, connection, context: TrustedContext, references: tuple[SourceRef, ...]
    ) -> tuple[SourceStatus, ...]: ...
    async def source_status_many(
        self, context: TrustedContext, references: tuple[SourceRef, ...]
    ) -> tuple[SourceStatus, ...]: ...
    async def retract_publication(
        self, context: TrustedContext, source_ref: SourceRef, *, connection
    ) -> SourceRef: ...
    async def register_archive(
        self,
        context: TrustedContext,
        *,
        mapping_id: str,
        channel: dict,
        records: tuple[dict, ...],
        explicit_request: bool,
    ) -> tuple[dict, ...]: ...
    async def validate_persistence(
        self, connection, context: TrustedContext, source_refs: tuple[SourceRef, ...]
    ) -> bool: ...
    async def describe_for_write_on(
        self, connection, context: TrustedContext, source_refs: tuple[SourceRef, ...]
    ) -> tuple[dict, ...]: ...
    def register_dependant(
        self, callback: Callable[[object, tuple[SourceRef, ...], str], Awaitable[None]],
        *, name: str | None = None,
        partitions: Callable[[object, tuple[SourceRef, ...], str], Awaitable[tuple[str | None, ...]]] | None = None
    ) -> None: ...
    async def check(
        self, context: TrustedContext, source_refs: tuple[SourceRef, ...] = ()
    ) -> None: ...
    async def check_many(
        self, context: TrustedContext, refs_groups: tuple[tuple[SourceRef, ...], ...]
    ) -> tuple[bool, ...]: ...
    async def check_many_on(
        self, connection, context: TrustedContext, refs_groups: tuple[tuple[SourceRef, ...], ...]
    ) -> tuple[bool, ...]: ...
    async def prepare_event_write(
        self, connection, context: TrustedContext, *, producer: str, event_id: str,
        revision: int, parts: tuple[InputPart, ...], support_refs: tuple[SourceRef, ...] = (),
        additional_refs: tuple[SourceRef, ...] = (),
    ) -> str | None: ...
    async def prepare_event_writes(
        self, connection, context: TrustedContext, events: tuple[dict, ...], *,
        additional_refs: tuple[SourceRef, ...] = (),
        scoped_refs: tuple[tuple[TrustedContext, tuple[SourceRef, ...]], ...] = (),
    ) -> tuple[str | None, ...]: ...
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
        connection=None,
        support_refs: tuple[SourceRef, ...] = (),
        generation: str | None = None,
    ) -> tuple[SourceRef, ...]: ...
    async def register_media(
        self,
        context: TrustedContext,
        media: MediaRef,
        *,
        locator: dict,
        origin_kind: OriginKind = OriginKind.HUMAN,
        generation: str | None = None,
    ) -> SourceRef: ...
    async def register_media_supplement(
        self, context: TrustedContext, media: MediaRef, *, original_ref: SourceRef,
        producer: str, event_id: str, event_revision: int, part_index: int,
        supplement_revision: int, connection=None, generation: str | None = None
    ) -> SourceRef: ...
    async def validate_media_supplement_on(
        self, connection, context: TrustedContext, supplement_ref: SourceRef, *,
        original_ref: SourceRef, producer: str, event_id: str, event_revision: int,
        part_index: int
    ) -> bool: ...
    async def publish_media(
        self,
        context: TrustedContext,
        media: MediaRef,
        *,
        locator: dict,
        source_refs: tuple[SourceRef, ...] = (),
        target: str = "actor",
        generation: str | None = None,
    ) -> SourceRef: ...
    async def capture(
        self, context: TrustedContext, *, locator: dict, raw: bytes, mime_type: str,
        generation: str | None = None
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
