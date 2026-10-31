"""Same-origin plugin console adapter, reusing the authenticated Platform operator."""

from ..contracts import Fault, require
from .control import openapi, validate
import secrets
import time
from .views import POLICY

PREFIX = "/api/v1/plugins/"


class WebPlugins:
    def __init__(self, service, authorize_actor=None, actor_directory=None):
        self.service, self.host = service, service.host
        self.authorize_actor, self.actor_directory = authorize_actor, actor_directory
        self.tickets = {}
        self.view_policy = POLICY

    async def page(self, ticket, session):
        from .views import document

        await self.guard(session)
        entry = self.tickets.pop(ticket, None)
        require(
            entry
            and entry["principal_id"] == session.principal_id
            and entry["access_epoch"] == session.access_epoch
            and entry["expires"] > time.monotonic(),
            "not_found",
            404,
        )
        return await self.host.local(document, entry["package"], entry["view"], entry["nonce"])

    async def guard(self, session):
        await self.service.authority.check(session)
        require(
            "*" in session.permissions or "config.publish" in session.permissions,
            "operator_not_authorized",
            403,
        )
        return True

    async def route(self, operation, body, session):
        require(isinstance(body, dict), "invalid_input", 400)
        await self.guard(session)
        validate(operation, body)
        principal = session.principal_id

        async def authorize():
            return await self.guard(session)

        host, repo = self.host, self.host.repository
        if operation == "view":
            result = await host.view()
        elif operation == "schema":
            result = openapi()
        elif operation == "catalog":
            result = {"items": await host.catalog()}
        elif operation == "actors":
            require(self.actor_directory is not None, "actor_directory_unavailable", 503)
            result = {
                "items": [
                    {"actor_id": row["id"], "name": row["name"], "revision": row["revision"]}
                    for row in await self.actor_directory(session)
                    if row.get("enabled", True)
                ]
            }
        elif operation == "view-content":
            instance = await host.instance(body["instance_id"])
            package = await host.package(instance["package_id"], instance["version"])
            view = next(
                (
                    item
                    for item in package["description"].get("views", [])
                    if item["id"] == body["page_id"]
                ),
                None,
            )
            require(view, "not_found", 404)
            self.tickets = {
                key: item
                for key, item in self.tickets.items()
                if item["expires"] > time.monotonic()
            }
            require(len(self.tickets) < 256, "too_many_requests", 429)
            ticket = secrets.token_urlsafe(32)
            self.tickets[ticket] = {
                "principal_id": session.principal_id,
                "access_epoch": session.access_epoch,
                "package": package,
                "view": view,
                "nonce": body["nonce"],
                "expires": time.monotonic() + 60,
            }
            result = {"url": PREFIX + "page/" + ticket}
        elif operation == "install":
            source = body["source"]
            if source["type"] == "artifact":
                import base64
                from tianshu.types import MediaRef

                media = MediaRef(**source["media"])
                require(
                    media.size_bytes is not None and media.size_bytes <= 6 * 1024 * 1024,
                    "plugin_package_too_large",
                    413,
                )
                raw, _ = await self.service.artifacts.resolve(session, media)
                source = {"type": "zip", "data": base64.b64encode(raw).decode()}
            result = await host.install(source, body["client_request_id"], wait=True)
        elif operation == "installation":
            result = await host.local(repo.get, "operations", body["id"])
            require(result, "not_found", 404)
        elif operation == "create":
            result = await host.create(body["package_id"], body["name"], body["client_request_id"])
        elif operation == "configure":
            result = await host.configure(
                body["instance_id"],
                body["config"],
                body.get("secrets", {}),
                body["expected_revision"],
            )
        elif operation in {"enable", "disable"}:
            result = await host.state(
                body["instance_id"], operation == "enable", body["expected_revision"]
            )
        elif operation == "bindings":
            require(self.authorize_actor is not None, "actor_authority_unavailable", 503)
            for binding in body["bindings"]:
                scope = binding.get("scope", {})
                require(scope.get("kind") == "character", "plugin_binding_scope_invalid", 400)
                await self.authorize_actor(
                    session, scope.get("character_id"), permission="config.publish"
                )
            result = await host.bindings(
                body["instance_id"], body["bindings"], body["expected_revision"]
            )
        elif operation == "invoke":
            from tianshu.types import Action, OriginKind, ServiceClass
            from tianshu.runtime.serialization import wire
            from ..contracts import digest

            await self.service.discover(session)
            instance = await host.instance(body["instance_id"])
            binding = next(
                (
                    x
                    for x in instance["bindings"]
                    if x["enabled"]
                    and x["scope"] == {"kind": "character", "character_id": session.actor_id}
                    and (not body.get("binding_id") or x["binding_id"] == body["binding_id"])
                    and {"kind": "action", "id": body["action"]} in x.get("entries", [])
                ),
                None,
            )
            require(binding, "plugin_binding_required", 403)
            identifier = (
                "plugin:" + instance["id"] + ":" + binding["binding_id"] + ":" + body["action"]
            )
            key = digest([session.principal_id, identifier, body["client_request_id"]])
            from ..api import contains_secret

            arguments = (
                await self.service.configuration.seal_command(session, key, body["input"])
                if contains_secret(body["input"])
                else body["input"]
            )
            result = wire(
                await self.service.actions.invoke(
                    Action(
                        key,
                        body["client_request_id"],
                        identifier,
                        session.actor_id,
                        session,
                        arguments,
                        OriginKind.HUMAN,
                    ),
                    background=True,
                    service_class=ServiceClass.ELASTIC
                    if self.service.catalog.handler(identifier).definition.long_running
                    else ServiceClass.INTERACTIVE,
                )
            )
        elif operation == "invocations":
            result = {
                "items": [
                    host.executions.public(item)
                    for item in await host.local(repo.calls, body.get("instance_id"))
                ]
            }
        elif operation == "invocation":
            result = host.executions.public(await host.local(repo.call, body["id"]))
        elif operation == "cancel":
            result = await self.service.actions.cancel(body["id"], session)
        elif operation in {"reconcile", "resume"}:
            result = (
                await self.service.actions.resume(
                    body["id"], session, client_request_id=body["client_request_id"]
                )
                if operation == "resume"
                else await self.service.actions.reconcile(body["id"], session)
            )
        elif operation == "upgrade":
            result = await host.upgrade(
                body["instance_id"], body["version"], body["expected_revision"]
            )
        elif operation == "uninstall":
            result = await host.uninstall(body["package_id"])
        elif operation == "logs":
            result = {"items": await host.local(repo.logs, body["instance_id"])}
        elif operation == "schedules":
            if "operation" in body:
                result = await host.scheduler.manage(
                    body["instance_id"], body["operation"], body["value"]
                )
            else:
                result = {
                    "items": [
                        item
                        for item in await host.local(repo.list, "schedules")
                        if item["instance_id"] == body["instance_id"]
                    ]
                }
        else:
            raise Fault("not_found", 404)
        await self.guard(session)
        return result
