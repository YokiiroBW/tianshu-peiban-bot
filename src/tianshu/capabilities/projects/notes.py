"""Versioned research notes retain statements, inferences and explicit decisions separately."""

from uuid import NAMESPACE_URL, uuid5
from tianshu.runtime.serialization import wire
from tianshu.sources.dependencies import unique_refs
from ..contracts import Fault, canonical, digest, require
from .primitives import text, integer, strings, refs


def validate_note(payload):
    require(
        isinstance(payload, dict)
        and set(payload)
        == {"question", "source_statements", "inferences", "open_questions", "decision"},
        "invalid_research_note",
        400,
    )
    text(payload["question"], 2000)
    statements = payload["source_statements"]
    require(isinstance(statements, list) and 1 <= len(statements) <= 16, "evidence_required", 400)
    for statement in statements:
        require(set(statement) == {"kind", "statement", "source"}, "invalid_source_statement", 400)
        text(statement["kind"], 64)
        text(statement["statement"], 2000)
    strings(payload["inferences"], 2000, 16)
    strings(payload["open_questions"], 2000, 16)
    if payload["decision"] is not None:
        decision = payload["decision"]
        require(set(decision) == {"summary", "basis"}, "invalid_decision", 400)
        text(decision["summary"], 2000)
        require(
            isinstance(decision["basis"], list) and 1 <= len(decision["basis"]) <= 16,
            "decision_basis_required",
            400,
        )
        for item in decision["basis"]:
            require(
                set(item) == {"kind", "reference"} and item["kind"] in {"unit", "note"},
                "invalid_decision_basis",
                400,
            )


class Notes:
    def __init__(self, store, documents):
        self.store, self.documents = store, documents

    async def row(self, project, note_id, version=None):
        async with self.store.database.transaction(read_only=True) as conn:
            row = await (
                await conn.execute(
                    "SELECT n.*,v.version,v.payload,v.citations,v.source_refs,v.sha256,v.state AS version_state,v.observed_at FROM capability_projects.notes n JOIN capability_projects.note_versions v ON v.note_id=n.note_id AND v.version=COALESCE(%s,n.current_version) WHERE n.project_id=%s AND n.note_id=%s",
                    (version, project["project_id"], note_id),
                )
            ).fetchone()
        require(row is not None, "note_unavailable", 404)
        return row

    async def resolve(self, context, project, reference, *, visited=(), budget=None):
        budget = budget if budget is not None else [4096]
        budget[0] -= 1
        require(budget[0] >= 0 and len(visited) < 64, "note_citation_budget", 409)
        note_id = text(reference.get("note_id"), 128)
        require(note_id not in visited, "note_citation_cycle", 409)
        row = await self.row(project, note_id, integer(reference.get("version"), 1))
        require(
            row["version"] == row["current_version"]
            and row["state"] == row["version_state"] == "active"
            and row["sha256"] == reference["sha256"],
            "stale_note_citation",
            409,
        )
        await self.store.readable(context, row["source_refs"])
        for citation in row["citations"]:
            if citation["kind"] == "note":
                await self.resolve(
                    context,
                    project,
                    citation["reference"],
                    visited=(*visited, note_id),
                    budget=budget,
                )
            else:
                await self.documents.evidence(context, project, citation["reference"])
        return refs(row["source_refs"])

    async def citations(self, context, project, payload, *, note_id=None):
        citations = [
            {"kind": "unit", "reference": value["source"]} for value in payload["source_statements"]
        ]
        if payload["decision"]:
            citations += payload["decision"]["basis"]
        dependencies = []
        for item in citations:
            if item["kind"] == "note":
                dependencies.extend(
                    await self.resolve(
                        context, project, item["reference"], visited=(note_id,) if note_id else ()
                    )
                )
            else:
                selected = await self.documents.evidence(context, project, item["reference"])
                dependencies.extend(refs(selected["source_refs"]))
        return citations, unique_refs(dependencies)

    async def save(self, action):
        body = action.arguments
        project = await self.store.project(action.context, body["project_id"])
        payload = body["note"]
        validate_note(payload)
        identifier = body.get("note_id") or str(
            uuid5(NAMESPACE_URL, "project-note:" + action.action_id)
        )
        expected = integer(body.get("expected_revision", 0), 0)
        citations, dependencies = await self.citations(
            action.context, project, payload, note_id=identifier
        )
        signature = digest([payload, citations, wire(dependencies)])
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
                    "SELECT * FROM capability_projects.notes WHERE note_id=%s FOR UPDATE",
                    (identifier,),
                )
            ).fetchone()
            if old and old["last_action_id"] == action.action_id:
                return {
                    "note_id": identifier,
                    "revision": old["current_version"],
                    "version": old["current_version"],
                    "sha256": signature,
                    "source_refs": wire(dependencies),
                }
            require(
                not old or old["project_id"] == project["project_id"], "note_project_mismatch", 403
            )
            require((old["current_version"] if old else 0) == expected, "revision_conflict", 409)
            version = expected + 1
            await conn.execute(
                "INSERT INTO capability_projects.notes VALUES(%s,%s,%s,'active',%s) ON CONFLICT(note_id) DO UPDATE SET current_version=excluded.current_version,state='active',last_action_id=excluded.last_action_id",
                (identifier, project["project_id"], version, action.action_id),
            )
            await conn.execute(
                "INSERT INTO capability_projects.note_versions(note_id,version,payload,citations,source_refs,sha256,state,action_id) VALUES(%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s,'active',%s)",
                (
                    identifier,
                    version,
                    canonical(payload),
                    canonical(citations),
                    canonical(wire(dependencies)),
                    signature,
                    action.action_id,
                ),
            )
            await self.store.dependency(conn, "note", identifier + ":" + str(version), dependencies)
            await conn.execute(
                "UPDATE capability_projects.projects SET content_revision=content_revision+1 WHERE project_id=%s",
                (project["project_id"],),
            )
        return {
            "note_id": identifier,
            "version": version,
            "revision": version,
            "sha256": signature,
            "source_refs": wire(dependencies),
        }

    async def get(self, context, body):
        project = await self.store.project(context, body["project_id"])
        row = await self.row(project, body["note_id"], body.get("version"))
        await self.store.readable(context, row["source_refs"])
        current = True
        reason = None
        try:
            await self.resolve(
                context,
                project,
                {"note_id": row["note_id"], "version": row["version"], "sha256": row["sha256"]},
            )
        except Fault as error:
            current = False
            reason = error.code
        return {
            **row,
            "revision": row["version"],
            "current": current,
            "citation_state": reason or "current",
            "reference": {
                "note_id": row["note_id"],
                "version": row["version"],
                "sha256": row["sha256"],
            },
        }

    async def list(self, context, body):
        project = await self.store.project(context, body["project_id"])
        limit = integer(body.get("limit", 16), 1, 32)
        after = self.store.position(context, project, "notes", body.get("cursor"))
        async with self.store.database.transaction(read_only=True) as conn:
            rows = await (
                await conn.execute(
                    "SELECT note_id FROM capability_projects.notes WHERE project_id=%s AND (%s::text IS NULL OR note_id>%s) ORDER BY note_id LIMIT %s",
                    (project["project_id"], after, after, limit + 1),
                )
            ).fetchall()
        items = []
        omitted = []
        for row in rows[:limit]:
            try:
                value = await self.get(context, {**body, "note_id": row["note_id"]})
                if (
                    not body.get("query")
                    or body["query"].casefold() in canonical(value["payload"]).casefold()
                ):
                    items.append(value)
            except Fault as error:
                omitted.append(error.code)
        cursor = (
            self.store.cursor(context, project, "notes", rows[limit - 1]["note_id"])
            if len(rows) > limit
            else None
        )
        return self.store.page(items, len(rows) > limit, cursor, missing=tuple(set(omitted)))

    async def withdraw(self, action):
        body = action.arguments
        project = await self.store.project(action.context, body["project_id"])
        row = await self.row(project, body["note_id"])
        expected = integer(body["expected_revision"], 1)
        async with self.store.database.transaction() as conn:
            await self.store.writable(conn, action.context, refs(row["source_refs"]))
            await self.store.lock_project(conn, action.context, project)
            if row["last_action_id"] == action.action_id:
                return {
                    "note_id": row["note_id"],
                    "version": row["current_version"],
                    "state": "withdrawn",
                }
            updated = await (
                await conn.execute(
                    "UPDATE capability_projects.notes SET state='withdrawn',current_version=current_version+1,last_action_id=%s WHERE note_id=%s AND project_id=%s AND current_version=%s RETURNING *",
                    (action.action_id, body["note_id"], project["project_id"], expected),
                )
            ).fetchone()
            require(updated, "revision_conflict", 409)
            await conn.execute(
                "INSERT INTO capability_projects.note_versions(note_id,version,payload,citations,source_refs,sha256,state,action_id) VALUES(%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s,'withdrawn',%s)",
                (
                    row["note_id"],
                    expected + 1,
                    canonical(row["payload"]),
                    canonical(row["citations"]),
                    canonical(row["source_refs"]),
                    digest([row["sha256"], "withdrawn"]),
                    action.action_id,
                ),
            )
            await self.store.dependency(
                conn, "note", row["note_id"] + ":" + str(expected + 1), row["source_refs"]
            )
            await conn.execute(
                "UPDATE capability_projects.projects SET content_revision=content_revision+1 WHERE project_id=%s",
                (project["project_id"],),
            )
        return {
            "note_id": body["note_id"],
            "version": expected + 1,
            "revision": expected + 1,
            "state": "withdrawn",
            "source_refs": row["source_refs"],
        }
