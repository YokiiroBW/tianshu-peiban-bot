"""Remove withdrawn result bodies from the native queue, keeping their identity."""
from datetime import datetime
from .codec import signature, source_refs
from . import rbg_result_ledger as ledger
from tianshu.runtime.errors import AppError
from tianshu.runtime.recovery_policy import RecoveryWait
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
        "FROM procrastinate_jobs j LEFT JOIN LATERAL (SELECT data_generation FROM runtime.job_keys k "
        "WHERE k.job_id=j.id ORDER BY k.cancelled_at NULLS FIRST,k.created_at,k.key LIMIT 1) k ON true "
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


async def invalidate(connection, changes, selector, kind, *, actor_id=None, filter_actor=False, jobs=None):
    rows = await matching_jobs(connection, selector, actor_id=actor_id, filter_actor=filter_actor)
    if jobs is None:
        from tianshu.runtime.rbj_wake import redact_wake_on as redact
    else:
        redact = jobs.redact_wake_on
    changed_refs = sorted((ref for values in changes.values() for ref in values), key=signature)
    # A receipt can have several physical jobs. Order by the domain lock key,
    # not by job id, so overlapping fragment batches acquire deliveries alike.
    rows.sort(key=lambda row: (row.get("data_generation") or "",
                               ((row.get("args") or {}).get("event") or {}).get("event_id") or "", row["id"]))
    for candidate in rows:
        row = candidate
        for _ in range(3):
            event = (row["args"] or {}).get("event") or {}
            if event.get("source_invalidated") or not intersects(event_refs(event), changes, kind):
                break
            if filter_actor and event.get("context", {}).get("actor_id") != actor_id:
                break
            cleaned = redacted_event(event, kind)
            generation = row["data_generation"]
            try:
                action_id, receipt_id, revision = ledger.event_identity(event)
            except AppError:
                wake_key = None  # The Runtime port still scrubs unverifiable legacy payloads.
            else:
                wake_key = (f"conversation.result:{generation}:{action_id}:{revision}:{receipt_id}"
                            if generation is not None else None)
            invalidation_ref = "source-redaction:" + signature(
                [kind, changed_refs, actor_id if filter_actor else None, candidate["id"]])
            try:
                # Domain ledger is locked before Runtime's object/native locks.
                # A CAS retry rolls this savepoint back, including any new row.
                async with connection.transaction():
                    delivery = None
                    if generation is not None and wake_key is not None:
                        delivery = await (await connection.execute(
                            "SELECT action_id,result_revision FROM conversation.result_deliveries "
                            "WHERE data_generation=%s AND receipt_id=%s FOR UPDATE",
                            (generation, receipt_id))).fetchone()
                        if not delivery:
                            await ledger.register_on(connection, generation, event)
                            delivery = {"action_id": action_id, "result_revision": revision}
                    result = await redact(connection, candidate["id"], expected_task="conversation.result",
                        expected_generation=generation, expected_payload=row["args"],
                        redacted_payload={**row["args"], "event": cleaned}, invalidation_ref=invalidation_ref,
                        wake_object_key=wake_key)
                    if result.get("state") == "changed":
                        raise _PayloadChanged()
                    if result.get("state") == "missing":
                        raise _JobMissing()
                    if result.get("state") == "waiting" and result.get("reason") == "wake_lock_busy":
                        retry_at = result.get("retry_at")
                        if not isinstance(retry_at, datetime) or retry_at.utcoffset() is None:
                            raise AppError("result_redaction_wait_invalid", status=503)
                        raise RecoveryWait("source_redaction_lock_busy", retry_at, dependency="runtime:wake_redaction")
                    if result.get("state") not in {"redacted", "already_redacted"}:
                        raise AppError(result.get("reason") or "result_redaction_unconfirmed", status=409)
                    if delivery and (delivery["action_id"], delivery["result_revision"]) == (action_id, revision):
                        await ledger.invalidate_on(connection, generation, cleaned, "source_unavailable")
                break
            except _JobMissing:
                break
            except _PayloadChanged:
                pass
            except AppError as error:
                if error.code not in {"result_event_identity_mismatch", "result_receipt_revision_conflict"}:
                    raise
            # Runtime owns object/native lock order. Re-read without taking a
            # native lock here, then recheck the exact fragment and actor.
            row = await (await connection.execute(
                "SELECT j.id,j.args,k.data_generation FROM procrastinate_jobs j LEFT JOIN LATERAL "
                "(SELECT data_generation FROM runtime.job_keys k WHERE k.job_id=j.id "
                "ORDER BY k.cancelled_at NULLS FIRST,k.created_at,k.key LIMIT 1) k ON true "
                "WHERE j.id=%s AND j.task_name='conversation.result'", (candidate["id"],))).fetchone()
            if not row:
                break
        else:
            raise AppError("result_redaction_concurrent_change", status=409)


class _PayloadChanged(Exception):
    pass


class _JobMissing(Exception):
    pass
