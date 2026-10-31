"""Composition of project-owned use cases, over the existing product services."""

from dataclasses import replace
from uuid import uuid5, NAMESPACE_URL
from jsonschema import Draft202012Validator, ValidationError
from tianshu.runtime.serialization import wire
from tianshu.sources.dependencies import unique_refs
from ..contracts import Fault, require
from .types import ProjectSettings
from .store import ProjectStore
from .registry import Registry
from .documents import Documents
from .directories import Directories
from .notes import Notes
from .lessons import Lessons
from .experiences import Experiences
from .continuation import Continuation
from .query import ProjectQuery
from .primitives import refs
from .catalog import ProjectCapability, READ_SCHEMAS, WRITE_SCHEMAS


class Projects:
    def __init__(
        self,
        runtime,
        settings,
        *,
        authority,
        sources,
        artifacts,
        local,
        catalog,
        authorize_conversation=None,
    ):
        self.authorize_conversation = authorize_conversation
        self.settings = ProjectSettings.from_mapping(settings)
        self.store = ProjectStore(runtime, authority, sources)
        self.registry = Registry(self.store, self.settings, local)
        self.documents = Documents(self.store, local, artifacts)
        self.directories = Directories(self.store, self.documents, local)
        self.notes = Notes(self.store, self.documents)
        self.lessons = Lessons(self.store, self.documents)
        self.experiences = Experiences(self.store, self.lessons)
        self.continuation = Continuation(
            self.store, self.documents, self.notes, self.lessons, local, self.settings
        )
        self.query = ProjectQuery(self.store, self.documents)
        for operation, input_schema in {**READ_SCHEMAS, **WRITE_SCHEMAS}.items():
            catalog.register(ProjectCapability(self, operation, input_schema))

    def bind_content(self, content):
        self.documents.content = content

    async def scene(self, context, project_id, *, include_disabled=False):
        project = await self.store.project(context, project_id, include_disabled=include_disabled)
        require(self.authorize_conversation is not None, "project_scene_authority_unavailable", 503)
        return await self.authorize_conversation(context, project["conversation_id"])

    async def read(self, operation, context, body):
        await self.store.authority.check(context)
        if operation not in READ_SCHEMAS:
            raise Fault("project_operation_unknown", 404)
        try:
            Draft202012Validator(READ_SCHEMAS[operation]).validate(body)
        except ValidationError:
            raise Fault("invalid_project_input", 400) from None
        require(
            "*" in context.permissions or "projects.read" in context.permissions, "forbidden", 403
        )
        if body.get("project_id"):
            context = await self.scene(
                context, body["project_id"], include_disabled=operation == "registry/get"
            )
        if operation == "view":
            return await self.registry.view(context, body)
        if operation == "registry/get":
            return await self.store.project(context, body["project_id"], include_disabled=True)
        if operation == "documents":
            return await self.documents.list(context, body)
        if operation == "documents/read":
            return await self.documents.read(context, body)
        if operation == "documents/original":
            return await self.documents.original(context, body)
        if operation == "query":
            return await self.query.query(context, body)
        if operation == "check":
            project = await self.store.project(context, body["project_id"])
            return await self.documents.evidence(context, project, body["reference"])
        if operation == "notes":
            return await self.notes.list(context, body)
        if operation == "notes/get":
            return await self.notes.get(context, body)
        if operation == "lessons":
            return await self.lessons.list(context, body)
        if operation == "lessons/get":
            return await self.lessons.get(context, body)
        if operation == "experiences":
            return await self.experiences.list(context, body)
        if operation == "continuation/recover":
            return await self.continuation.recover(context, body)
        if operation == "continuation/check":
            return await self.continuation.check(context, body)
        if operation == "state":
            return await self.continuation.state(context, body)
        raise Fault("project_operation_unknown", 404)

    async def command(self, operation, action, attempt):
        if action.arguments.get("project_id") and operation != "registry/save":
            action = replace(
                action, context=await self.scene(action.context, action.arguments["project_id"])
            )
        if operation == "registry/save":
            return await self.registry.save(action)
        if operation == "directories/preview":
            return await self.directories.preview(action)
        if operation == "directories/import":
            return await self.directories.apply(action, attempt)
        if operation == "documents/import":
            return await self.documents.import_document(action, attempt)
        if operation == "documents/remove":
            return await self.documents.remove(action)
        if operation == "notes/save":
            return await self.notes.save(action)
        if operation == "notes/withdraw":
            return await self.notes.withdraw(action)
        if operation == "lessons/save":
            return await self.lessons.save(action)
        if operation == "lessons/retire":
            return await self.lessons.retire(action)
        if operation == "experiences/promote":
            return await self.experiences.promote(action)
        if operation == "experiences/withdraw":
            return await self.experiences.withdraw(action)
        if operation == "state/save":
            return await self.continuation.save_state(action)
        raise Fault("project_operation_unknown", 404)

    def result(self, operation, value):
        value = wire(value)
        if not isinstance(value, dict):
            raise Fault("project_result_invalid", 500)
        dependencies = refs(value.get("source_refs", ()))
        if operation in {"documents", "notes", "lessons", "experiences"}:
            dependencies = unique_refs(
                dependencies, *(refs(item.get("source_refs", ())) for item in value["items"])
            )
        elif operation == "state" and value.get("state"):
            dependencies = refs(value["state"]["source_refs"])
        return {**value, "source_refs": wire(dependencies)}

    async def reconcile(self, operation, action):
        async with self.store.database.transaction(read_only=True) as conn:
            if operation == "directories/import":
                row = await (
                    await conn.execute(
                        "SELECT result,state FROM capability_projects.imports WHERE action_id=%s",
                        (action.action_id,),
                    )
                ).fetchone()
                value = row["result"] if row and row["state"] == "completed" else None
            elif operation == "directories/preview":
                row = await (
                    await conn.execute(
                        "SELECT result FROM capability_projects.directory_plans WHERE plan_id=%s AND principal_id=%s",
                        (
                            str(uuid5(NAMESPACE_URL, "project-plan:" + action.action_id)),
                            action.context.principal_id,
                        ),
                    )
                ).fetchone()
                value = row["result"] if row else None
            else:
                mapping = {
                    "registry/save": ("registry_versions", "revision"),
                    "documents/import": ("document_versions", "version"),
                    "notes/save": ("note_versions", "version"),
                    "notes/withdraw": ("note_versions", "version"),
                    "lessons/save": ("lesson_versions", "version"),
                    "lessons/retire": ("lesson_versions", "version"),
                    "experiences/promote": ("experience_versions", "revision"),
                    "experiences/withdraw": ("experience_versions", "revision"),
                    "state/save": ("declared_states", "revision"),
                }
                selected = mapping.get(operation)
                if not selected:
                    return None
                table, version = selected
                rows = await (
                    await conn.execute(
                        f"SELECT * FROM capability_projects.{table} WHERE action_id=%s ORDER BY {version}",
                        (action.action_id,),
                    )
                ).fetchall()
                value = rows[0] if rows else None
        if value:
            await self.store.readable(action.context, value.get("source_refs", ()))
        return value

    async def withdraw(self, connection, source_refs, reason):
        from .withdrawal import withdraw

        await withdraw(self, connection, source_refs, reason)
