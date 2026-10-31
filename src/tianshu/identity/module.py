"""Session routes and request authentication for the single API surface."""

from importlib.resources import files

from aiohttp import web

from tianshu.runtime.api import TRUSTED_CONTEXT, command_body, json_response
from tianshu.runtime.composition import DomainModule
from tianshu.runtime.migrations import Migration
from .management import IdentityManagement


def create_module(identity):
    sessions, management = identity.sessions, IdentityManagement(identity)

    def register(app, _runtime):
        @web.middleware
        async def authenticate(request, handler):
            if not request.path.startswith("/api/v1/"):
                return await handler(request)
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                await sessions.verify_csrf(request)
            if request.path not in {"/api/v1/session", "/api/v1/setup", "/api/v1/auth/login"}:
                request[TRUSTED_CONTEXT] = await identity.for_request(
                    request,
                    actor_id=request.query.get("actor_id"),
                    person_id=request.query.get("person_id"),
                    conversation_id=request.query.get("conversation_id"),
                )
            return await handler(request)

        app.middlewares.append(authenticate)

        async def session(request):
            view, token = await sessions.inspect(request)
            response = json_response(view)
            response.headers["Cache-Control"] = "no-store"
            if token:
                sessions.set_cookie(response, token)
            return response

        async def setup(request):
            result, token = await sessions.setup(await command_body(request))
            response = json_response(result)
            sessions.set_cookie(response, token)
            return response

        async def login(request):
            result, token = await sessions.login(await command_body(request))
            response = json_response(result)
            sessions.set_cookie(response, token)
            return response

        async def logout(request):
            response = json_response(await sessions.logout(request, await command_body(request)))
            response.del_cookie(sessions.cookie, path="/")
            return response

        async def conversations(request):
            context = await identity.for_request(request, actor_id=request.query.get("actor_id"))
            return json_response(
                await identity.conversations(
                    context,
                    context.actor_id,
                    cursor=request.query.get("cursor"),
                    limit=request.query.get("limit", 50),
                )
            )

        async def open_conversation(request):
            body = await command_body(request)
            context = await identity.for_request(request, actor_id=body.get("actor_id"))
            return json_response(
                await identity.open_web_conversation(
                    context,
                    context.actor_id,
                    client_request_id=body["client_request_id"],
                    expected_revision=body.get("expected_revision"),
                )
            )

        async def people(request):
            return json_response(
                await management.people(
                    await identity.for_request(request),
                    cursor=request.query.get("cursor"),
                    limit=request.query.get("limit", 50),
                )
            )

        async def directory(request):
            return json_response(
                await management.directory(
                    await identity.for_request(request, permission="identity.manage")
                )
            )

        async def command(request):
            body = await command_body(request)
            context = await identity.for_request(
                request, actor_id=body.get("actor_id"), permission="identity.manage"
            )
            return json_response(
                await management.execute(context, request.match_info["operation"], body)
            )

        app.router.add_get("/api/v1/session", session)
        app.router.add_post("/api/v1/setup", setup)
        app.router.add_post("/api/v1/auth/login", login)
        app.router.add_post("/api/v1/auth/logout", logout)
        app.router.add_get("/api/v1/identity/conversations", conversations)
        app.router.add_post("/api/v1/identity/conversations/open", open_conversation)
        app.router.add_get("/api/v1/identity/people", people)
        app.router.add_get("/api/v1/identity/directory", directory)
        app.router.add_post(
            "/api/v1/identity/{operation:principals.save|actors.grant|scopes.grant|people.save|connections.save|accounts.link|conversations.link}",
            command,
        )

    return DomainModule(
        "identity",
        register,
        migrations=(
            Migration(
                "0010_identity", files("tianshu.identity").joinpath("schema.sql").read_text()
            ),
        ),
    )
