"""Capability references and scope; bytes are owned by runtime FileStore."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from uuid import uuid4
from tianshu.types import ArtifactRef, MediaRef, SourceRef, Coverage
from tianshu.runtime.files import FileStore
from tianshu.runtime.serialization import wire
from .contracts import canonical, Fault, digest


class StorageError(ValueError):
    pass


class Artifacts:
    def __init__(self, database, files, authority, sources=None):
        self.database, self.files, self.authority, self.sources = (
            database,
            files,
            authority,
            sources,
        )

    async def put(self, context, raw, mime_type, *, source_refs=(), coverage=None, metadata=None):
        media = await asyncio.to_thread(self.files.put_bytes, raw, media_type=mime_type)
        return await self.record(
            context, media, source_refs=source_refs, coverage=coverage, metadata=metadata
        )

    async def record(self, context, media, *, source_refs=(), coverage=None, metadata=None):
        await self.authority.check(context, tuple(source_refs))
        identity = digest(
            [
                context.principal_id,
                context.actor_id,
                context.scope_ids,
                context.audience,
                media.sha256,
                wire(source_refs),
                metadata or {},
            ]
        )
        media = replace(media, media_id="artifact:" + identity)
        artifact = ArtifactRef(identity, media, tuple(source_refs), coverage, metadata or {})
        async with self.database.transaction() as conn:
            from .withdrawal import lock_sources

            await lock_sources(conn, source_refs)
            await self.authority.check(context, tuple(source_refs))
            await conn.execute(
                "INSERT INTO capabilities.artifacts(id,media_id,principal_id,actor_id,scopes,audience,body) VALUES(%s,%s,%s,%s,%s,%s,%s::jsonb) ON CONFLICT(id) DO NOTHING",
                (
                    artifact.artifact_id,
                    media.media_id,
                    context.principal_id,
                    context.actor_id,
                    list(context.scope_ids),
                    list(context.audience),
                    canonical(wire(artifact)),
                ),
            )
        return artifact

    async def reference(self, context, media_id):
        await self.authority.check(context)
        async with self.database.transaction(read_only=True) as conn:
            row = await (
                await conn.execute(
                    "SELECT body FROM capabilities.artifacts WHERE media_id=%s AND scopes<@%s AND audience<@%s AND ((actor_id IS NULL AND principal_id=%s) OR (actor_id IS NOT NULL AND actor_id=%s)) ORDER BY created_at DESC LIMIT 1",
                    (
                        media_id,
                        list(context.scope_ids),
                        list(context.audience),
                        context.principal_id,
                        context.actor_id,
                    ),
                )
            ).fetchone()
        if row is None:
            raise Fault("not_found", 404)
        from .serialization import artifact_from

        artifact = artifact_from(row["body"])
        await self.authority.check(context, artifact.source_refs)
        return artifact

    async def get(self, context, media_id):
        artifact = await self.reference(context, media_id)
        raw = await asyncio.to_thread(self.files.read_bytes, artifact.media)
        await self.authority.check(context, artifact.source_refs)
        return raw, artifact.media.mime_type

    async def resolve(self, context, media):
        if media.media_id.startswith("artifact:"):
            return await self.get(context, media.media_id)
        if self.sources is None:
            raise Fault("source_resolver_unavailable", 503)
        return await self.sources.resolve(context, media)

    @asynccontextmanager
    async def open_media(self, context, media):
        if media.media_id.startswith("artifact:"):
            artifact = await self.reference(context, media.media_id)
            manager = self.files.open_reader(artifact.media)
            from .local_work import settled

            enter = asyncio.create_task(asyncio.to_thread(manager.__enter__))
            try:
                reader = await asyncio.shield(enter)
            except asyncio.CancelledError:
                await asyncio.shield(enter)
                await settled(asyncio.to_thread, manager.__exit__, None, None, None)
                raise
            try:
                yield reader
            finally:
                await settled(asyncio.to_thread, manager.__exit__, None, None, None)
            await self.authority.check(context, artifact.source_refs)
        else:
            if self.sources is None:
                raise Fault("source_resolver_unavailable", 503)
            async with self.sources.open_media(context, media) as reader:
                yield reader

    async def ingest(self, context, raw, mime_type, *, locator):
        media = await asyncio.to_thread(self.files.put_bytes, raw, media_type=mime_type)
        from tianshu.types import OriginKind

        source = await self.sources.register_media(
            context, media, locator=locator, origin_kind=OriginKind.HUMAN
        )
        return await self.record(
            context, media, source_refs=(source,), metadata={"kind": "uploaded_original", **locator}
        )
