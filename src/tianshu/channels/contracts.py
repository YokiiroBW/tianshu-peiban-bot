"""Protocol outcomes carry actual acknowledgements, never inferred delivery success."""

from dataclasses import dataclass, field
from typing import Any, Mapping, Literal, Protocol
from tianshu.runtime.errors import AppError
from tianshu.types import TrustedContext, Receipt
from tianshu.conversation.types import ExpressionPlan


class ChannelError(AppError):
    def __init__(self, code, status=502, *, effect="none", details=None):
        super().__init__(code, status=status, details=details)
        self.effect = effect


@dataclass(frozen=True)
class TransportResult:
    state: Literal["prepared", "confirmed", "accepted", "rejected", "unknown"]
    data: Any = None
    retcode: int | None = None
    error_code: str | None = None
    message_ids: tuple[str, ...] = ()
    raw: Mapping[str, Any] = field(default_factory=dict)


class WebPublisher(Protocol):
    async def publish(self, context: TrustedContext, plan: ExpressionPlan) -> Receipt: ...
    async def get(self, context: TrustedContext, expression_id: str) -> Receipt | None: ...


class ConversationIngress(Protocol):
    async def ingest(self, context, message, connection=None): ...
