"""Reclassify bounded Life originals and wake their existing domain jobs."""

from datetime import UTC, datetime
from uuid import uuid4

from tianshu.conversation.codec import context_from_record, source_refs
from tianshu.conversation.generation import attempt_for
from tianshu.models.retry import transient_model_failure
from tianshu.models.timing import waiting_budget
from tianshu.runtime.errors import AppError
from tianshu.runtime.jobs import JobSpec
from tianshu.sources.dependencies import unique_refs
from . import plan_recovery
from .computation_retry import validate_original_receipt
from .diary_materials import episode_versions
from .generation_control import frozen_data_generation
from .planning import wake_lock, wake_payload
from .recovery import scheduled_context


async def _candidates(connection, after, limit):
    return await (await connection.execute(
        "SELECT * FROM ("
        "SELECT 'plan' AS kind,w.actor_id AS item_id,g.generation_key AS original_id,"
        "'plan:'||c.generation_key||':'||CASE WHEN g.generation_key=c.generation_key THEN 'z' ELSE 'a' END "
        "||g.generation_key AS scan_key FROM life.plan_watermarks w "
        "JOIN life.plan_generations c ON c.generation_key=w.generation_key "
        "JOIN life.plan_generations g ON g.recovery_lineage_id=c.recovery_lineage_id "
        "WHERE w.generation_consumed AND w.reason IN ('planner_attempts_exhausted','planner_retry_wait') "
        "AND w.last_error='transport_not_sent' AND c.state='retry_wait' AND c.recovery_state='failed' "
        "AND c.retired_at IS NULL AND g.recovery_state='failed' "
        "UNION ALL SELECT 'diary',d.id,g.generation_id,'diary:'||d.generation_id||':'||"
        "CASE WHEN g.generation_id=d.generation_id THEN 'z' ELSE 'a' END||g.generation_id "
        "FROM life.diaries d JOIN life.generation_recipes c ON c.generation_id=d.generation_id "
        "JOIN life.generation_recipes g ON g.recovery_lineage_id=c.recovery_lineage_id AND g.kind='diary' "
        "WHERE d.state IN ('failed','unknown') AND d.reason='transport_not_sent' "
        "AND NOT d.generation_cancel_requested "
        "UNION ALL SELECT 'chapter',c.id,g.generation_id,'chapter:'||c.generation_id||':'||"
        "CASE WHEN g.generation_id=c.generation_id THEN 'z' ELSE 'a' END||g.generation_id "
        "FROM life.chapters c JOIN life.generation_recipes current_recipe ON current_recipe.generation_id=c.generation_id "
        "JOIN life.generation_recipes g ON g.recovery_lineage_id=current_recipe.recovery_lineage_id AND g.kind='chapter' "
        "WHERE c.state IN ('failed','unknown') AND c.reason='transport_not_sent' "
        "AND NOT c.generation_cancel_requested) candidates "
        "WHERE scan_key>%s ORDER BY scan_key LIMIT %s",(after,limit))).fetchall()


async def _snapshot(worker, candidate):
    life, kind, identifier = worker.life, candidate["kind"], candidate["item_id"]
    async with life.database.transaction(read_only=True) as connection:
        data_generation = await life.jobs.current_generation_on(connection)
        if kind == "plan":
            item = await (await connection.execute(
                "SELECT * FROM life.plan_watermarks WHERE actor_id=%s",(identifier,))).fetchone()
            current = await plan_recovery.generation_on(connection,item["generation_key"]) if item else None
            original = await plan_recovery.generation_on(connection,candidate["original_id"])
            return item,current,original,data_generation
        table = "life.diaries" if kind == "diary" else "life.chapters"
        item = await (await connection.execute(f"SELECT * FROM {table} WHERE id=%s",(identifier,))).fetchone()
        current = await (await connection.execute("SELECT * FROM life.generation_recipes WHERE generation_id=%s",
            (item["generation_id"],))).fetchone() if item and item["generation_id"] else None
        original = await (await connection.execute("SELECT * FROM life.generation_recipes WHERE generation_id=%s",
            (candidate["original_id"],))).fetchone()
        return item,current,original,data_generation


def _eligible(candidate, item, current, original):
    if not item or not current or not original or not current.get("recovery_lineage_id"):
        return False
    if (current["recovery_lineage_id"] != original.get("recovery_lineage_id")
            or current["recovery_owner"] != original.get("recovery_owner")
            or current["recovery_version"] != original.get("recovery_version")):
        return False
    if candidate["kind"] == "plan":
        if current["actor_id"] != item["actor_id"] or original["actor_id"] != item["actor_id"]:
            return False
        return (item["generation_key"] == current["generation_key"] and item["generation_consumed"]
            and item["planned_watermark"] == item["change_watermark"]
            and item["reason"] in {"planner_attempts_exhausted","planner_retry_wait"}
            and item["last_error"] == "transport_not_sent" and current["state"] == "retry_wait"
            and current["recovery_state"] == "failed" and not current.get("retired_at")
            and original.get("recovery_state") == "failed")
    if any(row["kind"] != candidate["kind"] or row["item_id"] != item["id"] for row in (current,original)):
        return False
    return (item["generation_id"] == current["generation_id"] and item["state"] in {"failed","unknown"}
        and item["reason"] == "transport_not_sent" and not item["generation_cancel_requested"]
        and isinstance(item.get("generation_recipe"),dict) and isinstance(original.get("recipe"),dict))


async def _observe(worker, candidate, item, current, original, data_generation):
    life, kind = worker.life, candidate["kind"]
    value = current["context"] if kind != "chapter" else item["generation_context"]
    context = await scheduled_context(life,value)
    await life.identity.check(context)
    if kind in {"plan","diary"} and context.actor_id != item["actor_id"]:
        raise AppError("actor_scope_mismatch",status=403)
    actor = await life.companion.actor(context)
    if not actor["enabled"]:
        raise AppError("actor_disabled",status=409)
    current_id = attempt_for(current["generation_key"]) if kind == "plan" else current["generation_id"]
    original_id = attempt_for(original["generation_key"]) if kind == "plan" else original["generation_id"]
    current_request = await worker.generations.observe_request(current_id,observer=context)
    original_request = (current_request if original_id == current_id else
        await worker.generations.observe_request(original_id,observer=context))
    if not current_request or not original_request:
        raise AppError("generation_original_unavailable",status=409)
    if current_request.data_generation != data_generation or original_request.data_generation != data_generation:
        raise AppError("job_generation_expired",status=409)
    deadline = waiting_budget(current_request)
    if deadline is not None and deadline.deadline_at <= datetime.now(UTC):
        raise AppError("generation_request_expired",status=409)
    latest = await worker.models.status(current_id)
    receipt = latest if original_id == current_id else await worker.models.status(original_id)
    if latest is None or receipt is None:
        raise AppError("model_observation_unavailable",status=409)
    validate_original_receipt(current_request,latest,data_generation)
    validate_original_receipt(original_request,receipt,data_generation)
    if (latest.status != "failed" or latest.cancellation_reason == "explicit_cancel"
            or not transient_model_failure(latest.error_code)):
        raise AppError("original_not_retryable",status=409)
    if receipt.status != "failed" or receipt.cancellation_reason == "explicit_cancel":
        raise AppError("original_not_retryable",status=409)
    return context,current_request,original_request,latest,receipt


def _source_refs(item, current, original, current_request, original_request):
    return unique_refs(current_request.source_refs,original_request.source_refs,
        source_refs(current.get("source_refs") or ()),source_refs(original.get("source_refs") or ()),
        source_refs(item.get("source_refs") or ()),
        source_refs((item.get("generation_recipe") or {}).get("source_refs") or ()))


async def _recheck_domain(worker, connection, candidate, item, context, data_generation,
                          chapter_before=None, related_before=()):
    life, kind = worker.life, candidate["kind"]
    if kind == "plan":
        routine = await (await connection.execute(
            "SELECT * FROM life.routines WHERE actor_id=%s FOR SHARE",(context.actor_id,))).fetchone()
        if routine and not routine["enabled"]:
            raise AppError("routine_disabled",status=409)
        return routine,None
    recipe = item["generation_recipe"]
    frozen_data_generation(recipe,data_generation)
    if kind == "chapter":
        if worker.writing is None:
            raise AppError("writing_unavailable",status=503)
        chapter = await worker.writing._chapter(context,item["id"],connection)
        work = await (await connection.execute("SELECT * FROM life.works WHERE id=%s AND actor_id=%s "
            "AND scope_ids<@%s::text[] FOR SHARE",(chapter["work_id"],context.actor_id,list(context.scope_ids)))).fetchone()
        related = await worker.writing._version_refs(chapter,connection)
        if (not work or not chapter_before or chapter["revision"] != chapter_before["revision"]
                or related != related_before or work["revision"] != recipe.get("work_revision")
                or not await worker.writing._previous_current(connection,work["id"],recipe)):
            raise AppError("writing_material_changed",status=409)
        return None,None
    versions = tuple((entry["episode_id"],entry["revision"]) for entry in recipe.get("episodes",()))
    if set(versions) != await episode_versions(life.memory,context,versions,connection=connection):
        raise AppError("diary_episode_revised",status=409)
    automatic = recipe.get("automatic")
    if not automatic:
        return None,None
    day = await (await connection.execute(
        "SELECT * FROM life.diary_days WHERE actor_id=%s AND scope_key=%s AND day=%s FOR UPDATE",
        (automatic["actor_id"],automatic["scope_key"],automatic["day"]))).fetchone()
    if (not day or day["actor_id"] != context.actor_id or day["revision"] != automatic["revision"]
            or day["reviewed_revision"] >= automatic["revision"]
            or day["generation_id"] not in {None,item["generation_id"]}):
        raise AppError("diary_material_changed",status=409)
    return None,day


async def _reconcile_one(worker, candidate):
    life, kind = worker.life, candidate["kind"]
    item,current,original,data_generation = await _snapshot(worker,candidate)
    if not _eligible(candidate,item,current,original):
        return False,"material_changed"
    try:
        context,current_request,original_request,latest,receipt = await _observe(
            worker,candidate,item,current,original,data_generation)
    except AppError as error:
        if error.status in {401,403,410} and error.code != "actor_scope_mismatch":
            return False,error.code
        raise
    original_id = candidate["original_id"]
    async with life.database.transaction() as connection:
        await life.jobs.assert_generation_on(connection,data_generation)
        base_refs = _source_refs(item,current,original,current_request,original_request)
        chapter_before,related_before = None,()
        if kind == "chapter":
            if worker.writing is None:
                raise AppError("writing_unavailable",status=503)
            chapter_before = await worker.writing._chapter(context,item["id"],connection)
            related_before = await worker.writing._version_refs(chapter_before,connection)
        refs = unique_refs(base_refs,related_before)
        await life.validate_persistence(connection,context,refs)
        if kind == "plan":
            fresh = await (await connection.execute(
                "SELECT * FROM life.plan_watermarks WHERE actor_id=%s FOR UPDATE",(item["actor_id"],))).fetchone()
            current_fresh = await plan_recovery.generation_on(connection,current["generation_key"],lock=True)
            original_fresh = (current_fresh if original["generation_key"] == current["generation_key"] else
                await plan_recovery.generation_on(connection,original["generation_key"],lock=True))
        else:
            table = "life.diaries" if kind == "diary" else "life.chapters"
            fresh = await (await connection.execute(f"SELECT * FROM {table} WHERE id=%s FOR UPDATE",(item["id"],))).fetchone()
            current_fresh = await (await connection.execute("SELECT * FROM life.generation_recipes "
                "WHERE generation_id=%s FOR UPDATE",(current["generation_id"],))).fetchone()
            original_fresh = (current_fresh if original["generation_id"] == current["generation_id"] else
                await (await connection.execute("SELECT * FROM life.generation_recipes WHERE generation_id=%s FOR UPDATE",
                    (original["generation_id"],))).fetchone())
        if not _eligible(candidate,fresh,current_fresh,original_fresh) or fresh["revision"] != item["revision"]:
            return False,"material_changed"
        if current_fresh["context"] != current["context"] or original_fresh["context"] != original["context"]:
            return False,"material_changed"
        if kind != "plan" and (fresh["generation_recipe"] != item["generation_recipe"]
                or current_fresh["recipe"] != current["recipe"] or original_fresh["recipe"] != original["recipe"]):
            return False,"material_changed"
        if _source_refs(fresh,current_fresh,original_fresh,current_request,original_request) != base_refs:
            return False,"material_changed"
        routine,day = await _recheck_domain(worker,connection,candidate,fresh,context,data_generation,
            chapter_before,related_before)
        from .rbi_model_recovery import lock_original_on
        # Both observations can point at different physical attempts. Acquire
        # their Models locks in a stable order before Runtime touches the lineage.
        originals = {original_request.attempt_id:(original_request,receipt),
                     current_request.attempt_id:(current_request,latest)}
        for attempt in sorted(originals):
            request, physical_receipt = originals[attempt]
            await lock_original_on(connection,request,physical_receipt,data_generation)
        policy = plan_recovery.policy_for(life)
        attempt_id = attempt_for(original_id) if kind == "plan" else original_id
        budget = await policy.note_not_sent_on(connection,attempt_id,owner=original_fresh["recovery_owner"],
            expected_version=original_fresh["recovery_version"],model_receipt=receipt)
        if not budget["can_retry"] or budget.get("retry_at") is None:
            return False,budget["reason"]
        current_id = current_fresh["generation_key"] if kind == "plan" else current_fresh["generation_id"]
        if original_id != current_id:
            # Older records are classified first; only the authoritative latest
            # failure may restore this lineage's native wake.
            return False,"history_classified"
        when = budget["retry_at"]
        if kind == "plan":
            from .clock import awake_and_due
            clock_routine = routine or {"timezone":"Asia/Shanghai","wake_at":"08:00",
                "sleep_at":"23:00","idle_seconds":5400}
            awake,wake_at = awake_and_due(clock_routine,datetime.now(UTC))
            if not awake:
                when = max(when,wake_at)
            if fresh["repair_due_at"] is not None:
                when = max(when,fresh["repair_due_at"])
            await connection.execute("UPDATE life.plan_watermarks SET repair_due_at=%s,next_due_at=%s,"
                "reason='planner_retry_wait' WHERE actor_id=%s",(when,when,fresh["actor_id"]))
            await life.jobs.enqueue(connection,JobSpec("life.plan",f"rbh-plan:{data_generation}:{uuid4()}",
                wake_payload(fresh["actor_id"],data_generation),scheduled_at=when,
                subject_id=fresh["actor_id"],data_generation=data_generation),queueing_lock=wake_lock(fresh["actor_id"]))
        else:
            if fresh["generation_retry_due_at"] is not None:
                when = max(when,fresh["generation_retry_due_at"])
            table = "life.diaries" if kind == "diary" else "life.chapters"
            if day and day["generation_id"] is None:
                await connection.execute("UPDATE life.diary_days SET generation_id=%s,generation_revision=%s,"
                    "updated_at=now() WHERE actor_id=%s AND scope_key=%s AND day=%s",
                    (fresh["generation_id"],day["revision"],day["actor_id"],day["scope_key"],day["day"]))
            if fresh["state"] != "unknown" or fresh["generation_retry_due_at"] != when:
                await connection.execute(f"UPDATE {table} SET state='unknown',generation_retry_due_at=%s,"
                    "revision=revision+1 WHERE id=%s",(when,fresh["id"]))
            task = "life.diary_generate" if kind == "diary" else "life.chapter_generate"
            field = "diary_id" if kind == "diary" else "chapter_id"
            await life.jobs.enqueue(connection,JobSpec(task,f"rbh-{kind}:{data_generation}:{uuid4()}",
                {field:fresh["id"],"generation_id":fresh["generation_id"],"data_generation":data_generation},
                scheduled_at=when,subject_id=context.actor_id,data_generation=data_generation),
                queueing_lock=f"{kind}-generate:"+fresh["generation_id"])
        return True,"scheduled"


async def reconcile_transport_waits(worker, *, after="", limit=32):
    limit = max(1,min(int(limit),250))
    async with worker.life.database.transaction(read_only=True) as connection:
        rows = await _candidates(connection,after,limit)
    result = {"inspected":0,"scheduled":0,"ineligible":0,"changes":[]}
    for row in rows:
        result["inspected"] += 1
        try:
            scheduled,reason = await _reconcile_one(worker,row)
        except AppError as error:
            if error.code in {"actor_disabled","routine_disabled","generation_original_unavailable",
                    "generation_request_expired","model_observation_unavailable","original_not_retryable",
                    "generation_observation_context_mismatch","source_unavailable","access_changed",
                    "forbidden","writing_unavailable","writing_material_changed",
                    "diary_episode_revised","diary_material_changed"}:
                scheduled,reason = False,error.code
            else:
                raise
        result["scheduled" if scheduled else "ineligible"] += 1
        if scheduled:
            result["changes"].append({"kind":row["kind"],"id":row["item_id"],"reason":reason})
    return result,(rows[-1]["scan_key"] if rows else "")
