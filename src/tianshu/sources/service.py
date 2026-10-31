"""Short native-PG source transactions and batched dependency authorization."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime
import json

from psycopg.types.json import Jsonb

from tianshu.models.ledger import purge_source_results_many
from tianshu.runtime.api import CommandResult
from tianshu.runtime.commands import body_hash
from tianshu.runtime.errors import AppError
from tianshu.runtime.jobs import JobSpec
from tianshu.runtime.serialization import wire
from tianshu.runtime.cancellation import finish_cleanup
from tianshu.types import Coverage, MediaRef, OriginKind, ServiceClass, SourceRef


class SourceService:
    def __init__(self, database, identity, files, commands, jobs):
        self.database, self.identity, self.files = database, identity, files
        self.commands, self.jobs = commands, jobs
        self.dependants = []

    def register_dependant(self, callback):
        """An owner's callback invalidates only its own selected dependent state."""
        if callback in self.dependants:
            raise ValueError("duplicate_source_dependant")
        self.dependants.append(callback)

    async def check(self, context, source_refs=()):
        results = await self.check_many(context, (tuple(source_refs),))
        if not results[0]:
            raise AppError("source_unavailable", status=403)

    async def check_many(self, context, refs_groups):
        async with self.database.transaction(control=True, read_only=True) as conn:
            return await self.check_many_on(conn, context, refs_groups)

    async def validate_persistence(self, conn, context, source_refs):
        """Lock only selected roots/supports until result commit; forgetting locks these first too."""
        await self._lock_selected(conn, source_refs)
        try:
            return (await self.check_many_on(conn, context, (tuple(source_refs),)))[0]
        except AppError as exc:
            if exc.status in {401, 403, 409}:
                return False
            raise

    async def _lock_selected(
        self, conn, source_refs, *, extra=(), exclusive=False, extra_aliases=()
    ):
        if source_refs or extra or extra_aliases:
            from .archives import lock_selected

            await lock_selected(
                conn, source_refs, extra=extra, exclusive=exclusive, aliases=extra_aliases
            )

    async def register_archive(self, context, *, mapping_id, channel, records, explicit_request):
        from .archives import register_archive

        return await register_archive(
            self,
            context,
            mapping_id=mapping_id,
            channel=channel,
            records=records,
            explicit_request=explicit_request,
        )

    async def check_many_on(self, conn, context, refs_groups):
        from psycopg.errors import ProgramLimitExceeded

        await self.identity.check_context(context, connection=conn)
        keys = {(ref.source_id, ref.revision) for group in refs_groups for ref in group}
        if len(keys) > 2048 or len(refs_groups) > 1024:
            raise AppError("source_batch_too_large")
        if any(
            type(revision) is not int or revision < 1 or not isinstance(key, str)
            for key, revision in keys
        ):
            raise AppError("invalid_source_reference")
        try:
            row = await (
                await conn.execute(
                    "SELECT sources.check_groups(%s,%s::text[]) AS allowed",
                    (Jsonb(wire(refs_groups)), list(context.scope_ids)),
                )
            ).fetchone()
        except ProgramLimitExceeded:
            raise AppError("source_dependency_budget_exceeded", status=503) from None
        return tuple(row["allowed"])

    async def describe_for_write_on(self, conn, context, source_refs):
        """Verified descriptions with source locks held until the caller's write commits."""
        if not await self.validate_persistence(conn, context, tuple(source_refs)):
            raise AppError("source_unavailable", status=403)
        if not source_refs:
            return ()
        rows = await (
            await conn.execute(
                "SELECT source_id,revision,scope_ids,evidence_unit,origin_kind,occurred_at "
                "FROM sources.versions WHERE (source_id,revision) IN (SELECT * FROM unnest(%s::text[],%s::bigint[]))",
                ([ref.source_id for ref in source_refs], [ref.revision for ref in source_refs]),
            )
        ).fetchall()
        return tuple(rows)

    async def record_event(
        self, context, *, producer, event_id, revision, occurred_at, parts, origin_kind
    ):
        if occurred_at.tzinfo is None or type(revision) is not int or revision < 1:
            raise AppError("invalid_source_event")
        original_parts = parts
        parts = wire(parts)
        if len(parts) > 256:
            raise AppError("source_parts_too_many")
        refs = []
        async with self.database.transaction() as conn:
            intake, epochs = await self.identity.intake_scope(context, connection=conn)
            write_context = replace(context, scope_ids=intake, audience=intake)
            from .archives import event_alias, bind_event

            native_alias = await event_alias(self, conn, context, producer, event_id)
            if native_alias:
                await self._lock_selected(conn, (), exclusive=True, extra_aliases=(native_alias,))
                if await (
                    await conn.execute(
                        "SELECT 1 FROM sources.alias_suppression WHERE alias_key=%s",
                        (native_alias,),
                    )
                ).fetchone():
                    raise AppError("source_suppressed", status=409)
            previous_roots = await (
                await conn.execute(
                    "SELECT o.source_id,o.head_revision FROM sources.originals o JOIN "
                    "sources.versions v ON v.source_id=o.source_id AND v.revision=o.head_revision WHERE o.owner_id=%s "
                    "AND o.actor_id IS NOT DISTINCT FROM %s AND v.evidence_unit=%s ORDER BY o.source_id FOR UPDATE OF o",
                    (context.principal_id, context.actor_id, f"{producer}:{event_id}"),
                )
            ).fetchall()
            await purge_source_results_many(
                conn,
                tuple(
                    SourceRef(row["source_id"], row["head_revision"], {})
                    for row in previous_roots
                    if row["head_revision"] < revision
                ),
            )
            for position, part in enumerate(parts):
                if not isinstance(part, dict):
                    raise AppError("invalid_source_part")
                text = part.get("text")
                media = part.get("media")
                if media:
                    original = original_parts[position]
                    candidate = (
                        original.get("media")
                        if isinstance(original, dict)
                        else getattr(original, "media", None)
                    )
                    if not self.files.was_written(candidate):
                        media_id = media.get("media_id", "")
                        pieces = media_id.split(":")
                        if len(pieces) != 3 or pieces[0] != "source" or not pieces[2].isdigit():
                            raise AppError("source_media_not_authorized", status=403)
                        ref = SourceRef(pieces[1], int(pieces[2]), {})
                        if not (await self.check_many_on(conn, context, ((ref,),)))[0]:
                            raise AppError("source_media_not_authorized", status=403)
                        stored = await (
                            await conn.execute(
                                "SELECT media FROM sources.versions WHERE source_id=%s AND revision=%s",
                                (ref.source_id, ref.revision),
                            )
                        ).fetchone()
                        if not stored["media"] or stored["media"].get("sha256") != media.get(
                            "sha256"
                        ):
                            raise AppError("source_media_mismatch", status=403)
                if text is None and media is None:
                    # Preserve non-text references as bounded structured source content.
                    text = json.dumps(part, ensure_ascii=False)
                locator = {"producer": producer, "event_id": event_id, "part": position}
                key = body_hash(
                    {
                        "principal": context.principal_id,
                        "actor": context.actor_id,
                        "locator": locator,
                    }
                )
                metadata = part.get("metadata") or {}
                if (
                    not isinstance(metadata, dict)
                    or len(json.dumps(metadata, ensure_ascii=False)) > 16384
                ):
                    raise AppError("invalid_source_metadata")
                locator = {**locator, "part_kind": part.get("kind", "text"), "metadata": metadata}
                part_origin = (
                    OriginKind.OBSERVATION
                    if part.get("kind") in {"quote", "forward", "reply_quote", "reply", "reference"}
                    else origin_kind
                )
                refs.append(
                    await self._register(
                        conn,
                        write_context,
                        epochs,
                        key,
                        revision,
                        locator,
                        occurred_at,
                        part_origin,
                        text,
                        media,
                        f"{producer}:{event_id}",
                        Coverage(True),
                    )
                )
            removed = await (
                await conn.execute(
                    "SELECT o.source_id,o.head_revision FROM sources.originals o JOIN "
                    "sources.versions v ON v.source_id=o.source_id AND v.revision=o.head_revision WHERE o.owner_id=%s "
                    "AND o.actor_id IS NOT DISTINCT FROM %s AND v.evidence_unit=%s AND o.head_revision<%s "
                    "AND o.state='active' FOR UPDATE OF o",
                    (context.principal_id, context.actor_id, f"{producer}:{event_id}", revision),
                )
            ).fetchall()
            for row in removed:
                await conn.execute(
                    "UPDATE sources.originals SET state='retracted' WHERE source_id=%s",
                    (row["source_id"],),
                )
                await self._changed(
                    conn, SourceRef(row["source_id"], row["head_revision"], {}), "corrected"
                )
            await bind_event(conn, native_alias, tuple(refs))
        return tuple(refs)

    async def _register(
        self,
        conn,
        context,
        epochs,
        key,
        revision,
        locator,
        occurred_at,
        origin_kind,
        content,
        media,
        evidence_unit,
        coverage,
        support_groups=(),
        *,
        disclosure=False,
        support_context=None,
    ):
        if not context.scope_ids:
            raise AppError("source_scope_required", status=403)
        source_id = "source_" + key[:48]
        dependencies = tuple(
            SourceRef(**ref) if isinstance(ref, dict) else ref
            for group in support_groups
            for ref in group
        )
        await self._lock_selected(conn, dependencies, extra=(source_id,), exclusive=True)
        await conn.execute(
            "INSERT INTO sources.originals(source_id,source_key,owner_id,person_id,actor_id,head_revision) "
            "VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
            (source_id, key, context.principal_id, context.person_id, context.actor_id, revision),
        )
        original = await (
            await conn.execute(
                "SELECT * FROM sources.originals WHERE source_id=%s FOR UPDATE", (source_id,)
            )
        ).fetchone()
        if (
            original["state"] == "forgotten"
            or await (
                await conn.execute("SELECT 1 FROM sources.suppression WHERE source_key=%s", (key,))
            ).fetchone()
        ):
            raise AppError("source_suppressed", status=409)
        old = await (
            await conn.execute(
                "SELECT content,media,scope_ids,origin_kind FROM sources.versions WHERE "
                "source_id=%s AND revision=%s",
                (source_id, revision),
            )
        ).fetchone()
        if old:
            if (
                old["content"] != content
                or old["media"] != wire(media)
                or old["origin_kind"] != str(origin_kind)
                or set(old["scope_ids"]) != set(context.scope_ids)
            ):
                raise AppError("source_revision_conflict", status=409)
            return SourceRef(source_id, revision, {})
        if revision < original["head_revision"]:
            raise AppError("source_revision_obsolete", status=409)
        if await (
            await conn.execute(
                "SELECT 1 FROM sources.fragment_barriers WHERE source_key=%s AND revision<%s",
                (key, revision),
            )
        ).fetchone():
            raise AppError("source_intake_suppressed", status=409)
        await conn.execute(
            "INSERT INTO sources.versions(source_id,revision,intake_epoch,scope_ids,scope_epochs,audience,origin_kind,"
            "evidence_unit,content,media,locator,coverage,occurred_at,disclosure) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                source_id,
                revision,
                original["intake_epoch"],
                list(context.scope_ids),
                Jsonb(epochs),
                list(context.audience),
                str(origin_kind),
                evidence_unit,
                content,
                Jsonb(wire(media)) if media else None,
                Jsonb(locator),
                Jsonb(wire(coverage)),
                occurred_at,
                disclosure,
            ),
        )
        for index, group in enumerate(support_groups):
            group = tuple(
                SourceRef(**support) if isinstance(support, dict) else support for support in group
            )
            if (
                not group
                or not (
                    await self.check_many_on(conn, support_context or context, (tuple(group),))
                )[0]
            ):
                raise AppError("source_support_unavailable", status=403)
            for support in group:
                if support.source_id == source_id:
                    raise AppError("source_support_cycle")
                await conn.execute(
                    "INSERT INTO sources.supports(source_id,revision,group_id,support_source_id,support_revision,support_locator,support_key) "
                    "VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                    (
                        source_id,
                        revision,
                        str(index),
                        support.source_id,
                        support.revision,
                        Jsonb(wire(support.locator)),
                        body_hash(support),
                    ),
                )
        await conn.execute(
            "UPDATE sources.originals SET head_revision=%s WHERE source_id=%s",
            (revision, source_id),
        )
        if original["head_revision"] < revision:
            await self._changed(
                conn, SourceRef(source_id, original["head_revision"], {}), "corrected"
            )
        return SourceRef(source_id, revision, {})

    async def disclose(
        self,
        context,
        *,
        producer,
        event_id,
        revision,
        parts,
        source_refs,
        reader_ids=(),
        conversation_ids=(),
        connection=None,
    ):
        parts = wire(parts)
        if (
            len(parts) != 1
            or parts[0].get("kind", "text") != "text"
            or not isinstance(parts[0].get("text"), str)
        ):
            raise AppError("publication_text_required")
        if not 1 <= len(parts[0]["text"]) <= 1000000 or type(revision) is not int or revision < 1:
            raise AppError("invalid_publication")

        async def publish(conn):
            locator = {"producer": producer, "event_id": event_id, "kind": "publication"}
            key = body_hash(
                {"principal": context.principal_id, "actor": context.actor_id, "locator": locator}
            )
            await self._lock_selected(
                conn, tuple(source_refs), extra=("source_" + key[:48],), exclusive=True
            )
            if not await self.validate_persistence(conn, context, tuple(source_refs)):
                raise AppError("source_unavailable", status=403)
            stable_id = producer + ":" + event_id
            scopes, epochs = await self.identity.publication_scope(
                context, stable_id, reader_ids, conversation_ids, connection=conn
            )
            target = replace(context, scope_ids=scopes, audience=scopes)
            return await self._register(
                conn,
                target,
                epochs,
                key,
                revision,
                locator,
                datetime.now(UTC),
                OriginKind.ACTOR,
                parts[0]["text"],
                None,
                "publication:" + stable_id,
                Coverage(True),
                (tuple(source_refs),),
                disclosure=True,
                support_context=context,
            )

        if connection is not None:
            return await publish(connection)
        async with self.database.transaction() as conn:
            return await publish(conn)

    async def revoke_projection(self, conn, context, projection_id, *, source_refs=()):
        locator = {"kind": "ordinary_interest", "projection_id": projection_id}
        key = body_hash(
            {"principal": context.principal_id, "actor": context.actor_id, "locator": locator}
        )
        # Select all source locks before a domain callback can lock a claim or matter.
        await self._lock_selected(
            conn, tuple(source_refs), extra=("source_" + key[:48],), exclusive=True
        )
        row = await (
            await conn.execute(
                "UPDATE sources.originals SET state='retracted',intake_epoch=intake_epoch+1 "
                "WHERE source_key=%s AND owner_id=%s AND state='active' RETURNING source_id,head_revision",
                (key, context.principal_id),
            )
        ).fetchone()
        if row:
            await self._changed(
                conn, SourceRef(row["source_id"], row["head_revision"], {}), "corrected"
            )

    async def publish_projection(
        self,
        context,
        *,
        projection_id,
        content,
        source_refs,
        kind="ordinary_interest",
        connection=None,
    ):
        if (
            kind != "ordinary_interest"
            or not source_refs
            or not isinstance(content, str)
            or not 1 <= len(content) <= 4000
        ):
            raise AppError("invalid_shared_projection")
        if "memory.share" not in context.permissions and "*" not in context.permissions:
            raise AppError("forbidden", status=403)

        async def publish(conn):
            locator = {"kind": kind, "projection_id": projection_id}
            key = body_hash(
                {"principal": context.principal_id, "actor": context.actor_id, "locator": locator}
            )
            await self._lock_selected(
                conn, tuple(source_refs), extra=("source_" + key[:48],), exclusive=True
            )
            if not await self.validate_persistence(conn, context, tuple(source_refs)):
                raise AppError("source_unavailable", status=403)
            scope_id = await self.identity.public_scope(context, connection=conn)
            epochs = await self.identity.support_epochs((scope_id,), connection=conn)
            target = replace(context, scope_ids=(scope_id,), audience=(scope_id,))
            return await self._register(
                conn,
                target,
                epochs,
                key,
                1,
                locator,
                datetime.now(UTC),
                OriginKind.SYSTEM,
                content,
                None,
                "projection:" + projection_id,
                Coverage(True),
                (tuple(source_refs),),
                disclosure=True,
                support_context=context,
            )

        if connection is not None:
            return await publish(connection)
        async with self.database.transaction() as conn:
            return await publish(conn)

    async def capture(self, context, *, locator, raw, mime_type):
        await self.check(context)
        media = await asyncio.to_thread(self.files.put_bytes, raw, media_type=mime_type)
        text = raw.decode("utf-8") if mime_type.startswith("text/") else None
        return await self._register_media(context, media, locator, OriginKind.TOOL_RESULT, text)

    async def register_media(self, context, media, *, locator, origin_kind=OriginKind.HUMAN):
        return await self._register_media(context, media, locator, origin_kind, None)

    async def _register_media(self, context, media, locator, origin_kind, content):
        await self.check(context)
        if not self.files.was_written(media):
            raise AppError("media_intake_not_owned", status=403)

        def verify():
            with self.files.open_reader(media):
                pass

        await asyncio.to_thread(verify)
        async with self.database.transaction() as conn:
            intake, epochs = await self.identity.intake_scope(context, connection=conn)
            write_context = replace(context, scope_ids=intake, audience=intake)
            key = body_hash(
                {"principal": context.principal_id, "actor": context.actor_id, "locator": locator}
            )
            revision = locator.get("revision", 1)
            return await self._register(
                conn,
                write_context,
                epochs,
                key,
                revision,
                locator,
                datetime.now(UTC),
                origin_kind,
                content,
                wire(media),
                locator.get("evidence_unit", key),
                Coverage(True),
                locator.get("support_groups", ()),
            )

    async def _media(self, context, media):
        if not media.media_id.startswith("source:"):
            raise AppError("source_media_reference_required", status=403)
        pieces = media.media_id.split(":")
        if len(pieces) != 3 or not pieces[2].isdigit():
            raise AppError("invalid_source_media")
        source = SourceRef(pieces[1], int(pieces[2]), {})
        async with self.database.transaction(read_only=True) as conn:
            if not (await self.check_many_on(conn, context, ((source,),)))[0]:
                raise AppError("source_unavailable", status=403)
            row = await (
                await conn.execute(
                    "SELECT media FROM sources.versions WHERE source_id=%s AND revision=%s",
                    (source.source_id, source.revision),
                )
            ).fetchone()
        actual = MediaRef(**row["media"])
        if actual.sha256 != media.sha256 or actual.uri != media.uri:
            raise AppError("source_media_mismatch", status=403)
        return source, actual

    @asynccontextmanager
    async def open_media(self, context, media):
        source, actual = await self._media(context, media)
        reader = self.files.open_reader(actual)
        entering = asyncio.create_task(asyncio.to_thread(reader.__enter__))
        try:
            stream = await asyncio.shield(entering)
        except asyncio.CancelledError:
            await finish_cleanup(entering)
            await finish_cleanup(asyncio.to_thread(reader.__exit__, None, None, None))
            raise
        try:
            await self.check(context, (source,))
            yield stream
        finally:
            await finish_cleanup(asyncio.to_thread(reader.__exit__, None, None, None))

    async def resolve(self, context, media):
        source, actual = await self._media(context, media)
        if actual.size_bytes is None or actual.size_bytes > 16 * 1024 * 1024:
            raise AppError("source_requires_streaming", status=413)
        raw = await asyncio.to_thread(self.files.read_bytes, actual)
        await self.check(context, (source,))
        return raw, actual.mime_type

    async def read(self, context, source, requested):
        return (await self.read_many(context, ((source, requested),)))[0]

    async def read_many(self, context, selections):
        if len(selections) > 128:
            raise AppError("source_read_batch_too_large")
        refs = tuple(item[0] for item in selections)
        positions = []
        lengths = []
        for source, requested in selections:
            position = requested.get("position", (source.locator or {}).get("start", 0))
            maximum = min(max(int(requested.get("max_characters", 12000)), 1), 100000)
            if type(position) is not int or position < 0:
                raise AppError("invalid_source_position")
            lower = (source.locator or {}).get("start", 0)
            upper = (source.locator or {}).get("end")
            if position < lower or (upper is not None and position > upper):
                raise AppError("source_position_outside_reference")
            if source.locator and "end" in source.locator:
                maximum = min(maximum, max(source.locator["end"] - position, 0))
            positions.append(position)
            lengths.append(maximum)
        async with self.database.transaction(read_only=True) as conn:
            if not all(await self.check_many_on(conn, context, tuple((ref,) for ref in refs))):
                raise AppError("source_unavailable", status=403)
            rows = await (
                await conn.execute(
                    "SELECT q.ordinal,v.source_id,v.revision,v.media,v.origin_kind,v.occurred_at,v.locator,"
                    "length(v.content) AS total,substring(v.content from q.position+1 for q.maximum) AS content "
                    "FROM unnest(%s::text[],%s::bigint[],%s::integer[],%s::integer[]) WITH ORDINALITY "
                    "q(source_id,revision,position,maximum,ordinal) JOIN sources.versions v USING(source_id,revision) ORDER BY q.ordinal",
                    (
                        [ref.source_id for ref in refs],
                        [ref.revision for ref in refs],
                        positions,
                        lengths,
                    ),
                )
            ).fetchall()
        results = []
        for row, source, position, maximum in zip(rows, refs, positions, lengths):
            total = row["total"]
            if total is not None and position > total:
                raise AppError("invalid_source_position")
            end = min(total, position + maximum) if total is not None else position
            target_end = (
                min(total, (source.locator or {}).get("end", total)) if total is not None else None
            )
            coverage = Coverage(
                total is None
                or (position == (source.locator or {}).get("start", 0) and end >= target_end),
                total,
                ranges=({"start": position, "end": end},) if total is not None else (),
                representation="text" if total is not None else "original",
            )
            media = (
                {**row["media"], "media_id": f"source:{source.source_id}:{source.revision}"}
                if row["media"]
                else None
            )
            results.append(
                {
                    "source_ref": source,
                    "content": row["content"],
                    "representation": coverage.representation,
                    "coverage": coverage,
                    "next_position": end if total is not None and end < target_end else None,
                    "media": media,
                    "origin_kind": row["origin_kind"],
                    "occurred_at": row["occurred_at"],
                    "locator": row["locator"],
                }
            )
        await self.check(context, refs)
        return tuple(results)

    async def _changed(self, conn, source, kind):
        from .archives import invalidate_related

        related = await invalidate_related(conn, source, kind)
        affected = (source, *related)
        await purge_source_results_many(conn, affected)
        for callback in self.dependants:
            await callback(conn, affected, kind)
        for ref in affected:
            change = await (
                await conn.execute(
                    "INSERT INTO sources.changes(source_id,revision,kind) VALUES(%s,%s,%s) RETURNING sequence",
                    (ref.source_id, ref.revision, kind),
                )
            ).fetchone()
            await self.jobs.enqueue(
                conn,
                JobSpec(
                    "sources.propagate",
                    f"source-change:{change['sequence']}",
                    {"source": wire(ref), "kind": kind},
                    ServiceClass.DUE,
                ),
            )

    async def recall_event(
        self, context, producer, event_id, *, revision=None, reason="channel_recall"
    ):
        if (
            context.origin_kind not in {OriginKind.OBSERVATION, OriginKind.TOOL_RESULT}
            or not context.conversation_id
        ):
            raise AppError("verified_channel_event_required", status=403)
        async with self.database.transaction() as conn:
            if not await self.identity.verify_channel_producer(conn, context, producer):
                raise AppError("channel_producer_forbidden", status=403)
            rows = await (
                await conn.execute(
                    "SELECT o.source_id,o.head_revision FROM sources.originals o JOIN sources.versions v "
                    "ON v.source_id=o.source_id AND v.revision=o.head_revision WHERE o.actor_id=%s "
                    "AND v.evidence_unit=%s AND v.scope_ids<@%s::text[] AND o.state='active' "
                    "AND (%s::bigint IS NULL OR o.head_revision=%s) ORDER BY o.source_id",
                    (
                        context.actor_id,
                        producer + ":" + event_id,
                        list(context.scope_ids),
                        revision,
                        revision,
                    ),
                )
            ).fetchall()
            await self._lock_selected(
                conn,
                tuple(SourceRef(row["source_id"], row["head_revision"], {}) for row in rows),
                exclusive=True,
            )
            await purge_source_results_many(
                conn, tuple(SourceRef(row["source_id"], row["head_revision"], {}) for row in rows)
            )
            for row in rows:
                changed = await (
                    await conn.execute(
                        "UPDATE sources.originals SET state='retracted',intake_epoch=intake_epoch+1 "
                        "WHERE source_id=%s AND state='active' RETURNING source_id",
                        (row["source_id"],),
                    )
                ).fetchone()
                if changed:
                    await self._changed(
                        conn,
                        SourceRef(row["source_id"], row["head_revision"], {}),
                        "channel_recall",
                    )

    async def invalidate(self, context, body, *, forgotten=False):
        source = SourceRef(body["source_id"], body["expected_revision"], {})

        async def apply(conn):
            await self.identity.check_context(context, connection=conn)
            await self._lock_selected(conn, (source,), exclusive=True)
            row = await (
                await conn.execute(
                    "SELECT * FROM sources.originals WHERE source_id=%s FOR UPDATE",
                    (source.source_id,),
                )
            ).fetchone()
            if not row or not await self.identity.can_revoke_source(
                conn, context, row["owner_id"], row["person_id"], row["actor_id"]
            ):
                raise AppError("source_forbidden", status=403)
            if row["head_revision"] != source.revision:
                raise AppError("revision_conflict", status=409)
            kind = "forgotten" if forgotten else "retracted"
            await conn.execute(
                "UPDATE sources.originals SET state=%s,intake_epoch=intake_epoch+1 WHERE source_id=%s",
                (kind, source.source_id),
            )
            await conn.execute(
                "UPDATE sources.versions SET state=%s,content=CASE WHEN %s THEN NULL ELSE content END,"
                "media=CASE WHEN %s THEN NULL ELSE media END,locator=CASE WHEN %s THEN '{}'::jsonb ELSE locator END "
                "WHERE source_id=%s",
                (kind, forgotten, forgotten, forgotten, source.source_id),
            )
            if forgotten:
                await conn.execute(
                    "INSERT INTO sources.suppression(source_key,source_id,intake_epoch) VALUES(%s,%s,%s) "
                    "ON CONFLICT DO NOTHING",
                    (row["source_key"], source.source_id, row["intake_epoch"] + 1),
                )
            await self._changed(conn, source, kind)
            return CommandResult(
                body["client_request_id"],
                revision=source.revision,
                data={"state": kind, "source_id": source.source_id},
            )

        return await self.commands.execute(
            context,
            "sources.forget" if forgotten else "sources.retract",
            body["client_request_id"],
            body_hash(body),
            apply,
            on_replay=lambda conn, identity: self._replay_invalidation(conn, context, identity),
        )

    async def _replay_invalidation(self, conn, context, original):
        await self.identity.check_context(context, connection=conn)
        return original

    async def forget_fragment(self, context, body):
        source = SourceRef(body["source_id"], body["expected_revision"], {})
        start, end = body.get("start"), body.get("end")
        if type(start) is not int or type(end) is not int or not 0 <= start < end:
            raise AppError("invalid_source_fragment")

        async def apply(conn):
            await self.identity.check_context(context, connection=conn)
            await self._lock_selected(conn, (source,), exclusive=True)
            row = await (
                await conn.execute(
                    "SELECT o.owner_id,o.person_id,o.actor_id,o.source_key,o.head_revision,length(v.content) AS length FROM sources.originals o "
                    "JOIN sources.versions v ON v.source_id=o.source_id AND v.revision=o.head_revision WHERE o.source_id=%s FOR UPDATE OF o",
                    (source.source_id,),
                )
            ).fetchone()
            if not row or not await self.identity.can_revoke_source(
                conn, context, row["owner_id"], row["person_id"], row["actor_id"]
            ):
                raise AppError("source_forbidden", status=403)
            if (
                row["head_revision"] != source.revision
                or row["length"] is None
                or end > row["length"]
            ):
                raise AppError("source_fragment_changed", status=409)
            await conn.execute(
                "INSERT INTO sources.forgotten_fragments VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                (source.source_id, source.revision, start, end),
            )
            await conn.execute(
                "INSERT INTO sources.fragment_barriers(source_key,revision) VALUES(%s,%s) ON CONFLICT DO NOTHING",
                (row["source_key"], source.revision),
            )
            # Keep offsets stable while removing the actual forgotten text and original-file access.
            await conn.execute(
                "UPDATE sources.versions SET content=substring(content from 1 for %s)||repeat(' ',%s)||"
                "substring(content from %s),media=NULL,locator='{}' WHERE source_id=%s AND revision=%s",
                (start, end - start, end + 1, source.source_id, source.revision),
            )
            await self._changed(
                conn,
                SourceRef(source.source_id, source.revision, {"start": start, "end": end}),
                "fragment_forgotten",
            )
            return CommandResult(
                body["client_request_id"],
                revision=source.revision,
                data={
                    "source_id": source.source_id,
                    "forgotten_range": {"start": start, "end": end},
                },
            )

        return await self.commands.execute(
            context,
            "sources.forget_fragment",
            body["client_request_id"],
            body_hash(body),
            apply,
            on_replay=lambda conn, original: self._replay_invalidation(conn, context, original),
        )

    async def propagate(self, source, kind, cursor=None):
        source = SourceRef(**source)
        async with self.database.transaction() as conn:
            last_id, last_revision = (
                (cursor["source_id"], cursor["revision"]) if cursor else (None, None)
            )
            rows = await (
                await conn.execute(
                    "WITH RECURSIVE affected(source_id,revision) AS ("
                    "SELECT source_id,revision FROM sources.supports WHERE support_source_id=%s AND support_revision=%s "
                    "UNION SELECT d.source_id,d.revision FROM sources.supports d JOIN affected a ON "
                    "a.source_id=d.support_source_id AND a.revision=d.support_revision) "
                    "SELECT source_id,revision FROM affected WHERE (%s::text IS NULL OR (source_id,revision)>(%s,%s)) "
                    "ORDER BY source_id,revision LIMIT 513",
                    (source.source_id, source.revision, last_id, last_id, last_revision),
                )
            ).fetchall()
            candidates = tuple(
                SourceRef(row["source_id"], row["revision"], {}) for row in rows[:512]
            )
            await self._lock_selected(conn, candidates, exclusive=True)
            invalid = await (
                await conn.execute(
                    "SELECT v.source_id,v.revision FROM sources.versions v "
                    "WHERE (source_id,revision) IN(SELECT * FROM unnest(%s::text[],%s::bigint[])) "
                    "AND NOT sources.any_support_usable(jsonb_build_array(jsonb_build_array(jsonb_build_object("
                    "'source_id',v.source_id,'revision',v.revision,'locator','{}'::jsonb))),v.scope_ids)",
                    ([ref.source_id for ref in candidates], [ref.revision for ref in candidates]),
                )
            ).fetchall()
            dependents = tuple(SourceRef(row["source_id"], row["revision"], {}) for row in invalid)
            await purge_source_results_many(conn, dependents)
            if dependents:
                await conn.execute(
                    "UPDATE sources.versions v SET state='unsupported',content=NULL,media=NULL,locator='{}' "
                    "WHERE (source_id,revision) IN(SELECT * FROM unnest(%s::text[],%s::bigint[])) "
                    "AND NOT sources.any_support_usable(jsonb_build_array(jsonb_build_array(jsonb_build_object("
                    "'source_id',v.source_id,'revision',v.revision,'locator','{}'::jsonb))),v.scope_ids)",
                    ([ref.source_id for ref in dependents], [ref.revision for ref in dependents]),
                )
                for callback in self.dependants:
                    await callback(conn, dependents, kind)
            if len(rows) > 512:
                last = {"source_id": rows[511]["source_id"], "revision": rows[511]["revision"]}
                await self.jobs.enqueue(
                    conn,
                    JobSpec(
                        "sources.propagate",
                        f"source-propagation:{source.source_id}:{source.revision}:{kind}:{last_id}:{last['source_id']}:{last['revision']}",
                        {"source": wire(source), "kind": kind, "cursor": last},
                        ServiceClass.DUE,
                    ),
                )
