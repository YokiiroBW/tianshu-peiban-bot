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
                    person_id=body.get("person_id") if actor else None,
                    conversation_id=body.get("conversation_id") if actor else None,
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
        route("growth/review",growth.request_review,write=True)
        route("growth/view", lambda ctx, body: growth.view(ctx, cursor=body.get("cursor"), limit=body.get("limit", 40)))
        route("growth/revoke", lambda ctx, body: growth.set_state(ctx, body, "revoked"), write=True)
        route("growth/restore", lambda ctx, body: growth.set_state(ctx, body, "active"), write=True)
        route("growth/revise", growth.revise, write=True)
        route("growth/unresolved/dismiss", growth.habits.dismiss_unresolved, write=True)
        route("growth/history", lambda ctx, body: growth.habits.history(ctx, body.get('id')))
        route("growth/reflections", lambda ctx, body: growth.reflection_page(ctx,
              cursor=body.get('cursor'), limit=body.get('limit', 40), time_mode=body.get('time_mode', 'current'),
              valid_at=body.get('valid_at'), known_at=body.get('known_at')))
        route("growth/examples/reject", growth.examples.reject, write=True)
        route("growth/examples/view", lambda ctx, body: growth.examples.view(ctx,
              cursor=body.get('cursor'), limit=body.get('limit', 40)))

    def tasks(queue, _runtime):
        queue.register("companion.organize", growth.organize, retryable=False)
        queue.register("companion.growth.retry", growth.retries.run, retryable=False)
        queue.register("companion.growth.input_retry", growth.retry_input, retryable=True)
        queue.register("companion.episode_changed", growth.consume_episode_change, retryable=True)
        queue.register("companion.growth.statistics", growth.observations.replay, retryable=False)

    async def start(runtime):
        if runtime.role == 'bot-background':
            await growth.recover()

    return DomainModule("companion", register, start=start, register_tasks=tasks,
        migrations=(Migration("0030_companion", files("tianshu.companion").joinpath("schema.sql").read_text()),
            Migration("0034_companion_growth_batches", files("tianshu.companion").joinpath("growth_batches.sql").read_text()),
            Migration("0037_companion_growth_retry", files("tianshu.companion").joinpath("growth_retry.sql").read_text()),
            Migration("0039_companion_learning", files("tianshu.companion").joinpath("habit_revisions.sql").read_text()),
            Migration("0040_companion_remove_legacy_growth", files("tianshu.companion").joinpath("growth_remove_legacy.sql").read_text())),)
