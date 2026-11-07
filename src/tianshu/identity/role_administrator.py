"""Explicit verified channel-person binding to an existing role administrator."""

from dataclasses import replace

from tianshu.runtime.api import CommandResult
from tianshu.runtime.commands import body_hash
from tianshu.runtime.errors import AppError

from .service import require_permission


class RoleAdministrator:
    def __init__(self, identity):
        self.identity = identity

    async def view(self, context, *, connection=None):
        require_permission(context.permissions, "identity.manage")
        if not context.actor_id or not context.person_id:
            raise AppError("administrator_person_required")
        async with self.identity.database.transaction(read_only=True, connection=connection) as conn:
            await self.identity.check_context(context, connection=conn)
            current = await (await conn.execute(
                "SELECT a.*,p.display_name AS person_name,u.display_name AS account_name "
                "FROM identity.role_administrators a JOIN identity.people p USING(person_id) "
                "JOIN identity.principals u USING(principal_id) WHERE actor_id=%s", (context.actor_id,),
            )).fetchone()
            accounts = await (await conn.execute(
                "SELECT a.connection_id,a.external_id,c.namespace FROM identity.accounts a "
                "JOIN identity.connections c USING(connection_id) WHERE c.actor_id=%s AND a.person_id=%s "
                "AND c.enabled ORDER BY a.connection_id,a.external_id", (context.actor_id, context.person_id),
            )).fetchall()
            principals = await (await conn.execute(
                "SELECT p.principal_id,p.display_name,p.username FROM identity.principals p "
                "JOIN identity.actors a ON a.actor_id=%s LEFT JOIN identity.actor_grants g "
                "ON g.actor_id=a.actor_id AND g.principal_id=p.principal_id "
                "WHERE p.enabled AND p.permissions && ARRAY['*','identity.manage'] "
                "AND (a.owner_id=p.principal_id OR g.permissions && ARRAY['*','identity.manage']) "
                "ORDER BY p.username", (context.actor_id,),
            )).fetchall()
        return {"current": current, "accounts": accounts, "principals": principals,
            "revision": current["revision"] if current else 0,
            "is_selected": bool(current and current["enabled"] and current["person_id"] == context.person_id),
            "can_manage": bool(context.permissions & {"*", "identity.users.manage"})}

    async def save(self, context, body):
        require_permission(context.permissions, "identity.manage")
        require_permission(context.permissions, "identity.users.manage")
        if not context.actor_id or not context.person_id or type(body.get("enabled")) is not bool:
            raise AppError("invalid_administrator_binding")

        async def apply(conn):
            await conn.execute("SELECT actor_id FROM identity.actors WHERE actor_id=%s FOR UPDATE", (context.actor_id,))
            current = await self.view(context, connection=conn)
            if type(body.get("expected_revision")) is not int or body["expected_revision"] != current["revision"]:
                raise AppError("revision_conflict", status=409)
            if not current["accounts"]:
                raise AppError("administrator_channel_account_required", "请先关联这个人物的真实渠道账号。", status=409)
            previous = current["current"]
            revision = current["revision"] + 1
            if body["enabled"]:
                principal_id = body.get("principal_id")
                if principal_id not in {p["principal_id"] for p in current["principals"]}:
                    raise AppError("administrator_account_forbidden", status=403)
                principal = await (await conn.execute(
                    "SELECT permissions FROM identity.principals WHERE principal_id=%s FOR SHARE", (principal_id,),
                )).fetchone()
                if "*" not in context.permissions and not set(principal["permissions"]) <= context.permissions:
                    raise AppError("permission_grant_forbidden", status=403)
                await conn.execute(
                    "INSERT INTO identity.role_administrators(actor_id,person_id,principal_id,revision) VALUES(%s,%s,%s,%s) "
                    "ON CONFLICT(actor_id) DO UPDATE SET person_id=excluded.person_id,principal_id=excluded.principal_id,revision=excluded.revision",
                    (context.actor_id, context.person_id, principal_id, revision),
                )
            else:
                if not previous or previous["person_id"] != context.person_id:
                    raise AppError("administrator_binding_not_selected", status=409)
                # Keep the revision head so revoke/rebind cannot reuse a stale CAS.
                await conn.execute("UPDATE identity.role_administrators SET enabled=false,revision=%s WHERE actor_id=%s",
                    (revision, context.actor_id))
            if body["enabled"]:
                await conn.execute("UPDATE identity.role_administrators SET enabled=true WHERE actor_id=%s", (context.actor_id,))
            return CommandResult(body["client_request_id"], data={"enabled": body["enabled"]}, revision=revision)

        async def replay(conn, original):
            return replace(original, data=await self.view(context, connection=conn))

        return await self.identity.commands.execute(context, "identity.role-administrator.save",
            body["client_request_id"], body_hash(body), apply, on_replay=replay)
