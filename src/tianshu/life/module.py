from importlib.resources import files
from tianshu.runtime.api import command_body, json_response
from tianshu.runtime.composition import DomainModule
from tianshu.runtime.errors import AppError
from tianshu.runtime.execution import ExecutionLane
from tianshu.runtime.migrations import Migration
from .generation_control import GenerationControl


def create_module(service, presentation, writing, diaries, reading, worker, identity, clock):
    from .private_contact_policy import PrivateContactPolicy
    private_contacts = PrivateContactPolicy(service)
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
        route("day-plan", lambda ctx,body: service.days.view(ctx,body.get("day")))
        route("day-plan.move", service.days.move, write=True)
        route("day-plan.reconsider", service.days.reconsider, write=True)
        route("activities", lambda ctx, body: service.activities(ctx, cursor=body.get("cursor"), limit=body.get("limit", 40)))
        route("activities/checkpoint", service.activity_checkpoint)
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
        route("private-contact-policy", lambda ctx, _body: private_contacts.view(ctx))
        route("private-contact-policy.save", private_contacts.save, write=True)
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
        route("diaries/review", diaries.review, write=True)
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
        queue.register("life.plan", worker.plan, retryable=False, resource_recovery=True)
        queue.register("life.activity_step", worker.activity_step, retryable=False, resource_recovery=True)
        queue.register("life.activity_cancel", worker.cancel_activity, retryable=False)
        queue.register("life.activity_reconcile", worker.reconcile_activity, retryable=False, resource_recovery=True)
        queue.register("life.activity_completed_wake", worker.activity_completed_wake,
            execution_lane=ExecutionLane.LIFE, resource_recovery=True)
        queue.register("life.recovery_observe_activity", worker.recovery_observe_activity, retryable=False)
        queue.register("life.statistics_replay", worker.statistics_replay, retryable=False)
        queue.register("life.statistics_backfill", worker.statistics_backfill, retryable=False)
        queue.register("life.commitment_due", worker.commitment_due, retryable=False)
        queue.register("life.commitment_reconcile", worker.reconcile_commitment, retryable=False)
        queue.register("life.contact_due", worker.contact_due, retryable=False)
        queue.register("life.contact_reconcile", worker.reconcile_contact, retryable=False)
        queue.register("life.diary_review", diaries.review_day, retryable=False, resource_recovery=True)
        queue.register("life.diary_episode_changed", diaries.consume_episode_change,
            retryable=True, execution_lane=ExecutionLane.DIARY)
        queue.register("life.experience_statistics_backfill", generation_control.backfill_statistics,
            retryable=True, execution_lane=ExecutionLane.DIARY)
        queue.register("life.diary_collect", diaries.collect, retryable=False, resource_recovery=True)
        queue.register("life.diary_generate", diaries.generate, retryable=False, resource_recovery=True)
        queue.register("life.chapter_generate", writing.generate, retryable=False, resource_recovery=True)

    async def start(_runtime):
        if _runtime.role != "bot-background":
            return
        await clock.recover()
        await worker.recover()
        await generation_control.recover()
        await diaries.recover()
        from .statistics import enqueue_backfill
        await enqueue_backfill(worker)

    async def maintenance(_runtime):
        import asyncio
        results = await asyncio.gather(worker.reconcile_due(limit=32),
            diaries.collection.reconcile(limit=32), return_exceptions=True)
        for result in results:
            if isinstance(result,BaseException):
                raise result

    return DomainModule("life", register, start=start, register_tasks=tasks,
        maintenance=maintenance, maintenance_roles=("bot-background",),
        migrations=(
            Migration("0031_life", files("tianshu.life").joinpath("schema.sql").read_text()),
            Migration("0033_life_contact_expiry", files("tianshu.life").joinpath("contact_expiry.sql").read_text()),
            Migration("0035_life_planning_recovery", files("tianshu.life").joinpath("planning_recovery.sql").read_text()),
            Migration("0038_life_computation_retry", files("tianshu.life").joinpath("0038_life_computation_retry.sql").read_text()),
            Migration("0040_life_execution", files("tianshu.life").joinpath("rb_life_execution.sql").read_text()),
            Migration("rbf_life_planning_wake", files("tianshu.life").joinpath("rbf_life_planning_wake.sql").read_text()),
            Migration("rbg_plan_retirement", files("tianshu.life").joinpath("rbg_plan_retirement.sql").read_text()),
            Migration("rbh_transport_recovery", files("tianshu.life").joinpath("rbh_transport_recovery.sql").read_text(encoding="utf-8")),
            Migration("rbi_activity_reconciliation", files("tianshu.life").joinpath("rbi_activity_reconciliation.sql").read_text(encoding="utf-8")),
            Migration("private_contact_default", files("tianshu.life").joinpath("private_contact_default.sql").read_text(encoding="utf-8")),
        ))
