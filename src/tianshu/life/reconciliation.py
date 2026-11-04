"""Observe fixed actions/expressions, admitting only provably absent original work."""
from datetime import UTC, datetime

from tianshu.capabilities.serialization import action_from
from tianshu.conversation.codec import receipt, source_refs
from tianshu.conversation.types import ExpressionPlan
from tianshu.runtime.errors import AppError
from tianshu.runtime.serialization import wire
from tianshu.types import OriginKind, ServiceClass
from .recovery import scheduled_context
from .contact_control import expression_id


async def absent_action(worker, frozen):
    if not frozen:
        return None
    action=action_from(frozen)
    if action.deadline_at is not None and action.deadline_at<=datetime.now(UTC):
        return None
    # Original-ID admission is only reached after Actions.get proved absence.
    # Its original deadline and authorization are never refreshed or extended.
    await worker.life.identity.check(action.context,action.source_refs)
    return receipt(await worker.actions.invoke(action,background=True,service_class=ServiceClass.DUE))


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
            result=await absent_action(worker,row["frozen_action"])
        else:
            result=receipt(current["receipt"])
        if result:
            await worker.capability_result(context,result)
        return
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
