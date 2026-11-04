"""Memory use cases, selected-source propagation and the incremental worker registry."""

from base64 import urlsafe_b64decode, urlsafe_b64encode
from datetime import UTC, datetime
from importlib.resources import files
import json

from tianshu.runtime.api import CommandResult, command_body, json_response
from tianshu.runtime.commands import body_hash
from tianshu.runtime.composition import DomainModule
from tianshu.runtime.errors import AppError
from tianshu.runtime.execution import ExecutionLane
from tianshu.runtime.jobs import JobSpec
from tianshu.runtime.migrations import Migration
from tianshu.types import ServiceClass


_INDEX_KINDS = ("claim","episode","hypothesis","matter")


def index_cursor(value,kinds):
    if value is None:
        return "",""
    try:
        if not isinstance(value,str) or not value.startswith("i1:") or len(value)>2048:
            raise ValueError
        selected=json.loads(urlsafe_b64decode(value[3:]).decode())
        if selected["kinds"] != list(kinds) or selected["kind"] not in kinds or not isinstance(selected["id"],str):
            raise ValueError
        return selected["kind"],selected["id"]
    except (ValueError,TypeError,KeyError,UnicodeError):
        raise AppError("invalid_memory_index_cursor") from None


def next_index_cursor(row,kinds):
    return "i1:"+urlsafe_b64encode(json.dumps({"kind":row["kind"],"id":row["id"],"kinds":list(kinds)},
        ensure_ascii=False,separators=(",",":")).encode()).decode()


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
            budget=retrieval.foreground_budget()
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
        route("matters/get", lambda ctx, body: memory.claims.get_matter(ctx, body["matter_id"]))
        route("matters/save", memory.claims.save_matter, write=True)
        route("matters/history", lambda ctx, body: memory.claims.matter_history(ctx,body["matter_id"],
            cursor=body.get("cursor"),limit=body.get("limit",50)))
        route("relationship", lambda ctx, body: memory.relationship(ctx, body["person_id"]))
        route("relationship/save", memory.save_relationship, write=True)
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
        route("episodes/get", lambda ctx, body: memory.episode_at(ctx, body["episode_id"],
            time_mode=body.get("time_mode", "current"), valid_at=body.get("valid_at"),
            known_at=body.get("known_at")))

        async def rebuild(context, body):
            requested=body.get("kinds",[body["kind"]] if "kind" in body else list(_INDEX_KINDS))
            if not isinstance(requested,list) or not requested or any(kind not in _INDEX_KINDS for kind in requested):
                raise AppError("memory_index_kind_invalid")
            kinds=tuple(sorted(set(requested)))
            after_kind,after_id=index_cursor(body.get("cursor"),kinds)

            async def page_on(conn,*,enqueue):
                await identity.check_context(context, connection=conn)
                rows = await (
                    await conn.execute(
                        "WITH candidates AS (SELECT CASE WHEN r.memory_kind='hypothesis' THEN 'hypothesis' ELSE 'claim' END AS kind,"
                        "c.claim_id AS id,c.current_revision AS revision,r.support_groups,r.source_refs FROM memory.claims c JOIN memory.claim_revisions r "
                        "ON r.claim_id=c.claim_id AND r.revision=c.current_revision WHERE c.actor_id=%s AND r.state='active' "
                        "AND r.current_status='current' AND sources.any_support_usable(r.support_groups,%s::text[]) "
                        "UNION ALL SELECT 'matter',matter_id,revision,support_groups,source_refs FROM memory.matters WHERE actor_id=%s "
                        "AND active AND current_status='current' AND sources.any_support_usable(support_groups,%s::text[]) "
                        "UNION ALL SELECT 'episode',episode_id,revision,'[]'::jsonb,source_refs||supplement_refs FROM memory.episodes WHERE actor_id=%s "
                        "AND active AND scope_ids<@%s::text[]) SELECT * FROM candidates WHERE kind=ANY(%s::text[]) "
                        "AND (kind,id)>(%s,%s) ORDER BY kind,id LIMIT 201",
                        (
                            context.actor_id,list(context.scope_ids),
                            context.actor_id,list(context.scope_ids),context.actor_id,list(context.scope_ids),
                            list(kinds),after_kind,after_id,
                        ),
                    )
                ).fetchall()
                from .evidence import select_supports_on
                selected=await select_supports_on(memory.sources,conn,context,rows[:200])
                queued=0
                for row,refs in zip(rows[:200],selected,strict=True):
                    if enqueue and refs:
                        await memory.enqueue_index_on(conn,kind=row["kind"],object_id=row["id"],revision=row["revision"],
                            key=f"memory-explicit-reindex:{context.principal_id}:{body['client_request_id']}:{row['kind']}:{row['id']}:{row['revision']}")
                        queued+=1
                return CommandResult(
                    body["client_request_id"],
                    data={
                        "queued":queued,"kinds":kinds,"replayed":not enqueue,
                        "next_cursor": next_index_cursor(rows[199],kinds) if len(rows)>200 else None,
                        "coverage":{"complete":len(rows)<=200 and all(selected),
                            "missing":[] if all(selected) else ["unavailable_sources"]},
                    },
                )

            async def apply(conn):
                return await page_on(conn,enqueue=True)

            async def replay(conn, result):
                return await page_on(conn,enqueue=False)

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
        queue.register("memory.index_retry", workers.index_retry, retryable=False, execution_lane=ExecutionLane.MEMORY_INDEX)
        queue.register("memory.recover", workers.recover, retryable=True)

    async def start(runtime):
        if runtime.role == "bot-background":
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
            Migration("0022_memory_runtime", files("tianshu.memory").joinpath("0022_memory_runtime.sql").read_text()),
            Migration("0023_memory_index_retry", files("tianshu.memory").joinpath("0023_memory_index_retry.sql").read_text()),
            Migration("0024_memory_consolidation_retry", files("tianshu.memory").joinpath("0024_memory_consolidation_retry.sql").read_text()),
            Migration("0025_memory_write", files("tianshu.memory").joinpath("0025_memory_write.sql").read_text()),
        ),
    )
