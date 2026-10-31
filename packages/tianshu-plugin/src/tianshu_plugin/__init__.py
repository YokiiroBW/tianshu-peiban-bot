"""Public Python plugin SDK. Business authority always belongs to the host."""

from .api import (
    ActionResult,
    Context,
    Invocation,
    Plugin,
    PluginError,
    Recovery,
    action,
    bridge,
    command,
    on_event,
    tool,
)

__version__ = "1.0.0"
__all__ = [
    "ActionResult",
    "Context",
    "Invocation",
    "Plugin",
    "PluginError",
    "Recovery",
    "action",
    "bridge",
    "command",
    "on_event",
    "tool",
]
