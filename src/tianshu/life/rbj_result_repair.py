"""Life-owned, transaction-local evidence for a targeted result repair."""

from tianshu.conversation.codec import receipt
from tianshu.runtime.errors import AppError
from .rbi_activity_reconciliation import generation_on


OWNERS = {
    "life_activity": ("life.activities", {"cancelled", "completed"}),
    "life_commitment": ("life.commitments", {"cancelled", "completed", "expired", "scheduled"}),
}


async def inspect_on(worker, connection, context, result, result_revision, *, consumer, owner_id, data_generation):
    """Inspect Life after the caller has locked Sources and the Action head.

    A matching Life receipt alone does not prove the old Goal, Memory and
    consumer writes. Historic direct applications have no complete transaction
    marker here, so they remain unknown until independent proof exists.
    """
    if consumer not in OWNERS or (owner_id is not None and (not isinstance(owner_id,str) or not owner_id)):
        raise AppError("invalid_life_result_owner",status=400)
    result = receipt(result)
    if (result is None or not result.action_id or not result.receipt_id
            or type(result_revision) is not int or result_revision < 0
            or result.execution not in {"succeeded","failed","cancelled","unknown"}):
        raise AppError("invalid_life_result_observation",status=400)
    await generation_on(worker.life,connection,data_generation)
    table, terminal = OWNERS[consumer]
    if owner_id is None:
        rows = await (await connection.execute(
            f"SELECT * FROM {table} WHERE actor_id=%s AND action_id=%s ORDER BY id LIMIT 2 FOR UPDATE",
            (context.actor_id,result.action_id))).fetchall()
        if len(rows) != 1:
            return {"state":"unknown","reason":"life_result_owner_missing" if not rows
                else "life_result_owner_ambiguous"}
        row = rows[0]
    else:
        row = await (await connection.execute(
            f"SELECT * FROM {table} WHERE id=%s AND actor_id=%s FOR UPDATE",
            (owner_id,context.actor_id))).fetchone()
        if not row:
            return {"state":"unknown","reason":"life_result_owner_missing","owner_id":owner_id}
    identity = {"owner_id":row["id"],"owner_revision":row["revision"]}
    if not set(row["scope_ids"]) <= set(context.scope_ids):
        return {"state":"unknown","reason":"life_result_owner_scope_changed",**identity}
    if row["action_id"] != result.action_id:
        return {"state":"superseded","reason":"life_result_action_replaced",**identity}
    if row["state"] in terminal - {"completed"}:
        return {"state":"superseded","reason":"life_result_owner_"+row["state"],**identity}
    previous = row["last_result_revision"]
    if type(previous) is not int or previous > result_revision:
        return {"state":"unknown","reason":"life_result_revision_regressed",**identity}
    if previous == result_revision:
        return {"state":"unknown","reason":"life_result_receipt_conflict"
            if row["last_receipt_id"] != result.receipt_id else "local_result_application_unconfirmed",**identity}
    if row["state"] in terminal:
        return {"state":"superseded","reason":"life_result_owner_"+row["state"],**identity}
    allowed = ({"planned","running","dormant","paused"} if consumer == "life_activity"
        else {"dispatching","waiting_result","waiting_expression","unknown","failed"})
    if row["state"] not in allowed:
        return {"state":"unknown","reason":"life_result_owner_state_unrecognized",**identity}
    return {"state":"not_applied","reason":"life_result_not_applied",**identity}


def reject_unproven_duplicate(row, result, result_revision, *, consumer, on_complete):
    """A newly claimed consumer cannot manufacture proof from a Life row."""
    if (on_complete is not None and result.execution in {"succeeded","failed","cancelled","unknown"}
            and result_revision == row["last_result_revision"] and row["last_receipt_id"] == result.receipt_id):
        raise AppError("local_result_application_unconfirmed",status=409,
            details={"consumer":consumer,"owner_id":row["id"]})
