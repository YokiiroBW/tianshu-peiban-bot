"""Ordered playlist reconciliation with recoverable creation and shared scan leases."""

import asyncio
import json
import uuid

from ....contracts import Fault, digest, require
from .client import Client, object_field
from .matching import mapped_members, retain_verified
from .exports import prepare_exports
from .state import CONNECTION, LINK, State


class Navidrome:
    def __init__(self, music):
        self.music, self.media, self.repo = music, music.media, music.media.repo
        self.state = State(music)
        self.client = Client()

    async def close(self):
        await self.client.close()

    def check_current(self, connection, link, owner, generation):
        with self.repo.transaction() as db:
            self.repo.require_scan(
                db, "navidrome:" + connection["connection_id"], owner, generation
            )
            current = self.repo._document(db, CONNECTION, "server")
            row = self.repo._document(db, LINK, link["link_id"])
            settings = self.repo._document(db, "music_settings", "library") or {}
            require(
                current and current["enabled"] and current["revision"] == connection["revision"],
                "scope_changed",
                409,
            )
            require(row and row["revision"] == link["revision"], "version_conflict", 409)
            require(
                settings.get("directory") == connection["local_directory"],
                "navidrome_mapping_changed",
                409,
            )

    def remember_remote(self, link, identity):
        # Creation may have completed just as the user paused/edited the link.
        # Keep its ID before any rename, without overwriting the newer command.
        with self.repo.transaction() as db:
            current = self.repo._document(db, LINK, link["link_id"])
            require(current and current["marker"] == link["marker"], "scope_changed", 409)
            require(
                not current.get("remote_id") or current["remote_id"] == identity,
                "navidrome_playlist_conflict",
                409,
            )
            current["remote_id"] = identity
            # Observations do not advance the user's configuration revision.
            db.execute(
                'UPDATE documents SET body=%s WHERE kind=%s AND id=%s',
                (json.dumps(current, ensure_ascii=False), LINK, link["link_id"]),
            )

    async def target(self, connection, link, guard, patch):
        if link.get("remote_id"):
            return await self.client.playlist(connection, link["remote_id"])
        marker = link["marker"]
        rows = await self.client.playlists(connection)
        matches = [
            r
            for r in rows
            if r.get("owner") == connection["username"]
            and (r.get("name") == marker or str(r.get("comment") or "").startswith(marker + "\n"))
        ]
        require(len(matches) <= 1, "navidrome_playlist_conflict", 409)
        if matches:
            target = await self.client.playlist(connection, matches[0]["id"])
        else:
            # Never blindly resend a creation with an unknown outcome. The unique
            # temporary name lets a later observation recover the original ID.
            require(not link.get("create_sent"), "navidrome_creation_unverified", 409)
            await guard()
            await patch(create_sent=True)
            try:
                target = (
                    await self.client.request(connection, "createPlaylist", {"name": marker})
                ).get("playlist")
            except Fault as error:
                if error.code in {"navidrome_auth_required", "navidrome_forbidden"}:
                    await patch(create_sent=False)
                raise
            require(
                isinstance(target, dict) and isinstance(target.get("id"), str),
                "navidrome_invalid_response",
                502,
            )
        await self.media.local(self.remember_remote, link, target["id"])
        link["remote_id"] = target["id"]
        return await self.client.playlist(connection, target["id"])

    async def sync(self, connection, link, owner, generation, catalog):
        local = self.media.local

        async def guard():
            await local(self.check_current, connection, link, owner, generation)

        async def patch(**changes):
            result = await local(self.repo.fact, LINK, link["link_id"], changes, link["revision"])
            link.update(result)

        await guard()
        if not catalog.get("exports_prepared"):
            try:
                await self.music.downloads.exclusive(prepare_exports, self.repo, connection)
            except OSError:
                raise Fault("navidrome_export_unavailable", 409) from None
            catalog["exports_prepared"] = True
        deleting = link["status"] == "deleting"
        if deleting and not link.get("remote_id") and not link.get("create_sent"):
            await patch(status="deleted", code="deleted", checked_at=self.repo.clock())
            return
        await patch(status="deleting" if deleting else "syncing", code="syncing")
        target = await self.target(connection, link, guard, patch)
        require(target.get("owner") == connection["username"], "navidrome_playlist_conflict", 409)
        if deleting:
            await guard()
            await self.client.request(connection, "deletePlaylist", {"id": target["id"]})
            try:
                await self.client.playlist(connection, target["id"])
            except Fault as error:
                if error.code != "navidrome_not_found":
                    raise
            else:
                raise Fault("navidrome_sync_unverified", 409)
            await patch(status="deleted", code="deleted", checked_at=self.repo.clock())
            return
        require(not target.get("readonly"), "navidrome_readonly_playlist", 409)
        playlist, tracks = await local(self.music.library.export_snapshot, link["playlist_id"])
        source_digest = digest(
            {
                "name": playlist["name"],
                "description": playlist["description"],
                "tracks": [{k: v for k, v in t.items() if k != "job"} for t in tracks],
            }
        )
        if not catalog.get("scan_checked") and connection.get("can_scan"):
            # Ask the existing Navidrome scanner to see newly archived files; this
            # does not transfer audio. Non-admin users rely on its regular scan.
            status = await self.client.request(connection, "getScanStatus")
            unindexed = any(m["code"] == "waiting_index" for m in link.get("members", []))
            needs_scan = link.get("scan_requested_digest") != source_digest or (
                unindexed and self.repo.clock() - link.get("scan_requested_at", 0) >= 300
            )
            if needs_scan and not object_field(status, "scanStatus").get("scanning"):
                await guard()
                await patch(
                    scan_requested_digest=source_digest, scan_requested_at=self.repo.clock()
                )
                await self.client.request(connection, "startScan")
            for _ in range(5):
                status = await self.client.request(connection, "getScanStatus")
                if not object_field(status, "scanStatus").get("scanning"):
                    break
                await asyncio.sleep(1)
            else:
                raise Fault("navidrome_scanning", 409)
            catalog["scan_checked"] = True
            catalog["songs"] = None
        if catalog.get("songs") is None:
            catalog["songs"] = await self.client.songs(connection)
        members = await local(mapped_members, tracks, catalog["songs"], connection)
        unresolved = {
            identity
            for member in members
            if member["code"] in {"ambiguous", "waiting_index"}
            for identity in member.get("candidate_ids", [])
        }
        pending_paths = set()
        if unresolved:
            resolved = await self.client.paths.resolve(connection, unresolved)
            paths, pending_paths = resolved["paths"], resolved["pending"]
            songs = [
                {**song, "real_path": paths[song["id"]]["path"]}
                if song["id"] in paths and paths[song["id"]]["size"] == song.get("size")
                else song
                for song in catalog["songs"]
            ]
            members = await local(mapped_members, tracks, songs, connection)
        members = retain_verified(members, link.get("members", []), target, pending_paths)
        for member in members:
            member.pop("candidate_ids", None)
        wanted = [m["song_id"] for m in members if m.get("song_id")]
        name = link["name"] or playlist["name"]
        comment = link["marker"] + "\n" + playlist["description"][:2000]
        await guard()
        if [r["id"] for r in target.get("entry", [])] != wanted:
            if wanted:
                await self.client.request(
                    connection,
                    "createPlaylist",
                    [
                        ("playlistId", target["id"]),
                        *(("songId", identity) for identity in wanted),
                    ],
                )
            else:
                # Navidrome 0.64.2 keeps old members for createPlaylist without
                # songId. Removing the observed positions correctly empties it.
                await self.client.request(
                    connection,
                    "updatePlaylist",
                    [
                        ("playlistId", target["id"]),
                        *(("songIndexToRemove", i) for i in range(len(target.get("entry", [])))),
                    ],
                )
        if (
            target.get("name") != name
            or target.get("comment", "") != comment
            or bool(target.get("public")) != link["public"]
        ):
            await guard()
            await self.client.request(
                connection,
                "updatePlaylist",
                {
                    "playlistId": target["id"],
                    "name": name,
                    "comment": comment,
                    "public": str(link["public"]).lower(),
                },
            )
        observed = await self.client.playlist(connection, target["id"])
        require(
            [r["id"] for r in observed.get("entry", [])] == wanted
            and observed.get("name") == name
            and observed.get("comment", "") == comment
            and bool(observed.get("public")) == link["public"],
            "navidrome_sync_unverified",
            409,
        )
        await guard()
        # A source snapshot or shared file can change while the remote request runs.
        latest, latest_tracks = await local(self.music.library.export_snapshot, link["playlist_id"])
        latest_digest = digest(
            {
                "name": latest["name"],
                "description": latest["description"],
                "tracks": [{k: v for k, v in t.items() if k != "job"} for t in latest_tracks],
            }
        )
        changed = latest_digest != source_digest
        missing = sum(not m.get("song_id") or m["code"] == "verification_pending" for m in members)
        status = "queued" if changed else "partial" if missing else "synced"
        await patch(
            status=status,
            code=status,
            source_digest=source_digest,
            name_observed=name,
            members=members,
            synced_count=len(wanted),
            total=len(members),
            problem_count=missing,
            checked_at=self.repo.clock(),
            next_sync_at=0 if changed else self.repo.clock() + (60 if missing else 300),
        )

    async def tick(self):
        local = self.media.local
        saved = await local(self.state.saved)
        if not saved or not saved["enabled"]:
            return
        rows = await local(self.repo.list, LINK)
        due = [
            r
            for r in rows
            if r["connection_id"] == saved["connection_id"]
            and r["status"] != "deleted"
            and r.get("next_sync_at", 0) <= self.repo.clock()
            and (r["auto_sync"] or r["status"] in {"queued", "syncing", "deleting"})
        ]
        if not due:
            return
        owner, key = str(uuid.uuid4()), "navidrome:" + saved["connection_id"]
        try:
            generation = await local(self.repo.scan_claim, key, owner)
        except Fault as error:
            if error.code == "scan_in_progress":
                return
            raise
        running = asyncio.current_task()

        async def heartbeat():
            while True:
                await asyncio.sleep(5)
                try:
                    await local(self.repo.scan_heartbeat, key, owner, generation)
                except Fault:
                    running.cancel()
                    return

        pulse = asyncio.create_task(heartbeat())
        try:
            connection = await local(self.state.connection)
            catalog = {}
            for link in due:
                try:
                    async with asyncio.timeout(180):
                        await self.sync(connection, link, owner, generation, catalog)
                except (Fault, TimeoutError, OSError) as error:
                    code = error.code if isinstance(error, Fault) else "navidrome_unavailable"
                    if code in {"version_conflict", "scope_changed", "lease_conflict"}:
                        continue
                    status = "deleting" if link["status"] == "deleting" else "failed"
                    if status == "deleting" and code == "navidrome_not_found":
                        status, code = "deleted", "deleted"
                    try:
                        await local(
                            self.repo.fact,
                            LINK,
                            link["link_id"],
                            {
                                "status": status,
                                "code": code,
                                "checked_at": self.repo.clock(),
                                "next_sync_at": self.repo.clock() + 60,
                            },
                            link["revision"],
                        )
                    except Fault:
                        pass
        finally:
            pulse.cancel()
            await asyncio.gather(pulse, return_exceptions=True)
            await local(self.repo.scan_release, key, owner, generation)

    async def pump(self):
        try:
            await self.tick()
        except (Fault, OSError):
            pass
