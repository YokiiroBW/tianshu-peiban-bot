"""Source read/invalidation routes and the one dependency-propagation task."""

from importlib.resources import files

from tianshu.runtime.api import command_body, json_response
from tianshu.runtime.composition import DomainModule
from tianshu.runtime.migrations import Migration
from tianshu.runtime.execution import ExecutionLane
from tianshu.types import SourceRef


def create_module(service, identity):
    def register(app, _runtime):
        async def read(request):
            body = await request.json()
            context = await identity.for_request(
                request,
                actor_id=body.get("actor_id"),
                person_id=body.get("person_id"),
                conversation_id=body.get("conversation_id"),
            )
            return json_response(
                await service.read(
                    context, SourceRef(**body["source_ref"]), body.get("requested", {})
                )
            )

        async def invalidate(request):
            body = await command_body(request)
            context = await identity.for_request(
                request,
                actor_id=body.get("actor_id"),
                person_id=body.get("person_id"),
                conversation_id=body.get("conversation_id"),
            )
            operation = request.match_info["operation"]
            result = (
                await service.forget_fragment(context, body)
                if operation == "forget-fragment"
                else await service.invalidate(context, body, forgotten=operation == "forget")
            )
            return json_response(result)

        app.router.add_post("/api/v1/sources/read", read)
        app.router.add_post(
            "/api/v1/sources/{operation:forget|retract|forget-fragment}", invalidate
        )

    def tasks(queue, _runtime):
        queue.register("sources.propagate", service.propagate, retryable=True,
                       resource_recovery=True)
        queue.register("sources.dispatch", service.dispatch_dependant, retryable=True,
                       execution_lane=ExecutionLane.SOURCE_LIFECYCLE, resource_recovery=True)
        queue.register("sources.notify", service.notify_dependant, retryable=True,
                       execution_lane=ExecutionLane.SOURCE_LIFECYCLE, resource_recovery=True)

    return DomainModule(
        "sources",
        register,
        register_tasks=tasks,
        migrations=(
            Migration(
                "0011_sources",
                files("tianshu.sources").joinpath("schema.sql").read_text()
                + "\n"
                + files("tianshu.sources").joinpath("functions.sql").read_text(),
            ),
            Migration("0012_sources_rb", files("tianshu.sources").joinpath("0012_sources_rb.sql").read_text()),
            Migration(
                "rbf_source_proof_jit",
                files("tianshu.sources").joinpath("rbf_source_proof_jit.sql").read_text(encoding="utf-8"),
            ),
            Migration(
                "rbf_source_proof_reuse",
                files("tianshu.sources").joinpath("rbf_source_proof_reuse.sql").read_text(encoding="utf-8"),
            ),
        ),
    )
