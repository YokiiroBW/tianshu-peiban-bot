"""Live invocation identity; durable authority remains in the Action ledger."""
from contextvars import ContextVar
CURRENT_ACTION=ContextVar("capability_action",default=None)
CURRENT_EXECUTION=ContextVar("capability_execution",default=None)

CURRENT_CONTEXT=ContextVar("capability_trusted_context",default=None)


def current_business_deadline(context):
    """A nested read inherits only the current action's exact trusted scene."""
    action = CURRENT_ACTION.get()
    return action.deadline_at if action is not None and action.context == context else None
