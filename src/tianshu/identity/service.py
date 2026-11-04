"""Verify requested targets against real principals, actors, conversations and scope grants."""

from dataclasses import replace
from uuid import uuid4
from hashlib import sha256

from tianshu.runtime.api import CommandResult, Page
from tianshu.runtime.commands import body_hash
from tianshu.runtime.errors import AppError
from tianshu.runtime.serialization import wire
from tianshu.types import Coverage, OriginKind, TrustedContext
from .types import AdmissionDecision, SceneAuthority


def identifier(prefix):
    return prefix + "_" + uuid4().hex


def require_permission(permissions, permission):
    if permission and permission not in permissions and "*" not in permissions:
        raise AppError("forbidden", status=403)


class IdentityService:
    def __init__(self, database, commands, *, sessions=None):
        self.database, self.commands, self.sessions = database, commands, sessions
        self.sources = None

    def bind_sources(self, sources):
        if self.sources is not None:
            raise RuntimeError("source_authority_already_bound")
        self.sources = sources

    @staticmethod
    def scene_id(context):
        """Identify the scene independently of its viewers and their authority."""
        if context.conversation_id:
            identity = {"kind": "conversation", "actor_id": context.actor_id,
                        "conversation_id": context.conversation_id}
        elif context.actor_id:
            identity = {"kind": "actor", "actor_id": context.actor_id,
                        "person_id": context.person_id}
        else:
            identity = {"kind": "system", "principal_id": context.principal_id}
        return "scene_" + body_hash(identity)[:48]

    async def scene_authority_on(self, connection, context):
        """Read a scene's existing authority in the caller's transaction."""
        await self.check_context(context, connection=connection)
        rows = await (await connection.execute(
            "SELECT scope_id,access_epoch,intake_epoch FROM identity.scopes "
            "WHERE scope_id=ANY(%s) ORDER BY scope_id", (list(context.scope_ids),)
        )).fetchall()
        if len(rows) != len(set(context.scope_ids)):
            raise AppError("access_changed", status=409)
        return SceneAuthority(
            scene_id=self.scene_id(context), principal_id=context.principal_id,
            actor_id=context.actor_id, person_id=context.person_id,
            conversation_id=context.conversation_id, scope_ids=tuple(context.scope_ids),
            permissions=tuple(sorted(context.permissions)), principal_epoch=context.access_epoch,
            scope_access_epochs=tuple((row["scope_id"], row["access_epoch"]) for row in rows),
            scope_intake_epochs=tuple((row["scope_id"], row["intake_epoch"]) for row in rows),
        )

    async def system_context(self, principal_id, *, permission, actor_id=None, connection=None):
        """Internal automation identity resolved from an already configured principal."""
        if not isinstance(principal_id, str) or not principal_id or not permission:
            raise AppError("system_principal_required", status=403)

        async def resolve(conn):
            context = await self.context_on(
                conn, principal_id, actor_id=actor_id, permission=permission,
                origin_kind=OriginKind.SYSTEM,
            )
            if actor_id:
                await self.check_active_actor(context, connection=conn)
            return context

        if connection is not None:
            return await resolve(connection)
        async with self.database.transaction(control=True, read_only=True) as conn:
            return await resolve(conn)

    async def for_request(
        self, request, *, actor_id=None, person_id=None, conversation_id=None, permission=None
    ):
        principal = await self.sessions.authenticate(request)
        return await self.context(
            principal["principal_id"],
            actor_id=actor_id,
            person_id=person_id,
            conversation_id=conversation_id,
            permission=permission,
        )

    async def context(
        self,
        principal_id,
        *,
        actor_id=None,
        person_id=None,
        conversation_id=None,
        permission=None,
        origin_kind=OriginKind.HUMAN,
    ):
        async with self.database.transaction(read_only=True) as conn:
            return await self.context_on(
                conn,
                principal_id,
                actor_id=actor_id,
                person_id=person_id,
                conversation_id=conversation_id,
                permission=permission,
                origin_kind=origin_kind,
            )

    async def context_on(
        self,
        conn,
        principal_id,
        *,
        actor_id=None,
        person_id=None,
        conversation_id=None,
        permission=None,
        origin_kind=OriginKind.HUMAN,
    ):
        principal = await (
            await conn.execute(
                "SELECT * FROM identity.principals WHERE principal_id=%s AND enabled",
                (principal_id,),
            )
        ).fetchone()
        if not principal:
            raise AppError("unauthorized", status=401)
        permissions = set(principal["permissions"])
        publication_only = False
        if actor_id:
            actor = await (
                await conn.execute(
                    "SELECT a.*,g.permissions AS granted FROM identity.actors a "
                    "LEFT JOIN identity.actor_grants g ON g.actor_id=a.actor_id AND g.principal_id=%s "
                    "WHERE a.actor_id=%s",
                    (principal_id, actor_id),
                )
            ).fetchone()
            if not actor:
                raise AppError("actor_forbidden", status=403)
            if actor["owner_id"] != principal_id and actor["granted"] is None:
                publication_only = bool(
                    await (
                        await conn.execute(
                            "SELECT 1 FROM identity.scope_grants g JOIN "
                            "identity.scopes s USING(scope_id) WHERE g.principal_id=%s AND s.actor_id=%s AND s.kind='publication' LIMIT 1",
                            (principal_id, actor_id),
                        )
                    ).fetchone()
                )
                if not publication_only:
                    raise AppError("actor_forbidden", status=403)
                permissions = {"life.published.read", "sources.read"}
                if person_id and person_id != principal["person_id"]:
                    raise AppError("person_forbidden", status=403)
            # Owners can still manage a disabled role; execution is checked separately.
            if not actor["enabled"] and permission not in {
                None,
                "companion.manage",
                "companion.read",
                "identity.manage",
            }:
                raise AppError("actor_disabled", status=409)
            if actor["owner_id"] != principal_id and not publication_only:
                granted = set(actor["granted"] or ())
                permissions = (
                    granted
                    if "*" in permissions
                    else permissions
                    if "*" in granted
                    else permissions & granted
                )
        conversation_kind, delivery_allowed = None, False
        scope_id = None
        if conversation_id:
            conversation = await (
                await conn.execute(
                    "SELECT * FROM identity.conversations WHERE conversation_id=%s",
                    (conversation_id,),
                )
            ).fetchone()
            if not conversation or (actor_id and conversation["actor_id"] != actor_id):
                raise AppError("conversation_forbidden", status=403)
            if actor_id is None:
                return await self.context_on(
                    conn,
                    principal_id,
                    actor_id=conversation["actor_id"],
                    person_id=person_id,
                    conversation_id=conversation_id,
                    permission=permission,
                    origin_kind=origin_kind,
                )
            if person_id and conversation["person_id"] not in {None, person_id}:
                raise AppError("person_target_conflict", status=403)
            person_id = person_id or conversation["person_id"]
            conversation_kind, scope_id = conversation["kind"], conversation["scope_id"]
            if conversation["adapter"] == "web":
                delivery_allowed = True
            else:
                link = await (
                    await conn.execute(
                        "SELECT l.mode,l.reply_owner,c.event_permissions FROM identity.conversation_links l JOIN identity.connections c "
                        "USING(connection_id) WHERE l.conversation_id=%s AND c.enabled LIMIT 1",
                        (conversation_id,),
                    )
                ).fetchone()
                delivery_allowed = bool(
                    link and link["mode"] == "reply" and link["reply_owner"] == "tianshu"
                )
                if link:
                    allowed = set(link["event_permissions"])
                    permissions = allowed if "*" in permissions else permissions & allowed
                else:
                    permissions = set()
            if person_id and conversation_kind == "group":
                visible = await (
                    await conn.execute(
                        "SELECT 1 FROM identity.conversation_members WHERE conversation_id=%s "
                        "AND person_id=%s LIMIT 1",
                        (conversation_id, person_id),
                    )
                ).fetchone()
                if not visible:
                    raise AppError("person_forbidden", status=403)
        person_filter = person_id if conversation_kind != "group" else None
        rows = await (
            await conn.execute(
                "SELECT s.*,g.permissions AS granted FROM identity.scopes s "
                "JOIN identity.scope_grants g USING(scope_id) WHERE g.principal_id=%s "
                "AND (s.actor_id=%s OR (%s::text IS NULL AND s.actor_id IS NULL)) "
                "AND (NOT %s OR s.kind='publication') "
                "AND (%s::text IS NULL OR s.scope_id=%s OR s.kind='public' OR EXISTS(SELECT 1 FROM identity.scope_inclusions i "
                "WHERE i.publication_scope_id=s.scope_id AND i.conversation_scope_id=%s)) "
                "AND (%s::text IS NULL OR s.person_id=%s OR s.kind IN ('public','publication')) ORDER BY s.scope_id",
                (
                    principal_id,
                    actor_id,
                    actor_id,
                    publication_only,
                    scope_id,
                    scope_id,
                    scope_id,
                    person_filter,
                    person_filter,
                ),
            )
        ).fetchall()
        if scope_id and not any(row["scope_id"] == scope_id for row in rows):
            raise AppError("conversation_forbidden", status=403)
        if (
            person_filter
            and not publication_only
            and not any(row["person_id"] == person_id for row in rows)
        ):
            raise AppError("person_forbidden", status=403)
        if any(row["kind"] == "publication" for row in rows):
            permissions.update(("life.published.read", "sources.read"))
        require_permission(permissions, permission)
        scopes = tuple(
            row["scope_id"] for row in rows if "read" in row["granted"] or "*" in row["granted"]
        )
        return TrustedContext(
            principal_id,
            actor_id,
            person_id,
            conversation_id,
            scopes,
            scopes,
            principal["access_epoch"],
            origin_kind,
            frozenset(permissions),
            conversation_kind,
            delivery_allowed,
        )

    async def check_context(self, context, *, connection=None):
        async def check(conn):
            current = await self.context_on(
                conn,
                context.principal_id,
                actor_id=context.actor_id,
                person_id=context.person_id,
                conversation_id=context.conversation_id,
                origin_kind=context.origin_kind,
            )
            if current.access_epoch != context.access_epoch or not set(context.scope_ids) <= set(
                current.scope_ids
            ):
                raise AppError("access_changed", status=409)
            if "*" not in current.permissions and not context.permissions <= current.permissions:
                raise AppError("access_changed", status=409)
            if context.delivery_allowed and not current.delivery_allowed:
                raise AppError("delivery_access_changed", status=409)
            return current

        if connection is not None:
            return await check(connection)
        async with self.database.transaction(control=True, read_only=True) as conn:
            return await check(conn)

    async def lock_context_on(self, connection, context):
        """Hold the current grant authorities while a dependent write commits."""
        if context.actor_id:
            await connection.execute(
                "SELECT actor_id FROM identity.actors WHERE actor_id=%s FOR SHARE",
                (context.actor_id,),
            )
        if context.scope_ids:
            await connection.execute(
                "SELECT scope_id FROM identity.scopes WHERE scope_id=ANY(%s) "
                "ORDER BY scope_id FOR SHARE", (list(context.scope_ids),),
            )
        if context.conversation_id:
            await connection.execute(
                "SELECT c.connection_id FROM identity.connections c "
                "JOIN identity.conversation_links l USING(connection_id) "
                "WHERE l.conversation_id=%s ORDER BY c.connection_id FOR SHARE OF c,l",
                (context.conversation_id,),
            )
        await connection.execute(
            "SELECT principal_id FROM identity.principals WHERE principal_id=%s FOR SHARE",
            (context.principal_id,),
        )
        return await self.check_context(context, connection=connection)

    async def check(self, context, source_refs=()):
        if self.sources is None:
            await self.check_context(context)
            if source_refs:
                raise AppError("source_authority_not_bound", status=503)
        else:
            await self.sources.check(context, source_refs)

    async def check_on(self, connection, context, source_refs=()):
        if (
            not self.sources
            or not (await self.sources.check_many_on(connection, context, (tuple(source_refs),)))[0]
        ):
            raise AppError("source_unavailable", status=403)

    async def publication_scope(
        self, context, stable_id, reader_ids, conversation_ids, *, connection
    ):
        await self.lock_context_on(connection, context)
        if len(reader_ids) > 256 or len(conversation_ids) > 256:
            raise AppError("invalid_publication_audience")
        if not context.actor_id:
            # Independent projects disclose only into an already authorized system
            # scope.  This path never creates grants or widens the saved audience.
            require_permission(context.permissions, "memory.share")
            if conversation_ids:
                raise AppError("invalid_publication_audience")
            scopes, epochs = await self.intake_scope(context, connection=connection)
            readers = set(reader_ids) | {context.principal_id}
            rows = await (await connection.execute(
                "SELECT principal_id FROM identity.scope_grants WHERE scope_id=%s "
                "AND principal_id=ANY(%s) AND ('read'=ANY(permissions) OR '*'=ANY(permissions))",
                (scopes[0], sorted(readers)),
            )).fetchall()
            if {row["principal_id"] for row in rows} != readers:
                raise AppError("publication_reader_forbidden", status=403)
            return scopes, epochs
        require_permission(context.permissions, "life.manage")
        scope_id = (
            "publication_"
            + sha256(
                (context.principal_id + ":" + context.actor_id + ":" + stable_id).encode()
            ).hexdigest()[:40]
        )
        readers = set(reader_ids) | {context.principal_id}
        actual = await (
            await connection.execute(
                "SELECT principal_id FROM identity.principals WHERE enabled AND principal_id=ANY(%s)",
                (sorted(readers),),
            )
        ).fetchall()
        if {row["principal_id"] for row in actual} != readers:
            raise AppError("publication_reader_not_found")
        targets = []
        for conversation_id in sorted(set(conversation_ids)):
            target = await self.context_on(
                connection,
                context.principal_id,
                actor_id=context.actor_id,
                conversation_id=conversation_id,
            )
            row = await (
                await connection.execute(
                    "SELECT scope_id FROM identity.conversations WHERE conversation_id=%s",
                    (target.conversation_id,),
                )
            ).fetchone()
            targets.append(row["scope_id"])
        await connection.execute(
            "INSERT INTO identity.scopes(scope_id,actor_id,kind,label) VALUES(%s,%s,'publication','Published content') "
            "ON CONFLICT DO NOTHING",
            (scope_id, context.actor_id),
        )
        await connection.execute(
            "SELECT scope_id FROM identity.scopes WHERE scope_id=%s FOR UPDATE", (scope_id,)
        )
        await connection.execute("DELETE FROM identity.scope_grants WHERE scope_id=%s", (scope_id,))
        await connection.execute(
            "DELETE FROM identity.scope_inclusions WHERE publication_scope_id=%s", (scope_id,)
        )
        for principal_id in sorted(readers):
            await connection.execute(
                "INSERT INTO identity.scope_grants VALUES(%s,%s,%s)",
                (scope_id, principal_id, ["read"]),
            )
        for target_scope in targets:
            await connection.execute(
                "INSERT INTO identity.scope_inclusions VALUES(%s,%s)", (scope_id, target_scope)
            )
            await connection.execute(
                "INSERT INTO identity.scope_grants(scope_id,principal_id,permissions) SELECT %s,principal_id,ARRAY['read'] "
                "FROM identity.scope_grants WHERE scope_id=%s ON CONFLICT DO NOTHING",
                (scope_id, target_scope),
            )
        await connection.execute(
            "UPDATE identity.scopes SET access_epoch=access_epoch+1 WHERE scope_id=%s", (scope_id,)
        )
        row = await (
            await connection.execute(
                "SELECT intake_epoch FROM identity.scopes WHERE scope_id=%s", (scope_id,)
            )
        ).fetchone()
        return (scope_id,), {scope_id: row["intake_epoch"]}

    async def check_many(self, context, refs_groups):
        if self.sources is None:
            await self.check_context(context)
            return tuple(not refs for refs in refs_groups)
        return await self.sources.check_many(context, refs_groups)

    async def actor_ids(self, context):
        await self.check_context(context)
        async with self.database.transaction(read_only=True) as conn:
            rows = await (
                await conn.execute(
                    "SELECT a.actor_id FROM identity.actors a LEFT JOIN "
                    "identity.actor_grants g ON g.actor_id=a.actor_id AND g.principal_id=%s "
                    "WHERE a.owner_id=%s OR g.principal_id=%s ORDER BY a.actor_id",
                    (context.principal_id, context.principal_id, context.principal_id),
                )
            ).fetchall()
        return tuple(row["actor_id"] for row in rows)

    async def subjects_on(self, conn, context):
        await self.check_context(context, connection=conn)
        return await (
            await conn.execute(
                "SELECT DISTINCT p.person_id,p.display_name,p.revision,ARRAY(SELECT alias FROM identity.person_aliases a "
                "WHERE a.person_id=p.person_id ORDER BY alias) AS aliases FROM identity.people p "
                "WHERE p.person_id=%s OR EXISTS(SELECT 1 FROM identity.scopes s WHERE s.person_id=p.person_id AND s.scope_id=ANY(%s)) "
                "OR EXISTS(SELECT 1 FROM identity.conversation_members m "
                "WHERE m.person_id=p.person_id AND m.conversation_id=%s) ORDER BY p.person_id LIMIT 256",
                (context.person_id, list(context.scope_ids), context.conversation_id),
            )
        ).fetchall()

    async def memory_subjects_on(self, conn, context):
        """People and the actual selected group are distinct memory subjects."""
        people = tuple(await self.subjects_on(conn, context))
        group = await self.group_on(conn, context)
        return people + (
            (
                {
                    "person_id": group["subject_id"],
                    "display_name": group["label"],
                    "aliases": (),
                    "revision": group["revision"],
                    "kind": "group",
                },
            )
            if group
            else ()
        )

    async def resolve_memory_subjects_on(
        self, conn, context, subject_ids=(), *, aliases=(), include_group=True
    ):
        """Resolve actual authors/references without truncating a large group directory."""
        await self.check_context(context, connection=conn)
        selected = sorted(set(subject_ids))
        names = sorted({value.casefold() for value in aliases if isinstance(value, str) and value})
        if len(selected) > 128 or len(names) > 64:
            raise AppError("memory_identity_candidates_too_large")
        rows = await (await conn.execute(
            "SELECT p.person_id,p.display_name,p.revision,ARRAY(SELECT alias FROM identity.person_aliases a "
            "WHERE a.person_id=p.person_id ORDER BY alias) AS aliases FROM identity.people p "
            "WHERE (p.person_id=ANY(%s::text[]) OR lower(p.display_name)=ANY(%s::text[]) "
            "OR EXISTS(SELECT 1 FROM identity.person_aliases a WHERE a.person_id=p.person_id AND lower(a.alias)=ANY(%s::text[]))) "
            "AND (p.person_id=%s OR EXISTS(SELECT 1 FROM identity.scopes s WHERE s.person_id=p.person_id AND s.scope_id=ANY(%s::text[])) "
            "OR EXISTS(SELECT 1 FROM identity.conversation_members m WHERE m.person_id=p.person_id AND m.conversation_id=%s) "
            "OR EXISTS(SELECT 1 FROM identity.events e WHERE e.speaker_id=p.person_id AND e.conversation_id=%s)) "
            "ORDER BY p.person_id LIMIT 129",
            (selected, names, names, context.person_id, list(context.scope_ids), context.conversation_id,context.conversation_id),
        )).fetchall()
        if len(rows) > 128:
            raise AppError("memory_identity_candidates_too_large")
        result = tuple({**row, "subject_id": row["person_id"], "kind": "person"} for row in rows)
        actor_subject = "actor:" + context.actor_id if context.actor_id else None
        if actor_subject in selected:
            result += ({"person_id": actor_subject, "subject_id": actor_subject,
                        "display_name": context.actor_id, "aliases": (), "revision": 1, "kind": "actor"},)
        group = await self.group_on(conn, context) if include_group else None
        if group:
            result += ({"person_id": group["subject_id"], "subject_id": group["subject_id"],
                        "display_name": group["label"], "aliases": (), "revision": group["revision"], "kind": "group"},)
        return result

    async def group_on(self, conn, context):
        await self.check_context(context, connection=conn)
        if context.conversation_kind != "group" or not context.conversation_id:
            return None
        row = await (
            await conn.execute(
                "SELECT conversation_id,actor_id,label,revision,scope_id FROM identity.conversations WHERE conversation_id=%s AND actor_id=%s AND kind='group'",
                (context.conversation_id, context.actor_id),
            )
        ).fetchone()
        if not row or row["scope_id"] not in context.scope_ids:
            raise AppError("group_projection_forbidden", status=403)
        return {**row, "subject_id": "group:" + row["conversation_id"]}

    async def event_author_on(self, conn, context, producer, event_id, revision):
        """A selected person is a target; authorship comes from the login or admitted event."""
        await self.check_context(context, connection=conn)
        if await self.verify_channel_producer(conn, context, producer):
            row = await (
                await conn.execute(
                    "SELECT e.speaker_id FROM identity.events e JOIN identity.connections c USING(connection_id) WHERE e.producer=%s AND e.event_id=%s AND e.revision=%s AND e.conversation_id=%s AND c.actor_id=%s",
                    (producer, event_id, revision, context.conversation_id, context.actor_id),
                )
            ).fetchone()
            if not row or row["speaker_id"] != context.person_id:
                raise AppError("event_author_unverified", status=403)
            return row["speaker_id"]
        row = await (
            await conn.execute(
                "SELECT person_id FROM identity.principals WHERE principal_id=%s AND enabled",
                (context.principal_id,),
            )
        ).fetchone()
        return row["person_id"] if row else None

    async def authorize_actor(self, context, actor_id, *, permission=None):
        await self.check_context(context)
        if context.conversation_id and actor_id != context.actor_id:
            raise AppError("actor_target_conflict", status=403)
        selected = await self.context(
            context.principal_id,
            actor_id=actor_id,
            person_id=context.person_id if actor_id == context.actor_id else None,
            conversation_id=context.conversation_id,
            permission=permission,
            origin_kind=context.origin_kind,
        )
        return self._limit_context(selected, context)

    async def authorize_conversation(self, context, conversation_id, *, permission=None):
        await self.check_context(context)
        selected = await self.context(
            context.principal_id,
            actor_id=context.actor_id,
            person_id=context.person_id if context.conversation_id == conversation_id else None,
            conversation_id=conversation_id,
            permission=permission,
            origin_kind=context.origin_kind,
        )
        return self._limit_context(selected, context)

    async def refresh_scheduled_context(self, previous, *, permission, connection=None,
                                        require_actor=True):
        """Reauthorize a timer's author configuration, never an in-flight execution.

        The saved scene and grants are an upper bound. An epoch change alone may
        be a password/other-role edit. Newly granted scopes are not adopted, and
        losing an original scope or required permission stops the saved timer.
        Role timers require an actor by
        default; independent administrator subscriptions may retain actor=None.
        """
        async def refresh(conn):
            if require_actor and not previous.actor_id:
                raise AppError("scheduled_actor_required", status=403)
            current = await self.context_on(
                conn, previous.principal_id, actor_id=previous.actor_id,
                person_id=previous.person_id, conversation_id=previous.conversation_id,
                permission=permission, origin_kind=OriginKind.TIMER,
            )
            if not previous.actor_id and current.actor_id:
                raise AppError("scheduled_scene_changed", status=403)
            if current.actor_id:
                await self.check_active_actor(current, connection=conn)
            if not set(previous.scope_ids).issubset(current.scope_ids):
                raise AppError("scheduled_scope_unavailable", status=403)
            limited = self._limit_context(current, previous)
            require_permission(limited.permissions, permission)
            return limited

        if connection is not None:
            return await refresh(connection)
        async with self.database.transaction(read_only=True) as conn:
            return await refresh(conn)

    async def for_participation(self, context, *, source_refs=()):
        """B's explicit participation use case may select a currently reply-owned group."""
        if context.conversation_kind != "group" or not context.conversation_id:
            return None
        async with self.database.transaction(control=True, read_only=True) as conn:
            await self.check_context(context, connection=conn)
            if (
                source_refs
                and not (await self.sources.check_many_on(conn, context, (tuple(source_refs),)))[0]
            ):
                return None
            link = await (
                await conn.execute(
                    "SELECT 1 FROM identity.conversation_links l JOIN identity.connections c USING(connection_id) WHERE l.conversation_id=%s AND c.actor_id=%s AND c.enabled AND l.mode='reply' AND l.reply_owner='tianshu'",
                    (context.conversation_id, context.actor_id),
                )
            ).fetchone()
            if not link:
                return None
            current = await self.context_on(
                conn,
                context.principal_id,
                actor_id=context.actor_id,
                person_id=context.person_id,
                conversation_id=context.conversation_id,
                permission="conversation.send",
                origin_kind=OriginKind.ACTOR,
            )
            limited = self._limit_context(current, context)
            await self.check_active_actor(limited, connection=conn)
            return replace(limited, delivery_allowed=current.delivery_allowed)

    @staticmethod
    def _limit_context(current, previous):
        permissions = (
            current.permissions
            if "*" in previous.permissions
            else previous.permissions
            if "*" in current.permissions
            else current.permissions & previous.permissions
        )
        if (current.actor_id, current.conversation_id) == (
            previous.actor_id,
            previous.conversation_id,
        ):
            scopes = tuple(scope for scope in current.scope_ids if scope in previous.scope_ids)
            audience = tuple(scope for scope in current.audience if scope in previous.audience)
            return replace(
                current,
                permissions=permissions,
                scope_ids=scopes,
                audience=audience,
                delivery_allowed=current.delivery_allowed and previous.delivery_allowed,
            )
        return replace(current, permissions=permissions)

    async def check_active_actor(self, context, *, connection=None):
        async def check(conn):
            await self.check_context(context, connection=conn)
            if context.actor_id:
                row = await (
                    await conn.execute(
                        "SELECT enabled FROM identity.actors WHERE actor_id=%s", (context.actor_id,)
                    )
                ).fetchone()
                if not row or not row["enabled"]:
                    raise AppError("actor_disabled", status=409)

        if connection is not None:
            return await check(connection)
        async with self.database.transaction(control=True, read_only=True) as conn:
            return await check(conn)

    async def connection(self, context, connection_id):
        await self.check_context(context)
        async with self.database.transaction(read_only=True) as conn:
            row = await (
                await conn.execute(
                    "SELECT * FROM identity.connections WHERE connection_id=%s AND enabled",
                    (connection_id,),
                )
            ).fetchone()
        if not row or row["actor_id"] != context.actor_id:
            raise AppError("connection_forbidden", status=403)
        return row

    async def verify_channel_producer(self, conn, context, producer):
        await self.check_context(context, connection=conn)
        return bool(
            await (
                await conn.execute(
                    "SELECT c.connection_id FROM identity.connections c JOIN identity.conversation_links l "
                    "USING(connection_id) WHERE c.enabled AND (c.principal_id=%s OR EXISTS(SELECT 1 FROM identity.accounts a WHERE a.connection_id=c.connection_id AND a.principal_id=%s AND a.person_id=%s)) AND c.actor_id=%s AND l.conversation_id=%s "
                    "AND %s=c.namespace||':'||c.connection_id",
                    (
                        context.principal_id,
                        context.principal_id,
                        context.person_id,
                        context.actor_id,
                        context.conversation_id,
                        producer,
                    ),
                )
            ).fetchone()
        )

    async def archive_scene_on(self, conn, context):
        """Actual registered channel targets for this read scene, including observation-only scenes."""
        await self.check_context(context, connection=conn)
        if not context.actor_id or not context.conversation_id:
            raise AppError("archive_conversation_required", status=403)
        return tuple(
            await (
                await conn.execute(
                    "SELECT c.connection_id,c.namespace,c.self_external_id,l.external_id AS target_ref,v.kind "
                    "FROM identity.conversations v JOIN identity.conversation_links l USING(conversation_id) "
                    "JOIN identity.connections c USING(connection_id) WHERE v.conversation_id=%s "
                    "AND v.actor_id=%s AND v.scope_id=ANY(%s) AND c.enabled ORDER BY c.connection_id",
                    (context.conversation_id, context.actor_id, list(context.scope_ids)),
                )
            ).fetchall()
        )

    async def channel_context(self, connection_id, conversation_external_id):
        """Only authenticated transport owners call this for non-message events; no HTTP body endpoint."""
        async with self.database.transaction(read_only=True) as conn:
            row = await (
                await conn.execute(
                    "SELECT c.*,l.conversation_id FROM identity.connections c JOIN identity.conversation_links l "
                    "USING(connection_id) WHERE c.connection_id=%s AND c.enabled AND l.external_id=%s",
                    (connection_id, conversation_external_id),
                )
            ).fetchone()
            if not row:
                raise AppError("channel_target_forbidden", status=403)
            context = await self.context_on(
                conn,
                row["principal_id"],
                actor_id=row["actor_id"],
                conversation_id=row["conversation_id"],
                origin_kind=OriginKind.OBSERVATION,
            )
            return replace(context, delivery_allowed=False, permissions=frozenset())

    async def can_revoke_source(self, conn, context, owner_id, person_id, actor_id):
        return (
            await self.can_revoke_sources_on(conn, context, ((owner_id, person_id, actor_id),))
        )[0]

    async def can_revoke_sources_on(self, conn, context, ownership):
        await self.check_context(context, connection=conn)
        person = context.person_id
        if not person:
            row = await (
                await conn.execute(
                    "SELECT person_id FROM identity.principals WHERE principal_id=%s",
                    (context.principal_id,),
                )
            ).fetchone()
            person = row["person_id"] if row else None
        return tuple(
            owner_id == context.principal_id
            and actor_id == context.actor_id
            and ("*" in context.permissions or person_id is None or person_id == person)
            for owner_id, person_id, actor_id in ownership
        )

    async def channel_target(self, context, conversation_id):
        if not context.delivery_allowed or context.conversation_id != conversation_id:
            raise AppError("delivery_not_allowed", status=403)
        current = await self.authorize_conversation(context, conversation_id)
        await self.check_active_actor(current)
        async with self.database.transaction(read_only=True) as conn:
            row = await (
                await conn.execute(
                    "SELECT v.*,l.connection_id,c.enabled AS connection_enabled FROM identity.conversations v "
                    "LEFT JOIN identity.conversation_links l USING(conversation_id) LEFT JOIN identity.connections c USING(connection_id) "
                    "WHERE v.conversation_id=%s",
                    (conversation_id,),
                )
            ).fetchone()
        if not row or (row["adapter"] != "web" and not row["connection_enabled"]):
            raise AppError("channel_target_unavailable", status=409)
        return {
            "conversation_id": conversation_id,
            "actor_id": current.actor_id,
            "adapter": row["adapter"],
            "connection_id": row["connection_id"],
            "target_ref": row["target_ref"] or "web:" + conversation_id,
            "kind": row["kind"],
            "revision": row["revision"],
            "context": current,
        }

    async def check_actor(self, context, actor_id, permission="config.publish"):
        return await self.authorize_actor(context, actor_id, permission=permission)

    async def publication_recipients(self, context, *, cursor=None, limit=100):
        """Account identities already related to this authorized actor, without private profiles."""
        await self.check_context(context)
        require_permission(context.permissions, "life.manage")
        if not context.actor_id:
            raise AppError("actor_required")
        limit = min(max(int(limit), 1), 200)
        async with self.database.transaction(control=True, read_only=True) as conn:
            rows = await (
                await conn.execute(
                    "SELECT DISTINCT p.principal_id,p.display_name FROM identity.principals p "
                    "WHERE p.enabled AND p.password_hash IS NOT NULL AND (%s::text IS NULL OR p.principal_id>%s) AND ("
                    "p.principal_id=%s OR EXISTS(SELECT 1 FROM identity.actors a WHERE a.actor_id=%s AND a.owner_id=p.principal_id) "
                    "OR EXISTS(SELECT 1 FROM identity.actor_grants g WHERE g.actor_id=%s AND g.principal_id=p.principal_id) "
                    "OR EXISTS(SELECT 1 FROM identity.scope_grants g JOIN identity.scopes s USING(scope_id) "
                    "WHERE s.actor_id=%s AND s.kind='publication' AND g.principal_id=p.principal_id)) "
                    "ORDER BY p.principal_id LIMIT %s",
                    (
                        cursor,
                        cursor,
                        context.principal_id,
                        context.actor_id,
                        context.actor_id,
                        context.actor_id,
                        limit + 1,
                    ),
                )
            ).fetchall()
        return Page(
            tuple(rows[:limit]),
            rows[limit - 1]["principal_id"] if len(rows) > limit else None,
            Coverage(len(rows) <= limit),
        )

    async def register_actor(self, context, *, connection, enabled=True):
        await self.check_context(context, connection=connection)
        require_permission(context.permissions, "companion.manage")
        actor_id = identifier("actor")
        await connection.execute(
            "INSERT INTO identity.actors(actor_id,owner_id,enabled) VALUES(%s,%s,%s)",
            (actor_id, context.principal_id, enabled),
        )
        principal = await (
            await connection.execute(
                "SELECT person_id FROM identity.principals WHERE principal_id=%s",
                (context.principal_id,),
            )
        ).fetchone()
        for kind, person in (("public", None), ("private", principal["person_id"])):
            scope = identifier("scope")
            await connection.execute(
                "INSERT INTO identity.scopes(scope_id,actor_id,person_id,kind,label) "
                "VALUES(%s,%s,%s,%s,%s)",
                (scope, actor_id, person, kind, kind),
            )
            await connection.execute(
                "INSERT INTO identity.scope_grants VALUES(%s,%s,%s)",
                (scope, context.principal_id, ["*"]),
            )
            if kind == "private":
                await connection.execute(
                    "INSERT INTO identity.conversations(conversation_id,actor_id,"
                    "scope_id,person_id,kind,label) VALUES(%s,%s,%s,%s,'private','私聊')",
                    (identifier("conversation"), actor_id, scope, person),
                )
        return actor_id

    async def set_actor_enabled(self, context, actor_id, enabled, *, connection):
        await self.check_context(context, connection=connection)
        require_permission(context.permissions, "companion.manage")
        row = await (
            await connection.execute(
                "UPDATE identity.actors SET enabled=%s,access_epoch=access_epoch+1 "
                "WHERE actor_id=%s AND owner_id=%s RETURNING access_epoch",
                (enabled, actor_id, context.principal_id),
            )
        ).fetchone()
        if not row:
            raise AppError("actor_forbidden", status=403)
        await connection.execute(
            "UPDATE identity.principals SET access_epoch=access_epoch+1 WHERE principal_id "
            "IN (SELECT owner_id FROM identity.actors WHERE actor_id=%s UNION SELECT principal_id FROM "
            "identity.actor_grants WHERE actor_id=%s)",
            (actor_id, actor_id),
        )
        return row["access_epoch"]

    async def conversations(self, context, actor_id, *, cursor=None, limit=50):
        current = await self.authorize_actor(context, actor_id)
        limit = min(max(int(limit), 1), 100)
        async with self.database.transaction(read_only=True) as conn:
            rows = await (
                await conn.execute(
                    "SELECT c.*,COALESCE(c.target_ref,'web:'||c.conversation_id) AS target_ref "
                    "FROM identity.conversations c WHERE c.actor_id=%s "
                    "AND c.scope_id=ANY(%s) AND (%s::text IS NULL OR c.conversation_id>%s) "
                    "ORDER BY c.conversation_id LIMIT %s",
                    (actor_id, list(current.scope_ids), cursor, cursor, limit + 1),
                )
            ).fetchall()
        more = len(rows) > limit
        return Page(
            tuple(rows[:limit]),
            rows[limit - 1]["conversation_id"] if more else None,
            Coverage(complete=not more),
        )

    async def scope_epochs(self, context, *, connection):
        await self.check_context(context, connection=connection)
        rows = await (
            await connection.execute(
                "SELECT scope_id,intake_epoch FROM identity.scopes WHERE scope_id=ANY(%s)",
                (list(context.scope_ids),),
            )
        ).fetchall()
        return {row["scope_id"]: row["intake_epoch"] for row in rows}

    async def support_epochs(self, scope_ids, *, connection):
        """Authority-only epoch metadata; does not grant content or original-file access."""
        rows = await (
            await connection.execute(
                "SELECT scope_id,intake_epoch FROM identity.scopes WHERE scope_id=ANY(%s)",
                (list(scope_ids),),
            )
        ).fetchall()
        return {row["scope_id"]: row["intake_epoch"] for row in rows}

    async def public_scope(self, context, *, connection):
        await self.check_context(context, connection=connection)
        row = await (
            await connection.execute(
                "SELECT scope_id FROM identity.scopes WHERE actor_id=%s AND kind='public' "
                "AND scope_id=ANY(%s) ORDER BY scope_id LIMIT 1",
                (context.actor_id, list(context.scope_ids)),
            )
        ).fetchone()
        if not row:
            raise AppError("public_projection_scope_unavailable", status=403)
        return row["scope_id"]

    async def intake_scope(self, context, *, connection):
        await self.lock_context_on(connection, context)
        epochs = await self.scope_epochs(context, connection=connection)
        if context.conversation_id:
            row = await (
                await connection.execute(
                    "SELECT scope_id FROM identity.conversations WHERE conversation_id=%s",
                    (context.conversation_id,),
                )
            ).fetchone()
        else:
            row = await (
                await connection.execute(
                    "SELECT s.scope_id FROM identity.scopes s JOIN identity.principals p "
                    "ON p.principal_id=%s WHERE s.scope_id=ANY(%s) AND s.kind=CASE WHEN %s::text IS NULL THEN 'system' ELSE 'private' END "
                    "AND s.person_id=COALESCE(%s,p.person_id) ORDER BY s.scope_id LIMIT 1",
                    (
                        context.principal_id,
                        list(context.scope_ids),
                        context.actor_id,
                        context.person_id,
                    ),
                )
            ).fetchone()
        if not row or row["scope_id"] not in epochs:
            raise AppError("source_intake_scope_unavailable", status=403)
        scope = row["scope_id"]
        return (scope,), {scope: epochs[scope]}

    async def open_web_conversation(
        self, context, actor_id, *, client_request_id, expected_revision=None
    ):
        current = await self.authorize_actor(context, actor_id)

        async def open_on(conn):
            await self.check_context(current, connection=conn)
            principal = await (
                await conn.execute(
                    "SELECT person_id FROM identity.principals WHERE principal_id=%s",
                    (current.principal_id,),
                )
            ).fetchone()
            scope = await (
                await conn.execute(
                    "SELECT s.scope_id FROM identity.scopes s JOIN identity.scope_grants g "
                    "USING(scope_id) WHERE s.actor_id=%s AND s.person_id=%s AND s.kind='private' AND g.principal_id=%s "
                    "ORDER BY s.scope_id LIMIT 1",
                    (actor_id, principal["person_id"], current.principal_id),
                )
            ).fetchone()
            if not scope:
                raise AppError("private_scope_unavailable", status=409)
            await conn.execute(
                "INSERT INTO identity.conversations(conversation_id,actor_id,scope_id,person_id,kind,label) "
                "VALUES(%s,%s,%s,%s,'private','私聊') ON CONFLICT(actor_id,scope_id,kind,adapter,target_ref) DO NOTHING",
                (identifier("conversation"), actor_id, scope["scope_id"], principal["person_id"]),
            )
            row = await (
                await conn.execute(
                    "SELECT *,COALESCE(target_ref,'web:'||conversation_id) AS target_ref FROM "
                    "identity.conversations WHERE actor_id=%s AND scope_id=%s AND kind='private' AND adapter='web'",
                    (actor_id, scope["scope_id"]),
                )
            ).fetchone()
            return CommandResult(client_request_id, data=row, revision=row["revision"])

        async def replay(conn, original):
            result = await open_on(conn)
            return replace(original, data=result.data, revision=result.revision)

        return await self.commands.execute(
            current,
            "identity.web_conversation",
            client_request_id,
            body_hash({"actor_id": actor_id, "expected_revision": expected_revision}),
            open_on,
            on_replay=replay,
        )

    async def admit_event(self, connection_id, event):
        if event.occurred_at.tzinfo is None or event.revision < 1:
            raise AppError("invalid_event")
        async with self.database.transaction() as conn:
            connection = await (
                await conn.execute(
                    "SELECT * FROM identity.connections WHERE connection_id=%s AND enabled",
                    (connection_id,),
                )
            ).fetchone()
            if not connection:
                raise AppError("connection_not_authorized", status=403)
            from .intake import sender_and_target

            link, sender = await sender_and_target(conn, connection, event)
            if not link or not sender or link["kind"] != event.conversation_kind:
                return AdmissionDecision(
                    "ignore",
                    None,
                    "none",
                    "unbound_target_or_author",
                    event.event_id,
                    event.revision,
                )
            selected_principal = sender["principal_id"] or connection["principal_id"]
            try:
                context = await self.context_on(
                    conn,
                    selected_principal,
                    actor_id=connection["actor_id"],
                    conversation_id=link["conversation_id"],
                    origin_kind=OriginKind.REPLAY if event.archived else OriginKind.HUMAN,
                )
            except AppError as exc:
                if selected_principal == connection["principal_id"] or exc.status not in {401, 403}:
                    raise
                # An application account link does not grant this social target access.
                # The registered bot owner remains the narrowly limited transport caller.
                context = await self.context_on(
                    conn,
                    connection["principal_id"],
                    actor_id=connection["actor_id"],
                    conversation_id=link["conversation_id"],
                    origin_kind=OriginKind.REPLAY if event.archived else OriginKind.HUMAN,
                )
            allowed = frozenset(connection["event_permissions"])
            context = replace(
                context,
                permissions=allowed
                if "*" in context.permissions
                else context.permissions & allowed,
            )
            # Current speaker and quoted author remain different identities.
            context = replace(context, person_id=sender["person_id"])
            mode = link["mode"]
            latest = await (
                await conn.execute(
                    "SELECT max(revision) AS revision FROM identity.events WHERE "
                    "connection_id=%s AND producer=%s AND event_id=%s",
                    (connection_id, event.producer, event.event_id),
                )
            ).fetchone()
            previous_identity = await (
                await conn.execute(
                    "SELECT conversation_id,speaker_id FROM identity.events WHERE connection_id=%s "
                    "AND producer=%s AND event_id=%s LIMIT 1",
                    (connection_id, event.producer, event.event_id),
                )
            ).fetchone()
            if previous_identity and (
                previous_identity["speaker_id"] != sender["person_id"]
                or previous_identity["conversation_id"] != context.conversation_id
            ):
                raise AppError("event_identity_changed", status=409)
            if event.archived or (
                latest["revision"] is not None and latest["revision"] > event.revision
            ):
                mode = "observe" if mode != "ignore" else "ignore"
            elif (
                link["mention_required"]
                and connection["self_external_id"] not in event.mentioned_external_ids
                and not any(
                    word in part.get("text", "")
                    for word in link["reply_keywords"]
                    for part in event.parts
                    if isinstance(part, dict)
                    and part.get("kind", "text") == "text"
                    and isinstance(part.get("text"), str)
                )
            ):
                mode = "observe" if mode == "reply" else mode
            owner = link["reply_owner"]
            if event.command and event.reply_owner != "tianshu":
                owner, mode = event.reply_owner, "observe"
            context = replace(context, delivery_allowed=mode == "reply" and owner == "tianshu")
            semantic = wire(event)
            semantic.pop("archived", None)
            semantic.pop("reply_owner", None)
            payload_hash = body_hash(semantic)
            old = await (
                await conn.execute(
                    "SELECT content_hash,disposition FROM identity.events WHERE "
                    "connection_id=%s AND producer=%s AND event_id=%s AND revision=%s",
                    (connection_id, event.producer, event.event_id, event.revision),
                )
            ).fetchone()
            if old and old["content_hash"] != payload_hash:
                raise AppError("event_revision_conflict", status=409)
            if not old:
                inserted = await (
                    await conn.execute(
                        "INSERT INTO identity.events VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                        "ON CONFLICT DO NOTHING RETURNING content_hash",
                        (
                            connection_id,
                            event.producer,
                            event.event_id,
                            event.revision,
                            payload_hash,
                            context.conversation_id,
                            sender["person_id"],
                            mode,
                            event.occurred_at,
                        ),
                    )
                ).fetchone()
                if not inserted:
                    old = await (
                        await conn.execute(
                            "SELECT content_hash,disposition FROM identity.events WHERE "
                            "connection_id=%s AND producer=%s AND event_id=%s AND revision=%s",
                            (connection_id, event.producer, event.event_id, event.revision),
                        )
                    ).fetchone()
                    if old["content_hash"] != payload_hash:
                        raise AppError("event_revision_conflict", status=409)
            accounts = await (
                await conn.execute(
                    "SELECT external_id,person_id FROM identity.accounts "
                    "WHERE connection_id=%s AND external_id=ANY(%s)",
                    (
                        connection_id,
                        list(event.mentioned_external_ids)
                        + [event.original_author_external_id or ""],
                    ),
                )
            ).fetchall()
            people = {row["external_id"]: row["person_id"] for row in accounts}
            original_author=people.get(event.original_author_external_id)
            if event.reply_to_event_id:
                original=await (await conn.execute(
                    "SELECT speaker_id FROM identity.events WHERE connection_id=%s AND producer=%s "
                    "AND event_id=%s AND conversation_id=%s ORDER BY revision DESC LIMIT 1",
                    (connection_id,event.producer,event.reply_to_event_id,context.conversation_id))).fetchone()
                # The outer sender's account field is not proof of a quoted author's identity.
                original_author=original["speaker_id"] if original else None
            return AdmissionDecision(
                mode,
                context,
                owner,
                "replayed_revision" if old else "policy",
                event.event_id,
                event.revision,
                bool(old),
                tuple(people[item] for item in event.mentioned_external_ids if item in people),
                original_author,
                event.reply_to_event_id,
            )
