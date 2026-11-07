"""SnowLuma stream frames over HTTP or WS, with bounded file-store intake."""

import asyncio
import base64
import binascii
import json

from tianshu.runtime.serialization import wire

from ..contracts import ChannelError, TransportResult

MAX_FRAME = 48 * 1024 * 1024  # Upstream permits a 32 MiB binary chunk.


async def enqueue(queue, closed, frame):
    """Backpressure may end immediately when the request is cancelled."""
    tasks = [asyncio.create_task(queue.put(frame)), asyncio.create_task(closed.wait())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


class StreamFailed(Exception):
    def __init__(self, result):
        self.result = result


async def http_frames(response):
    buffer = bytearray()
    async for chunk in response.content.iter_chunked(65536):
        buffer.extend(chunk)
        while b"\n" in buffer:
            line, _, rest = buffer.partition(b"\n")
            buffer = bytearray(rest)
            if len(line) > MAX_FRAME:
                raise ChannelError("channel_stream_frame_budget", 413, effect="unknown")
            if line.strip():
                yield decode(line)
        if len(buffer) > MAX_FRAME:
            raise ChannelError("channel_stream_frame_budget", 413, effect="unknown")
    if buffer.strip():
        yield decode(buffer)


def decode(value):
    try:
        return json.loads(value)
    except (ValueError, UnicodeError):
        raise ChannelError("channel_invalid_json", 502, effect="unknown") from None


async def collect(frames, name, *, artifacts=None, context=None, connection=None, source_refs=()):
    from ..transports import onebot_result

    download = name in {
        "download_file_stream",
        "download_file_image_stream",
        "download_file_record_stream",
    }
    if download and (artifacts is None or context is None):
        raise ChannelError("channel_stream_context_required", 503)
    last = None
    count = size = 0
    filename = "snowluma-download"
    declared_size = None
    summaries = []
    terminal = False

    async def chunks():
        nonlocal last, count, size, filename, declared_size, terminal
        async for raw in frames:
            outcome = onebot_result(raw)
            if outcome.state != "confirmed":
                raise StreamFailed(outcome)
            packet = raw.get("data")
            if not isinstance(packet, dict) or terminal:
                raise ChannelError("channel_stream_protocol", 502, effect="unknown")
            kind, data_type = packet.get("type"), packet.get("data_type")
            if kind in {"error", "reset"}:
                raise StreamFailed(
                    TransportResult("rejected", packet, raw=raw, error_code="channel_stream_failed")
                )
            last = raw
            if data_type == "file_info":
                if count or declared_size is not None:
                    raise ChannelError("channel_stream_protocol", 502, effect="unknown")
                filename = str(packet.get("file_name", filename))[:512]
                declared_size = packet.get("file_size")
            elif data_type == "file_chunk":
                if not download or packet.get("index") != count:
                    raise ChannelError("channel_stream_chunk_order", 502, effect="unknown")
                try:
                    value = base64.b64decode(packet["data"], validate=True)
                except (KeyError, ValueError, TypeError, binascii.Error):
                    raise ChannelError(
                        "channel_stream_chunk_invalid", 502, effect="unknown"
                    ) from None
                if packet.get("size") != len(value):
                    raise ChannelError("channel_stream_chunk_size", 502, effect="unknown")
                count += 1
                size += len(value)
                yield value
            elif kind == "response":
                terminal = True
            elif not download:
                summaries.append(packet)
                if len(summaries) > 64 or len(json.dumps(summaries).encode()) > 1024 * 1024:
                    raise ChannelError("channel_stream_response_budget", 413, effect="unknown")
            else:
                raise ChannelError("channel_stream_protocol", 502, effect="unknown")
        if last is None:
            raise ChannelError("channel_stream_incomplete", 502, effect="unknown")
        packet = last["data"]
        if download:
            if (
                not terminal
                or packet.get("data_type") != "file_complete"
                or packet.get("total_chunks") != count
                or packet.get("total_bytes") != size
                or declared_size not in {None, 0, size}
            ):
                raise ChannelError("channel_stream_incomplete", 502, effect="unknown")
        elif not terminal and not (
            name == "upload_file_stream"
            and packet.get("status") in {"file_created", "chunk_received"}
        ):
            raise ChannelError("channel_stream_incomplete", 502, effect="unknown")

    try:
        if download:
            media = await artifacts.files.put_async(
                chunks(), max_bytes=(connection or {}).get("max_media_bytes", 128 * 1024 * 1024)
            )
            artifact = await artifacts.record(
                context,
                media,
                source_refs=tuple(source_refs),
                metadata={
                    "connection_id": (connection or {}).get("id"),
                    "api_name": name,
                    "filename": filename,
                },
            )
            result = {**last, "data": {**last["data"], "artifact": wire(artifact)}}
        else:
            async for _ in chunks():
                pass
            result = {**last, "data": {**last["data"], "frames": summaries}}
        return onebot_result(result)
    except StreamFailed as error:
        return error.result


async def call(transport, identifier, name, params, submitted, *, context, source_refs):
    import aiohttp

    connection = transport.connection
    if name.startswith("download_file_") and context is None:
        raise ChannelError("channel_stream_context_required", 503)
    endpoint = connection.get("api_endpoint") or (
        connection.get("endpoint") if connection["transport"] not in {"ws", "reverse_ws"} else None
    )
    try:
        if endpoint:
            from urllib.parse import quote

            await submitted()
            async with transport.session.post(
                endpoint.rstrip("/") + "/" + quote(name, safe="._"),
                json=params,
                headers={"Authorization": "Bearer " + transport.token},
                allow_redirects=False,
                timeout=aiohttp.ClientTimeout(total=None, sock_connect=15, sock_read=35),
            ) as response:
                if response.status != 200:
                    return TransportResult(
                        "rejected" if 400 <= response.status < 500 else "unknown",
                        retcode=response.status,
                        error_code="channel_stream_http_failed",
                    )
                return await collect(
                    http_frames(response),
                    name,
                    artifacts=getattr(transport, "artifacts", None),
                    context=context,
                    connection=connection,
                    source_refs=source_refs,
                )
        socket = transport.sockets.get("API") or transport.sockets.get("Universal")
        if socket is None or socket.closed:
            raise ChannelError("channel_socket_not_connected", 503)
        queue = asyncio.Queue(maxsize=2)
        closed = asyncio.Event()
        transport.streams[identifier] = (socket, queue, closed)
        try:
            await submitted()
            await socket.send_json({"action": name, "params": params, "echo": identifier})

            async def frames():
                while True:
                    raw = await asyncio.wait_for(queue.get(), 35)
                    if raw is None:
                        raise ChannelError("onebot_ws_disconnected", 503, effect="unknown")
                    yield raw
                    packet = raw.get("data") or {}
                    if (
                        raw.get("retcode") != 0
                        or name == "upload_file_stream"
                        or packet.get("type") in {"response", "error", "reset"}
                    ):
                        break

            return await collect(
                frames(),
                name,
                artifacts=getattr(transport, "artifacts", None),
                context=context,
                connection=connection,
                source_refs=source_refs,
            )
        finally:
            closed.set()
            transport.streams.pop(identifier, None)
    except asyncio.CancelledError:
        raise
    except (aiohttp.ClientError, TimeoutError, OSError, ChannelError) as error:
        return TransportResult(
            "unknown", error_code=getattr(error, "code", "channel_stream_interrupted")
        )
