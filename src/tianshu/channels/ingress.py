"""Authenticated transport events become admitted, durable conversation input once."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid5, NAMESPACE_URL
from tianshu.types import Action, OriginKind, ServiceClass, SourceRef
from tianshu.runtime.jobs import JobSpec
from tianshu.runtime.serialization import wire
from tianshu.capabilities.contracts import canonical, digest
from tianshu.conversation.types import InputMessage, InputPart
from .contracts import ChannelError
from .onebot.messages import normalize_event
from .telegram import normalize_update


def live_eligible(event, decision, row):
    """A live transport observation is only a candidate, never reply authority."""
    return (
        not event.archived
        and not decision.duplicate
        and not row["superseded"]
        and decision.reply_owner == "tianshu"
    )


async def accept_event(service, connection_id, raw, *, ownership=None):
    connection = await service.repository.connection(connection_id)
    if not connection.get("enabled"):
        raise ChannelError("channel_disabled", 409)
    if connection["adapter"] == "telegram":
        event, _ = normalize_update(connection, raw)
    else:
        event, _ = normalize_event(
            connection,
            raw,
            reply_owner=(ownership or {}).get("reply_owner", "tianshu"),
            archived=bool(ownership is not None and not ownership.get("dispatch_completed")),
        )
    if event is None:
        identifier = "telegram-update:" + str(raw["update_id"])
        producer = connection["namespace"] + ":" + connection_id
        revision = 1
    else:
        identifier = event.event_id
        producer = event.producer
        revision = event.revision
    key = digest([connection_id, producer, identifier, revision])
    envelope = {"raw": raw, "ownership": ownership}
    payload = service.repository.seal(envelope, key + ":event")
    async with service.runtime.database.transaction() as conn:
        row = await (
            await conn.execute(
                "INSERT INTO channels.events(id,connection_id,producer,event_id,revision,payload) VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING id",
                (key, connection_id, producer, identifier, revision, payload),
            )
        ).fetchone()
        if row:
            await service.runtime.jobs.enqueue(
                conn,
                JobSpec(
                    "channels.ingest",
                    key,
                    {"event_key": key},
                    service_class=ServiceClass.INTERACTIVE,
                ),
            )
    return {
        "accepted": True,
        "event_id": identifier,
        "revision": revision,
        "duplicate": row is None,
    }


async def process_event(service, event_key):
    async with service.runtime.database.transaction(read_only=True) as conn:
        row = await (
            await conn.execute(
                "SELECT e.*,EXISTS(SELECT 1 FROM channels.events newer WHERE newer.connection_id=e.connection_id AND newer.producer=e.producer AND newer.event_id=e.event_id AND newer.revision>e.revision) AS superseded FROM channels.events e WHERE e.id=%s",
                (event_key,),
            )
        ).fetchone()
    if not row or row["state"] in {"consumed", "withdrawn"}:
        return
    connection = await service.repository.connection(row["connection_id"])
    if not connection.get("enabled"):
        raise ChannelError("channel_disabled", 409)
    envelope = service.repository.unseal(row["payload"], event_key + ":event")
    raw = envelope["raw"]
    ownership = envelope.get("ownership") or {}
    if connection["adapter"] == "telegram":
        event, parts = normalize_update(connection, raw)
    else:
        event, parts = normalize_event(
            connection,
            raw,
            reply_owner=ownership.get("reply_owner", "tianshu"),
            archived=bool(ownership and not ownership.get("dispatch_completed")),
        )
    refs = ()
    if event is None or not parts:
        await operational_event(service, connection, raw, event)
        outcome = {"disposition": "operational", "event_id": row["event_id"]}
    else:
        decision = await service.identity.admit_event(connection["id"], event)
        if decision.disposition == "ignore" or decision.context is None:
            outcome = {"disposition": "ignore", "reason": decision.reason}
        else:
            context = decision.context
            refs = ()
            # Duplicate admission is not completion of the B ingest. Repeat the
            # same stable turn until B itself has committed its source/turn work.
            checkpoint = (row.get("outcome") or {}).get("media_checkpoint")
            if checkpoint:
                from tianshu.types import MediaRef

                parts = tuple(
                    InputPart(
                        x["kind"],
                        x.get("text"),
                        MediaRef(**x["media"]) if x.get("media") else None,
                        x.get("metadata", {}),
                    )
                    for x in checkpoint["parts"]
                )
                refs = tuple(SourceRef(**x) for x in checkpoint["source_refs"])
            else:
                converted = []
                refs = []

                async def call_api(name, params):
                    if name == "__token__":
                        return await service.repository.token(connection)
                    key = digest([row["id"], name, params])
                    action = Action(
                        str(uuid5(NAMESPACE_URL, key)),
                        key,
                        "channels.read",
                        context.actor_id,
                        context,
                        {"connection_id": connection["id"], "api_name": name, "params": params},
                        OriginKind.OBSERVATION,
                    )
                    receipt = await service.actions.invoke(action, background=False)
                    if receipt.execution != "succeeded":
                        raise ChannelError(
                            receipt.error_code or "channel_media_lookup_unavailable", 503
                        )
                    return receipt.result["data"]

                for index, part in enumerate(parts):
                    if part.metadata.get("original_pending"):
                        part, source = await service.media.receive(
                            context,
                            connection,
                            part,
                            call_api,
                            event_id=event.event_id,
                            part_index=index,
                            revision=event.revision,
                        )
                        if source:
                            refs.append(source)
                    elif part.kind == "reply" and connection["adapter"] != "telegram":
                        try:
                            original = await call_api(
                                "get_msg", {"message_id": part.metadata["event_id"]}
                            )
                            if original.get(
                                "group_id"
                            ) is not None and event.conversation_external_id != "group:" + str(
                                original["group_id"]
                            ):
                                raise ChannelError("reply_origin_scope_mismatch", 403)
                            author = str(original.get("sender", {}).get("user_id", ""))
                            if event.conversation_kind == "private" and (
                                original.get("group_id") is not None
                                or author != event.sender_external_id
                            ):
                                raise ChannelError("reply_origin_scope_mismatch", 403)
                            part = replace(
                                part,
                                metadata={
                                    **part.metadata,
                                    "original_author_external_id": author,
                                    "original_message_id": str(
                                        original.get("message_id", part.metadata["event_id"])
                                    ),
                                },
                            )
                        except Exception as error:
                            part = replace(
                                part,
                                metadata={
                                    **part.metadata,
                                    "lookup_unavailable": getattr(
                                        error, "code", "reply_lookup_failed"
                                    ),
                                },
                            )
                    elif part.kind == "forward" and connection["adapter"] != "telegram":
                        expanded, extra = await expand_forward(
                            service,
                            context,
                            connection,
                            part,
                            call_api,
                            event.event_id,
                            event_revision=event.revision,
                            max_parts=max(1, 250 - len(converted) - len(parts)),
                        )
                        converted.extend(expanded)
                        refs.extend(extra)
                        continue
                    converted.append(part)
                parts = tuple(converted)
                refs = tuple(refs)
                async with service.runtime.database.transaction() as conn:
                    if not await service.sources.validate_persistence(conn, context, refs):
                        raise ChannelError("source_revoked", 410)
                    await service.repository.bind_event_sources(conn, event_key, refs)
                    await conn.execute(
                        "UPDATE channels.events SET outcome=%s::jsonb WHERE id=%s AND state<>'withdrawn'",
                        (
                            canonical(
                                {
                                    "media_checkpoint": {
                                        "parts": wire(parts),
                                        "source_refs": wire(refs),
                                    }
                                }
                            ),
                            event_key,
                        ),
                    )
            if service.conversation is None:
                raise ChannelError("conversation_ingress_not_bound", 503)
            message = InputMessage(
                event.producer,
                event.event_id,
                event.revision,
                event.occurred_at,
                row["created_at"],
                parts,
                refs,
                decision.reply_owner,
                decision.disposition == "reply" and not event.archived,
                event.command,
                context.conversation_kind or "unknown",
                autonomous_eligible=live_eligible(event, decision, row),
            )
            ingested = await service.conversation.ingest(context, message)
            registered_refs = tuple(
                item if isinstance(item, SourceRef) else SourceRef(**item)
                for item in ingested.get("source_refs", ())
            )
            refs = tuple(refs) + registered_refs
            outcome = {
                "disposition": decision.disposition,
                "event_id": event.event_id,
                "revision": event.revision,
                "duplicate_admission": decision.duplicate,
            }
    async with service.runtime.database.transaction() as conn:
        if refs and not await service.sources.validate_persistence(conn, context, refs):
            await conn.execute(
                "UPDATE channels.events SET state='withdrawn',payload=NULL,outcome=NULL WHERE id=%s",
                (event_key,),
            )
            return
        await service.repository.bind_event_sources(conn, event_key, refs)
        await conn.execute(
            "UPDATE channels.events SET state='consumed',payload=NULL,outcome=%s::jsonb WHERE id=%s AND state<>'withdrawn'",
            (canonical(outcome), event_key),
        )


async def operational_event(service, connection, raw, event):
    if connection["adapter"] == "telegram":
        subtype = next((key for key in raw if key != "update_id"), "unknown")
    else:
        subtype = str(
            raw.get("notice_type", raw.get("request_type", raw.get("meta_event_type", "unknown")))
        )
    # These are transport/provider observations, not a second Memory authority.
    await service.repository.observe(
        connection["id"],
        raw.get("post_type", "telegram_update"),
        subtype,
        {
            "event_id": event.event_id if event else str(raw.get("update_id")),
            "payload": raw,
            "observed_at": datetime.now(UTC).isoformat(),
            "verified_transport": True,
        },
    )
    if raw.get("notice_type") in {"group_recall", "friend_recall"}:
        kind = "group" if raw["notice_type"] == "group_recall" else "private"
        target = (
            "group:" + str(raw["group_id"]) if kind == "group" else "private:" + str(raw["user_id"])
        )
        context = await service.identity.channel_context(connection["id"], target)
        await service.sources.recall_event(
            context,
            connection["namespace"] + ":" + connection["id"],
            "message:" + kind + ":" + str(raw["message_id"]),
            reason="channel_recall",
        )


async def expand_forward(
    service,
    context,
    connection,
    part,
    call_api,
    event_id,
    *,
    depth=0,
    max_parts=128,
    path="root",
    event_revision=1,
):
    from .onebot.messages import input_parts, segments

    segment = part.metadata.get("segment", {})
    refs = []
    try:
        if depth > 2:
            raise ChannelError("forward_depth_budget", 413)
        if segment.get("type") == "forward":
            raw = await call_api("get_forward_msg", {"id": segment["data"]["id"]})
        elif segment.get("type") == "node":
            raw = {"messages": [segment["data"]]}
        else:
            return [part], refs
        native = canonical(raw).encode()
        if len(native) > 4 * 1024 * 1024:
            raise ChannelError("forward_byte_budget", 413)
        source = await service.sources.capture(
            context,
            locator={
                "kind": "channel_forward",
                "connection_id": connection["id"],
                "event_id": event_id,
                "forward_id": segment.get("data", {}).get("id"),
                "depth": depth,
                "path": path,
                "revision": event_revision,
                "evidence_unit": connection["namespace"] + ":" + connection["id"] + ":" + event_id,
            },
            raw=native,
            mime_type="application/json",
        )
        refs.append(source)
        nodes = raw.get("messages", raw.get("message", []))
        output = [
            InputPart(
                "forward",
                metadata={
                    "channel": "onebot.v11",
                    "forward_source_ref": wire(source),
                    "node_count": len(nodes),
                    "analysis_completed": False,
                },
            )
        ]
        for node_index, node in enumerate(nodes[:128]):
            sender = node.get("sender", {})
            author = str(sender.get("user_id", node.get("user_id", node.get("uin", ""))))
            content = node.get("content", node.get("message", []))
            for child_index, child in enumerate(input_parts(segments(content))):
                if len(output) >= max_parts:
                    output[0] = replace(
                        output[0], metadata={**output[0].metadata, "omitted": "forward_part_budget"}
                    )
                    return output, refs
                metadata = {
                    **child.metadata,
                    "original_author_external_id": author,
                    "original_author_display_name": sender.get(
                        "nickname", node.get("nickname", node.get("name"))
                    ),
                    "forward_node_index": node_index,
                    "forward_source_ref": wire(source),
                }
                child = replace(
                    child, kind="quote" if child.kind == "text" else child.kind, metadata=metadata
                )
                if child.metadata.get("original_pending"):
                    child, media_source = await service.media.receive(
                        context,
                        connection,
                        child,
                        call_api,
                        event_id=event_id,
                        part_index=path + ":" + str(node_index) + ":" + str(child_index),
                        revision=event_revision,
                    )
                    if media_source:
                        refs.append(media_source)
                if child.kind == "forward":
                    nested, nested_refs = await expand_forward(
                        service,
                        context,
                        connection,
                        child,
                        call_api,
                        event_id,
                        depth=depth + 1,
                        path=path + ":" + str(node_index) + ":" + str(child_index),
                        event_revision=event_revision,
                        max_parts=max_parts - len(output),
                    )
                    output.extend(nested)
                    refs.extend(nested_refs)
                else:
                    output.append(child)
        return output, refs
    except asyncio.CancelledError:
        raise
    except Exception as error:
        return [
            replace(
                part,
                metadata={
                    "channel": "onebot.v11",
                    "lookup_unavailable": getattr(error, "code", "forward_lookup_failed"),
                    "analysis_completed": False,
                },
            )
        ], refs
