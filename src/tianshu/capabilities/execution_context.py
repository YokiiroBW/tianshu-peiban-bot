"""Live invocation identity; durable authority remains in the Action ledger."""
from contextvars import ContextVar
CURRENT_ACTION=ContextVar("capability_action",default=None)
CURRENT_EXECUTION=ContextVar("capability_execution",default=None)

CURRENT_CONTEXT=ContextVar("capability_trusted_context",default=None)
