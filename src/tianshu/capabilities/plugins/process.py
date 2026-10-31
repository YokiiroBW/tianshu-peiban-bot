"""Bounded bidirectional RPC and supervised process lifetime."""

import asyncio
import json
from uuid import uuid4
from pathlib import Path

from ..contracts import Fault, canonical, require
from .packages import clean_environment

MAX_FRAME = 1024 * 1024
import tianshu_plugin
WORKER = Path(tianshu_plugin.__file__).with_name("worker.py")


class RemoteError(Exception):
    def __init__(self, data):
        self.data = data


class Process:
    def __init__(self, package, callback):
        self.runtime_id = str(uuid4())
        self.package, self.callback = package, callback
        self.pending, self.counter = {}, 0
        self.process, self.reader, self.stderr = None, None, None
        self.handlers = set()
        self.write_lock = asyncio.Lock()

    async def start(self):
        self.process = await asyncio.create_subprocess_exec(
            self.package["python"],
            "-I",
            "-u",
            str(WORKER),
            self.package["root"],
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=clean_environment(),
            cwd=self.package["root"],
            limit=MAX_FRAME + 2,
        )
        self.reader = asyncio.create_task(self.read())
        self.stderr = asyncio.create_task(self.drain_stderr())
        hello = await self.call("runtime.hello", {}, timeout=10)
        require(hello == {"protocol": 1, "sdk_version": "2.0.0"}, "plugin_incompatible", 409)

    @property
    def alive(self):
        return (
            self.process is not None and self.process.returncode is None and not self.reader.done()
        )

    async def send(self, document):
        frame = canonical({"jsonrpc": "2.0", **document}).encode() + b"\n"
        require(len(frame) <= MAX_FRAME + 1, "budget_exceeded", 413)
        async with self.write_lock:
            self.process.stdin.write(frame)
            await self.process.stdin.drain()

    async def call(self, method, params, *, timeout=30):
        require(self.alive, "plugin_unavailable", 503)
        self.counter += 1
        identifier = "h" + str(self.counter)
        future = asyncio.get_running_loop().create_future()
        self.pending[identifier] = future
        try:
            await self.send({"id": identifier, "method": method, "params": params})
            async with asyncio.timeout(timeout):
                return await future
        finally:
            self.pending.pop(identifier, None)

    async def reply(self, document):
        try:
            result = await self.callback(document["method"], document.get("params", {}))
            await self.send({"id": document["id"], "result": result})
        except Fault as error:
            await self.send(
                {
                    "id": document["id"],
                    "error": {
                        "code": -32000,
                        "data": {"code": error.code, "effect": getattr(error, "effect", "none")},
                    },
                }
            )
        except (OSError, ValueError, KeyError, TypeError):
            await self.send({"id": document["id"], "error": {"code": -32602}})

    async def read(self):
        try:
            while True:
                raw = await self.process.stdout.readline()
                if not raw:
                    break
                require(len(raw) <= MAX_FRAME + 1, "budget_exceeded", 413)
                document = json.loads(raw)
                require(document.get("jsonrpc") == "2.0", "plugin_error", 502)
                if "method" in document:
                    require(len(self.handlers) < 32, "budget_exceeded", 429)
                    task = asyncio.create_task(self.reply(document))
                    self.handlers.add(task)
                    task.add_done_callback(self.handlers.discard)
                elif (future := self.pending.get(document.get("id"))) and not future.done():
                    if "error" in document:
                        future.set_exception(
                            RemoteError(document["error"].get("data", {"code": "plugin_error"}))
                        )
                    else:
                        future.set_result(document.get("result"))
        except (OSError, ValueError, Fault, asyncio.CancelledError):
            pass
        finally:
            for future in tuple(self.pending.values()):
                if not future.done():
                    future.set_exception(Fault("plugin_unavailable", 503))

    async def drain_stderr(self):
        # Arbitrary plugin print/tracebacks may contain private data. Drain to avoid
        # pipe deadlocks; SDK structured logs go through the redacting host port.
        while await self.process.stderr.read(8192):
            pass

    async def close(self, reason="stopped"):
        if self.alive:
            try:
                await self.call("plugin.stop", {"reason": reason}, timeout=5)
            except (Fault, RemoteError, TimeoutError, OSError):
                pass
        if self.process:
            if self.process.returncode is None:
                self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=3)
            except TimeoutError:
                self.process.kill()
                await self.process.wait()
        for task in (self.reader, self.stderr, *tuple(self.handlers)):
            if task and not task.done():
                task.cancel()
        await asyncio.gather(
            *(task for task in (self.reader, self.stderr, *tuple(self.handlers)) if task),
            return_exceptions=True,
        )
