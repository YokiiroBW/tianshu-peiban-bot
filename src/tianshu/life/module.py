from importlib.resources import files
from tianshu.runtime.api import command_body, json_response
from tianshu.runtime.composition import DomainModule
from tianshu.runtime.errors import AppError
from tianshu.runtime.migrations import Migration
from .generation_control import GenerationControl


def create_module(service, presentation, writing, diaries, reading, worker, identity, clock):
    generation_control = GenerationControl(service, writing, diaries)
    def register(app, _runtime):
        async def access(request):
            body = await request.json()
            context = await identity.for_request(request, actor_id=body.get("actor_id"),
                person_id=body.get("person_id"), conversation_id=body.get("conversation_id"))
            permissions = context.permissions
            if not permissions & {"*", "life.read", "life.manage", "life.published.read"}:
                raise AppError("forbidden", status=403)
            return json_response({"can_manage": bool(permissions & {"*", "life.manage"}),
                "can_read": bool(permissions & {"*", "life.read"}),
                "can_read_published": bool(permissions & {"*", "life.manage", "life.published.read"})})
        app.router.add_post("/api/v1/life/access", access)

        def route(operation, callback, *, write=False, permission=None):
            async def handler(request):
                body = await command_body(request) if write else await request.json()
                if not isinstance(body, dict):
                    raise AppError("invalid_input")
                context = await identity.for_request(request, actor_id=body.get("actor_id"),
                    person_id=body.get("person_id"), conversation_id=body.get("conversation_id"),
                    permission=None if permission=="life.published.read" else permission or ("life.manage" if write else "life.read"))
                if permission=="life.published.read" and not ({"*","life.manage","life.published.read"}&context.permissions):
                    raise AppError("forbidden",status=403)
                return json_response(await callback(context, body))
            app.router.add_post("/api/v1/life/" + operation, handler)

        route("routine",lambda ctx,_body:clock.view(ctx))
        route("routine.save",clock.save,write=True)
        route("state", lambda ctx, _body: service.state(ctx))
        route("goals", lambda ctx, body: service.goals(ctx, cursor=body.get("cursor"), limit=body.get("limit", 40)))
        route("goals.save", service.save_goal, write=True)
        route("activities", lambda ctx, body: service.activities(ctx, cursor=body.get("cursor"), limit=body.get("limit", 40)))
        route("activities.save", service.save_activity, write=True)
        route("activities.pause", lambda ctx, body: service.transition_activity(ctx, body, "paused"), write=True)
        route("activities.resume", lambda ctx, body: service.transition_activity(ctx, body, "running"), write=True)
        route("activities.cancel", lambda ctx, body: service.transition_activity(ctx, body, "cancelled"), write=True)
        route("commitments", lambda ctx, body: service.commitments(ctx, cursor=body.get("cursor"), limit=body.get("limit", 40)))
        route("commitments.save", service.save_commitment, write=True)
        route("commitments.reschedule", service.reschedule_commitment, write=True)
        route("commitments.cancel", service.cancel_commitment, write=True)
        route("contact-preferences", lambda ctx, _body: service.contacts.preferences(ctx))
        route("contact-preferences.save", service.save_contact_preferences, write=True)
        route("room", lambda ctx, _body: presentation.room(ctx))
        route("room.save", presentation.save_room, write=True)
        route("outfits", lambda ctx, body: presentation.outfits(ctx, cursor=body.get("cursor"), limit=body.get("limit", 40)))
        route("outfits.save", presentation.save_outfit, write=True)
        route("outfits.select", presentation.select_outfit, write=True)
        route("outfits.delete", presentation.delete_outfit, write=True)
        route("diaries", lambda ctx, body: diaries.list(ctx, cursor=body.get("cursor"), limit=body.get("limit", 30)), permission="life.published.read")
        route("diaries/read", diaries.read, permission="life.published.read")
        route("diaries/request", diaries.request, write=True)
        route("diaries/cancel", lambda ctx, body: generation_control.request(ctx,body,kind="diary",operation="cancel"), write=True)
        route("diaries/reconcile", lambda ctx, body: generation_control.request(ctx,body,kind="diary",operation="reconcile"), write=True)
        route("diaries/revise", diaries.revise, write=True)
        route("diaries/publish", diaries.publish, write=True)
        route("works", lambda ctx, body: writing.list(ctx, cursor=body.get("cursor"), limit=body.get("limit", 30)), permission="life.published.read")
        route("works.save", writing.save_work, write=True)
        route("chapters", writing.chapters, permission="life.published.read")
        route("chapters/read", writing.read, permission="life.published.read")
        route("chapters.add", writing.add_chapter, write=True)
        route("chapters/generate", writing.request_generation, write=True)
        route("chapters/cancel", lambda ctx, body: generation_control.request(ctx,body,kind="chapter",operation="cancel"), write=True)
        route("chapters/reconcile", lambda ctx, body: generation_control.request(ctx,body,kind="chapter",operation="reconcile"), write=True)
        route("chapters/revise", writing.revise, write=True)
        route("chapters/review", writing.review, write=True)
        route("chapters/publish", writing.publish, write=True)
        route("reading", lambda ctx, body: reading.list(ctx, cursor=body.get("cursor"), limit=body.get("limit", 30)))
        route("reading/open", reading.open, write=True)
        route("reading/shared", lambda ctx, body: reading.shared(ctx, body.get("grant_id")))
        route("reading/read", reading.read, write=True)
        route("reading/pause", lambda ctx, body: reading.set_state(ctx, body, "paused"), write=True)
        route("reading/resume", lambda ctx, body: reading.set_state(ctx, body, "reading"), write=True)
        route("reading/close", lambda ctx, body: reading.set_state(ctx, body, "closed"), write=True)

    def tasks(queue, _runtime):
        queue.register("life.generation_control",generation_control.work,retryable=True)
        queue.register("life.activate",clock.activate,retryable=True)
        queue.register("life.clock",clock.tick,retryable=True)
        queue.register("life.plan", worker.plan, retryable=False)
        queue.register("life.activity_step", worker.activity_step, retryable=False)
        queue.register("life.activity_cancel", worker.cancel_activity, retryable=False)
        queue.register("life.activity_reconcile", worker.reconcile_activity, retryable=False)
        queue.register("life.commitment_due", worker.commitment_due, retryable=False)
        queue.register("life.contact_due", worker.contact_due, retryable=False)
        queue.register("life.diary_collect", diaries.collect, retryable=False)
        queue.register("life.diary_generate", diaries.generate, retryable=False)
        queue.register("life.chapter_generate", writing.generate, retryable=False)

    async def start(_runtime):
        await clock.recover()
        await worker.recover()
        await generation_control.recover()

    return DomainModule("life", register, start=start, register_tasks=tasks,
        migrations=(Migration("0031_life", files("tianshu.life").joinpath("schema.sql").read_text()),))
