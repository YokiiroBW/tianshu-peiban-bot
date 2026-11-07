"""NetEase synchronization uses the media scheduler, lease and revision mechanisms."""

import asyncio
import uuid

from ...contracts import Fault, require
from ..scheduling import pump_scans, scan_due
from .library import MusicLibrary
from .netease import Netease
from .downloads import Downloads
from .providers import Bridge, PROVIDERS
from .imports import Imports
from .navidrome import Navidrome
from . import tools
from .request_control import RequestControl, Deferred
from .maintenance import Maintenance
from .lyrics import Lyrics


class Music:
    def __init__(self, media):
        self.media = media
        self.library = MusicLibrary(media.repo, media.accounts)
        self.requests = RequestControl(media)
        self.connector = Netease(control=self.requests)
        self.connectors = {name: Bridge(name, self.requests) for name in ("qq", "kugou")}
        for name, client in {"netease": self.connector, **self.connectors}.items():
            client.cache.shared = getattr(media, "shared_cache", None)
            client.cache.scope = name
        self.qr = {}
        self.downloads = Downloads(self)
        self.imports = Imports(self)
        self.navidrome = Navidrome(self)
        self.maintenance = Maintenance(self)
        self.lyrics = Lyrics(self)

    def client(self, provider):
        require(provider in PROVIDERS, "invalid_input", 400)
        return self.connector if provider == "netease" else self.connectors[provider]

    def save_settings(self, body, db):
        result = self.library.save_settings(body, db)
        self.imports.configure(result["settings"], db)
        return result

    def capabilities(self):
        return [
            {
                "provider": name,
                "label": label,
                "available": name == "netease" or bool(tools.executable("musicbridge")),
                "login_types": ["qq", "wx"] if name == "qq" else ["qr"],
            }
            for name, label in PROVIDERS.items()
        ]

    async def close(self):
        self.qr.clear()
        await self.connector.close()
        await self.navidrome.close()
        for client in self.connectors.values():
            await client.close()

    async def scan(self, identity):
        media, repo = self.media, self.media.repo
        subscription = await media.local(repo.get, "music_playlist", identity)
        require(subscription is not None, "not_found", 404)
        require(subscription["state"] == "active", "music_subscription_paused", 409)
        account, cookie = await media.local(self.library.account, subscription["account_id"])
        owner, key = str(uuid.uuid4()), "music:" + identity
        generation = await media.local(repo.scan_claim, key, owner)

        async def heartbeat():
            while True:
                await asyncio.sleep(5)
                await media.local(repo.scan_heartbeat, key, owner, generation)

        pulse = asyncio.create_task(heartbeat())
        try:
            async with asyncio.timeout(120):
                client = self.client(account["provider"])
                if hasattr(client, "snapshot_since"):
                    snapshot = await client.snapshot_since(
                        subscription["source_id"], account["user_id"], cookie, subscription
                    )
                else:
                    snapshot = await client.snapshot(
                        subscription["source_id"], account["user_id"], cookie
                    )
            return await media.local(
                self.library.complete_scan, account, subscription, snapshot, owner, generation
            )
        except (Fault, TimeoutError) as error:
            if isinstance(error, TimeoutError):
                error = Fault("music_upstream_unavailable", 502)
            current = await media.local(repo.get, "music_playlist", identity)
            if current and current["revision"] == subscription["revision"]:
                await media.local(
                    repo.put,
                    "music_playlist",
                    identity,
                    {
                        **current,
                        "code": error.code,
                        "next_scan_at": max(repo.clock() + 60, error.until)
                        if isinstance(error, Deferred)
                        else repo.clock() + max(300, current["interval_seconds"]),
                    },
                    current["revision"],
                )
            if error.code == "music_auth_required":
                try:
                    await media.local(self.library.account_state, account, "auth_required")
                except Fault:
                    pass
            raise error
        finally:
            pulse.cancel()
            await asyncio.gather(pulse, return_exceptions=True)
            await media.local(repo.scan_release, key, owner, generation)

    async def tick(self):
        await scan_due(self.media, "music_playlist", "playlist_id", self.scan)

    def start_background(self, background):
        background.start(
            "music_playlists", pump_scans, self.media, "music_playlist", "playlist_id", self.scan
        )
        background.start("music_imports", self.imports.pump)
        background.start("music_navidrome", self.navidrome.pump)
        background.start("music_maintenance", self.maintenance.pump)
        background.start("music_lyrics", self.lyrics.pump)
