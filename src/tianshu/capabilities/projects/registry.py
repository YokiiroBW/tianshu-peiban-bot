"""Explicit registry of managed project roots, scan directories and checkouts."""

from pathlib import Path
from tianshu.runtime.serialization import wire
from ..contracts import canonical, require
from .primitives import text, strings, integer
from .file_sources import directory_path, file_path
from .workdir import BRANCH, IDENTIFIER, MAX_FILES


class Registry:
    def __init__(self, store, settings, local):
        self.store, self.settings, self.local = store, settings, local

    async def view(self, context, body):
        await self.store.authority.check(context)
        limit = integer(body.get("limit", 40), 1, 100)
        cursor = body.get("cursor")
        async with self.store.database.transaction(read_only=True) as conn:
            rows = await (
                await conn.execute(
                    "SELECT project_id,label,revision,content_revision,enabled FROM capability_projects.projects WHERE actor_id=%s AND scope_ids<@%s AND (%s::text IS NULL OR project_id>%s) ORDER BY project_id LIMIT %s",
                    (context.actor_id, list(context.scope_ids), cursor, cursor, limit + 1),
                )
            ).fetchall()
        result = wire(
            self.store.page(
                rows[:limit],
                len(rows) > limit,
                rows[limit - 1]["project_id"] if len(rows) > limit else None,
            )
        )
        if "*" in context.permissions or "config.publish" in context.permissions:
            result["allowed_roots"] = list(self.settings.allowed_roots)
        return result

    def configuration(self, body):
        root = Path(text(body["root"], 4096)).resolve(strict=True)
        allowed = [Path(value).resolve(strict=True) for value in self.settings.allowed_roots]
        require(
            root.is_dir() and any(root.is_relative_to(parent) for parent in allowed),
            "project_root_not_managed",
            403,
        )
        directories = strings(body.get("directories", []), 1024, 32)
        for directory in directories:
            directory_path({"root": str(root)}, directory)
        urls = strings(body.get("allowed_urls", []), 4096, 128)
        from ..content_discovery.candidates import public_url

        for url in urls:
            public_url(url)
        entries = body.get("worktrees", [])
        require(isinstance(entries, list) and len(entries) <= 16, "invalid_worktrees", 400)
        seen = set()
        for entry in entries:
            require(
                isinstance(entry, dict)
                and set(entry) <= {"id", "path", "branch", "files", "max_bytes"},
                "invalid_worktree",
                400,
            )
            require(
                IDENTIFIER.fullmatch(text(entry.get("id"), 64)) and entry["id"] not in seen,
                "invalid_worktree",
                400,
            )
            seen.add(entry["id"])
            target = Path(text(entry.get("path"), 4096)).resolve(strict=True)
            require(
                target.is_dir() and any(target.is_relative_to(parent) for parent in allowed),
                "worktree_root_not_managed",
                403,
            )
            branch = text(entry.get("branch"), 255)
            require(BRANCH.fullmatch(branch) and ".." not in branch, "invalid_branch", 400)
            selected = strings(entry.get("files", []), 1024, MAX_FILES)
            for filename in selected:
                # Registration validates spelling and current containment, not file content.
                file_path({"root": str(target)}, filename)
            integer(entry.get("max_bytes", 1048576), 1024, 8 * 1024 * 1024)
            entry["path"] = str(target)
        return str(root), directories, urls, entries

    async def save(self, action):
        context, body = action.context, dict(action.arguments)
        require(context.actor_id is not None, "project_actor_required", 400)
        root, directories, urls, worktrees = await self.local(self.configuration, body)
        identifier = text(body.get("project_id"), 128)
        expected = integer(body.get("expected_revision"), 0)
        label = text(body.get("label"), 256)
        async with self.store.database.transaction() as conn:
            await self.store.writable(conn, context, action.source_refs)
            old = await (
                await conn.execute(
                    "SELECT * FROM capability_projects.projects WHERE project_id=%s FOR UPDATE",
                    (identifier,),
                )
            ).fetchone()
            if old and old["last_action_id"] == action.action_id:
                return old
            require((old["revision"] if old else 0) == expected, "revision_conflict", 409)
            if old:
                require(
                    old["actor_id"] == context.actor_id
                    and set(old["scope_ids"]) <= set(context.scope_ids),
                    "project_unavailable",
                    404,
                )
            row = await (
                await conn.execute(
                    "INSERT INTO capability_projects.projects(project_id,actor_id,scope_ids,owner_id,label,root,directories,allowed_urls,worktrees,enabled,revision,last_action_id) VALUES(%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s,%s,%s) ON CONFLICT(project_id) DO UPDATE SET label=excluded.label,root=excluded.root,directories=excluded.directories,allowed_urls=excluded.allowed_urls,worktrees=excluded.worktrees,enabled=excluded.enabled,revision=excluded.revision,last_action_id=excluded.last_action_id,updated_at=now() RETURNING *",
                    (
                        identifier,
                        context.actor_id,
                        list(context.scope_ids),
                        context.principal_id,
                        label,
                        root,
                        canonical(directories),
                        canonical(urls),
                        canonical(worktrees),
                        body.get("enabled", True),
                        expected + 1,
                        action.action_id,
                    ),
                )
            ).fetchone()
        return row
