"""Private stdio JSON-RPC worker. stdout is exclusively the protocol stream."""

import asyncio
import contextlib
import dataclasses
import importlib
import json
from pathlib import Path
import sys
import threading

# -I deliberately ignores PYTHONPATH and the caller's user site.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tianshu_plugin import ActionResult, Context, Invocation, PluginError, Recovery
from tianshu_plugin.api import CURRENT, Tasks

MAX_FRAME = 1024 * 1024


class Worker:
    def __init__(self, package):
        self.package = Path(package)
        self.output = sys.stdout
        sys.stdout = sys.stderr
        self.manifest = json.loads((self.package / "plugin.json").read_text(encoding="utf-8"))
        self.pending, self.running, self.counter = {}, {}, 0
        self.plugin, self.tasks = None, Tasks()
        self.invocations = {}
        self.stopped = asyncio.Event()

    def send(self, document):
        text = json.dumps({"jsonrpc": "2.0", **document}, ensure_ascii=True, allow_nan=False)
        if len(text.encode()) > MAX_FRAME:
            raise PluginError("plugin_error", "Protocol frame too large", effect="unknown")
        self.output.write(text + "\n")
        self.output.flush()

    async def rpc(self, method, params):
        self.counter += 1
        identifier = "w" + str(self.counter)
        future = asyncio.get_running_loop().create_future()
        self.pending[identifier] = future
        self.send({"id": identifier, "method": method, "params": params})
        try:
            return await future
        finally:
            self.pending.pop(identifier, None)

    def load(self):
        if hasattr(self, "plugin_type"):
            return
        sys.path.insert(0, str(self.package))
        module, name = self.manifest["entrypoint"].split(":")
        self.plugin_type = getattr(importlib.import_module(module), name)
        self.actions = {}
        self.description = {"schema_version": 1, "actions": [], "commands": [], "tools": []}
        for name in dir(self.plugin_type):
            function = getattr(self.plugin_type, name)
            if hasattr(function, "_action"):
                metadata = dict(function._action)
                metadata["handler"] = name
                metadata["required_capabilities"] = [
                    {"id": identifier, "major": major}
                    for identifier, major in metadata["required_capabilities"]
                ]
                if hasattr(function, "_command"):
                    command = {"id": metadata["id"], "action": metadata["id"], **function._command}
                    if command["reply_pointer"] is None:
                        command.pop("reply_pointer")
                    self.description["commands"].append(command)
                if hasattr(function, "_tool"):
                    self.description["tools"].append({"action": metadata["id"], **function._tool})
                self.actions[metadata["id"]] = metadata
                self.description["actions"].append(metadata)
            for entry in ("event", "bridge"):
                if hasattr(function, "_" + entry):
                    metadata={**getattr(function,"_"+entry),"handler":name}
                    metadata["required_capabilities"]=[{"id":identifier,"major":major} for identifier,major in metadata.get("required_capabilities",())]
                    self.description.setdefault("events" if entry=="event" else "bridges",[]).append(metadata)
        return self.description

    async def execute(self, method, params):
        if method == "runtime.hello":
            return {"protocol": 1, "sdk_version": "2.0.0"}
        self.load()
        if method == "plugin.describe":
            return self.description
        if method == "plugin.validate":
            self.plugin_type.validate_config(params["config"], params["secret_configured"])
            return {"valid": True}
        if method == "plugin.migrate_config":
            return self.plugin_type.migrate_config(params["from_version"], params["config"])
        if method == "plugin.migrate_data":
            await self.plugin.migrate_data(params["from_version"], params["to_version"])
            return {"migrated": True}
        if method == "plugin.initialize":
            self.plugin = self.plugin_type(Context(params, self.rpc, self.tasks))
            await self.plugin.initialize()
            return {"initialized": True}
        if method == "plugin.start":
            await self.plugin.start()
            return {"ready": True}
        if method == "plugin.stop":
            for invocation in self.invocations.values():
                invocation.cancelled = True
            if self.plugin:
                await self.plugin.stop(params.get("reason", "stopped"))
            await self.tasks.close()
            return {"stopped": True}
        if method == "plugin.cancel":
            if params["invocation_id"] in self.invocations:
                self.invocations[params["invocation_id"]].cancelled = True
            task = self.running.get(params["invocation_id"])
            if task:
                task.cancel()
            return {"cancel_requested": task is not None}
        if method == "plugin.event":
            self.load()
            metadata=next((item for item in self.description.get("events",[]) if item["id"]==params["subscription_id"]),None)
            if metadata is None or metadata["event_type"]!=params["event"]["type"] or metadata["event_version"]!=params["event"].get("version",1):
                raise PluginError("event_not_subscribed",effect="none")
            context=Context(params["call"],self.rpc,self.tasks)
            token=CURRENT.set(context)
            try:
                invocation=Invocation(params["call"],context)
                result=await getattr(self.plugin,metadata["handler"])(invocation,params["event"])
                return dataclasses.asdict(result) if isinstance(result,ActionResult) else {"result":result,"effect":"none"}
            finally:CURRENT.reset(token)
        if method in {"plugin.invoke", "plugin.resume", "plugin.reconcile"}:
            envelope = params["call"]
            context = Context(envelope, self.rpc, self.tasks)
            token = CURRENT.set(context)
            invocation = Invocation(envelope, context)
            self.running[invocation.id] = asyncio.current_task()
            self.invocations[invocation.id] = invocation
            try:
                metadata = self.actions[params["action"]]
                if method == "plugin.invoke":
                    result = await getattr(self.plugin, metadata["handler"])(
                        invocation, params["input"]
                    )
                else:
                    key = "resume_handler" if method == "plugin.resume" else "reconcile_handler"
                    if not metadata.get(key):
                        raise PluginError("capability_unavailable", effect="none")
                    result = await getattr(self.plugin, metadata[key])(
                        invocation, Recovery(params["recovery"])
                    )
                if isinstance(result, ActionResult):
                    return dataclasses.asdict(result)
                if metadata["effect"] != "read":
                    raise PluginError(
                        "plugin_error", "Write action requires ActionResult", effect="unknown"
                    )
                return {"result": result, "effect": "none", "external": None, "presentation": None}
            finally:
                self.running.pop(invocation.id, None)
                self.invocations.pop(invocation.id, None)
                CURRENT.reset(token)
        raise PluginError("method_not_found", effect="none")

    async def request(self, document):
        identifier, method = document["id"], document["method"]
        try:
            result = await self.execute(method, document.get("params", {}))
            self.send({"id": identifier, "result": result})
        except asyncio.CancelledError:
            self.send(
                {
                    "id": identifier,
                    "error": {
                        "code": -32000,
                        "data": {
                            "code": "cancelled",
                            "effect": "unknown",
                            "retriable": False,
                        },
                    },
                }
            )
        except BaseException as error:
            data = {"code": "plugin_error", "effect": "unknown", "retriable": False}
            if isinstance(error, PluginError):
                data = {
                    "code": error.code,
                    "effect": error.effect,
                    "retriable": error.retriable,
                    "external": error.external,
                }
            self.send({"id": identifier, "error": {"code": -32000, "data": data}})
        finally:
            if method == "plugin.stop":
                self.stopped.set()

    async def run(self):
        queue = asyncio.Queue(maxsize=64)
        loop = asyncio.get_running_loop()

        def reader():
            while True:
                line = sys.stdin.buffer.readline(MAX_FRAME + 2)
                if not line or len(line) > MAX_FRAME + 1:
                    asyncio.run_coroutine_threadsafe(queue.put(None), loop).result()
                    return
                asyncio.run_coroutine_threadsafe(queue.put(line), loop).result()

        threading.Thread(target=reader, daemon=True).start()
        while not self.stopped.is_set():
            line_task = asyncio.create_task(queue.get())
            stop_task = asyncio.create_task(self.stopped.wait())
            done, _ = await asyncio.wait(
                [line_task, stop_task], return_when=asyncio.FIRST_COMPLETED
            )
            if stop_task in done:
                line_task.cancel()
                break
            stop_task.cancel()
            line = line_task.result()
            if line is None:
                break
            document = json.loads(line)
            if "method" in document:
                self.tasks.spawn("rpc", lambda d=document: self.request(d))
            elif (future := self.pending.get(document.get("id"))) and not future.done():
                if "error" in document:
                    data = document["error"].get("data", {})
                    future.set_exception(
                        PluginError(
                            data.get("code", "plugin_error"), effect=data.get("effect", "none")
                        )
                    )
                else:
                    future.set_result(document.get("result"))


if __name__ == "__main__":
    with contextlib.redirect_stdout(sys.stderr):
        worker = Worker(sys.argv[1])
    # Worker retains the original protocol stdout even while plugin imports print.
    worker.output = sys.__stdout__
    sys.stdout = sys.stderr
    asyncio.run(worker.run())
