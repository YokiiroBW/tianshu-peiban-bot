"""Actual HTTP/WS transports; reconnect never resubmits an unknown API call."""

import asyncio
import json
import logging
from urllib.parse import quote
import aiohttp
from .contracts import ChannelError, TransportResult

MAX_RESPONSE = 4 * 1024 * 1024


def onebot_result(raw):
    if (not isinstance(raw, dict) or not isinstance(raw.get("retcode"), int)
            or isinstance(raw.get("retcode"), bool)):
        return TransportResult("unknown", error_code="onebot_invalid_response")
    code = raw["retcode"]
    status = raw.get("status")
    if status == "async" or code == 1:
        return TransportResult("accepted", raw.get("data"), code, raw=raw)
    if status == "ok" and code == 0:
        data = raw.get("data")
        identifier = data.get("message_id") if isinstance(data, dict) else None
        return TransportResult(
            "confirmed",
            data,
            code,
            message_ids=(str(identifier),)
            if isinstance(identifier, (str, int)) and not isinstance(identifier, bool) and str(identifier) != "0"
            else (),
            raw=raw,
        )
    if status == "failed" and code != 0:
        return TransportResult("rejected", raw.get("data"), code, "onebot_api_failed", raw=raw)
    return TransportResult(
        "unknown", raw.get("data"), code, "onebot_inconsistent_response", raw=raw
    )


async def bounded_json(response, maximum=MAX_RESPONSE):
    raw = bytearray()
    async for chunk in response.content.iter_chunked(65536):
        raw.extend(chunk)
        if len(raw) > maximum:
            raise ChannelError("channel_response_budget", 413, effect="unknown")
    try:
        return json.loads(raw)
    except ValueError:
        raise ChannelError("channel_invalid_json", 502, effect="unknown") from None


class OneBotTransport:
    def __init__(self, connection, token, *, on_event, on_response):
        self.connection, self.token = connection, token
        self.on_event, self.on_response = on_event, on_response
        self.session = None
        self.task = None
        self.sockets = {}
        self.pending = {}
        self.streams = {}
        self.stopped = False

    async def start(self):
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=40), connector=aiohttp.TCPConnector(limit=4)
        )
        if self.connection["transport"] == "ws":
            self.task = asyncio.create_task(
                self.connect(), name="onebot-ws:" + self.connection["id"]
            )

    async def connect(self):
        delay = 1
        while not self.stopped:
            try:
                async with self.session.ws_connect(
                    self.connection["endpoint"],
                    headers={"Authorization": "Bearer " + self.token},
                    heartbeat=20,
                    max_msg_size=48 * 1024 * 1024 if self.connection.get("provider") == "snowluma" else MAX_RESPONSE,
                ) as socket:
                    delay = 1
                    await self.attach(socket, "Universal")
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logging.getLogger(__name__).warning(
                    "onebot_connection_interrupted",
                    extra={
                        "connection_id": self.connection["id"],
                        "error_code": getattr(error, "code", type(error).__name__),
                    },
                )
            if not self.stopped:
                await asyncio.sleep(delay)
            delay = min(30, delay * 2)

    async def attach(self, socket, role):
        if role not in {"Universal", "API", "Event"}:
            raise ChannelError("onebot_ws_role", 400)
        old = self.sockets.get(role)
        if old is not None and old is not socket:
            await old.close(code=1012, message=b"connection replaced")
        self.sockets[role] = socket
        try:
            async for message in socket:
                if message.type == aiohttp.WSMsgType.TEXT:
                    try:
                        body = json.loads(message.data)
                    except ValueError:
                        await socket.close(code=1003)
                        break
                    if "echo" in body and "retcode" in body:
                        echo = str(body["echo"])
                        stream = self.streams.get(echo)
                        if stream is not None and stream[0] is socket:
                            from .snowluma.streaming import enqueue

                            await enqueue(stream[1], stream[2], body)
                            continue
                        if body.get("stream") == "stream-action":
                            # A late frame cannot prove a complete, validated download.
                            continue
                        result = onebot_result(body)
                        pending = self.pending.get(echo)
                        future = pending[1] if pending and pending[0] is socket else None
                        if future is not None and not future.done():
                            future.set_result(result)
                        else:
                            await self.on_response(self.connection["id"], echo, result)
                    elif "post_type" in body:
                        await self.on_event(self.connection["id"], body)
                elif message.type in {aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR}:
                    break
        finally:
            for stream_socket, queue, _closed in tuple(self.streams.values()):
                if stream_socket is socket:
                    if queue.full():
                        queue.get_nowait()
                    queue.put_nowait(None)
            if self.sockets.get(role) is socket:
                self.sockets.pop(role, None)
            if role in {"Universal", "API"}:
                for pending_socket, future in tuple(self.pending.values()):
                    if pending_socket is socket and not future.done():
                        future.set_result(
                            TransportResult("unknown", error_code="onebot_ws_disconnected")
                        )

    async def call(self, identifier, name, params, submitted, *, context=None, source_refs=()):
        from .onebot.registry import resolve_api

        spec = resolve_api(name, self.connection)
        if spec is not None and spec.stream:
            from .snowluma.streaming import call

            return await call(self, identifier, name, params, submitted,
                              context=context, source_refs=source_refs)
        transport = self.connection["transport"]
        if transport in {"ws", "reverse_ws"}:
            socket = self.sockets.get("API") or self.sockets.get("Universal")
            if socket is None or socket.closed:
                raise ChannelError("channel_socket_not_connected", 503)
            future = asyncio.get_running_loop().create_future()
            self.pending[identifier] = (socket, future)
            try:
                await submitted()
                await socket.send_json({"action": name, "params": params, "echo": identifier})
                return await asyncio.wait_for(asyncio.shield(future), 35)
            except asyncio.TimeoutError:
                return TransportResult("unknown", error_code="onebot_echo_timeout")
            finally:
                self.pending.pop(identifier, None)
        endpoint = self.connection.get("api_endpoint") or self.connection.get("endpoint")
        if not endpoint:
            raise ChannelError("channel_api_endpoint_required", 503)
        try:
            await submitted()
            async with self.session.post(
                endpoint.rstrip("/") + "/" + quote(name, safe="._"),
                json=params,
                headers={"Authorization": "Bearer " + self.token},
                allow_redirects=False,
            ) as response:
                if 400 <= response.status < 500:
                    return TransportResult(
                        "rejected",
                        retcode=response.status,
                        error_code="onebot_http_rejected",
                        raw={"status": "failed", "retcode": response.status},
                    )
                if response.status >= 500:
                    return TransportResult(
                        "unknown", retcode=response.status, error_code="onebot_http_server_error"
                    )
                if response.status != 200:
                    return TransportResult("unknown", error_code="onebot_http_status")
                return onebot_result(await bounded_json(response))
        except asyncio.CancelledError:
            raise
        except (aiohttp.ClientError, OSError, ChannelError) as error:
            return TransportResult(
                "unknown", error_code=getattr(error, "code", "onebot_transport_interrupted")
            )

    async def close(self):
        self.stopped = True
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        for socket in tuple(self.sockets.values()):
            await socket.close(code=1001, message=b"owner shutting down")
        if self.session:
            await self.session.close()


class OwnerClient:
    """Fixed authenticated internal RPC. It never creates a second socket owner."""

    def __init__(self, settings, credentials):
        self.settings, self.credentials = settings, credentials

    async def execute(self, identifier):
        return await self.request("execute", identifier)

    async def cancel(self, identifier):
        return await self.request("cancel", identifier)

    async def reconcile(self, identifier):
        return await self.request("reconcile", identifier)

    async def request(self, operation, identifier):
        base = self.settings.get("owner_url")
        reference = self.settings.get("owner_token_ref")
        if not base or not reference:
            raise ChannelError("channel_owner_not_configured", 503)
        token = await self.credentials.resolve(reference)
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=45)) as session:
            try:
                async with session.post(
                    base.rstrip("/") + "/internal/channels/" + operation,
                    json={"call_id": identifier},
                    headers={"Authorization": "Bearer " + token},
                    allow_redirects=False,
                ) as response:
                    if response.status != 200:
                        raise ChannelError("channel_owner_rpc_failed", 503, effect="unknown")
                    return await bounded_json(response)
            except asyncio.CancelledError:
                raise
            except (aiohttp.ClientError, asyncio.TimeoutError):
                raise ChannelError("channel_owner_rpc_interrupted", 503, effect="unknown") from None
