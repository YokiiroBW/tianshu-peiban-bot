"""Linux Unix-socket host for the narrowly mounted comic file writer."""

import argparse
import asyncio
import json
from pathlib import Path

from aiohttp import web

from ...contracts import require
from .publisher import CHUNK_BYTES, PREFIX, PROTOCOL, PublisherStore
from .unix_service import boundary, serve


def application(store, client_uid):
    async def health(request):
        return web.json_response(
            {"protocol": PROTOCOL, "status": "alive", "targets": list(store.targets)}
        )

    async def transfer(request):
        target, key = request.match_info["target"], request.match_info["key"]
        if request.method == "GET":
            value = await asyncio.to_thread(store.get, target, key)
        elif request.match_info.get("offset") is not None:
            data = await request.read()
            value = await asyncio.to_thread(
                store.append,
                target,
                key,
                int(request.match_info["offset"]),
                data,
                request.headers.get("X-Chunk-Sha256", ""),
            )
        elif request.path.endswith("/complete"):
            value = await asyncio.to_thread(store.complete, target, key)
        else:
            require(
                request.content_length is not None and request.content_length <= 8192,
                "invalid_input",
                400,
            )
            value = await asyncio.to_thread(store.prepare, target, key, await request.json())
        return web.json_response(value)

    app = web.Application(middlewares=[boundary(client_uid)], client_max_size=CHUNK_BYTES + 1)
    app.router.add_get(PREFIX + "/health", health)
    path = PREFIX + "/{target}/{key}"
    app.router.add_get(path, transfer)
    app.router.add_post(path, transfer)
    app.router.add_put(path + "/chunks/{offset:\\d+}", transfer)
    app.router.add_post(path + "/complete", transfer)
    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--settings", required=True)
    args = parser.parse_args()
    settings = json.loads(Path(args.settings).read_bytes())
    require(set(settings) == {"targets", "socket", "client_uid"}, "invalid_input", 400)
    serve(
        application(PublisherStore(settings["targets"]), settings["client_uid"]), settings["socket"]
    )


if __name__ == "__main__":
    main()
