"""Explicit current-conversation archive intake and stable native-message suppression."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from itertools import groupby
import math

from tianshu.runtime.commands import body_hash
from tianshu.runtime.errors import AppError
from tianshu.runtime.recovery_policy import RecoveryWait
from tianshu.types import Coverage, OriginKind, SourceRef


def alias_key(context, alias, scene):
    if not isinstance(alias, dict) or any(
        not isinstance(alias.get(key), str) or not 1 <= len(alias[key]) <= 512
        for key in ("platform", "account_id", "room_id", "message_type", "id_kind", "message_id")
    ):
        raise AppError("invalid_archive_alias")
    if alias["id_kind"] != "external" or not any(
        alias["platform"] == target["namespace"]
        and alias["account_id"] == target["self_external_id"]
        and alias["room_id"] == target["target_ref"]
        and alias["message_type"] == target["kind"]
        for target in scene
    ):
        raise AppError("archive_alias_scene_mismatch", status=403)
    # import_source_id identifies an archive import, never a different native message.
    return body_hash(
        {
            "actor_id": context.actor_id,
            **{
                key: alias[key]
                for key in (
                    "platform",
                    "account_id",
                    "room_id",
                    "message_type",
                    "id_kind",
                    "message_id",
                )
            },
        }
    )


async def event_alias(service, conn, context, producer, event_id):
    if not context.conversation_id or not event_id.startswith("message:"):
        return None
    if not await service.identity.verify_channel_producer(conn, context, producer):
        return None
    fields = event_id.split(":", 2)
    if len(fields) != 3 or fields[1] not in {"private", "group"}:
        return None
    scene = await service.identity.archive_scene_on(conn, context)
    target = next(
        (row for row in scene if producer == row["namespace"] + ":" + row["connection_id"]), None
    )
    if target is None or target["kind"] != fields[1]:
        return None
    return alias_key(
        context,
        {
            "platform": target["namespace"],
            "account_id": target["self_external_id"],
            "room_id": target["target_ref"],
            "message_type": fields[1],
            "id_kind": "external",
            "message_id": fields[2],
        },
        scene,
    )


async def bind_event(conn, key, refs):
    for ref in refs if key else ():
        await conn.execute(
            "INSERT INTO sources.source_aliases VALUES(%s,%s,%s) ON CONFLICT DO NOTHING",
            (key, ref.source_id, ref.revision),
        )


def _lock_set_changed():
    return RecoveryWait(
        "source_lock_set_changed", datetime.now(UTC) + timedelta(seconds=5),
        dependency="sources:lock_set",
    )


async def lock_selected(conn, refs, *, extra=(), exclusive=False, aliases=(), write_ids=()):
    """Lock selected roots in ID order, using exclusive mode only for writers.

    write_ids are roots that will be changed; their directly bound aliases can
    be invalidated too. Other selected sources remain shared dependencies.
    """
    async def selected():
        rows = await (await conn.execute(
            "WITH RECURSIVE edges(source_id,revision,target_id,target_revision) AS NOT MATERIALIZED ("
            "SELECT source_id,revision,support_source_id,support_revision FROM sources.supports UNION ALL "
            "SELECT a.source_id,a.revision,b.source_id,b.revision FROM sources.source_aliases a "
            "JOIN sources.source_aliases b USING(alias_key)), selected(source_id,revision) AS ("
            "(SELECT * FROM unnest(%s::text[],%s::bigint[]) UNION SELECT source_id,head_revision "
            "FROM sources.originals WHERE source_id=ANY(%s) UNION SELECT source_id,revision FROM sources.source_aliases "
            "WHERE alias_key=ANY(%s)) UNION SELECT e.target_id,e.target_revision FROM edges e "
            "JOIN selected s ON s.source_id=e.source_id AND s.revision=e.revision) "
            "SELECT DISTINCT source_id FROM selected ORDER BY source_id",
            (
                [ref.source_id for ref in refs],
                [ref.revision for ref in refs],
                list(extra),
                list(aliases),
            ),
        )).fetchall()
        return {row["source_id"] for row in rows} | set(extra)

    async def writable(identifiers):
        if exclusive:
            return set(identifiers)
        targets = set(write_ids)
        if targets or aliases:
            rows = await (await conn.execute(
                "SELECT DISTINCT b.source_id FROM sources.source_aliases b WHERE b.alias_key=ANY(%s) "
                "OR b.alias_key IN (SELECT alias_key FROM sources.source_aliases WHERE source_id=ANY(%s))",
                (list(aliases), list(targets)),
            )).fetchall()
            targets.update(row["source_id"] for row in rows)
        return targets

    async def alias_keys(targets):
        rows = await (await conn.execute(
            "SELECT DISTINCT alias_key FROM sources.source_aliases "
            "WHERE source_id=ANY(%s) ORDER BY alias_key",
            (sorted(targets),),
        )).fetchall()
        return {row["alias_key"] for row in rows} | set(aliases)

    identifiers = await selected()
    targets = await writable(identifiers)
    identifiers |= targets
    keys = await alias_keys(targets) if exclusive or targets or aliases else set()
    if keys:
        await conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended('sources:alias:'||value,0)) "
            "FROM unnest(%s::text[]) value ORDER BY value",
            (sorted(keys),),
        )
        # A concurrent alias binding may have committed while these locks were
        # awaited. Never extend a partially held alias set out of order.
        identifiers = await selected()
        targets = await writable(identifiers)
        identifiers |= targets
        if not (await alias_keys(targets)) <= keys:
            raise _lock_set_changed()
    if targets:
        # A row lock cannot protect a root that has not been inserted yet.
        # Coordinate those identities as well, in the same order for all writers.
        await conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended('sources:root:'||value,0)) "
            "FROM unnest(%s::text[]) value ORDER BY value",
            (sorted(targets),),
        )
    for write, group in groupby(sorted(identifiers), key=lambda item: item in targets):
        selected_ids = list(group)
        await conn.execute(
            "SELECT source_id FROM sources.originals WHERE source_id=ANY(%s) "
            "ORDER BY source_id " + ("FOR UPDATE" if write else "FOR SHARE"),
            (selected_ids,),
        )

    # The closure can also change before a row lock is obtained. Abort the
    # transaction instead of acquiring a newly discovered, unordered lock.
    current = await selected()
    current_targets = await writable(current)
    if not current <= identifiers or not current_targets <= targets:
        raise _lock_set_changed()
    if (exclusive or current_targets or aliases) and not (await alias_keys(current_targets)) <= keys:
        raise _lock_set_changed()


async def invalidate_related(conn, source, kind):
    withdrawn = kind in {"forgotten", "retracted", "fragment_forgotten", "channel_recall"}
    if withdrawn:
        await conn.execute(
            "INSERT INTO sources.alias_suppression(alias_key,reason) "
            "SELECT DISTINCT alias_key,%s FROM sources.source_aliases WHERE source_id=%s "
            "ON CONFLICT DO NOTHING",
            (kind, source.source_id),
        )
    archive_origin = bool(
        await (
            await conn.execute(
                "SELECT 1 FROM sources.archive_records WHERE source_id=%s", (source.source_id,)
            )
        ).fetchone()
    )
    rows = await (
        await conn.execute(
            "SELECT DISTINCT b.source_id,o.head_revision AS revision FROM sources.source_aliases a "
            "JOIN sources.source_aliases b USING(alias_key) LEFT JOIN sources.archive_records r ON r.source_id=b.source_id "
            "JOIN sources.originals o ON o.source_id=b.source_id WHERE a.source_id=%s AND a.revision=%s "
            "AND b.source_id<>a.source_id AND o.state='active' AND (r.source_id IS NOT NULL OR %s) ORDER BY b.source_id",
            (source.source_id, source.revision, archive_origin and withdrawn),
        )
    ).fetchall()
    for row in rows:
        if archive_origin and withdrawn:
            await conn.execute(
                "UPDATE sources.originals SET state='forgotten',intake_epoch=intake_epoch+1 "
                "WHERE source_id=%s",
                (row["source_id"],),
            )
            await conn.execute(
                "INSERT INTO sources.suppression(source_key,source_id,intake_epoch) "
                "SELECT source_key,source_id,intake_epoch FROM sources.originals WHERE source_id=%s ON CONFLICT DO NOTHING",
                (row["source_id"],),
            )
        await conn.execute(
            "UPDATE sources.versions SET state=%s,content=NULL,media=NULL,locator='{}'::jsonb "
            "WHERE source_id=%s",
            (
                "forgotten"
                if archive_origin and withdrawn
                else "archive_stale"
                if kind == "corrected"
                else "unsupported",
                row["source_id"],
            ),
        )
    return tuple(SourceRef(row["source_id"], row["revision"], {}) for row in rows)


async def register_archive(service, context, *, mapping_id, channel, records, explicit_request):
    if not {"*", "memory.read"} & context.permissions:
        raise AppError("forbidden", status=403)
    if (
        type(explicit_request) is not bool
        or not isinstance(mapping_id, str)
        or not 1 <= len(mapping_id) <= 128
        or not isinstance(channel, dict)
        or not isinstance(records, (tuple, list))
        or len(records) > 100
    ):
        raise AppError("invalid_archive_batch")
    if any(not isinstance(row, dict) for row in records):
        raise AppError("invalid_archive_record")
    if sum(len(str(row.get("text", ""))) for row in records) > 2 * 1024 * 1024:
        raise AppError("archive_material_too_large", status=413)
    results = []
    async with service.database.transaction() as conn:
        scene = await service.identity.archive_scene_on(conn, context)
        intake, epochs = await service.identity.intake_scope(context, connection=conn)
        generation = await (await conn.execute(
            "SELECT id,created_at FROM runtime.installation_generation WHERE singleton"
        )).fetchone()
        if not generation:
            raise AppError("archive_generation_unavailable", status=503)
        write_context = replace(context, scope_ids=intake, audience=intake)
        prepared = []
        for record in records:
            aliases = record.get("native_aliases")
            if (
                not isinstance(record.get("locator"), str)
                or not 1 <= len(record["locator"]) <= 2048
                or not isinstance(record.get("stored_content_sha256"), str)
                or not 1 <= len(record["stored_content_sha256"]) <= 128
                or not isinstance(record.get("text"), str)
                or len(record["text"]) > 1024 * 1024
                or record.get("text_representation") != "stored_text"
                or not isinstance(record.get("sender_id"), str)
                or len(record["sender_id"]) > 512
                or not isinstance(aliases, list)
                or not 1 <= len(aliases) <= 32
                or type(record.get("timestamp")) not in (int, float)
                or not math.isfinite(record["timestamp"])
            ):
                raise AppError("invalid_archive_record")
            keys = tuple(sorted({alias_key(context, alias, scene) for alias in aliases}))
            try:
                occurred_at = datetime.fromtimestamp(record["timestamp"], UTC)
            except (ValueError, OverflowError, OSError):
                raise AppError("invalid_archive_timestamp") from None
            key = body_hash(
                {
                    "archive": mapping_id,
                    "channel": channel,
                    "locator": record["locator"],
                    "actor_id": context.actor_id,
                    "conversation_id": context.conversation_id,
                }
            )
            prepared.append((record, keys, key, occurred_at))
        # One query for all selected aliases. Tombstones and fragment barriers remain after content erasure.
        all_keys = sorted({key for _, keys, _, _ in prepared for key in keys})
        bound = await (
            await conn.execute(
                "SELECT a.alias_key,a.source_id,a.revision,o.state,o.head_revision,v.state AS version_state,"
                "EXISTS(SELECT 1 FROM sources.forgotten_fragments f WHERE f.source_id=a.source_id AND f.revision=a.revision) AS fragment_forgotten "
                "FROM sources.source_aliases a JOIN sources.originals o USING(source_id) "
                "JOIN sources.versions v ON v.source_id=a.source_id AND v.revision=a.revision WHERE a.alias_key=ANY(%s)",
                (all_keys,),
            )
        ).fetchall()
        await service._lock_selected(
            conn,
            tuple(SourceRef(row["source_id"], row["revision"], {}) for row in bound),
            extra=tuple("source_" + key[:48] for _, _, key, _ in prepared),
            extra_aliases=all_keys,
            write_ids=tuple("source_" + key[:48] for _, _, key, _ in prepared),
        )
        # Locks precede this second metadata query: a concurrent forget cannot be missed.
        bound = await (
            await conn.execute(
                "SELECT a.alias_key,a.source_id,a.revision,o.state,o.head_revision,v.state AS version_state,"
                "EXISTS(SELECT 1 FROM sources.forgotten_fragments f WHERE f.source_id=a.source_id AND f.revision=a.revision) AS fragment_forgotten "
                "FROM sources.source_aliases a JOIN sources.originals o USING(source_id) "
                "JOIN sources.versions v ON v.source_id=a.source_id AND v.revision=a.revision WHERE a.alias_key=ANY(%s)",
                (all_keys,),
            )
        ).fetchall()
        suppressed = {
            row["alias_key"]
            for row in await (
                await conn.execute(
                    "SELECT alias_key FROM sources.alias_suppression WHERE alias_key=ANY(%s)",
                    (all_keys,),
                )
            ).fetchall()
        }
        for record, keys, key, occurred_at in prepared:
            related = [row for row in bound if row["alias_key"] in keys]
            denied = bool(set(keys) & suppressed) or any(
                row["state"] in {"forgotten", "retracted"}
                or row["version_state"] in {"forgotten", "retracted", "unsupported"}
                or row["fragment_forgotten"]
                for row in related
            )
            result = {
                "locator": record["locator"],
                "allowed": False,
                "source_ref": None,
                "reason": None,
            }
            if denied:
                result["reason"] = "source_withdrawn"
                results.append(result)
                continue
            existing = await (
                await conn.execute(
                    "SELECT a.*,o.head_revision,o.state FROM sources.archive_records a JOIN sources.originals o USING(source_id) "
                    "WHERE a.archive_key=%s",
                    (key,),
                )
            ).fetchone()
            if (not explicit_request and occurred_at < generation["created_at"]
                and not (existing and existing["explicit_authorized"]
                         and existing["content_hash"] == record["stored_content_sha256"])):
                results.append({**result, "reason": "archive_before_installation"})
                continue
            if existing and existing["content_hash"] == record["stored_content_sha256"]:
                ref = SourceRef(existing["source_id"], existing["head_revision"], {})
                allowed = (await service.check_many_on(conn, context, ((ref,),)))[0]
                if allowed and explicit_request and not existing["explicit_authorized"]:
                    await conn.execute(
                        "UPDATE sources.archive_records SET explicit_authorized=true WHERE archive_key=%s",
                        (key,),
                    )
                results.append(
                    {
                        **result,
                        "allowed": allowed,
                        "source_ref": ref if allowed else None,
                        "reason": None if allowed else "source_unavailable",
                    }
                )
                continue
            from .dependencies import unique_refs

            supports = unique_refs(
                tuple(
                    SourceRef(row["source_id"], row["revision"], {})
                    for row in related
                    if row["state"] == "active"
                    and row["head_revision"] == row["revision"]
                    and (existing is None or row["source_id"] != existing["source_id"])
                )
            )
            if supports and not (await service.check_many_on(conn, context, (supports,)))[0]:
                results.append({**result, "reason": "native_source_unavailable"})
                continue
            ref = await service._register(
                conn,
                write_context,
                epochs,
                key,
                existing["head_revision"] + 1 if existing else 1,
                {
                    "archive": {
                        "mapping_id": mapping_id,
                        "channel": channel,
                        "locator": record["locator"],
                        "stored_content_sha256": record["stored_content_sha256"],
                        "sender_id": record["sender_id"],
                        "native_aliases": record["native_aliases"],
                        "coverage": record.get("coverage"),
                        "observation_id": record.get("archive_observation_id"),
                        **{
                            field: record.get(field)
                            for field in (
                                "source_sequence",
                                "external_message_id",
                                "message_id",
                                "media_state",
                            )
                        },
                    }
                },
                occurred_at,
                OriginKind.TOOL_RESULT,
                record["text"],
                None,
                "archive:" + body_hash({"channel": channel, "locator": record["locator"]}),
                Coverage(True, representation="stored_text"),
                (supports,) if supports else (),
                locks_prepared=True,
            )
            await conn.execute(
                "INSERT INTO sources.archive_records(archive_key,source_id,content_hash,alias_keys,explicit_authorized) "
                "VALUES(%s,%s,%s,%s,%s) ON CONFLICT(archive_key) DO UPDATE SET "
                "content_hash=excluded.content_hash,alias_keys=excluded.alias_keys,"
                "explicit_authorized=sources.archive_records.explicit_authorized OR excluded.explicit_authorized",
                (key, ref.source_id, record["stored_content_sha256"], list(keys), explicit_request),
            )
            for alias in keys:
                await conn.execute(
                    "INSERT INTO sources.source_aliases VALUES(%s,%s,%s) ON CONFLICT DO NOTHING",
                    (alias, ref.source_id, ref.revision),
                )
            results.append({**result, "allowed": True, "source_ref": ref})
    return tuple(results)
