"""Explicit route/task registration consumed by the single application assembly."""
from importlib.resources import files
from tianshu.runtime.api import command_body, json_response
from tianshu.runtime.composition import DomainModule
from tianshu.runtime.errors import AppError
from tianshu.runtime.migrations import Migration
from tianshu.runtime.execution import ExecutionLane


def create_module(service, expression, identity, result_consumer, renderer):
    def register(app, _runtime):
        def route(operation, callback, *, write=False, permission=None):
            async def handler(request):
                body = await command_body(request) if write else await request.json()
                if not isinstance(body, dict):
                    raise AppError("invalid_input")
                context = await identity.for_request(request, actor_id=body.get("actor_id"),
                    person_id=body.get("person_id"), conversation_id=body.get("conversation_id"),
                    permission=permission or ("conversation.send" if write else "conversation.read"))
                return json_response(await callback(context, body))
            app.router.add_post("/api/v1/conversation/"+operation, handler)
        route("conversations", lambda ctx, body: identity.conversations(ctx, ctx.actor_id, cursor=body.get("cursor"), limit=body.get("limit", 50)))
        route("participation", service.participation.view, permission="companion.read")
        route("participation.save", service.participation.save, write=True, permission="companion.manage")
        route("messages", lambda ctx, body: service.messages(ctx, cursor=body.get("cursor"), limit=body.get("limit", 50)))
        route("send", service.send, write=True)
        route("turn", lambda ctx, body: service.turn(ctx, body.get("turn_id")))
        route("cancel", service.cancel, write=True)
        route("expression", lambda ctx, body: renderer.get(ctx, body.get("expression_id")))
        route("expression/reconcile", service.reconcile_expression, write=True)

    def tasks(queue, _runtime):
        queue.register("conversation.expression_release",renderer.release,retryable=False,resource_recovery=True)
        queue.register("conversation.expression_render",renderer.render,retryable=False,resource_recovery=True)
        queue.register("conversation.render_expire",renderer.expire,retryable=True,resource_recovery=True)
        queue.register("conversation.expression_result",result_consumer.expression_progress,retryable=True,resource_recovery=True)
        queue.register("conversation.expression_reconcile",service.reconcile_expression_work,retryable=False)
        queue.register("conversation.participation",service.participation.decide,retryable=False,resource_recovery=True)
        queue.register("conversation.run", service.run, retryable=False,
                       lane_resolver=service.execution_lane_on, resource_recovery=True)
        queue.register("conversation.prepare", service.prepare_turn, retryable=False,
                       lane_resolver=service.execution_lane_on, resource_recovery=True)
        queue.register("conversation.seal", service.collector.seal, retryable=False, resource_recovery=True)
        queue.register("conversation.cancel", service.cancel_work, retryable=False)
        queue.register("conversation.reconcile", service.reconcile, retryable=False, resource_recovery=True)
        queue.register("conversation.result", result_consumer.consume, retryable=True, resource_recovery=True)
        queue.register("conversation.expire", service.expire, retryable=True, resource_recovery=True)
        queue.register("conversation.expression_expire", renderer.expire_delivery, retryable=True, resource_recovery=True)
        queue.register("conversation.supplement_notify", service.supplement_notify, retryable=True,
                       execution_lane=ExecutionLane.CONTROL)
        queue.register("conversation.model_submit", service.model_submit, retryable=False,
                       execution_lane=ExecutionLane.CONVERSATION, resource_recovery=True)
        queue.register("conversation.model_complete", service.model_complete, retryable=False,
                       execution_lane=ExecutionLane.CONVERSATION, resource_recovery=True)
        queue.register("conversation.model_consume_expire", service.model_consume_expire, retryable=True,
                       execution_lane=ExecutionLane.CONVERSATION, resource_recovery=True)
        queue.register("conversation.model_observe_unknown", service.model_observe_unknown, retryable=True,
                       execution_lane=ExecutionLane.CONVERSATION, resource_recovery=True)
        queue.register("conversation.example_usage", service.record_example_usage, retryable=True,
                       execution_lane=ExecutionLane.CONTROL)
        queue.register("conversation.statistics_reconcile", service.reconcile_statistics, retryable=True,
                       execution_lane=ExecutionLane.CONTROL)

    async def start(_runtime):
        await service.recover()
        await service.participation.recover()
        await expression.recover()
        await renderer.recover()
        if service.statistics is not None:
            async with service.database.transaction() as connection:
                await service._schedule_statistics_repair(connection)
        async with service.database.transaction() as connection:
            queued = await (await connection.execute("SELECT DISTINCT actor_id,conversation_id FROM conversation.turns WHERE state='queued'")).fetchall()
        for row in queued:
            await service._next(row["actor_id"], row["conversation_id"])

    return DomainModule("conversation", register, start=start, register_tasks=tasks,
        migrations=(Migration("0032_conversation", files("tianshu.conversation").joinpath("schema.sql").read_text()),
                    Migration("0039_conversation_context_selection", files("tianshu.conversation").joinpath("context_selection.sql").read_text()),
                    Migration("0040_conversation_input_supplements", files("tianshu.conversation").joinpath("input_supplements.sql").read_text()),
                    Migration("0041_conversation_submission_confirmation", files("tianshu.conversation").joinpath("submission_confirmation.sql").read_text()),
                    Migration("rbg_result_consumption", files("tianshu.conversation").joinpath("rbg_result_consumption.sql").read_text())))
