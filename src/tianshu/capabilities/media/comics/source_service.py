"""Read-only source access for NAS trees owned by a different filesystem UID."""

import argparse
import asyncio
import json
import os
import stat
from pathlib import Path

from aiohttp import web

from ...contracts import require
from .imports import DirectoryInputs
from .packages import IMAGE_EXTENSIONS, input_path
from .source_client import PREFIX, PROTOCOL
from .unix_service import boundary, serve


def application(directory, client_uid):
    reader = DirectoryInputs(directory)

    async def operation(request):
        body = await request.json()
        require(
            isinstance(body, dict) and body.get("root") == str(reader.root()),
            "comic_input_changed",
            409,
        )
        name = request.match_info["operation"]
        if name == "probe":

            def check_directory():
                with os.scandir(input_path(reader.root(), "")) as entries:
                    next(entries, None)

            await asyncio.to_thread(check_directory)
            value = {"status": "alive", "protocol": PROTOCOL}
        elif name == "browse":
            value = await asyncio.to_thread(
                reader.browse, body.get("relative_path", ""), body.get("offset", 0)
            )
        elif name == "inspect":
            value = await asyncio.to_thread(reader.inspect, body.get("relative_path", ""))
        elif name == "recheck":
            chapter = body.get("chapter")
            require(
                isinstance(chapter, dict)
                and set(chapter) == {"relative_path", "input_kind", "input_digest"},
                "invalid_input",
                400,
            )
            await asyncio.to_thread(reader.recheck, chapter)
            value = {"verified": True}
        elif name == "file":
            path = await asyncio.to_thread(input_path, reader.root(), body.get("relative_path", ""))
            require(
                path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS | {".zip", ".cbz"},
                "comic_input_unsupported",
                409,
            )
            expected = body.get("file")
            require(
                isinstance(expected, dict) and set(expected) == {"name", "size", "mtime_ns"},
                "invalid_input",
                400,
            )
            fd = await asyncio.to_thread(os.open, path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(fd, "rb") as stream:
                before = os.fstat(stream.fileno())
                require(
                    stat.S_ISREG(before.st_mode)
                    and expected
                    == {"name": path.name, "size": before.st_size, "mtime_ns": before.st_mtime_ns},
                    "comic_input_changed",
                    409,
                )
                response = web.StreamResponse(
                    headers={
                        "Content-Length": str(before.st_size),
                        "Content-Type": "application/octet-stream",
                        "X-Comic-Protocol": PROTOCOL,
                    }
                )
                await response.prepare(request)
                while chunk := await asyncio.to_thread(stream.read, 65536):
                    await response.write(chunk)
                after = os.fstat(stream.fileno())
                require(
                    (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns),
                    "comic_input_changed",
                    409,
                )
                await response.write_eof()
                return response
        else:
            raise web.HTTPNotFound()
        return web.json_response(value, headers={"X-Comic-Protocol": PROTOCOL})

    app = web.Application(middlewares=[boundary(client_uid)], client_max_size=8192)
    app.router.add_post(PREFIX + "/{operation}", operation)
    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--settings", required=True)
    args = parser.parse_args()
    config = json.loads(Path(args.settings).read_bytes())
    require(
        set(config) == {"directory", "socket", "client_uid"}
        and Path(config["directory"]).is_absolute(),
        "invalid_input",
        400,
    )
    serve(application(config["directory"], config["client_uid"]), config["socket"])


if __name__ == "__main__":
    main()
