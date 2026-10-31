"""Low-frequency lyric completion, independent of the audio download queue."""

import asyncio
import json
import uuid
from collections import Counter

from ...contracts import Fault
from .lyrics_storage import KIND, VERSION, WEEK, LyricStorage, archive_key, read_local, schedule
from .lyrics_text import coverage, normalize, score
from .maintenance import phase
from .request_control import Deferred


class Lyrics:
    def __init__(self, music):
        self.music, self.media, self.repo = music, music.media, music.media.repo
        self.storage = LyricStorage(music)

    def plan(self):
        now = self.repo.clock()
        with self.repo.transaction() as db:
            settings = self.repo._document(db, "music_settings", "library") or {}
            rows = db.execute(
                "SELECT f.body AS artifact,c.body AS checked FROM documents f JOIN documents r ON r.kind='music_recording' AND r.id=f.id LEFT JOIN documents c ON c.kind=%s AND c.id=f.id WHERE f.kind='music_library_file' AND (r.body::jsonb #>> '{merged_into}') IS NULL AND NOT coalesce((f.body::jsonb #>> '{missing}')::boolean,false) AND (f.body::jsonb #>> '{directory}')=%s",
                (KIND, settings.get("directory")),
            ).fetchall()
            due = []
            for row in rows:
                artifact = json.loads(row["artifact"])
                state = json.loads(row["checked"]) if row["checked"] else {}
                if (
                    state.get("archive_key") != archive_key(artifact)
                    or state.get("version") != VERSION
                ):
                    schedule(self.repo, artifact["recording_id"], artifact, db)
                    state = {}
                if state.get("next_check_at", 0) <= now:
                    due.append((state.get("next_check_at", 0), artifact["recording_id"], artifact))
            return min(due, key=lambda row: row[:2])[2] if due else None

    def view(self):
        with self.repo.transaction() as db:
            rows = [
                json.loads(r[0])
                for r in db.execute(
                    "SELECT c.body FROM documents c JOIN documents r ON r.kind='music_recording' AND r.id=c.id WHERE c.kind=%s AND (r.body::jsonb #>> '{merged_into}') IS NULL",
                    (KIND,),
                )
            ]
        counts = dict(Counter(r["state"] for r in rows))
        return {
            "counts": counts,
            "tracked": len(rows),
            "synced": sum(bool(r.get("coverage", {}).get("synced")) for r in rows),
            "translated": sum(bool(r.get("coverage", {}).get("translation_synced")) for r in rows),
            "retry_min_seconds": 21600,
            "check_interval_seconds": WEEK,
        }

    async def collect(self, artifact, prior):
        local = self.media.local
        catalog, library = self.music.library.catalog, self.music.library
        recording = await local(catalog.recording, artifact["recording_id"])
        original = recording["metadata"]
        best = normalize(await local(read_local, artifact), original.get("duration_ms"))
        source = prior.get("source") or {
            "provider": artifact.get("download_provider", "local"),
            "source_id": artifact.get("download_source_id"),
        }
        checked = []
        if prior.get("network_after", 0) > self.repo.clock():
            return best, source, prior.get("providers", [])
        # A complete local timeline needs no network recheck. Missing files are
        # discovered by the weekly local pass, without hashing/decoding audio.
        if coverage(best)["translation_synced"]:
            return best, source, checked
        accounts = [
            a for a in await local(self.repo.list, "music_account") if a["state"] == "ready"
        ]
        accounts.sort(key=lambda a: (a["provider"] != "netease", a["account_id"]))
        for account in accounts:
            provider = account["provider"]
            result = {
                "provider": provider,
                "account_id": account["account_id"],
                "code": "music_lyrics_unavailable",
            }
            checked.append(result)
            try:
                account, cookie = await local(library.account, account["account_id"])
                client = self.music.client(provider)
                tracks = await self.music.downloads.resolver.matching_tracks(
                    account, cookie, original, artifact["recording_id"], result
                )
                for track in tracks[:3]:
                    value = (
                        await client.lyrics_track(track, cookie)
                        if hasattr(client, "lyrics_track")
                        else await client.lyrics(track["source_id"], cookie)
                    )
                    value = normalize(value, original.get("duration_ms"))
                    result["code"] = "music_lyrics_found" if value else "music_lyrics_unavailable"
                    if score(value) > score(best):
                        best = value
                        source = {"provider": provider, "source_id": track["source_id"]}
                    if coverage(best)["translation_synced"]:
                        break
                if coverage(best)["translation_synced"]:
                    break
            except (Fault, TimeoutError) as error:
                result["code"] = (
                    error.code if isinstance(error, Fault) else "music_upstream_unavailable"
                )
                result["failed"] = True
                if isinstance(error, Deferred):
                    result["retry_at"] = error.until
        if not accounts:
            checked.append({"code": "music_auth_required", "failed": True})
        return best, source, checked

    async def tick(self, recording_id=None):
        local = self.media.local
        owner, key = str(uuid.uuid4()), "music_lyrics"
        try:
            generation = await local(self.repo.scan_claim, key, owner)
        except Fault as error:
            if error.code == "scan_in_progress":
                return None
            raise

        async def heartbeat():
            while True:
                await asyncio.sleep(5)
                await local(self.repo.scan_heartbeat, key, owner, generation)

        pulse = asyncio.create_task(heartbeat())
        artifact = None
        try:
            artifact = (
                await local(self.music.library.catalog.file, recording_id)
                if recording_id
                else await local(self.plan)
            )
            if not artifact:
                return None
            identity = artifact["recording_id"]
            prior = await local(self.repo.get, KIND, identity) or {}
            if prior.get("archive_key") != archive_key(artifact):
                prior = {}
            # Finish a crash-interrupted sidecar write before making cloud calls.
            if prior.get("pending"):
                return await self.music.downloads.exclusive(
                    self.storage.publish, artifact, {}, {}, owner, generation
                )
            async with asyncio.timeout(240):
                value, source, checked = await self.collect(artifact, prior)
            present = coverage(value)
            failures = [row for row in checked if row.get("failed")]
            local_only = prior.get("network_after", 0) > self.repo.clock()
            attempts = (
                prior.get("attempts", 0)
                if local_only
                else prior.get("attempts", 0) + 1
                if failures
                else 0
            )
            wait = min(WEEK, 21600 * 2 ** min(attempts - 1, 5)) if failures else WEEK
            now = self.repo.clock()
            next_at = max(
                now + wait + phase(identity, 1800),
                min((r.get("retry_at", 0) for r in failures), default=0),
            )
            if local_only:
                next_at = prior["network_after"]
            result = {
                "state": "retry_wait"
                if failures
                else "synced"
                if present["synced"]
                else "plain"
                if present["original"]
                else "missing",
                "coverage": present,
                "source": source if present["original"] else None,
                "providers": checked,
                "checked_at": prior.get("checked_at", now) if local_only else now,
                "next_check_at": next_at,
                "attempts": attempts,
            }
            return await self.music.downloads.exclusive(
                self.storage.publish, artifact, value, result, owner, generation
            )
        except Exception as error:
            if artifact:
                code = error.code if isinstance(error, Fault) else "music_lyrics_unavailable"

                def remember():
                    with self.repo.transaction() as db:
                        self.storage.current(artifact, owner, generation, db)
                        prior = self.repo._document(db, KIND, artifact["recording_id"]) or {}
                        self.repo.put(
                            KIND,
                            artifact["recording_id"],
                            {
                                **prior,
                                "recording_id": artifact["recording_id"],
                                "archive_key": archive_key(artifact),
                                "state": "retry_wait",
                                "code": code,
                                "next_check_at": self.repo.clock() + 21600,
                            },
                            db=db,
                        )

                try:
                    await local(remember)
                except Fault:
                    pass
            return None
        finally:
            pulse.cancel()
            await asyncio.gather(pulse, return_exceptions=True)
            await local(self.repo.scan_release, key, owner, generation)

    async def pump(self):
        await self.tick()
