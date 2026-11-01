"""One supervised book-audio worker, independent of video and music download capacity."""

import asyncio
from datetime import datetime, timezone

from ....contracts import Fault, digest, require
from .config import profile_id
from .files import AudioFiles
from .ledger import Ledger
from .segments import segments


def public_job(row):
    return {
        key: row.get(key)
        for key in (
            "id",
            "chapter_id",
            "title",
            "order",
            "profile_id",
            "state",
            "code",
            "completed_parts",
            "content_hash",
            "artifact",
            "revision",
            "created_at",
        )
    }


class BookAudio:
    def __init__(self, books):
        self.books, self.repo, self.local = books, books.repo, books.media.local
        self.files = AudioFiles(books)
        self.ledger = Ledger(self)
        self.wake = asyncio.Event()
        self.lock = asyncio.Lock()

    async def settings(self):
        speech = self.books.media.capabilities.speech
        return {
            "voice_profiles": await speech.config.list("voice_profile"),
            "connections": await speech.config.list("speech_connection"),
            "execution_owner": "speech.synthesize",
        }

    async def view(self, book_id):
        await self.local(self.books.catalog.book, book_id)
        rows = await self.local(self.repo.list, "book_audio_job")
        return {
            "jobs": sorted(
                [public_job(r) for r in rows if r["book_id"] == book_id],
                key=lambda r: (r["order"], r["created_at"]),
            ),
            "plan": await self.local(self.repo.get, "book_audio_plan", book_id),
            **await self.settings(),
        }

    async def prepare(self, body):
        speech = self.books.media.capabilities.speech
        selected = await speech.config.get("voice_profile", body.get("speech_profile_id"))
        require(selected is not None, "voice_profile_required", 409)
        require(
            body.get("speech_profile_revision", selected["revision"]) == selected["revision"],
            "profile_revision_changed",
            409,
        )
        connection = await speech.config.get("speech_connection", selected["connection_id"])
        require(
            connection and connection.get("enabled") and connection.get("configured"),
            "speech_not_configured",
            503,
        )
        selected = {
            **selected,
            "connection_revision": connection["revision"],
            "provider": connection["transport"],
        }
        rows, missing = await self.local(
            self.ledger.prepare, body.get("book_id"), selected, body.get("chapter_ids")
        )
        estimate = await self.local(self.ledger.estimate, rows, missing)
        return selected, rows, {**estimate, "profile": selected, "profile_id": profile_id(selected)}

    async def run_once(self):
        async with self.lock:
            job = await self.local(self.ledger.claim)
            if not job:
                return False
            try:
                cached = await self.local(self.files.read, job["root"], job["key"])
                if cached:
                    artifact = await self.local(self.files.link, job, cached)
                else:
                    pieces = []
                    generated = False
                    for index, part in enumerate(segments(job["text"])):
                        plan = await self.local(self.repo.get, "book_audio_plan", job["book_id"])
                        if plan and plan.get("paused") and plan["profile_id"] == job["profile_id"]:
                            await self.local(self.ledger.update, job, state="queued", code="paused")
                            return True
                        key = digest({"part": part["text"], "profile": job["profile"]})
                        cached = await self.local(self.files.read, job["root"], key)
                        if cached is None:
                            if generated:
                                # Published fragment caches and completed_parts
                                # belong to this original chapter job. Yield its
                                # lease before another paid fragment; the next
                                # durable wake reuses those same Action keys.
                                await self.local(self.ledger.update, job,
                                    state="queued", code="queued", lease=0,
                                    next_at=self.repo.clock())
                                return True
                            from tianshu.types import Action, OriginKind, SourceRef
                            from tianshu.runtime.serialization import wire
                            from ....serialization import context_from, artifact_from

                            service = self.books.media.capabilities
                            context = context_from(job["context"])
                            selected = await service.configuration.get(
                                "voice_profile", job["profile"]["id"]
                            )
                            require(
                                selected and selected["revision"] == job["profile"]["revision"],
                                "voice_profile_changed",
                                409,
                            )
                            identity = "book-speech:" + digest(
                                [job["id"], key, job.get("generation", 0)]
                            )
                            action = Action(
                                identity,
                                identity,
                                "speech.synthesize",
                                context.actor_id,
                                context,
                                {
                                    "profile_id": selected["id"],
                                    "profile_revision": selected["revision"],
                                    "text": part["text"],
                                },
                                OriginKind.TIMER,
                                source_refs=tuple(
                                    SourceRef(**x) for x in job.get("source_refs", [])
                                ),
                            )
                            result = wire(await service.actions.invoke(action))
                            require(
                                result["execution"] == "succeeded",
                                "book_audio_unknown"
                                if result["execution"] == "unknown"
                                else result.get("error_code") or "book_audio_failed",
                                409,
                            )
                            produced = [artifact_from(a) for a in result["artifacts"]]
                            require(len(produced) == 1, "book_audio_multiple_clips", 409)
                            raw, _ = await service.artifacts.get(
                                context, produced[0].media.media_id
                            )
                            cached = await self.local(
                                self.files.publish,
                                job["root"],
                                key,
                                part["text"],
                                raw,
                                result["result"].get("alignment", []),
                            )
                            generated = True
                        pieces.append((cached, part))
                        await self.local(
                            self.ledger.update,
                            job,
                            completed_parts=index + 1,
                            lease=self.repo.clock() + 240,
                        )
                    artifact = await self.files.assemble(job, pieces)
                await self.local(
                    self.ledger.update,
                    job,
                    state="ready",
                    code="ready",
                    artifact=artifact,
                    lease=0,
                    completed_at=self.repo.clock(),
                )
            except asyncio.CancelledError:
                raise
            except Exception as error:
                code = error.code if isinstance(error, Fault) else "book_audio_unknown"
                waiting = code in {
                    "book_audio_budget",
                    "book_audio_inflight",
                    "book_audio_rate_limited",
                }
                unknown = code in {
                    "book_audio_unknown",
                    "book_audio_remote_unknown",
                    "book_audio_response_invalid",
                }
                await self.local(
                    self.ledger.update,
                    job,
                    state="waiting" if waiting else "unknown" if unknown else "failed",
                    code=code,
                    next_at=self.repo.clock() + (3600 if code == "book_audio_budget" else 120),
                    lease=0,
                )
            return True

    async def reconcile(self):
        for plan in await self.local(self.repo.list, "book_audio_plan"):
            if not plan["auto_new"] or plan.get("paused"):
                continue
            # A checkpoint includes all chapters already present when auto-follow was enabled,
            # even those not selected for initial generation.
            directory = await self.local(self.books.catalog.directory, plan["book_id"])
            initial = set(plan["initial_chapters"])
            added = [c["chapter_id"] for c in directory if c["chapter_id"] not in initial]
            if not added:
                continue
            try:
                rows, _ = await self.local(
                    self.ledger.prepare, plan["book_id"], plan["profile"], added
                )
                rows = [
                    {**r, "context": plan["context"], "source_refs": plan.get("source_refs", [])}
                    for r in rows
                ]
            except Fault as error:
                # A disconnected book volume must not stop unrelated media loops.
                await self.local(
                    self.repo.put,
                    "book_audio_plan",
                    plan["book_id"],
                    {**plan, "code": error.code},
                    plan["revision"],
                )
                continue

            def enqueue():
                with self.repo.transaction() as db:
                    current = self.repo._document(db, "book_audio_plan", plan["book_id"])
                    if current and current["revision"] == plan["revision"]:
                        self.ledger.enqueue(rows, db)

            await self.local(enqueue)

    async def loop(self):
        await self.reconcile()
        await self.run_once()

    async def manifest(self, job_id):
        job = await self.local(self.repo.get, "book_audio_job", job_id)
        require(job and job["state"] == "ready", "book_audio_not_ready", 409)
        manifest = await self.local(self.files.read, job["root"], job["key"])
        if manifest is None:
            await self.local(self.missing, job)
        require(manifest, "book_audio_file_missing", 409)
        return {
            **manifest,
            "job_id": job_id,
            "url": "/api/v1/books/files/" + job["book_id"] + "/audio-" + job_id,
        }

    async def file(self, book_id, job_id):
        job = await self.local(self.repo.get, "book_audio_job", job_id)
        require(job and job["book_id"] == book_id and job["state"] == "ready", "not_found", 404)
        manifest = await self.local(self.files.read, job["root"], job["key"])
        if manifest is None:
            await self.local(self.missing, job)
        require(manifest, "book_audio_file_missing", 409)
        return (
            self.files.cache(job["root"], job["key"]) / "audio.mp3",
            job["title"] + ".mp3",
            "audio/mpeg",
        )

    def missing(self, job):
        with self.repo.transaction() as db:
            current = self.repo._document(db, "book_audio_job", job["id"])
            if current and current["state"] == "ready" and current["key"] == job["key"]:
                self.repo.put(
                    "book_audio_job",
                    job["id"],
                    {**current, "state": "failed", "code": "book_audio_file_missing"},
                    db=db,
                )
