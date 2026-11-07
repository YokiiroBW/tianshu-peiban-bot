
"""Same-origin built web assets, with streaming file/range responses and SPA navigation."""

import re
from pathlib import Path
from aiohttp import web
from tianshu.runtime.ui_release import BACKEND_UI_CONTRACT, UiReleaseManager

def register_static(app, directory, *, ui_release_directory=None, backend_contract=BACKEND_UI_CONTRACT):
    ui_root = Path(ui_release_directory) if ui_release_directory else None
    manager = UiReleaseManager(ui_root, Path(directory), backend_contract=backend_contract) if ui_root else None
    
    root = Path(directory).resolve(strict=True)
    index = root / "index.html"
    if not index.is_file():
        raise ValueError("built_web_index_missing")

    async def handle_version(request):
        if not manager:
            raise web.HTTPNotFound()
        return web.json_response(manager.status(), headers={"Cache-Control": "no-cache, no-store, must-revalidate"})

    async def serve(request):
        route = request.match_info["path"]
        
        # 1. Version API
        if route == "_ui/version":
            return await handle_version(request)
            
        # 2. Versioned releases
        if route.startswith("_ui/releases/"):
            if not manager:
                raise web.HTTPNotFound()
            parts = route.split("/", 3)
            if len(parts) < 4:
                raise web.HTTPNotFound()
            build_id, asset_path = parts[2], parts[3]
            
            target = manager.resolve_asset(build_id, asset_path)
            if not target or not target.is_file():
                raise web.HTTPNotFound()
                
            response = web.FileResponse(target)
            response.headers["Cache-Control"] = "no-cache" if asset_path == "index.html" else "public,max-age=31536000,immutable"
            return response
            
        # 3. Reserved paths
        if route.split("/", 1)[0] in {"api", "health", "internal", "channels", "v1", "v1beta", "_ui"}:
            raise web.HTTPNotFound()
            
        # 4. Legacy root or Builtin fallback for static files
        legacy_root = (ui_root / "legacy-root") if ui_root and (ui_root / "legacy-root").exists() else root
        target = (legacy_root / route).resolve()
        if target.is_relative_to(legacy_root) and target.is_file() and route != "" and route != "index.html":
            response = web.FileResponse(target)
            response.headers["Cache-Control"] = (
                "public,max-age=31536000,immutable"
                if re.search(r"[.-][A-Za-z0-9_-]{8,16}\.", target.name)
                else "no-cache"
            )
            return response
            
        # 5. Root / SPA fallback
        if Path(route).suffix and route != "index.html" or ("text/html" not in request.headers.get("Accept", "") and route != ""):
            raise web.HTTPNotFound()
            
        active_snapshot = manager._get_current() if manager else None
        active_index = (active_snapshot.root / "index.html") if active_snapshot else index
        
        headers = {"Cache-Control": "no-cache"}
        if active_snapshot:
            headers["X-Tianshu-UI-Build-Id"] = active_snapshot.build_id
            
        return web.FileResponse(active_index, headers=headers)

    app.router.add_get("/{path:.*}", serve)
    app.router.add_head("/_ui/version", handle_version)
