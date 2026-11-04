"""Actual registered checkout facts remain separate from index and operator declarations."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4
from tianshu.runtime.serialization import wire
from tianshu.sources.dependencies import unique_refs
from ..contracts import canonical, digest, require
from .primitives import text, integer, strings, refs
from . import workdir


class Continuation:
    def __init__(self, store, documents, notes, lessons, local, settings):
        self.store, self.documents, self.notes, self.lessons, self.local, self.settings = (
            store,
            documents,
            notes,
            lessons,
            local,
            settings,
        )

    def registered(self, project, worktree_id):
        entry = next(
            (dict(value) for value in project["worktrees"] if value["id"] == worktree_id), None
        )
        require(entry is not None, "worktree_unregistered", 403)
        entry.setdefault("max_bytes", 1048576)
        if self.settings.git_executable:
            entry["git_executable"] = self.settings.git_executable
        return entry

    async def facts(self, context, project, worktree_id):
        entry = self.registered(project, worktree_id)
        async with self.store.database.transaction(read_only=True) as conn:
            indexed = await (
                await conn.execute(
                    "SELECT d.document_id,d.current_version AS version,d.locator,CASE WHEN d.state='active' THEN 'ready' ELSE d.state END AS state,v.sha256 AS hash FROM capability_projects.documents d JOIN capability_projects.document_versions v ON v.document_id=d.document_id AND v.version=d.current_version WHERE d.project_id=%s AND d.kind='file' AND d.locator=ANY(%s)",
                    (project["project_id"], entry["files"]),
                )
            ).fetchall()
        # Bounded native Git and file IO never holds a database connection.
        return await self.local(workdir.observe, entry, indexed)

    async def state(self, context, body):
        project = await self.store.project(context, body["project_id"])
        async with self.store.database.transaction(read_only=True) as conn:
            row = await (
                await conn.execute(
                    "SELECT * FROM capability_projects.declared_states WHERE project_id=%s ORDER BY revision DESC LIMIT 1",
                    (project["project_id"],),
                )
            ).fetchone()
        if row:
            await self.store.readable(context, row["source_refs"])
            if row["state"] != "active":
                return {
                    "project_id": project["project_id"],
                    "state": None,
                    "revision": row["revision"],
                    "reason": row["state"],
                }
        return {
            "project_id": project["project_id"],
            "state": row,
            "revision": row["revision"] if row else 0,
            "kind": "operator_declared_not_executed",
        }

    async def save_state(self, action):
        body = action.arguments
        project = await self.store.project(action.context, body["project_id"])
        payload = body["state"]
        require(
            isinstance(payload, dict)
            and set(payload) <= {"goal", "constraints", "unfinished", "decisions", "verifications"},
            "invalid_project_state",
            400,
        )
        text(payload.get("goal"), 2000)
        for key in ("constraints", "unfinished", "decisions"):
            strings(payload.get(key, []), 2000, 32)
        evidence = []
        dependencies = []
        for citation in body.get("evidence", []):
            selected = await self.documents.evidence(action.context, project, citation)
            evidence.append(citation)
            dependencies.extend(refs(selected["source_refs"]))
        verifications = []
        declared = payload.get("verifications", [])
        require(
            isinstance(declared, list) and len(declared) <= 4, "verification_worktree_budget", 400
        )
        for value in declared:
            require(
                isinstance(value, dict)
                and set(value) <= {"label", "status", "worktree_id", "head"},
                "invalid_declared_verification",
                400,
            )
            text(value.get("label"), 1000)
            text(value.get("status"), 128)
            actual = await self.facts(action.context, project, value["worktree_id"])
            require(actual["head"] == value.get("head"), "verification_checkout_changed", 409)
            verifications.append(
                {
                    "declaration": value,
                    "actual_binding": {
                        "worktree_id": actual["id"],
                        "branch": actual["branch"],
                        "head": actual["head"],
                        "dirty": actual["dirty"],
                        "changes": actual["changes"],
                        "collected_at": actual["collected_at"],
                    },
                    "execution": "not_executed_here",
                }
            )
        frozen = {**payload, "verifications": verifications, "trust": "operator_declaration"}
        expected = integer(body.get("expected_revision", 0), 0)
        async with self.store.database.transaction() as conn:
            await self.store.writable(
                conn, action.context, unique_refs(action.source_refs, dependencies)
            )
            current = await self.store.lock_project(conn, action.context, project)
            require(
                current["content_revision"] == project["content_revision"],
                "project_evidence_changed",
                409,
            )
            old = await (
                await conn.execute(
                    "SELECT revision,action_id FROM capability_projects.declared_states WHERE project_id=%s ORDER BY revision DESC LIMIT 1",
                    (project["project_id"],),
                )
            ).fetchone()
            if old and old["action_id"] == action.action_id:
                return {
                    "project_id": project["project_id"],
                    "revision": old["revision"],
                    "source_refs": wire(dependencies),
                }
            require((old["revision"] if old else 0) == expected, "revision_conflict", 409)
            await conn.execute(
                "INSERT INTO capability_projects.declared_states(project_id,revision,payload,source_refs,evidence,action_id) VALUES(%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s)",
                (
                    project["project_id"],
                    expected + 1,
                    canonical(frozen),
                    canonical(wire(dependencies)),
                    canonical(evidence),
                    action.action_id,
                ),
            )
            await self.store.dependency(
                conn, "state", project["project_id"] + ":" + str(expected + 1), dependencies
            )
            await conn.execute(
                "UPDATE capability_projects.projects SET content_revision=content_revision+1 WHERE project_id=%s",
                (project["project_id"],),
            )
        return {
            "project_id": project["project_id"],
            "revision": expected + 1,
            "state": frozen,
            "source_refs": wire(dependencies),
        }

    async def recover(self, context, body):
        project = await self.store.project(context, body["project_id"])
        facts = await self.facts(context, project, body["worktree_id"])
        state = (await self.state(context, body))["state"]
        documents = await self.documents.list(
            context,
            {"project_id": project["project_id"], "limit": min(body.get("document_limit", 16), 32)},
        )
        notes = await self.notes.list(
            context,
            {"project_id": project["project_id"], "limit": min(body.get("note_limit", 4), 8)},
        )
        lessons = await self.lessons.list(
            context,
            {"project_id": project["project_id"], "limit": min(body.get("lesson_limit", 4), 8)},
        )
        ready_notes = tuple(value for value in notes.items if value.get("current"))
        ready_lessons = tuple(value for value in lessons.items if value.get("current"))
        omitted_stale = len(ready_notes) != len(notes.items) or len(ready_lessons) != len(
            lessons.items
        )
        dependencies = unique_refs(
            refs(state["source_refs"]) if state else (),
            *(
                refs(value["source_refs"])
                for value in (*documents.items, *notes.items, *lessons.items)
            ),
        )
        payload = {
            "project_id": project["project_id"],
            "project_revision": project["revision"],
            "content_revision": project["content_revision"],
            "checkout": facts,
            "index": {
                "items": wire(documents.items),
                "next_cursor": documents.next_cursor,
                "coverage": wire(documents.coverage),
                "source_validation": "not_checked",
            },
            "declared_state": state,
            "notes": {**wire(notes), "items": wire(ready_notes)},
            "lessons": {**wire(lessons), "items": wire(ready_lessons)},
            "trust": "source_material_not_instructions",
            "omissions": ["stale_evidence"] if omitted_stale else [],
        }
        limit = integer(body.get("budget_bytes", 32768), 4096, 262144)
        if len(canonical(wire(payload)).encode()) > limit:
            payload["notes"] = {
                "items": [],
                "coverage": {"complete": False, "missing": ["budget"]},
                "next_cursor": None,
            }
            payload["lessons"] = {
                "items": [],
                "coverage": {"complete": False, "missing": ["budget"]},
                "next_cursor": None,
            }
            payload["index"]["items"] = []
            payload["omissions"].append("budget")
        require(len(canonical(wire(payload)).encode()) <= limit, "continuation_budget", 413)
        pack_id = str(uuid4())
        signature = digest(wire(payload))
        async with self.store.database.transaction() as conn:
            await self.store.writable(conn, context, dependencies)
            current = await self.store.lock_project(conn, context, project)
            require(
                current["content_revision"] == project["content_revision"],
                "project_evidence_changed",
                409,
            )
            await conn.execute(
                "INSERT INTO capability_projects.continuations(pack_id,project_id,principal_id,actor_id,scope_ids,project_revision,worktree_id,facts,payload,source_refs,sha256,expires_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s,%s)",
                (
                    pack_id,
                    project["project_id"],
                    context.principal_id,
                    context.actor_id,
                    list(context.scope_ids),
                    project["content_revision"],
                    facts["id"],
                    canonical(facts),
                    canonical(wire(payload)),
                    canonical(wire(dependencies)),
                    signature,
                    datetime.now(UTC) + timedelta(minutes=15),
                ),
            )
            await self.store.dependency(conn, "continuation", pack_id, dependencies)
        return {
            "pack_id": pack_id,
            "sha256": signature,
            "expires_in": 900,
            "payload": payload,
            "source_refs": wire(dependencies),
            "budget": {"limit_bytes": limit, "used_bytes": len(canonical(wire(payload)).encode())},
        }

    async def check(self, context, body):
        project = await self.store.project(context, body["project_id"])
        async with self.store.database.transaction(read_only=True) as conn:
            pack = await (
                await conn.execute(
                    "SELECT * FROM capability_projects.continuations WHERE pack_id=%s AND project_id=%s AND principal_id=%s AND actor_id IS NOT DISTINCT FROM %s AND scope_ids<@%s",
                    (
                        body["pack_id"],
                        project["project_id"],
                        context.principal_id,
                        context.actor_id,
                        list(context.scope_ids),
                    ),
                )
            ).fetchone()
        require(pack is not None, "continuation_unavailable", 404)
        await self.store.readable(context, pack["source_refs"])
        live = await self.facts(context, project, pack["worktree_id"])
        differences = []
        for key in ("head", "branch", "dirty", "changes", "files", "registration"):
            if live[key] != pack["facts"][key]:
                differences.append(key)
        if pack["project_revision"] != project["content_revision"]:
            differences.append("project_content_revision")
        if pack["expires_at"] <= datetime.now(UTC):
            differences.append("expired")
        require(
            body.get("sha256", pack["sha256"]) == pack["sha256"], "continuation_hash_mismatch", 409
        )
        return {
            "pack_id": pack["pack_id"],
            "valid": not differences,
            "differences": differences,
            "actual_checkout": live,
            "source_refs": pack["source_refs"],
        }
