"""First contact from an authenticated transport within its actual configured target."""

from uuid import NAMESPACE_URL, uuid5
import json

from tianshu.runtime.errors import AppError


def stable(kind, *parts):
    return (
        kind
        + "_"
        + uuid5(
            NAMESPACE_URL, "identity.intake:" + json.dumps((kind, *parts), ensure_ascii=False)
        ).hex
    )


async def sender_and_target(conn, connection, event):
    if event.producer != connection["namespace"] + ":" + connection["connection_id"]:
        raise AppError("channel_producer_mismatch", status=403)
    if (
        not isinstance(event.sender_external_id, str)
        or not 1 <= len(event.sender_external_id) <= 256
        or not isinstance(event.conversation_external_id, str)
        or not 1 <= len(event.conversation_external_id) <= 512
    ):
        raise AppError("invalid_event_identity")
    await conn.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
        ("identity.intake:" + connection["connection_id"] + ":" + event.conversation_external_id,),
    )
    link = await (
        await conn.execute(
            "SELECT l.*,c.kind FROM identity.conversation_links l JOIN identity.conversations c USING(conversation_id) WHERE connection_id=%s AND external_id=%s",
            (connection["connection_id"], event.conversation_external_id),
        )
    ).fetchone()
    if link and (link["kind"] != event.conversation_kind or link["mode"] == "ignore"):
        return link, None
    if not link and (
        event.conversation_kind != "private" or connection["default_mode"] == "ignore"
    ):
        return None, None
    if not link and event.conversation_external_id != "private:" + event.sender_external_id:
        raise AppError("private_target_author_mismatch", status=403)
    actor = await (
        await conn.execute(
            "SELECT enabled FROM identity.actors WHERE actor_id=%s", (connection["actor_id"],)
        )
    ).fetchone()
    if not actor or not actor["enabled"]:
        return None, None
    await conn.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
        ("identity.account:" + connection["connection_id"] + ":" + event.sender_external_id,),
    )
    sender = await (
        await conn.execute(
            "SELECT * FROM identity.accounts WHERE connection_id=%s AND external_id=%s",
            (connection["connection_id"], event.sender_external_id),
        )
    ).fetchone()
    if not sender:
        person_id = stable(
            "person", connection["namespace"], connection["connection_id"], event.sender_external_id
        )
        supplied = event.sender_display_name
        label = (
            supplied.strip()[:128]
            if isinstance(supplied, str) and supplied.strip()
            else event.sender_external_id[:128]
        )
        await conn.execute(
            "INSERT INTO identity.people(person_id,display_name) VALUES(%s,%s) ON CONFLICT DO NOTHING",
            (person_id, label),
        )
        sender = await (
            await conn.execute(
                "INSERT INTO identity.accounts(connection_id,external_id,person_id) VALUES(%s,%s,%s) ON CONFLICT(connection_id,external_id) DO UPDATE SET external_id=excluded.external_id RETURNING *",
                (connection["connection_id"], event.sender_external_id, person_id),
            )
        ).fetchone()
    if not link:
        scope_id = stable(
            "scope", connection["connection_id"], connection["actor_id"], sender["person_id"]
        )
        label = (
            await (
                await conn.execute(
                    "SELECT display_name FROM identity.people WHERE person_id=%s",
                    (sender["person_id"],),
                )
            ).fetchone()
        )["display_name"]
        await conn.execute(
            "INSERT INTO identity.scopes(scope_id,actor_id,person_id,kind,label) VALUES(%s,%s,%s,'private',%s) ON CONFLICT DO NOTHING",
            (scope_id, connection["actor_id"], sender["person_id"], label),
        )
        # Only the registered transport owner receives its actual private target.
        await conn.execute(
            "INSERT INTO identity.scope_grants(scope_id,principal_id,permissions) VALUES(%s,%s,ARRAY['read']) ON CONFLICT DO NOTHING",
            (scope_id, connection["principal_id"]),
        )
        conversation_id = stable(
            "conversation", connection["connection_id"], event.conversation_external_id
        )
        await conn.execute(
            "INSERT INTO identity.conversations(conversation_id,actor_id,scope_id,person_id,kind,label,adapter,target_ref) VALUES(%s,%s,%s,%s,'private',%s,%s,%s) ON CONFLICT DO NOTHING",
            (
                conversation_id,
                connection["actor_id"],
                scope_id,
                sender["person_id"],
                label,
                connection["namespace"],
                event.conversation_external_id,
            ),
        )
        link = await (
            await conn.execute(
                "INSERT INTO identity.conversation_links(connection_id,external_id,conversation_id,mode,inherited_private_policy) VALUES(%s,%s,%s,%s,true) ON CONFLICT(connection_id,external_id) DO UPDATE SET external_id=excluded.external_id RETURNING *, 'private'::text AS kind",
                (
                    connection["connection_id"],
                    event.conversation_external_id,
                    conversation_id,
                    connection["default_mode"],
                ),
            )
        ).fetchone()
    await conn.execute(
        "INSERT INTO identity.conversation_members(conversation_id,connection_id,external_id,person_id) VALUES(%s,%s,%s,%s) ON CONFLICT(conversation_id,connection_id,external_id) DO UPDATE SET person_id=excluded.person_id,last_observed_at=now()",
        (
            link["conversation_id"],
            connection["connection_id"],
            event.sender_external_id,
            sender["person_id"],
        ),
    )
    return link, sender
