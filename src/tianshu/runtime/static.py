"""Same-origin built web assets, with streaming file/range responses and SPA navigation."""

from pathlib import Path
import re

from aiohttp import web


def register_static(app, directory):
    root = Path(directory).resolve(strict=True)
    index = root / "index.html"
    if not index.is_file():
        raise ValueError("built_web_index_missing")

    async def serve(request):
        route = request.match_info["path"]
        if route.split("/", 1)[0] in {"api", "health", "internal", "channels", "v1", "v1beta"}:
            raise web.HTTPNotFound()
        target = (root / route).resolve()
        if not target.is_relative_to(root):
            raise web.HTTPNotFound()
        if target.is_file():
            response = web.FileResponse(target)
            response.headers["Cache-Control"] = (
                "public,max-age=31536000,immutable"
                if re.search(r"[.-][A-Za-z0-9_-]{8,16}\.", target.name)
                else "no-cache"
            )
            return response
        if Path(route).suffix or "text/html" not in request.headers.get("Accept", ""):
            raise web.HTTPNotFound()
        return web.FileResponse(index, headers={"Cache-Control": "no-cache"})

    app.router.add_get("/{path:.*}", serve)
