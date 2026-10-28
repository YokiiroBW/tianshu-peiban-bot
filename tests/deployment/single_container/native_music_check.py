#!/usr/bin/env python3
"""One isolated native music HTTP -> archive -> playlist-adapter acceptance flow.

Run the installed R11 wheel with the staged backend fixtures and frozen contracts.
Only a NEW --output directory is written; no production configuration or media is
read. NetEase protocol responses and Navidrome are local recorded HTTP fixtures.
NetEase's original encryption/parser/cache, Platform Cookie/CSRF/media commands,
the original background worker, transfer, ffmpeg/ffprobe, catalog, exports and
Navidrome reconciliation are real. CDN URLs have one exact fixture-only mapping.
This is not a browser-JavaScript test, real account test, or a Navidrome server test.
"""

import argparse
import asyncio
from collections import Counter
from contextlib import ExitStack
import hashlib
import importlib
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import sysconfig
import tarfile
import time
import traceback
import uuid
from unittest.mock import patch

sys.dont_write_bytecode = True
PLATFORM_COMMIT = "5b7ea997b3c0e2e7bd3f015c4856ed29b67eb6fa"
DECODER_SHA256 = "6d71732f14665207103aca5ff2334c7d869c0aea4b7fc05fc4b54e734825b749"
DECODER_SOURCE_SHA256 = (
    "ad41ded12eeb45ba047662d0ff8ce415950d4010e681fbfec7927abab21e46b1"
)
DECODER_SOURCE_PINS = {
    "tools/musicdecode/main.go": "43283f8999ccd9a9e71dcc5c86cafb1ac6d7568c42f3490902b486cdbe9879f8",
    "tools/musicdecode/go.mod": "063117552cbc0a96667b90031bb6e9caceb29c44f8cbb47d6c2d70254c236ee4",
    "tools/musicdecode/go.sum": "cfbce32c1fe409e084393c880b56fd634610578ba5d4055f5746d88f7ed1cf6a",
    "tools/musicdecode/third_party/unlock_cli/UPSTREAM.json": "5d1b67b9bac758743cd43482a741bb7171ea25015b49af57ffbe0abf318ae026",
    "scripts/build/music_tools.py": "b8cd4a73233e92e623126eaa8c4a1debfbf84d7d6c27cbe9f4d5f01620e8ff70",
}
CDN_AUDIO = "https://m10.music.126.net/phase2.flac"
CDN_COVER = "https://p1.music.126.net/phase2.png"


class CheckFailed(RuntimeError):
    pass


def check(value, code):
    if not value:
        raise CheckFailed(code)


def write_json(path, value):
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def sha(path):
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def command(args, *, input=None, timeout=45):
    result = subprocess.run(args, input=input, capture_output=True, timeout=timeout)
    check(result.returncode == 0, "fixture_tool_failed")
    return result.stdout


def decoder_provenance(options):
    if options.decoder is None:
        return {
            "status": "not_requested",
            "two_original_ncm_tests_run_separately": True,
        }
    check(options.decoder.is_file(), "pinned_decoder_missing")
    check(sha(options.decoder) == DECODER_SHA256, "pinned_decoder_hash_mismatch")
    check(options.decoder_source.is_file(), "decoder_corresponding_source_missing")
    check(
        sha(options.decoder_source) == DECODER_SOURCE_SHA256,
        "decoder_source_hash_mismatch",
    )
    with tarfile.open(options.decoder_source, "r:gz") as archive:
        names = set(archive.getnames())
        check(
            set(DECODER_SOURCE_PINS) <= names, "decoder_corresponding_source_incomplete"
        )
        for name, digest in DECODER_SOURCE_PINS.items():
            raw = archive.extractfile(name).read().replace(b"\r\n", b"\n")
            check(
                hashlib.sha256(raw).hexdigest() == digest,
                "decoder_source_differs_from_final_platform",
            )
        upstream = json.load(
            archive.extractfile(
                "tools/musicdecode/third_party/unlock_cli/UPSTREAM.json"
            )
        )
        check(
            upstream["module"] == "unlock-music.dev/cli"
            and upstream["version"] == "v0.2.12",
            "decoder_upstream_lock_mismatch",
        )
        module = archive.extractfile("tools/musicdecode/go.mod").read().decode()
        build = archive.extractfile("scripts/build/music_tools.py").read().decode()
        check(
            "go 1.27.2" in module
            and "unlock-music.dev/cli v0.2.12" in module
            and "replace unlock-music.dev/cli => ./third_party/unlock_cli" in module
            and 'VERSION = "go1.27.2"' in build
            and "ecbadb99091a3f46e31f5f934b068b1864eafa7995211b39eaddf76996045fe5"
            in build,
            "decoder_build_lock_mismatch",
        )
    version = json.loads(
        command([str(options.decoder)], input=b'{"operation":"version"}')
    )
    check(
        version.get("ok") is True
        and version["value"]["protocol"] == 1
        and version["value"]["unlock_music_version"] == "v0.2.12",
        "decoder_version_mismatch",
    )
    return {
        "status": "pinned_existing_image_tool_verified",
        "binary_sha256": DECODER_SHA256,
        "source_sha256": DECODER_SOURCE_SHA256,
        "unlock_music_version": "v0.2.12",
        "go_version": "1.27.2",
        "source_entries": len(names),
        "final_platform_source_locks_match": True,
        "downloaded_binary": False,
        "two_original_ncm_tests_run_separately": True,
    }


class RecordedNetease:
    """Recorded response shapes; original Netease client still performs every call."""

    cookie = "phase2-synthetic-music-cookie"

    def __init__(self, audio, cover):
        self.audio, self.cover = audio, cover
        self.entries = ["101", "101", "103"]
        self.updated = 1
        self.calls = Counter()
        self.full_downloads = 0
        self.asset_credentials_seen = False
        self.runner = None

    @staticmethod
    def song():
        return {
            "id": 101,
            "name": "Phase2 Synthetic Song",
            "ar": [{"id": 1, "name": "Phase2 Synthetic Artist"}],
            "al": {"id": 11, "name": "Phase2 Synthetic Album", "picUrl": CDN_COVER},
            "dt": 3000,
            "cd": "01",
            "no": 1,
        }

    def playlist(self):
        return {
            "id": 900,
            "name": "Phase2 Synthetic Playlist",
            "description": "Synthetic fixture; no commercial music or account",
            "creator": {"userId": 123},
            "trackCount": len(self.entries),
            "updateTime": self.updated,
            "trackIds": [{"id": int(identity)} for identity in self.entries],
        }

    async def start(self):
        from aiohttp import web

        app = web.Application()
        app.router.add_post("/{kind:eapi|weapi}/{tail:.*}", self.handle)
        app.router.add_get("/audio.flac", self.asset)
        app.router.add_get("/cover.png", self.asset)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        self.origin = "http://127.0.0.1:" + str(
            site._server.sockets[0].getsockname()[1]
        )
        return self

    async def handle(self, request):
        from aiohttp import web

        path = request.match_info["tail"]
        self.calls[path] += 1
        form = await request.post()
        check(bool(form.get("params")), "native_netease_envelope_missing")
        value = {"code": 200}
        if path == "login/qrcode/unikey":
            value["unikey"] = "phase2-synthetic-key"
        elif path == "login/qrcode/client/login":
            value["code"] = 803
        elif path == "w/nuser/account/get":
            value["profile"] = {"userId": 123, "nickname": "Phase2 Synthetic Account"}
        elif path == "user/playlist":
            value.update(playlist=[self.playlist()], more=False)
        elif path == "v6/playlist/detail":
            value["playlist"] = self.playlist()
        elif path == "v3/song/detail":
            value["songs"] = [self.song()]
        elif path == "cloudsearch/pc":
            value["result"] = {"songs": [self.song()]}
        elif path == "song/enhance/download/url/v1":
            value["data"] = {
                "id": 101,
                "level": "lossless",
                "size": len(self.audio),
                "md5": hashlib.md5(self.audio).hexdigest(),
                "url": CDN_AUDIO,
            }
        elif path == "song/lyric":
            value.update(
                lrc={"lyric": "[00:00.00]合成歌词"},
                tlyric={"lyric": ""},
                romalrc={"lyric": ""},
            )
        elif path == "v1/album/11":
            value["album"] = {
                "id": 11,
                "artists": [{"name": "Phase2 Synthetic Album Artist"}],
                "publishTime": 1767312000000,
            }
        else:
            raise CheckFailed("unexpected_recorded_netease_endpoint")
        response = web.json_response(value)
        if path == "login/qrcode/client/login":
            response.set_cookie("MUSIC_U", self.cookie)
        return response

    async def asset(self, request):
        from aiohttp import web

        self.asset_credentials_seen |= any(
            name in request.headers for name in ("Cookie", "Authorization")
        )
        raw = self.cover if request.path == "/cover.png" else self.audio
        if request.path == "/audio.flac" and "Range" not in request.headers:
            self.full_downloads += 1
        if "Range" in request.headers:
            return web.Response(
                body=raw,
                status=206,
                headers={"Content-Range": f"bytes 0-{len(raw) - 1}/{len(raw)}"},
            )
        return web.Response(body=raw)

    def fixture_cdn(self, value):
        mapping = {
            CDN_AUDIO: self.origin + "/audio.flac",
            CDN_COVER: self.origin + "/cover.png",
        }
        if value in mapping:
            return mapping[value]
        check(value in mapping.values(), "fixture_cdn_not_in_exact_allowlist")
        return value


class Browser:
    def __init__(self, client, origin):
        self.client, self.origin, self.csrf = client, origin, None
        self.calls = Counter()

    async def post(self, operation, body, expected=200, *, wrong_csrf=False):
        path = "/api/web/media/" + operation
        async with self.client.post(
            self.origin + path,
            json=body,
            headers={
                "Origin": self.origin,
                "X-CSRF-Token": "phase2-invalid" if wrong_csrf else self.csrf,
            },
        ) as response:
            value = await response.json()
            check(
                response.status == expected,
                "http_" + operation.replace("/", "_") + "_" + str(response.status),
            )
            self.calls[operation] += 1
            return value

    async def wait_job(self, *, reused=None):
        page = await self.post(
            "jobs/list", {"states": None, "cursor": None, "page_size": 10}
        )
        identities = [
            row["job_id"] for row in page["jobs"] if row.get("media_kind") == "audio"
        ]
        check(identities, "native_queued_audio_missing")
        deadline = time.monotonic() + 100
        while time.monotonic() < deadline:
            jobs = [
                (await self.post("jobs/detail", {"job_id": identity}))["job"]
                for identity in identities
            ]
            if jobs and all(row["state"] == "completed" for row in jobs):
                if reused is None or all(
                    row.get("audio_reused") is reused for row in jobs
                ):
                    return jobs
            check(
                not any(
                    row["state"] in {"failed", "auth_required", "unknown", "cancelled"}
                    for row in jobs
                ),
                "native_music_job_failed",
            )
            await asyncio.sleep(1)
        raise CheckFailed("native_music_job_timeout")


async def run(options):
    import aiohttp
    from PIL import Image
    from mutagen.flac import FLAC
    from fixtures import ENV, start_http
    from media_runtime_fixtures import settings
    from test_music_navidrome import SyntheticNavidrome
    from web_fixtures import PASSWORD
    from services.platform.server import create_app
    from services.platform.service import Platform
    from services.platform.media.music.netease import Netease
    from services.platform.media.music.transfer import Transfer
    from services.platform.media.music.audio import probe

    root = options.output.resolve()
    check(not root.exists(), "output_must_be_a_new_isolated_directory")
    root.mkdir(parents=True, mode=0o700)
    report = {
        "status": "running",
        "platform_source_commit": PLATFORM_COMMIT,
        "scope": {
            "synthetic_only": True,
            "production_configuration_or_media_read": False,
            "real_qq_or_model_requests": False,
            "browser_javascript_exercised": False,
            "unified_business_host_exercised": False,
            "external_netease": "local recorded HTTP response shapes; original adapter and protocol",
            "cdn": "exact two fixture URLs mapped to loopback HTTP",
            "external_navidrome": "local recorded Subsonic HTTP server; original adapter/reconciliation",
            "music_memory_callback": "not an existing music runtime path; no memory write invented",
        },
    }
    runner, platform, upstream, nav = None, None, None, None
    phase = "tool_provenance"
    started = time.monotonic()
    try:
        modules = {}
        for name in (
            "services.platform.service",
            "services.platform.media.music.runtime",
            "services.platform.media.music.netease",
            "services.platform.media.music.downloads",
            "services.platform.media.music.catalog",
            "services.platform.media.music.navidrome.sync",
        ):
            path = Path(importlib.import_module(name).__file__).resolve()
            if options.source_root is None:
                check(
                    path.is_relative_to(Path(sysconfig.get_path("purelib")).resolve()),
                    "installed_wheel_required",
                )
            modules[name] = sha(path)
        report["installed_wheel"] = options.source_root is None
        report["module_sha256"] = modules
        report["decoder_provenance"] = decoder_provenance(options)
        report["ffmpeg"] = (
            command([str(options.ffmpeg), "-version"]).decode().splitlines()[0]
        )
        report["ffprobe"] = (
            command([str(options.ffprobe), "-version"]).decode().splitlines()[0]
        )
        original = root / "generated-sine.flac"
        command(
            [
                str(options.ffmpeg),
                "-nostdin",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:duration=3",
                "-c:a",
                "flac",
                str(original),
            ]
        )
        audio_sha = sha(original)
        picture = io.BytesIO()
        Image.new("RGB", (32, 32), "blue").save(picture, "PNG")
        upstream = await RecordedNetease(
            original.read_bytes(), picture.getvalue()
        ).start()
        nav = SyntheticNavidrome()
        await nav.start()
        phase = "native_platform_start"
        with ExitStack() as stack:
            stack.enter_context(
                patch.dict(
                    os.environ, {**ENV, "SYNTHETIC_MEDIA_PUBLISH": "phase2-unused"}
                )
            )
            config = settings(
                str(root),
                {
                    "python": sys.executable,
                    "ffmpeg": str(options.ffmpeg),
                    "ffprobe": str(options.ffprobe),
                },
                upstream.origin,
            )
            config["media"]["targets"] = []
            platform = Platform(config)
            media, music = platform.media, platform.media.music
            await music.connector.close()
            music.connector = Netease(
                web_origin=upstream.origin,
                api_origin=upstream.origin,
                control=music.requests,
            )
            music.connector.cache.shared, music.connector.cache.scope = (
                media.shared_cache,
                "netease",
            )
            music.downloads.transfer = Transfer(
                validate_url=upstream.fixture_cdn, control=music.requests
            )
            check(
                Path(media.repo.database).resolve().is_relative_to(root),
                "ledger_outside_fixture",
            )
            runner, origin = await start_http(create_app(platform))
            config["web"]["origin"] = origin
            async with aiohttp.ClientSession(
                cookie_jar=aiohttp.CookieJar(unsafe=True), trust_env=False
            ) as client:
                browser = Browser(client, origin)
                async with client.get(origin + "/api/web/session") as response:
                    session = await response.json()
                browser.csrf = session["csrf"]
                await browser.post("music/view", {}, 401)
                async with client.post(
                    origin + "/api/web/login",
                    json={"username": "synthetic-admin", "password": PASSWORD},
                    headers={"Origin": origin, "X-CSRF-Token": browser.csrf},
                ) as response:
                    check(response.status == 200, "synthetic_http_login_failed")
                    browser.csrf = (await response.json())["csrf"]
                await browser.post("music/view", {}, 403, wrong_csrf=True)
                phase = "account_playlist_and_search"
                qr = await browser.post(
                    "music/accounts/qr/start", {"provider": "netease"}
                )
                account = (
                    await browser.post("music/accounts/qr/poll", {"qr_id": qr["qr_id"]})
                )["account"]
                check(account is not None, "recorded_account_not_connected")
                listed = await browser.post(
                    "music/playlists/list", {"account_id": account["account_id"]}
                )
                check(len(listed["playlists"]) == 1, "native_playlist_list_missing")
                _, cookie = await media.local(
                    music.library.account, account["account_id"]
                )
                found = await music.connector.search("Phase2 Synthetic Song", cookie)
                check(
                    len(found) == 1 and found[0]["source_id"] == "101",
                    "native_search_parser_failed",
                )
                directory = root / "library"
                directory.mkdir()
                await browser.post(
                    "music/settings/save",
                    {
                        "directory": str(directory),
                        "layout": "artist_album",
                        "expected_revision": 0,
                        "client_id": str(uuid.uuid4()),
                    },
                )
                playlist = (
                    await browser.post(
                        "music/playlists/subscribe",
                        {
                            "account_id": account["account_id"],
                            "source_id": "900",
                            "interval_seconds": 3600,
                            "expected_revision": 0,
                            "client_id": str(uuid.uuid4()),
                        },
                    )
                )["playlist"]
                phase = "native_worker_audio_archive"
                queued = await browser.post(
                    "music/playlists/downloads",
                    {
                        "playlist_id": playlist["playlist_id"],
                        "quality": {"level": "lossless", "allow_fallback": False},
                        "auto_download": True,
                        "expected_revision": playlist["revision"],
                        "client_id": str(uuid.uuid4()),
                    },
                )
                check(
                    queued["queued"] == 1 and queued["unavailable"] == 1,
                    "ordered_members_not_deduplicated_for_queue",
                )
                jobs = await browser.wait_job()
                check(
                    len(jobs) == 1 and jobs[0]["audio_unlock"]["state"] == "not_needed",
                    "native_audio_completion_missing",
                )
                job = jobs[0]
                receipt = job["audio_receipt"]
                item = next(row for row in receipt["files"] if row["kind"] == "audio")
                archived = (directory / item["path"]).resolve()
                check(
                    archived.is_relative_to(directory)
                    and sha(archived) == item["sha256"],
                    "archive_file_receipt_mismatch",
                )
                tags = FLAC(archived)
                check(bool(tags.pictures), "native_cover_missing")
                check(
                    tags["title"] == ["Phase2 Synthetic Song"]
                    and tags["artist"] == ["Phase2 Synthetic Artist"],
                    "portable_audio_tags_missing",
                )
                observed = await probe(archived, None, media.config["engine"])
                artifact = await media.local(
                    music.library.catalog.file, job["recording_id"]
                )
                check(
                    observed["content_id"] == artifact["content_id"],
                    "native_pcm_identity_mismatch",
                )
                catalog = await browser.post("music/library/view", {"offset": 0})
                check(
                    catalog["counts"]["music_library_file"] == 1,
                    "archive_not_in_native_catalog",
                )
                detail = await browser.post(
                    "music/playlists/detail",
                    {"playlist_id": playlist["playlist_id"], "offset": 0},
                )
                check(
                    len(detail["tracks"]) == 3
                    and detail["tracks"][0]["file"] == detail["tracks"][1]["file"]
                    and detail["tracks"][2]["file"] is None,
                    "playlist_order_or_unavailable_entry_lost",
                )
                exported = await browser.post(
                    "music/playlists/export", {"playlist_id": playlist["playlist_id"]}
                )
                export_dir = Path(exported["directory"])
                check(
                    export_dir.resolve().is_relative_to(directory),
                    "export_outside_fixture",
                )
                export = json.loads((export_dir / "playlist.json").read_bytes())
                check(
                    [row["source_id"] for row in export["tracks"]]
                    == ["101", "101", "103"]
                    and exported["exported"] == 2,
                    "native_export_order_failed",
                )
                check(
                    (export_dir / "playlist.m3u8")
                    .read_text(encoding="utf-8")
                    .count(".flac")
                    == 2,
                    "native_m3u_duplicate_positions_lost",
                )
                transfers = upstream.full_downloads
                check(
                    transfers == 1 and not upstream.asset_credentials_seen,
                    "credential_free_single_transfer_failed",
                )
                phase = "native_repeat_reuses_archive"
                latest = (await browser.post("music/view", {}))["playlists"][0]
                await browser.post(
                    "music/playlists/download",
                    {
                        "playlist_id": playlist["playlist_id"],
                        "expected_revision": latest["revision"],
                        "client_id": str(uuid.uuid4()),
                    },
                )
                reused = await browser.wait_job(reused=True)
                check(
                    upstream.full_downloads == transfers and len(reused) == 1,
                    "repeat_download_transferred_again",
                )
                phase = "navidrome_adapter_and_reconciliation"
                nav.songs = [
                    {
                        "id": "phase2-indexed-audio",
                        "path": "Virtual Artist/Virtual Album/Track.flac",
                        "title": tags["title"][0],
                        "album": tags["album"][0],
                        "artists": [{"name": name} for name in tags["artist"]],
                        "duration": int(tags.info.length),
                        "size": archived.stat().st_size,
                        "suffix": "flac",
                    }
                ]
                connection = {
                    "base_url": nav.url,
                    "username": "synthetic",
                    "password": nav.password,
                }
                await browser.post("music/navidrome/test", connection)
                await browser.post(
                    "music/navidrome/save",
                    {
                        **connection,
                        "music_folder_id": "1",
                        "music_directory": "/music/phase2",
                        "expected_revision": 0,
                        "client_id": str(uuid.uuid4()),
                    },
                )
                await browser.post(
                    "music/navidrome/playlists/save",
                    {
                        "playlist_id": playlist["playlist_id"],
                        "name": "Phase2 API Playlist",
                        "public": False,
                        "auto_sync": True,
                        "expected_revision": 0,
                        "client_id": str(uuid.uuid4()),
                    },
                )

                async def wait_nav(status):
                    deadline = time.monotonic() + 35
                    while time.monotonic() < deadline:
                        await music.navidrome.tick()
                        view = (await browser.post("music/view", {}))["navidrome"]
                        rows = view["playlists"]
                        if rows and rows[0]["status"] == status:
                            return rows[0]
                        await asyncio.sleep(0.3)
                    raise CheckFailed("native_navidrome_" + status + "_timeout")

                partial = await wait_nav("partial")
                check(
                    partial["synced_count"] == 2 and partial["problem_count"] == 1,
                    "unavailable_member_not_reported_partial",
                )
                upstream.entries, upstream.updated = ["101", "101"], 2
                await browser.post(
                    "music/playlists/scan", {"playlist_id": playlist["playlist_id"]}
                )
                final = await wait_nav("synced")
                remote = next(iter(nav.playlists.values()))
                check(
                    final["synced_count"] == 2
                    and final["problem_count"] == 0
                    and [row["id"] for row in remote["entry"]]
                    == ["phase2-indexed-audio"] * 2,
                    "native_navidrome_ordered_readback_failed",
                )
                check(
                    len(nav.playlists) == 1
                    and nav.creates == 1
                    and remote["public"] is False,
                    "native_navidrome_duplicate_or_visibility_failed",
                )
                check(
                    (export_dir / ".ndignore").is_file()
                    and b"playlist.m3u8" in (export_dir / ".ndignore").read_bytes(),
                    "native_export_ignore_missing",
                )
                check(
                    sha(original) == audio_sha
                    and sha(archived) == item["sha256"]
                    and upstream.full_downloads == 1,
                    "source_or_archive_changed_during_reconcile",
                )
                check(
                    upstream.cookie.encode()
                    not in Path(media.repo.database).read_bytes(),
                    "synthetic_cookie_stored_in_plaintext",
                )
                check(
                    nav.password.encode() not in Path(media.repo.database).read_bytes(),
                    "synthetic_navidrome_password_stored_in_plaintext",
                )
                report.update(
                    status="passed",
                    native_http_cookie_csrf=True,
                    native_background_worker=True,
                    native_netease_protocol_and_search=True,
                    source_audio_sha256=audio_sha,
                    archived_audio_sha256=item["sha256"],
                    content_id_verified=True,
                    portable_tags_and_cover_verified=bool(tags.pictures),
                    single_archive=True,
                    ordered_duplicate_positions=True,
                    missing_member_preserved=True,
                    repeat_reused_without_full_transfer=True,
                    navidrome_partial_then_synced=True,
                    navidrome_ordered_remote_readback=True,
                    nfo_status="music uses native JSON/M3U8 exports and Navidrome adapter; no music NFO invented",
                    counts={
                        "full_audio_transfers": upstream.full_downloads,
                        "recorded_netease_calls": dict(sorted(upstream.calls.items())),
                        "web_operations": dict(sorted(browser.calls.items())),
                        "navidrome_operations": dict(
                            Counter(row[0] for row in nav.calls)
                        ),
                        "remote_playlists": len(nav.playlists),
                        "final_members": final["synced_count"],
                    },
                )
            await runner.cleanup()
            runner = None
            platform.close()
            platform = None
        phase = "sqlite_integrity_after_close"
        databases = []
        for database in root.rglob("*.sqlite"):
            with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as db:
                check(
                    db.execute("PRAGMA integrity_check").fetchone()[0] == "ok",
                    "fixture_database_integrity_failed",
                )
            databases.append(database.relative_to(root).as_posix())
        report["closed_sqlite_databases_verified"] = databases
    except BaseException as error:
        report.update(
            status="failed",
            failed_stage=phase,
            error_code=str(error)
            if isinstance(error, CheckFailed)
            else "native_music_fixture_failed",
        )
        (root / "failure.private.log").write_text(
            traceback.format_exc(), encoding="utf-8"
        )
    finally:
        if runner is not None:
            await runner.cleanup()
        if platform is not None:
            platform.close()
        if upstream is not None and upstream.runner is not None:
            await upstream.runner.cleanup()
        if nav is not None:
            await nav.close()
        report["seconds"] = round(time.monotonic() - started, 3)
        write_json(root / "result.json", report)
        print(
            json.dumps(
                {
                    "status": report["status"],
                    "failed_stage": report.get("failed_stage"),
                    "error_code": report.get("error_code"),
                    "evidence": str(root / "result.json"),
                }
            ),
            flush=True,
        )
    return 0 if report["status"] == "passed" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform-fixtures", type=Path, required=True)
    parser.add_argument("--contracts", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ffmpeg", type=Path, default=Path("/usr/bin/ffmpeg"))
    parser.add_argument("--ffprobe", type=Path, default=Path("/usr/bin/ffprobe"))
    parser.add_argument("--decoder", type=Path)
    parser.add_argument(
        "--decoder-source",
        type=Path,
        default=Path("/usr/share/tianshu-music-tools/corresponding-source.tar.gz"),
    )
    parser.add_argument(
        "--source-root",
        type=Path,
        help="Local source-only dry validation; final Linux evidence omits this",
    )
    options = parser.parse_args()
    sys.path.insert(0, str(options.platform_fixtures.resolve()))
    if options.source_root is not None:
        sys.path.insert(0, str(options.source_root.resolve()))
    os.environ.update(
        TS012_CONTRACT_DIR=str(options.contracts.resolve()),
        TIANSHU_WORKSPACE=str(options.contracts.resolve().parents[2]),
        PYTHONDONTWRITEBYTECODE="1",
    )
    return asyncio.run(run(options))


if __name__ == "__main__":
    raise SystemExit(main())
