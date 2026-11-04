"""One executable catalog; declarations never create a handler."""

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol
from jsonschema import Draft202012Validator
from tianshu.types import Action, Attempt, Receipt, TrustedContext
from .contracts import Fault


@dataclass(frozen=True)
class Availability:
    implemented: bool
    configured: bool
    enabled: bool = True
    provider_supported: bool | None = None
    verified_at: str | None = None
    reason: str | None = None
    constraints: Mapping[str, Any] = field(default_factory=dict)

    @property
    def can_execute(self):
        return (
            self.implemented
            and self.configured
            and self.enabled
            and self.provider_supported is not False
        )


@dataclass(frozen=True)
class CapabilityDefinition:
    id: str
    title: str
    description: str
    input_schema: Mapping[str, Any]
    output_schema: Mapping[str, Any]
    effect: str = "read"
    version: int = 1
    permissions: frozenset[str] = frozenset()
    public_command: Mapping[str, Any] | None = None
    model_tool: Mapping[str, Any] | None = None
    long_running: bool = False


def is_administrative(definition: CapabilityDefinition) -> bool:
    """Configuration/operations metadata, independent of execution effects or API names."""
    return "config.publish" in definition.permissions or (
        "external.manage" in definition.permissions
        and definition.model_tool is None
        and definition.public_command is None
    )


class Handler(Protocol):
    definition: CapabilityDefinition

    async def availability(self, context: TrustedContext) -> Availability: ...
    async def execute(self, action: Action, attempt: Attempt) -> Receipt: ...
    async def reconcile(self, action: Action, attempt: Attempt) -> Receipt: ...
    async def cancel(self, action: Action, attempt: Attempt) -> Receipt: ...


class Catalog:
    def __init__(self):
        self.handlers = {}
        self.refresher = None
        self.configuration = None
        self.on_change = None
        from .catalog_observations import CatalogObservations
        self.observations = CatalogObservations(self)

    def register(self, handler):
        definition = handler.definition
        if definition.id in self.handlers:
            raise ValueError("duplicate_capability")
        Draft202012Validator.check_schema(definition.input_schema)
        Draft202012Validator.check_schema(definition.output_schema)
        self.handlers[definition.id] = handler

    def visible_handlers(self, context):
        result = []
        for handler in tuple(self.handlers.values()):
            if hasattr(handler, "visible") and not handler.visible(context):
                continue
            if (
                hasattr(handler, "visible_actor_id")
                and handler.visible_actor_id != context.actor_id
            ):
                continue
            definition = handler.definition
            if "*" not in context.permissions and not definition.permissions <= context.permissions:
                continue
            result.append(handler)
        return result

    async def discover(self, context):
        if self.refresher is not None:
            await self.refresher(context)
        result = []
        for handler in self.visible_handlers(context):
            definition = handler.definition
            availability = await handler.availability(context)
            result.append(
                {
                    "definition": definition,
                    "availability": availability,
                    "can_execute": availability.can_execute,
                }
            )
        return (
            await self.configuration.project(context, result)
            if self.configuration is not None
            else result
        )

    async def planning(self, context):
        """Read declarations and fresh observations; refresh missing ones in the background."""
        if self.refresher is not None:
            await self.refresher(context)
        revisions = {}
        configuration = getattr(self.configuration, "configuration", None)
        if configuration is not None:
            revisions = await configuration.availability_revisions()
        rows = self.observations.rows(context, self.visible_handlers(context), revisions)
        if self.configuration is not None:
            rows = await self.configuration.project(context, rows)
        # Data-only manifests may declare an uninstalled adapter; that is known
        # to be non-executable, not a successful availability observation.
        for row in rows:
            row.setdefault("freshness", "declaration")
            row.setdefault("observed_at", None)
            row.setdefault("expires_at", None)
        return rows

    async def close(self):
        await self.observations.close()

    async def availability(self, context, handler):
        availability = await handler.availability(context)
        if self.configuration is not None:
            availability = await self.configuration.availability(context, handler, availability)
        return availability

    def handler(self, name):
        if name not in self.handlers:
            raise Fault("adapter_not_installed", 503)
        return self.handlers[name]
