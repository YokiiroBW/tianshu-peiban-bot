"""Small authority ports. Implementations belong to identity, sources, life and expression."""

from typing import Protocol, Any
from tianshu.types import TrustedContext, SourceRef, MediaRef, ArtifactRef, Receipt


class AuthorityPort(Protocol):
    async def check(
        self, context: TrustedContext, source_refs: tuple[SourceRef, ...] = ()
    ) -> None: ...


from tianshu.sources.ports import SourcePort


class LifeSnapshotPort(Protocol):
    async def scene(self, context: TrustedContext, *, activity_id: str | None = None) -> dict: ...
    async def sharing(self, context: TrustedContext, *, action_id: str, window: dict) -> dict: ...


class ExpressionPort(Protocol):
    async def send(self, context: TrustedContext, plan: Any) -> Receipt: ...


class ResultEvents(Protocol):
    async def publish(self, connection: Any, event: dict) -> None: ...


class ActorAuthorityPort(Protocol):
    async def authorize_actor(
        self, context: TrustedContext, actor_id: str, *, permission: str | None = None
    ) -> TrustedContext: ...


class ActorDirectoryPort(Protocol):
    async def roles(self, context: TrustedContext) -> list[dict]: ...
