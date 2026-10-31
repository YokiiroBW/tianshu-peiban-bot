"""Project-specific lessons and separately disclosed, explicitly reviewed experience."""

from uuid import NAMESPACE_URL, uuid5
from tianshu.runtime.serialization import wire
from tianshu.sources.dependencies import unique_refs
from ..contracts import Fault, canonical, digest, require
from .primitives import text, integer, strings, refs


def validate_lesson(payload):
    require(
        isinstance(payload, dict)
        and set(payload)
        == {"trigger", "symptom", "cause", "correction", "verification", "scope", "evidence"},
        "invalid_lesson",
        400,
    )
    for key in ("trigger", "symptom", "cause", "correction", "verification"):
        text(payload[key], 2000)
    scope = payload["scope"]
    require(
        isinstance(scope, dict)
        and set(scope) == {"platform", "language", "framework", "applies_to", "excludes"},
        "invalid_lesson_scope",
        400,
    )
    for key in ("platform", "language", "framework"):
        text(scope[key], 128)
    strings(scope["applies_to"], 500, 16, 1)
    strings(scope["excludes"], 500, 16)
    require(
        isinstance(payload["evidence"], list) and 1 <= len(payload["evidence"]) <= 16,
        "evidence_required",
        400,
    )


class Lessons:
    def __init__(self, store, documents):
        self.store, self.documents = store, documents

    async def row(self, project, lesson_id, version=None):
        async with self.store.database.transaction(read_only=True) as conn:
            row = await (
                await conn.execute(
                    "SELECT l.*,v.version,v.payload,v.evidence,v.source_refs,v.sha256,v.state AS version_state,v.observed_at FROM capability_projects.lessons l JOIN capability_projects.lesson_versions v ON v.lesson_id=l.lesson_id AND v.version=COALESCE(%s,l.current_version) WHERE l.project_id=%s AND l.lesson_id=%s",
                    (version, project["project_id"], lesson_id),
                )
            ).fetchone()
        require(row is not None, "lesson_unavailable", 404)
        return row

    async def resolve(self, context, project, reference):
        row = await self.row(project, reference["lesson_id"], integer(reference["version"], 1))
        require(
            row["current_version"] == row["version"]
            and row["state"] == row["version_state"] == "active"
            and row["sha256"] == reference["sha256"],
            "stale_lesson",
            409,
        )
        await self.store.readable(context, row["source_refs"])
        for evidence in row["evidence"]:
            await self.documents.evidence(context, project, evidence)
        return row

    async def save(self, action):
        body = action.arguments
        project = await self.store.project(action.context, body["project_id"])
        payload = body["lesson"]
        validate_lesson(payload)
        evidence = payload["evidence"]
        groups = []
        for item in evidence:
            groups.append(
                refs((await self.documents.evidence(action.context, project, item))["source_refs"])
            )
        dependencies = unique_refs(*groups)
        identifier = body.get("lesson_id") or str(
            uuid5(NAMESPACE_URL, "project-lesson:" + action.action_id)
        )
        expected = integer(body.get("expected_revision", 0), 0)
        signature = digest([payload, wire(dependencies)])
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
                    "SELECT * FROM capability_projects.lessons WHERE lesson_id=%s FOR UPDATE",
                    (identifier,),
                )
            ).fetchone()
            if old and old["last_action_id"] == action.action_id:
                return {
                    "lesson_id": identifier,
                    "version": old["current_version"],
                    "revision": old["current_version"],
                    "sha256": signature,
                    "source_refs": wire(dependencies),
                }
            require(
                not old or old["project_id"] == project["project_id"],
                "lesson_project_mismatch",
                403,
            )
            require((old["current_version"] if old else 0) == expected, "revision_conflict", 409)
            version = expected + 1
            await conn.execute(
                "INSERT INTO capability_projects.lessons VALUES(%s,%s,%s,'active',%s) ON CONFLICT(lesson_id) DO UPDATE SET current_version=excluded.current_version,state='active',last_action_id=excluded.last_action_id",
                (identifier, project["project_id"], version, action.action_id),
            )
            await conn.execute(
                "INSERT INTO capability_projects.lesson_versions(lesson_id,version,payload,evidence,source_refs,sha256,state,action_id) VALUES(%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s,'active',%s)",
                (
                    identifier,
                    version,
                    canonical(payload),
                    canonical(evidence),
                    canonical(wire(dependencies)),
                    signature,
                    action.action_id,
                ),
            )
            await self.store.dependency(
                conn, "lesson", identifier + ":" + str(version), dependencies
            )
            await conn.execute(
                "UPDATE capability_projects.projects SET content_revision=content_revision+1 WHERE project_id=%s",
                (project["project_id"],),
            )
        return {
            "lesson_id": identifier,
            "version": version,
            "revision": version,
            "sha256": signature,
            "source_refs": wire(dependencies),
        }

    async def get(self, context, body):
        project = await self.store.project(context, body["project_id"])
        row = await self.row(project, body["lesson_id"], body.get("version"))
        await self.store.readable(context, row["source_refs"])
        try:
            await self.resolve(
                context,
                project,
                {"lesson_id": row["lesson_id"], "version": row["version"], "sha256": row["sha256"]},
            )
            current, reason = True, None
        except Fault as error:
            current, reason = False, error.code
        return {
            **row,
            "revision": row["version"],
            "current": current,
            "citation_state": reason or "current",
            "reference": {
                "project_id": project["project_id"],
                "lesson_id": row["lesson_id"],
                "version": row["version"],
                "sha256": row["sha256"],
            },
        }

    async def list(self, context, body):
        project = await self.store.project(context, body["project_id"])
        limit = integer(body.get("limit", 16), 1, 32)
        after = self.store.position(context, project, "lessons", body.get("cursor"))
        async with self.store.database.transaction(read_only=True) as conn:
            rows = await (
                await conn.execute(
                    "SELECT lesson_id FROM capability_projects.lessons WHERE project_id=%s AND (%s::text IS NULL OR lesson_id>%s) ORDER BY lesson_id LIMIT %s",
                    (project["project_id"], after, after, limit + 1),
                )
            ).fetchall()
        items = []
        missing = []
        for row in rows[:limit]:
            try:
                item = await self.get(context, {**body, "lesson_id": row["lesson_id"]})
                if (
                    not body.get("query")
                    or body["query"].casefold() in canonical(item["payload"]).casefold()
                ):
                    items.append(item)
            except Fault as error:
                missing.append(error.code)
        cursor = (
            self.store.cursor(context, project, "lessons", rows[limit - 1]["lesson_id"])
            if len(rows) > limit
            else None
        )
        return self.store.page(items, len(rows) > limit, cursor, missing=tuple(set(missing)))

    async def retire(self, action):
        body = action.arguments
        project = await self.store.project(action.context, body["project_id"])
        row = await self.row(project, body["lesson_id"])
        expected = integer(body["expected_revision"], 1)
        async with self.store.database.transaction() as conn:
            await self.store.writable(conn, action.context, refs(row["source_refs"]))
            await self.store.lock_project(conn, action.context, project)
            if row["last_action_id"] == action.action_id:
                return {
                    "lesson_id": row["lesson_id"],
                    "version": row["current_version"],
                    "state": "retired",
                }
            changed = await (
                await conn.execute(
                    "UPDATE capability_projects.lessons SET state='retired',current_version=current_version+1,last_action_id=%s WHERE lesson_id=%s AND project_id=%s AND current_version=%s RETURNING current_version",
                    (action.action_id, row["lesson_id"], project["project_id"], expected),
                )
            ).fetchone()
            require(changed, "revision_conflict", 409)
            await conn.execute(
                "INSERT INTO capability_projects.lesson_versions(lesson_id,version,payload,evidence,source_refs,sha256,state,action_id) VALUES(%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s,'retired',%s)",
                (
                    row["lesson_id"],
                    expected + 1,
                    canonical(row["payload"]),
                    canonical(row["evidence"]),
                    canonical(row["source_refs"]),
                    digest([row["sha256"], "retired"]),
                    action.action_id,
                ),
            )
            await self.store.dependency(
                conn, "lesson", row["lesson_id"] + ":" + str(expected + 1), row["source_refs"]
            )
            await conn.execute(
                "UPDATE capability_projects.projects SET content_revision=content_revision+1 WHERE project_id=%s",
                (project["project_id"],),
            )
        return {
            "lesson_id": row["lesson_id"],
            "version": expected + 1,
            "revision": expected + 1,
            "state": "retired",
            "source_refs": row["source_refs"],
        }
