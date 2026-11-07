"""One private-chat choice composed from Identity admission and Life contact rules."""

from tianshu.conversation.codec import signature
from tianshu.identity.management import IdentityManagement
from tianshu.identity.service import require_permission
from tianshu.runtime.api import CommandResult
from tianshu.runtime.errors import AppError


class PrivateContactPolicy:
    def __init__(self, life):
        self.life = life
        self.management = IdentityManagement(life.identity)

    async def view(self, context, *, connection=None):
        if not context.actor_id or not context.person_id or context.conversation_kind == "group":
            raise AppError("private_contact_person_required")
        async with self.life.database.transaction(read_only=True, connection=connection) as conn:
            await self.life.identity.check_context(context, connection=conn)
            links = await (await conn.execute(
                "SELECT l.*,v.label FROM identity.conversation_links l "
                "JOIN identity.conversations v USING(conversation_id) "
                "JOIN identity.connections c USING(connection_id) "
                "WHERE v.actor_id=%s AND v.person_id=%s AND v.kind='private' "
                "AND v.scope_id=ANY(%s::text[]) AND c.principal_id=%s "
                "ORDER BY l.connection_id,l.external_id",
                (context.actor_id, context.person_id, list(context.scope_ids), context.principal_id),
            )).fetchall()
            preferences = await self.life.contacts.preferences(context, connection=conn)
        can_reply = any(link["mode"] == "reply" and link["reply_owner"] == "tianshu"
                        for link in links)
        mode = ("active" if preferences["enabled"] else "passive") if can_reply or not links else "disabled"
        token = signature({"links": links, "revision": preferences["revision"],
            "edit_revision": preferences["edit_revision"], "enabled": preferences["enabled"]})
        return {"mode": mode, "token": token, "links": links,
            "contact_revision": preferences["edit_revision"],
            "can_manage": all(context.permissions & {"*", permission}
                for permission in ("identity.manage", "life.manage"))}

    async def save(self, context, body):
        if not context.actor_id or not context.person_id or context.conversation_kind == "group":
            raise AppError("private_contact_person_required")
        require_permission(context.permissions, "identity.manage")
        require_permission(context.permissions, "life.manage")
        mode = body.get("mode")
        if mode not in {"disabled", "passive", "active"}:
            raise AppError("invalid_private_contact_mode")

        async def apply(conn):
            await self.life.validate_persistence(conn, context)
            await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                ("private-contact-policy:" + context.actor_id + ":" + context.person_id,))
            current = await self.view(context, connection=conn)
            if body.get("policy_token") != current["token"]:
                raise AppError("revision_conflict", "联系设置已变化，请刷新后重试。", status=409)
            if body.get("expected_revision") != current["contact_revision"]:
                raise AppError("revision_conflict", status=409)
            if not current["links"]:
                raise AppError("private_channel_required", "此人物尚未关联外部私聊会话。", status=409)
            preferences = await self.life.contacts.preferences(context, connection=conn)
            fields = {key: preferences[key] for key in (
                "timezone", "quiet_start", "quiet_end", "cooldown_seconds", "daily_quota",
                "unanswered_limit", "responsiveness", "expiry_seconds",
            )}
            result = await self.life.save_contact_preferences(context, {
                **fields, "enabled": mode == "active",
                "expected_revision": current["contact_revision"],
                "client_request_id": body["client_request_id"] + ":contact",
            }, connection=conn)
            for index, link in enumerate(current["links"]):
                await self.management.execute(context, "conversations.link", {
                    "connection_id": link["connection_id"], "external_id": link["external_id"],
                    "kind": "private", "mode": "ignore" if mode == "disabled" else "reply",
                    "mention_required": False, "reply_owner": "tianshu",
                    "reply_keywords": link["reply_keywords"], "expected_revision": link["revision"],
                    "client_request_id": body["client_request_id"] + ":link:" + str(index),
                }, connection=conn)
            return CommandResult(body["client_request_id"], data={"mode": mode}, revision=result.revision)

        async def replay(conn, original):
            from dataclasses import replace
            return replace(original, data=await self.view(context, connection=conn))

        return await self.life.commands.execute(context, "life.private-contact-policy.save",
            body["client_request_id"], signature(body), apply, on_replay=replay)
