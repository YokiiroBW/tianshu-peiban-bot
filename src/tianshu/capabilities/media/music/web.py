"""Music commands inherit the existing console session, CSRF and media receipts."""

import asyncio
import uuid

from ...contracts import Fault, require
from .providers import identity as source_id, PROVIDERS
from .navidrome.web import route as navidrome_route


class WebMusic:
    def __init__(self, web):
        self.web, self.media = web, web.media

    async def route(self, operation, body, session):
        await self.web.live(session)
        if self.media.config is None:
            require(operation == "view", "media_not_configured", 503)
            return {"configured": False, "accounts": [], "playlists": []}
        music = self.media.music
        library, connector, local = music.library, music.connector, self.media.local
        if operation.startswith("navidrome/"):
            return await navidrome_route(self.web, operation[len("navidrome/") :], body, session)
        if operation == "view":
            require(body == {}, "invalid_input", 400)
            return {
                **await local(library.view),
                "providers": music.capabilities(),
                "routing": "automatic",
                "imports": await local(music.imports.view),
                "navidrome": await local(music.navidrome.state.view),
                "maintenance": await local(music.maintenance.view),
                "lyrics": await local(music.lyrics.view),
            }
        if operation == "maintenance/save":
            self.fields(
                body, {"enabled", "playlist_interval_seconds", "expected_revision", "client_request_id"}
            )
            return await self.web.receipt(
                body, "music/" + operation, session, lambda db: music.maintenance.save(body, db)
            )
        if operation == "library/scan":
            self.fields(body, set())
            result = await local(music.imports.enqueue)
            self.media.wake.set()
            return result
        if operation == "library/view":
            self.fields(body, {"offset"})
            return await local(library.catalog.view, body["offset"])
        if operation == "library/link":
            self.fields(body, {"source_key", "recording_id", "expected_revision", "client_request_id"})
            return await self.web.receipt(
                body, "music/" + operation, session, lambda db: library.catalog.link(body, db)
            )
        if operation == "settings/save":
            self.fields(body, {"directory", "layout", "expected_revision", "client_request_id"})
            return await self.web.receipt(
                body, "music/" + operation, session, lambda db: library.save_settings(body, db)
            )
        if operation == "settings/inspect":
            self.fields(body, {"directory"})
            return await local(library.inspect_directory, body["directory"])
        if operation == "accounts/qr/start":
            require(set(body) <= {"provider", "login_type"}, "invalid_input", 400)
            provider = body.get("provider", "netease")
            require(provider in PROVIDERS, "invalid_input", 400)
            login_type = body.get("login_type", "qq" if provider == "qq" else "qr")
            require(
                login_type in ({"qq", "wx"} if provider == "qq" else {"qr"}), "invalid_input", 400
            )
            connector = music.client(provider)
            now = self.media.repo.clock()
            music.qr = {k: v for k, v in music.qr.items() if v["expires_at"] > now}
            require(len(music.qr) < 16, "qr_capacity", 429)
            data = (
                await connector.qr_start()
                if provider == "netease"
                else await connector.qr_start(login_type)
            )
            await self.web.live(session)
            identity = str(uuid.uuid4())
            music.qr[identity] = {
                **data,
                "expires_at": now + 180,
                "session": session,
                "lock": asyncio.Lock(),
                "last_poll": 0,
                "state": "waiting",
                "account": None,
                "provider": provider,
            }
            return {
                "qr_id": identity,
                "url": data["url"],
                "image": data.get("image"),
                "expires_at": now + 180,
            }
        if operation == "accounts/qr/poll":
            self.fields(body, {"qr_id"})
            qr = music.qr.get(body["qr_id"])
            if qr is None or qr["expires_at"] <= self.media.repo.clock():
                return {"state": "expired", "account": None}
            require(qr["session"] is session, "forbidden", 403)
            connector = music.client(qr["provider"])
            async with qr["lock"]:
                if qr["state"] == "ready" or self.media.repo.clock() - qr["last_poll"] < 3:
                    return {"state": qr["state"], "account": qr["account"]}
                qr["last_poll"] = self.media.repo.clock()
                state, cookie = await connector.qr_poll(qr["key"], qr["cookie"])
                qr["cookie"] = cookie
                if state == "ready":
                    profile = await connector.account(cookie)
                    await self.web.live(session)
                    require(qr["expires_at"] > self.media.repo.clock(), "qr_expired", 409)
                    qr["account"] = await local(
                        library.save_account, profile, cookie, qr["provider"]
                    )
                    qr["cookie"] = {}
                qr["state"] = state
                return {"state": state, "account": qr["account"]}
        if operation == "accounts/revoke":
            self.fields(body, {"account_id", "expected_revision", "client_request_id"})
            return await self.web.receipt(
                body, "music/" + operation, session, lambda db: library.revoke(body, db)
            )
        if operation == "playlists/list":
            self.fields(body, {"account_id"})
            account, cookie = await local(library.account, body["account_id"])
            connector = music.client(account["provider"])
            try:
                result = (
                    await connector.playlists(account["user_id"], cookie, fresh=True)
                    if hasattr(connector, "cache")
                    else await connector.playlists(account["user_id"], cookie)
                )
            except TimeoutError:
                raise Fault("music_upstream_unavailable", 502) from None
            except Fault as error:
                if error.code == "music_auth_required":
                    await local(library.account_state, account, "auth_required")
                raise
            await self.web.live(session)
            current, _ = await local(library.account, account["account_id"])
            require(current["revision"] == account["revision"], "account_changed", 409)
            return {"playlists": result}
        if operation == "playlists/subscribe":
            self.fields(
                body,
                {"account_id", "source_id", "interval_seconds", "expected_revision", "client_request_id"},
            )
            require(
                type(body["interval_seconds"]) is int and 60 <= body["interval_seconds"] <= 604800,
                "invalid_input",
                400,
            )
            source_id(body["source_id"])
            account, cookie = await local(library.account, body["account_id"])
            connector = music.client(account["provider"])
            try:
                async with asyncio.timeout(120):
                    snapshot = await connector.snapshot(
                        body["source_id"], account["user_id"], cookie
                    )
            except TimeoutError:
                raise Fault("music_upstream_unavailable", 502) from None
            return await self.web.receipt(
                body,
                "music/" + operation,
                session,
                lambda db: library.subscribe(
                    account, snapshot, body["interval_seconds"], body["expected_revision"], db
                ),
            )
        if operation == "playlists/control":
            self.fields(body, {"playlist_id", "action", "expected_revision", "client_request_id"})
            return await self.web.receipt(
                body, "music/" + operation, session, lambda db: library.control(body, db)
            )
        if operation in {"playlists/download", "playlists/downloads"}:
            fields = {"playlist_id", "expected_revision", "client_request_id"}
            if operation == "playlists/downloads":
                fields |= {"auto_download", "quality"}
            self.fields(body, fields)
            method = library.downloads if operation == "playlists/downloads" else library.download
            result = await self.web.receipt(
                body, "music/" + operation, session, lambda db: method(body, db)
            )
            self.media.wake.set()
            return result
        if operation == "playlists/export":
            self.fields(body, {"playlist_id"})
            return await music.downloads.exclusive(
                music.downloads.storage.export, body["playlist_id"]
            )
        if operation == "playlists/scan":
            self.fields(body, {"playlist_id"})
            return await music.scan(body["playlist_id"])
        if operation == "playlists/detail":
            self.fields(body, {"playlist_id", "offset"})
            return await local(library.detail, body["playlist_id"], body["offset"])
        raise Fault("not_found", 404)

    @staticmethod
    def fields(body, fields):
        require(set(body) == fields, "invalid_input", 400)
        if "expected_revision" in body:
            require(
                type(body["expected_revision"]) is int and body["expected_revision"] >= 0,
                "invalid_input",
                400,
            )
        for key in ("account_id", "playlist_id", "qr_id"):
            if key in body:
                require(
                    isinstance(body[key], str) and 0 < len(body[key]) <= 256, "invalid_input", 400
                )
