"""Observe fixed actions and expressions without replaying an unknown Action."""
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from tianshu.conversation.codec import receipt, signature, source_refs
from tianshu.conversation.types import ExpressionPlan
from tianshu.runtime.errors import AppError
from tianshu.runtime.serialization import wire
from tianshu.runtime.jobs import JobSpec
from tianshu.types import OriginKind, ServiceClass
from .recovery import scheduled_context
from .contact_control import expression_id


async def queue_commitment_observation(worker, context, action_id, generation, *, delay=30):
    """Keep observing the same Action while its commitment is unresolved."""
    from .rbi_activity_reconciliation import generation_on
    async with worker.life.database.transaction() as connection:
        generation = await generation_on(worker.life,connection,generation)
        row = await worker.life.lock_row(connection,context,
            "SELECT * FROM life.commitments WHERE action_id=%s AND actor_id=%s FOR UPDATE",
            (action_id,context.actor_id))
        if not row or row["state"] not in {"dispatching","waiting_result","waiting_expression","unknown"}:
            return None
        return await _queue_commitment_on(worker,connection,context,row,generation,delay)


async def _queue_commitment_on(worker, connection, context, row, generation, delay=30):
    now = (await (await connection.execute("SELECT now() AS now")).fetchone())["now"]
    due = now + timedelta(seconds=delay)
    job_id = await worker.life.jobs.enqueue(connection,JobSpec("life.commitment_reconcile",
        "commitment-observe:"+str(uuid4()),{"commitment_id":row["id"],"expected_revision":row["revision"]},
        scheduled_at=due,subject_id=context.actor_id,data_generation=generation),
        queueing_lock="life-commitment-observe:"+signature((row["id"],row["action_id"],row["revision"])))
    return {"job_id":job_id,"due_at":due}


async def missing_commitment_action(worker, context, snapshot):
    """A 404 can race admission; keep observing the existing Action identity."""
    from .rbi_activity_reconciliation import generation_on
    async with worker.life.database.transaction() as connection:
        generation = await generation_on(worker.life,connection)
        row = await worker.life.lock_row(connection,context,
            "SELECT * FROM life.commitments WHERE id=%s AND actor_id=%s FOR UPDATE",
            (snapshot["id"],context.actor_id))
        if (not row or row["revision"] != snapshot["revision"] or row["action_id"] != snapshot["action_id"]
                or row["state"] not in {"dispatching","waiting_result","waiting_expression","unknown"}):
            return {"state":"blocked","reason":"original_target_changed","action_id":snapshot["action_id"]}
        if row["state"] != "unknown" or row["reason"] != "original_action_unresolved":
            row = await (await connection.execute(
                "UPDATE life.commitments SET state='unknown',reason='original_action_unresolved',"
                "revision=revision+1,updated_at=now() WHERE id=%s AND revision=%s RETURNING *",
                (row["id"],row["revision"]))).fetchone()
        scheduled = await _queue_commitment_on(worker,connection,context,row,generation)
    return {"state":"waiting","reason":"original_action_not_found","action_id":row["action_id"],
        **scheduled}


async def contact(worker,candidate_id,expected_revision):
    life=worker.life
    async with life.database.transaction(read_only=True) as connection:
        row=await (await connection.execute("SELECT * FROM life.contact_candidates WHERE id=%s",(candidate_id,))).fetchone()
    if not row or row["revision"]!=expected_revision or row["state"] not in {"sending","waiting_expression","unknown"}:
        return
    context=await scheduled_context(life,row["context"])
    await life.identity.check(context,source_refs(row["source_refs"]))
    identifier=row["expression_id"] or expression_id(row)
    try:
        current=await worker.expression.get(context,identifier)
    except AppError as error:
        if error.status!=404:
            raise
        async with life.database.transaction() as connection:
            changed=await (await connection.execute("UPDATE life.contact_candidates SET state='pending',reason='original_expression_not_admitted' WHERE id=%s AND revision=%s AND state=ANY(%s) RETURNING id",(candidate_id,expected_revision,["sending","waiting_expression","unknown"]))).fetchone()
        if changed:
            await worker.contact_due(candidate_id,expected_revision)
        return
    current=await worker.expression.reconcile(context,identifier,current["revision"])
    await worker.expression_result(context,identifier,current)


async def commitment(worker,commitment_id,expected_revision):
    life=worker.life
    async with life.database.transaction(read_only=True) as connection:
        row=await (await connection.execute("SELECT * FROM life.commitments WHERE id=%s",(commitment_id,))).fetchone()
    if not row or row["revision"]!=expected_revision or row["state"] not in {"dispatching","waiting_result","waiting_expression","unknown"}:
        return
    context=await scheduled_context(life,row["context"])
    await life.identity.check(context,source_refs(row["source_refs"]))
    if row["action_id"]:
        try:
            current=await worker.actions.get(row["action_id"],context)
        except AppError as error:
            if error.status!=404:
                raise
            return await missing_commitment_action(worker,context,row)
        else:
            result=receipt(current["receipt"])
        if result:
            return await worker.capability_result(context,result)
        scheduled = await queue_commitment_observation(worker,context,row["action_id"],None)
        return {"state":"waiting","reason":"action_receipt_unavailable","action_id":row["action_id"],
            **(scheduled or {})}
    if row["last_expression_id"]:
        identifier=row["last_expression_id"]
        try:
            current=await worker.expression.get(context,identifier)
        except AppError as error:
            if error.status!=404:
                raise
            if row["due_at"] and (datetime.now(UTC)-row["due_at"]).total_seconds()>max(60,row["grace_seconds"]):
                return
            output=await worker.expression.send(context,ExpressionPlan(identifier,context.actor_id,row["target_ref"],
                ({"kind":"text","text":row["title"]},),source_refs(row["source_refs"]),OriginKind.TIMER),service_class=ServiceClass.DUE)
            current={"state":output.state,"receipt":wire(output.receipt),"reason":output.reason}
        else:
            current=await worker.expression.reconcile(context,identifier,current["revision"])
        await worker.expression_result(context,identifier,current)
