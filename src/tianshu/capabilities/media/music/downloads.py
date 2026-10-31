"""Music acquisition worker. Queue, leases, cancellation and retry remain owned by Media."""

import asyncio
import json
from pathlib import Path

from ...contracts import Fault, require
from ...local_work import settled
from ..destination import safe_path
from ..video_storage import sync_file
from .audio import cover, lossless_flac, probe, record, write_tags
from .decoding import Decoder
from .quality import verify_quality, satisfies
from .resolver import Resolver
from .selection import Selection
from .storage import AudioStorage
from .lyrics_text import render as render_lyrics, sidecars as lyric_sidecars
from .transfer import Transfer
from .request_control import Deferred


class Downloads:
    def __init__(self, music):
        self.music, self.media = music, music.media
        self.storage, self.transfer = AudioStorage(music), Transfer(control=music.requests)
        self.decoder = Decoder(self.media, self.storage)
        self.resolver = Resolver(music)
        self.selection = Selection(self)
        self.publication_lock = asyncio.Lock()

    async def exclusive(self, operation, *args):
        # Cooperate within this worker pool; the filesystem lock still coordinates processes.
        async with self.publication_lock:
            return await settled(self.media.local, operation, *args)

    async def acquire(self, job, candidate, update):
        provider, cookie = candidate["provider"], candidate["cookie"]
        connector, local = self.music.client(provider), self.media.local
        track = {
            k: v
            for k, v in candidate["track"].items()
            if k not in {"revision", "audio_state", "availability"}
        }
        offered = candidate["offered"]
        maximum = min(self.media.config["engine"]["max_bytes"], 1024**3)
        require(
            offered.get("size") is None or offered["size"] <= maximum, "music_file_too_large", 409
        )
        raw = await local(self.storage.stage, job, "source.part")
        raw.unlink(
            missing_ok=True
        )  # Generation-owned scratch only; retries start a fresh transfer.
        transferred = await self.transfer.fetch(
            offered["url"],
            raw,
            maximum=maximum,
            expected_size=offered.get("size"),
            expected_md5=offered.get("md5"),
            progress=lambda value: update(progress=value),
            provider=provider,
            account_id=candidate["account"]["account_id"],
        )
        await update(state="validating", stage="validating", code="validating")
        raw, _, unlock, artwork, warnings = await self.decoder.prepare(
            job,
            raw,
            update,
            provider=provider,
            hints=(
                offered.get("format"),
                offered["url"],
                (transferred or {}).get("filename"),
                (transferred or {}).get("path"),
            ),
        )
        observed = await probe(raw, track, self.media.config["engine"])
        quality = verify_quality(job["quality"], offered["level"], observed)
        lyrics = {}
        if track.get("album_id") and hasattr(connector, "album"):
            try:
                track.update(await connector.album(track["album_id"], cookie))
            except Fault:
                warnings.append("music_album_unavailable")
        try:
            lyrics = (
                await connector.lyrics_track(track, cookie)
                if hasattr(connector, "lyrics_track")
                else await connector.lyrics(track["source_id"], cookie)
            )
        except Fault:
            warnings.append("music_lyrics_unavailable")
        if track.get("cover_url"):
            picture = await local(self.storage.stage, job, "cover.input")
            try:
                picture.unlink(missing_ok=True)
                await self.transfer.fetch(
                    track["cover_url"],
                    picture,
                    maximum=16 * 1024**2,
                    provider=provider,
                    artwork=True,
                )
                artwork = await local(lambda: cover(picture.read_bytes()))
            except Fault:
                warnings.append("music_cover_unavailable")
        return await self.prepare_package(
            job, track, raw, observed, quality, artwork, lyrics, warnings, unlock=unlock
        )

    async def prepare_package(
        self,
        job,
        track,
        raw,
        observed,
        quality,
        artwork=None,
        lyrics=None,
        warnings=None,
        *,
        unlock=None,
    ):
        local = self.media.local
        provider = track.get("provider", "local")
        lyrics, warnings = lyrics or {}, warnings or []
        if observed["codec"] == "ape":
            converted_path = await local(self.storage.stage, job, "normalized.flac")
            converted = await lossless_flac(
                raw, converted_path, observed, self.media.config["engine"]
            )
            quality = {
                **quality,
                **converted,
                "source_codec": "ape",
                "lossless_conversion": True,
            }
            raw, observed = converted_path, converted
        audio = await local(self.storage.stage, job, "audio." + quality["extension"])
        raw.replace(audio)
        await settled(local, write_tags, audio, track, artwork, render_lyrics(lyrics))
        # Confirm that tag insertion left a fully decodable audio stream.
        tagged = await probe(audio, track, self.media.config["engine"])
        require(
            all(
                tagged[k] == observed[k]
                for k in ("codec", "sample_rate", "channels", "bits_per_sample", "content_id")
            ),
            "music_tag_failed",
            409,
        )
        files = [(audio, "." + quality["extension"], "audio")]
        extras = {}
        for suffix, text in lyric_sidecars(lyrics).items():
            extras[suffix] = (text + "\n").encode("utf-8")
        if artwork:
            extras[".jpg"] = artwork
        extras[".source.json"] = json.dumps(
            {
                "provider": provider,
                "source_id": track["source_id"],
                "recording_id": job["recording_id"],
                "requested_from": {"provider": job["provider"], "source_id": job["source_id"]},
                "metadata": track,
                "quality": quality,
                "warnings": warnings,
                "unlock": unlock,
            },
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8")
        for suffix, content in extras.items():
            path = await local(self.storage.stage, job, "sidecar" + suffix)
            await local(path.write_bytes, content)
            await local(sync_file, path)
            files.append(
                (
                    path,
                    suffix,
                    "metadata"
                    if suffix == ".source.json"
                    else "cover"
                    if suffix == ".jpg"
                    else "lyrics",
                )
            )
        return {
            "track": track,
            "download_provider": provider,
            "download_source_id": track["source_id"],
            "content_id": observed["content_id"],
            "requested_quality": job["quality"],
            "quality": quality,
            "warnings": warnings,
            "unlock": unlock,
            "files": [
                {
                    "staging": path.relative_to(self.media.config["staging_directory"]).as_posix(),
                    "suffix": suffix,
                    "kind": kind,
                    **await local(record, path),
                }
                for path, suffix, kind in files
            ],
        }

    def package_present(self, package):
        if not package:
            return False
        try:
            return all(
                record(safe_path(Path(self.media.config["staging_directory"]), f["staging"]))
                == {k: f[k] for k in ("sha256", "size_bytes")}
                for f in package["files"]
            )
        except (OSError, Fault):
            return False

    async def run(self, job, owner):
        if job.get("audio_operation") in {"scan", "import"}:
            return await self.music.imports.run(job, owner)
        repo, local = self.media.repo, self.media.local
        identity, generation = job["job_id"], job["generation"]
        running = asyncio.current_task()

        async def update(**changes):
            nonlocal job
            job = await local(
                repo.update_job, identity, changes, owner=owner, generation=generation
            )
            return job

        async def pulse():
            while True:
                await asyncio.sleep(5)
                try:
                    await local(self.storage.guard, job, owner)
                except Fault:
                    current = await local(repo.job, identity, include_deleted=True)
                    if (
                        job["state"] in {"completed", "queued"}
                        or current
                        and current["generation"] == generation
                        and current["state"] in {"completed", "queued"}
                    ):
                        return
                    running.cancel()
                    return

        heartbeat = asyncio.create_task(pulse())
        try:
            quality, request_version = await local(self.resolver.requests, job)
            await update(
                quality=quality,
                processing_request_version=request_version,
                acquisition_account_id=None,
                acquisition_account_revision=None,
                audio_unlock=None,
                audio_attempts=[],
            )
            await self.music.imports.ensure_indexed(job, update)
            await self.storage.refresh_file(job)
            intent = await local(repo.get, "music_publication", identity)
            receipt = job.get("audio_receipt") or {}

            def signatures(files):
                return sorted((f["path"], f["sha256"], f["size_bytes"]) for f in files)

            unsettled = intent and (
                intent["state"] == "prepared"
                or signatures(intent["files"]) != signatures(receipt.get("files", []))
            )
            if (
                unsettled
                and intent["state"] != "retired"
                and satisfies(job["quality"], intent)
                and await local(self.storage.matches, intent)
            ):
                job = await self.exclusive(self.storage.publish, job, owner)
            else:
                existing = await local(self.storage.available, job)
                prepared = job.get("audio_package")
                if not await local(self.package_present, prepared):
                    prepared = None
                package, reused = await self.selection.choose(job, update, existing, prepared)
                if reused:
                    completed = await self.exclusive(self.storage.reuse, job, owner, reused)
                    require(completed is not None, "music_staging_changed", 409)
                    job = completed
                else:
                    resume = (
                        intent
                        and package is prepared
                        and intent["state"] == "prepared"
                        and await local(self.storage.matches, intent, True)
                    )
                    if intent and not resume:
                        await local(self.storage.retire, job)
                    await update(
                        audio_package=package,
                        actual_quality=package["quality"]["level"],
                        audio_quality=package["quality"],
                        download_provider=package["download_provider"],
                        download_source_id=package["download_source_id"],
                        audio_unlock=package.get("unlock"),
                        state="publishing",
                        stage="publishing",
                        code="publishing",
                    )
                    job = await self.exclusive(self.storage.publish, job, owner)
            try:
                await self.exclusive(self.storage.cleanup, job)
            except (Fault, OSError):
                pass  # Existing retention pump retries cleanup after confirmed publication.
            await self.exclusive(self.storage.export_related, job)
        except asyncio.CancelledError:
            current = await local(repo.job, identity, include_deleted=True)
            if current and current["generation"] == generation and current["state"] != "completed":
                await local(
                    repo.update_job,
                    identity,
                    {
                        "state": "cancelled" if current["cancel_requested"] else "retry_wait",
                        "code": "user_cancelled"
                        if current["cancel_requested"]
                        else "worker_interrupted",
                        "retry_delay": 5,
                    },
                    generation=generation,
                )
            raise
        except (Fault, OSError, TimeoutError) as error:
            code = error.code if isinstance(error, Fault) else "media_io_failure"
            deferred = isinstance(error, Deferred)
            attempts = job.get("attempts", 0) + (0 if code == "busy" or deferred else 1)
            transient = code in {
                "music_download_unavailable",
                "music_upstream_unavailable",
                "music_rate_limited",
                "busy",
            }
            state = (
                "cancelled"
                if code == "job_cancelled"
                else "auth_required"
                if code in {"music_auth_required", "music_account_unavailable", "account_changed"}
                else "retry_wait"
                if code == "busy" or deferred or transient and attempts < 4
                else "failed"
            )
            review_failed = (
                job.get("quality_review")
                and state in {"failed", "auth_required"}
                and job.get("audio_receipt")
                and await local(self.storage.matches, job["audio_receipt"])
            )
            if review_failed:
                await local(self.music.maintenance.failed, job, code)
                state, code = "completed", "music_quality_check_failed"
            try:
                await update(
                    state=state,
                    code=code,
                    attempts=attempts,
                    quality_review=False if review_failed else job.get("quality_review", False),
                    retry_delay=max(1, error.until - repo.clock())
                    if deferred
                    else 5
                    if code == "busy"
                    else min(300, 15 * 2**attempts),
                )
            except Fault:
                pass  # Lost lease/cancel is settled by its owner; never overwrite a newer worker.
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
