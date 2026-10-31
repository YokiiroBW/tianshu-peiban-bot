"""Explicit person/account/channel policy commands; scopes are created by the owner."""

from dataclasses import replace
import asyncio

from tianshu.runtime.api import CommandResult, Page
from tianshu.runtime.commands import body_hash
from tianshu.runtime.errors import AppError
from tianshu.types import Coverage
from .service import identifier, require_permission
from .passwords import password_hash
from .sessions import SessionService


def required(body, key, maximum=256):
    value = body.get(key)
    if not isinstance(value, str) or not 1 <= len(value) <= maximum:
        raise AppError("invalid_"+key)
    return value


class IdentityManagement:
    def __init__(self, identity):
        self.identity = identity

    async def people(self, context, *, cursor=None, limit=50):
        await self.identity.check_context(context)
        limit = min(max(int(limit), 1), 100)
        async with self.identity.database.transaction(read_only=True) as conn:
            rows = await (await conn.execute("SELECT DISTINCT p.* FROM identity.people p JOIN identity.scopes s "
                "USING(person_id) JOIN identity.scope_grants g USING(scope_id) WHERE g.principal_id=%s "
                "AND (%s::text IS NULL OR p.person_id>%s) ORDER BY p.person_id LIMIT %s",
                (context.principal_id, cursor, cursor, limit+1))).fetchall()
        return Page(tuple(rows[:limit]), rows[limit-1]["person_id"] if len(rows)>limit else None, Coverage(complete=len(rows)<=limit))

    async def directory(self, context):
        await self.identity.check_context(context)
        require_permission(context.permissions, "identity.manage")
        async with self.identity.database.transaction(read_only=True) as conn:
            connections = await (await conn.execute("SELECT * FROM identity.connections WHERE principal_id=%s "
                "ORDER BY connection_id", (context.principal_id,))).fetchall()
            accounts = await (await conn.execute("SELECT a.* FROM identity.accounts a JOIN identity.connections c "
                "USING(connection_id) WHERE c.principal_id=%s ORDER BY a.connection_id,a.external_id",
                (context.principal_id,))).fetchall()
            links = await (await conn.execute("SELECT l.* FROM identity.conversation_links l JOIN identity.connections c "
                "USING(connection_id) WHERE c.principal_id=%s ORDER BY l.connection_id,l.external_id",
                (context.principal_id,))).fetchall()
            scopes=await (await conn.execute("SELECT s.*,g.permissions FROM identity.scopes s JOIN identity.scope_grants g USING(scope_id) "
                "WHERE g.principal_id=%s ORDER BY s.scope_id",(context.principal_id,))).fetchall()
            principals=[]
            if "identity.users.manage" in context.permissions or "*" in context.permissions:
                principals=await (await conn.execute("SELECT principal_id,username,display_name,person_id,permissions,enabled,revision FROM identity.principals ORDER BY username LIMIT 1000")).fetchall()
        return {"connections": connections, "accounts": accounts, "conversation_links": links,"scopes":scopes,"principals":principals}

    async def execute(self, context, operation, body):
        require_permission(context.permissions, "identity.manage")
        try:
            hashed_password = await asyncio.to_thread(password_hash,body["password"]) if operation=="principals.save" and body.get("password") else None
        except ValueError:
            raise AppError("invalid_password_length") from None
        expected = body.get("expected_revision")
        if type(expected) is not int or expected < 0:
            raise AppError("expected_revision_required")
        async def apply(conn):
            await self.identity.check_context(context, connection=conn)
            if operation == "principals.save":
                require_permission(context.permissions,"identity.users.manage")
                principal_id=body.get("principal_id") or identifier("principal")
                permissions=body.get("permissions",[])
                if not isinstance(permissions,list) or any(not isinstance(value,str) or len(value)>128 for value in permissions):
                    raise AppError("invalid_principal_permissions")
                if "*" not in context.permissions and not set(permissions)<=context.permissions:
                    raise AppError("permission_grant_forbidden",status=403)
                if expected==0:
                    if not hashed_password:
                        raise AppError("password_required")
                    person_id=identifier("person")
                    await conn.execute("INSERT INTO identity.people(person_id,display_name) VALUES(%s,%s)",(person_id,required(body,"display_name",128)))
                    row=await (await conn.execute("INSERT INTO identity.principals(principal_id,username,display_name,password_hash,person_id,permissions,enabled) "
                        "VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING principal_id,username,display_name,person_id,permissions,enabled,revision",
                        (principal_id,SessionService.username(body),body["display_name"],hashed_password,person_id,permissions,bool(body.get("enabled",True))))).fetchone()
                else:
                    row=await (await conn.execute("UPDATE identity.principals SET display_name=%s,permissions=%s,enabled=%s,"
                        "password_hash=COALESCE(%s,password_hash),revision=revision+1,access_epoch=access_epoch+1,"
                        "auth_epoch=auth_epoch+CASE WHEN %s IS NOT NULL OR NOT %s THEN 1 ELSE 0 END WHERE principal_id=%s AND revision=%s "
                        "RETURNING principal_id,username,display_name,person_id,permissions,enabled,revision",
                        (required(body,"display_name",128),permissions,bool(body.get("enabled",True)),hashed_password,hashed_password,
                         bool(body.get("enabled",True)),principal_id,expected))).fetchone()
            elif operation in {"actors.grant","scopes.grant"}:
                principal_id=required(body,"principal_id")
                if not await (await conn.execute("SELECT 1 FROM identity.principals WHERE principal_id=%s AND enabled",(principal_id,))).fetchone():
                    raise AppError("principal_not_found")
                permissions=body.get("permissions",[])
                if not isinstance(permissions,list) or any(not isinstance(value,str) for value in permissions):
                    raise AppError("invalid_grant_permissions")
                if "*" not in context.permissions and not set(permissions)<=context.permissions:
                    raise AppError("permission_grant_forbidden",status=403)
                if operation=="actors.grant":
                    actor_id=required(body,"actor_id")
                    row=await (await conn.execute("UPDATE identity.actors SET access_epoch=access_epoch+1 WHERE actor_id=%s AND owner_id=%s "
                        "AND access_epoch=%s RETURNING access_epoch AS revision",(actor_id,context.principal_id,expected))).fetchone()
                    if row:
                        await conn.execute("DELETE FROM identity.actor_grants WHERE actor_id=%s AND principal_id=%s",(actor_id,principal_id))
                        if permissions:
                            await conn.execute("INSERT INTO identity.actor_grants VALUES(%s,%s,%s)",(actor_id,principal_id,permissions))
                            person=await (await conn.execute("SELECT person_id FROM identity.principals WHERE principal_id=%s",(principal_id,))).fetchone()
                            scope=await (await conn.execute("SELECT scope_id FROM identity.scopes WHERE actor_id=%s AND person_id=%s AND kind='private' "
                                "ORDER BY scope_id LIMIT 1",(actor_id,person["person_id"]))).fetchone()
                            scope_id=scope["scope_id"] if scope else identifier("scope")
                            if not scope:
                                await conn.execute("INSERT INTO identity.scopes(scope_id,actor_id,person_id,kind,label) VALUES(%s,%s,%s,'private','私聊')",
                                    (scope_id,actor_id,person["person_id"]))
                            await conn.execute("INSERT INTO identity.scope_grants VALUES(%s,%s,%s) ON CONFLICT DO NOTHING",(scope_id,principal_id,["read","write"]))
                            await conn.execute("INSERT INTO identity.scope_grants(scope_id,principal_id,permissions) SELECT scope_id,%s,ARRAY['read'] "
                                "FROM identity.scopes WHERE actor_id=%s AND kind='public' ON CONFLICT DO NOTHING",(principal_id,actor_id))
                else:
                    scope_id=required(body,"scope_id")
                    row=await (await conn.execute("UPDATE identity.scopes s SET access_epoch=access_epoch+1 WHERE s.scope_id=%s AND s.access_epoch=%s "
                        "AND EXISTS(SELECT 1 FROM identity.scope_grants g WHERE g.scope_id=s.scope_id AND g.principal_id=%s AND '*'=ANY(g.permissions)) "
                        "RETURNING access_epoch AS revision",(scope_id,expected,context.principal_id))).fetchone()
                    if row:
                        await conn.execute("DELETE FROM identity.scope_grants WHERE scope_id=%s AND principal_id=%s",(scope_id,principal_id))
                        if permissions:
                            await conn.execute("INSERT INTO identity.scope_grants VALUES(%s,%s,%s)",(scope_id,principal_id,permissions))
                await conn.execute("UPDATE identity.principals SET access_epoch=access_epoch+1 WHERE principal_id=%s",(principal_id,))
            elif operation == "people.save":
                person_id = body.get("person_id") or identifier("person")
                name = required(body, "display_name", 128)
                if expected == 0:
                    row = await (await conn.execute("INSERT INTO identity.people(person_id,display_name) VALUES(%s,%s) "
                        "ON CONFLICT DO NOTHING RETURNING *", (person_id, name))).fetchone()
                    if row:
                        scope = identifier("scope")
                        await conn.execute("INSERT INTO identity.scopes(scope_id,actor_id,person_id,kind,label) "
                            "VALUES(%s,%s,%s,'private',%s)", (scope, context.actor_id, person_id, name))
                        await conn.execute("INSERT INTO identity.scope_grants VALUES(%s,%s,%s)", (scope, context.principal_id, ["*"]))
                else:
                    visible = await (await conn.execute("SELECT 1 FROM identity.scopes s JOIN identity.scope_grants g "
                        "USING(scope_id) WHERE s.person_id=%s AND g.principal_id=%s LIMIT 1",
                        (person_id, context.principal_id))).fetchone()
                    if not visible:
                        raise AppError("person_forbidden", status=403)
                    row = await (await conn.execute("UPDATE identity.people SET display_name=%s,revision=revision+1 "
                        "WHERE person_id=%s AND revision=%s RETURNING *", (name, person_id, expected))).fetchone()
            elif operation == "connections.save":
                await self.identity.context_on(conn, context.principal_id, actor_id=body.get("actor_id"), permission="identity.manage")
                values = (required(body, "connection_id"), context.principal_id, required(body, "actor_id"),
                          required(body, "namespace"), required(body, "self_external_id"), bool(body.get("enabled", True)))
                if expected == 0:
                    row = await (await conn.execute("INSERT INTO identity.connections(connection_id,principal_id,actor_id,namespace,"
                        "self_external_id,enabled) VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING *", values)).fetchone()
                else:
                    row = await (await conn.execute("UPDATE identity.connections SET actor_id=%s,namespace=%s,self_external_id=%s,"
                        "enabled=%s,revision=revision+1 WHERE connection_id=%s AND principal_id=%s AND revision=%s RETURNING *",
                        (*values[2:], values[0], context.principal_id, expected))).fetchone()
            elif operation in {"accounts.link", "conversations.link"}:
                connection = await (await conn.execute("SELECT * FROM identity.connections WHERE connection_id=%s "
                    "AND principal_id=%s", (required(body, "connection_id"), context.principal_id))).fetchone()
                if not connection:
                    raise AppError("connection_forbidden", status=403)
                external_id = required(body, "external_id")
                if operation == "accounts.link":
                    person_id = required(body, "person_id")
                    principal_id=body.get("principal_id")
                    if principal_id:
                        linked=await (await conn.execute("SELECT person_id FROM identity.principals WHERE principal_id=%s AND enabled",(principal_id,))).fetchone()
                        if not linked or linked["person_id"]!=person_id:
                            raise AppError("account_principal_person_mismatch")
                    await self.identity.context_on(conn, context.principal_id, actor_id=connection["actor_id"],permission="identity.manage")
                    if not await (await conn.execute("SELECT 1 FROM identity.people WHERE person_id=%s",(person_id,))).fetchone():
                        raise AppError("person_not_found")
                    if "identity.users.manage" not in context.permissions and "*" not in context.permissions:
                        visible=await (await conn.execute("SELECT 1 FROM identity.scopes s JOIN identity.scope_grants g USING(scope_id) "
                            "WHERE s.person_id=%s AND g.principal_id=%s LIMIT 1",(person_id,context.principal_id))).fetchone()
                        if not visible:
                            raise AppError("person_forbidden",status=403)
                    values = (connection["connection_id"], external_id, person_id)
                    if expected == 0:
                        row = await (await conn.execute("INSERT INTO identity.accounts(connection_id,external_id,person_id,principal_id) "
                            "VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING *", (*values,principal_id))).fetchone()
                    else:
                        row = await (await conn.execute("UPDATE identity.accounts SET person_id=%s,principal_id=%s,revision=revision+1 "
                            "WHERE connection_id=%s AND external_id=%s AND revision=%s RETURNING *",
                            (person_id,principal_id, *values[:2], expected))).fetchone()
                else:
                    kind, mode = body.get("kind"), body.get("mode", "reply")
                    if kind not in {"private", "group", "project", "system"} or mode not in {"reply", "observe", "ignore"}:
                        raise AppError("invalid_conversation_policy")
                    if expected == 0:
                        scope_id, conversation_id = identifier("scope"), identifier("conversation")
                        person_id = body.get("person_id")
                        if kind == "private":
                            account = await (await conn.execute("SELECT person_id FROM identity.accounts WHERE connection_id=%s "
                                "AND external_id=%s", (connection["connection_id"], external_id))).fetchone()
                            if not account:
                                raise AppError("private_account_not_linked", status=409)
                            person_id = account["person_id"]
                            existing_scope = await (await conn.execute("SELECT scope_id FROM identity.scopes WHERE actor_id=%s "
                                "AND person_id=%s AND kind='private' ORDER BY scope_id LIMIT 1",
                                (connection["actor_id"], person_id))).fetchone()
                            if existing_scope:
                                scope_id = existing_scope["scope_id"]
                        if kind != "private" or not existing_scope:
                            await conn.execute("INSERT INTO identity.scopes(scope_id,actor_id,person_id,kind,label) VALUES(%s,%s,%s,%s,%s)",
                                (scope_id, connection["actor_id"], person_id, kind, required(body, "label")))
                        await conn.execute("INSERT INTO identity.scope_grants VALUES(%s,%s,%s) ON CONFLICT DO NOTHING", (scope_id, context.principal_id, ["*"]))
                        await conn.execute("INSERT INTO identity.conversations(conversation_id,actor_id,scope_id,person_id,kind,label,adapter,target_ref) "
                            "VALUES(%s,%s,%s,%s,%s,%s,%s,%s)", (conversation_id, connection["actor_id"], scope_id, person_id,
                            kind, body["label"], connection["namespace"], required(body, "target_ref")))
                        row = await (await conn.execute("INSERT INTO identity.conversation_links(connection_id,external_id,conversation_id,mode,"
                            "mention_required,reply_owner) VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING *",
                            (connection["connection_id"], external_id, conversation_id, mode, bool(body.get("mention_required", False)),
                             body.get("reply_owner", "tianshu")))).fetchone()
                    else:
                        row = await (await conn.execute("UPDATE identity.conversation_links SET mode=%s,mention_required=%s,reply_owner=%s,"
                            "revision=revision+1 WHERE connection_id=%s AND external_id=%s AND revision=%s RETURNING *",
                            (mode, bool(body.get("mention_required", False)), body.get("reply_owner", "tianshu"),
                             connection["connection_id"], external_id, expected))).fetchone()
            else:
                raise AppError("identity_operation_not_found", status=404)
            if not row:
                raise AppError("revision_conflict", status=409)
            if operation=="people.save" and "aliases" in body:
                aliases=body["aliases"]
                if not isinstance(aliases,list) or len(aliases)>32 or any(not isinstance(value,str) or not 1<=len(value)<=128 for value in aliases):
                    raise AppError("invalid_person_aliases")
                await conn.execute("DELETE FROM identity.person_aliases WHERE person_id=%s",(row["person_id"],))
                for alias in sorted(set(aliases)):
                    await conn.execute("INSERT INTO identity.person_aliases VALUES(%s,%s)",(row["person_id"],alias))
            if operation=="connections.save" and "event_permissions" in body:
                permissions=body["event_permissions"]
                if not isinstance(permissions,list) or any(not isinstance(value,str) or value=="*" for value in permissions):
                    raise AppError("invalid_event_permissions")
                if "*" not in context.permissions and not set(permissions)<=context.permissions:
                    raise AppError("permission_grant_forbidden",status=403)
                row=await (await conn.execute("UPDATE identity.connections SET event_permissions=%s WHERE connection_id=%s RETURNING *",
                    (permissions,row["connection_id"]))).fetchone()
            return CommandResult(body["client_request_id"], data=row, revision=row["revision"])
        async def replay(conn, original):
            await self.identity.check_context(context, connection=conn)
            # Return current directory via its independent short read, never cached personal data.
            return replace(original, data={"refresh_required": True})
        return await self.identity.commands.execute(context, "identity."+operation, body["client_request_id"],
                                                    body_hash(body), apply, on_replay=replay)
