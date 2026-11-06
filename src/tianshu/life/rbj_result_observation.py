"""Separate Action observation/control from terminal result consumption."""

from datetime import timedelta

from tianshu.conversation.codec import receipt, signature, source_refs
from tianshu.runtime.errors import AppError
from tianshu.runtime.jobs import JobSpec
from .rbi_activity_reconciliation import eligible, ensure_reconcile_on, generation_on
from .rbi_result_fence import DirectResultFence


TERMINAL = frozenset({"succeeded", "failed", "cancelled", "unknown"})
CONTROL = frozenset({"pending", "running"})
MAX_HEAD_OBSERVATIONS = 3


def _head(snapshot, action_id):
    action = snapshot.get("action") or {}
    current = receipt(snapshot.get("receipt"))
    revision = snapshot.get("result_revision")
    if action.get("action_id") != action_id or (current is not None and current.action_id != action_id):
        raise AppError("action_receipt_identity_mismatch", status=409)
    if type(revision) is not int or revision < 0:
        raise AppError("result_revision_invalid", status=409)
    return current, revision


def _advanced(previous, current, action_id):
    old, old_revision = _head(previous, action_id)
    new, new_revision = _head(current, action_id)
    if new_revision < old_revision:
        raise AppError("result_head_regressed", status=409)
    if new_revision == old_revision:
        if signature(previous.get("receipt")) != signature(current.get("receipt")):
            raise AppError("result_receipt_revision_conflict", status=409)
        # A reported advance without an observable higher head is not proof
        # that the old transaction is safe to apply or that a new head exists.
        raise AppError("result_head_advanced", status=409)
    if old is None or new is None:
        raise AppError("result_head_unavailable", status=409)
    return new


async def _handoff(worker, context, action_id, snapshot, generation):
    current, revision = _head(snapshot, action_id)
    if current is None or current.execution not in TERMINAL:
        raise AppError("result_head_not_terminal", status=409)
    # Actions owns event construction and consumer budget. In particular, a
    # blocked result is returned unchanged rather than reported as delivered.
    result = await worker.actions.ensure_result_consumption(context, action_id,
        data_generation=generation, expected_result_revision=revision,
        expected_receipt_id=current.receipt_id)
    if (not isinstance(result, dict) or result.get("state") not in {"accepted", "waiting", "blocked", "verified"}
            or not result.get("reason") or result.get("action_id") != action_id):
        raise AppError("result_handoff_invalid", status=409)
    return result


async def _recovery_guard(worker, context, action_id, fence, generation):
    if fence is None:
        return True
    if worker.recovery_guard_on is None:
        raise AppError("recovery_guard_unavailable", status=503)
    async with worker.life.database.transaction() as connection:
        await generation_on(worker.life, connection, generation)
        query = "SELECT * FROM life.activities WHERE id=%s AND actor_id=%s"
        args = (fence["activity_id"], context.actor_id)
        snapshot = await (await connection.execute(query,args)).fetchone()
        await worker.life.validate_persistence(connection,context,
            source_refs(snapshot["source_refs"]) if snapshot else ())
        await worker.recovery_guard_on(connection, fence["attempt_id"], fence["owner_token"])
        current = await (await connection.execute(query+" FOR UPDATE",args)).fetchone()
        return bool(signature(snapshot) == signature(current) and current
            and current["revision"] == fence["expected_revision"]
            and current["action_id"] == action_id and current["state"] not in {"cancelled", "completed", "paused"})


async def _defer_head(worker, context, action_id, generation):
    """Leave an original-object wake if a changing control head never settles."""
    async with worker.life.database.transaction() as connection:
        await generation_on(worker.life, connection, generation)
        now = (await (await connection.execute("SELECT now() AS now")).fetchone())["now"]
        due = now + timedelta(seconds=15)
        activity = await worker.life.lock_row(connection, context,
            "SELECT * FROM life.activities WHERE action_id=%s AND actor_id=%s FOR UPDATE",
            (action_id, context.actor_id))
        if eligible(activity):
            job_id = await ensure_reconcile_on(worker.life, connection, activity_id=activity["id"],
                action_id=action_id, data_generation=generation, due_at=due, reason="head_advancing")
            if job_id is not None:
                return {"state":"waiting", "reason":"result_head_advancing", "action_id":action_id,
                    "job_id":job_id, "due_at":due}
        if activity:
            return {"state":"blocked", "reason":"original_target_changed", "action_id":action_id}
        commitment = await worker.life.lock_row(connection, context,
            "SELECT * FROM life.commitments WHERE action_id=%s AND actor_id=%s FOR UPDATE",
            (action_id, context.actor_id))
        if commitment and commitment["state"] in {"dispatching", "waiting_result", "unknown"}:
            from uuid import uuid4
            job_id = await worker.life.jobs.enqueue(connection, JobSpec("life.commitment_reconcile",
                "commitment-head-observe:"+str(uuid4()),
                {"commitment_id":commitment["id"], "expected_revision":commitment["revision"]},
                scheduled_at=due, subject_id=context.actor_id, data_generation=generation),
                queueing_lock="life-commitment-observe:"+signature((commitment["id"],action_id,commitment["revision"])))
            return {"state":"waiting", "reason":"result_head_advancing", "action_id":action_id,
                "job_id":job_id, "due_at":due}
    return {"state":"blocked", "reason":"original_target_changed", "action_id":action_id}


async def observe(worker, context, result, *, recovery_fence=None, data_generation=None):
    original = receipt(result)
    if original is None or not original.action_id:
        return False
    action_id = original.action_id
    async with worker.life.database.transaction() as connection:
        generation = await generation_on(worker.life, connection, data_generation)
    snapshot = await worker.actions.get(action_id, context)
    for number in range(MAX_HEAD_OBSERVATIONS):
        current, revision = _head(snapshot, action_id)
        if current is None:
            return {"state":"waiting", "reason":"action_receipt_unavailable", "action_id":action_id}
        if current.execution in TERMINAL:
            if not await _recovery_guard(worker, context, action_id, recovery_fence, generation):
                return {"state":"blocked", "reason":"activity_changed", "action_id":action_id}
            outcome = await _handoff(worker, context, action_id, snapshot, generation)
            if current.execution == "unknown" or current.effect == "unknown":
                from .reconciliation import queue_commitment_observation
                await queue_commitment_observation(worker,context,action_id,generation)
            return outcome
        if current.execution not in CONTROL:
            raise AppError("action_execution_invalid", status=409)
        capability = snapshot["action"]["capability_id"]
        observed_at = snapshot["result_observed_at"]
        try:
            fence = DirectResultFence(worker.life, worker.actions, context, snapshot, generation)
            activity = await worker._activity_result(context, current, revision, observed_at, capability,
                recovery_fence=recovery_fence, on_complete=fence, data_generation=generation)
            if recovery_fence is not None:
                return activity
            fence = DirectResultFence(worker.life, worker.actions, context, snapshot, generation)
            commitment = await worker._commitment_result(context, current, revision, observed_at, capability,
                on_complete=fence, data_generation=generation)
            if commitment:
                from .reconciliation import queue_commitment_observation
                await queue_commitment_observation(worker,context,action_id,generation)
            return activity or commitment
        except AppError as error:
            if error.code != "result_head_advanced":
                raise
            # This read runs only after the fenced control transaction exited.
            # It must prove a strictly newer head of the same Action.
            latest = await worker.actions.get(action_id, context)
            newer = _advanced(snapshot, latest, action_id)
            if newer.execution in TERMINAL:
                if not await _recovery_guard(worker, context, action_id, recovery_fence, generation):
                    return {"state":"blocked", "reason":"activity_changed", "action_id":action_id}
                outcome = await _handoff(worker, context, action_id, latest, generation)
                if newer.execution == "unknown" or newer.effect == "unknown":
                    from .reconciliation import queue_commitment_observation
                    await queue_commitment_observation(worker,context,action_id,generation)
                return outcome
            if newer.execution not in CONTROL:
                raise AppError("action_execution_invalid", status=409)
            snapshot = latest
            if number + 1 == MAX_HEAD_OBSERVATIONS:
                return await _defer_head(worker, context, action_id, generation)
