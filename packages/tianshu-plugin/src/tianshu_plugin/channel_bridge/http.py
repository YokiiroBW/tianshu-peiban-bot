"""Optional aiohttp listener for SDK hosts whose normal API is Dashboard protected."""

import json
from aiohttp import web
from .host import PROTOCOL


async def handle(host, method, path, authorization, body=b""):
    if not host.authorized(authorization):
        return 401, {"protocol": PROTOCOL, "error_code": "unauthorized"}
    try:
        if path == "calls" and method == "POST":
            return 200, await host.call(json.loads(body))
        parts = path.split("/")
        if len(parts) == 2 and parts[0] == "calls" and method == "GET":
            return 200, await host.status(parts[1])
        if len(parts) == 3 and parts[0] == "calls" and method == "POST":
            if parts[2] == "ack":
                await host.acknowledge(parts[1])
                return 200, await host.status(parts[1])
            if parts[2] == "cancel":
                return 200, await host.cancel(parts[1])
        return 404, {"protocol": PROTOCOL, "error_code": "not_found"}
    except (ValueError, KeyError, TypeError):
        return 400, {"protocol": PROTOCOL, "error_code": "invalid_request"}


async def start_listener(host, listen_host, port):
    app = web.Application(client_max_size=128 * 1024 * 1024)

    async def route(request):
        status, body = await handle(
            host,
            request.method,
            request.match_info["path"],
            request.headers.get("Authorization"),
            await request.read(),
        )
        return web.json_response(body, status=status, headers={"Cache-Control": "no-store"})

    app.router.add_route("*", "/tianshu/channel-bridge/v2/{path:.*}", route)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, listen_host, port).start()
    return runner
