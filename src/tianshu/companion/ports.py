"""Small public boundaries consumed by companion, life and conversation."""
from typing import Any, Protocol
from datetime import datetime
from tianshu.types import TrustedContext, SourceRef, RequestBudget
from tianshu.memory.types import EpisodePage
from tianshu.runtime.api import Page


class IdentityPort(Protocol):
    async def for_request(self, request: Any, *, actor_id: str | None = None,
                          conversation_id: str | None = None,
                          person_id: str | None = None,
                          permission: str | None = None) -> TrustedContext: ...
    async def check(self, context: TrustedContext,
                    source_refs: tuple[SourceRef, ...] = ()) -> None: ...
    async def check_many(self, context: TrustedContext,
                         refs_groups: tuple[tuple[SourceRef, ...], ...]) -> tuple[bool, ...]: ...


class MemoryPort(Protocol):
    async def relationship(self, context: TrustedContext, person_id: str) -> dict: ...
    async def projection(self, context: TrustedContext, person_id: str | None = None,
                         group_id: str | None = None) -> dict: ...
    async def observe_episode(self, context: TrustedContext, episode: dict, *, connection=None) -> Any: ...
    async def episodes_since(self, context: TrustedContext, cursor: str | None,
                             limit: int) -> EpisodePage: ...
    async def episodes_by_refs(self, context: TrustedContext, refs: tuple[tuple[str, int], ...],
                               *, connection=None) -> Page: ...
    async def episodes_between(self, context: TrustedContext, start: datetime,
                               end: datetime, limit: int = 200,
                               cursor: str | None = None) -> EpisodePage: ...
    async def record_reflection(self, context: TrustedContext, proposal: Any, *,
                                connection=None, episode_revisions: dict[str, int] | None = None) -> Any: ...
    async def style_view(self, context: TrustedContext, person_id: str | None = None) -> dict: ...
    async def link_matter(self, context: TrustedContext, source_ref: SourceRef,
                          matter_id: str) -> None: ...


class ContextPort(Protocol):
    async def assemble(self, context: TrustedContext, purpose: str, query: str,
                       budget: RequestBudget, *, persona_text: str = "",
                       recent_messages: tuple = (), tools: tuple = (),
                       max_input_tokens: int = 16000, reserved_output_tokens: int = 2048,
                       contributions: tuple = ()) -> Any: ...
