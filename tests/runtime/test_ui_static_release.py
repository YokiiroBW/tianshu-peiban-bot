"""Targeted runtime tests for versioned UI static service and release contract validation."""

from __future__ import annotations

import json
import os
from pathlib import Path
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from tianshu.runtime.static import register_static
from tianshu.runtime.ui_release import (
    BACKEND_UI_CONTRACT,
    UI_CURRENT_SCHEMA_VERSION,
    UI_PACKAGE_SCHEMA_VERSION,
    UI_STATUS_SCHEMA_VERSION,
    build_release_manifest,
    install_release_directory,
    release_base_url,
    switch_current_release,
    ui_release_lock,
    validate_compatibility,
    verify_release_directory,
)


def _create_release_source(
    base_dir: Path,
    build_id: str,
    source_sha: str,
    *,
    marker: str = "v1",
    compatibility: dict | None = None,
) -> Path:
    src = base_dir / f"src-{build_id}"
    src.mkdir(parents=True, exist_ok=True)
    vite_base = release_base_url(build_id)
    (src / "index.html").write_text(
        f'<!doctype html><html><head><script type="module" src="{vite_base}assets/app-{marker}.js"></script></head>'
        f'<body data-build="{build_id}">{marker}</body></html>',
        encoding="utf-8",
    )
    assets = src / "assets"
    assets.mkdir(exist_ok=True)
    (assets / f"app-{marker}.js").write_text(
        f'console.log("{build_id}", "{marker}");\n', encoding="utf-8"
    )
    (assets / f"lazy-{marker}.js").write_text(
        f'export const page = "{build_id}:{marker}";\n', encoding="utf-8"
    )
    room = src / "room-illustration-assets"
    room.mkdir(exist_ok=True)
    (room / "room-clean-afternoon-v1.png").write_bytes(b"PNGDATA-" + build_id.encode("utf-8"))
    manifest = build_release_manifest(
        src,
        build_id=build_id,
        source_sha=source_sha,
        compatibility=compatibility,
    )
    (src / "release.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return src


def _create_builtin_dist(base_dir: Path) -> Path:
    dist = base_dir / "builtin-dist"
    dist.mkdir(parents=True, exist_ok=True)
    (dist / "index.html").write_text(
        '<!doctype html><html><head><script type="module" src="/assets/index-builtin1234.js"></script></head>'
        "<body>builtin-baseline</body></html>",
        encoding="utf-8",
    )
    (dist / "assets").mkdir(exist_ok=True)
    (dist / "assets" / "index-builtin1234.js").write_text(
        'console.log("builtin-legacy-asset");\n', encoding="utf-8"
    )
    return dist


async def test_builtin_baseline_and_live_switch_a_to_b_to_a_with_old_page_isolation(tmp_path: Path):
    builtin_dir = _create_builtin_dist(tmp_path)
    ui_root = tmp_path / "data" / "ui"
    sha_a = "a" * 40
    sha_b = "b" * 40
    build_a = "ui-20261108t010000z-aaaaaaaaaaaa"
    build_b = "ui-20261108t020000z-bbbbbbbbbbbb"

    src_a = _create_release_source(tmp_path, build_a, sha_a, marker="A")
    src_b = _create_release_source(tmp_path, build_b, sha_b, marker="B")

    app = web.Application()
    register_static(app, builtin_dir, ui_release_directory=ui_root)

    async with TestClient(TestServer(app)) as client:
        # 1. No external release yet -> serves builtin baseline
        resp_ver = await client.get("/_ui/version")
        assert resp_ver.status == 200
        assert "no-store" in resp_ver.headers.get("Cache-Control", "")
        status_0 = await resp_ver.json()
        assert status_0["schema_version"] == UI_STATUS_SCHEMA_VERSION
        assert status_0["served_mode"] == "builtin"
        assert status_0["served_build_id"].startswith("builtin-")
        assert status_0["requested_build_id"] is None
        assert status_0["compatible"] is True
        assert status_0["degraded"] is False
        assert "ui_root" not in json.dumps(status_0)

        resp_root = await client.get("/", headers={"Accept": "text/html"})
        assert resp_root.status == 200
        assert "builtin-baseline" in await resp_root.text()
        assert resp_root.headers.get("Cache-Control") == "no-cache"

        # 2. Install & activate Release A while server is running
        inst_a = install_release_directory(ui_root, src_a, min_free_bytes=1024)
        assert inst_a["package_installed"] is True and inst_a["pointer_switched"] is False
        sw_a = switch_current_release(ui_root, build_a)
        assert sw_a["pointer_switched"] is True and sw_a["activation_sequence"] == 1

        resp_a = await client.get("/#/workbench", headers={"Accept": "text/html"})
        assert resp_a.status == 200
        html_a = await resp_a.text()
        assert f"/_ui/releases/{build_a}/assets/app-A.js" in html_a
        assert resp_a.headers.get("X-Tianshu-UI-Build-Id") == build_a

        # 3. Install & switch to Release B without restarting aiohttp server
        install_release_directory(ui_root, src_b, min_free_bytes=1024)
        sw_b = switch_current_release(ui_root, build_b, expected_current_build_id=build_a)
        assert sw_b["activation_sequence"] == 2
        assert sw_b["previous_build_id"] == build_a

        status_b = await (await client.get("/_ui/version")).json()
        assert status_b["served_build_id"] == build_b
        assert status_b["requested_build_id"] == build_b
        assert status_b["served_mode"] == "external"
        assert status_b["activation_sequence"] == 2

        # New / refreshed page gets B
        resp_b = await client.get("/", headers={"Accept": "text/html"})
        html_b = await resp_b.text()
        assert f"/_ui/releases/{build_b}/assets/app-B.js" in html_b
        assert f"/_ui/releases/{build_a}/" not in html_b

        # Already-open Page A requests lazy chunk and room illustration image after switch to B
        lazy_a = await client.get(f"/_ui/releases/{build_a}/assets/lazy-A.js")
        assert lazy_a.status == 200
        assert (await lazy_a.text()).strip() == f'export const page = "{build_a}:A";'
        assert lazy_a.headers.get("Cache-Control") == "public,max-age=31536000,immutable"

        img_a = await client.get(
            f"/_ui/releases/{build_a}/room-illustration-assets/room-clean-afternoon-v1.png"
        )
        assert img_a.status == 200
        assert await img_a.read() == b"PNGDATA-" + build_a.encode("utf-8")

        # 4. Rollback B -> A via same switch function
        sw_rb = switch_current_release(ui_root, build_a, expected_current_build_id=build_b)
        assert sw_rb["current_build_id"] == build_a
        assert sw_rb["activation_sequence"] == 3

        resp_after_rb = await client.get("/", headers={"Accept": "text/html"})
        assert f"/_ui/releases/{build_a}/assets/app-A.js" in await resp_after_rb.text()


async def test_head_range_404_reserved_paths_and_security_boundaries(tmp_path: Path):
    builtin_dir = _create_builtin_dist(tmp_path)
    ui_root = tmp_path / "data" / "ui"
    build_a = "ui-20261108t010000z-aaaaaaaaaaaa"
    src_a = _create_release_source(tmp_path, build_a, "a" * 40, marker="A")
    install_release_directory(ui_root, src_a, min_free_bytes=1024)
    switch_current_release(ui_root, build_a)

    # Create legacy-root snapshot for first-access compatibility check
    legacy_root = ui_root / "legacy-root" / "assets"
    legacy_root.mkdir(parents=True, exist_ok=True)
    (legacy_root / "old-v0-12345678.js").write_text("legacy-v0-content", encoding="utf-8")

    app = web.Application()
    register_static(app, builtin_dir, ui_release_directory=ui_root)

    async with TestClient(TestServer(app)) as client:
        # HEAD and Range on versioned asset
        asset_url = f"/_ui/releases/{build_a}/assets/app-A.js"
        head_resp = await client.head(asset_url)
        assert head_resp.status == 200
        assert head_resp.headers.get("Cache-Control") == "public,max-age=31536000,immutable"

        range_resp = await client.get(asset_url, headers={"Range": "bytes=0-6"})
        assert range_resp.status == 206
        assert await range_resp.read() == b"console"

        # HEAD on /_ui/version
        head_ver = await client.head("/_ui/version")
        assert head_ver.status == 200
        assert "no-store" in head_ver.headers.get("Cache-Control", "")

        # Missing versioned asset returns real 404, never HTML
        missing_asset = await client.get(
            f"/_ui/releases/{build_a}/assets/nonexistent.js",
            headers={"Accept": "text/html,*/*"},
        )
        assert missing_asset.status == 404

        # Internal files (release.json, current.json, .staging, .lock) are blocked with 404
        for forbidden in (
            f"/_ui/releases/{build_a}/release.json",
            "/_ui/current.json",
            "/_ui/.staging/test",
            "/_ui/.lock",
            "/release.json",
            "/current.json",
        ):
            res = await client.get(forbidden, headers={"Accept": "text/html,*/*"})
            assert res.status == 404, forbidden

        # Reserved backend prefixes never fallback to SPA index.html
        for reserved in (
            "/api/v1/unknown",
            "/health/unknown",
            "/internal/debug",
            "/channels/hook",
            "/v1/models",
            "/v1beta/models",
        ):
            res = await client.get(reserved, headers={"Accept": "text/html"})
            assert res.status == 404, reserved

        # Legacy root serves old unprefixed asset but NEVER maps new release's app-A.js to /assets/app-A.js
        legacy_res = await client.get("/assets/old-v0-12345678.js")
        assert legacy_res.status == 200
        assert await legacy_res.text() == "legacy-v0-content"

        unmapped_new = await client.get("/assets/app-A.js")
        assert unmapped_new.status == 404


async def test_degraded_recovery_keeps_last_known_good_or_builtin_and_reports_reason(tmp_path: Path):
    builtin_dir = _create_builtin_dist(tmp_path)
    ui_root = tmp_path / "data" / "ui"
    build_a = "ui-20261108t010000z-aaaaaaaaaaaa"
    build_b = "ui-20261108t020000z-bbbbbbbbbbbb"
    src_a = _create_release_source(tmp_path, build_a, "a" * 40, marker="A")
    src_b = _create_release_source(tmp_path, build_b, "b" * 40, marker="B")

    install_release_directory(ui_root, src_a, min_free_bytes=1024)
    install_release_directory(ui_root, src_b, min_free_bytes=1024)
    switch_current_release(ui_root, build_a)

    app = web.Application()
    register_static(app, builtin_dir, ui_release_directory=ui_root)

    async with TestClient(TestServer(app)) as client:
        # Establish A as last_known_good
        s1 = await (await client.get("/_ui/version")).json()
        assert s1["served_build_id"] == build_a and s1["degraded"] is False

        # Corrupt B on disk (tamper with file content so digest mismatches) and point current.json to B
        switch_current_release(ui_root, build_b, expected_current_build_id=build_a)
        (ui_root / "releases" / build_b / "assets" / "app-B.js").write_text(
            "TAMPERED_CONTENT", encoding="utf-8"
        )
        # Touch current.json so manager re-evaluates
        current_file = ui_root / "current.json"
        current_file.write_text(current_file.read_text(encoding="utf-8") + "\n", encoding="utf-8")

        s2 = await (await client.get("/_ui/version")).json()
        assert s2["requested_build_id"] == build_b
        assert s2["served_build_id"] == build_a
        assert s2["served_mode"] == "fallback_last_known_good"
        assert s2["degraded"] is True
        assert s2["degraded_reason"] == "digest_mismatch"

        # Root HTML still safely serves A!
        root_html = await (await client.get("/", headers={"Accept": "text/html"})).text()
        assert f"/_ui/releases/{build_a}/assets/app-A.js" in root_html


def test_compatibility_contract_and_conflict_guards(tmp_path: Path):
    ui_root = tmp_path / "ui"
    same_sha = "c" * 40
    build_id = "ui-20261108t030000z-cccccccccccc"

    # Self-proclaimed compatible without required contract fields is rejected even with matching SHA
    with pytest.raises(ValueError, match="compatibility_mismatch"):
        validate_compatibility(
            {
                "schema_version": UI_PACKAGE_SCHEMA_VERSION,
                "source_sha": same_sha,
                "compatible": True,
                "compatibility": {"compatible": True},
            }
        )

    # Incompatible contract version is rejected
    with pytest.raises(ValueError, match="compatibility_mismatch"):
        _create_release_source(
            tmp_path,
            build_id,
            same_sha,
            compatibility={
                "contract_id": "tianshu.web_api.v1",
                "api_prefix": "/api/v1",
                "required_capabilities": ["session.v1"],
                "min_backend_contract_version": 2,
                "max_backend_contract_version": 2,
            },
        )

    # Valid install, idempotent re-install, and conflicting content rejection
    src_1 = _create_release_source(tmp_path / "v1", build_id, same_sha, marker="v1")
    res_1 = install_release_directory(ui_root, src_1, min_free_bytes=1024)
    assert res_1["package_installed"] is True and res_1["reused_existing"] is False

    res_dup = install_release_directory(ui_root, src_1, min_free_bytes=1024)
    assert res_dup["package_installed"] is True and res_dup["reused_existing"] is True

    src_conflict = _create_release_source(tmp_path / "v2", build_id, same_sha, marker="v2-different")
    with pytest.raises(ValueError, match="build_id_content_conflict"):
        install_release_directory(ui_root, src_conflict, min_free_bytes=1024)

    # CAS mismatch on switch
    switch_current_release(ui_root, build_id, expected_current_build_id=None, require_expected=True)
    with pytest.raises(ValueError, match="expected_current_build_id_mismatch"):
        switch_current_release(ui_root, build_id, expected_current_build_id="ui-wrong-id")

    # Concurrent lock contention guard
    with ui_release_lock(ui_root):
        with pytest.raises(RuntimeError, match="ui_release_lock_busy"):
            with ui_release_lock(ui_root, blocking=False):
                pass
