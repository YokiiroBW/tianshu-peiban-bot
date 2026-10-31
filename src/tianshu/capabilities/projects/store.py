"""Native PG project metadata; originals and text stay in the single Sources owner."""

import base64
import json
from tianshu.runtime.serialization import wire
from tianshu.types import Page, Coverage
from ..contracts import Fault, canonical, require
from ..withdrawal import depend
from .primitives import refs


class ProjectStore:
    def __init__(self, runtime, authority, sources):
        self.database = runtime.database
        self.credentials = runtime.credentials
        self.authority = authority
        self.sources = sources

    async def project(self, context, project_id, *, include_disabled=False):
        await self.authority.check(context)
        require(context.actor_id is not None, "project_actor_required", 400)
        async with self.database.transaction(read_only=True) as conn:
            row = await (
                await conn.execute(
                    "SELECT * FROM capability_projects.projects WHERE project_id=%s AND actor_id=%s AND scope_ids<@%s AND (enabled OR %s)",
                    (project_id, context.actor_id, list(context.scope_ids), include_disabled),
                )
            ).fetchone()
        require(row is not None, "project_unavailable", 404)
        return row

    async def readable(self, context, source_refs):
        await self.authority.check(context, refs(source_refs))

    async def writable(self, conn, context, source_refs):
        selected = refs(source_refs)
        if not await self.sources.validate_persistence(conn, context, selected):
            raise Fault("source_unavailable", 410)
        return selected

    async def lock_project(self, conn, context, project):
        row = await (
            await conn.execute(
                "SELECT * FROM capability_projects.projects WHERE project_id=%s AND actor_id=%s AND scope_ids<@%s AND enabled FOR UPDATE",
                (project["project_id"], context.actor_id, list(context.scope_ids)),
            )
        ).fetchone()
        require(row is not None, "project_unavailable", 404)
        require(row["revision"] == project["revision"], "project_revision_changed", 409)
        return row

    def cursor(self, context, project, operation, position, *, binding=None):
        value = {
            "project_id": project["project_id"],
            "revision": project["content_revision"],
            "operation": operation,
            "position": position,
            "binding": binding,
        }
        raw = self.credentials.seal_bytes(
            canonical(value).encode(), domain="project-cursor", reference=context.principal_id
        )
        return base64.urlsafe_b64encode(raw).decode()

    def position(self, context, project, operation, cursor, *, binding=None):
        if cursor is None:
            return None
        require(isinstance(cursor, str) and len(cursor) <= 4096, "invalid_cursor", 400)
        try:
            raw = self.credentials.unseal_bytes(
                base64.urlsafe_b64decode(cursor),
                domain="project-cursor",
                reference=context.principal_id,
            )
            value = json.loads(raw)
        except Exception:
            raise Fault("invalid_cursor", 400) from None
        require(
            value["project_id"] == project["project_id"]
            and value["operation"] == operation
            and value.get("binding") == binding,
            "invalid_cursor",
            400,
        )
        require(value["revision"] == project["content_revision"], "cursor_stale", 409)
        return value["position"]

    async def dependency(self, conn, kind, identifier, source_refs):
        await depend(conn, "project_" + kind, identifier, refs(source_refs))

    @staticmethod
    def page(items, more, next_cursor, *, missing=()):
        return Page(
            tuple(items),
            next_cursor if more else None,
            Coverage(
                not more and not missing, missing=tuple(missing) + (("more_pages",) if more else ())
            ),
        )
