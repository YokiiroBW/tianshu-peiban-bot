"""Retire an obsolete plan without changing an unknown Models outcome."""

from datetime import datetime, timedelta

from tianshu.conversation.codec import context_from_record
from tianshu.conversation.generation import attempt_for
from tianshu.runtime.errors import AppError
from . import plan_recovery


def observe_after(status, now, count=0):
    if status not in {None,"queued","running","unknown"}:
        return None
    return now+timedelta(seconds=min(21600,600*2**min(max(count,0),6)))


def _deadline(metadata):
    if not metadata or not metadata.get("request_present"):
        return None
    raw = (metadata.get("business_deadline") if metadata.get("execution_timeout") is not None
           else metadata.get("legacy_deadline"))
    if not raw:
        return None
    try:
        value = datetime.fromisoformat(raw.replace("Z","+00:00"))
    except (TypeError,ValueError):
        return None
    return value if value.utcoffset() is not None else None


async def request_metadata_on(connection, key):
    return await (await connection.execute(
        "SELECT attempt_id,state,(request IS NOT NULL) AS request_present,"
        "request->>'execution_timeout_seconds' AS execution_timeout,"
        "request->>'business_deadline_at' AS business_deadline,"
        "request#>>'{budget,deadline_at}' AS legacy_deadline "
        "FROM conversation.generation_requests WHERE material_key=%s",(key,))).fetchone()


def classification(mark, generation, request, now):
    if not mark or not mark.get("generation_key") or mark.get("generation_consumed"):
        return None
    if not generation or generation.get("generation_key") != mark["generation_key"]:
        return None
    if generation.get("state") in {"superseded","cancelled","rejected","expired"}:
        return "superseded"
    if generation.get("recovery_state") == "cancelled":
        return "superseded"
    if request and request.get("state") == "invalidated":
        return "source_withdrawn"
    deadline = _deadline(request)
    if request and (request.get("state") == "expired" or (deadline is not None and deadline <= now)):
        return "expired"
    return None


async def retire(engine, context, mark, generation, request, reason, now):
    """Persist a retry wake, seal Models, then CAS the original domain pointer."""
    if not generation.get("recovery_lineage_id"):
        await engine.waiting(context,mark,"planner_observation_unavailable",now)
        return {"state":"blocked","reason":"planner_recovery_lineage_missing"}
    attempt_id = attempt_for(mark["generation_key"])
    if request and request["attempt_id"] != attempt_id:
        raise AppError("planner_original_attempt_mismatch",status=409)
    async with engine.transaction() as connection:
        current = await (await connection.execute(
            "SELECT * FROM life.plan_watermarks WHERE actor_id=%s FOR UPDATE",(context.actor_id,))).fetchone()
        if (not current or current["revision"] != mark["revision"]
                or current["generation_key"] != mark["generation_key"] or current["generation_consumed"]):
            return {"state":"changed"}
        await connection.execute("UPDATE life.plan_generations SET retirement_reason=%s,"
            "retirement_requested_at=COALESCE(retirement_requested_at,now()) WHERE generation_key=%s AND retired_at IS NULL",
            (reason,mark["generation_key"]))
        await engine.schedule(connection,context,current,now+timedelta(seconds=30),"retirement-observe")
    sealed = await engine.worker.models.seal_admission(attempt_id,
        lineage_id=generation["recovery_lineage_id"],data_generation=engine.data_generation,
        context=context,original_context=context_from_record(generation["context"]),
        reason="expired" if reason == "expired" else "superseded")
    if (sealed.get("attempt_id") != attempt_id or sealed.get("lineage_id") != generation["recovery_lineage_id"]
            or sealed.get("data_generation") != engine.data_generation
            or sealed.get("state") not in {"sealed_absent","sealed_queued","sealed_existing","explicit_cancelled"}):
        raise AppError("invalid_model_admission_retirement_response",status=502)
    explicit = (sealed.get("explicit_cancel") is True or sealed["state"] == "explicit_cancelled"
                or generation.get("reason") == "explicit_cancel")
    async with engine.transaction() as connection:
        current = await (await connection.execute(
            "SELECT * FROM life.plan_watermarks WHERE actor_id=%s FOR UPDATE",(context.actor_id,))).fetchone()
        if not current or current["generation_key"] != mark["generation_key"] or current["generation_consumed"]:
            return {"state":"changed"}
        if current["revision"] != mark["revision"]:
            await engine.schedule(connection,context,current,now,"retirement-recheck")
            return {"state":"changed"}
        old = await plan_recovery.generation_on(connection,mark["generation_key"],lock=True)
        current_request = await request_metadata_on(connection,mark["generation_key"])
        if (not old or old.get("recovery_lineage_id") != generation["recovery_lineage_id"]
                or (classification(current,old,current_request,now) != reason
                    and not (old.get("retirement_requested_at") and old.get("retirement_reason") == reason))):
            await engine.schedule(connection,context,current,now,"retirement-recheck")
            return {"state":"changed"}
        if old.get("recovery_state") != "cancelled":
            await plan_recovery.cancel_on(engine.life,connection,old,old["recovery_version"])
        await connection.execute(
            "UPDATE life.plan_generations SET retirement_reason=%s,retirement_model_state=%s,"
            "retirement_original_status=%s,retirement_observe_after=%s,"
            "retirement_data_generation=%s,retired_at=COALESCE(retired_at,now()),"
            "state=CASE WHEN state IN ('pending','reviewed','retry_wait') THEN 'superseded' ELSE state END "
            "WHERE generation_key=%s",("explicit_cancel" if explicit else reason,sealed["state"],
                sealed.get("status"),observe_after(sealed.get("status"),now),engine.data_generation,mark["generation_key"]))
        await connection.execute(
            "UPDATE life.plan_watermarks SET generation_consumed=true,reason=%s,last_error=%s,"
            "repair_due_at=NULL,review_after=NULL,next_due_at=now() WHERE actor_id=%s",
            ("planner_cancelled" if explicit else "planner_retired",
             "explicit_cancel" if explicit else None,context.actor_id))
        if not explicit:
            await engine.schedule(connection,context,current,now,"retired")
    return {"state":"cancelled" if explicit else "retired","model_state":sealed["state"]}
