"""Remove withdrawn result bodies from the native queue, keeping their identity."""
from .codec import document, source_refs
from . import rbg_result_ledger as ledger
from tianshu.runtime.errors import AppError
from tianshu.sources.dependencies import intersects, unique_refs


def event_refs(event):
    refs = list(source_refs(event.get("source_refs") or ()))
    receipt = event.get("receipt") or {}
    result = receipt.get("result") or {}
    refs.extend(source_refs(result.get("source_refs") or ()))
    consumption = result.get("consumption")
    nested_source = consumption.get("source") if isinstance(consumption, dict) else None
    for value in (result.get("source"), nested_source):
        if isinstance(value, dict) and "source_id" in value:
            refs.extend(source_refs((value,)))
    for artifact in receipt.get("artifacts") or ():
        refs.extend(source_refs(artifact.get("source_refs") or ()))
    return unique_refs(tuple(refs))


def redacted_event(event, kind):
    receipt = event["receipt"]
    return {"type": event["type"], "version": event["version"], "event_id": event["event_id"],
        "action_id": event["action_id"], "result_revision": event["result_revision"],
        "result_observed_at": event.get("result_observed_at"), "context": event["context"],
        "source_refs": [], "source_invalidated": True, "source_invalidation_kind": kind,
        "receipt": {"receipt_id": receipt["receipt_id"], "action_id": receipt["action_id"],
            "attempt_id": receipt.get("attempt_id"), "execution": receipt["execution"],
            "effect": receipt["effect"], "delivery": receipt["delivery"],
            "artifacts": [], "coverage": None, "result": {"source_revoked": True},
            "error_code": "source_revoked"}}


async def matching_jobs(connection, selector, *, actor_id=None, filter_actor=False):
    # SQL narrows by source ID; the normal dependency matcher below checks the
    # exact version and forgotten fragment before any body is removed.
    actor_filter = " AND event->'context'->>'actor_id' IS NOT DISTINCT FROM %s" if filter_actor else ""
    parameters = (selector, selector, actor_id) if filter_actor else (selector, selector)
    return await (await connection.execute(
        "WITH result_jobs AS (SELECT j.id,j.args,k.data_generation,j.args->'event' AS event "
        "FROM procrastinate_jobs j LEFT JOIN runtime.job_keys k ON k.job_id=j.id "
        "WHERE j.task_name='conversation.result' AND j.args->'event'->>'type'='capability.result') "
        "SELECT id,args,data_generation,event FROM result_jobs WHERE (EXISTS (SELECT 1 FROM jsonb_array_elements("
        "COALESCE(NULLIF(event->'source_refs','null'::jsonb),'[]'::jsonb)||"
        "COALESCE(NULLIF(event->'receipt'->'result'->'source_refs','null'::jsonb),'[]'::jsonb)||"
        "CASE WHEN jsonb_typeof(event->'receipt'->'result'->'source')='object' "
        "THEN jsonb_build_array(event->'receipt'->'result'->'source') ELSE '[]'::jsonb END||"
        "CASE WHEN jsonb_typeof(event->'receipt'->'result'->'consumption'->'source')='object' "
        "THEN jsonb_build_array(event->'receipt'->'result'->'consumption'->'source') ELSE '[]'::jsonb END"
        ") ref WHERE ref->>'source_id'=ANY(%s)) OR EXISTS (SELECT 1 FROM "
        "jsonb_array_elements(COALESCE(NULLIF(event->'receipt'->'artifacts','null'::jsonb),'[]'::jsonb)) artifact "
        "CROSS JOIN LATERAL jsonb_array_elements(COALESCE(NULLIF(artifact->'source_refs','null'::jsonb),'[]'::jsonb)) ref "
        "WHERE ref->>'source_id'=ANY(%s)))" + actor_filter + " ORDER BY id", parameters)).fetchall()


async def actors_for_source(connection, changes, selector, kind):
    rows = await matching_jobs(connection, selector)
    return {row["event"]["context"].get("actor_id") for row in rows
            if intersects(event_refs(row["event"]), changes, kind)}


async def invalidate(connection, changes, selector, kind, *, actor_id=None, filter_actor=False):
    rows = await matching_jobs(connection, selector, actor_id=actor_id, filter_actor=filter_actor)
    for candidate in rows:
        # Recheck the saved event after locking; no stale selection may overwrite
        # a new payload or turn a disjoint fragment into a deletion.
        row = await (await connection.execute(
            "SELECT j.args,k.data_generation FROM procrastinate_jobs j LEFT JOIN runtime.job_keys k ON k.job_id=j.id "
            "WHERE j.id=%s AND j.task_name='conversation.result' FOR UPDATE OF j", (candidate["id"],))).fetchone()
        if not row:
            continue
        event = row["args"].get("event") or {}
        if event.get("source_invalidated") or not intersects(event_refs(event), changes, kind):
            continue
        if filter_actor and event.get("context", {}).get("actor_id") != actor_id:
            continue
        cleaned = redacted_event(event, kind)
        await connection.execute("UPDATE procrastinate_jobs SET args=%s WHERE id=%s",
                                 (document({**row["args"], "event": cleaned}), candidate["id"]))
        generation = row["data_generation"]
        if generation is None:
            continue  # An unbound legacy job cannot be executed in the current generation.
        existing = await (await connection.execute(
            "SELECT action_id,result_revision FROM conversation.result_deliveries WHERE data_generation=%s AND receipt_id=%s",
            (generation, cleaned["event_id"]))).fetchone()
        if existing and (existing["action_id"] != cleaned["action_id"]
                         or existing["result_revision"] != cleaned["result_revision"]):
            continue  # Retain the identity collision; source deletion must still commit.
        try:
            # A concurrent historical identity collision must not roll back the
            # redaction of this native payload.
            async with connection.transaction():
                await ledger.invalidate_on(connection, generation, cleaned, "source_unavailable")
        except AppError as error:
            if error.code not in {"result_event_identity_mismatch", "result_receipt_revision_conflict"}:
                raise
