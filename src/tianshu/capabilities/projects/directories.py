"""Reviewed, immutable directory plans and explicit incremental import progress."""

from uuid import NAMESPACE_URL, uuid5
from tianshu.runtime.serialization import wire
from tianshu.types import Coverage
from ..contracts import Fault, canonical, digest, require
from .file_sources import walk_directory, presence, SUFFIXES
from .documents import file_original
from .primitives import text, integer
from pathlib import Path
import hashlib


def scan(project, directory, indexed, after, limit):
    require(directory in project["directories"], "directory_not_registered", 403)
    paths, coverage = walk_directory(project, directory)
    candidates = sorted(
        "/".join(parts) for parts in paths if Path(*parts).suffix.lower() in SUFFIXES
    )
    candidates = [value for value in candidates if after is None or value > after]
    selected = candidates[:limit]
    previous = {row["locator"]: row for row in indexed}
    result = []
    for locator in selected:
        old = previous.get(locator)
        try:
            raw, _ = file_original(project, locator)
            actual = hashlib.sha256(raw).hexdigest()
            state = (
                "unchanged"
                if old and old["sha256"] == actual and old["state"] == "active"
                else "changed"
                if old
                else "new"
            )
            result.append(
                {
                    "locator": locator,
                    "sha256": actual,
                    "state": state,
                    "size_bytes": len(raw),
                    "expected_version": old["version"] if old else 0,
                }
            )
        except (Fault, OSError) as error:
            result.append(
                {
                    "locator": locator,
                    "state": "unavailable",
                    "reason": getattr(error, "code", "file_unavailable"),
                    "expected_version": old["version"] if old else 0,
                }
            )
    more = len(candidates) > limit
    # Only a complete walk can support removal candidates; import checks each again.
    if not coverage["truncated"] and after is None and not more:
        found = set(candidates)
        for locator, old in previous.items():
            if locator not in found and presence(project, directory, locator) != "present":
                result.append(
                    {
                        "locator": locator,
                        "document_id": old["document_id"],
                        "state": "removed",
                        "expected_version": old["version"],
                    }
                )
    return result, coverage, selected[-1] if more else None


class Directories:
    def __init__(self, store, documents, local):
        self.store, self.documents, self.local = store, documents, local

    async def preview(self, action):
        body = action.arguments
        project = await self.store.project(action.context, body["project_id"])
        plan_id = str(uuid5(NAMESPACE_URL, "project-plan:" + action.action_id))
        async with self.store.database.transaction(read_only=True) as conn:
            prior = await (
                await conn.execute(
                    "SELECT result FROM capability_projects.directory_plans WHERE plan_id=%s AND principal_id=%s",
                    (plan_id, action.context.principal_id),
                )
            ).fetchone()
        if prior:
            return prior["result"]
        directory = text(body.get("directory"), 1024)
        limit = integer(body.get("limit", 128), 1, 512)
        async with self.store.database.transaction(read_only=True) as conn:
            indexed = await (
                await conn.execute(
                    "SELECT d.document_id,d.locator,d.current_version AS version,d.state,v.sha256 FROM capability_projects.documents d JOIN capability_projects.document_versions v ON v.document_id=d.document_id AND v.version=d.current_version WHERE d.project_id=%s AND d.kind='file' AND starts_with(d.locator,%s) ORDER BY d.locator LIMIT 2049",
                    (project["project_id"], directory.rstrip("/") + "/"),
                )
            ).fetchall()
        manifest, observed, next_position = await self.local(
            scan, project, directory, indexed, body.get("position"), limit
        )
        coverage = Coverage(
            not next_position and not observed["truncated"] and len(indexed) <= 2048,
            missing=tuple(
                reason
                for reason, condition in (
                    ("more_files", bool(next_position)),
                    ("walk_limit", observed["truncated"]),
                    ("index_limit", len(indexed) > 2048),
                )
                if condition
            ),
        )
        plan_id = str(uuid5(NAMESPACE_URL, "project-plan:" + action.action_id))
        signature = digest([project["project_id"], project["revision"], directory, manifest])
        result = {
            "plan_id": plan_id,
            "project_id": project["project_id"],
            "project_revision": project["revision"],
            "directory": directory,
            "sha256": signature,
            "items": manifest,
            "coverage": wire(coverage),
            "next_position": next_position,
            "observation": observed,
            "imported": False,
        }
        async with self.store.database.transaction() as conn:
            await self.store.writable(conn, action.context, action.source_refs)
            await self.store.lock_project(conn, action.context, project)
            await conn.execute(
                "INSERT INTO capability_projects.directory_plans(plan_id,project_id,principal_id,directory,project_revision,manifest,sha256,coverage,result) VALUES(%s,%s,%s,%s,%s,%s::jsonb,%s,%s::jsonb,%s::jsonb) ON CONFLICT(plan_id) DO NOTHING",
                (
                    plan_id,
                    project["project_id"],
                    action.context.principal_id,
                    directory,
                    project["revision"],
                    canonical(manifest),
                    signature,
                    canonical(wire(coverage)),
                    canonical(result),
                ),
            )
        return result

    async def apply(self, action, attempt):
        body = action.arguments
        project = await self.store.project(action.context, body["project_id"])
        async with self.store.database.transaction(read_only=True) as conn:
            plan = await (
                await conn.execute(
                    "SELECT * FROM capability_projects.directory_plans WHERE plan_id=%s AND project_id=%s AND principal_id=%s",
                    (body["plan_id"], project["project_id"], action.context.principal_id),
                )
            ).fetchone()
        require(
            plan
            and plan["project_revision"] == project["revision"]
            and body.get("plan_hash") == plan["sha256"],
            "directory_plan_changed",
            409,
        )
        choices = body.get("files", [])
        removals = body.get("tombstones", [])
        require(
            isinstance(choices, list)
            and isinstance(removals, list)
            and len(choices) + len(removals) <= 512,
            "invalid_import_selection",
            400,
        )
        items = {row["locator"]: row for row in plan["manifest"]}
        require(
            all(
                value in items and items[value]["state"] in {"new", "changed", "unchanged"}
                for value in choices
            ),
            "file_not_in_plan",
            400,
        )
        require(
            all(value in items and items[value]["state"] == "removed" for value in removals),
            "removal_not_in_plan",
            400,
        )
        import_id = str(uuid5(NAMESPACE_URL, "project-import:" + action.action_id))
        async with self.store.database.transaction() as conn:
            await self.store.writable(conn, action.context, action.source_refs)
            await self.store.lock_project(conn, action.context, project)
            await conn.execute(
                "INSERT INTO capability_projects.imports(import_id,project_id,action_id,plan_id,state) VALUES(%s,%s,%s,%s,'running') ON CONFLICT(action_id) DO NOTHING",
                (import_id, project["project_id"], action.action_id, plan["plan_id"]),
            )
            prior = await (
                await conn.execute(
                    "SELECT * FROM capability_projects.imports WHERE action_id=%s",
                    (action.action_id,),
                )
            ).fetchone()
        results = list(prior["items"])
        done = {value["locator"] for value in results}
        for locator in choices:
            if locator in done:
                continue
            item = items[locator]
            result = await self.documents.import_document(
                action,
                attempt,
                {
                    "project_id": project["project_id"],
                    "kind": "file",
                    "locator": locator,
                    "expected_version": item["expected_version"],
                    "expected_hash": item["sha256"],
                },
                project=project,
            )
            results.append({"locator": locator, **result})
            await self.progress(action, project, results, "running")
        for locator in removals:
            if locator in done:
                continue
            require(
                await self.local(presence, project, plan["directory"], locator) != "present",
                "directory_file_returned",
                409,
            )
            result = await self.documents.remove(
                action,
                {
                    "project_id": project["project_id"],
                    "document_id": items[locator]["document_id"],
                    "expected_version": items[locator]["expected_version"],
                },
            )
            results.append({"locator": locator, **result})
            await self.progress(action, project, results, "running")
        return await self.progress(action, project, results, "completed")

    async def progress(self, action, project, results, state):
        from tianshu.sources.dependencies import unique_refs
        from .primitives import refs

        source_refs = unique_refs(*(refs(item.get("source_refs", ())) for item in results))
        value = {
            "project_id": project["project_id"],
            "state": state,
            "items": results,
            "source_refs": wire(source_refs),
        }
        async with self.store.database.transaction() as conn:
            await self.store.writable(conn, action.context, source_refs)
            await self.store.lock_project(conn, action.context, project)
            await conn.execute(
                "UPDATE capability_projects.imports SET items=%s::jsonb,state=%s,result=%s::jsonb,updated_at=now() WHERE action_id=%s",
                (canonical(results), state, canonical(value), action.action_id),
            )
            await self.store.dependency(conn, "import", action.action_id, source_refs)
        return value
