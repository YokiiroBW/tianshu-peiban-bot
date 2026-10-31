"""Public composition entry; concrete services are injected by the application root."""
from importlib.resources import files
from tianshu.runtime.api import command_body, json_response
from tianshu.runtime.composition import DomainModule
from tianshu.runtime.errors import AppError
from tianshu.runtime.migrations import Migration


def create_module(service, growth, identity):
    def register(app, _runtime):
        async def read_body(request):
            value = await request.json()
            if not isinstance(value, dict):
                raise AppError("invalid_input")
            return value

        def route(operation, callback, *, write=False, actor=True):
            async def handler(request):
                body = await command_body(request) if write else await read_body(request)
                context = await identity.for_request(request,
                    actor_id=body.get("actor_id") if actor else None,
                    person_id=body.get("person_id"),
                    permission="companion.manage" if write else "companion.read")
                return json_response(await callback(context, body))
            app.router.add_post("/api/v1/companion/" + operation, handler)

        route("roles/list", lambda ctx, body: service.roles(ctx, cursor=body.get("cursor"), limit=body.get("limit", 50)), actor=False)
        route("roles/create", service.save_actor, write=True, actor=False)
        route("roles/save", service.save_actor, write=True)
        route("roles/delete", service.delete_actor, write=True)
        route("roles/get", lambda ctx, _body: service.actor(ctx))
        route("personas/catalog", lambda ctx, _body: service.persona_catalog(ctx), actor=False)
        route("personas/view", lambda ctx, body: service.persona(ctx, body.get("persona_id"), body.get("revision")), actor=False)
        route("personas/history", lambda ctx, body: service.persona_history(ctx, body.get("persona_id"), cursor=body.get("cursor"), limit=body.get("limit", 40)), actor=False)
        route("personas/save", service.save_persona, write=True, actor=False)
        route("affect/view", lambda ctx, _body: service.affect(ctx))
        route("growth/view", lambda ctx, body: growth.view(ctx, cursor=body.get("cursor"), limit=body.get("limit", 40)))
        route("growth/revoke", lambda ctx, body: growth.set_state(ctx, body, "revoked"), write=True)
        route("growth/restore", lambda ctx, body: growth.set_state(ctx, body, "active"), write=True)

    def tasks(queue, _runtime):
        queue.register("companion.organize", growth.organize, retryable=False)

    return DomainModule("companion", register, register_tasks=tasks,
        migrations=(Migration("0030_companion", files("tianshu.companion").joinpath("schema.sql").read_text()),))
