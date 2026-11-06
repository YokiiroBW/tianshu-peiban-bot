"""A new Life decision after an original failed result was consumed."""

from tianshu.capabilities.actions import result_sources
from tianshu.conversation.codec import signature, source_refs
from tianshu.runtime.errors import AppError
from tianshu.sources.dependencies import unique_refs
from .continuation import action_resolved, known_read_failure
from .rbi_activity_reconciliation import timestamp
from .rbi_result_fence import DirectResultFence


def consumed_activity(handoff, row, result, revision, generation):
    if not isinstance(handoff,dict) or handoff.get("state") not in {"accepted","waiting","verified"}:
        return False
    if handoff.get("state") == "verified" and handoff.get("reason") in {
            "invalidated", "superseded", "source_withdrawn"}:
        return False
    if (handoff.get("action_id") != result.action_id or handoff.get("receipt_id") != result.receipt_id
            or handoff.get("result_revision") != revision):
        return False
    progress = (handoff.get("consumer_progress") or {}).get("life_activity") or {}
    proof = progress.get("proof") or {}
    return (progress.get("data_generation") == generation and progress.get("action_id") == result.action_id
        and progress.get("receipt_id") == result.receipt_id and progress.get("result_revision") == revision
        and progress.get("disposition") == "done" and proof.get("activity_id") == row["id"]
        and proof.get("last_result_revision") == revision and proof.get("last_receipt_id") == result.receipt_id)


async def close(worker, context, snapshot, actual, result, generation, *, deadline_required):
    """CAS and record only the new skip decision, never replay the old result."""
    try:
        return await _close_on_current_head(worker,context,snapshot,actual,result,generation,
            deadline_required=deadline_required)
    except AppError as error:
        if error.code != "result_head_advanced" or not getattr(error,"rbj_control_head_advanced",False):
            raise
        from .rbj_result_observation import _advanced, _handoff, observe, TERMINAL
        latest = await worker.actions.get(result.action_id,context)
        newer = _advanced(actual,latest,result.action_id)
        if newer.execution in TERMINAL:
            return await _handoff(worker,context,result.action_id,latest,generation)
        return await observe(worker,context,newer,data_generation=generation)


async def _close_on_current_head(worker, context, snapshot, actual, result, generation, *, deadline_required):
    capability = actual["action"]["capability_id"]
    revision = actual["result_revision"]
    if not known_read_failure(capability,result):
        return {"state":"blocked","reason":"read_failure_not_proven","action_id":result.action_id}
    fence = DirectResultFence(worker.life,worker.actions,context,actual,generation)
    dependencies = unique_refs(source_refs(snapshot["source_refs"]),result_sources(result))
    prepared = worker._read_skip_event(snapshot,result,revision,dependencies)[0]
    async with worker.life.database.transaction() as connection:
        await fence.begin(connection)
        await worker.life.sources.prepare_event_writes(connection,context,(prepared,),
            additional_refs=unique_refs(dependencies,fence.source_refs))
        try:
            await fence.fence(connection)
        except AppError as error:
            if error.code == "result_head_advanced":
                error.rbj_control_head_advanced = True
            raise
        row = await (await connection.execute(
            "SELECT * FROM life.activities WHERE id=%s AND actor_id=%s FOR UPDATE",
            (snapshot["id"],context.actor_id))).fetchone()
        if signature(row) != signature(snapshot):
            return {"state":"blocked","reason":"activity_changed","action_id":result.action_id}
        if (not row or row["state"] != "dormant" or row["action_id"] != result.action_id
                or row["last_result_revision"] != revision or row["last_receipt_id"] != result.receipt_id
                or action_resolved(row)):
            return {"state":"blocked","reason":"activity_changed","action_id":result.action_id}
        if deadline_required:
            if row["waiting_reason"] != "capability_recovery":
                return {"state":"blocked","reason":"activity_changed","action_id":result.action_id}
            raw = (row["checkpoint_data"].get("frozen_action") or {}).get("deadline_at")
            deadline = timestamp(raw)
            if deadline is None:
                return {"state":"blocked","reason":"original_action_deadline_unconfirmed","action_id":result.action_id}
            now = (await (await connection.execute("SELECT now() AS now")).fetchone())["now"]
            if deadline > now:
                return {"state":"waiting","reason":"original_action_deadline_not_reached",
                    "action_id":result.action_id,"due_at":deadline}
        elif (row["resume_condition"] != "review_failure"
                or row["waiting_reason"] in {"video_checkpoint_unavailable","read_checkpoint_unavailable"}):
            return {"state":"blocked","reason":"activity_changed","action_id":result.action_id}
        continuing = await worker._close_read_failure(connection,row,result,revision)
        await worker._record_read_skip(connection,context,row,result,revision,dependencies)
        if continuing:
            current = await (await connection.execute("SELECT * FROM life.activities WHERE id=%s",(row["id"],))).fetchone()
            now = (await (await connection.execute("SELECT now() AS now")).fetchone())["now"]
            await worker._queue_continuation(connection,context,current,now)
        await worker.life.signal(context,f"activity-read-skip:{result.action_id}:{revision}",connection=connection)
    return {"state":"accepted","reason":"read_failure_continued","action_id":result.action_id,
        "receipt_id":result.receipt_id,"result_revision":revision}
