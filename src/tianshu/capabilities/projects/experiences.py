"""Only explicitly approved multi-project lessons become a disclosed shared summary."""

from uuid import NAMESPACE_URL, uuid5
from tianshu.runtime.serialization import wire
from tianshu.sources.dependencies import unique_refs
from tianshu.types import SourceRef
from tianshu.conversation.types import InputPart
from ..contracts import canonical, require
from .primitives import text, integer, strings, refs


class Experiences:
    def __init__(self, store, lessons):
        self.store, self.lessons = store, lessons

    async def promote(self, action):
        body = action.arguments
        entry = body["entry"]
        require(
            isinstance(entry, dict)
            and set(entry)
            == {
                "title",
                "rule",
                "applicability",
                "excludes",
                "counterexamples",
                "recheck_after",
                "evidence",
            },
            "invalid_experience",
            400,
        )
        text(entry["title"], 200)
        text(entry["rule"], 2000)
        for key in ("applicability", "excludes", "counterexamples"):
            strings(entry[key], 500, 16)
        if entry["recheck_after"] is not None:
            from datetime import datetime

            value = datetime.fromisoformat(entry["recheck_after"])
            require(value.tzinfo is not None, "aware_recheck_time_required", 400)
        evidence = entry["evidence"]
        require(
            isinstance(evidence, list) and 2 <= len(evidence) <= 16,
            "multi_project_evidence_required",
            400,
        )
        projects = {}
        dependencies = []
        for reference in evidence:
            project_id = text(reference.get("project_id"), 128)
            project = projects.get(project_id) or await self.store.project(
                action.context, project_id
            )
            projects[project_id] = project
            row = await self.lessons.resolve(action.context, project, reference)
            dependencies.extend(refs(row["source_refs"]))
        require(len(projects) >= 2, "multi_project_evidence_required", 400)
        dependencies = unique_refs(dependencies)
        identifier = body.get("experience_id") or str(
            uuid5(NAMESPACE_URL, "project-experience:" + action.action_id)
        )
        expected = integer(body.get("expected_revision", 0), 0)
        payload = {key: value for key, value in entry.items() if key != "evidence"}
        async with self.store.database.transaction() as conn:
            # Sources takes its full support locks and registers disclosure first.
            disclosure = await self.store.sources.disclose(
                action.context,
                producer="projects.experience",
                event_id=identifier,
                revision=expected + 1,
                parts=(InputPart("text", text=canonical(payload)),),
                source_refs=dependencies,
                connection=conn,
            )
            all_refs = unique_refs(dependencies, (disclosure,))
            await self.store.writable(conn, action.context, all_refs)
            for project_id in sorted(projects):
                current = await self.store.lock_project(conn, action.context, projects[project_id])
                require(
                    current["content_revision"] == projects[project_id]["content_revision"],
                    "project_evidence_changed",
                    409,
                )
            old = await (
                await conn.execute(
                    "SELECT * FROM capability_projects.experiences WHERE experience_id=%s FOR UPDATE",
                    (identifier,),
                )
            ).fetchone()
            if old and old["last_action_id"] == action.action_id:
                return {
                    "experience_id": identifier,
                    "revision": old["revision"],
                    "source_refs": [wire(disclosure)],
                }
            require((old["revision"] if old else 0) == expected, "revision_conflict", 409)
            require(
                not old or old["owner_id"] == action.context.principal_id,
                "experience_owner_required",
                403,
            )
            await conn.execute(
                "INSERT INTO capability_projects.experiences VALUES(%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb,'active',%s,now()) ON CONFLICT(experience_id) DO UPDATE SET revision=excluded.revision,payload=excluded.payload,evidence=excluded.evidence,source_refs=excluded.source_refs,disclosure_ref=excluded.disclosure_ref,state='active',last_action_id=excluded.last_action_id,observed_at=now()",
                (
                    identifier,
                    action.context.principal_id,
                    action.context.actor_id,
                    list(action.context.scope_ids),
                    expected + 1,
                    canonical(payload),
                    canonical(evidence),
                    canonical(wire(all_refs)),
                    canonical(wire(disclosure)),
                    action.action_id,
                ),
            )
            await conn.execute(
                "INSERT INTO capability_projects.experience_versions(experience_id,revision,payload,evidence,source_refs,disclosure_ref,state,action_id) VALUES(%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb,'active',%s)",
                (
                    identifier,
                    expected + 1,
                    canonical(payload),
                    canonical(evidence),
                    canonical(wire(all_refs)),
                    canonical(wire(disclosure)),
                    action.action_id,
                ),
            )
            await self.store.dependency(
                conn, "experience", identifier + ":" + str(expected + 1), all_refs
            )
        return {
            "experience_id": identifier,
            "revision": expected + 1,
            "state": "active",
            "source_ref": wire(disclosure),
            "source_refs": [wire(disclosure)],
        }

    async def list(self, context, body):
        await self.store.authority.check(context)
        limit = integer(body.get("limit", 16), 1, 40)
        after = body.get("cursor")
        async with self.store.database.transaction(read_only=True) as conn:
            rows = await (
                await conn.execute(
                    "SELECT * FROM capability_projects.experiences WHERE actor_id=%s AND state='active' AND (%s::text IS NULL OR experience_id>%s) ORDER BY experience_id LIMIT %s",
                    (context.actor_id, after, after, limit + 1),
                )
            ).fetchall()
        items = []
        for row in rows[:limit]:
            if not row["disclosure_ref"]:
                continue
            public = SourceRef(**row["disclosure_ref"])
            if not (await self.store.sources.check_many(context, ((public,),)))[0]:
                continue
            readable = (await self.store.sources.check_many(context, (refs(row["source_refs"]),)))[
                0
            ]
            # Public approval never discloses private source material or private project ids.
            items.append(
                {
                    "experience_id": row["experience_id"],
                    "revision": row["revision"],
                    "payload": row["payload"],
                    "evidence": row["evidence"] if readable else "protected",
                    "source_refs": [wire(public)],
                    "observed_at": row["observed_at"],
                    "can_withdraw": row["owner_id"] == context.principal_id,
                }
            )
        return self.store.page(
            items,
            len(rows) > limit,
            rows[limit - 1]["experience_id"] if len(rows) > limit else None,
            missing=("unavailable_sources",) if len(items) < min(limit, len(rows)) else (),
        )

    async def withdraw(self, action):
        body = action.arguments
        async with self.store.database.transaction(read_only=True) as conn:
            old = await (
                await conn.execute(
                    "SELECT * FROM capability_projects.experiences WHERE experience_id=%s AND owner_id=%s AND actor_id=%s",
                    (body["experience_id"], action.context.principal_id, action.context.actor_id),
                )
            ).fetchone()
        require(old and old["revision"] == body["expected_revision"], "revision_conflict", 409)
        disclosure = SourceRef(**old["disclosure_ref"])
        async with self.store.database.transaction() as conn:
            await self.store.sources.retract_publication(
                action.context, disclosure, connection=conn
            )
            changed = await (
                await conn.execute(
                    "UPDATE capability_projects.experiences SET state='withdrawn',revision=revision+1,last_action_id=%s WHERE experience_id=%s AND revision=%s RETURNING revision",
                    (action.action_id, old["experience_id"], old["revision"]),
                )
            ).fetchone()
            require(changed, "revision_conflict", 409)
            await conn.execute(
                "INSERT INTO capability_projects.experience_versions(experience_id,revision,payload,evidence,source_refs,disclosure_ref,state,action_id) VALUES(%s,%s,'{}','[]','[]',NULL,'withdrawn',%s)",
                (old["experience_id"], changed["revision"], action.action_id),
            )
        return {
            "experience_id": old["experience_id"],
            "revision": changed["revision"],
            "state": "withdrawn",
            "source_refs": [],
        }
