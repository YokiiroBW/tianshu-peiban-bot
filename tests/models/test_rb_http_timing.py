"""Use an isolated local provider to distinguish pool, headers and body timing."""

import asyncio

import aiohttp
from aiohttp import web

from tianshu.models.http_timing import HttpTiming, trace_config


async def test_pool_queue_slow_headers_slow_body_and_reused_connection():
    release_first = asyncio.Event()
    saw_first_body = asyncio.Event()
    started = 0

    async def upstream(_request):
        nonlocal started
        started += 1
        first = started == 1
        if not first:
            await asyncio.sleep(0.05)
        response = web.StreamResponse()
        await response.prepare(_request)
        if not first:
            await asyncio.sleep(0.05)
        await response.write(b"one")
        if first:
            await release_first.wait()
        await response.write_eof()
        return response

    app = web.Application()
    app.router.add_post("/fixture", upstream)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    first_timing, second_timing = HttpTiming(None), HttpTiming(None)

    async def call(session, timing, first):
        async with session.post(f"http://127.0.0.1:{port}/fixture", json={}, trace_request_ctx=timing) as response:
            timing.mark("response_headers")
            if await response.content.readany():
                timing.mark("first_body_byte")
                if first:
                    saw_first_body.set()
            await response.read()
            timing.mark("read_complete")

    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit=1), trace_configs=[trace_config()]) as session:
        first = asyncio.create_task(call(session, first_timing, True))
        second = None
        try:
            await asyncio.wait_for(saw_first_body.wait(), 2)
            second = asyncio.create_task(call(session, second_timing, False))

            async def pool_wait_started():
                while "pool_wait_start" not in second_timing.points:
                    await asyncio.sleep(0.001)

            await asyncio.wait_for(pool_wait_started(), 2)
            release_first.set()
            await asyncio.wait_for(asyncio.gather(first, second), 3)
            points = second_timing.record()["points_ms"]
            assert points["pool_wait_end"] >= points["pool_wait_start"]
            assert points["response_headers"] > points["pool_wait_end"]
            assert points["first_body_byte"] > points["response_headers"]
            assert points["first_valid_output"] is None
            assert second_timing.connection_reused is True
            assert points["dns_start"] is None and points["connect_start"] is None
        finally:
            release_first.set()
            for task in (first, second):
                if task is not None and not task.done():
                    task.cancel()
            await asyncio.gather(*(task for task in (first, second) if task is not None), return_exceptions=True)
    await runner.cleanup()
