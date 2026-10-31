"""Weekly recording checks reuse Media jobs and publication; no second download queue."""

import asyncio
import json

from ...contracts import digest, require
from ..repository import VISIBLE

SETTINGS = "music_maintenance"
CHECK = "music_quality_check"
WEEK = 7 * 86400
DEFAULT_SYNC = 12 * 3600


def phase(identity, period):
    return int(digest(identity)[:12], 16) % period


class Maintenance:
    def __init__(self, music):
        self.music, self.repo = music, music.media.repo

    def settings(self, db=None):
        if db is None:
            with self.repo.transaction() as db:
                return self.settings(db)
        return self.repo._document(db, SETTINGS, "settings") or {
            "enabled": False,
            "interval_seconds": WEEK,
            "playlist_interval_seconds": DEFAULT_SYNC,
            "enabled_at": 0,
            "revision": 0,
        }

    def save(self, body, db):
        current = self.settings(db)
        require(
            type(body["enabled"]) is bool
            and type(body["playlist_interval_seconds"]) is int
            and 3600 <= body["playlist_interval_seconds"] <= WEEK,
            "invalid_input",
            400,
        )
        now = self.repo.clock()
        interval = body["playlist_interval_seconds"]
        updated = self.repo.put(
            SETTINGS,
            "settings",
            {
                **current,
                "enabled": body["enabled"],
                "playlist_interval_seconds": interval,
                "enabled_at": current["enabled_at"] or now,
            },
            body["expected_revision"],
            db=db,
        )
        changed = 0
        if interval != current["playlist_interval_seconds"] or current["revision"] == 0:
            for raw in db.execute(
                "SELECT body FROM documents WHERE kind='music_playlist'"
            ).fetchall():
                playlist = json.loads(raw[0])
                self.repo.put(
                    "music_playlist",
                    playlist["playlist_id"],
                    {
                        **playlist,
                        "interval_seconds": interval,
                        "next_scan_at": now + max(60, phase(playlist["playlist_id"], interval)),
                    },
                    db=db,
                )
                changed += 1
        if not body["enabled"]:
            # A queued review can return to its already archived state. In-flight publication settles normally.
            for raw in db.execute(
                "SELECT body FROM jobs WHERE state IN ('queued','retry_wait') AND owner IS NULL AND (body::jsonb #>> '{quality_review}')::boolean IS TRUE AND NOT coalesce((body::jsonb #>> '{quality_catchup}')::boolean,false)"
            ).fetchall():
                job = json.loads(raw[0])
                self.repo.update_job(
                    job["job_id"],
                    {
                        "state": "completed",
                        "stage": "completed",
                        "code": "music_quality_review_paused",
                        "quality_review": False,
                    },
                    db=db,
                )
        return {"settings": updated, "playlists_updated": changed}

    def key(self, job, db):
        recording = self.music.library.catalog.resolve(job["recording_id"], db)
        return digest(
            {"recording": recording, "directory": job["destination_directory"]}
        ), recording

    def record(self, job, db=None):
        if db is None:
            with self.repo.transaction() as db:
                return self.record(job, db)
        if job.get("state") != "completed" or not job.get("audio_routing"):
            return job
        now = self.repo.clock()
        deferred = job["audio_routing"].get("deferred", [])
        incomplete = (
            bool(deferred)
            or job["audio_routing"].get("complete") is False
            or any(
                code
                in {
                    "music_upstream_unavailable",
                    "music_download_unavailable",
                    "music_upstream_rejected",
                    "music_rate_limited",
                }
                for provider in job["audio_routing"].get("providers", [])
                for code in provider.get("failures", [])
            )
        )
        current = db.execute(
            'SELECT state,generation FROM jobs WHERE id=%s', (job["job_id"],)
        ).fetchone()
        if (
            current is None
            or current["state"] != "completed"
            or current["generation"] != job["generation"]
        ):
            return
        key, recording = self.key(job, db)
        prior = self.repo._document(db, CHECK, key) or {}
        next_check = (
            max(now + 60, min(row["retry_at"] for row in deferred)) + phase(key, 300)
            if deferred
            else now + (86400 if incomplete else WEEK)
        )
        outcome = (
            "partial"
            if incomplete
            else "upgraded"
            if job.get("quality_review") and not job.get("audio_reused")
            else "unchanged"
            if job.get("audio_reused")
            else "downloaded"
        )
        self.repo.put(
            CHECK,
            key,
            {
                **prior,
                "recording_id": recording,
                "job_id": job["job_id"],
                "title": job["title"],
                "checked_at": now,
                "next_check_at": next_check,
                "pending_channels": deferred,
                "outcome": outcome,
                "code": "music_quality_check_partial" if incomplete else "ready",
                "quality": job.get("audio_quality"),
                "upgrades": prior.get("upgrades", 0) + (outcome == "upgraded"),
            },
            db=db,
        )
        return self.repo.update_job(
            job["job_id"], {"quality_review": False, "quality_catchup": False}, db=db
        )

    def failed(self, job, code):
        with self.repo.transaction() as db:
            key, recording = self.key(job, db)
            prior = self.repo._document(db, CHECK, key) or {}
            self.repo.put(
                CHECK,
                key,
                {
                    **prior,
                    "recording_id": recording,
                    "job_id": job["job_id"],
                    "title": job["title"],
                    "checked_at": self.repo.clock(),
                    "next_check_at": self.repo.clock() + 86400,
                    "outcome": "unavailable",
                    "code": code,
                },
                db=db,
            )

    def plan(self):
        now = self.repo.clock()
        with self.repo.transaction() as db:
            settings = self.settings(db)
            pending = db.execute(
                "SELECT 1 FROM documents WHERE kind=%s AND jsonb_array_length(body::jsonb -> 'pending_channels')>0 LIMIT 1",
                (CHECK,),
            ).fetchone()
            if not settings["enabled"] and not pending:
                return {"state": "paused"}
            foreground = db.execute(
                'SELECT 1 FROM jobs WHERE '
                + VISIBLE
                + " AND state IN ('queued','downloading','validating','publishing','retry_wait') AND NOT coalesce((body::jsonb #>> '{quality_review}')::boolean,false) LIMIT 1"
            ).fetchone()
            reviewing = db.execute(
                'SELECT 1 FROM jobs WHERE '
                + VISIBLE
                + " AND state IN ('queued','downloading','validating','publishing','retry_wait') AND (body::jsonb #>> '{quality_review}')::boolean IS TRUE LIMIT 1"
            ).fetchone()
            if reviewing:
                return {"state": "waiting_downloads" if foreground else "reviewing"}
            playlists = {
                row["playlist_id"]: row
                for raw in db.execute("SELECT body FROM documents WHERE kind='music_playlist'")
                if (row := json.loads(raw[0]))["state"] == "active" and row.get("auto_download")
            }
            members = {key: set(row["entries"]) for key, row in playlists.items()}
            jobs = [
                json.loads(raw[0])
                for raw in db.execute(
                    'SELECT body FROM jobs WHERE '
                    + VISIBLE
                    + " AND state='completed' AND (body::jsonb #>> '{media_kind}')='audio' AND (body::jsonb #>> '{audio_operation}') IS NULL ORDER BY id"
                ).fetchall()
            ]
            requests = self.repo._documents(
                db, "music_request", [key for job in jobs for key in job.get("request_ids", [])]
            )
            jobs = [
                job
                for job in jobs
                if any(
                    (request := requests.get(key))
                    and request["source_id"] in members.get(request["playlist_id"], ())
                    for key in job.get("request_ids", [])
                )
            ]
            reads = self.music.library.catalog.reads(
                db, recordings=[job["recording_id"] for job in jobs]
            )
            candidates = [
                (
                    digest(
                        {
                            "recording": reads.resolve(job["recording_id"]),
                            "directory": job["destination_directory"],
                        }
                    ),
                    job,
                )
                for job in jobs
            ]
            checks = self.repo._documents(db, CHECK, [key for key, _ in candidates])
            due, seen = [], set()
            for key, job in candidates:
                recording = reads.resolve(job["recording_id"])
                if key in seen:
                    continue
                seen.add(key)
                artifact = reads.file(recording)
                if (
                    not artifact
                    or artifact.get("missing")
                    or artifact["directory"] != job["destination_directory"]
                ):
                    continue
                row = checks.get(key)
                if row is None:
                    if not settings["enabled"]:
                        continue
                    row = self.repo.put(
                        CHECK,
                        key,
                        {
                            "recording_id": recording,
                            "job_id": job["job_id"],
                            "title": job["title"],
                            "checked_at": None,
                            "next_check_at": settings["enabled_at"] + phase(key, WEEK),
                            "outcome": "pending",
                            "code": "ready",
                        },
                        db=db,
                    )
                catchup = bool(row.get("pending_channels"))
                if row["next_check_at"] <= now and (
                    catchup or settings["enabled"] and not foreground
                ):
                    due.append((not catchup, row["next_check_at"], job))
            if not due:
                return {
                    "state": "waiting_downloads" if foreground else "scheduled",
                    "recordings": len(seen),
                }
            weekly, _, job = min(due, key=lambda item: (item[0], item[1], item[2]["job_id"]))
            self.repo.update_job(
                job["job_id"],
                {
                    "state": "queued",
                    "stage": "queued",
                    "code": "music_quality_check_queued",
                    "quality_review": True,
                    "quality_catchup": not weekly,
                    "attempts": 0,
                    "cancel_requested": False,
                },
                db=db,
            )
            return {"state": "reviewing", "job_id": job["job_id"]}

    def view(self):
        rows = self.repo.list(CHECK)
        settings = self.settings()
        now = self.repo.clock()
        return {
            "settings": settings,
            "counts": {
                "tracked": len(rows),
                "checked": sum(row.get("checked_at") is not None for row in rows),
                "due": sum(row["next_check_at"] <= now for row in rows),
                "upgrades": sum(row.get("upgrades", 0) for row in rows),
            },
            "recent": sorted(rows, key=lambda row: row.get("checked_at") or 0, reverse=True)[:5],
            "requests": self.music.requests.view(),
        }

    async def pump(self):
        await self.music.media.local(self.plan)
