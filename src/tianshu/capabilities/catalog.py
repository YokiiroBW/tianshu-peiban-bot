"""One executable catalog; declarations never create a handler."""
from dataclasses import dataclass,field
from typing import Any, Mapping, Protocol
from jsonschema import Draft202012Validator
from tianshu.types import Action, Attempt, Receipt, TrustedContext
from .contracts import Fault

@dataclass(frozen=True)
class Availability:
    implemented: bool
    configured: bool
    enabled: bool=True
    provider_supported: bool|None=None
    verified_at: str|None=None
    reason: str|None=None
    @property
    def can_execute(self):
        return self.implemented and self.configured and self.enabled and self.provider_supported is not False

@dataclass(frozen=True)
class CapabilityDefinition:
    id: str
    title: str
    description: str
    input_schema: Mapping[str,Any]
    output_schema: Mapping[str,Any]
    effect: str="read"
    version: int=1
    permissions: frozenset[str]=frozenset()
    public_command: Mapping[str,Any]|None=None
    model_tool: Mapping[str,Any]|None=None
    long_running: bool=False

class Handler(Protocol):
    definition: CapabilityDefinition
    async def availability(self, context: TrustedContext)->Availability: ...
    async def execute(self, action: Action, attempt: Attempt)->Receipt: ...
    async def reconcile(self, action: Action, attempt: Attempt)->Receipt: ...
    async def cancel(self, action: Action, attempt: Attempt)->Receipt: ...

class Catalog:
    def __init__(self): self.handlers={}
    def register(self, handler):
        definition=handler.definition
        if definition.id in self.handlers: raise ValueError("duplicate_capability")
        Draft202012Validator.check_schema(definition.input_schema)
        Draft202012Validator.check_schema(definition.output_schema)
        self.handlers[definition.id]=handler
    async def discover(self, context):
        result=[]
        for handler in self.handlers.values():
            definition=handler.definition
            if "*" not in context.permissions and not definition.permissions <= context.permissions: continue
            availability=await handler.availability(context)
            result.append({"definition":definition,"availability":availability,"can_execute":availability.can_execute})
        return result
    def handler(self, name):
        if name not in self.handlers: raise Fault("adapter_not_installed",503)
        return self.handlers[name]
