"""Recheck a revocable proposal at every real submission boundary."""
from datetime import UTC, datetime

from tianshu.conversation.codec import source_refs
from tianshu.runtime.jobs import JobSpec
from tianshu.runtime.errors import AppError
from tianshu.types import ServiceClass


def expression_id(candidate):
    return f"contact:{candidate['id']}:{candidate['delivery_generation']}"


async def preflight(worker, context, candidate_id, *, expression_id=None):
    life=worker.life
    async with life.database.transaction(read_only=True) as connection:
        candidate=await (await connection.execute("SELECT * FROM life.contact_candidates WHERE id=%s AND actor_id=%s",(candidate_id,context.actor_id))).fetchone()
    if not candidate or candidate["target_ref"]!=context.conversation_id:
        return {"decision":"cancel","reason":"contact_target_unavailable"}
    if candidate["state"] not in {"sending","waiting_expression"} or (expression_id and candidate["expression_id"]!=expression_id):
        return {"decision":"cancel","reason":"contact_proposal_replaced"}
    try:
        await life.identity.check(context,source_refs(candidate["source_refs"]))
    except AppError as error:
        if error.status not in {401,403,404,409,410}:
            raise
        async with life.database.transaction() as connection:
            await connection.execute("UPDATE life.contact_candidates SET state='cancelled',reason='source_unavailable',revision=revision+1 WHERE id=%s AND revision=%s AND expression_id=%s AND state IN ('sending','waiting_expression')",(candidate_id,candidate['revision'],candidate['expression_id']))
        return {'decision':'cancel','reason':'source_unavailable'}
    preferences=await life.contacts.preferences(context)
    decision=life.attention.decide(candidate,preferences,await life.contacts.history(context),now=datetime.now(UTC))
    if decision.decision=="send":
        return {"decision":"send","reason":decision.reason}
    async with life.database.transaction() as connection:
        current=await life.lock_row(connection,context,"SELECT * FROM life.contact_candidates WHERE id=%s FOR UPDATE",(candidate_id,))
        if current["revision"]!=candidate["revision"] or current["expression_id"]!=candidate["expression_id"] or current["state"] not in {"sending","waiting_expression"}:
            return {"decision":"cancel","reason":"contact_proposal_replaced"}
        if decision.decision=="defer":
            await connection.execute("UPDATE life.contact_candidates SET state='waiting',reason=%s,due_at=%s,expression_id=NULL,delivery_generation=delivery_generation+1,revision=revision+1 WHERE id=%s",(decision.reason,decision.due_at,candidate_id))
            await life.jobs.enqueue(connection,JobSpec("life.contact_due",f"contact:{candidate_id}:{current['revision']+1}",{"candidate_id":candidate_id,"expected_revision":current["revision"]+1},ServiceClass.DUE,decision.due_at,context.actor_id))
            await connection.execute("UPDATE life.commitments SET last_expression_id=%s WHERE last_expression_id=%s AND state='waiting_expression'",(f"contact:{candidate_id}:{current['delivery_generation']+1}",current["expression_id"]))
        else:
            await connection.execute("UPDATE life.contact_candidates SET state=%s,reason=%s,revision=revision+1 WHERE id=%s",("archive" if decision.decision=="archive" else "cancelled",decision.reason,candidate_id))
            await connection.execute("UPDATE life.commitments SET state='expired',reason=%s,revision=revision+1 WHERE last_expression_id=%s AND state='waiting_expression'",(decision.reason,current["expression_id"]))
    return {"decision":"defer" if decision.decision=="defer" else "cancel","reason":decision.reason,"due_at":decision.due_at}
