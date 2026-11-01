"""Memory use cases, selected-source propagation and the incremental worker registry."""

from datetime import UTC, datetime, timedelta
from importlib.resources import files
from uuid import uuid4

from tianshu.runtime.api import CommandResult, command_body, json_response
from tianshu.runtime.commands import body_hash
from tianshu.runtime.composition import DomainModule
from tianshu.runtime.errors import AppError
from tianshu.runtime.jobs import JobSpec
from tianshu.runtime.migrations import Migration
from tianshu.types import RequestBudget,ServiceClass


def create_module(memory, retrieval, workers, identity):
    def routes(app, _runtime):
        def route(name, callback, *, write=False, permission=None):
            async def handler(request):
                body = await command_body(request) if write else await request.json()
                if not isinstance(body, dict):
                    raise AppError("invalid_input")
                context = await identity.for_request(
                    request,
                    actor_id=body.get("actor_id"),
                    person_id=body.get("person_id"),
                    conversation_id=body.get("conversation_id"),
                    permission=permission or ("memory.manage" if write else "memory.read"),
                )
                return json_response(await callback(context, body))

            app.router.add_post("/api/v1/memory/" + name, handler)

        route(
            "projection",
            lambda ctx, body: memory.projection(ctx, body.get("person_id"), body.get("group_id"),
                time_mode=body.get("time_mode","current"),valid_at=body.get("valid_at"),known_at=body.get("known_at")),
        )

        async def query(context,body):
            fields={"query","subject_ids","matter_refs","time_mode","valid_at","known_at","evidence_kinds","coverage_mode","budget"}
            plan=body["plan"] if "plan" in body else {name:value for name,value in body.items() if name in fields}
            seconds=min(10.0,max(0.1,float(memory.settings.get("retrieval",{}).get("request_seconds",5))))
            budget=RequestBudget(datetime.now(UTC)+timedelta(seconds=seconds))
            return await retrieval.query(context,plan,budget,cursor=body.get("cursor"),limit=body.get("limit",50))

        route("query",query)
        route(
            "claims",
            lambda ctx, body: memory.claims.list(
                ctx,
                subject_id=body.get("subject_id"),
                cursor=body.get("cursor"),
                limit=body.get("limit", 50),
                time_mode=body.get("time_mode","current"),valid_at=body.get("valid_at"),known_at=body.get("known_at"),
            ),
        )
        route("claims/get", lambda ctx, body: memory.claims.get(ctx, body["claim_id"],
            time_mode=body.get("time_mode","current"),valid_at=body.get("valid_at"),known_at=body.get("known_at")))
        route("claims/history", lambda ctx, body: memory.claims.history(ctx,body["claim_id"],
            cursor=body.get("cursor"),limit=body.get("limit",50)))
        route("claims/correct", memory.claims.correct, write=True)
        route("claims/share", memory.claims.share_ordinary, write=True, permission="memory.share")
        route(
            "matters",
            lambda ctx, body: memory.claims.matters(
                ctx,
                subject_id=body.get("subject_id"),
                cursor=body.get("cursor"),
                limit=body.get("limit", 50),
                time_mode=body.get("time_mode","current"),valid_at=body.get("valid_at"),known_at=body.get("known_at"),
            ),
        )
        route("matters/save", memory.claims.save_matter, write=True)
        route("matters/history", lambda ctx, body: memory.claims.matter_history(ctx,body["matter_id"],
            cursor=body.get("cursor"),limit=body.get("limit",50)))
        route("relationship", lambda ctx, body: memory.relationship(ctx, body["person_id"]))
        route("relationship/save", memory.save_relationship, write=True)
        route(
            "reflections",
            lambda ctx, body: memory.reflection_page(
                ctx,
                person_id=body.get("person_id"),
                cursor=body.get("cursor"),
                limit=body.get("limit", 40),
            ),
        )
        route(
            "episodes",
            lambda ctx, body: memory.episodes_since(ctx, body.get("cursor"), body.get("limit", 50)),
        )
        route(
            "episodes/between",
            lambda ctx, body: memory.episodes_between(
                ctx, body["start"], body["end"], body.get("limit", 200), body.get("cursor")
            ),
        )

        async def rebuild(context, body):
            async def apply(conn):
                await identity.check_context(context, connection=conn)
                rows = await (
                    await conn.execute(
                        "SELECT c.claim_id,c.current_revision AS revision FROM memory.claims c JOIN memory.claim_revisions r "
                        "ON r.claim_id=c.claim_id AND r.revision=c.current_revision WHERE c.actor_id=%s AND r.state='active' "
                        "AND sources.any_support_usable(r.support_groups,%s::text[]) "
                        "AND (%s::text IS NULL OR c.claim_id>%s) ORDER BY c.claim_id LIMIT 201",
                        (
                            context.actor_id,
                            list(context.scope_ids),
                            body.get("cursor"),
                            body.get("cursor"),
                        ),
                    )
                ).fetchall()
                for row in rows[:200]:
                    await memory.jobs.enqueue(
                        conn,
                        JobSpec(
                            "memory.index_object",
                            "memory-explicit-reindex:" + str(uuid4()),
                            {"kind":"claim","id":row["claim_id"],"revision":row["revision"]},
                            ServiceClass.ELASTIC,
                        ),
                    )
                return CommandResult(
                    body["client_request_id"],
                    data={
                        "queued": min(len(rows), 200),
                        "next_cursor": rows[199]["claim_id"] if len(rows) > 200 else None,
                    },
                )

            async def replay(conn, result):
                await identity.check_context(context, connection=conn)
                return result

            return await memory.commands.execute(
                context,
                "memory.index.rebuild",
                body["client_request_id"],
                body_hash(body),
                apply,
                on_replay=replay,
            )

        route("index/rebuild", rebuild, write=True)

        async def resume_extraction(context, body):
            async def apply(conn):
                return await workers.recovery.resume_on(conn,context,body)
            async def replay(conn,result):
                return await workers.recovery.replay_resume_on(conn,context,body,result)
            return await memory.commands.execute(context,"memory.extraction.resume",body["client_request_id"],
                body_hash(body),apply,on_replay=replay)

        route("extraction/resume", resume_extraction, write=True)
        route("extraction/status",lambda ctx,body:memory.extraction_status(ctx,body["episode_id"]))

    def tasks(queue, _runtime):
        queue.register("memory.extract", workers.extract, retryable=False)
        queue.register("memory.consolidate", workers.consolidate, retryable=False)
        queue.register("memory.observe_legacy", workers.observe_legacy, retryable=False)
        queue.register("memory.index_object", workers.index_object, retryable=False)
        queue.register("memory.index", workers.index, retryable=False)
        queue.register("memory.recover", workers.recover, retryable=True)
        queue.register("memory.purge_source", memory.claims.purge_source, retryable=True)

    async def start(runtime):
        if runtime.role == "worker":
            async with runtime.database.transaction() as conn:
                bucket = int(datetime.now(UTC).timestamp()) // 30
                await runtime.jobs.enqueue(
                    conn,
                    JobSpec(
                        "memory.recover", "memory-recovery:" + str(bucket), {}, ServiceClass.DUE
                    ),
                )

    return DomainModule(
        "memory",
        routes,
        start=start,
        register_tasks=tasks,
        migrations=(
            Migration("0020_memory", files("tianshu.memory").joinpath("schema.sql").read_text()),
            Migration("0021_memory_consolidation", files("tianshu.memory").joinpath("0021_memory_consolidation.sql").read_text()),
        ),
    )
