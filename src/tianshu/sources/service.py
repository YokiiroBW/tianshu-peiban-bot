"""Short native-PG source transactions and batched dependency authorization."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime
import json

from psycopg.types.json import Jsonb

from tianshu.runtime.api import CommandResult
from tianshu.runtime.commands import body_hash
from tianshu.runtime.errors import AppError
from tianshu.runtime.jobs import JobSpec
from tianshu.runtime.recovery_policy import RecoveryWait
from tianshu.runtime.serialization import wire
from tianshu.runtime.cancellation import finish_cleanup, transaction_on
from tianshu.types import Coverage, MediaRef, OriginKind, ServiceClass, SourceRef


def _context_lock_order(context):
    """A stable order for every authority represented by a trusted context."""
    return (
        context.actor_id or "",
        tuple(sorted(context.scope_ids)),
        context.conversation_id or "",
        context.principal_id,
        context.person_id or "",
        context.access_epoch,
        str(context.origin_kind),
        tuple(sorted(context.permissions)),
        tuple(context.scope_ids),
        tuple(context.audience),
        context.conversation_kind or "",
        context.delivery_allowed,
        context.actor_id is not None,
        context.conversation_id is not None,
        context.person_id is not None,
        context.conversation_kind is not None,
    )


class SourceService:
    def __init__(self, database, identity, files, commands, jobs):
        self.database, self.identity, self.files = database, identity, files
        self.commands, self.jobs = commands, jobs
        self.dependants = {}

    def register_dependant(self, callback, *, name=None, partitions=None):
        """Register a transaction-local invalidator, delivered after source commit.

        Both callback and partitioner use the supplied connection; irreversible
        external work must be dispatched separately through durable state.
        """
        name = name or f"{callback.__module__}.{callback.__qualname__}"
        if name in self.dependants:
            raise ValueError("duplicate_source_dependant")
        self.dependants[name] = (callback, partitions)

    async def check(self, context, source_refs=()):
        results = await self.check_many(context, (tuple(source_refs),))
        if not results[0]:
            raise AppError("source_unavailable", status=403)

    async def check_many(self, context, refs_groups):
        from .rbj_budget import SourceBudget

        return await self._check_many(context, refs_groups, SourceBudget())

    async def _check_many(self, context, refs_groups, budget):
        from .proofs import check_many_on, normalize_groups

        groups = normalize_groups(refs_groups)
        refs = tuple(ref for group in groups for ref in group)
        async with self.database.transaction(control=True) as conn:
            await self.identity.lock_context_on(conn, context)
            await self._lock_selected(conn, refs, budget=budget)
            return await check_many_on(self.identity, conn, context, groups, budget=budget)

    async def register_evidence_on(self, connection, context, spec):
        from .evidence import register_on
        return await register_on(self, connection, context, spec)

    async def prove_many_on(self, connection, context, references, *, lock=False):
        from .evidence import prove_many_on
        return await prove_many_on(self, connection, context, references, lock=lock)

    async def prove_many(self, context, references):
        async with self.database.transaction(control=True, read_only=True) as conn:
            return await self.prove_many_on(conn, context, references)

    async def source_status_many(self, context, references):
        async with self.database.transaction(control=True, read_only=True) as conn:
            return await self.source_status_many_on(conn, context, references)

    async def source_status_many_on(self, connection, context, references):
        """Current state without disclosing metadata outside the caller's scopes."""
        from .types import SourceStatus
        refs = tuple(references)
        allowed = await self.check_many_on(connection, context, tuple((ref,) for ref in refs))
        rows = await (await connection.execute(
            "SELECT o.source_id,o.owner_id,o.person_id,o.actor_id,o.state,o.head_revision,"
            "v.revision,v.state AS version_state,v.scope_ids FROM sources.originals o "
            "JOIN sources.versions v USING(source_id) WHERE (v.source_id,v.revision) IN "
            "(SELECT * FROM unnest(%s::text[],%s::bigint[]))",
            ([ref.source_id for ref in refs], [ref.revision for ref in refs]),
        )).fetchall()
        indexed = {(row["source_id"], row["revision"]): row for row in rows}
        owned = await self.identity.can_revoke_sources_on(
            connection, context,
            tuple((row["owner_id"], row["person_id"], row["actor_id"]) for row in rows),
        )
        revocable = {(row["source_id"], row["revision"]): value for row, value in zip(rows, owned)}
        result = []
        for ref, usable in zip(refs, allowed):
            key = (ref.source_id, ref.revision)
            row = indexed.get(key)
            visible = bool(row and (revocable[key] or set(row["scope_ids"]) <= set(context.scope_ids)))
            state = "active" if usable else (
                (row["state"] if row["state"] != "active" else
                 "superseded" if row["head_revision"] != ref.revision else
                 row["version_state"] if row["version_state"] != "active" else "unavailable")
                if visible else "unavailable"
            )
            result.append(SourceStatus(ref, state, row["head_revision"] if visible else None, usable))
        return tuple(result)

    async def validate_persistence(self, conn, context, source_refs):
        """Lock only selected roots/supports until result commit; forgetting locks these first too."""
        from .rbj_budget import SourceBudget

        return await self._validate_persistence(conn, context, source_refs, SourceBudget())

    async def _validate_persistence(self, conn, context, source_refs, budget):
        from .proofs import check_many_on, normalize_groups

        source_refs = normalize_groups((source_refs,))[0]
        try:
            await self.identity.lock_context_on(conn, context)
            await self._lock_selected(conn, source_refs, budget=budget)
            return (await check_many_on(self.identity, conn, context, (source_refs,), budget=budget))[0]
        except RecoveryWait:
            raise
        except AppError as exc:
            if exc.status in {401, 403, 409}:
                return False
            raise

    async def _lock_selected(
        self, conn, source_refs, *, extra=(), exclusive=False, extra_aliases=(), write_ids=(),
        budget=None,
    ):
        if source_refs or extra or extra_aliases or write_ids:
            from .archives import lock_selected

            await lock_selected(
                conn, source_refs, extra=extra, exclusive=exclusive, aliases=extra_aliases,
                write_ids=write_ids, budget=budget,
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
        from .proofs import check_many_on

        return await check_many_on(self.identity, conn, context, refs_groups)

    async def _check_with_budget(self, conn, context, refs_groups, budget):
        from .proofs import check_many_on

        return await check_many_on(self.identity, conn, context, refs_groups, budget=budget)

    async def describe_for_write_on(self, conn, context, source_refs):
        """Verified descriptions with source locks held until the caller's write commits."""
        if not await self.validate_persistence(conn, context, tuple(source_refs)):
            raise AppError("source_unavailable", status=403)
        if not source_refs:
            return ()
        rows = await (
            await conn.execute(
                "SELECT source_id,revision,scope_ids,evidence_unit,origin_kind,occurred_at,speaker_id,locator,media "
                "FROM sources.versions WHERE (source_id,revision) IN (SELECT * FROM unnest(%s::text[],%s::bigint[]))",
                ([ref.source_id for ref in source_refs], [ref.revision for ref in source_refs]),
            )
        ).fetchall()
        return tuple(rows)

    async def prepare_event_write(
        self, connection, context, *, producer, event_id, revision, parts,
        support_refs=(), additional_refs=(),
    ):
        """Prepare one event before taking source or domain row locks."""
        aliases = await self.prepare_event_writes(connection, context, ({
            "producer": producer, "event_id": event_id, "revision": revision,
            "parts": parts, "support_refs": support_refs,
        },), additional_refs=additional_refs)
        return aliases[0]

    async def prepare_event_writes(
        self, connection, context, events, *, additional_refs=(), scoped_refs=()
    ):
        """Prepare event and per-context sources before any domain/source locks.

        Scoped groups are checked under their original contexts; false does not
        reject the event, and callers must recheck before using those groups.
        """
        from .rbj_budget import SourceBudget

        return await self._prepare_event_writes(
            connection, context, events, additional_refs=additional_refs,
            scoped_refs=scoped_refs, budget=SourceBudget(),
        )

    async def _prepare_event_writes(
        self, connection, context, events, *, additional_refs=(), scoped_refs=(), budget,
        validate_supports=True,
    ):
        """Prepare before domain/source locks; scoped false proofs remain optional.

        Subsequent record_event calls must use the same identities, revisions,
        parts and support refs. Callers recheck scoped groups before using them.
        """
        from .proofs import check_scoped_on, normalize_groups, unique_refs
        from .rbj_budget import bounded_tuple

        events = bounded_tuple(events, budget.remaining["inputs"])
        budget.charge(inputs=len(events), work=len(events))
        scoped_items = bounded_tuple(scoped_refs, budget.remaining["inputs"])
        budget.charge(inputs=len(scoped_items), work=len(scoped_items))
        scoped, supplied_refs = [], 0
        for original_context, refs in scoped_items:
            refs = bounded_tuple(refs, budget.remaining["inputs"] - supplied_refs)
            supplied_refs += len(refs)
            scoped.append((original_context, normalize_groups((
                tuple(SourceRef(**ref) if isinstance(ref, dict) else ref for ref in refs),
            ))[0]))
        scoped = tuple(scoped)
        additional_refs = bounded_tuple(additional_refs, budget.remaining["inputs"] - supplied_refs)
        supplied_refs += len(additional_refs)
        required = [SourceRef(**ref) if isinstance(ref, dict) else ref for ref in additional_refs]
        dependencies, source_ids, prepared = [], [], []
        for event in events:
            producer, event_id, revision = event["producer"], event["event_id"], event["revision"]
            if type(revision) is not int or revision < 1:
                raise AppError("invalid_source_event")
            original_parts = event["parts"]
            if len(original_parts) > 256:
                raise AppError("source_parts_too_many")
            parts = wire(original_parts)
            budget.charge(inputs=len(parts), work=len(parts))
            support_refs = bounded_tuple(event.get("support_refs", ()),
                                         budget.remaining["inputs"] - supplied_refs)
            supplied_refs += len(support_refs)
            required.extend(SourceRef(**ref) if isinstance(ref, dict) else ref for ref in support_refs)
            for position, part in enumerate(parts):
                if not isinstance(part, dict):
                    raise AppError("invalid_source_part")
                locator = {"producer": producer, "event_id": event_id, "part": position}
                key = body_hash({"principal": context.principal_id,
                                 "actor": context.actor_id, "locator": locator})
                source_ids.append("source_" + key[:48])
                media = part.get("media")
                if media:
                    original = original_parts[position]
                    candidate = (original.get("media") if isinstance(original, dict)
                                 else getattr(original, "media", None))
                    if not self.files.was_written(candidate):
                        pieces = media.get("media_id", "").split(":")
                        if len(pieces) != 3 or pieces[0] != "source" or not pieces[2].isdigit():
                            raise AppError("source_media_not_authorized", status=403)
                        dependencies.append(SourceRef(pieces[1], int(pieces[2]), {}))
            # Match the persisted evidence_unit used by the older-part query.
            event_key = body_hash({"principal": context.principal_id, "actor": context.actor_id,
                                   "evidence_unit": f"{producer}:{event_id}"})
            prepared.append((event_key, producer, event_id, revision))

        contexts = (context, *(original_context for original_context, _ in scoped))
        if scoped:
            contexts = sorted(contexts, key=_context_lock_order)
        previous_context = None
        for original_context in contexts:
            if original_context != previous_context:
                await self.identity.lock_context_on(connection, original_context)
                previous_context = original_context
        # One event key serializes revisions of that event, never unrelated actors/events.
        await connection.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended('sources:event:'||value,0)) "
            "FROM unnest(%s::text[]) value ORDER BY value",
            (sorted({item[0] for item in prepared}),),
        )
        from .archives import event_alias

        aliases = []
        for _, producer, event_id, revision in prepared:
            aliases.append(await event_alias(self, connection, context, producer, event_id))
            previous = await (await connection.execute(
                "SELECT o.source_id,o.head_revision FROM sources.originals o JOIN sources.versions v "
                "ON v.source_id=o.source_id AND v.revision=o.head_revision WHERE o.owner_id=%s "
                "AND o.actor_id IS NOT DISTINCT FROM %s AND v.evidence_unit=%s "
                "AND o.head_revision<%s AND o.state='active' ORDER BY o.source_id LIMIT %s",
                (context.principal_id, context.actor_id, f"{producer}:{event_id}", revision,
                 min(budget.remaining["inputs"], budget.remaining["nodes"]) + 1),
            )).fetchall()
            if len(previous) > budget.remaining["nodes"]:
                raise AppError("source_dependency_budget_exceeded", status=503)
            budget.charge(inputs=len(previous), work=len(previous))
            dependencies.extend(SourceRef(row["source_id"], row["head_revision"], {}) for row in previous)
            source_ids.extend(row["source_id"] for row in previous)
        scoped_sources = tuple(ref for _, refs in scoped for ref in refs)
        await self._lock_selected(
            connection, tuple((*required, *dependencies, *scoped_sources)), extra=tuple(source_ids),
            extra_aliases=tuple(alias for alias in aliases if alias), write_ids=tuple(source_ids),
            budget=budget,
        )
        # Exact duplicates under the same complete context need one proof.
        # Different contexts and different locators remain separate authorities.
        checks = []
        if required:
            checks.append((context, unique_refs(required), True))
        for original_context, refs in scoped:
            refs = unique_refs(refs)
            if not any(original_context == checked_context and refs == checked_refs
                       for checked_context, checked_refs, _ in checks):
                checks.append((original_context, refs, False))
        # record_event owns the atomic registration and proves each part's
        # supports under its actual intake context in _register. Its enclosing
        # savepoint rolls back the whole event on denial. Preflight callers
        # still need the proof here before taking their domain locks.
        if not validate_supports:
            return tuple(aliases)
        results = await check_scoped_on(self.identity, connection, tuple(
            (original_context, (refs,), mandatory)
            for original_context, refs, mandatory in checks
        ), budget=budget)
        if required and not results[0][0]:
            raise AppError("source_support_unavailable", status=403)
        return tuple(aliases)

    async def record_event(
        self, context, *, producer, event_id, revision, occurred_at, parts, origin_kind,
        connection=None, support_refs=(), generation=None,
    ):
        if occurred_at.tzinfo is None or type(revision) is not int or revision < 1:
            raise AppError("invalid_source_event")
        original_parts = parts
        parts = wire(parts)
        if len(parts) > 256:
            raise AppError("source_parts_too_many")
        from .rbj_budget import SourceBudget

        refs = []
        budget = SourceBudget()
        async with self._event_connection(connection) as conn:
            expected_generation = (
                generation if generation is not None else await self.jobs.current_generation_on(conn)
            )
            await self.jobs.assert_generation_on(conn, expected_generation)
            intake, epochs = await self.identity.intake_scope(context, connection=conn)
            write_context = replace(context, scope_ids=intake, audience=intake)
            author = (
                await self.identity.event_author_on(conn, context, producer, event_id, revision)
                if origin_kind == OriginKind.HUMAN
                else None
            )
            from .archives import bind_event

            native_alias = (await self._prepare_event_writes(conn, context, ({
                "producer": producer, "event_id": event_id, "revision": revision,
                "parts": original_parts, "support_refs": support_refs,
            },), budget=budget, validate_supports=False))[0]
            if native_alias:
                if await (
                    await conn.execute(
                        "SELECT 1 FROM sources.alias_suppression WHERE alias_key=%s",
                        (native_alias,),
                    )
                ).fetchone():
                    raise AppError("source_suppressed", status=409)
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
                        if not (await self._check_with_budget(conn, context, ((ref,),), budget))[0]:
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
                        (tuple(support_refs),) if support_refs else (),
                        speaker_id=author if part_origin == OriginKind.HUMAN else None,
                        locks_prepared=True, budget=budget,
                    )
                )
            removed = await (
                await conn.execute(
                    "SELECT o.source_id,o.head_revision FROM sources.originals o JOIN "
                    "sources.versions v ON v.source_id=o.source_id AND v.revision=o.head_revision WHERE o.owner_id=%s "
                    "AND o.actor_id IS NOT DISTINCT FROM %s AND v.evidence_unit=%s AND o.head_revision<%s "
                    "AND o.state='active' ORDER BY o.source_id FOR UPDATE OF o",
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

    @asynccontextmanager
    async def _event_connection(self, connection):
        if connection is not None:
            # A budget/error after an earlier part must not leave partial source
            # writes if the owner catches the error inside its outer transaction.
            async with transaction_on(connection):
                yield connection
        else:
            async with self.database.transaction() as opened:
                yield opened

    async def _freeze_generation(self, generation, *, connection=None):
        """Keep a supplied generation or capture one for a new synchronous call."""
        if generation is not None:
            return generation
        if connection is not None:
            return await self.jobs.current_generation_on(connection)
        async with self.database.transaction(control=True) as snapshot:
            return await self.jobs.current_generation_on(snapshot)

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
        speaker_id=None,
        locks_prepared=False,
        budget=None,
    ):
        from .rbj_budget import SourceBudget

        budget = budget if budget is not None else SourceBudget()
        if not context.scope_ids:
            raise AppError("source_scope_required", status=403)
        source_id = "source_" + key[:48]
        dependencies = tuple(
            SourceRef(**ref) if isinstance(ref, dict) else ref
            for group in support_groups
            for ref in group
        )
        if not locks_prepared:
            await self._lock_selected(conn, dependencies, extra=(source_id,), write_ids=(source_id,), budget=budget)
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
                "SELECT content,media,scope_ids,origin_kind,speaker_id,locator,coverage,disclosure,state "
                "FROM sources.versions WHERE "
                "source_id=%s AND revision=%s",
                (source_id, revision),
            )
        ).fetchone()
        if old:
            from .rbj_locking import support_rows_on

            rows = await support_rows_on(conn, source_id, revision, budget)
            stored_groups = {}
            for row in rows:
                stored_groups.setdefault(row["group_id"], []).append(SourceRef(
                    row["support_source_id"], row["support_revision"], row["support_locator"]
                ))
            def group_keys(groups):
                return sorted({body_hash(sorted({
                    body_hash(SourceRef(**ref) if isinstance(ref, dict) else ref) for ref in group
                })) for group in groups})
            if (
                old["content"] != content
                or old["speaker_id"] != speaker_id
                or old["media"] != wire(media)
                or old["origin_kind"] != str(origin_kind)
                or set(old["scope_ids"]) != set(context.scope_ids)
                or old["locator"] != wire(locator)
                or old["coverage"] != wire(coverage)
                or old["disclosure"] != disclosure
                or group_keys(stored_groups.values()) != group_keys(support_groups)
            ):
                raise AppError("source_revision_conflict", status=409)
            if (original["state"] != "active" or old["state"] != "active"
                or original["head_revision"] != revision):
                raise AppError("source_unavailable", status=403)
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
            "evidence_unit,content,media,locator,coverage,occurred_at,disclosure,speaker_id) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
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
                Jsonb(wire(locator)),
                Jsonb(wire(coverage)),
                occurred_at,
                disclosure,
                speaker_id,
            ),
        )
        for index, group in enumerate(support_groups):
            group = tuple(
                SourceRef(**support) if isinstance(support, dict) else support for support in group
            )
            if (
                not group
                or not (
                    await self._check_with_budget(conn, support_context or context, (tuple(group),), budget)
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
            from .rbj_budget import SourceBudget

            budget = SourceBudget()
            locator = {"producer": producer, "event_id": event_id, "kind": "publication"}
            key = body_hash(
                {"principal": context.principal_id, "actor": context.actor_id, "locator": locator}
            )
            stable_id = producer + ":" + event_id
            scopes, epochs = await self.identity.publication_scope(
                context, stable_id, reader_ids, conversation_ids, connection=conn
            )
            await self._lock_selected(
                conn, tuple(source_refs), extra=("source_" + key[:48],),
                write_ids=("source_" + key[:48],), budget=budget,
            )
            if not (await self._check_with_budget(conn, context, (tuple(source_refs),), budget))[0]:
                raise AppError("source_unavailable", status=403)
            target = replace(context, scope_ids=scopes, audience=scopes)
            return await self._register(
                conn,
                target,
                epochs,
                key,
                revision,
                locator,
                datetime.now(UTC),
                OriginKind.ACTOR if context.actor_id else OriginKind.TOOL_RESULT,
                parts[0]["text"],
                None,
                "publication:" + stable_id,
                Coverage(True),
                (tuple(source_refs),) if source_refs else (),
                disclosure=True,
                support_context=context,
                locks_prepared=True, budget=budget,
            )

        if connection is not None:
            # The audience is prepared before source locks; a failed proof or
            # source write must not leave audience changes in the caller's tx.
            async with transaction_on(connection):
                return await publish(connection)
        async with self.database.transaction() as conn:
            return await publish(conn)

    async def revoke_projection(self, conn, context, projection_id, *, source_refs=()):
        locator = {"kind": "ordinary_interest", "projection_id": projection_id}
        key = body_hash(
            {"principal": context.principal_id, "actor": context.actor_id, "locator": locator}
        )
        # Select all authority/source locks before a domain callback locks a claim.
        await self.identity.lock_context_on(conn, context)
        await self._lock_selected(
            conn, tuple(source_refs), extra=("source_" + key[:48],),
            write_ids=("source_" + key[:48],),
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

    async def retract_publication(self, context, source_ref, *, connection):
        """A publication owner withdraws its precise disclosure in the domain transaction."""
        conn = connection
        if source_ref.locator:
            raise AppError("publication_whole_reference_required")
        await self.identity.lock_context_on(conn, context)
        await self._lock_selected(conn, (source_ref,), write_ids=(source_ref.source_id,))
        row = await (
            await conn.execute(
                "SELECT o.*,v.disclosure FROM sources.originals o JOIN sources.versions v ON v.source_id=o.source_id AND v.revision=o.head_revision WHERE o.source_id=%s",
                (source_ref.source_id,),
            )
        ).fetchone()
        if (
            not row
            or not row["disclosure"]
            or not await self.identity.can_revoke_source(
                conn, context, row["owner_id"], row["person_id"], row["actor_id"]
            )
        ):
            raise AppError("publication_retraction_forbidden", status=403)
        if row["head_revision"] != source_ref.revision:
            raise AppError("revision_conflict", status=409)
        if row["state"] == "active":
            await conn.execute(
                "UPDATE sources.originals SET state='retracted',intake_epoch=intake_epoch+1 WHERE source_id=%s",
                (source_ref.source_id,),
            )
            await conn.execute(
                "UPDATE sources.versions SET state='retracted',content=NULL,media=NULL WHERE source_id=%s AND revision=%s",
                (source_ref.source_id, source_ref.revision),
            )
            await self._changed(conn, source_ref, "retracted")
        return source_ref

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
            from .rbj_budget import SourceBudget

            budget = SourceBudget()
            locator = {"kind": kind, "projection_id": projection_id}
            key = body_hash(
                {"principal": context.principal_id, "actor": context.actor_id, "locator": locator}
            )
            await self.identity.lock_context_on(conn, context)
            await self._lock_selected(
                conn, tuple(source_refs), extra=("source_" + key[:48],),
                write_ids=("source_" + key[:48],), budget=budget,
            )
            if not (await self._check_with_budget(conn, context, (tuple(source_refs),), budget))[0]:
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
                locks_prepared=True, budget=budget,
            )

        if connection is not None:
            async with transaction_on(connection):
                return await publish(connection)
        async with self.database.transaction() as conn:
            return await publish(conn)

    async def capture(self, context, *, locator, raw, mime_type, generation=None):
        generation = await self._freeze_generation(generation)
        await self.check(context)
        base_type = mime_type.partition(";")[0].strip().lower()
        structured = (
            base_type in {"application/json", "application/xml"}
            or (base_type.startswith("application/") and base_type.endswith(("+json", "+xml")))
        )
        # Preserve the exact UTF-8 representation and original MIME, including structured text.
        text = None
        if base_type.startswith("text/"):
            text = raw.decode("utf-8")
        elif structured:
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                # Non-UTF8 structured originals remain readable as their original byte media.
                pass
        media = await asyncio.to_thread(self.files.put_bytes, raw, media_type=mime_type)
        return await self._register_media(
            context, media, locator, OriginKind.TOOL_RESULT, text, generation=generation,
        )

    async def register_media(
        self, context, media, *, locator, origin_kind=OriginKind.HUMAN, generation=None,
    ):
        generation = await self._freeze_generation(generation)
        return await self._register_media(
            context, media, locator, origin_kind, None, generation=generation,
        )

    async def register_media_supplement(
        self, context, media, *, original_ref, producer, event_id, event_revision,
        part_index, supplement_revision, connection=None, generation=None,
    ):
        """Attach a separately versioned original file to one immutable event part."""
        if (
            not isinstance(original_ref, SourceRef) or original_ref.locator
            or type(event_revision) is not int or event_revision < 1
            or original_ref.revision != event_revision
            or type(part_index) is not int or part_index < 0
            or type(supplement_revision) is not int or supplement_revision < 1
            or not isinstance(producer, str) or not producer
            or not isinstance(event_id, str) or not event_id
        ):
            raise AppError("invalid_media_supplement")
        if not self.files.was_written(media):
            raise AppError("media_intake_not_owned", status=403)

        # Freeze the current generation before file verification yields. A
        # resumed job supplies its original generation instead of acquiring one.
        generation = await self._freeze_generation(generation, connection=connection)

        def verify():
            with self.files.open_reader(media):
                pass

        await asyncio.to_thread(verify)
        locator = {
            "kind": "media_supplement", "producer": producer, "event_id": event_id,
            "event_revision": event_revision, "part": part_index,
            "original_ref": wire(original_ref),
        }
        # The original reference already fixes the event owner and scene. A
        # different authorized reader must not create a second attachment identity.
        key = body_hash(locator)
        digest = body_hash({"locator": locator, "revision": supplement_revision, "media": media})
        from .rbj_budget import SourceBudget

        budget = SourceBudget()
        async with self._event_connection(connection) as conn:
            await self.jobs.assert_generation_on(conn, generation)
            await self.identity.lock_context_on(conn, context)
            await self._lock_selected(
                conn, (original_ref,), extra=("source_" + key[:48],),
                write_ids=("source_" + key[:48],), budget=budget,
            )
            if not (await self._check_with_budget(conn, context, ((original_ref,),), budget))[0]:
                raise AppError("source_unavailable", status=403)
            original = await (await conn.execute(
                "SELECT o.owner_id,o.person_id,o.actor_id,v.scope_ids,v.locator,"
                "v.occurred_at,v.origin_kind,v.speaker_id "
                "FROM sources.originals o JOIN sources.versions v USING(source_id) "
                "WHERE v.source_id=%s AND v.revision=%s",
                (original_ref.source_id, original_ref.revision),
            )).fetchone()
            if (not original or original["actor_id"] != context.actor_id
                or original["locator"].get("producer") != producer
                or original["locator"].get("event_id") != event_id
                or original["locator"].get("part") != part_index):
                raise AppError("media_supplement_original_mismatch", status=409)
            if original["owner_id"] != context.principal_id:
                raise AppError("media_supplement_owner_required", status=403)
            intake = tuple(original["scope_ids"])
            epochs = await self.identity.support_epochs(intake, connection=conn)
            target = replace(context, principal_id=original["owner_id"], person_id=original["person_id"],
                             scope_ids=intake, audience=intake)
            ref = await self._register(
                conn, target, epochs, key, supplement_revision, locator,
                original["occurred_at"], OriginKind(original["origin_kind"]), None,
                wire(media), f"supplement:{producer}:{event_id}:{event_revision}:{part_index}",
                Coverage(True), ((original_ref,),), support_context=context,
                speaker_id=original["speaker_id"],
                locks_prepared=True, budget=budget,
            )
            await conn.execute(
                "INSERT INTO sources.media_supplements(source_id,revision,request_digest) "
                "VALUES(%s,%s,%s) ON CONFLICT DO NOTHING",
                (ref.source_id, ref.revision, digest),
            )
            row = await (await conn.execute(
                "SELECT request_digest FROM sources.media_supplements WHERE source_id=%s AND revision=%s",
                (ref.source_id, ref.revision),
            )).fetchone()
            if row["request_digest"] != digest:
                raise AppError("source_revision_conflict", status=409)
            if not (await self._check_with_budget(conn, context, ((ref,),), budget))[0]:
                raise AppError("source_unavailable", status=403)
            return ref

    async def validate_media_supplement_on(
        self, connection, context, supplement_ref, *, original_ref, producer,
        event_id, event_revision, part_index,
    ):
        """Prove binding and hold both references until the caller's write commits."""
        if (not isinstance(supplement_ref, SourceRef) or supplement_ref.locator
            or not isinstance(original_ref, SourceRef) or original_ref.locator
            or original_ref.revision != event_revision):
            return False
        from .rbj_budget import SourceBudget

        budget = SourceBudget()
        await self.identity.lock_context_on(connection, context)
        await self._lock_selected(connection, (supplement_ref, original_ref), budget=budget)
        if not (await self._check_with_budget(connection, context, ((supplement_ref, original_ref),), budget))[0]:
            return False
        row = await (await connection.execute(
            "SELECT v.locator,v.media,o.actor_id,original.locator AS original_locator "
            "FROM sources.versions v JOIN sources.originals o USING(source_id) "
            "JOIN sources.media_supplements s ON s.source_id=v.source_id AND s.revision=v.revision "
            "JOIN sources.versions original ON original.source_id=%s AND original.revision=%s "
            "WHERE v.source_id=%s AND v.revision=%s",
            (original_ref.source_id, original_ref.revision,
             supplement_ref.source_id, supplement_ref.revision),
        )).fetchone()
        expected = {
            "kind": "media_supplement", "producer": producer, "event_id": event_id,
            "event_revision": event_revision, "part": part_index,
            "original_ref": wire(original_ref),
        }
        if (not row or not row["media"] or row["actor_id"] != context.actor_id
            or row["locator"] != expected
            or row["original_locator"].get("producer") != producer
            or row["original_locator"].get("event_id") != event_id
            or row["original_locator"].get("part") != part_index):
            return False
        from .rbj_locking import support_rows_on

        supports = await support_rows_on(connection, supplement_ref.source_id, supplement_ref.revision, budget)
        return bool(supports) and all(
            item["support_source_id"] == original_ref.source_id
            and item["support_revision"] == original_ref.revision
            and item["support_locator"] == wire(original_ref.locator)
            for item in supports
        )

    async def publish_media(
        self, context, media, *, locator, source_refs=(), target="actor", generation=None,
    ):
        """Explicitly copy original bytes to an existing actor or intake scope.

        Uploaded/private originals remain private. Their withdrawal still invalidates
        this disclosure, while an authorized reader need not read those originals.
        """
        if not isinstance(target, str) or target not in {"actor", "current"}:
            raise AppError("invalid_media_target", status=400)
        if target == "actor" and not context.actor_id:
            raise AppError("actor_required", status=400)
        if "config.publish" not in context.permissions and "*" not in context.permissions:
            raise AppError("forbidden", status=403)
        generation = await self._freeze_generation(generation)
        return await self._register_media(
            context, media, locator, context.origin_kind, None,
            actor_shared=target == "actor", source_refs=tuple(source_refs), disclosure=True,
            generation=generation,
        )

    async def _register_media(
        self, context, media, locator, origin_kind, content, *,
        generation, actor_shared=False, source_refs=(), disclosure=False,
    ):
        from .rbj_budget import SourceBudget

        budget = SourceBudget()
        if not (await self._check_many(context, (source_refs,), budget))[0]:
            raise AppError("source_unavailable", status=403)
        if not self.files.was_written(media):
            raise AppError("media_intake_not_owned", status=403)

        def verify():
            with self.files.open_reader(media):
                pass

        await asyncio.to_thread(verify)
        async with self.database.transaction() as conn:
            await self.jobs.assert_generation_on(conn, generation)
            if actor_shared:
                await self.identity.lock_context_on(conn, context)
                scope = await self.identity.public_scope(context, connection=conn)
                intake = (scope,)
                epochs = await self.identity.support_epochs(intake, connection=conn)
            else:
                intake, epochs = await self.identity.intake_scope(context, connection=conn)
            if disclosure:
                support_groups = (source_refs,) if source_refs else ()
            else:
                support_groups = locator.get("support_groups", ())
            write_context = replace(context, scope_ids=intake, audience=intake)
            identity = {"principal": context.principal_id, "actor": context.actor_id, "locator": locator}
            if actor_shared:
                identity["shared_scope"] = intake
            elif disclosure:
                identity["copy_scope"] = intake
            key = body_hash(identity)
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
                support_groups,
                disclosure=disclosure,
                support_context=context if disclosure else None,
                budget=budget,
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
                    "SELECT q.ordinal,v.source_id,v.revision,v.media,v.origin_kind,v.speaker_id,v.occurred_at,v.locator,v.scope_ids,o.owner_id,o.person_id,o.actor_id,"
                    "length(v.content) AS total,substring(v.content from q.position+1 for q.maximum) AS content "
                    "FROM unnest(%s::text[],%s::bigint[],%s::integer[],%s::integer[]) WITH ORDINALITY "
                    "q(source_id,revision,position,maximum,ordinal) JOIN sources.versions v USING(source_id,revision) JOIN sources.originals o USING(source_id) ORDER BY q.ordinal",
                    (
                        [ref.source_id for ref in refs],
                        [ref.revision for ref in refs],
                        positions,
                        lengths,
                    ),
                )
            ).fetchall()
            revocable = await self.identity.can_revoke_sources_on(
                conn,
                context,
                tuple((row["owner_id"], row["person_id"], row["actor_id"]) for row in rows),
            )
        results = []
        for row, source, position, maximum, can_revoke in zip(
            rows, refs, positions, lengths, revocable
        ):
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
                    "speaker_id": row["speaker_id"],
                    "source_person_id": row["person_id"],
                    "scope_ids": row["scope_ids"],
                    "can_revoke": can_revoke,
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
                    {"source": wire(ref), "kind": kind, "change_id": change["sequence"]},
                    ServiceClass.DUE,
                ),
            )

    async def _notify_dependants(self, conn, refs, kind, event_key):
        for name in self.dependants:
            await self.jobs.enqueue(
                conn,
                JobSpec(
                    "sources.dispatch", f"source-dispatch:{event_key}:{body_hash(name)}",
                    {"consumer": name, "sources": wire(refs), "kind": kind,
                     "event_key": event_key}, ServiceClass.DUE,
                ),
            )

    async def dispatch_dependant(self, consumer, sources, kind, event_key):
        registration = self.dependants.get(consumer)
        if registration is None:
            raise AppError("source_consumer_unavailable", status=503)
        refs = tuple(SourceRef(**item) for item in sources)
        async with self.database.transaction() as conn:
            _, partitioner = registration
            actors = await partitioner(conn, refs, kind) if partitioner else (None,)
            actors = tuple(dict.fromkeys(actors))
            if any(actor is not None and (not isinstance(actor, str) or not actor) for actor in actors):
                raise AppError("invalid_source_consumer_partition")
            for actor_id in actors:
                await self.jobs.enqueue(
                    conn,
                    JobSpec(
                        "sources.notify",
                        f"source-notify:{event_key}:{body_hash({'consumer': consumer, 'actor': actor_id})}",
                        {"consumer": consumer, "sources": sources, "kind": kind,
                         "actor_id": actor_id, "partitioned": partitioner is not None},
                        ServiceClass.DUE, subject_id=actor_id,
                    ),
                )

    async def notify_dependant(self, consumer, sources, kind, actor_id=None, partitioned=False):
        registration = self.dependants.get(consumer)
        if registration is None:
            raise AppError("source_consumer_unavailable", status=503)
        callback, partitioner = registration
        if bool(partitioner) != partitioned:
            raise AppError("source_consumer_contract_changed", status=409)
        refs = tuple(SourceRef(**item) for item in sources)
        async with self.database.transaction() as conn:
            if partitioned:
                await callback(conn, refs, kind, actor_id=actor_id)
            else:
                await callback(conn, refs, kind)

    async def recall_event(
        self, context, producer, event_id, *, revision=None, reason="channel_recall"
    ):
        if (
            context.origin_kind not in {OriginKind.OBSERVATION, OriginKind.TOOL_RESULT}
            or not context.conversation_id
        ):
            raise AppError("verified_channel_event_required", status=403)
        async with self.database.transaction() as conn:
            await self.identity.lock_context_on(conn, context)
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
                write_ids=tuple(row["source_id"] for row in rows),
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
            await self.identity.lock_context_on(conn, context)
            await self._lock_selected(conn, (source,), write_ids=(source.source_id,))
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
            await self.identity.lock_context_on(conn, context)
            await self._lock_selected(conn, (source,), write_ids=(source.source_id,))
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

    async def propagate(self, source, kind, cursor=None, change_id=None):
        source = SourceRef(**source)
        async with self.database.transaction() as conn:
            event_key = str(change_id) if change_id is not None else body_hash({"source": source, "kind": kind})
            if cursor is None:
                # Consumer registration belongs to the background process, not to
                # whichever process happened to withdraw the source.
                await self._notify_dependants(conn, (source,), kind, f"change:{event_key}")
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
            await self._lock_selected(conn, candidates, write_ids=tuple(ref.source_id for ref in candidates))
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
            if dependents:
                await conn.execute(
                    "UPDATE sources.versions v SET state='unsupported',content=NULL,media=NULL,locator='{}' "
                    "WHERE (source_id,revision) IN(SELECT * FROM unnest(%s::text[],%s::bigint[])) "
                    "AND NOT sources.any_support_usable(jsonb_build_array(jsonb_build_array(jsonb_build_object("
                    "'source_id',v.source_id,'revision',v.revision,'locator','{}'::jsonb))),v.scope_ids)",
                    ([ref.source_id for ref in dependents], [ref.revision for ref in dependents]),
                )
                await self._notify_dependants(
                    conn, dependents, kind,
                    f"propagation:{event_key}:{body_hash(cursor)}",
                )
            if len(rows) > 512:
                last = {"source_id": rows[511]["source_id"], "revision": rows[511]["revision"]}
                await self.jobs.enqueue(
                    conn,
                    JobSpec(
                        "sources.propagate",
                        f"source-propagation:{event_key}:{last['source_id']}:{last['revision']}",
                        {"source": wire(source), "kind": kind, "cursor": last,
                         "change_id": change_id},
                        ServiceClass.DUE,
                    ),
                )
