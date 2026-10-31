"""Audio publication intents and verified files, in the shared media ledger and root lock."""

import json
import os
from pathlib import Path

from ...contracts import Fault, digest, require
from ..destination import safe_path, path_component as component
from ..organizer import library_lock
from ..video_storage import copy_file, sync_directory, sync_file
from .audio import record, probe
from .quality import satisfies, quality_rank
from .catalog import source_key
from .navidrome.state import mark_dirty
from .navidrome.exports import ignore_export


def stem(track, layout):
    artist = component(", ".join(track.get("artists") or []), "未知艺术家", 80)
    album_artist = component(
        ", ".join(track.get("album_artists") or track.get("artists") or []), "未知艺术家", 80
    )
    return "/".join(
        (
            album_artist if layout == "album_artist_album" else artist,
            component(track.get("album"), "未知专辑", 100),
            artist + " - " + component(track.get("title"), track["source_id"], 80),
        )
    )


class AudioStorage:
    def __init__(self, music):
        self.music, self.media = music, music.media
        self.repo = music.media.repo

    def stage(self, job, name):
        root = Path(self.media.config["staging_directory"])
        if "candidate_index" in job:
            name = f"candidate-{job['candidate_index']}/{name}"
        relative = f".music/{job['job_id']}/{job['generation']}/{name}"
        path = safe_path(root, relative)
        with self.repo.transaction() as db:
            prior = self.repo._document(db, "music_stage", job["job_id"]) or {"files": []}
            self.repo.put(
                "music_stage",
                job["job_id"],
                {
                    "files": list(dict.fromkeys([*prior["files"], relative])),
                    "state": "retained",
                },
                db=db,
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def check_scope(self, job):
        settings = self.music.library.settings()
        require(
            settings["directory"] == job["destination_directory"]
            and settings["layout"] == job["audio_layout"],
            "scope_changed",
            409,
        )
        require(Path(settings["directory"]).is_dir(), "music_directory_unavailable", 409)

    def guard(self, job, owner):
        self.check_scope(job)
        self.repo.heartbeat(job["job_id"], owner, job["generation"])
        if job.get("acquisition_account_id"):
            account, _ = self.music.library.account(job["acquisition_account_id"])
            require(
                account["revision"] == job["acquisition_account_revision"], "account_changed", 409
            )

    async def refresh_file(self, job):
        """Music Tag Web may change tags; decoded audio identity remains the authority."""
        local, catalog = self.media.local, self.music.library.catalog
        artifact = await local(catalog.file, job["recording_id"])
        if (
            not artifact
            or artifact["directory"] != job["destination_directory"]
            or await local(self.matches, artifact, include_lyrics=False)
        ):
            return
        if not artifact.get("content_id"):
            return
        try:
            root = Path(artifact["directory"])
            audio = next(f for f in artifact["files"] if f["kind"] == "audio")
            observed = await probe(
                safe_path(root, audio["path"]), None, self.media.config["engine"]
            )
            if observed["content_id"] != artifact["content_id"]:
                return
            files = []
            for item in artifact["files"]:
                path = safe_path(root, item["path"])
                if path.is_file():
                    files.append({**item, **await local(record, path)})

            def save():
                with self.repo.transaction() as db:
                    catalog.store_file(job["recording_id"], {**artifact, "files": files}, db)

            await local(save)
        except (Fault, OSError, StopIteration):
            return

    def matches(self, artifact, allow_missing=False, *, include_lyrics=True):
        root = Path(artifact["directory"])
        try:
            return bool(artifact["files"]) and all(
                (allow_missing and not safe_path(root, item["path"]).exists())
                or record(safe_path(root, item["path"]))
                == {k: item[k] for k in ("sha256", "size_bytes")}
                for item in artifact["files"]
                if include_lyrics or item["kind"] != "lyrics"
            )
        except (OSError, Fault):
            return False

    def available(self, job):
        self.check_scope(job)
        artifact = self.music.library.catalog.file(job["recording_id"])
        if not artifact:
            artifact = self.repo.get("music_artifact", job["audio_key"])
        if (
            artifact
            and artifact["directory"] == job["destination_directory"]
            and self.matches(artifact, include_lyrics=False)
        ):
            return artifact
        return None

    def reusable(self, job):
        artifact = self.available(job)
        return artifact if artifact and satisfies(job["quality"], artifact) else None

    def finish_job(self, job, owner, artifact, *, reused=False):
        # Files have crossed the publication point. A late cancel cannot undo that fact.
        with self.repo.transaction() as db:
            row = db.execute(
                'SELECT owner,generation,body FROM jobs WHERE id=%s', (job["job_id"],)
            ).fetchone()
            require(
                row and row["owner"] == owner and row["generation"] == job["generation"],
                "lease_conflict",
                409,
            )
            self.repo.put("music_artifact", job["audio_key"], artifact, db=db)
            catalog = self.music.library.catalog
            artifact = catalog.store_file(job["recording_id"], artifact, db)
            pending = json.loads(row["body"]).get("request_version", 0) != job.get(
                "processing_request_version", 0
            )
            comparison = {}
            if job.get("audio_routing"):
                comparison["audio_routing"] = {
                    **job["audio_routing"],
                    "selected": {
                        "provider": artifact.get("download_provider", "local"),
                        "source_id": artifact.get("download_source_id"),
                        "quality": artifact["quality"],
                        "reused": reused,
                    },
                }
            completed = self.repo.update_job(
                job["job_id"],
                {
                    **comparison,
                    "state": "queued" if pending else "completed",
                    "stage": "queued" if pending else "completed",
                    "code": "queued" if pending else "music_archived",
                    "audio_receipt": artifact,
                    "audio_reused": reused,
                    "progress": 1,
                    "actual_quality": artifact["quality"]["level"],
                    "audio_quality": artifact["quality"],
                    "audio_unlock": job.get("audio_unlock") or artifact.get("unlock"),
                    "download_provider": artifact.get("download_provider"),
                    "download_source_id": artifact.get("download_source_id"),
                    "cancel_requested": False,
                },
                generation=job["generation"],
                db=db,
            )
            # Publication and the deferred-channel follow-up share one commit.
            return self.music.maintenance.record(completed, db) or completed

    def reuse(self, job, owner, compared=None):
        self.guard(job, owner)
        with library_lock(job["destination_directory"]):
            artifact = self.available(job) if compared else self.reusable(job)
            if (
                compared
                and artifact
                and quality_rank(artifact["quality"]) < quality_rank(compared["quality"])
            ):
                return None
            if artifact:
                self.guard(job, owner)
                return self.finish_job(job, owner, artifact, reused=True)
        return None

    def publish(self, job, owner):
        self.guard(job, owner)
        package = job["audio_package"]
        with library_lock(job["destination_directory"]) as (root, control):
            catalog = self.music.library.catalog
            existing = self.reusable(job)
            if existing and quality_rank(existing["quality"]) >= quality_rank(package["quality"]):
                return self.finish_job(job, owner, existing, reused=True)
            duplicate = (
                catalog.content_file(package.get("content_id"), str(root))
                if package.get("content_id")
                else None
            )
            if (
                duplicate
                and self.matches(duplicate)
                and quality_rank(duplicate["quality"]) >= quality_rank(package["quality"])
            ):
                with self.repo.transaction() as db:
                    catalog.merge(job["recording_id"], duplicate["recording_id"], db)
                return self.finish_job(job, owner, duplicate, reused=True)
            intent = self.repo.get("music_publication", job["job_id"])
            if intent is None or intent["state"] == "retired":
                expected_revision = intent["revision"] if intent else 0
                base = stem(package["track"], job["audio_layout"])
                # Existing files (including unregistered originals) always keep their names and bytes.
                for suffix in (
                    "",
                    f" [{job['provider']}-{job['source_id']}-{job['audio_key'][:8]}]",
                    f" [{job['job_id']}-{job['generation']}]",
                ):
                    proposed = base + suffix
                    parent = safe_path(root, proposed + package["files"][0]["suffix"]).parent
                    names = (
                        {p.name.casefold() for p in parent.iterdir()} if parent.is_dir() else set()
                    )
                    if all(
                        (Path(proposed).name + f["suffix"]).casefold() not in names
                        for f in package["files"]
                    ):
                        break
                else:
                    raise Fault("music_target_conflict", 409)
                intent = self.repo.put(
                    "music_publication",
                    job["job_id"],
                    {
                        "directory": str(root),
                        "job_id": job["job_id"],
                        "source_id": job["source_id"],
                        "quality": package["quality"],
                        "state": "prepared",
                        "warnings": package["warnings"],
                        **{
                            key: package.get(key)
                            for key in (
                                "content_id",
                                "requested_quality",
                                "download_provider",
                                "download_source_id",
                                "unlock",
                            )
                        },
                        "managed": True,
                        "files": [{**f, "path": proposed + f["suffix"]} for f in package["files"]],
                    },
                    expected_revision,
                )
            require(intent["directory"] == str(root), "scope_changed", 409)
            for index, item in enumerate(intent["files"]):
                self.guard(job, owner)
                expected = {k: item[k] for k in ("sha256", "size_bytes")}
                target = safe_path(root, item["path"])
                temporary = safe_path(control, f"music-{job['job_id']}-{index}.tmp")
                if target.exists():
                    require(record(target) == expected, "music_target_conflict", 409)
                    if temporary.exists() and record(temporary) == expected:
                        temporary.unlink()
                    continue
                source = safe_path(Path(self.media.config["staging_directory"]), item["staging"])
                require(record(source) == expected, "music_staging_changed", 409)
                target.parent.mkdir(parents=True, exist_ok=True)
                # This exact private temporary was reserved by the durable intent above.
                if temporary.exists() and record(temporary) != expected:
                    temporary.unlink()
                if not temporary.exists():
                    copy_file(source, temporary)
                require(record(temporary) == expected, "music_staging_changed", 409)
                self.guard(job, owner)
                try:
                    os.link(temporary, target)  # Atomic no-replace, including on SMB.
                except FileExistsError:
                    require(record(target) == expected, "music_target_conflict", 409)
                except OSError:
                    raise Fault("music_atomic_publish_unavailable", 409) from None
                sync_directory(target.parent)
                temporary.unlink()
            require(self.matches(intent), "music_publication_unverified", 409)
            artifact = {k: intent[k] for k in ("directory", "source_id", "quality", "warnings")}
            artifact.update(
                {
                    k: intent.get(k)
                    for k in (
                        "content_id",
                        "requested_quality",
                        "download_provider",
                        "download_source_id",
                        "unlock",
                    )
                }
            )
            artifact["managed"] = True
            artifact.update(
                published_at=self.repo.clock(),
                files=[
                    {k: f[k] for k in ("path", "sha256", "size_bytes", "kind")}
                    for f in intent["files"]
                ],
            )
            self.check_scope(job)
            self.repo.put("music_publication", job["job_id"], {**intent, "state": "published"})
            return self.finish_job(job, owner, artifact)

    def retire(self, job):
        with self.repo.transaction() as db:
            intent = self.repo._document(db, "music_publication", job["job_id"])
            if intent and intent["state"] != "retired":
                self.repo.put(
                    "music_publication_history",
                    f"{job['job_id']}:{intent['revision']}",
                    intent,
                    db=db,
                )
                self.repo.put(
                    "music_publication", job["job_id"], {**intent, "state": "retired"}, db=db
                )

    def cleanup(self, job):
        self.cleanup_retired()
        stage = self.repo.get("music_stage", job["job_id"])
        if not stage or stage["state"] == "cleaned":
            return
        artifact = job.get("audio_receipt")
        require(artifact and self.matches(artifact), "music_publication_unverified", 409)
        root = Path(self.media.config["staging_directory"])
        for relative in stage["files"]:
            require(relative.startswith(f".music/{job['job_id']}/"), "unsafe_library_path", 409)
            if int(relative.split("/")[2]) > job["generation"]:
                continue
            path = safe_path(root, relative)
            path.unlink(missing_ok=True)
            try:
                path.parent.rmdir()
            except OSError:
                pass
        with self.repo.transaction() as db:
            latest = self.repo._document(db, "music_stage", job["job_id"])
            pending = [
                relative
                for relative in latest["files"]
                if int(relative.split("/")[2]) > job["generation"]
            ]
            self.repo.put(
                "music_stage",
                job["job_id"],
                {**latest, "state": "retained" if pending else "cleaned"},
                db=db,
            )

    def cleanup_retired(self):
        for retired in self.repo.list("music_retired_file"):
            if retired["state"] != "pending":
                continue
            current = self.music.library.catalog.file(retired["replacement_recording_id"])
            if not current or not self.matches(current):
                continue
            with library_lock(retired["directory"]):
                keep = {f["path"] for f in current["files"]}
                for item in retired["files"]:
                    path = safe_path(Path(retired["directory"]), item["path"])
                    if (
                        item["path"] not in keep
                        and path.is_file()
                        and record(path) == {k: item[k] for k in ("sha256", "size_bytes")}
                    ):
                        path.unlink()
                key = digest({"recording": retired["recording_id"], "files": retired["files"]})
                self.repo.put("music_retired_file", key, {**retired, "state": "cleaned"})

    def export_related(self, job):
        with self.repo.transaction() as db:
            requests = self.repo._documents(db, "music_request", job.get("request_ids", []))
            playlists = {row["playlist_id"] for row in requests.values()}
            exports = [
                json.loads(raw[0])
                for raw in db.execute("SELECT body FROM documents WHERE kind='music_export'")
            ]
            exports = [p for p in exports if p["directory"] == job["destination_directory"]]
            if job.get("recording_id"):
                catalog = self.music.library.catalog
                subscriptions = self.repo._documents(
                    db, "music_playlist", [p["playlist_id"] for p in exports]
                )
                source_keys = {
                    identity: [
                        source_key(playlist["provider"], source)
                        for source in dict.fromkeys(playlist["entries"])
                    ]
                    for identity, playlist in subscriptions.items()
                    if identity not in playlists
                }
                reads = catalog.reads(
                    db,
                    recordings=[job["recording_id"]],
                    sources=[key for keys in source_keys.values() for key in keys],
                    with_files=False,
                )
                target = reads.resolve(job["recording_id"])
                for exported in exports:
                    identity = exported["playlist_id"]
                    if identity in playlists:
                        continue
                    for key in source_keys.get(identity, []):
                        binding = reads.source(key)
                        if binding and binding["recording_id"] == target:
                            playlists.add(identity)
                            break
                for identity in playlists:
                    mark_dirty(self.repo, db, identity)
            else:
                # A whole-library scan can change many recordings and paths.
                playlists.update(p["playlist_id"] for p in exports)
                mark_dirty(self.repo, db)
        for identity in playlists:
            try:
                self.export(identity)
            except (Fault, OSError):
                pass  # An export failure must not undo verified audio publication.

    def export(self, identity):
        settings = self.music.library.settings()
        require(
            settings["directory"] and Path(settings["directory"]).is_dir(),
            "music_directory_unavailable",
            409,
        )
        with library_lock(settings["directory"]) as (root, control):
            playlist, tracks = self.music.library.export_snapshot(identity)
            exported, lines, verified = [], ["#EXTM3U"], {}
            for track in tracks:
                track.pop("job", None)
                artifact = track.pop("artifact", None)
                audio = (
                    next((f for f in artifact["files"] if f["kind"] == "audio"), None)
                    if artifact
                    else None
                )
                key = artifact.get("recording_id") if artifact else None
                if key and key not in verified:
                    # Export references an already verified archive. It does not
                    # publish/copy audio and must not rehash an entire playlist.
                    # Content checks remain in import, acquisition and reuse.
                    try:
                        path = safe_path(root, audio["path"]) if audio else None
                        verified[key] = bool(
                            artifact["directory"] == str(root)
                            and path
                            and path.is_file()
                            and path.stat().st_size == audio["size_bytes"]
                        )
                    except OSError:
                        verified[key] = False
                valid = verified.get(key)
                relative = "../../" + audio["path"] if valid and audio else None
                exported.append({**track, "file": relative})
                if relative:
                    label = " - ".join([", ".join(track["artists"]), track["title"]])
                    lines += ["#EXTINF:-1," + label.replace("\n", " ").replace("\r", " "), relative]
            folder = "歌单/" + digest(identity)[:20]
            result = {
                "playlist_id": identity,
                "name": playlist["name"],
                "revision": playlist["revision"],
                "tracks": exported,
            }
            payloads = {
                "playlist.m3u8": "\n".join(lines) + "\n",
                "playlist.json": json.dumps(result, ensure_ascii=False, indent=2),
            }
            # A managed export directory is claimed once. Unknown existing directories are retained.
            owner = self.repo.get("music_export", digest({"root": str(root), "playlist": identity}))
            directory = safe_path(root, folder)
            require(owner or not directory.exists(), "music_target_conflict", 409)
            self.repo.put(
                "music_export",
                digest({"root": str(root), "playlist": identity}),
                {"directory": str(root), "relative": folder, "playlist_id": identity},
            )
            directory.mkdir(parents=True, exist_ok=True)
            navidrome = self.repo.get("music_navidrome", "server")
            if navidrome and navidrome["enabled"]:
                ignore_export(directory)
            for name, content in payloads.items():
                target = safe_path(directory, name)
                if target.is_file() and target.read_bytes() == content.encode("utf-8"):
                    continue
                temporary = safe_path(control, "music-export-" + digest(identity)[:20] + ".tmp")
                temporary.write_text(content, encoding="utf-8", newline="\n")
                sync_file(temporary)
                temporary.replace(target)
            sync_directory(directory)
            return {
                "directory": str(directory),
                "total": len(tracks),
                "exported": sum(bool(t["file"]) for t in exported),
                "files": list(payloads),
            }
