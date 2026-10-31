"""Small plugin authoring API; no dependency on Platform or its databases."""

import asyncio
import contextvars
import copy
from dataclasses import dataclass
from types import MappingProxyType

CURRENT = contextvars.ContextVar("plugin_context", default=None)


class PluginError(Exception):
    def __init__(self, code, message="", *, effect="none", retriable=False, external=None):
        super().__init__(message)
        self.code, self.effect = code, effect
        self.message, self.retriable, self.external = message, retriable, external


@dataclass(frozen=True)
class ActionResult:
    result: object
    effect: str = "none"
    external: dict | None = None
    presentation: list | None = None


@dataclass(frozen=True)
class StateValue:
    value: object
    revision: int


class Capability:
    def __init__(self, context, identifier, major):
        self.context, self.identifier, self.major = context, identifier, major

    async def call(self, operation, input):
        return await self.context._rpc(
            "host.capability",
            {
                "context_ref": self.context._reference,
                "capability": self.identifier,
                "major": self.major,
                "operation": operation,
                "input": dict(input),
            },
        )


class State:
    def __init__(self, context):
        self.port = context.capability("state.kv")

    async def get(self, key):
        result = await self.port.call("get", {"key": key})
        return StateValue(**result) if result is not None else None

    async def compare_and_set(self, key, value, *, expected_revision):
        result = await self.port.call(
            "compare_and_set",
            {
                "key": key,
                "value": value,
                "expected_revision": expected_revision,
            },
        )
        return StateValue(**result)

    async def delete(self, key, *, expected_revision):
        await self.port.call("delete", {"key": key, "expected_revision": expected_revision})


class ManagedTask:
    def __init__(self, task):
        self.task = task

    def cancel(self):
        self.task.cancel()

    async def wait(self):
        await self.task


class Tasks:
    def __init__(self):
        self.active = set()

    def spawn(self, name, work):
        task = asyncio.create_task(work(), name=name)
        self.active.add(task)
        task.add_done_callback(self.active.discard)
        return ManagedTask(task)

    async def close(self):
        tasks = tuple(task for task in self.active if task is not asyncio.current_task())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


class Log:
    def __init__(self, context):
        self.context = context

    def _emit(self, level, event, fields):
        # Logs are tracked tasks and therefore participate in instance shutdown.
        self.context.tasks.spawn(
            "log",
            lambda: self.context._rpc(
                "host.log",
                {
                    "context_ref": self.context._reference,
                    "level": level,
                    "event": event,
                    "fields": fields,
                },
            ),
        )

    def info(self, event, **fields):
        self._emit("info", event, fields)

    def warning(self, event, **fields):
        self._emit("warning", event, fields)

    def error(self, event, **fields):
        self._emit("error", event, fields)


class LocalActions:
    def __init__(self, context):
        self.port = context.capability("jobs.manage")

    async def invoke(self, action, input, *, intent_key):
        return await self.port.call(
            "submit",
            {
                "action": action,
                "input": input,
                "intent_key": intent_key,
            },
        )


class Context:
    def __init__(self, envelope, rpc, tasks):
        self._rpc, self._reference = rpc, envelope["context_ref"]
        self.instance_id = envelope["instance_id"]
        self.config_revision = envelope["config_revision"]
        self.config = MappingProxyType(copy.deepcopy(envelope["config"]))
        self.secret_configured = MappingProxyType(dict(envelope["secret_configured"]))
        self._capabilities = frozenset(envelope["capabilities"])
        self.tasks, self.log = tasks, Log(self)

    @property
    def state(self):
        return State(self)

    @property
    def actions(self):
        return LocalActions(self)

    def capability(self, id, *, major=1):
        if not self.has_capability(id, major=major):
            raise PluginError("capability_unavailable", effect="none")
        return Capability(self, id, major)

    def has_capability(self, id, *, major=1):
        return major == 1 and id in self._capabilities


class Invocation:
    def __init__(self, envelope, context):
        self.id, self.intent_key = envelope["invocation_id"], envelope["intent_key"]
        self.ctx, self.cancelled = context, False

    async def progress(self, *, completed, total=None, message=""):
        await self.ctx._rpc(
            "host.progress",
            {
                "context_ref": self.ctx._reference,
                "completed": completed,
                "total": total,
                "message": message,
            },
        )

    async def checkpoint(self, state):
        result = await self.ctx.capability("jobs.manage").call("checkpoint", {"state": state})
        return result["checkpoint_ref"]


class Recovery:
    def __init__(self, document):
        self.input = document["input"]
        checkpoint = document.get("checkpoint")
        self.checkpoint = StateValue(**checkpoint) if checkpoint else None
        self.previous_receipt, self.dispatch = document["previous_receipt"], document["dispatch"]


class Plugin:
    def __init__(self, ctx):
        self._maintenance = ctx

    @property
    def ctx(self):
        return CURRENT.get() or self._maintenance

    async def initialize(self):
        pass

    async def start(self):
        pass

    async def stop(self, reason):
        pass

    @classmethod
    def validate_config(cls, config, secret_configured):
        pass

    @classmethod
    def migrate_config(cls, from_version, config):
        return config

    async def migrate_data(self, from_version, to_version):
        if from_version != to_version:
            raise PluginError("config_invalid", "Data migration is not implemented", effect="none")


def action(**metadata):
    def decorate(function):
        function._action = {
            "effect": "read",
            "timeout_seconds": 30,
            "idempotency": "none",
            "required_capabilities": (),
            "resumable": False,
            **metadata,
        }
        return function

    return decorate


def command(*names, arguments=(), reply_pointer=None):
    def decorate(function):
        function._command = {
            "names": list(names),
            "arguments": list(arguments),
            "reply_pointer": reply_pointer,
        }
        return function

    return decorate


def tool(name, *, description):
    def decorate(function):
        function._tool = {"name": name, "description": description}
        return function

    return decorate


def on_event(type, *, version, id, required_capabilities=()):
    def decorate(function):
        function._event = {
            "event_type": type,
            "event_version": version,
            "id": id,
            "required_capabilities": required_capabilities,
        }
        return function

    return decorate


def bridge(**metadata):
    def decorate(function):
        function._bridge = metadata
        return function

    return decorate
