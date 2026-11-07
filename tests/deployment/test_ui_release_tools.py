"""Targeted deployment tests for UI release build, package, install, activate, rollback, and status tools."""

from __future__ import annotations

import io
import json
from pathlib import Path
import subprocess
import tarfile
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from deploy.core.ui_release import (
    UiReleaseVerificationError,
    _pack_release_directory,
    activate_ui_release,
    build_ui_package,
    install_ui_package,
    main as ui_release_main,
    preserve_legacy_root,
    rollback_ui_release,
    status_ui_release,
)
from tianshu.runtime.static import register_static
from tianshu.runtime.ui_release import (
    build_release_manifest,
    release_base_url,
    ui_release_lock,
)


def _make_packaged_release(
    tmp_path: Path,
    build_id: str,
    source_sha: str,
    *,
    marker: str = "v1",
    compatibility: dict | None = None,
) -> tuple[Path, Path]:
    rel_dir = tmp_path / f"pkg-src-{build_id}-{marker}"
    rel_dir.mkdir(parents=True, exist_ok=True)
    vite_base = release_base_url(build_id)
    (rel_dir / "index.html").write_text(
        f'<!doctype html><html><head><script type="module" src="{vite_base}assets/main-{marker}.js"></script></head>'
        f"<body>{build_id}:{marker}</body></html>",
        encoding="utf-8",
    )
    assets = rel_dir / "assets"
    assets.mkdir(exist_ok=True)
    (assets / f"main-{marker}.js").write_text(f'console.log("{marker}");\n', encoding="utf-8")
    manifest = build_release_manifest(
        rel_dir,
        build_id=build_id,
        source_sha=source_sha,
        compatibility=compatibility,
    )
    (rel_dir / "release.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    archive_path = tmp_path / f"ui-release-{build_id}-{marker}.tar.gz"
    _pack_release_directory(rel_dir, archive_path)
    return rel_dir, archive_path


def test_build_ui_package_exports_only_committed_source_and_excludes_dirty_worktree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    repo = tmp_path / "repo"
    web_dir = repo / "web"
    (web_dir / "src").mkdir(parents=True)
    (web_dir / "package.json").write_text('{"name":"tianshu-web"}\n', encoding="utf-8")
    (web_dir / "package-lock.json").write_text('{"lockfileVersion":3}\n', encoding="utf-8")
    (web_dir / "index.html").write_text("<!doctype html><html></html>\n", encoding="utf-8")
    (web_dir / "src" / "committed.ts").write_text("export const v = 1;\n", encoding="utf-8")

    sha = "1234567890abcdef1234567890abcdef12345678"
    build_id = "ui-20261108t010000z-1234567890ab"

    # Dirty uncommitted file in worktree that MUST NOT leak into exported source
    (web_dir / "src" / "uncommitted_dirty.ts").write_text("DIRTY", encoding="utf-8")

    def fake_run(command, *, cwd=None, env=None):
        cmd0 = Path(command[0]).name.lower()
        if "git" in cmd0 and command[1] == "archive":
            out_idx = command.index("--output") + 1
            tar_out = Path(command[out_idx])
            with tarfile.open(tar_out, "w") as tf:
                for rel in ("web/package.json", "web/package-lock.json", "web/index.html", "web/src/committed.ts"):
                    full = repo / rel
                    tf.add(full, arcname=rel)
            return
        if "npm" in cmd0 and command[1:] == ["run", "build"]:
            export_web = Path(cwd)
            assert not (export_web / "src" / "uncommitted_dirty.ts").exists()
            base = env["TIANSHU_UI_BASE"]
            bid = env["TIANSHU_UI_BUILD_ID"]
            assert bid == build_id and base == f"/_ui/releases/{build_id}/"
            dist = export_web / "dist"
            dist.mkdir(parents=True, exist_ok=True)
            (dist / "index.html").write_text(
                f'<!doctype html><script type="module" src="{base}assets/index-abc12345.js"></script>',
                encoding="utf-8",
            )
            (dist / "assets").mkdir(exist_ok=True)
            (dist / "assets" / "index-abc12345.js").write_text(
                f'window.__BUILD__="{bid}";\n', encoding="utf-8"
            )
            return
        if "npm" in cmd0 and command[1:] == ["ci"]:
            return
        raise AssertionError(f"Unexpected command: {command}")

    monkeypatch.setattr("deploy.core.ui_release.run", fake_run)

    owned_root = tmp_path / "owned"
    owned_root.mkdir()
    output = owned_root / "build-1"

    receipt = build_ui_package(
        repo,
        sha,
        owned_root,
        output,
        build_id=build_id,
        min_free_bytes=1024,
    )
    assert receipt["build_id"] == build_id
    assert receipt["source_sha"] == sha
    assert receipt["vite_base"] == f"/_ui/releases/{build_id}/"
    assert receipt["package_installed"] is False
    assert receipt["pointer_switched"] is False
    assert receipt["http_verified"] is False
    assert Path(receipt["archive_path"]).is_file()


def test_install_rejects_tampered_unsafe_symlink_and_conflict_without_touching_current(
    tmp_path: Path,
):
    ui_root = tmp_path / "data" / "ui"
    sha_a = "a" * 40
    build_a = "ui-20261108t010000z-aaaaaaaaaaaa"
    build_bad = "ui-20261108t020000z-bbbbbbbbbbbb"

    _, archive_a = _make_packaged_release(tmp_path, build_a, sha_a, marker="A")
    inst_a = install_ui_package(ui_root, archive_a, min_free_bytes=1024)
    assert inst_a["package_installed"] is True and inst_a["reused_existing"] is False

    # Idempotent re-install of identical package
    inst_a_again = install_ui_package(ui_root, archive_a, min_free_bytes=1024)
    assert inst_a_again["package_installed"] is True and inst_a_again["reused_existing"] is True

    act_a = activate_ui_release(ui_root, build_a)
    assert act_a["pointer_switched"] is True and act_a["current_build_id"] == build_a

    # Same build_id with different content must be rejected
    _, archive_a_conflict = _make_packaged_release(
        tmp_path, build_a, sha_a, marker="A-DIFFERENT"
    )
    with pytest.raises(ValueError, match="build_id_content_conflict"):
        install_ui_package(ui_root, archive_a_conflict, min_free_bytes=1024)

    # Archive with path traversal ("../escape.txt") must be rejected
    traversal_tar = tmp_path / "traversal.tar.gz"
    with tarfile.open(traversal_tar, "w:gz") as tf:
        info = tarfile.TarInfo(name="../escape.txt")
        data = b"evil"
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    with pytest.raises(ValueError, match="unsafe_ui_archive_member"):
        install_ui_package(ui_root, traversal_tar, min_free_bytes=1024)

    # Archive with symlink must be rejected
    symlink_tar = tmp_path / "symlink.tar.gz"
    with tarfile.open(symlink_tar, "w:gz") as tf:
        info = tarfile.TarInfo(name="assets/evil-link.js")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        tf.addfile(info)
    with pytest.raises(ValueError, match="unsafe_ui_archive_member"):
        install_ui_package(ui_root, symlink_tar, min_free_bytes=1024)

    # Tampered file inside archive must be rejected
    bad_dir, _ = _make_packaged_release(tmp_path, build_bad, "b" * 40, marker="B")
    (bad_dir / "assets" / "main-B.js").write_text("TAMPERED", encoding="utf-8")
    tampered_tar = tmp_path / "tampered.tar.gz"
    _pack_release_directory(bad_dir, tampered_tar)
    with pytest.raises(ValueError, match="digest_mismatch"):
        install_ui_package(ui_root, tampered_tar, min_free_bytes=1024)

    # Current active pointer MUST still be build_a
    st = status_ui_release(ui_root)
    assert st["current_build_id"] == build_a
    assert st["activation_sequence"] == 1


async def test_activate_rollback_http_verification_and_preserve_legacy(tmp_path: Path):
    builtin_dir = tmp_path / "builtin"
    builtin_dir.mkdir()
    (builtin_dir / "index.html").write_text("<!doctype html><body>builtin</body>", encoding="utf-8")
    (builtin_dir / "assets").mkdir()
    (builtin_dir / "assets" / "legacy-12345678.js").write_text("legacy", encoding="utf-8")

    ui_root = tmp_path / "data" / "ui"
    leg = preserve_legacy_root(ui_root, builtin_dir)
    assert leg["preserved"] is True and (ui_root / "legacy-root" / "assets" / "legacy-12345678.js").is_file()

    build_a = "ui-20261108t010000z-aaaaaaaaaaaa"
    build_b = "ui-20261108t020000z-bbbbbbbbbbbb"
    _, archive_a = _make_packaged_release(tmp_path, build_a, "a" * 40, marker="A")
    _, archive_b = _make_packaged_release(tmp_path, build_b, "b" * 40, marker="B")

    install_ui_package(ui_root, archive_a, min_free_bytes=1024)
    install_ui_package(ui_root, archive_b, min_free_bytes=1024)

    import asyncio

    app = web.Application()
    register_static(app, builtin_dir, ui_release_directory=ui_root)
    server = TestServer(app)
    await server.start_server()
    try:
        base_url = str(server.make_url("")).rstrip("/")

        # Activate A with real HTTP verification (run synchronous urllib client in worker thread)
        act_a = await asyncio.to_thread(
            activate_ui_release, ui_root, build_a, verify_url=base_url
        )
        assert act_a["package_installed"] is True
        assert act_a["pointer_switched"] is True
        assert act_a["http_verified"] is True

        # Activate B with expected_current CAS and real HTTP verification
        act_b = await asyncio.to_thread(
            activate_ui_release,
            ui_root,
            build_b,
            expected_current_build_id=build_a,
            verify_url=base_url,
        )
        assert act_b["current_build_id"] == build_b
        assert act_b["previous_build_id"] == build_a
        assert act_b["http_verified"] is True

        # Rollback B -> A with real HTTP verification
        rb_a = await asyncio.to_thread(
            rollback_ui_release,
            ui_root,
            expected_current_build_id=build_b,
            verify_url=base_url,
        )
        assert rb_a["current_build_id"] == build_a
        assert rb_a["rolled_back_to"] == build_a
        assert rb_a["http_verified"] is True

        # Both historical releases A and B remain installed on disk
        st = await asyncio.to_thread(
            status_ui_release, ui_root, builtin_root=builtin_dir, verify_url=base_url
        )
        assert st["http_verified"] is True
        assert st["release_count"] == 2
        assert {r["build_id"] for r in st["installed_releases"]} == {build_a, build_b}

        # Failed network verification must NOT claim http_verified=True and CLI returns non-zero
        with pytest.raises(UiReleaseVerificationError) as exc_info:
            await asyncio.to_thread(
                activate_ui_release,
                ui_root,
                build_b,
                expected_current_build_id=build_a,
                verify_url="http://127.0.0.1:1",
                timeout=0.2,
            )
        err_receipt = exc_info.value.receipt
        assert err_receipt["package_installed"] is True
        assert err_receipt["pointer_switched"] is True
        assert err_receipt["http_verified"] is False
        assert err_receipt["verification_error"]

        exit_code = await asyncio.to_thread(
            ui_release_main,
            ["status", "--ui-root", str(ui_root), "--verify-url", "http://127.0.0.1:1", "--timeout", "0.2"],
        )
        assert exit_code == 1
    finally:
        await server.close()
