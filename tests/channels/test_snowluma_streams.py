"""Real local HTTP/WS streaming, without any external QQ operation."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from tianshu.channels.snowluma.streaming import collect, enqueue
from tianshu.channels.transports import OneBotTransport
from tianshu.runtime.files import FileStore
from tianshu.types import ArtifactRef


def frame(packet, **extra):
    return {"status": "ok", "retcode": 0, "stream": "stream-action", "data": packet, **extra}


async def packets(items):
    for item in items:
        yield item


def download_frames():
    return [
        frame({"type": "stream", "data_type": "file_info", "file_name": "a.txt", "file_size": 5}),
        frame(
            {"type": "stream", "data_type": "file_chunk", "index": 0, "data": "aGVsbG8=", "size": 5}
        ),
        frame(
            {"type": "response", "data_type": "file_complete", "total_bytes": 5, "total_chunks": 1}
        ),
    ]


def artifacts():
    async def put_async(chunks, **kwargs):
        return b"".join([chunk async for chunk in chunks])

    return SimpleNamespace(
        files=SimpleNamespace(put_async=AsyncMock(side_effect=put_async)),
        record=AsyncMock(side_effect=lambda c, m, **kw: {"bytes": len(m)}),
    )


@pytest.mark.asyncio
async def test_download_commits_only_complete_file_with_original_sources():
    store = artifacts()
    result = await collect(
        packets(download_frames()),
        "download_file_stream",
        artifacts=store,
        context="context",
        connection={"id": "snow"},
        source_refs=("source",),
    )
    assert result.state == "confirmed"
    assert result.data["artifact"] == {"bytes": 5}
    assert store.record.await_args.args == ("context", b"hello")
    assert store.record.await_args.kwargs["source_refs"] == ("source",)
    for invalid in (
        download_frames()[:-1],
        [
            *download_frames()[:2],
            frame(
                {
                    "type": "response",
                    "data_type": "file_complete",
                    "total_chunks": 1,
                    "total_bytes": 6,
                }
            ),
        ],
    ):
        store = artifacts()
        with pytest.raises(Exception) as error:
            await collect(packets(invalid), "download_file_stream", artifacts=store, context="ctx")
        assert error.value.code == "channel_stream_incomplete"
        store.record.assert_not_awaited()


@pytest.mark.asyncio
async def test_error_after_chunks_never_publishes_artifact():
    store = artifacts()
    result = await collect(
        packets(
            [
                *download_frames()[:2],
                {"retcode": 100, "status": "failed", "data": {"type": "error"}},
            ]
        ),
        "download_file_stream",
        artifacts=store,
        context="ctx",
    )
    assert result.state == "rejected"
    store.record.assert_not_awaited()


@pytest.mark.asyncio
async def test_upload_chunk_ack_is_distinct_from_whole_file_completion():
    result = await collect(
        packets(
            [
                frame(
                    {
                        "type": "stream",
                        "status": "chunk_received",
                        "received_chunks": 1,
                        "total_chunks": 2,
                    }
                )
            ]
        ),
        "upload_file_stream",
    )
    assert result.state == "confirmed"
    assert result.data["status"] == "chunk_received"
    assert "file_path" not in result.data


@pytest.mark.asyncio
async def test_http_transport_decodes_split_stream_frames_and_real_failure():
    async def handler(request):
        assert request.headers["Authorization"] == "Bearer test"
        response = web.StreamResponse(headers={"Content-Type": "application/json"})
        await response.prepare(request)
        documents = (
            [
                frame({"type": "stream", "data_type": "data_chunk", "data": "hello"}),
                frame({"type": "response", "data_type": "data_complete"}),
            ]
            if not (await request.json()).get("error")
            else [{"retcode": 100, "status": "failed", "data": {"type": "error"}}]
        )
        for document in documents:
            raw = (json.dumps(document) + "\r\n\r\n").encode()
            await response.write(raw[:9])
            await response.write(raw[9:])
        await response.write_eof()
        return response

    app = web.Application()
    app.router.add_post("/test_download_stream", handler)
    async with TestServer(app) as server:
        transport = OneBotTransport(
            {"provider": "snowluma", "transport": "http", "endpoint": str(server.make_url("/"))},
            "test",
            on_event=AsyncMock(),
            on_response=AsyncMock(),
        )
        await transport.start()
        try:
            submit = AsyncMock()
            result = await transport.call("call", "test_download_stream", {}, submit)
            assert result.state == "confirmed"
            assert len(result.data["frames"]) == 1
            assert (
                await transport.call("failed", "test_download_stream", {"error": True}, submit)
            ).state == "rejected"
            assert submit.await_count == 2
        finally:
            await transport.close()


@pytest.mark.asyncio
async def test_ws_frames_wait_for_terminal_and_late_frames_do_not_confirm_calls():
    transport = OneBotTransport(
        {"provider": "snowluma", "transport": "reverse_ws"},
        "test",
        on_event=AsyncMock(),
        on_response=AsyncMock(),
    )

    class Socket:
        closed = False

        def __init__(self):
            self.queue = asyncio.Queue()

        def __aiter__(self):
            return self

        async def __anext__(self):
            value = await self.queue.get()
            if value is None:
                raise StopAsyncIteration
            return SimpleNamespace(type=web.WSMsgType.TEXT, data=json.dumps(value))

        async def send_json(self, body):
            await self.queue.put(
                frame(
                    {"type": "stream", "data_type": "data_chunk", "data": "one"}, echo=body["echo"]
                )
            )
            await self.queue.put(
                frame({"type": "response", "data_type": "data_complete"}, echo=body["echo"])
            )

    socket = Socket()
    reader = asyncio.create_task(transport.attach(socket, "API"))
    await asyncio.sleep(0)
    try:
        result = await transport.call("call", "test_download_stream", {}, AsyncMock())
        assert result.state == "confirmed"
        await socket.queue.put(frame({"type": "response"}, echo="old-call"))
        await socket.queue.put(None)
        await reader
        transport.on_response.assert_not_awaited()
    finally:
        reader.cancel()
        await asyncio.gather(reader, return_exceptions=True)


@pytest.mark.asyncio
async def test_cancelled_stream_releases_backpressure():
    queue, closed = asyncio.Queue(maxsize=1), asyncio.Event()
    queue.put_nowait("existing")
    task = asyncio.create_task(enqueue(queue, closed, "pending"))
    await asyncio.sleep(0)
    closed.set()
    await asyncio.wait_for(task, 1)


@pytest.mark.asyncio
async def test_download_uses_real_file_store_and_complete_receipt(tmp_path):
    store = FileStore(tmp_path)
    recorded = []

    async def record(context, media, **kwargs):
        recorded.append(media)
        return ArtifactRef("download", media, metadata=kwargs["metadata"])

    result = await collect(
        packets(download_frames()),
        "download_file_stream",
        artifacts=SimpleNamespace(files=store, record=record),
        context="ctx",
    )
    assert result.state == "confirmed"
    assert store.read_bytes(recorded[0]) == b"hello"
    assert result.data["artifact"]["media"]["size_bytes"] == 5
    assert not list((tmp_path / ".incoming").iterdir())
