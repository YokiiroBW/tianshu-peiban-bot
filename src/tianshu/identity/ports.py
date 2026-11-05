"""Verified target selection; request fields never grant scope or delivery rights."""

from typing import Any, Protocol

from tianshu.runtime.api import CommandResult, Page
from tianshu.types import TrustedContext
from .types import AdmissionDecision, IncomingEvent, SceneAuthority


class IdentityPort(Protocol):
    def scene_id(self, context: TrustedContext) -> str: ...
    async def scene_authority_on(
        self, connection, context: TrustedContext
    ) -> SceneAuthority: ...
    async def lock_context_on(
        self, connection, context: TrustedContext, *, exclusive_scope_ids: tuple[str, ...] = ()
    ) -> TrustedContext: ...
    async def system_context(
        self, principal_id: str, *, permission: str, actor_id: str | None = None,
        connection=None
    ) -> TrustedContext: ...
    async def publication_scope(
        self, context: TrustedContext, stable_id: str, reader_ids: tuple[str, ...],
        conversation_ids: tuple[str, ...], *, connection
    ) -> tuple[tuple[str, ...], dict[str, int]]: ...
    async def refresh_scheduled_context(
        self, previous: TrustedContext, *, permission: str, connection=None,
        require_actor: bool = True
    ) -> TrustedContext: ...
    async def for_participation(
        self, context: TrustedContext, *, source_refs=()
    ) -> TrustedContext | None: ...
    async def memory_subjects_on(self, connection, context: TrustedContext) -> tuple[dict, ...]: ...
    async def event_author_on(
        self, connection, context: TrustedContext, producer: str, event_id: str, revision: int
    ) -> str | None: ...
    async def archive_scene_on(self, connection, context: TrustedContext) -> tuple[dict, ...]: ...
    async def publication_recipients(
        self, context: TrustedContext, *, cursor: str | None = None, limit: int = 100
    ): ...
    async def context_on(
        self,
        connection,
        principal_id: str,
        *,
        actor_id=None,
        person_id=None,
        conversation_id=None,
        permission=None,
        origin_kind=None,
    ) -> TrustedContext: ...
    async def for_request(
        self, request: Any, *, actor_id=None, person_id=None, conversation_id=None, permission=None
    ) -> TrustedContext: ...
    async def authorize_actor(
        self, context: TrustedContext, actor_id: str, *, permission=None
    ) -> TrustedContext: ...
    async def authorize_conversation(
        self, context: TrustedContext, conversation_id: str, *, permission=None
    ) -> TrustedContext: ...
    async def connection(self, context: TrustedContext, connection_id: str) -> dict: ...
    async def channel_target(self, context: TrustedContext, conversation_id: str) -> dict: ...
    async def channel_context(
        self, connection_id: str, conversation_external_id: str
    ) -> TrustedContext: ...
    async def admit_event(self, connection_id: str, event: IncomingEvent) -> AdmissionDecision: ...
    async def conversations(
        self, context: TrustedContext, actor_id: str, *, cursor=None, limit=50
    ) -> Page: ...
    async def open_web_conversation(
        self,
        context: TrustedContext,
        actor_id: str,
        *,
        client_request_id: str,
        expected_revision=None,
    ) -> CommandResult: ...
