"""Music accounts and ordered playlist snapshots in the existing media Repository."""

import json
import os
from pathlib import Path

from ...contracts import require
from ..config import text
from .catalog import Catalog, ENRICHED_FIELDS, source_key
from .providers import PROVIDERS, identity
from .quality import policy, satisfies
from .queue import enqueue_playlist
from .navidrome.state import mark_dirty, pause_playlist


def public_account(account):
    return {
        key: account[key]
        for key in ("account_id", "provider", "user_id", "label", "state", "revision", "checked_at")
    }


def summary(playlist):
    return {key: value for key, value in playlist.items() if key != "entries"}


class MusicLibrary:
    def __init__(self, repository, accounts):
        self.repo, self.accounts = repository, accounts
        self.catalog = Catalog(repository)

    def account(self, identity):
        require(isinstance(identity, str), "invalid_input", 400)
        account = self.repo.get("music_account", identity)
        require(
            account is not None and account["state"] != "revoked", "music_account_unavailable", 409
        )
        require(account["state"] == "ready", "music_auth_required", 409)
        return account, {
            **json.loads(self.accounts.unseal(identity, account["secret"])),
            "_music_account_id": identity,
            "_music_account_revision": account["revision"],
        }

    def check_account(self, account, db):
        current = self.repo._document(db, "music_account", account["account_id"])
        require(
            current and current["revision"] == account["revision"] and current["state"] == "ready",
            "account_changed",
            409,
        )

    def save_account(self, profile, cookie, provider="netease"):
        cookie = {key: value for key, value in cookie.items() if not key.startswith("_music_")}
        require(provider in PROVIDERS, "invalid_input", 400)
        account_id = provider + ":" + identity(profile["user_id"])
        require(
            isinstance(cookie.get("MUSIC_U" if provider == "netease" else "cookie"), str)
            and cookie.get("MUSIC_U" if provider == "netease" else "cookie"),
            "music_auth_required",
            409,
        )
        with self.repo.transaction() as db:
            account = self.repo.put(
                "music_account",
                account_id,
                {
                    "account_id": account_id,
                    "provider": provider,
                    "user_id": profile["user_id"],
                    "label": profile["label"] or PROVIDERS[provider],
                    "state": "ready",
                    "checked_at": self.repo.clock(),
                    "secret": self.accounts.seal(account_id, json.dumps(cookie)),
                },
                db=db,
            )
            control = self.repo._document(db, "music_request_control", account_id)
            if control and control.get("auth_required"):
                self.repo.put(
                    "music_request_control", account_id, {**control, "auth_required": False}, db=db
                )
            self.resume_account_jobs(account, db)
            return public_account(account)

    def resume_account_jobs(self, account, db):
        """Fresh credentials wake only jobs blocked on this account's login."""
        requests = {
            raw[0]
            for raw in db.execute(
                "SELECT id FROM documents WHERE kind='music_request' AND (body::jsonb #>> '{account_id}')=%s",
                (account["account_id"],),
            )
        }
        for raw in db.execute(
            "SELECT body FROM jobs WHERE state IN ('auth_required','failed') AND (body::jsonb #>> '{media_kind}')='audio'"
        ).fetchall():
            job = json.loads(raw[0])
            if job.get("code") not in {
                "music_auth_required",
                "music_account_unavailable",
                "account_changed",
            }:
                continue
            if account["account_id"] not in {
                job.get("account_id"),
                job.get("acquisition_account_id"),
            } and not requests.intersection(job.get("request_ids", [])):
                continue
            self.repo.update_job(
                job["job_id"],
                {
                    "state": "queued",
                    "stage": "queued",
                    "code": "music_account_restored",
                    "attempts": 0,
                    "cancel_requested": False,
                },
                db=db,
            )

    def account_state(self, account, state):
        current = self.repo.get("music_account", account["account_id"])
        if current and current["revision"] != account["revision"] and current["state"] == state:
            return current
        return self.repo.put(
            "music_account",
            account["account_id"],
            {
                **account,
                "state": state,
                "checked_at": self.repo.clock(),
                "secret": None if state == "revoked" else account["secret"],
            },
            account["revision"],
        )

    def revoke(self, body, db):
        account = self.repo._document(db, "music_account", body["account_id"])
        require(account is not None, "not_found", 404)
        account = self.repo.put(
            "music_account",
            account["account_id"],
            {
                **account,
                "state": "revoked",
                "secret": None,
                "checked_at": self.repo.clock(),
            },
            body["expected_revision"],
            db=db,
        )
        return {"account": public_account(account)}

    def settings(self):
        return self.repo.get("music_settings", "library") or {
            "revision": 0,
            "directory": "",
            "layout": "artist_album",
            "filename": "artist_title",
        }

    def save_settings(self, body, db):
        directory = text(body.get("directory"), 4096)
        require(Path(directory).is_absolute(), "music_directory_absolute", 400)
        require(body.get("layout") in {"artist_album", "album_artist_album"}, "invalid_input", 400)
        return {
            "settings": self.repo.put(
                "music_settings",
                "library",
                {
                    "directory": directory,
                    "layout": body["layout"],
                    "filename": "artist_title",
                },
                body["expected_revision"],
                db=db,
            )
        }

    def inspect_directory(self, directory):
        root = Path(text(directory, 4096))
        require(root.is_absolute(), "music_directory_absolute", 400)
        require(
            not any(p.is_symlink() or p.is_junction() for p in (root, *root.parents)),
            "unsafe_library_path",
            409,
        )
        try:
            require(root.is_dir(), "music_directory_unavailable", 409)
            rows = []
            with os.scandir(root) as artists:
                for artist in artists:
                    if len(rows) >= 12:
                        break
                    if (
                        artist.name.startswith(".")
                        or artist.name == "attachments"
                        or not artist.is_dir(follow_symlinks=False)
                        or Path(artist.path).is_junction()
                    ):
                        continue
                    with os.scandir(artist.path) as albums:
                        count = 0
                        for album in albums:
                            if count >= 2:
                                break
                            if (
                                not album.is_dir(follow_symlinks=False)
                                or Path(album.path).is_junction()
                            ):
                                continue
                            count += 1
                            with os.scandir(album.path) as files:
                                samples = []
                                for file in files:
                                    if len(samples) >= 3:
                                        break
                                    if file.is_file(follow_symlinks=False) and Path(
                                        file.name
                                    ).suffix.lower() in {
                                        ".flac",
                                        ".mp3",
                                        ".m4a",
                                        ".ogg",
                                        ".opus",
                                        ".lrc",
                                    }:
                                        samples.append(file.name)
                            rows.append(
                                {"artist": artist.name, "album": album.name, "files": samples}
                            )
            return {
                "state": "readable",
                "checked_at": self.repo.clock(),
                "samples": rows,
                "sample_only": True,
            }
        except OSError:
            return {
                "state": "unavailable",
                "checked_at": self.repo.clock(),
                "samples": [],
                "sample_only": True,
            }

    def view(self):
        return {
            "configured": True,
            "provider": "netease",
            "audio_download_available": True,
            "accounts": [public_account(row) for row in self.repo.list("music_account")],
            "playlists": [summary(row) for row in self.repo.list("music_playlist")],
            "settings": self.settings(),
        }

    def subscribe(self, account, snapshot, interval, expected, db):
        self.check_account(account, db)
        identity = account["account_id"] + ":playlist:" + snapshot["playlist"]["source_id"]
        current = self.repo._document(db, "music_playlist", identity)
        removed = current and current.get("state") == "removed"
        require(
            (current["revision"] if current and not removed else 0) == expected,
            "version_conflict",
            409,
        )
        return self.install_snapshot(
            account,
            identity,
            snapshot,
            {
                "interval_seconds": interval,
                "state": "active",
                "revision": current["revision"] if removed else expected,
            },
            db,
        )

    def install_snapshot(self, account, identity, snapshot, current, db):
        self.check_account(account, db)
        bindings = {}
        previous_tracks = self.repo._documents(
            db,
            "music_track",
            [
                account["account_id"] + ":track:" + track["source_id"]
                for track in snapshot["tracks"]
            ],
        )
        for track in snapshot["tracks"]:
            track = {**track, "provider": account["provider"]}
            if track.get("availability") == "known":
                binding = self.catalog.register(account["provider"], track, db)
                bindings[track["source_id"]] = binding
                track.update(binding)
            track_id = account["account_id"] + ":track:" + track["source_id"]
            previous = previous_tracks.get(track_id)
            if previous and track["availability"] == "unavailable":
                track = {**previous, "availability": "unavailable"}
            previous_tracks[track_id] = self.repo.put("music_track", track_id, track, db=db)
        now = self.repo.clock()
        result = self.repo.put(
            "music_playlist",
            identity,
            {
                **snapshot["playlist"],
                "playlist_id": identity,
                "provider": account["provider"],
                "account_id": account["account_id"],
                "entries": snapshot["entries"],
                "member_count": len(snapshot["entries"]),
                "state": current["state"],
                "interval_seconds": current["interval_seconds"],
                "synced_at": now,
                "metadata_synced_at": now,
                "next_scan_at": now + current["interval_seconds"],
                "code": "ready",
                "complete": True,
                "auto_download": current.get("auto_download", False),
                "quality": policy(current.get("quality")),
            },
            current["revision"],
            db=db,
        )
        if result["auto_download"]:
            enqueue_playlist(self, identity, db, bindings=bindings)
        mark_dirty(self.repo, db, identity)
        return {"playlist": summary(result)}

    def complete_scan(self, account, subscription, snapshot, owner, generation):
        with self.repo.transaction() as db:
            self.repo.require_scan(db, "music:" + subscription["playlist_id"], owner, generation)
            if snapshot.get("unchanged"):
                self.check_account(account, db)
                result = self.repo.put(
                    "music_playlist",
                    subscription["playlist_id"],
                    {
                        **subscription,
                        **snapshot["playlist"],
                        "code": "ready",
                        "synced_at": self.repo.clock(),
                        "next_scan_at": self.repo.clock() + subscription["interval_seconds"],
                    },
                    subscription["revision"],
                    db=db,
                )
                return {"playlist": summary(result)}
            return self.install_snapshot(
                account, subscription["playlist_id"], snapshot, subscription, db
            )

    def control(self, body, db):
        row = self.repo._document(db, "music_playlist", body["playlist_id"])
        require(row and row.get("state") != "removed", "not_found", 404)
        require(body["action"] in {"pause", "resume", "remove"}, "invalid_input", 400)
        if body["action"] == "remove":
            self.repo.remove_subscription(
                "music_playlist",
                row["playlist_id"],
                body["expected_revision"],
                "music:" + row["playlist_id"],
                db=db,
                changes={"auto_download": False},
            )
            pause_playlist(self.repo, db, row["playlist_id"])
            return {"removed": True}
        row = self.repo.put(
            "music_playlist",
            row["playlist_id"],
            {
                **row,
                "state": "paused" if body["action"] == "pause" else "active",
                "next_scan_at": self.repo.clock(),
            },
            body["expected_revision"],
            db=db,
        )
        return {"playlist": summary(row)}

    def _tracks(self, playlist, entries, db):
        """One page/export read phase; reuse bindings, aliases, files and jobs per transaction."""
        track_keys = {source: playlist["account_id"] + ":track:" + source for source in entries}
        tracks = self.repo._documents(db, "music_track", track_keys.values())
        source_keys = {source: source_key(playlist["provider"], source) for source in entries}
        reads = self.catalog.reads(db, sources=source_keys.values())
        settings = self.repo._document(db, "music_settings", "library") or {}
        mapping = (
            self.repo._document(db, "music_playlist_jobs", playlist["playlist_id"]) or {}
        ).get("jobs", {})
        # These projections historically include a playlist's recorded terminal job even after
        # it leaves the task list. Keep that reference semantics while batching the lookup.
        jobs = self.repo._jobs(
            db, [mapping[source] for source in entries if source in mapping], include_deleted=True
        )
        result = []
        for source in entries:
            track = tracks.get(track_keys[source])
            binding = reads.source(source_keys[source])
            artifact = None
            if binding:
                track = {**track, "recording_id": binding["recording_id"]}
                metadata = binding["metadata"]
                if metadata.get("metadata_enriched"):
                    track.update({k: metadata[k] for k in ENRICHED_FIELDS if metadata.get(k)})
                artifact = reads.file(binding["recording_id"])
            if artifact and (
                artifact.get("missing") or artifact["directory"] != settings.get("directory")
            ):
                artifact = None
            result.append((track, artifact, jobs.get(mapping.get(source))))
        return result

    def detail(self, identity, offset, limit=100):
        require(type(offset) is int and offset >= 0, "invalid_input", 400)
        with self.repo.transaction() as db:
            row = self.repo._document(db, "music_playlist", identity)
            require(row is not None, "not_found", 404)
            tracks = []
            for index, (track, artifact, job) in enumerate(
                self._tracks(row, row["entries"][offset : offset + limit], db), offset
            ):
                audio = next(
                    (f for f in (artifact or {}).get("files", []) if f["kind"] == "audio"), None
                )
                state = job["state"] if job else "not_downloaded"
                if audio and (
                    state in {"completed", "not_downloaded"}
                    or state in {"failed", "auth_required", "cancelled"}
                    and satisfies(policy(row.get("quality")), artifact)
                ):
                    state = "completed"
                tracks.append(
                    {
                        **track,
                        "position": index + 1,
                        "audio_state": state if audio or state != "completed" else "not_downloaded",
                        "audio_code": "music_archived"
                        if state == "completed" and audio
                        else job["code"]
                        if job
                        else None,
                        "audio_quality": artifact.get("quality") if artifact else None,
                        "audio_unlock": (artifact or {}).get("unlock")
                        if state == "completed"
                        else (job or {}).get("audio_unlock"),
                        "download_provider": artifact.get("download_provider")
                        if artifact
                        else None,
                        "download_source_id": artifact.get("download_source_id")
                        if artifact
                        else None,
                        "file": audio["path"] if audio else None,
                        "job_id": job["job_id"] if job else None,
                    }
                )
            return {
                "playlist": summary(row),
                "tracks": tracks,
                "offset": offset,
                "more": offset + len(tracks) < len(row["entries"]),
            }

    def downloads(self, body, db):
        row = self.repo._document(db, "music_playlist", body["playlist_id"])
        require(row and row.get("state") != "removed", "not_found", 404)
        require(type(body["auto_download"]) is bool, "invalid_input", 400)
        row = self.repo.put(
            "music_playlist",
            row["playlist_id"],
            {
                **row,
                "auto_download": body["auto_download"],
                "quality": policy(body["quality"]),
            },
            body["expected_revision"],
            db=db,
        )
        result = enqueue_playlist(self, row["playlist_id"], db) if row["auto_download"] else {}
        return {"playlist": summary(row), **result}

    def download(self, body, db):
        row = self.repo._document(db, "music_playlist", body["playlist_id"])
        require(row and row.get("state") != "removed", "not_found", 404)
        require(row and row["revision"] == body["expected_revision"], "version_conflict", 409)
        return enqueue_playlist(self, row["playlist_id"], db, manual=True)

    def export_snapshot(self, identity):
        with self.repo.transaction() as db:
            playlist = self.repo._document(db, "music_playlist", identity)
            require(playlist is not None, "not_found", 404)
            tracks = []
            for index, (track, artifact, job) in enumerate(
                self._tracks(playlist, playlist["entries"], db)
            ):
                tracks.append(
                    {
                        "source_id": playlist["entries"][index],
                        "position": index + 1,
                        "title": track["title"],
                        "artists": track["artists"],
                        "album": track["album"],
                        "provider": playlist["provider"],
                        "recording_id": track.get("recording_id"),
                        "artifact": artifact,
                        "job": job,
                    }
                )
            return playlist, tracks
