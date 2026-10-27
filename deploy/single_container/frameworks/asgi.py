"""ASGI HTTP and lifespan, with concurrent streaming and disconnect delivery."""

import asyncio
from urllib.parse import unquote, urlsplit


class Application:
    def __init__(self, app):
        self.app = app
        self.started = False
        self.task = None
        self.events = asyncio.Queue()
        self.replies = asyncio.Queue()
        self.state = {}

    async def open(self):
        async def send(message):
            await self.replies.put(message)

        self.task = asyncio.create_task(
            self.app(
                {"type": "lifespan", "asgi": {"version": "3.0"}, "state": self.state},
                self.events.get,
                send,
            )
        )
        await self._event("startup")
        self.started = True

    async def _event(self, phase):
        await self.events.put({"type": "lifespan." + phase})
        reply = asyncio.create_task(self.replies.get())
        try:
            done, _ = await asyncio.wait(
                (self.task, reply), return_when=asyncio.FIRST_COMPLETED
            )
            if reply not in done:
                await self.task
                raise RuntimeError("ASGI application ended before lifespan " + phase)
            message = reply.result()
            if message["type"] != "lifespan." + phase + ".complete":
                raise RuntimeError("ASGI lifespan " + phase + " failed")
        finally:
            reply.cancel()
            await asyncio.gather(reply, return_exceptions=True)

    async def close(self):
        if self.task is None:
            return
        try:
            if self.started:
                await self._event("shutdown")
        finally:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.started = False

    async def handle(self, call):
        request = call.request
        url = urlsplit(request.url)
        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.4"},
            "http_version": "1.1",
            "method": request.method,
            "scheme": "http",
            "path": unquote(url.path or "/"),
            "raw_path": (url.path or "/").encode("ascii"),
            "query_string": url.query.encode("ascii"),
            "root_path": "",
            "headers": [(key.lower(), value) for key, value in request.headers],
            "server": (
                url.hostname,
                url.port or (443 if url.scheme == "https" else 80),
            ),
            "client": None,
            "state": self.state.copy(),
        }
        call.exchange._mark(call, scope)
        body_complete = response_complete = False

        async def receive():
            nonlocal body_complete
            if call.disconnected.is_set():
                return {"type": "http.disconnect"}
            if body_complete:
                await call.disconnected.wait()
                return {"type": "http.disconnect"}
            part = await call.input.receive()
            body_complete = part is None
            return {
                "type": "http.request",
                "body": part or b"",
                "more_body": not body_complete,
            }

        async def send(message):
            nonlocal response_complete
            if message["type"] == "http.response.start":
                call.start_response(message["status"], message.get("headers", []))
            elif message["type"] == "http.response.body":
                if not call.headers.done() or response_complete:
                    raise RuntimeError("Invalid ASGI response body order")
                if request.method != "HEAD":
                    await call.output.send(message.get("body", b""))
                if not message.get("more_body", False):
                    response_complete = True
                    await call.output.finish()
            else:
                raise RuntimeError("Unsupported ASGI HTTP message")

        await self.app(scope, receive, send)
        if not response_complete:
            raise RuntimeError("ASGI application ended before response completion")
