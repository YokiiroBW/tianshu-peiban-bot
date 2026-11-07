"""Same-origin built web assets, with versioned immutable releases, range responses, and SPA navigation."""

from __future__ import annotations

from pathlib import Path
import re

from aiohttp import web

from .ui_release import (
    BACKEND_UI_CONTRACT,
    UiReleaseManager,
    _assert_no_symlinks_in_chain,
    validate_relative_asset_path,
)

RESERVED_PREFIXES = frozenset({"api", "health", "internal", "channels", "v1", "v1beta", "_ui"})


def _safe_legacy_file(base_dir: Path, rel_route: str) -> Path | None:
    try:
        clean_rel = validate_relative_asset_path(rel_route)
    except ValueError:
        return None
    if clean_rel == "index.html":
        return None
    if not base_dir.exists() or base_dir.is_symlink() or not base_dir.is_dir():
        return None
    root = base_dir.resolve()
    target = root / clean_rel
    try:
        _assert_no_symlinks_in_chain(root, target)
        resolved = target.resolve(strict=True)
        if not resolved.is_relative_to(root) or not resolved.is_file():
            return None
        return resolved
    except Exception:
        return None


def register_static(
    app,
    directory,
    *,
    ui_release_directory=None,
    backend_contract=BACKEND_UI_CONTRACT,
):
    root = Path(directory).resolve(strict=True)
    index = root / "index.html"
    if not index.is_file():
        raise ValueError("built_web_index_missing")
    ui_root = Path(ui_release_directory) if ui_release_directory is not None else None
    manager = UiReleaseManager(ui_root, root, backend_contract=backend_contract)
    app["ui_release_manager"] = manager

    async def serve(request: web.Request):
        route = request.match_info["path"]

        if route == "_ui/version":
            return web.json_response(
                manager.status(),
                headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
            )

        if route.startswith("_ui/releases/"):
            parts = route.split("/", 3)
            if len(parts) < 4 or not parts[2] or not parts[3]:
                raise web.HTTPNotFound()
            build_id, asset_path = parts[2], parts[3]
            target = manager.resolve_asset(build_id, asset_path)
            if target is None:
                raise web.HTTPNotFound()
            response = web.FileResponse(target)
            response.headers["Cache-Control"] = (
                "no-cache"
                if asset_path == "index.html"
                else "public,max-age=31536000,immutable"
            )
            response.headers["X-Tianshu-UI-Build-Id"] = build_id
            return response

        first_segment = route.split("/", 1)[0]
        if first_segment in RESERVED_PREFIXES:
            raise web.HTTPNotFound()

        active_snapshot, _ = manager.resolve_active()

        if route and route != "index.html":
            target: Path | None = None
            if active_snapshot.builtin and not active_snapshot.versioned_base:
                try:
                    clean_rel = validate_relative_asset_path(route)
                except ValueError:
                    clean_rel = ""
                if clean_rel and clean_rel in active_snapshot.files:
                    candidate = active_snapshot.root / clean_rel
                    try:
                        _assert_no_symlinks_in_chain(active_snapshot.root, candidate)
                        resolved = candidate.resolve(strict=True)
                        if resolved.is_relative_to(active_snapshot.root) and resolved.is_file():
                            target = resolved
                    except Exception:
                        target = None
            else:
                legacy_dir = (ui_root / "legacy-root") if ui_root is not None else None
                if legacy_dir is not None and legacy_dir.exists():
                    target = _safe_legacy_file(legacy_dir, route)
                elif not manager.builtin_snapshot.versioned_base:
                    target = _safe_legacy_file(manager.builtin_snapshot.root, route)

            if target is not None:
                response = web.FileResponse(target)
                response.headers["Cache-Control"] = (
                    "public,max-age=31536000,immutable"
                    if re.search(r"[.-][A-Za-z0-9_-]{8,16}\.", target.name)
                    else "no-cache"
                )
                return response

        if Path(route).suffix and route != "index.html":
            raise web.HTTPNotFound()
        accept = request.headers.get("Accept", "")
        if route != "" and "text/html" not in accept:
            raise web.HTTPNotFound()

        entry_file = active_snapshot.root / "index.html"
        if not entry_file.is_file() or entry_file.is_symlink():
            raise web.HTTPNotFound()
        return web.FileResponse(
            entry_file,
            headers={
                "Cache-Control": "no-cache",
                "X-Tianshu-UI-Build-Id": active_snapshot.build_id,
            },
        )

    app.router.add_get("/{path:.*}", serve)
