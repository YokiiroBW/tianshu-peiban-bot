"""Explicit route/task registration consumed by the single application assembly."""
from importlib.resources import files
from tianshu.runtime.api import command_body, json_response
from tianshu.runtime.composition import DomainModule
from tianshu.runtime.errors import AppError
from tianshu.runtime.migrations import Migration


def create_module(service, expression, identity, result_consumer):
    def register(app, _runtime):
        def route(operation, callback, *, write=False):
            async def handler(request):
                body = await command_body(request) if write else await request.json()
                if not isinstance(body, dict):
                    raise AppError("invalid_input")
                context = await identity.for_request(request, actor_id=body.get("actor_id"),
                    person_id=body.get("person_id"), conversation_id=body.get("conversation_id"),
                    permission="conversation.send" if write else "conversation.read")
                return json_response(await callback(context, body))
            app.router.add_post("/api/v1/conversation/"+operation, handler)
        route("conversations", lambda ctx, body: identity.conversations(ctx, ctx.actor_id, cursor=body.get("cursor"), limit=body.get("limit", 50)))
        route("messages", lambda ctx, body: service.messages(ctx, cursor=body.get("cursor"), limit=body.get("limit", 50)))
        route("send", service.send, write=True)
        route("turn", lambda ctx, body: service.turn(ctx, body.get("turn_id")))
        route("cancel", service.cancel, write=True)
        route("expression", lambda ctx, body: expression.get(ctx, body.get("expression_id")))
        route("expression/reconcile", service.reconcile_expression, write=True)

    def tasks(queue, _runtime):
        queue.register("conversation.run", service.run, retryable=False)
        queue.register("conversation.seal", service.collector.seal, retryable=False)
        queue.register("conversation.cancel", service.cancel_work, retryable=False)
        queue.register("conversation.reconcile", service.reconcile, retryable=False)
        queue.register("conversation.result", result_consumer.consume, retryable=True)
        queue.register("conversation.expire", service.expire, retryable=True)
        queue.register("conversation.expression_expire", expression.expire, retryable=True)

    async def start(_runtime):
        await service.recover()
        await expression.recover()
        async with service.database.transaction() as connection:
            queued = await (await connection.execute("SELECT DISTINCT actor_id,conversation_id FROM conversation.turns WHERE state='queued'")).fetchall()
        for row in queued:
            await service._next(row["actor_id"], row["conversation_id"])

    return DomainModule("conversation", register, start=start, register_tasks=tasks,
        migrations=(Migration("0032_conversation", files("tianshu.conversation").joinpath("schema.sql").read_text()),))
